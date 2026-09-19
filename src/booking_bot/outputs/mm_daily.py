"""MM-DAILY PLANNING (GV) - output chung theo ngay (SRS muc 25, DMS muc 25).

SRS ghi ro: mapping chinh xac chi chot duoc sau khi xem file that.
Vi vay module nay dinh nghia mot bo cot HOP LY va de sua: doi ten trong
SPEC.columns cho khop file that la chay duoc ngay, khong phai sua logic.
"""

from __future__ import annotations

from typing import Any

from .base import AllocationLike, OutputSpec, ShipmentLike, mode_for, num, type_for

SPEC = OutputSpec(
    role="mm_daily",
    display="MM-DAILY PLANNING (GV)",
    columns=["DATE", "NO", "BUYER", "JO", "PCS", "CTN", "GW", "CBM", "MODE", "TYPE", "PIC", "REMARK"],
    key_columns=["JO", "DATE"],
    milestone="T7",
    notes="Can doi chieu voi file that de chot ten cot / dong bat dau (SRS muc 64).",
)


def build_record(
    shipment: ShipmentLike, alloc: AllocationLike | None = None, air_type: str = "AC"
) -> dict[str, Any]:
    return {
        "DATE": shipment.exfty_date,
        "NO": shipment.shipment_no,
        "BUYER": shipment.buyer_code or shipment.buyer_raw,
        "JO": shipment.jo,
        "PCS": num(shipment.final_export_qty),
        "CTN": num(shipment.final_carton_qty),
        "GW": num(shipment.gross_weight),
        "CBM": num(shipment.final_cbm),
        "MODE": mode_for(shipment, alloc),
        "TYPE": type_for(shipment, alloc, air_type),
        "PIC": shipment.pic,
        "REMARK": shipment.effective_status,
        "_shipment_code": shipment.shipment_code,
    }
