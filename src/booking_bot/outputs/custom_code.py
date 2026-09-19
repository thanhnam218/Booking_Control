"""CUSTOM CODE (HS Code) - moc T-7 (SRS muc 24, DMS muc 24)."""

from __future__ import annotations

from typing import Any

from .base import AllocationLike, OutputSpec, ShipmentLike

SPEC = OutputSpec(
    role="custom_code",
    display="Custom Code",
    columns=["NO", "JO", "BUYER", "CUSTOMS CODE", "EXFTY", "PIC"],
    key_columns=["JO"],
    milestone="T7",
    notes=(
        "Khoa = JO (SRS muc 24: JO da ton tai -> UPDATE hoac SKIP). "
        "Bot khong bao gio ghi de Customs Code ma User da nhap tay: "
        "chi dien khi o dang trong (protect_user_edits)."
    ),
)


def build_record(
    shipment: ShipmentLike, alloc: AllocationLike | None = None, air_type: str = "AC"
) -> dict[str, Any]:
    return {
        "NO": shipment.shipment_no,
        "JO": shipment.jo,
        "BUYER": shipment.buyer_code or shipment.buyer_raw,
        # De trong neu Follow Up chua co -> nguoi lam HS code se dien
        "CUSTOMS CODE": shipment.customs_code or None,
        "EXFTY": shipment.exfty_date,
        "PIC": shipment.pic,
        "_shipment_code": shipment.shipment_code,
    }
