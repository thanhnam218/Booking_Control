"""Dashboard, danh sach / chi tiet lo hang, Exception Center, thong bao, audit."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select

from ..audit import log_audit, resolve_exception
from ..core import forwarder as fw
from ..core import status as st
from ..core.deadline import compute_milestones, days_until
from ..db import session_scope
from ..db.models import (
    AssignmentHistory,
    AuditLog,
    ContainerAllocation,
    ExceptionRecord,
    Notification,
    ScanRun,
    Shipment,
    UserOverride,
    utcnow,
)
from ..services import shipment_service as svc
from ..services.assignment import AssignmentError, active_users, assign, workload
from .deps import CTX, PermissionDenied, current, engine, render, require

router = APIRouter()


def row_dict(sh: Shipment, today: dt.date) -> dict[str, Any]:
    d = days_until(sh.exfty_date, today) if sh.exfty_date else None
    return {
        "code": sh.shipment_code, "no": sh.shipment_no, "jo": sh.jo,
        "buyer": sh.buyer_code or sh.buyer_raw, "exfty": sh.exfty_date, "days_left": d,
        "status": sh.effective_status, "auto_status": sh.auto_status, "cbm": sh.final_cbm,
        "pcs": sh.final_export_qty, "mode": sh.ship_mode, "booking_mode": sh.booking_mode,
        "cdf": sh.cdf, "bl": sh.bl, "pic": sh.pic, "site": sh.site,
        "co": "Yes" if sh.co_required else "No", "co_form": sh.co_form,
        "overridden": sh.effective_status != sh.auto_status,
        "assigned_to": sh.assigned_to, "backup_owner": sh.backup_owner,
        "forwarder": sh.forwarder, "forwarder_status": sh.forwarder_status, "origin": sh.origin,
        "deadline": svc.deadline_label(sh, today),
    }


# ---------------------------------------------------------------------------
# Dashboard: User -> "Lo hang cua toi"; Quan ly -> them Team Workload
# ---------------------------------------------------------------------------

BUCKETS = [
    ("overdue", "Quá hạn"), ("today", "Hôm nay"), ("d1", "D-1"), ("d3", "D-3"), ("d7", "D-7"),
    ("d14", "D-14"), ("d20", "D-20"), ("hold", "Hold"), ("cancel", "Đã hủy"), ("done", "Hoàn tất"),
]


def bucket_of(sh: Shipment, today: dt.date) -> str:
    stt = sh.effective_status
    if stt == "COMPLETED":
        return "done"
    if stt == "CANCELLED":
        return "cancel"
    if stt == "RED":
        return "hold"
    if not sh.exfty_date:
        return ""
    d = (sh.exfty_date - today).days
    if d < 0:
        return "overdue"
    if d == 0:
        return "today"
    for k, lim in (("d1", 1), ("d3", 3), ("d7", 7), ("d14", 14), ("d20", 20)):
        if d <= lim:
            return k
    return ""


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    p = current(request)
    today = dt.date.today()
    with session_scope() as s:
        ships = list(s.execute(svc.visible_stmt(p)).scalars())
        counts: dict[str, int] = {}
        buckets: dict[str, int] = {k: 0 for k, _ in BUCKETS}
        for sh in ships:
            counts[sh.effective_status] = counts.get(sh.effective_status, 0) + 1
            b = bucket_of(sh, today)
            if b:
                buckets[b] += 1
        urgent = sorted(
            [sh for sh in ships if sh.effective_status in ("WHITE", "RED", "PARTIAL") and sh.exfty_date],
            key=lambda x: x.exfty_date)[:15]
        upcoming = sorted([sh for sh in ships if sh.exfty_date and sh.exfty_date >= today],
                          key=lambda x: x.exfty_date)[:15]
        exc_stmt = select(func.count()).select_from(ExceptionRecord).where(ExceptionRecord.status == "OPEN")
        if not p.can("exceptions.view_all"):
            codes = [sh.shipment_code for sh in ships]
            exc_stmt = exc_stmt.where(ExceptionRecord.shipment_code.in_(codes or ["-"]))
        exc_count = s.execute(exc_stmt).scalar_one()
        last_run = s.execute(select(ScanRun).order_by(ScanRun.id.desc()).limit(1)).scalar_one_or_none()
        team = workload(s, today) if p.can("reports.team") else []
        data = dict(
            counts=counts, total=len(ships), buckets=buckets, bucket_labels=BUCKETS,
            urgent=[row_dict(x, today) for x in urgent], upcoming=[row_dict(x, today) for x in upcoming],
            exc_count=exc_count, team=team,
            last_run=None if last_run is None else {
                "started": last_run.started_at, "mode": last_run.mode, "files": last_run.files_scanned,
                "sheets": last_run.sheets_scanned, "shipments": last_run.shipments_found,
                "new": last_run.shipments_new, "changed": last_run.shipments_changed,
                "notifications": last_run.notifications, "exceptions": last_run.exceptions,
                "error": last_run.error},
        )
        data["v3"] = _dashboard_v3(s, p, ships, today)
    return render(request, "dashboard.html", **data)


def _dashboard_v3(s, p, ships: list[Shipment], today: dt.date) -> dict[str, Any]:
    """Tile v3: email cho duyet, task qua han, booking can xu ly, Weekly Planning."""
    from ..core import workflow as wf
    from ..db.models_v3 import Booking, BookingShipment, WorkflowTask
    from ..services import weekly_service
    from .routes_email import queue_counts

    codes = [sh.shipment_code for sh in ships]
    q = queue_counts(s, p)
    tstmt = select(WorkflowTask.due_date).where(WorkflowTask.status.not_in(tuple(wf.DONE)),
                                                WorkflowTask.due_date.is_not(None), WorkflowTask.due_date <= today)
    if p.role == "user":
        tstmt = tstmt.where(WorkflowTask.owner == p.name)
    else:
        tstmt = tstmt.where(WorkflowTask.shipment_code.in_(codes or ["-"]))
    dues = [d for (d,) in s.execute(tstmt).all()]
    attention = s.execute(
        select(func.count(func.distinct(Booking.id))).join(BookingShipment, BookingShipment.booking_id == Booking.id)
        .where(BookingShipment.shipment_code.in_(codes or ["-"]),
               Booking.status.in_(("REQUESTED", "PENDING", "ON_HOLD", "DELAYED", "CANCEL_REQUESTED",
                                   "REBOOK_REQUESTED")))).scalar_one()
    weekly = weekly_service.status(s, engine().settings, user="" if p.can("shipments.view_all") else p.name)
    weekly.pop("rows", None)
    return {"emails": q["REVIEW"] + q["CONFLICT"] + q["UNMATCHED"], "conflicts": q["CONFLICT"],
            "tasks_overdue": sum(d < today for d in dues), "tasks_today": sum(d == today for d in dues),
            "bookings_attention": attention, "weekly": weekly}


# ---------------------------------------------------------------------------
# Danh sach lo hang (dang bang, loc nhanh) - luoi 47 cot nam o /followup
# ---------------------------------------------------------------------------


@router.get("/shipments", response_class=HTMLResponse)
def shipments(request: Request, status: str = "", buyer: str = "", assigned: str = "", q: str = "",
              days: int = 0, bucket: str = ""):
    p = current(request)
    today = dt.date.today()
    with session_scope() as s:
        stmt = svc.apply_filters(svc.visible_stmt(p), {"status": status, "buyer": buyer,
                                                        "assigned": assigned, "q": q})
        if days:
            stmt = stmt.where(Shipment.exfty_date.is_not(None),
                              Shipment.exfty_date <= today + dt.timedelta(days=days),
                              Shipment.exfty_date >= today)
        rows = [sh for sh in s.execute(svc.order_like_followup(stmt).limit(1000)).scalars()]
        if bucket:
            rows = [sh for sh in rows if bucket_of(sh, today) == bucket]
        data = [row_dict(sh, today) for sh in rows]
        buyers = sorted({r["buyer"] for r in data if r["buyer"]})
        users = sorted(active_users(s)) if p.can("shipments.view_all") else []
    return render(request, "shipments.html", rows=data, buyers=buyers, users=users,
                  f={"status": status, "buyer": buyer, "assigned": assigned, "q": q, "days": days,
                     "bucket": bucket}, statuses=st.ALL_STATUSES, bucket_labels=BUCKETS)


@router.get("/shipments/{code}", response_class=HTMLResponse)
def shipment_detail(request: Request, code: str):
    p = current(request)
    today = dt.date.today()
    with session_scope() as s:
        try:
            sh = svc.get_visible(s, p, code)
        except svc.ServiceError:
            return render(request, "error.html", status_code=404, message="Không tìm thấy lô hàng")
        alloc = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one_or_none()
        overrides = list(s.execute(
            select(UserOverride).where(UserOverride.shipment_code == code, UserOverride.active.is_(True))
        ).scalars())
        audits = list(s.execute(
            select(AuditLog).where(AuditLog.entity_id == code).order_by(AuditLog.id.desc()).limit(60)
        ).scalars())
        notes = list(s.execute(
            select(Notification).where(Notification.shipment_code == code)
            .order_by(Notification.id.desc()).limit(20)
        ).scalars())
        history = list(s.execute(
            select(AssignmentHistory).where(AssignmentHistory.shipment_code == code)
            .order_by(AssignmentHistory.id.desc()).limit(30)
        ).scalars())
        ms = compute_milestones(sh.exfty_date, engine().settings.deadlines) if sh.exfty_date else None
        tmpl = engine().template
        data = row_dict(sh, today)
        data.update({
            "status_reason": sh.status_reason or {},
            "invoices": [i.invoice_no for i in sh.invoices],
            "declarations": [d.declaration_no for d in sh.declarations],
            "source": "Tạo trên web" if sh.origin == "WEB"
            else f"{sh.source_file_code} / {sh.source_sheet} / dòng {sh.source_row}",
            "data_confidence": sh.data_confidence, "group_key": sh.group_key, "remark": sh.remark,
            "hold_reason": sh.hold_reason, "booking_no": sh.booking_no, "etd": sh.etd, "eta": sh.eta,
            "vessel": sh.vessel, "container_type_user": sh.container_type_user,
            "container_no": sh.container_no, "seal_no": sh.seal_no, "closing_time": sh.closing_time,
            "go": sh.go, "derived_notes": sh.derived_notes or {}, "user_color": sh.user_color,
            "version": sh.version, "archived": sh.archived, "updated_by": sh.updated_by,
            "updated_at": sh.updated_at, "qty": sh.final_export_qty or sh.order_qty,
            "booking_status": sh.booking_status, "hold_status": sh.hold_status, "ship_status": sh.ship_status,
        })
        fields = [{"label": c.label, "field": c.field, "value": (sh.row_data or {}).get(c.field),
                   "src": ((sh.field_src or {}).get(c.field) or {}).get("src", c.source),
                   "by": ((sh.field_src or {}).get(c.field) or {}).get("by", ""),
                   "formula": c.formula is not None} for c in tmpl.columns]
        ctx = dict(
            sh=data, fields=fields, alloc=_alloc_dict(alloc),
            overrides=[{"field": o.field, "value": o.user_value, "user": o.user_name, "note": o.note,
                        "at": o.created_at} for o in overrides],
            audits=[{"ts": a.ts, "actor": f"{a.actor_type}:{a.actor_name}", "action": a.action,
                     "field": a.field, "old": a.old_value, "new": a.new_value, "note": a.note} for a in audits],
            notifications=[{"ts": n.created_at, "cat": n.category, "sev": n.severity, "title": n.title,
                            "body": n.body} for n in notes],
            history=[{"ts": h.ts, "kind": h.kind, "from": h.from_user, "to": h.to_user, "by": h.by_user,
                      "method": h.method, "note": h.note} for h in history],
            milestones=ms.as_dict() if ms else None, statuses=st.ALL_STATUSES,
            users=sorted((u.name, u.label) for u in active_users(s).values()
                         if u.role in ("user", "manager", "admin")) if p.can("shipments.assign") else [],
            can_edit=svc.can_edit(p, sh),
            forwarder_candidates=(sh.derived_notes or {}).get("forwarder_candidates", []),
            msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
        )
        from .routes_work import shipment_v3_context

        ctx.update(shipment_v3_context(s, p, sh, today))
    return render(request, "shipment_detail.html", **ctx)


@router.post("/shipments/{code}/override")
def set_override(request: Request, code: str, field: str = Form(...), value: str = Form(""),
                 note: str = Form("")):
    p = current(request)
    with session_scope() as s:
        sh = svc.get_visible(s, p, code)
        if not svc.can_edit(p, sh):
            raise PermissionDenied()
        old = s.execute(select(UserOverride).where(
            UserOverride.shipment_code == code, UserOverride.field == field,
            UserOverride.active.is_(True))).scalar_one_or_none()
        if old is not None:
            old.active = False
        if value:
            s.add(UserOverride(shipment_code=code, field=field, user_value=value, user_name=p.name, note=note))
        if field == "status":
            sh.effective_status = value or sh.auto_status
            sh.version = (sh.version or 1) + 1
        log_audit(s, "USER_OVERRIDE", actor_type="USER", actor_name=p.name, entity="shipment",
                  entity_id=code, field=field, old_value=old.user_value if old else "",
                  new_value=value or "(bo override)", note=note or "USER > AUTOMATION")
    return RedirectResponse(f"/shipments/{code}", status_code=303)


@router.post("/shipments/{code}/assign")
def assign_one(request: Request, code: str, to_user: str = Form(""), kind: str = Form("PRIMARY"),
               note: str = Form("")):
    p = require(request, "shipments.assign")
    with session_scope() as s:
        sh = svc.get_visible(s, p, code)
        try:
            assign(s, [sh], to_user, by=p.name, kind=kind, method="MANUAL", note=note)
        except AssignmentError as exc:
            return render(request, "error.html", status_code=400, message=str(exc))
    return RedirectResponse(f"/shipments/{code}", status_code=303)


@router.post("/shipments/{code}/forwarder")
def set_forwarder(request: Request, code: str, forwarder: str = Form("")):
    p = current(request)
    with session_scope() as s:
        sh = svc.get_visible(s, p, code)
        if not svc.can_edit(p, sh):
            raise PermissionDenied()
        old = sh.forwarder
        sh.forwarder = forwarder.strip()[:120]
        sh.forwarder_status = fw.MANUAL if sh.forwarder else ""
        sh.version = (sh.version or 1) + 1
        if not sh.forwarder:
            engine()._resolve_forwarder(sh)
        log_audit(s, "FORWARDER_SET", actor_type="USER", actor_name=p.name, entity="shipment",
                  entity_id=code, field="forwarder", old_value=old, new_value=sh.forwarder or "(theo rule)")
    return RedirectResponse(f"/shipments/{code}", status_code=303)


@router.post("/shipments/{code}/archive")
def archive_one(request: Request, code: str, archived: str = Form("yes"), note: str = Form("")):
    p = require(request, "shipments.archive")
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one_or_none()
        if sh is None:
            return render(request, "error.html", status_code=404, message="Không tìm thấy lô hàng")
        svc.set_archived(s, p, [sh], archived == "yes", note)
    return RedirectResponse(f"/shipments/{code}", status_code=303)


@router.post("/shipments/{code}/open-source")
def open_source(request: Request, code: str):
    """Chi mo duoc file khi dang ngoi tai chinh may chu (file nam tren may do)."""
    from ..notify.channels import open_file
    from .deps import is_local

    p = current(request)
    if not is_local(request):
        return JSONResponse({"ok": False, "error": "Chỉ mở được file trên chính máy chủ"}, status_code=400)
    with session_scope() as s:
        sh = svc.get_visible(s, p, code)
        fcfg = engine().settings.file_by_code(sh.source_file_code)
        path = str(engine().settings.resolve_path(fcfg.path)) if fcfg else ""
        ok = open_file(path, sh.source_sheet, sh.source_row)
    return JSONResponse({"ok": ok, "path": path})


# ---------------------------------------------------------------------------
# Exception Center
# ---------------------------------------------------------------------------


@router.get("/exceptions", response_class=HTMLResponse)
def exceptions_page(request: Request, show: str = "OPEN", code: str = ""):
    p = current(request)
    with session_scope() as s:
        stmt = select(ExceptionRecord).order_by(ExceptionRecord.id.desc())
        if show != "ALL":
            stmt = stmt.where(ExceptionRecord.status == "OPEN")
        if code:
            stmt = stmt.where(ExceptionRecord.code == code)
        if not p.can("exceptions.view_all"):
            mine = [c for (c,) in s.execute(
                select(Shipment.shipment_code).where(
                    or_(Shipment.assigned_to == p.name, Shipment.backup_owner == p.name))).all()]
            stmt = stmt.where(ExceptionRecord.shipment_code.in_(mine or ["-"]))
        rows = list(s.execute(stmt.limit(500)).scalars())
        data = [{
            "id": r.id, "code": r.code, "severity": r.severity, "reason": r.reason,
            "file": Path(r.source_file).name if r.source_file else "", "sheet": r.source_sheet,
            "row": r.source_row, "col": r.source_column, "value": r.value, "action": r.suggested_action,
            "status": r.status, "created": r.created_at, "shipment": r.shipment_code,
            "payload": r.payload or {},
        } for r in rows]
        codes = [c for (c,) in s.execute(select(ExceptionRecord.code).distinct().order_by(
            ExceptionRecord.code)).all()]
    return render(request, "exceptions.html", rows=data, show=show, code=code, codes=codes)


@router.post("/exceptions/{exc_id}/resolve")
def resolve_exc(request: Request, exc_id: int, note: str = Form(""), status: str = Form("RESOLVED"),
                decision: str = Form("")):
    p = require(request, "exceptions.resolve")
    with session_scope() as s:
        rec = s.get(ExceptionRecord, exc_id)
        if rec is not None and rec.code == "EX015" and decision == "excel":
            sh = s.execute(select(Shipment).where(Shipment.shipment_code == rec.shipment_code)).scalar_one()
            payload = rec.payload or {}
            svc.accept_excel_value(s, engine(), p, sh, payload.get("field", ""), payload.get("excel"))
            note = (note + " | Nhan gia tri Excel").strip(" |")
        elif rec is not None and rec.code == "EX015":
            note = (note + " | Giu gia tri web").strip(" |")
        resolve_exception(s, exc_id, p.name, note, status)
    return RedirectResponse("/exceptions", status_code=303)


# ---------------------------------------------------------------------------
# Thong bao (moi nguoi thay thong bao cua minh; Quan ly thay ca thong bao chung)
# ---------------------------------------------------------------------------


def _mine(p):
    """Thong bao cua toi. Quan ly nhan them thong bao chung (khong co nguoi phu trach)."""
    if p.is_manager:
        return or_(Notification.recipient == p.name, Notification.recipient == "")
    return Notification.recipient == p.name


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, scope: str = "mine"):
    p = current(request)
    with session_scope() as s:
        stmt = select(Notification)
        if scope != "all" or not p.is_manager:
            stmt = stmt.where(_mine(p))
        rows = list(s.execute(stmt.order_by(Notification.id.desc()).limit(300)).scalars())
        data = [{"id": n.id, "ts": n.created_at, "cat": n.category, "sev": n.severity, "title": n.title,
                 "body": n.body, "shipment": n.shipment_code, "recipient": n.recipient,
                 "read": n.read_at, "ack": n.acknowledged_at} for n in rows]
    return render(request, "notifications.html", rows=data, scope=scope)


@router.post("/notifications/{nid}/ack")
def ack_notification(request: Request, nid: int):
    p = current(request)
    with session_scope() as s:
        n = s.get(Notification, nid)
        if n is not None and (n.recipient == p.name or p.is_manager):
            n.acknowledged_at = utcnow()
            n.read_at = n.read_at or utcnow()
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/read-all")
def read_all(request: Request):
    p = current(request)
    with session_scope() as s:
        stmt = select(Notification).where(Notification.read_at.is_(None), _mine(p))
        for n in s.execute(stmt).scalars():
            n.read_at = utcnow()
    return RedirectResponse("/notifications", status_code=303)


@router.get("/api/notifications/unread")
def api_unread(request: Request, since: int = 0):
    """Chuong thong bao + Desktop Notification (trinh duyet tu hoi moi 60 giay)."""
    p = current(request)
    with session_scope() as s:
        stmt = select(Notification).where(Notification.read_at.is_(None), _mine(p))
        rows = list(s.execute(stmt.order_by(Notification.id.desc()).limit(50)).scalars())
        fresh = [{"id": n.id, "title": n.title, "body": n.body, "severity": n.severity,
                  "shipment": n.shipment_code} for n in rows if n.id > since and n.severity != "INFO"]
    return {"count": len(rows), "latest_id": rows[0].id if rows else since, "fresh": fresh[:5]}


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, q: str = "", actor: str = ""):
    require(request, "audit.view")
    with session_scope() as s:
        stmt = select(AuditLog)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(AuditLog.entity_id.like(like) | AuditLog.action.like(like)
                              | AuditLog.field.like(like))
        if actor:
            stmt = stmt.where(AuditLog.actor_name == actor)
        rows = [{"ts": a.ts, "actor": f"{a.actor_type}:{a.actor_name}", "action": a.action,
                 "entity": a.entity, "entity_id": a.entity_id, "field": a.field, "old": a.old_value,
                 "new": a.new_value, "note": a.note, "source": a.source}
                for a in s.execute(stmt.order_by(AuditLog.id.desc()).limit(500)).scalars()]
    return render(request, "audit.html", rows=rows, q=q, actor=actor)


@router.get("/api/status")
def api_status(request: Request):
    p = current(request)
    sched = CTX["scheduler"]
    state = sched.state if sched else None
    with session_scope() as s:
        counts: dict[str, int] = {}
        for sh in s.execute(svc.visible_stmt(p)).scalars():
            counts[sh.effective_status] = counts.get(sh.effective_status, 0) + 1
    return {
        "user": p.name, "role": p.role,
        "running": bool(state and state.running), "paused": bool(state and state.paused),
        "last_scan": state.last_scan.isoformat() if state and state.last_scan else None,
        "next_scan": state.next_scan.isoformat() if state and state.next_scan else None,
        "counts": counts,
    }


def _alloc_dict(alloc: ContainerAllocation | None) -> dict[str, Any] | None:
    if alloc is None:
        return None
    return {
        "total_cbm": alloc.total_cbm, "qty_40hc": alloc.qty_40hc, "qty_40dc": alloc.qty_40dc,
        "qty_20st": alloc.qty_20st, "capacity": alloc.estimated_capacity, "excess": alloc.excess_capacity,
        "type": alloc.container_type, "mode": alloc.booking_mode, "status": alloc.allocation_status,
        "source": alloc.allocation_source, "note": alloc.note,
    }

