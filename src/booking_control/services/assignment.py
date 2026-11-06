"""Phan cong lo hang cho nguoi phu trach (SRS v2 muc 17, 34).

    Manager/Admin: Assign, Reassign, Bulk Assign (theo Buyer / Forwarder / EX-FACT / Mode)
    Automation   : lo hang MOI chua co nguoi phu trach -> giao theo rule, roi theo chu file
                   Follow Up. KHONG BAO GIO doi nguoi phu trach Quan ly da giao tay.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_audit
from ..core.forwarder import _mode
from ..core.normalize import norm_key
from ..db.models import AssignmentHistory, Notification, Shipment, User, utcnow
from ..settings import AssignmentCfg

ASSIGNABLE_ROLES = ("user", "manager", "admin")


class AssignmentError(ValueError):
    pass


@dataclass
class AssignOutcome:
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    by_user: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def active_users(s: Session) -> dict[str, User]:
    return {u.name: u for u in s.execute(select(User).where(User.active.is_(True))).scalars()}


def assign(
    s: Session, shipments: list[Shipment], to_user: str, *, by: str, kind: str = "PRIMARY",
    method: str = "MANUAL", note: str = "", notify: bool = True,
) -> AssignOutcome:
    """Giao / giao lai. to_user = "" -> bo phan cong (tra ve NOT_ASSIGNED)."""
    kind = kind.upper()
    if kind not in ("PRIMARY", "BACKUP"):
        raise AssignmentError("kind phai la PRIMARY hoac BACKUP")
    if to_user:
        u = active_users(s).get(to_user)
        if u is None:
            raise AssignmentError(f"Nhân viên '{to_user}' không tồn tại hoặc đã khóa")
        if u.role not in ASSIGNABLE_ROLES:
            raise AssignmentError(f"'{to_user}' có vai trò {u.role} - không giao lô hàng được")

    out = AssignOutcome()
    for sh in shipments:
        attr = "assigned_to" if kind == "PRIMARY" else "backup_owner"
        old = getattr(sh, attr) or ""
        if old == to_user:
            out.unchanged.append(sh.shipment_code)
            continue
        setattr(sh, attr, to_user)
        if kind == "PRIMARY":
            sh.assigned_by, sh.assigned_at = by, utcnow()
        sh.version = (sh.version or 0) + 1
        s.add(AssignmentHistory(shipment_code=sh.shipment_code, kind=kind, from_user=old, to_user=to_user,
                                by_user=by, method=method, note=note))
        log_audit(s, "ASSIGNED" if to_user else "UNASSIGNED",
                  actor_type="AUTO" if method in ("RULE", "FILE_OWNER") else "USER",
                  actor_name=by, entity="shipment", entity_id=sh.shipment_code,
                  field=attr, old_value=old, new_value=to_user or "(bo)", note=f"{method} {note}".strip())
        out.changed.append(sh.shipment_code)
        if to_user:
            out.by_user[to_user].append(sh.jo or sh.shipment_code)
    if notify:
        notify_assignees(s, out, kind=kind, by=by)
    return out


def notify_assignees(s: Session, out: AssignOutcome, *, kind: str = "PRIMARY", by: str = "") -> None:
    """1 thong bao gom nhom cho moi nguoi duoc giao (khong ban 30 thong bao cho 30 lo)."""
    for user, jos in out.by_user.items():
        if not jos or user == by:
            continue
        role = "phụ trách chính" if kind == "PRIMARY" else "dự phòng"
        preview = ", ".join(jos[:8]) + (f" ... (+{len(jos) - 8})" if len(jos) > 8 else "")
        s.add(Notification(
            category="ASSIGNED", severity="INFO", recipient=user,
            title=f"Bạn được giao {len(jos)} lô hàng ({role})",
            body=f"{by or 'Hệ thống'} vừa giao: {preview}",
            context={"count": len(jos), "by": by}, dedup_key="",
            delivered_at=utcnow(),
        ))


def rule_for(cfg: AssignmentCfg, sh: Shipment) -> tuple[str, str] | None:
    buyer = norm_key(sh.buyer_code or sh.buyer_raw)
    for r in cfg.rules:
        if r.buyer and buyer not in {norm_key(b) for b in r.buyer}:
            continue
        if r.forwarder and norm_key(sh.forwarder) not in {norm_key(f) for f in r.forwarder}:
            continue
        if r.mode and _mode(sh.ship_mode) not in {_mode(m) for m in r.mode}:
            continue
        if r.site and (sh.site or "").upper() not in {x.upper() for x in r.site}:
            continue
        return r.user, r.backup
    return None


def auto_assign(s: Session, cfg: AssignmentCfg, shipments: list[Shipment]) -> AssignOutcome:
    """Giao tu dong cac lo CHUA co nguoi phu trach. Tra ve de gom thong bao."""
    total = AssignOutcome()
    if not cfg.auto_assign_new:
        return total
    users = active_users(s)
    for sh in shipments:
        if sh.assigned_to or sh.archived:
            continue
        target, backup, method = "", "", ""
        hit = rule_for(cfg, sh)
        if hit and hit[0] in users:
            target, backup, method = hit[0], hit[1], "RULE"
        elif cfg.use_file_owner and sh.owner_user in users and users[sh.owner_user].role in ASSIGNABLE_ROLES:
            target, method = sh.owner_user, "FILE_OWNER"
        if not target:
            continue
        o = assign(s, [sh], target, by="automation", method=method, notify=False)
        for u, j in o.by_user.items():
            total.by_user[u].extend(j)
        total.changed += o.changed
        if backup and backup in users and not sh.backup_owner:
            assign(s, [sh], backup, by="automation", kind="BACKUP", method=method, notify=False)
    notify_assignees(s, total, by="automation")
    return total


def workload(s: Session, today: dt.date | None = None) -> list[dict[str, Any]]:
    """Team Workload cho Dashboard Quan ly (SRS v2 muc 50)."""
    today = today or dt.date.today()
    rows: dict[str, dict[str, Any]] = {}
    users = active_users(s)
    for name, u in users.items():
        if u.role in ASSIGNABLE_ROLES:
            rows[name] = {"user": name, "label": u.label, "role": u.role, "total": 0, "overdue": 0,
                          "due3": 0, "hold": 0, "white": 0, "completed": 0}
    rows[""] = {"user": "", "label": "(Chưa phân công)", "role": "", "total": 0, "overdue": 0,
                "due3": 0, "hold": 0, "white": 0, "completed": 0}
    stmt = select(Shipment).where(Shipment.is_active.is_(True), Shipment.archived.is_(False))
    for sh in s.execute(stmt).scalars():
        r = rows.setdefault(sh.assigned_to, {"user": sh.assigned_to, "label": sh.assigned_to + " (đã khóa)",
                                             "role": "", "total": 0, "overdue": 0, "due3": 0, "hold": 0,
                                             "white": 0, "completed": 0})
        st = sh.effective_status
        if st in ("COMPLETED", "CANCELLED"):
            r["completed"] += 1
            continue
        r["total"] += 1
        if sh.exfty_date:
            days = (sh.exfty_date - today).days
            if days < 0:
                r["overdue"] += 1
            elif days <= 3:
                r["due3"] += 1
        if st == "RED":
            r["hold"] += 1
        if st == "WHITE":
            r["white"] += 1
    return sorted(rows.values(), key=lambda x: (x["user"] == "", x["label"]))
