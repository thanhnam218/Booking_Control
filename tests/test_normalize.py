"""Chuan hoa du lieu - DMS muc 6-7, 15; SRS muc 56."""

from __future__ import annotations

import datetime as dt

import pytest

from booking_control.core.normalize import (
    clean_text,
    has_value,
    is_blank,
    norm_key,
    parse_date,
    parse_decimal,
    parse_flag,
    parse_ship_mode,
    split_invoices,
    strip_accents,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("20/09/2026", dt.date(2026, 9, 20)),
        ("20-09-2026", dt.date(2026, 9, 20)),
        ("2026-09-20", dt.date(2026, 9, 20)),
        ("20.9.2026", dt.date(2026, 9, 20)),
        (dt.datetime(2026, 9, 20, 8, 30), dt.date(2026, 9, 20)),
        (dt.date(2026, 9, 20), dt.date(2026, 9, 20)),
        (46285, dt.date(2026, 9, 20)),  # so serial cua Excel (he 1900)
    ],
)
def test_parse_date_formats(raw, expected):
    r = parse_date(raw)
    assert r.value == expected, r.reason


def test_parse_date_invalid_is_not_guessed():
    """SRS muc 56: Automation khong tu doan du lieu sai."""
    r = parse_date("31/02/2026")
    assert r.value is None
    assert r.state in ("INVALID", "NEED_REVIEW")
    assert parse_date("").state == "BLANK"
    assert parse_date("khong ro").state == "NEED_REVIEW"


def test_parse_date_day_first_vs_month_first():
    assert parse_date("05/09/2026", day_first=True).value == dt.date(2026, 9, 5)
    assert parse_date("05/09/2026", day_first=False).value == dt.date(2026, 5, 9)


@pytest.mark.parametrize(
    "raw, expected",
    [("1234.56", 1234.56), ("1,234.56", 1234.56), ("1.234,56", 1234.56),
     ("25 CBM", 25.0), (" 63 ", 63.0), (48.5, 48.5), ("0", 0.0)],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw).value == pytest.approx(expected)


def test_parse_decimal_uncertain_flags_review():
    r = parse_decimal("25?")
    assert r.value == 25.0
    assert r.state == "NEED_REVIEW"


def test_blank_equivalents():
    for b in ["", "-", "N/A", "TBA", "  ", "pending"]:
        assert is_blank(b), b
    assert has_value("CDF-0012")
    assert has_value(0)


def test_flags():
    assert parse_flag("Yes").value is True
    assert parse_flag("KHONG").value is False
    assert parse_flag("").state == "BLANK"
    # O Booking ghi so booking -> coi nhu da book
    assert parse_flag("BKG-77120").value is True


def test_ship_mode():
    assert parse_ship_mode("By Air").value == "AIR"
    assert parse_ship_mode("sea").value == "SEA"
    assert parse_ship_mode("DHL").value == "EXPRESS"
    assert parse_ship_mode("xyz").state == "NEED_REVIEW"


def test_split_invoices():
    assert split_invoices("INV001, INV002 ; INV003") == ["INV001", "INV002", "INV003"]
    assert split_invoices("") == []
    assert split_invoices("INV001/INV001") == ["INV001"]


def test_vietnamese_accents_are_comparable():
    assert strip_accents("Ngày xuất") == "Ngay xuat"
    assert norm_key("Ngày xuất") == norm_key("NGAY XUAT")
    assert norm_key("Ex Fty Date") == norm_key("ExFtyDate") == "EXFTYDATE"


def test_clean_text():
    assert clean_text("  A   B  ") == "A B"
    assert clean_text(10.0) == "10"
    assert clean_text(None) == ""
