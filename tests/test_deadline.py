"""Deadline Engine - SRS muc 44-48, 73."""

from __future__ import annotations

import datetime as dt

import pytest

from booking_control.core.deadline import (
    check_t3_fields,
    compute_milestones,
    days_until,
    due_milestones,
    evaluate_shipment,
    t3_summary,
)
from booking_control.settings import DeadlinesCfg

CFG = DeadlinesCfg()
TODAY = dt.date(2026, 9, 18)


def ev(exfty_offset: int, status: str = "WHITE", **kw):
    return evaluate_shipment(
        shipment_code="SHP-TEST-0001", jo="JO-1", buyer="ABC",
        exfty=TODAY + dt.timedelta(days=exfty_offset), status=status, cfg=CFG, today=TODAY, **kw
    )


def cats(events) -> set[str]:
    return {e.category for e in events}


def test_milestones_math():
    ms = compute_milestones(dt.date(2026, 10, 8), CFG)
    assert ms.booking_start == dt.date(2026, 9, 18)   # T-20
    assert ms.booking_end == dt.date(2026, 9, 24)     # T-14
    assert ms.t7 == dt.date(2026, 10, 1)
    assert ms.t3 == dt.date(2026, 10, 5)
    assert ms.t1 == dt.date(2026, 10, 7)


@pytest.mark.parametrize("offset", [20, 18, 15, 14])
def test_booking_window_warns(offset):
    assert "BOOKING_DUE" in cats(ev(offset))


def test_outside_window_no_booking_reminder():
    assert "BOOKING_DUE" not in cats(ev(25))


def test_late_booking_is_critical():
    events = ev(10)
    assert "BOOKING_OVERDUE" in cats(events)
    assert [e for e in events if e.category == "BOOKING_OVERDUE"][0].severity == "CRITICAL"


def test_booked_shipment_gets_no_booking_reminder():
    assert "BOOKING_DUE" not in cats(ev(18, status="YELLOW"))


def test_t7_t3_t1_fire_in_order():
    assert "T7" in cats(ev(7, status="YELLOW"))
    assert "T7" not in cats(ev(8, status="YELLOW"))
    assert "T1" in cats(ev(1, status="YELLOW"))
    assert "T1" not in cats(ev(2, status="YELLOW"))


def test_hold_reminder_respects_interval():
    """SRS muc 45: mac dinh nhac lai moi 3 ngay."""
    assert "HOLD_REMINDER" in cats(ev(10, status="RED"))
    recent = TODAY - dt.timedelta(days=1)
    assert "HOLD_REMINDER" not in cats(ev(10, status="RED", last_hold_notice=recent))
    old = TODAY - dt.timedelta(days=3)
    assert "HOLD_REMINDER" in cats(ev(10, status="RED", last_hold_notice=old))


def test_overdue_alert_within_grace():
    assert "OVERDUE" in cats(ev(-1, status="YELLOW"))
    assert "OVERDUE" not in cats(ev(-1, status="COMPLETED"))
    assert "OVERDUE" not in cats(ev(-30, status="YELLOW"))  # qua lau -> thoi nhac


def test_invalid_date_produces_critical_event():
    events = evaluate_shipment(shipment_code="S1", jo="JO-1", buyer="ABC", exfty=None,
                               status="WHITE", cfg=CFG, today=TODAY)
    assert events[0].category == "INVALID_DATE"
    assert events[0].severity == "CRITICAL"


def test_t3_field_check_states():
    """SRS muc 47: FOUND / MISSING / INVALID chu khong chi co/khong."""
    values = {"Invoice": "INV1", "FinalExportQty": 100, "FinalCartonQty": None,
              "GrossWeight": -5, "FinalCBM": 12.0, "Price": 3.2, "DiscountPrice": 3.0}
    states = {"Invoice": "VALID", "FinalExportQty": "VALID", "FinalCartonQty": "BLANK",
              "GrossWeight": "VALID", "FinalCBM": "VALID", "Price": "VALID",
              "DiscountPrice": "VALID"}
    check = check_t3_fields(values, states)
    assert check["Inv"] == "FOUND"
    assert check["Carton"] == "MISSING"
    assert check["GW"] == "INVALID"
    summary = t3_summary(check)
    assert "Carton" in summary and "GW" in summary


def test_t3_event_only_when_something_missing():
    ok = {k: "FOUND" for k in ["Inv", "FinalQty", "Carton", "GW", "CBM", "Price", "Discount Price"]}
    assert "T3" not in cats(ev(2, status="YELLOW", t3_check=ok))
    bad = {**ok, "Carton": "MISSING"}
    assert "T3" in cats(ev(2, status="YELLOW", t3_check=bad))


def test_due_milestones_set():
    assert due_milestones(TODAY + dt.timedelta(days=7), CFG, TODAY) == {"T7"}
    assert due_milestones(TODAY + dt.timedelta(days=1), CFG, TODAY) == {"T7", "T3", "T1"}
    assert due_milestones(None, CFG, TODAY) == set()


def test_exfty_change_recalculates():
    """SRS muc 48: doi ngay xuat -> deadline phai doi theo, khong giu deadline cu."""
    before = compute_milestones(dt.date(2026, 9, 20), CFG)
    after = compute_milestones(dt.date(2026, 9, 25), CFG)
    assert after.t7 != before.t7 and after.booking_end != before.booking_end
    assert days_until(dt.date(2026, 9, 25), TODAY) == 7
