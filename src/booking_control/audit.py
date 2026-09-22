"""Audit Log + Exception Center helpers (SRS muc 36, 55)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import AuditLog, ExceptionRecord, utcnow

# Ma loi chuan (DMS muc 33)
EX_CODES = {
    "EX001": "Thieu / sai ExFtyDate",
    "EX002": "Buyer chua duoc dinh nghia",
    "EX003": "Shipment nghi ngo trung lap",
    "EX004": "CBM khong hop le",
    "EX005": "Sheet chua xac dinh - can User xac nhan",
    "EX006": "File dang bi khoa",
    "EX007": "User da thay doi du lieu trong luc Automation xu ly",
    "EX008": "Thieu Buyer Rule",
    "EX009": "Khong tinh duoc container",
    "EX010": "Header Excel thay doi - mapping khong khop",
    "EX011": "Truong bat buoc bi trong",
    "EX012": "Khong ghi duoc file dich",
    "EX013": "Cong thuc Excel bi go de / thieu / khong khop",
    "EX014": "CONT TYPE User book khong du cho hoac khong doc duoc",
    "EX015": "Excel khac gia tri User/Quan ly da sua tren web (giu gia tri web)",
    "EX016": "Nguoi dung sua truong can Quan ly xem lai",
    "EX017": "Forwarder can xac nhan (nhieu rule khop / xung dot voi email)",
}


def log_audit(
    session: Session,
    action: str,
    *,
    actor_type: str = "AUTO",
    actor_name: str = "automation",
    entity: str = "",
    entity_id: str = "",
    field: str = "",
    old_value: Any = "",
    new_value: Any = "",
    source: str = "",
    note: str = "",
) -> AuditLog:
    row = AuditLog(
        actor_type=actor_type,
        actor_name=actor_name,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        field=field,
        old_value=_fmt(old_value),
        new_value=_fmt(new_value),
        source=source,
        note=note,
    )
    session.add(row)
    return row


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def raise_exception(
    session: Session,
    code: str,
    reason: str,
    *,
    severity: str = "WARNING",
    source_file: str = "",
    source_sheet: str = "",
    source_row: int | None = None,
    source_column: str = "",
    value: Any = "",
    suggested_action: str = "",
    shipment_code: str = "",
    payload: dict[str, Any] | None = None,
    dedup_key: str = "",
) -> ExceptionRecord | None:
    """Tao exception. Neu da co exception OPEN cung dedup_key thi khong tao trung."""
    dedup_key = dedup_key or f"{code}:{source_file}:{source_sheet}:{source_row}:{source_column}"
    existing = session.execute(
        select(ExceptionRecord)
        .where(ExceptionRecord.dedup_key == dedup_key)
        .where(ExceptionRecord.status == "OPEN")
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        existing.reason = reason
        existing.value = _fmt(value)
        return None

    rec = ExceptionRecord(
        code=code,
        severity=severity,
        source_file=source_file,
        source_sheet=source_sheet,
        source_row=source_row,
        source_column=source_column,
        value=_fmt(value),
        reason=reason or EX_CODES.get(code, ""),
        suggested_action=suggested_action,
        shipment_code=shipment_code,
        payload=payload or {},
        dedup_key=dedup_key,
    )
    session.add(rec)
    return rec


def resolve_exception(
    session: Session, exc_id: int, user: str, note: str = "", status: str = "RESOLVED"
) -> bool:
    rec = session.get(ExceptionRecord, exc_id)
    if rec is None:
        return False
    rec.status = status
    rec.resolved_at = utcnow()
    rec.resolved_by = user
    rec.resolution_note = note
    log_audit(
        session,
        "RESOLVE_EXCEPTION",
        actor_type="USER",
        actor_name=user,
        entity="exception",
        entity_id=str(exc_id),
        new_value=status,
        note=note,
    )
    return True


def open_exceptions(session: Session, limit: int = 200) -> list[ExceptionRecord]:
    return list(
        session.execute(
            select(ExceptionRecord)
            .where(ExceptionRecord.status == "OPEN")
            .order_by(ExceptionRecord.severity.desc(), ExceptionRecord.created_at.desc())
            .limit(limit)
        ).scalars()
    )
