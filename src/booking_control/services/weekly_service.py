"""Weekly Planning tuan sau: danh sach lo, con thieu gi, chot, nhac 12:00 thu 6 (SRSv5 muc 36).

Trang thai chot luu trong system_settings: weekly.confirmed.<YYYY-Www> = {by, at, incomplete}.
Job weekly_planning_check (moi gio) goi check_reminders(): moi moc nhac chi gui 1 lan / tuan.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_audit
from ..core import weekly as wk
from ..db.models import ContainerAllocation, Notification, Shipment, SystemSetting, User, utcnow


def _key(label: str) -> str:
    return f"weekly.confirmed.{label}"


def week_rows(s: Session, start: dt.date, end: dt.date, *, user: str = "") -> list[dict[str, Any]]:
    stmt = select(Shipment).where(Shipment.is_active.is_(True), Shipment.archived.is_(False),
                                  Shipment.exfty_date.between(start, end),
                                  Shipment.effective_status != "CANCELLED")
    if user:
        stmt = stmt.where((Shipment.assigned_to == user) | (Shipment.backup_owner == user))
    ships = list(s.execute(stmt.order_by(Shipment.exfty_date, Shipment.buyer_code, Shipment.jo)).scalars())
    allocs = {a.group_key: a.container_type for a in s.execute(select(ContainerAllocation).where(
        ContainerAllocation.group_key.in_([sh.group_key for sh in ships] or ["-"]))).scalars()}
    rows = []
    for sh in ships:
        r = {"code": sh.shipment_code, "jo": sh.jo, "no": sh.shipment_no, "buyer": sh.buyer_code or sh.buyer_raw,
             "exfty": sh.exfty_date, "assigned_to": sh.assigned_to, "booking_status": sh.booking_status,
             "booking_no": sh.booking_no, "forwarder": sh.forwarder, "mode": sh.ship_mode,
             "qty": sh.final_export_qty or sh.order_qty, "container": sh.container_type_user or allocs.get(sh.group_key, ""),
             "status": sh.effective_status}
        r["missing"] = wk.missing_items(r)
        rows.append(r)
    return rows


def status(s: Session, settings, now: dt.datetime | None = None, *, user: str = "") -> dict[str, Any]:
    now = now or dt.datetime.now()
    cfg = settings.weekly_planning
    start, end = wk.planning_week(now.date())
    label = wk.week_label(start)
    rows = week_rows(s, start, end, user=user)
    row = s.get(SystemSetting, _key(label))
    confirmed = (row.value if row else None) or None
    deadline = wk.deadline_at(now.date(), cfg)
    incomplete = [r for r in rows if r["missing"]]
    return {
        "enabled": cfg.enabled, "label": label, "start": start, "end": end, "deadline": deadline,
        "rows": rows, "total": len(rows), "incomplete": len(incomplete), "confirmed": confirmed,
        "late": confirmed is None and now >= deadline,
        "hours_left": round((deadline - now).total_seconds() / 3600, 1),
    }


def confirm(s: Session, settings, *, actor: str, now: dt.datetime | None = None, note: str = "") -> dict[str, Any]:
    now = now or dt.datetime.now()
    st = status(s, settings, now)
    value = {"by": actor, "at": now.isoformat(timespec="minutes"), "incomplete": st["incomplete"],
             "total": st["total"], "late": now >= st["deadline"], "note": note}
    row = s.get(SystemSetting, _key(st["label"]))
    if row is None:
        s.add(SystemSetting(key=_key(st["label"]), value=value))
    else:
        row.value = value
    log_audit(s, "WEEKLY_CONFIRMED", actor_type="USER", actor_name=actor, entity="weekly", entity_id=st["label"],
              new_value=f"{st['total']} lo, con thieu {st['incomplete']}", note=note)
    return value


def check_reminders(s: Session, settings, now: dt.datetime | None = None) -> dict[str, Any]:
    """Job moi gio: nhac theo moc (thu 5 / thu 6 sang / 11h) + bao tre han sau 12:00 thu 6."""
    now = now or dt.datetime.now()
    cfg = settings.weekly_planning
    if not cfg.enabled:
        return {"skipped": "disabled"}
    st = status(s, settings, now)
    if st["confirmed"]:
        return {"week": st["label"], "confirmed": True}
    managers = [u.name for u in s.execute(select(User).where(User.active.is_(True),
                                                             User.role.in_(("admin", "manager")))).scalars()]
    owners = sorted({r["assigned_to"] for r in st["rows"] if r["missing"] and r["assigned_to"]})
    sent = 0
    rem = wk.due_reminder(now, cfg)
    if rem is not None:
        stage, severity = f"R{rem.stage}", rem.level.upper()
        title = (f"Weekly Planning {st['label']}: hạn {st['deadline']:%H:%M %d/%m} - "
                 f"còn {st['incomplete']}/{st['total']} lô thiếu thông tin")
    elif st["late"]:
        stage, severity = "LATE", "CRITICAL"
        title = f"Weekly Planning {st['label']} QUÁ HẠN {st['deadline']:%H:%M %d/%m} - chưa chốt"
    else:
        return {"week": st["label"], "sent": 0}
    for r in sorted(set(managers) | set(owners)) or [""]:
        mine = [x for x in st["rows"] if x["missing"] and x["assigned_to"] == r]
        body = ("Lô của bạn còn thiếu: " + "; ".join(f"{x['jo']} ({', '.join(x['missing'])})" for x in mine[:10])
                if mine else "Mở trang Weekly Planning để kiểm tra và chốt")
        key = f"WEEKLY:{st['label']}:{stage}:{r}"
        if s.execute(select(Notification.id).where(Notification.dedup_key == key)).first():
            continue
        s.add(Notification(category="WEEKLY_PLANNING", severity=severity, title=title, body=body, recipient=r,
                           dedup_key=key, context={"week": st["label"]}, delivered_at=utcnow()))
        sent += 1
    return {"week": st["label"], "stage": stage, "sent": sent, "incomplete": st["incomplete"]}
