"""Task B1-B15.1 va chung tu bat buoc cua tung lo (SRSv5 muc 27-31, 61-62).

    ensure_tasks()      sinh task con thieu, cap nhat han khi EX-FACT / ETD doi (task chua xong)
    task_rows()         trang thai hien thi: BLOCKED (buoc truoc chua xong) / OVERDUE / ...
    complete_task()     chan neu buoc truoc chua xong hoac thieu chung tu bat buoc
    checklist()         Document Matrix: chung tu nao da co / thieu / qua han
    deadline_events()   task qua han + chung tu qua han -> thong bao cho nguoi phu trach
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from ..audit import log_audit
from ..core import workflow as wf
from ..core.deadline import DeadlineEvent
from ..db.models import Shipment, SystemSetting, utcnow
from ..db.models_v3 import Booking, BookingShipment, Document, ShipmentEvent, WorkflowTask
from .booking_service import log_event


class WorkflowError(ValueError):
    pass


def _cfg(settings) -> dict[str, Any]:
    return settings.workflow or {}


def anchors_many(s: Session, ships: list[Shipment]) -> dict[str, dict[str, dt.date | None]]:
    """Moc tinh han cho nhieu lo trong 1 truy van: EX-FACT, ETD (booking hieu luc moi nhat), ngay tao booking."""
    latest: dict[str, Booking] = {}
    codes = [sh.shipment_code for sh in ships]
    for i in range(0, len(codes), 900):  # gioi han tham so SQLite
        for b, link in s.execute(select(Booking, BookingShipment).join(
                BookingShipment, BookingShipment.booking_id == Booking.id)
                .where(BookingShipment.shipment_code.in_(codes[i:i + 900]))).all():
            if b.status in ("CANCELLED", "REBOOKED"):
                continue
            cur = latest.get(link.shipment_code)
            if cur is None or (b.created_at, b.id) > (cur.created_at, cur.id):
                latest[link.shipment_code] = b
    out = {}
    for sh in ships:
        b = latest.get(sh.shipment_code)
        out[sh.shipment_code] = {"EXFTY": sh.exfty_date, "ETD": (b.etd if b else None) or sh.etd,
                                 "BOOKING": b.created_at.date() if b and b.created_at else None}
    return out


def anchors_for(s: Session, sh: Shipment) -> dict[str, dt.date | None]:
    return anchors_many(s, [sh])[sh.shipment_code]


def _steps_hash(steps: list[wf.StepDef]) -> str:
    raw = json.dumps([[st.code, st.name, st.seq, st.depends_on, st.required_docs] for st in steps], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()


def ensure_tasks(s: Session, settings, ships: list[Shipment], today: dt.date) -> int:
    """Sinh task con thieu + cap nhat han / nguoi phu trach. Doc dang tuple + ghi hang loat (10.000 lo van nhanh)."""
    cfg = _cfg(settings)
    steps = wf.load_steps(cfg)
    if not steps:
        return 0
    lo, hi = (cfg.get("generate_window_days") or [-30, 75])[:2]
    codes = [sh.shipment_code for sh in ships]
    existing: dict[str, dict[str, Any]] = defaultdict(dict)
    for i in range(0, len(codes), 900):
        for row in s.execute(select(WorkflowTask.id, WorkflowTask.shipment_code, WorkflowTask.step_code,
                                    WorkflowTask.status, WorkflowTask.due_date, WorkflowTask.manual_due,
                                    WorkflowTask.manual_status, WorkflowTask.owner)
                             .where(WorkflowTask.shipment_code.in_(codes[i:i + 900]))):
            existing[row.shipment_code][row.step_code] = row
    active = [sh for sh in ships if sh.exfty_date and not sh.archived and sh.effective_status != "CANCELLED"
              and (lo <= (sh.exfty_date - today).days <= hi or sh.shipment_code in existing)]
    anchors_all = anchors_many(s, active)
    docs_all = uploaded_docs(s, [sh.shipment_code for sh in active]) if any(st.auto_done for st in steps) else {}
    now = utcnow()
    new_rows: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    auto_events: list[dict[str, Any]] = []
    for sh in ships:
        mine = existing.get(sh.shipment_code, {})
        if sh.effective_status == "CANCELLED" and not sh.archived:  # lo huy -> dong task con mo, khong nhac nua
            updates += [{"id": r.id, "status": "CANCELLED", "note": "Lo hang da huy"}
                        for r in mine.values() if r.status not in wf.DONE]
    for sh in active:
        mine = existing.get(sh.shipment_code, {})
        anchors = anchors_all[sh.shipment_code]
        state = {k: r.status for k, r in mine.items()}
        ctx = {"booking_status": sh.booking_status, "ship_status": sh.ship_status,
               "effective_status": sh.effective_status, "docs": docs_all.get(sh.shipment_code, set())}
        for st in steps:
            if not wf.applicable(st, mode=sh.ship_mode, co_required=sh.co_required):
                continue
            due = wf.due_date(st.anchor, st.offset, anchors)
            row = mine.get(st.code)
            deps_done = all(state.get(d) in wf.DONE or d not in state for d in st.depends_on)
            auto = deps_done and wf.auto_done(st, ctx) and not (row is not None and row.manual_status)
            if row is None:
                new_rows.append({"shipment_code": sh.shipment_code, "step_code": st.code, "name": st.name,
                                 "seq": st.seq, "status": "COMPLETED" if auto else "PENDING", "due_date": due,
                                 "owner": sh.assigned_to or "", "depends_on": st.depends_on,
                                 "required_docs": st.required_docs, "completed_by": "automation" if auto else "",
                                 "completed_at": now if auto else None, "note": "", "manual_due": False,
                                 "manual_status": False})
                state[st.code] = "COMPLETED" if auto else "PENDING"
                continue
            if row.status in wf.DONE:
                continue
            ch: dict[str, Any] = {}
            if auto:
                ch.update(status="COMPLETED", completed_by="automation", completed_at=now)
                state[st.code] = "COMPLETED"
                auto_events.append({"event_type": "TASK_AUTO_COMPLETED", "shipment_code": sh.shipment_code,
                                    "source": "SYSTEM", "actor": "automation", "old_value": row.status,
                                    "new_value": "COMPLETED", "note": f"{st.code} {st.name}", "ts": now,
                                    "payload": {"auto_done": st.auto_done}})
            if not row.manual_due and row.due_date != due:
                ch["due_date"] = due
            if sh.assigned_to and row.owner != sh.assigned_to:
                ch["owner"] = sh.assigned_to
            if ch:
                updates.append({"id": row.id, **ch})
    if auto_events:
        s.execute(insert(ShipmentEvent), auto_events)
    if new_rows:
        s.execute(insert(WorkflowTask), new_rows)
    for keys in {tuple(sorted(u)) for u in updates}:  # ghi hang loat theo tung nhom cot
        s.execute(update(WorkflowTask), [u for u in updates if tuple(sorted(u)) == keys])
    # Doi workflow.yaml (ten buoc / thu tu / phu thuoc / chung tu) -> cap nhat task chua xong 1 lan
    digest = _steps_hash(steps)
    row = s.get(SystemSetting, "workflow.steps_hash")
    if row is None or (row.value or {}).get("hash") != digest:
        for st in steps:
            s.execute(update(WorkflowTask).where(WorkflowTask.step_code == st.code,
                                                 WorkflowTask.status.not_in(tuple(wf.DONE)))
                      .values(name=st.name, seq=st.seq, depends_on=st.depends_on, required_docs=st.required_docs)
                      .execution_options(synchronize_session=False))
        if row is None:
            s.add(SystemSetting(key="workflow.steps_hash", value={"hash": digest}))
        else:
            row.value = {"hash": digest}
    return len(new_rows)


def task_rows(s: Session, tasks: list[WorkflowTask], today: dt.date) -> list[dict[str, Any]]:
    codes = {t.shipment_code for t in tasks}
    all_tasks = {(t.shipment_code, t.step_code): t for t in s.execute(
        select(WorkflowTask).where(WorkflowTask.shipment_code.in_(codes or ["-"]))).scalars()}
    docs = uploaded_docs(s, list(codes))
    out = []
    for t in tasks:
        deps_done = all((all_tasks.get((t.shipment_code, d)) is None)
                        or all_tasks[(t.shipment_code, d)].status in wf.DONE for d in t.depends_on or [])
        missing_docs = [d for d in (t.required_docs or []) if d not in docs.get(t.shipment_code, set())]
        out.append({
            "id": t.id, "code": t.shipment_code, "step": t.step_code, "name": t.name, "seq": t.seq,
            "status": t.status, "display": wf.display_status(t.status, deps_done=deps_done, due=t.due_date,
                                                             today=today),
            "due": t.due_date, "owner": t.owner, "deps": t.depends_on or [], "missing_docs": missing_docs,
            "completed_by": t.completed_by, "completed_at": t.completed_at, "note": t.note,
            "days_left": (t.due_date - today).days if t.due_date else None,
        })
    return out


def set_task_status(s: Session, task: WorkflowTask, status: str, *, actor: str, note: str = "",
                    today: dt.date | None = None) -> None:
    if status not in ("PENDING", "IN_PROGRESS", "WAITING", "COMPLETED", "SKIPPED", "CANCELLED"):
        raise WorkflowError(f"Trang thai task khong hop le: {status}")
    if status == "COMPLETED":
        row = task_rows(s, [task], today or dt.date.today())[0]
        if row["display"] == "BLOCKED":
            raise WorkflowError(f"{task.step_code} chua lam duoc: buoc {', '.join(task.depends_on)} chua xong")
        if row["missing_docs"]:
            raise WorkflowError(f"{task.step_code} thieu chung tu: {', '.join(row['missing_docs'])} - hay tai len truoc")
        task.completed_by, task.completed_at = actor, utcnow()
    old = task.status
    task.status = status
    task.manual_status = True  # nguoi dung da quyet -> Automation khong tu doi lai
    task.note = note or task.note
    log_event(s, "TASK_" + status, shipment_code=task.shipment_code, actor=actor, old=old, new=status,
              note=f"{task.step_code} {task.name}")
    log_audit(s, "TASK_STATUS", actor_type="USER", actor_name=actor, entity="task", entity_id=str(task.id),
              field=task.step_code, old_value=old, new_value=status, note=note)


def uploaded_docs(s: Session, codes: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for d in s.execute(select(Document).where(Document.shipment_code.in_(codes or ["-"]),
                                              Document.current_version > 0,
                                              Document.status != "REJECTED")).scalars():
        out.setdefault(d.shipment_code, set()).add(d.doc_type)
    return out


def checklist(s: Session, settings, sh: Shipment, today: dt.date, *, docs: dict[str, Document] | None = None,
              anchors: dict[str, dt.date | None] | None = None) -> list[dict[str, Any]]:
    cfg = _cfg(settings)
    labels = {d["code"]: d.get("label", d["code"]) for d in cfg.get("document_types") or []}
    required = wf.required_documents(cfg.get("document_rules") or [], mode=sh.ship_mode, buyer=sh.buyer_code,
                                     forwarder=sh.forwarder)
    if docs is None:
        docs = {d.doc_type: d for d in s.execute(select(Document).where(
            Document.shipment_code == sh.shipment_code)).scalars()}
    anchors = anchors if anchors is not None else anchors_for(s, sh)
    out = []
    for code in required + [c for c in docs if c not in required]:
        d = docs.get(code)
        due = wf.document_due(code, cfg.get("document_deadlines") or {}, anchors)
        have = d is not None and d.current_version > 0 and d.status != "REJECTED"
        state = ("APPROVED" if d and d.status == "APPROVED" else "OK") if have else (
            "REJECTED" if d and d.status == "REJECTED" else ("OVERDUE" if due and due < today else "MISSING"))
        out.append({"code": code, "label": labels.get(code, code), "required": code in required, "due": due,
                    "state": state, "version": d.current_version if d else 0, "doc_id": d.id if d else None,
                    "status": d.status if d else ""})
    return out


def deadline_events(s: Session, settings, ships: list[Shipment], today: dt.date) -> list[DeadlineEvent]:
    """Task qua han / den han hom nay + chung tu bat buoc qua han -> thong bao (1 lan / ngay)."""
    events: list[DeadlineEvent] = []
    by_code = {sh.shipment_code: sh for sh in ships}
    tasks = list(s.execute(select(WorkflowTask).where(
        WorkflowTask.shipment_code.in_(list(by_code) or ["-"]),
        WorkflowTask.status.not_in(tuple(wf.DONE)), WorkflowTask.due_date.is_not(None),
        WorkflowTask.due_date <= today,
        WorkflowTask.due_date >= today - dt.timedelta(days=30))).scalars())  # qua han lau -> chi hien o trang Viec
    for row in task_rows(s, tasks, today):
        if row["display"] == "BLOCKED":
            continue
        sh = by_code[row["code"]]
        late = -row["days_left"]
        events.append(DeadlineEvent(
            category="TASK_OVERDUE" if late > 0 else "TASK_DUE", severity="WARNING" if late <= 2 else "CRITICAL",
            title=f"{sh.jo}: {row['step']} {row['name']} " + (f"qua han {late} ngay" if late else "den han hom nay"),
            body=("Thieu chung tu: " + ", ".join(row["missing_docs"])) if row["missing_docs"] else "Mo Viec cua toi",
            days_left=row["days_left"], due_date=row["due"],
            payload={"shipment_code": sh.shipment_code, "jo": sh.jo, "buyer": sh.buyer_code},
            dedup_key=f"TASK:{row['id']}:{today.isoformat()}"))
    near = [sh for sh in ships if sh.effective_status != "CANCELLED" and sh.exfty_date
            and abs((sh.exfty_date - today).days) <= 45]
    anchors_all = anchors_many(s, near)
    docs_all: dict[str, dict[str, Document]] = {}
    near_codes = [sh.shipment_code for sh in near]
    for i in range(0, len(near_codes), 900):
        for d in s.execute(select(Document).where(Document.shipment_code.in_(near_codes[i:i + 900]))).scalars():
            docs_all.setdefault(d.shipment_code, {})[d.doc_type] = d
    for sh in near:
        missing = [c for c in checklist(s, settings, sh, today, docs=docs_all.get(sh.shipment_code, {}),
                                        anchors=anchors_all[sh.shipment_code])
                   if c["required"] and c["state"] == "OVERDUE"]
        if missing:
            events.append(DeadlineEvent(
                category="DOCUMENT_MISSING", severity="WARNING",
                title=f"{sh.jo}: thieu chung tu {', '.join(c['code'] for c in missing)}",
                body="Tai len o trang lo hang > Chung tu", days_left=(sh.exfty_date - today).days,
                payload={"shipment_code": sh.shipment_code, "jo": sh.jo, "buyer": sh.buyer_code},
                dedup_key=f"DOC:{sh.shipment_code}:{today.isoformat()}"))
    return events
