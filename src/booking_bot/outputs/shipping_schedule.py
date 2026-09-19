"""Lich xuat hang - IP (DV) - moc T-1, tach 2 co so TDV/TGV (SRS muc 26, DMS muc 26).

    TDV = Co so 2
    TGV = Co so 1

Shipment thuoc co so nao duoc quyet dinh boi (theo thu tu uu tien):
    1. Buyer rule co truong `site`
    2. site_rules trong config.yaml (theo PIC / Buyer / Destination...)
    3. default_site
"""

from __future__ import annotations

from typing import Any

from ..core.normalize import clean_text, norm_key
from .base import AllocationLike, OutputSpec, ShipmentLike, num, type_for

SITE_LABELS = {"TDV": "Co so 2", "TGV": "Co so 1"}

SPEC = OutputSpec(
    role="shipping_schedule",
    display="Lich xuat hang - IP (DV)",
    columns=["NO", "EXFTY", "BUYER", "JO", "CBM", "TYPE", "PIC"],
    key_columns=["JO", "EXFTY"],
    milestone="T1",
    notes="File co 2 sheet TDV / TGV. Bot ghi vao dung sheet theo site cua shipment.",
)


def resolve_site(
    shipment: ShipmentLike,
    buyer_site: str = "",
    site_rules: Any = None,
) -> str:
    """Tra ve 'TDV' hoac 'TGV'."""
    if buyer_site:
        return buyer_site.upper()
    if site_rules is None:
        return "TGV"

    field_values = {
        "PIC": clean_text(getattr(shipment, "pic", "")),
        "Buyer": clean_text(getattr(shipment, "buyer_code", "") or getattr(shipment, "buyer_raw", "")),
        "Destination": clean_text(getattr(shipment, "destination", "")),
        "ShipMode": clean_text(getattr(shipment, "ship_mode", "")),
        "Owner": clean_text(getattr(shipment, "owner_user", "")),
    }

    for rule in getattr(site_rules, "rules", []) or []:
        cond = rule.when or {}
        fname = cond.get("field", "")
        wanted = [norm_key(x) for x in (cond.get("in") or [])]
        actual = norm_key(field_values.get(fname, ""))
        owner = norm_key(getattr(shipment, "owner_user", ""))
        if actual and actual in wanted:
            return rule.site.upper()
        if fname == "PIC" and owner and owner in wanted:
            return rule.site.upper()

    return (getattr(site_rules, "default_site", "TGV") or "TGV").upper()


def build_record(
    shipment: ShipmentLike, alloc: AllocationLike | None = None, air_type: str = "AC"
) -> dict[str, Any]:
    return {
        "NO": shipment.shipment_no,
        "EXFTY": shipment.exfty_date,
        "BUYER": shipment.buyer_code or shipment.buyer_raw,
        "JO": shipment.jo,
        "CBM": num(shipment.final_cbm),
        "TYPE": type_for(shipment, alloc, air_type),
        "PIC": shipment.pic,
        "_shipment_code": shipment.shipment_code,
        "_site": shipment.site or "TGV",
    }
