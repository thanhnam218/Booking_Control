"""Export Shipment Group (SRSv5.1 muc 2-4, SRSv5.4 muc 6-7, SRSv5.5 muc 4).

Ba doi tuong KHAC NHAU, khong duoc coi la mot:

    SHIPMENT  ->  EXPORT SHIPMENT GROUP  ->  BOOKING

Nhieu lo nho cung mot ngay xuat co the gom lai de xuat chung. Dieu kien gom nhom:

    BUYER + EX-FACT DATE + DEST + MODE

Nhom KHONG dong nghia voi mot Booking: forwarder van co the tach mot nhom thanh nhieu
booking. Vi vay nhom chi la lop TONG HOP va DE XUAT - khong tu sinh booking.

Nhom tu cong ho nhan vien: tong PCS, CTNS, G.W, CBM, so JO, so GO, so container du kien.
Khong gioi han so JO/GO trong mot nhom (vi du 25 JO trong SRS chi la mot truong hop that).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any

from .normalize import clean_text, norm_key

# Lo da dong lai thi khong gom nhom nua
CLOSED_STATUSES = ("COMPLETED", "CANCELLED")


def group_key(buyer: Any, exfty: dt.date | None, dest: Any, mode: Any) -> str:
    """Khoa nhom: BUYER + EX-FACT DATE + DEST + MODE (SRSv5.1 muc 3)."""
    if exfty is None:
        return ""
    parts = [norm_key(buyer), exfty.isoformat(), norm_key(dest), norm_key(mode)]
    if not parts[0]:
        return ""
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def group_label(buyer: Any, exfty: dt.date | None, dest: Any, mode: Any) -> str:
    bits = [clean_text(buyer) or "(không rõ buyer)"]
    if exfty:
        bits.append(exfty.strftime("%d/%m/%Y"))
    if clean_text(dest):
        bits.append(clean_text(dest))
    if clean_text(mode):
        bits.append(clean_text(mode))
    return " · ".join(bits)


@dataclass(slots=True)
class GroupTotals:
    """Tong cua mot nhom - nhan vien khong phai cong tay (SRSv5.5 muc 4)."""

    shipments: int = 0
    jo_count: int = 0
    go_count: int = 0
    pcs: float = 0.0
    ctns: float = 0.0
    gross_weight: float = 0.0
    cbm: float = 0.0
    jos: list[str] = field(default_factory=list)
    gos: list[str] = field(default_factory=list)
    missing_cbm: int = 0          # lo chua co CBM -> tong chua day du
    booking_numbers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "shipments": self.shipments, "jo_count": self.jo_count, "go_count": self.go_count,
            "pcs": round(self.pcs, 3), "ctns": round(self.ctns, 3),
            "gross_weight": round(self.gross_weight, 3), "cbm": round(self.cbm, 3),
            "jos": self.jos, "gos": self.gos, "missing_cbm": self.missing_cbm,
            "booking_numbers": self.booking_numbers,
        }


def summarize(shipments: list[Any]) -> GroupTotals:
    """Cong tong cho mot nhom. `shipments` la cac object co cac thuoc tinh cua Shipment."""
    t = GroupTotals()
    jos: set[str] = set()
    gos: set[str] = set()
    bookings: set[str] = set()
    for sh in shipments:
        t.shipments += 1
        # Uu tien so lieu cuoi (Final PKL) - dung nguon nhu Data Source Matrix cua SRS
        qty = sh.final_export_qty if sh.final_export_qty is not None else sh.order_qty
        t.pcs += float(qty or 0)
        t.ctns += float(sh.final_carton_qty or 0)
        t.gross_weight += float(sh.gross_weight or 0)
        if sh.final_cbm is None:
            t.missing_cbm += 1
        else:
            t.cbm += float(sh.final_cbm)
        if clean_text(sh.jo):
            jos.add(clean_text(sh.jo).upper())
        if clean_text(sh.go):
            gos.add(clean_text(sh.go).upper())
        if clean_text(getattr(sh, "booking_no", "")):
            bookings.add(clean_text(sh.booking_no).upper())
    t.jos = sorted(jos)
    t.gos = sorted(gos)
    t.jo_count = len(jos)
    t.go_count = len(gos)
    t.booking_numbers = sorted(bookings)
    return t


def is_groupable(sh: Any) -> bool:
    return (sh.effective_status or "") not in CLOSED_STATUSES and sh.exfty_date is not None
