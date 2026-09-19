"""Shipment Identification - SRS muc 10-13, DMS muc 8-12."""

from __future__ import annotations

import datetime as dt

from booking_bot.core.shipment import (
    ShipmentCandidate,
    build_candidates,
    detect_merge_candidates,
    detect_split_jo,
    group_key,
    make_shipment_code,
    shipment_key,
)
from booking_bot.excel.reader import CanonicalRow


def row(no, jo, exfty, buyer="ABC", cbm=20.0, inv="", idx=2, owner="user_a"):
    r = CanonicalRow(file_code="F01-A", file_path="/x.xlsx", sheet=buyer, row_index=idx,
                     owner_user=owner)
    r.values = {"ShipmentNo": no, "JO": jo, "ExFtyDate": exfty, "Buyer": buyer,
                "FinalCBM": cbm, "Invoice": inv}
    r.invoices = [x.strip() for x in inv.split(",") if x.strip()]
    return r


def test_jo_is_not_the_primary_key():
    """SRS muc 10: cung JO, khac ExFtyDate -> 2 shipment khac nhau."""
    a = shipment_key("user_a", "125", "ABC123", dt.date(2026, 9, 20))
    b = shipment_key("user_a", "125", "ABC123", dt.date(2026, 9, 25))
    assert a != b


def test_same_row_same_key():
    a = shipment_key("user_a", "125", "ABC123", dt.date(2026, 9, 20))
    b = shipment_key("user_a", " 125 ", "abc-123", dt.date(2026, 9, 20))
    assert a == b  # chuan hoa truoc khi bam


def test_shipment_code_format():
    assert make_shipment_code(dt.date(2026, 9, 12), 1) == "SHP-20260912-0001"


def test_split_jo_two_dates_are_two_shipments(buyers):
    rows = [
        row("20003", "JO-X2003", dt.date(2026, 9, 22), buyer="XYZ", idx=2),
        row("20004", "JO-X2003", dt.date(2026, 9, 28), buyer="XYZ", idx=3),
    ]
    cands, dups = build_candidates(rows, buyers.resolve)
    assert len(cands) == 2
    assert not dups
    notes = detect_split_jo(cands)
    assert len(notes) == 1
    assert len(notes[0]["exfty_dates"]) == 2


def test_true_duplicate_is_flagged_critical(buyers):
    rows = [
        row("50001", "JO-D5001", dt.date(2026, 10, 3), buyer="DEF", inv="INV-1", idx=2),
        row("50001", "JO-D5001", dt.date(2026, 10, 3), buyer="DEF", inv="INV-1", idx=3),
    ]
    cands, dups = build_candidates(rows, buyers.resolve)
    assert len(cands) == 1          # khong tao ban ghi trung trong DB
    assert len(dups) == 1
    assert dups[0]["severity"] == "CRITICAL"


def test_same_key_different_invoice_needs_review(buyers):
    rows = [
        row("50001", "JO-D5001", dt.date(2026, 10, 3), buyer="DEF", inv="INV-1", idx=2),
        row("50001", "JO-D5001", dt.date(2026, 10, 3), buyer="DEF", inv="INV-2", idx=3),
    ]
    _, dups = build_candidates(rows, buyers.resolve)
    assert dups[0]["severity"] == "WARNING"


def test_unknown_buyer_raises_issue(buyers):
    rows = [row("90001", "JO-Z1", dt.date(2026, 10, 3), buyer="CONG TY LA HOAC")]
    cands, _ = build_candidates(rows, buyers.resolve)
    assert cands[0].buyer_rule.known is False
    assert cands[0].issues[0]["code"] == "EX008"


def test_buyer_alias_resolves(buyers):
    assert buyers.resolve("ABC CO.,LTD").code == "ABC"
    assert buyers.resolve("IKEA SUPPLY").code == "IKEA"
    assert buyers.resolve("ikea").code == "IKEA"


def test_group_key_modes(buyers):
    ikea = buyers.resolve("IKEA")     # by_exfty
    abc = buyers.resolve("ABC")       # by_no_exfty
    d = dt.date(2026, 10, 1)
    a = {"ShipmentNo": "1", "JO": "J1", "ExFtyDate": d}
    b = {"ShipmentNo": "2", "JO": "J2", "ExFtyDate": d}
    assert group_key(a, ikea) == group_key(b, ikea)     # gom chung theo ngay
    assert group_key(a, abc) != group_key(b, abc)       # tach theo NO


def test_merge_suggestion_for_small_nearby_lots(buyers):
    rows = [
        row("60001", "JO-M1", dt.date(2026, 10, 1), buyer="ABC", cbm=12.0, idx=2),
        row("60002", "JO-M2", dt.date(2026, 10, 4), buyer="ABC", cbm=15.0, idx=3),
    ]
    cands, _ = build_candidates(rows, buyers.resolve)
    sugg = detect_merge_candidates(cands)
    assert len(sugg) == 1
    assert sugg[0]["total_cbm"] == 27.0
    assert sugg[0]["gap_days"] == 3


def test_candidate_helpers(buyers):
    rows = [row("1", "JO-1", dt.date(2026, 10, 1), cbm=33.3)]
    cands, _ = build_candidates(rows, buyers.resolve)
    c: ShipmentCandidate = cands[0]
    assert c.shipment_no == "1" and c.jo == "JO-1" and c.cbm == 33.3
