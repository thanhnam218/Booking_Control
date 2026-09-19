"""WEEKLY PLANNING 2026 - moc T-7 (SRS muc 22-23, DMS muc 23)."""

from __future__ import annotations

from typing import Any

from .base import AllocationLike, OutputSpec, ShipmentLike, mode_for, num, type_for, yes_no

SPEC = OutputSpec(
    role="weekly_planning",
    display="WEEKLY PLANNING 2026",
    columns=["NO", "EXFTY", "BUYER", "JO", "PCS", "CBM", "MODE", "TYPE", "PIC", "CO", "CO FORM", "STATUS"],
    key_columns=["JO", "EXFTY"],
    milestone="T7",
    notes=(
        "Khoa = JO + EXFTY vi mot JO co the tach thanh nhieu ngay xuat (SRS muc 10). "
        "Neu file that khong co cot NO / CO FORM / STATUS thi Bot tu bo qua cac cot do."
    ),
)


def build_record(
    shipment: ShipmentLike, alloc: AllocationLike | None = None, air_type: str = "AC"
) -> dict[str, Any]:
    return {
        "NO": shipment.shipment_no,
        "EXFTY": shipment.exfty_date,
        "BUYER": shipment.buyer_code or shipment.buyer_raw,
        "JO": shipment.jo,
        "PCS": num(shipment.final_export_qty),
        "CBM": num(shipment.final_cbm),
        "MODE": mode_for(shipment, alloc),
        "TYPE": type_for(shipment, alloc, air_type),
        "PIC": shipment.pic,
        "CO": yes_no(bool(shipment.co_required)),
        "CO FORM": shipment.co_form or "",
        "STATUS": shipment.effective_status,
        "_shipment_code": shipment.shipment_code,
    }
