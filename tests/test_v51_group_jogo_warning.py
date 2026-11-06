"""SRSv5.1 / v5.4 / v5.5: ba truc doc lap, quan he JO-GO, Export Shipment Group.

Cac vi du trong file nay lay thang tu SRS de co the doi chieu tung dong.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from booking_control.core import export_group as eg
from booking_control.core import jogo
from booking_control.core.deadline import (
    WARN_BOOKING_DEADLINE,
    WARN_BOOKING_OVERDUE,
    WARN_NONE,
    booking_warning,
)

TODAY = dt.date(2026, 9, 18)


# ---------------------------------------------------------------------------
# SRSv5.1 muc 16 - Deadline Warning la truc RIENG, khong phai Business Status
# ---------------------------------------------------------------------------


@dataclass
class _Cfg:
    end_days_before: int = 14


class _Deadlines:
    booking_window = _Cfg()


@pytest.mark.parametrize("days,expected", [
    (20, WARN_NONE),               # D-20 -> binh thuong
    (15, WARN_NONE),               # D-15 -> binh thuong
    (14, WARN_NONE),               # D-14 -> binh thuong (dung bang nguong)
    (13, WARN_BOOKING_DEADLINE),   # D-13 -> VIEN DO
    (7, WARN_BOOKING_DEADLINE),
    (3, WARN_BOOKING_DEADLINE),
    (1, WARN_BOOKING_DEADLINE),
    (0, WARN_BOOKING_DEADLINE),
    (-1, WARN_BOOKING_OVERDUE),
])
def test_booking_warning_follows_srs_table(days, expected):
    """Bang vi du trong SRSv5.1 muc 16: D-14 binh thuong, D-13 tro di vien do."""
    exfty = TODAY + dt.timedelta(days=days)
    assert booking_warning(booked=False, exfty=exfty, cfg=_Deadlines, today=TODAY) == expected


def test_booked_shipment_never_warns():
    """Canh bao tinh tu BUSINESS STATUS (da book chua), khong tu mau."""
    exfty = TODAY + dt.timedelta(days=3)
    assert booking_warning(booked=True, exfty=exfty, cfg=_Deadlines, today=TODAY) == WARN_NONE


def test_closed_shipment_never_warns():
    exfty = TODAY - dt.timedelta(days=5)
    assert booking_warning(booked=False, exfty=exfty, cfg=_Deadlines, today=TODAY,
                           closed=True) == WARN_NONE


def test_warning_threshold_is_configurable():
    """SRS: cac moc phai chinh duoc - khong hard-code 14."""

    class Cfg20:
        booking_window = _Cfg(end_days_before=20)

    exfty = TODAY + dt.timedelta(days=18)
    assert booking_warning(booked=False, exfty=exfty, cfg=_Deadlines, today=TODAY) == WARN_NONE
    assert booking_warning(booked=False, exfty=exfty, cfg=Cfg20, today=TODAY) == WARN_BOOKING_DEADLINE


# ---------------------------------------------------------------------------
# SRSv5.1 muc 5 - Quan he JO - GO
# ---------------------------------------------------------------------------


def test_jo_go_example_from_srs():
    """JO 26V02426KR01 + JO 26V02426KR02 <-> GO S26V02426 qua ma goc 26V02426."""
    rules = jogo.JoGoRules()
    assert rules.core_of_jo("26V02426KR01") == "26V02426"
    assert rules.core_of_jo("26V02426KR02") == "26V02426"
    assert rules.core_of_go("S26V02426") == "26V02426"


def test_second_jo_is_linked_even_when_its_row_has_no_go():
    """Dong thu hai khong ghi GO van duoc noi - nhung ghi nhan la SUY RA, khong chac chan."""
    rows = [("26V02426KR01", "S26V02426"), ("26V02426KR02", "")]
    links = jogo.links_from_rows(rows, jogo.JoGoRules())
    by_jo = {x.jo: x for x in links}
    assert by_jo["26V02426KR01"].source == jogo.SOURCE_IMPORT
    assert by_jo["26V02426KR01"].confidence == 1.0
    assert by_jo["26V02426KR02"].source == jogo.SOURCE_RULE
    assert by_jo["26V02426KR02"].confidence < 1.0
    assert jogo.group_by_go(links) == {"S26V02426": ["26V02426KR01", "26V02426KR02"]}


def test_unrelated_codes_are_not_linked():
    rows = [("26V02426KR01", "S26V02426"), ("26V07782TH01", "S26V07782")]
    links = jogo.links_from_rows(rows, jogo.JoGoRules())
    assert jogo.group_by_go(links) == {
        "S26V02426": ["26V02426KR01"], "S26V07782": ["26V07782TH01"]}


def test_short_or_unparsable_code_gives_no_core():
    """Khong doan bua: ma qua ngan / khong khop mau thi bo qua."""
    rules = jogo.JoGoRules()
    assert rules.core_of_jo("AB01") == ""
    assert rules.core_of_jo("") == ""
    assert jogo.links_from_rows([("AB01", "")], rules) == []


# ---------------------------------------------------------------------------
# SRSv5.1 muc 3-4, SRSv5.5 muc 4 - Export Shipment Group
# ---------------------------------------------------------------------------


@dataclass
class _Ship:
    jo: str = ""
    go: str = ""
    order_qty: float | None = None
    final_export_qty: float | None = None
    final_carton_qty: float | None = None
    gross_weight: float | None = None
    final_cbm: float | None = None
    booking_no: str = ""
    effective_status: str = "WHITE"
    exfty_date: dt.date | None = TODAY


def test_group_key_uses_all_four_fields():
    """Gom nhom = BUYER + EX-FACT DATE + DEST + MODE (SRSv5.1 muc 3)."""
    base = eg.group_key("GJR", dt.date(2026, 10, 15), "KR", "SEA")
    assert base
    assert base == eg.group_key("gjr", dt.date(2026, 10, 15), "kr", "sea")  # khong phan biet hoa thuong
    assert base != eg.group_key("GJR", dt.date(2026, 10, 16), "KR", "SEA")  # khac ngay
    assert base != eg.group_key("GJR", dt.date(2026, 10, 15), "JP", "SEA")  # khac cang
    assert base != eg.group_key("GJR", dt.date(2026, 10, 15), "KR", "AIR")  # khac mode
    assert base != eg.group_key("ABC", dt.date(2026, 10, 15), "KR", "SEA")  # khac buyer


def test_group_key_empty_without_buyer_or_date():
    assert eg.group_key("", dt.date(2026, 10, 15), "KR", "SEA") == ""
    assert eg.group_key("GJR", None, "KR", "SEA") == ""


def test_group_totals_match_srs_example():
    """SRSv5.1 muc 4: 25 JO, 3 GO, 149.10 CBM - va khong gioi han so JO/GO."""
    ships = []
    for i in range(25):
        ships.append(_Ship(jo=f"JO{i:03d}", go=f"S{i % 3}", final_export_qty=100,
                           final_carton_qty=4, gross_weight=50, final_cbm=5.964))
    totals = eg.summarize(ships)
    assert totals.shipments == 25
    assert totals.jo_count == 25
    assert totals.go_count == 3
    assert round(totals.cbm, 2) == 149.10
    assert totals.pcs == 2500
    assert totals.missing_cbm == 0


def test_totals_prefer_final_qty_over_order_qty():
    """Data Source Matrix: Final Qty lay tu Final PKL, uu tien hon Order Qty."""
    totals = eg.summarize([_Ship(order_qty=100, final_export_qty=90)])
    assert totals.pcs == 90
    totals2 = eg.summarize([_Ship(order_qty=100, final_export_qty=None)])
    assert totals2.pcs == 100


def test_missing_cbm_is_counted_not_guessed():
    """Thieu CBM -> dem rieng de bao 'tong chua du', khong coi la 0 roi tinh container."""
    totals = eg.summarize([_Ship(final_cbm=10.0), _Ship(final_cbm=None)])
    assert totals.cbm == 10.0
    assert totals.missing_cbm == 1


def test_group_collects_booking_numbers_but_does_not_merge_them():
    """SRSv5.1 muc 3: mot nhom co the ung voi NHIEU booking - chi liet ke, khong gop."""
    totals = eg.summarize([_Ship(booking_no="BK001"), _Ship(booking_no="BK002"),
                           _Ship(booking_no="BK001")])
    assert totals.booking_numbers == ["BK001", "BK002"]


def test_closed_shipments_leave_the_group():
    assert eg.is_groupable(_Ship(effective_status="WHITE")) is True
    assert eg.is_groupable(_Ship(effective_status="COMPLETED")) is False
    assert eg.is_groupable(_Ship(effective_status="CANCELLED")) is False
    assert eg.is_groupable(_Ship(exfty_date=None)) is False
