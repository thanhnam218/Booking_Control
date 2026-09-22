"""Chung tu, task B1-B15.1, Weekly Planning, ma tran quyen (SRSv5 muc 28-36, 56-63).

    POST /shipments/{code}/documents        Tai len chung tu (moi lan = 1 version moi, khong ghi de)
    GET  /documents/versions/{vid}          Tai file chung tu
    POST /documents/{doc_id}/status         Duyet / tra lai chung tu (Quan ly)
    /tasks                                  Viec cua toi (Quan ly: chon nguoi / tat ca)
    POST /tasks/{id}/status                 Bat dau / Hoan thanh / Bo qua / Mo lai
    POST /tasks/{id}/due                    Doi han task (Quan ly)
    /weekly, POST /weekly/confirm           Weekly Planning tuan sau, chot truoc 12:00 thu 6
    /admin/permissions                      Ma tran vai tro x quyen
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import String, cast, or_, select

from ..core import booking as bk
from ..core import workflow as wf
from ..db import session_scope
from ..db.models import Shipment, User
from ..db.models_v3 import Document, DocumentVersion, Email, ShipmentEvent, ShipmentExecution, WorkflowTask
from ..services import booking_service as bs
from ..services import document_service as ds
from ..services import shipment_service as svc
from ..services import weekly_service as ws
from ..services import workflow_service as wfs
from .auth import PERMISSION_LABELS, PERMISSIONS, ROLE_LABELS, ROLES
from .deps import PermissionDenied, current, engine, render, require
from .routes_booking import _back

router = APIRouter()

TASK_ACTIONS = {"IN_PROGRESS": "Bắt đầu", "COMPLETED": "Hoàn thành", "SKIPPED": "Bỏ qua", "PENDING": "Mở lại",
                "WAITING": "Chờ bên ngoài"}


# ---------------------------------------------------------------------------
# Du lieu v3 cho trang chi tiet lo hang
# ---------------------------------------------------------------------------


def shipment_v3_context(s, p, sh: Shipment, today: dt.date) -> dict:
    settings = engine().settings
    code = sh.shipment_code
    by_code = bs.bookings_for(s, [code]).get(code, [])
    summary, shipped = bs.summaries(s, [sh])[code]
    bookings = [{"id": b.id, "label": b.label, "status": b.status, "status_label": bk.STATUS_LABELS.get(b.status),
                 "qty": link.allocated_qty, "etd": b.etd, "vessel": b.vessel, "forwarder": b.forwarder,
                 "source": b.source, "replacement": b.replacement_booking_id}
                for b, link in sorted(by_code, key=lambda x: x[0].id)]
    execs = [{"date": x.ship_date, "qty": x.shipped_qty, "cont": x.container_no, "seal": x.seal_no,
              "booking_id": x.booking_id, "by": x.created_by, "note": x.note}
             for x in s.execute(select(ShipmentExecution).where(ShipmentExecution.shipment_code == code)
                                .order_by(ShipmentExecution.ship_date, ShipmentExecution.id)).scalars()]
    events = [{"ts": e.ts, "type": e.event_type, "source": e.source, "actor": e.actor, "old": e.old_value,
               "new": e.new_value, "note": e.note, "booking_id": e.booking_id, "email_id": e.email_id}
              for e in s.execute(select(ShipmentEvent).where(ShipmentEvent.shipment_code == code)
                                 .order_by(ShipmentEvent.id.desc()).limit(150)).scalars()]
    docs = wfs.checklist(s, settings, sh, today)
    versions = {k: [{"id": v.id, "version": v.version, "filename": v.filename, "size": v.size, "by": v.uploaded_by,
                     "at": v.uploaded_at, "source": v.source, "note": v.note} for v in vs]
                for k, vs in ds.versions(s, code).items()}
    tasks = wfs.task_rows(s, list(s.execute(select(WorkflowTask).where(WorkflowTask.shipment_code == code)
                                            .order_by(WorkflowTask.seq)).scalars()), today)
    emails = [{"id": e.id, "subject": e.subject, "received": e.received_at, "category": e.category,
               "status": e.match_status}
              for e in s.execute(select(Email).where(cast(Email.shipment_codes, String).like(f"%{code}%"))
                                 .order_by(Email.received_at.desc()).limit(30)).scalars()
              if code in (e.shipment_codes or [])]
    can_edit = svc.can_edit(p, sh)
    return {
        "v3": {"bookings": bookings, "summary": summary, "shipped": shipped, "execs": execs, "events": events,
               "docs": docs, "versions": versions, "tasks": tasks, "emails": emails,
               "doc_types": ds.doc_types(settings), "labels": bk.STATUS_LABELS,
               "can_booking": can_edit and p.can("booking.edit"),
               "can_exec": can_edit and p.can("execution.record"),
               "can_upload": can_edit and p.can("document.upload"),
               "can_approve": p.can("document.approve"),
               "can_task": can_edit and p.can("workflow.complete"),
               "task_actions": TASK_ACTIONS, "today": today}
    }


# ---------------------------------------------------------------------------
# Chung tu
# ---------------------------------------------------------------------------


@router.post("/shipments/{code}/documents")
async def upload_document(request: Request, code: str, doc_type: str = Form(...), note: str = Form(""),
                          file: UploadFile = File(...)):
    p = require(request, "document.upload")
    content = await file.read()
    with session_scope() as s:
        sh = svc.get_visible(s, p, code)
        if not svc.can_edit(p, sh):
            raise PermissionDenied()
        try:
            v = ds.add_version(s, engine().settings, sh, doc_type, file.filename or "file", content, actor=p.name,
                               note=note)
        except ds.DocumentError as exc:
            return _back(f"/shipments/{code}", err=str(exc))
        msg = f"Đã lưu {doc_type} v{v.version}" if v else "File giống hệt bản trước - không tạo version mới"
        if v:  # du chung tu -> buoc tuong ung tu hoan thanh ngay
            s.flush()
            wfs.ensure_tasks(s, engine().settings, [sh], dt.date.today())
    return _back(f"/shipments/{code}", msg=msg)


@router.get("/documents/versions/{vid}")
def download_document(request: Request, vid: int):
    p = current(request)
    with session_scope() as s:
        v = s.get(DocumentVersion, vid)
        doc = s.get(Document, v.document_id) if v else None
        if doc is None:
            raise svc.NotFound("Không tìm thấy chứng từ")
        svc.get_visible(s, p, doc.shipment_code)
        if not Path(v.stored_path).exists():
            raise svc.NotFound("File chứng từ không còn trên ổ lưu trữ")
        path, name = v.stored_path, Path(v.stored_path).name
    return FileResponse(path, filename=name)


@router.post("/documents/{doc_id}/status")
def document_status(request: Request, doc_id: int, status: str = Form(...), note: str = Form("")):
    p = require(request, "document.approve")
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            raise svc.NotFound("Không tìm thấy chứng từ")
        svc.get_visible(s, p, doc.shipment_code)
        try:
            ds.set_status(s, doc, status, actor=p.name, note=note)
        except ds.DocumentError as exc:
            return _back(f"/shipments/{doc.shipment_code}", err=str(exc))
        code = doc.shipment_code
    return _back(f"/shipments/{code}", msg=f"Chứng từ -> {status}")


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, owner: str = "", show: str = "open", days: int = 14, q: str = "", msg: str = "",
               err: str = ""):
    p = current(request)
    today = dt.date.today()
    if not p.can("tasks.view_all"):
        owner = p.name
    elif owner == "" and p.role == "user":
        owner = p.name
    with session_scope() as s:
        stmt = select(WorkflowTask, Shipment).join(Shipment, Shipment.shipment_code == WorkflowTask.shipment_code) \
            .where(Shipment.archived.is_(False))
        if owner and owner != "__all__":
            stmt = stmt.where(or_(WorkflowTask.owner == owner, Shipment.backup_owner == owner))
        if not p.can("shipments.view_all"):
            stmt = stmt.where(or_(Shipment.assigned_to == p.name, Shipment.backup_owner == p.name))
        if show == "open":
            stmt = stmt.where(WorkflowTask.status.not_in(tuple(wf.DONE)),
                              or_(WorkflowTask.due_date.is_(None), WorkflowTask.due_date <= today + dt.timedelta(days=days)))
        elif show == "done":
            stmt = stmt.where(WorkflowTask.status.in_(tuple(wf.DONE)))
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Shipment.jo.ilike(like), Shipment.shipment_no.ilike(like),
                                  WorkflowTask.step_code == q.strip().upper()))
        pairs = s.execute(stmt.order_by(WorkflowTask.due_date.is_(None), WorkflowTask.due_date, WorkflowTask.seq)
                          .limit(1500)).all()
        rows = wfs.task_rows(s, [t for t, _ in pairs], today)
        info = {sh.shipment_code: sh for _, sh in pairs}
        for r in rows:
            sh = info[r["code"]]
            r.update({"jo": sh.jo, "buyer": sh.buyer_code or sh.buyer_raw, "exfty": sh.exfty_date,
                      "can": p.can("workflow.complete") and svc.can_edit(p, sh)})
        if show == "overdue":
            rows = [r for r in rows if r["display"] == "OVERDUE"]
        users = sorted(u.name for u in s.execute(select(User).where(User.active.is_(True))).scalars()) \
            if p.can("tasks.view_all") else []
    counts = {"OVERDUE": sum(r["display"] == "OVERDUE" for r in rows),
              "BLOCKED": sum(r["display"] == "BLOCKED" for r in rows),
              "TODAY": sum(r["due"] == today for r in rows if r["status"] not in wf.DONE)}
    return render(request, "tasks.html", rows=rows, users=users, f={"owner": owner, "show": show, "days": days, "q": q},
                  counts=counts, actions=TASK_ACTIONS, msg=msg, err=err, today=today)


@router.post("/tasks/{task_id}/status")
def task_status(request: Request, task_id: int, status: str = Form(...), note: str = Form(""), back: str = Form("")):
    p = require(request, "workflow.complete")
    with session_scope() as s:
        t = s.get(WorkflowTask, task_id)
        if t is None:
            raise svc.NotFound("Không tìm thấy task")
        sh = svc.get_visible(s, p, t.shipment_code)
        if not svc.can_edit(p, sh):
            raise PermissionDenied()
        target = back if back.startswith("/") else "/tasks"
        try:
            wfs.set_task_status(s, t, status, actor=p.name, note=note, today=dt.date.today())
        except wfs.WorkflowError as exc:
            return _back(target, err=str(exc))
    return _back(target, msg=f"{t.step_code}: {TASK_ACTIONS.get(status, status)}")


@router.post("/tasks/{task_id}/due")
def task_due(request: Request, task_id: int, due: str = Form(""), back: str = Form("")):
    p = require(request, "shipments.assign")
    with session_scope() as s:
        t = s.get(WorkflowTask, task_id)
        if t is None:
            raise svc.NotFound("Không tìm thấy task")
        old = t.due_date
        t.due_date = dt.date.fromisoformat(due) if due else None
        t.manual_due = bool(due)
        bs.log_event(s, "TASK_DUE_CHANGED", shipment_code=t.shipment_code, actor=p.name, old=old, new=t.due_date,
                     note=t.step_code)
    return _back(back if back.startswith("/") else "/tasks", msg="Đã đổi hạn")


# ---------------------------------------------------------------------------
# Weekly Planning
# ---------------------------------------------------------------------------


@router.get("/weekly", response_class=HTMLResponse)
def weekly_page(request: Request, mine: int = 0, msg: str = "", err: str = ""):
    p = current(request)
    with session_scope() as s:
        user = p.name if (mine or not p.can("shipments.view_all")) else ""
        st = ws.status(s, engine().settings, user=user)
    return render(request, "weekly.html", w=st, mine=bool(user), msg=msg, err=err)


@router.post("/weekly/confirm")
def weekly_confirm(request: Request, note: str = Form("")):
    p = require(request, "weekly.confirm")
    with session_scope() as s:
        v = ws.confirm(s, engine().settings, actor=p.name, note=note)
    return _back("/weekly", msg=f"Đã chốt Weekly Planning ({v['total']} lô, còn thiếu {v['incomplete']})")


# ---------------------------------------------------------------------------
# Ma tran quyen
# ---------------------------------------------------------------------------


@router.get("/admin/permissions", response_class=HTMLResponse)
def permissions_page(request: Request):
    require(request, "audit.view")
    matrix = [{"perm": k, "label": PERMISSION_LABELS.get(k, k), "roles": {r: r in v for r in ROLES}}
              for k, v in PERMISSIONS.items()]
    return render(request, "permissions.html", matrix=matrix, roles=ROLES, role_labels=ROLE_LABELS)
