"""Shipment Identification & Grouping (SRS muc 10-13, DMS muc 8-12).

Diem cot loi cua kien truc: JO KHONG phai khoa duy nhat.
  ShipmentKey = sha256(owner | JO | ExFtyDate)
  -> cung JO nhung khac ExFtyDate = 2 shipment khac nhau (JO tach lam 2 ngay xuat).
  -> cung JO + ExFtyDate + cung INV = nghi ngo duplicate -> Exception Center.

Tai sao KHONG dua NO vao khoa (quyet dinh sau khi xem file that):
  NO trong Follow Up la CONG THUC tinh tu INVOICE:
      =IF(LEFT(M2,4)="TEGV",RIGHT(M2,5)-2108,"")
  -> truoc khi xuat invoice, NO trong. Neu NO nam trong khoa thi luc co invoice
     lo hang se bi coi la lo MOI, mat lich su. NO chi dung de GOM LO.

GroupKey dung de gom nhieu dong thanh 1 lo khi tinh container / dat booking chung.
Thu tu uu tien khi gom:  NO  ->  BOOKING NO  ->  tung JO rieng.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .buyer_rules import BuyerRule
from .normalize import clean_text, norm_key, to_canonical_date

if TYPE_CHECKING:  # core/ khong duoc phu thuoc excel/ luc chay (tranh vong import)
    from ..excel.reader import CanonicalRow


def shipment_key(
    owner: str, shipment_no: Any, jo: Any, exfty: dt.date | None, include_no: bool = False
) -> str:
    """Khoa lo hang. Mac dinh = owner + JO + ExFtyDate (NO la cong thuc, xuat hien muon)."""
    parts = [norm_key(owner), norm_key(jo), to_canonical_date(exfty)]
    if include_no:
        parts.insert(1, norm_key(shipment_no))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:40]


def make_shipment_code(exfty: dt.date | None, seq: int) -> str:
    """SHP-20260912-0001 (SRS muc 10)."""
    stamp = exfty.strftime("%Y%m%d") if exfty else dt.date.today().strftime("%Y%m%d")
    return f"SHP-{stamp}-{seq:04d}"


def group_key(row_like: dict[str, Any], rule: BuyerRule) -> str:
    """Quy tac gom lo theo Buyer Rule (DMS muc 13).

    by_no_exfty : NO + ExFtyDate  (mac dinh)
    by_no       : NO
    by_booking  : BOOKING NO
    by_exfty    : Buyer + ExFtyDate  -> gom moi JO cung ngay xuat lai
    manual      : moi dong 1 lo, User tu gom

    Khi che do can NO ma NO con trong (chua co invoice):
        -> gom theo BOOKING NO neu co (1 booking = 1 bo container)
        -> khong co ca hai: moi JO 1 nhom rieng, KHONG gom bua
    """
    buyer = norm_key(rule.code)
    no = norm_key(row_like.get("ShipmentNo"))
    booking = norm_key(row_like.get("BookingNo"))
    exfty = to_canonical_date(row_like.get("ExFtyDate"))
    jo = norm_key(row_like.get("JO"))
    mode = rule.shipment_group

    if mode in ("by_no", "by_no_exfty") and not no:
        mode = "by_booking" if booking else "manual"

    if mode == "by_booking":
        raw = f"{buyer}|BKG:{booking}" if booking else f"{buyer}|MANUAL:{jo}:{exfty}"
    elif mode == "by_no":
        raw = f"{buyer}|NO:{no}"
    elif mode == "by_exfty":
        raw = f"{buyer}|EXFTY:{exfty}"
    elif mode == "manual":
        raw = f"{buyer}|MANUAL:{jo}:{exfty}"
    else:
        raw = f"{buyer}|NO:{no}|EXFTY:{exfty}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class ShipmentCandidate:
    """Ban ghi trung gian giua CanonicalRow va model Shipment trong DB."""

    key: str
    group: str
    row: CanonicalRow
    buyer_rule: BuyerRule
    invoices: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)

    # tien ich doc nhanh
    @property
    def shipment_no(self) -> str:
        return clean_text(self.row.values.get("ShipmentNo"))

    @property
    def jo(self) -> str:
        return clean_text(self.row.values.get("JO"))

    @property
    def exfty(self) -> dt.date | None:
        v = self.row.values.get("ExFtyDate")
        return v if isinstance(v, dt.date) else None

    @property
    def cbm(self) -> float:
        v = self.row.values.get("FinalCBM")
        return float(v) if isinstance(v, (int, float)) else 0.0


def build_candidates(
    rows: list[CanonicalRow],
    resolve_buyer,
    owner_default: str = "",
    include_no: bool = False,
) -> tuple[list[ShipmentCandidate], list[dict[str, Any]]]:
    """Tao danh sach shipment tu cac dong Excel + phat hien duplicate (SRS muc 13).

    resolve_buyer: callable(buyer_raw) -> BuyerRule
    Tra ve (candidates, duplicate_exceptions)
    """
    candidates: list[ShipmentCandidate] = []
    seen: dict[str, ShipmentCandidate] = {}
    duplicates: list[dict[str, Any]] = []

    for row in rows:
        owner = row.owner_user or owner_default
        buyer_raw = clean_text(row.values.get("Buyer"))
        rule = resolve_buyer(buyer_raw)

        key = shipment_key(owner, row.values.get("ShipmentNo"), row.values.get("JO"),
                           row.values.get("ExFtyDate"), include_no=include_no)
        gkey = group_key(row.values, rule)

        cand = ShipmentCandidate(key=key, group=gkey, row=row, buyer_rule=rule,
                                 invoices=list(row.invoices))

        if not rule.known and buyer_raw:
            cand.issues.append(
                {
                    "code": "EX008",
                    "severity": "WARNING",
                    "reason": f"Chua co Buyer Rule cho '{buyer_raw}'",
                    "suggested_action": "Them buyer vao config/buyer_rules.yaml hoac Dashboard > Buyers",
                }
            )

        prev = seen.get(key)
        if prev is not None:
            same_inv = set(map(norm_key, prev.invoices)) == set(map(norm_key, cand.invoices))
            cand.duplicate_of = f"{prev.row.sheet}!{prev.row.row_index}"
            duplicates.append(
                {
                    "code": "EX003",
                    "severity": "CRITICAL" if same_inv else "WARNING",
                    "source_file": row.file_path,
                    "source_sheet": row.sheet,
                    "source_row": row.row_index,
                    "value": f"NO={cand.shipment_no} JO={cand.jo} ExFty={to_canonical_date(cand.exfty)}",
                    "reason": (
                        "Trung hoan toan JO + ExFtyDate + Invoice -> kha nang duplicate that su"
                        if same_inv
                        else "Trung JO + ExFtyDate nhung Invoice khac -> can User xac nhan"
                    ),
                    "suggested_action": f"Kiem tra dong {prev.row.row_index} va {row.row_index} cua sheet {row.sheet}",
                    "dedup_key": f"DUP:{key}",
                }
            )
            # Van giu lai de User thay trong Exception Center, nhung khong ghi de ban ghi truoc
            continue

        seen[key] = cand
        candidates.append(cand)

    return candidates, duplicates


def summarize_groups(candidates: list[ShipmentCandidate]) -> dict[str, list[ShipmentCandidate]]:
    groups: dict[str, list[ShipmentCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.group, []).append(c)
    return groups


def detect_split_jo(candidates: list[ShipmentCandidate]) -> list[dict[str, Any]]:
    """JO tach thanh nhieu ngay xuat (SRS muc 13 / yeu cau 'JO tach thanh 2').

    Day KHONG phai loi - chi ghi nhan de hien thi cho User.
    """
    by_jo: dict[str, list[ShipmentCandidate]] = {}
    for c in candidates:
        if c.jo:
            by_jo.setdefault(norm_key(c.jo), []).append(c)

    notes = []
    for jo_key, items in by_jo.items():
        dates = {to_canonical_date(i.exfty) for i in items if i.exfty}
        if len(dates) > 1:
            notes.append(
                {
                    "jo": items[0].jo,
                    "jo_key": jo_key,
                    "exfty_dates": sorted(dates),
                    "shipment_keys": [i.key for i in items],
                    "note": "JO duoc tach thanh nhieu ngay xuat - moi ngay la 1 shipment rieng",
                }
            )
    return notes


def detect_merge_candidates(
    candidates: list[ShipmentCandidate], window_days: int = 7
) -> list[dict[str, Any]]:
    """Goi y gop lo: cung Buyer, ngay xuat cach nhau <= window_days, deu chua du 1 container.

    Phuc vu tinh huong 'da book roi lai cancel, doi lo sau xuat chung 1 ngay'.
    Bot chi GOI Y, User quyet dinh.
    """
    by_buyer: dict[str, list[ShipmentCandidate]] = {}
    for c in candidates:
        if c.exfty:
            by_buyer.setdefault(c.buyer_rule.code, []).append(c)

    out = []
    for buyer, items in by_buyer.items():
        items = sorted(items, key=lambda x: x.exfty or dt.date.max)
        for i in range(len(items) - 1):
            a, b = items[i], items[i + 1]
            if a.group == b.group or not a.exfty or not b.exfty:
                continue
            gap = (b.exfty - a.exfty).days
            if 0 < gap <= window_days and (a.cbm + b.cbm) <= 63 and a.cbm and b.cbm:
                out.append(
                    {
                        "buyer": buyer,
                        "shipments": [a.key, b.key],
                        "jos": [a.jo, b.jo],
                        "exfty_dates": [to_canonical_date(a.exfty), to_canonical_date(b.exfty)],
                        "total_cbm": round(a.cbm + b.cbm, 2),
                        "gap_days": gap,
                        "note": "Hai lo nho cung buyer, cach nhau it ngay - co the gop xuat chung 1 container",
                    }
                )
    return out
