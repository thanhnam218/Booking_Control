"""Nghiem thu Status Engine - SRS muc 27-35, 52, 68, 73."""

from __future__ import annotations

from booking_control.core.status import effective_status, evaluate, is_upgrade, status_counts


def base(**kw):
    v = {"HoldFlag": False, "CancelFlag": False, "ShippedFlag": False, "ShippedQty": None,
         "FinalExportQty": 1000, "BookingFlag": False, "CDF": "", "BL": ""}
    v.update(kw)
    return v


def test_white_when_not_booked():
    assert evaluate(base()).status == "WHITE"


def test_yellow_missing_bl():
    r = evaluate(base(BookingFlag=True, CDF="CDF-001", BL=""))
    assert r.status == "YELLOW"
    assert "BL" in r.reason


def test_yellow_missing_cdf():
    assert evaluate(base(BookingFlag=True, CDF="", BL="MAEU-1")).status == "YELLOW"


def test_green_when_cdf_and_bl():
    r = evaluate(base(BookingFlag=True, CDF="CDF-001", BL="MAEU-1"))
    assert r.status == "GREEN"
    assert "80%" in r.reason


def test_red_has_highest_priority():
    """SRS muc 31: Booking=YES, CDF=YES, BL=YES, HOLD=YES -> RED chu khong phai GREEN."""
    r = evaluate(base(BookingFlag=True, CDF="CDF-001", BL="MAEU-1", HoldFlag=True,
                      HoldReason="CUSTOMER"))
    assert r.status == "RED"
    assert "CUSTOMER" in r.reason


def test_blank_equivalents_are_not_filled():
    """DMS muc 20-21: 'TBA', '-', 'N/A' khong duoc tinh la da dien."""
    for fake in ["-", "N/A", "TBA", "pending", "  "]:
        assert evaluate(base(BookingFlag=True, CDF="CDF-1", BL=fake)).status == "YELLOW"


def test_booking_inferred_when_column_missing():
    """File khong co cot Booking -> suy ra tu CDF/BL va ghi ro da suy luan."""
    v = base(CDF="CDF-9", BL="BL-9")
    v.pop("BookingFlag")
    r = evaluate(v)
    assert r.status == "GREEN"
    assert r.booking_inferred is True


def test_strict_mode_does_not_infer():
    v = base(CDF="CDF-9", BL="BL-9")
    v.pop("BookingFlag")
    assert evaluate(v, strict_booking_column=True).status == "WHITE"


def test_cancelled():
    assert evaluate(base(BookingFlag=True, CancelFlag=True)).status == "CANCELLED"


def test_partial_and_completed():
    """Xuat toi nay 1 nua, mai dong tiep."""
    p = evaluate(base(BookingFlag=True, CDF="C", BL="B", ShippedQty=500, FinalExportQty=1000))
    assert p.status == "PARTIAL"
    assert "50%" in p.reason
    c = evaluate(base(BookingFlag=True, CDF="C", BL="B", ShippedQty=1000, FinalExportQty=1000))
    assert c.status == "COMPLETED"


def test_hold_beats_cancel_and_shipped():
    v = base(BookingFlag=True, HoldFlag=True, CancelFlag=True, ShippedFlag=True)
    assert evaluate(v).status == "RED"


def test_explain_trace_is_readable():
    """SRS muc 52: User bam 'Why Yellow?' phai co cau tra loi."""
    r = evaluate(base(BookingFlag=True, CDF="CDF-1", BL=""))
    text = r.explain()
    assert "CDF" in text and "BL" in text and "YELLOW" in text
    checks = [t["check"] for t in r.trace]
    assert checks[0] == "HOLD"  # HOLD luon duoc kiem tra truoc tien


def test_user_override_wins():
    """SRS muc 34-35."""
    assert effective_status("GREEN", "YELLOW") == ("YELLOW", "USER")
    assert effective_status("GREEN", None) == ("GREEN", "AUTO")


def test_is_upgrade():
    assert is_upgrade("YELLOW", "GREEN")
    assert not is_upgrade("GREEN", "RED")


def test_status_counts_has_all_keys():
    c = status_counts(["GREEN", "GREEN", "RED"])
    assert c["GREEN"] == 2 and c["RED"] == 1 and c["WHITE"] == 0
