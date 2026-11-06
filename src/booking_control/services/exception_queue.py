"""Hang doi ngoai le - "Exception Only" (SRSv5.5 muc 10).

SRS: thay vi bat nhan vien ra soat 100 lo moi ngay, he thong tu kiem ca 100 va nhan vien
chi nhin "⚠ 5 lo can xu ly".

Tam loai theo dung danh sach trong SRS:

    1. Booking overdue        7. Allocation incomplete
    2. Email conflict         8. ETD changed
    3. Missing CBM
    4. Booking cancelled
    5. Rebook required
    6. Document missing

Moi muc tra ve: so luong, duong dan mo thang den cho can xu ly, va muc do khan.
Chi dem trong pham vi NGUOI DANG DANG NHAP duoc xem.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import ExceptionRecord, Shipment
from ..web.auth import Principal
from . import shipment_service as svc

CRITICAL = "CRITICAL"
WARNING = "WARNING"


@dataclass(slots=True)
class QueueItem:
    key: str
    label: str
    count: int
    url: str
    severity: str = WARNING
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "count": self.count, "url": self.url,
                "severity": self.severity, "hint": self.hint}


def build(s: Session, p: Principal, today: dt.date | None = None) -> list[dict[str, Any]]:
    """Dem tung loai ngoai le. Muc nao bang 0 thi khong hien."""
    today = today or dt.date.today()
    visible = svc.visible_stmt(p)
    codes = [c for (c,) in s.execute(visible.with_only_columns(Shipment.shipment_code)).all()]

    def count(stmt) -> int:
        return s.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()

    def exc(*exception_codes: str) -> int:
        q = select(func.count()).select_from(ExceptionRecord).where(
            ExceptionRecord.status == "OPEN", ExceptionRecord.code.in_(exception_codes))
        if not p.can("exceptions.view_all"):
            q = q.where(ExceptionRecord.shipment_code.in_(codes or ["-"]))
        return s.execute(q).scalar_one()

    items = [
        # 1. Booking overdue - chua book ma sat / qua han
        QueueItem(
            "booking_overdue", "Booking quá hạn / sắp quá hạn",
            count(visible.where(Shipment.deadline_warning != "")),
            "/followup?warn=1&hide_done=1", CRITICAL,
            "Chưa có booking mà ngày xuất đã gần",
        ),
        # 2. Email conflict - email va PDF khong khop
        QueueItem(
            "email_conflict", "Email ≠ PDF, cần bạn chọn",
            _email_conflicts(s, p), "/emails?status=CONFLICT", CRITICAL,
            "Hệ thống không tự chọn khi hai nguồn mâu thuẫn",
        ),
        # 3. Missing CBM - thieu CBM thi khong tinh duoc container
        QueueItem(
            "missing_cbm", "Thiếu CBM",
            count(visible.where(
                Shipment.final_cbm.is_(None),
                Shipment.effective_status.not_in(("COMPLETED", "CANCELLED")))),
            "/exceptions?code=EX004", WARNING,
            "Không có CBM thì không tính được container",
        ),
        # 4. Booking cancelled
        QueueItem(
            "booking_cancelled", "Booking bị hủy",
            count(visible.where(Shipment.booking_status == "CANCELLED")),
            "/bookings?status=CANCELLED", CRITICAL,
            "Lô đã hủy booking, cần đặt lại hoặc gộp lô sau",
        ),
        # 5. Rebook required - BOOKING NO doi, can xac nhan
        QueueItem(
            "rebook", "Cần xác nhận rebook", exc("EX019"),
            "/exceptions?code=EX019", CRITICAL,
            "Số booking thay đổi — xác nhận đây là booking mới hay gõ nhầm",
        ),
        # 6. Document missing
        QueueItem(
            "document_missing", "Thiếu chứng từ", _missing_documents(s, p, today),
            "/tasks?filter=missing_docs", WARNING,
            "Chứng từ bắt buộc chưa nộp và đã tới hạn",
        ),
        # 7. Allocation incomplete - split booking chua phan bo du
        QueueItem(
            "allocation", "Booking chưa phân bổ đủ", exc("EX018", "EX014", "EX009"),
            "/exceptions?code=EX018", WARNING,
            "Tổng số lượng các booking chưa bằng số lượng lô",
        ),
        # 8. ETD changed
        QueueItem(
            "etd_changed", "Ngày xuất vừa thay đổi",
            count(visible.where(
                Shipment.exfty_changed_at.is_not(None),
                Shipment.exfty_changed_at >= _since(today, days=3))),
            "/exceptions?code=EX007", WARNING,
            "EX-FACT DATE đổi trong 3 ngày qua — mọi deadline đã tính lại",
        ),
    ]
    return [i.as_dict() for i in items if i.count]


def _since(today: dt.date, days: int) -> dt.datetime:
    return dt.datetime.combine(today - dt.timedelta(days=days), dt.time.min)


def _email_conflicts(s: Session, p: Principal) -> int:
    try:
        from ..web.routes_email import queue_counts

        return int(queue_counts(s, p).get("CONFLICT", 0))
    except Exception:  # noqa: BLE001 - tinh nang email co the chua bat
        return 0


def _missing_documents(s: Session, p: Principal, today: dt.date) -> int:
    try:
        from ..core import workflow as wf
        from ..db.models_v3 import WorkflowTask

        stmt = select(func.count()).select_from(WorkflowTask).where(
            WorkflowTask.status.not_in(tuple(wf.DONE)),
            WorkflowTask.due_date.is_not(None),
            WorkflowTask.due_date < today,
        )
        if p.role == "user":
            stmt = stmt.where(WorkflowTask.owner == p.name)
        return s.execute(stmt).scalar_one()
    except Exception:  # noqa: BLE001
        return 0
