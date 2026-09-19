"""Khung chung cho Output Mapping (DMS muc 23-26).

Moi file dich khai bao:
    columns      : ten cot mong doi trong file Excel (so khop bang norm_key)
    key_columns  : khoa nghiep vu de UPSERT (khong bao gio INSERT trung)
    milestone    : moc thoi gian kich hoat (T7 / T3 / T1)
    build_record : bien 1 shipment -> 1 dong du lieu

Neu file that cua cong ty co ten cot khac, chi can sua `columns` trong
config/output_mapping.yaml (hoac sua truc tiep SPEC o day) - khong doi logic.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol


class ShipmentLike(Protocol):
    shipment_code: str
    shipment_no: str
    jo: str
    exfty_date: dt.date | None
    buyer_code: str
    buyer_raw: str
    ship_mode: str
    booking_mode: str
    final_export_qty: float | None
    final_carton_qty: float | None
    gross_weight: float | None
    final_cbm: float | None
    customs_code: str
    pic: str
    site: str
    co_required: bool
    co_form: str
    effective_status: str
    destination: str


@dataclass(slots=True)
class AllocationLike:
    container_type: str = ""
    booking_mode: str = ""
    qty_40hc: int = 0
    qty_40dc: int = 0
    qty_20st: int = 0
    total_cbm: float = 0.0
    allocation_status: str = "AUTO"


@dataclass(slots=True)
class OutputSpec:
    role: str
    display: str
    columns: list[str]
    key_columns: list[str]
    milestone: str
    sheet_hint: str | None = None
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def yes_no(flag: bool) -> str:
    return "Yes" if flag else "No"


def num(value: float | None, digits: int = 2) -> Any:
    if value is None:
        return None
    f = float(value)
    return int(f) if abs(f - int(f)) < 1e-9 else round(f, digits)


def type_for(shipment: ShipmentLike, alloc: AllocationLike | None, air_type: str = "AC") -> str:
    """Cot TYPE: AC cho AIR, con lai lay tu Container Engine (SRS muc 20-21)."""
    if (shipment.ship_mode or "").upper() in ("AIR", "EXPRESS"):
        return air_type
    if alloc and alloc.container_type:
        return alloc.container_type
    return ""


def mode_for(shipment: ShipmentLike, alloc: AllocationLike | None) -> str:
    """Cot MODE: AIR / SEA (Ship Mode), tach biet voi Booking Mode FCL-LCL."""
    return (shipment.ship_mode or "").upper() or (alloc.booking_mode if alloc else "")
