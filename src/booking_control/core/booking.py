"""Vong doi booking + suy ra trang thai lo hang tu booking (SRSv5 muc 7-13, Appendix A).

Module thuan: nhan du lieu don gian (dataclass / dict), khong doc DB.

    1 lo -> nhieu booking (split)      : tong so luong phan bo phai du so luong lo
    nhieu lo -> 1 booking (combined)   : 1 booking nhieu dong booking_shipments
    Cancel                             : khong xoa, trang thai CANCELLED
    Rebook                             : booking MOI, original_booking_id = booking cu
    Hold                               : Booking van CONFIRMED ve mat dat cho nhung Hold Status = HOLD
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

# Booking da giu cho (tinh la "da book" cho mau dong)
HELD_SPACE = {"BOOKED", "CONFIRMED", "AMENDED", "ON_HOLD", "DELAYED", "PARTIALLY_SHIPPED", "DEPARTED",
              "COMPLETED"}
# Con hieu luc (chua huy / chua bi thay the)
ACTIVE = HELD_SPACE | {"REQUESTED", "PENDING", "CANCEL_REQUESTED", "REBOOK_REQUESTED"}
CLOSED = {"CANCELLED", "REBOOKED"}

# Chuyen trang thai hop le (tu -> den). Nguoi dung / email chi di theo cac duong nay.
TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"REQUESTED", "BOOKED", "CONFIRMED", "CANCELLED"},
    "REQUESTED": {"PENDING", "BOOKED", "CONFIRMED", "CANCELLED", "ON_HOLD"},
    "PENDING": {"BOOKED", "CONFIRMED", "CANCELLED", "ON_HOLD"},
    "BOOKED": {"CONFIRMED", "AMENDED", "ON_HOLD", "DELAYED", "CANCEL_REQUESTED", "CANCELLED",
               "PARTIALLY_SHIPPED", "DEPARTED", "COMPLETED"},
    "CONFIRMED": {"AMENDED", "ON_HOLD", "DELAYED", "CANCEL_REQUESTED", "CANCELLED", "PARTIALLY_SHIPPED",
                  "DEPARTED", "COMPLETED"},
    "AMENDED": {"AMENDED", "CONFIRMED", "ON_HOLD", "DELAYED", "CANCEL_REQUESTED", "CANCELLED",
                "PARTIALLY_SHIPPED", "DEPARTED", "COMPLETED"},
    "DELAYED": {"AMENDED", "CONFIRMED", "ON_HOLD", "CANCEL_REQUESTED", "CANCELLED", "PARTIALLY_SHIPPED",
                "DEPARTED", "COMPLETED"},
    "ON_HOLD": {"CONFIRMED", "AMENDED", "CANCEL_REQUESTED", "CANCELLED", "DELAYED"},
    "CANCEL_REQUESTED": {"CANCELLED", "CONFIRMED"},
    "CANCELLED": {"REBOOK_REQUESTED", "REBOOKED"},
    "REBOOK_REQUESTED": {"REBOOKED", "CANCELLED"},
    "PARTIALLY_SHIPPED": {"PARTIALLY_SHIPPED", "DEPARTED", "COMPLETED", "ON_HOLD", "DELAYED"},
    "DEPARTED": {"COMPLETED", "PARTIALLY_SHIPPED"},
    "REBOOKED": set(),
    "COMPLETED": set(),
}

BOOKING_STATUSES_ALL = tuple(TRANSITIONS)

STATUS_LABELS = {
    "DRAFT": "Nháp", "REQUESTED": "Đã gửi yêu cầu", "PENDING": "Chờ xác nhận", "BOOKED": "Đã book",
    "CONFIRMED": "Đã xác nhận", "AMENDED": "Đã điều chỉnh", "ON_HOLD": "Đang hold", "DELAYED": "Trễ lịch",
    "CANCEL_REQUESTED": "Đang xin hủy", "CANCELLED": "Đã hủy", "REBOOK_REQUESTED": "Cần rebook",
    "REBOOKED": "Đã rebook", "PARTIALLY_SHIPPED": "Xuất một phần", "DEPARTED": "Tàu đã chạy",
    "COMPLETED": "Hoàn tất",
}
# Thu tu goi y tren nut "Chuyen trang thai"
ACTION_ORDER = ("CONFIRMED", "BOOKED", "PENDING", "AMENDED", "DELAYED", "ON_HOLD", "PARTIALLY_SHIPPED", "DEPARTED",
                "COMPLETED", "CANCEL_REQUESTED", "CANCELLED", "REBOOK_REQUESTED")


class BookingRuleError(ValueError):
    pass


def check_transition(old: str, new: str) -> None:
    if old == new:
        return
    if new not in TRANSITIONS.get(old, set()):
        raise BookingRuleError(f"Khong the chuyen booking tu {old} sang {new}")


# ---------------------------------------------------------------------------
# Tong hop booking cua 1 lo
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BookingView:
    id: int
    status: str
    booking_no: str = ""
    etd: dt.date | None = None
    created_at: dt.datetime | None = None
    allocated_qty: float | None = None  # None = ca lo


@dataclass
class ShipmentBookingSummary:
    booking_status: str = "NOT_BOOKED"       # trang thai booking hien hanh cua lo
    booked: bool = False                     # da giu cho -> khong con WHITE
    hold: bool = False
    cancelled: bool = False                  # moi booking deu da huy, chua rebook
    current_booking_no: str = ""
    current_etd: dt.date | None = None
    booking_nos: list[str] = field(default_factory=list)
    allocation: str = "NONE"                 # NONE | FULL | PARTIAL | OVER
    allocated_qty: float | None = None
    missing_qty: float | None = None
    has_bookings: bool = False


def summarize(bookings: list[BookingView], shipment_qty: float | None) -> ShipmentBookingSummary:
    out = ShipmentBookingSummary()
    if not bookings:
        return out
    out.has_bookings = True
    active = [b for b in bookings if b.status in ACTIVE]
    ordered = sorted(active or bookings, key=lambda b: (b.created_at or dt.datetime.min, b.id))
    current = ordered[-1]
    out.booking_nos = [b.booking_no for b in active if b.booking_no]
    if active:
        out.booking_status = current.status
        out.booked = any(b.status in HELD_SPACE for b in active)
        out.hold = any(b.status == "ON_HOLD" for b in active)
        held = [b for b in active if b.status in HELD_SPACE] or active
        latest = sorted(held, key=lambda b: (b.created_at or dt.datetime.min, b.id))[-1]
        out.current_booking_no = latest.booking_no
        out.current_etd = latest.etd
    else:
        out.booking_status = "CANCELLED"
        out.cancelled = True

    alloc = allocation_status(shipment_qty, [b.allocated_qty for b in active])
    out.allocation, out.allocated_qty, out.missing_qty = alloc
    return out


def allocation_status(shipment_qty: float | None, allocations: list[float | None]) -> tuple[str, float | None,
                                                                                            float | None]:
    """Split booking: 600 + 400 = 1000 -> FULL; 600 + 200 = 800 -> PARTIAL (thieu 200)."""
    if not allocations:
        return "NONE", None, None
    if any(a is None for a in allocations):
        return "FULL", shipment_qty, 0.0
    total = float(sum(a for a in allocations if a is not None))
    if not shipment_qty:
        return "FULL", total, None
    qty = float(shipment_qty)
    if total + 1e-6 < qty:
        return "PARTIAL", total, round(qty - total, 3)
    if total > qty * 1.001 + 1e-6:
        return "OVER", total, round(qty - total, 3)
    return "FULL", total, 0.0


def ship_status(shipped_qty: float | None, final_qty: float | None) -> str:
    """BR-012: shipped < final -> PARTIALLY_SHIPPED; >= final -> FULLY_SHIPPED."""
    if not shipped_qty:
        return "NOT_SHIPPED"
    if final_qty and float(shipped_qty) + 1e-6 < float(final_qty):
        return "PARTIALLY_SHIPPED"
    return "FULLY_SHIPPED"


def status_values(summary: ShipmentBookingSummary, shipped_qty: float | None) -> dict[str, Any]:
    """Gia tri bo sung cho Status Engine (mau dong) tu booking + lan xuat thuc te."""
    v: dict[str, Any] = {}
    if summary.has_bookings:
        v["BookingFlag"] = summary.booked
        if summary.hold:
            v["HoldFlag"] = True
            v.setdefault("HoldReason", "Booking dang hold")
        if summary.cancelled:
            v["CancelFlag"] = True
        if summary.current_booking_no:
            v["BookingNo"] = summary.current_booking_no
    if shipped_qty:
        v["ShippedQty"] = float(shipped_qty)
    return v
