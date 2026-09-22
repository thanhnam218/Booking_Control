"""Header Mapping + Sheet Detection - SRS muc 6-8, 73; DMS muc 3-4, 34-36."""

from __future__ import annotations

from openpyxl import Workbook, load_workbook

from booking_control.excel.detector import detect_sheet, detect_workbook
from booking_control.excel.mapping import FieldDictionary, HeaderMapper, col_letter


def test_col_letter():
    assert col_letter(1) == "A"
    assert col_letter(26) == "Z"
    assert col_letter(27) == "AA"


def test_alias_variants_map_to_same_field(dictionary: FieldDictionary):
    for header in ["ExFtyDate", "EX FTY DATE", "Ex Fty Date", "ExFty Date",
                   "Export Factory Date", "Ngay xuat"]:
        field, conf, method = dictionary.match_header(header)
        assert field == "ExFtyDate", header
        assert conf >= 0.85


def test_buyer_aliases(dictionary: FieldDictionary):
    assert dictionary.match_header("Customer Name")[0] == "Buyer"
    assert dictionary.match_header("BUYER")[0] == "Buyer"


def test_header_renamed_later_still_matches(dictionary: FieldDictionary):
    """DMS muc 36: 'Final CBM' doi thanh 'Final CBM (M3)' -> van map duoc."""
    field, conf, _ = dictionary.match_header("Final CBM (M3)")
    assert field == "FinalCBM"
    assert conf >= 0.85


def test_unknown_header_is_not_force_mapped(dictionary: FieldDictionary):
    field, _, method = dictionary.match_header("Ghi chu noi bo cua phong ke toan")
    assert field is None or method in ("fuzzy_review", "no_match")


def test_user_override_beats_dictionary(dictionary: FieldDictionary):
    mapper = HeaderMapper(dictionary, {"So khoi hang": "FinalCBM"})
    m = mapper.map_row(["No", "So khoi hang"], 1)
    assert m.index_of("FinalCBM") == 2
    assert [x.method for x in m.matches if x.canonical_field == "FinalCBM"] == ["user"]


def test_duplicate_columns_keep_best(dictionary: FieldDictionary):
    mapper = HeaderMapper(dictionary)
    m = mapper.map_row(["CBM", "Final CBM"], 1)
    assert len([x for x in m.matches if x.canonical_field == "FinalCBM"]) == 1


def _wb_with(headers, rows=1):
    wb = Workbook()
    ws = wb.active
    ws.title = "TEST"
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    for r in range(2, 2 + rows):
        for i in range(1, len(headers) + 1):
            ws.cell(row=r, column=i, value="x")
    return wb


def test_detect_follow_up_sheet(dictionary):
    wb = _wb_with(["No", "ExFtyDate", "JO", "Final CBM", "EELInvoice", "FinalExportQty"])
    det = detect_sheet(wb["TEST"], dictionary, HeaderMapper(dictionary))
    assert det.decision == "SCAN"
    assert det.is_follow_up
    assert det.confidence >= 0.8


def test_ignore_non_follow_up_sheet(dictionary):
    wb = _wb_with(["Thang", "Doanh thu", "Ghi chu"])
    det = detect_sheet(wb["TEST"], dictionary, HeaderMapper(dictionary))
    assert det.decision == "IGNORE"


def test_ask_user_for_ambiguous_sheet(dictionary):
    """SRS muc 7: khong chac chan thi hoi User, khong tu quyet."""
    wb = _wb_with(["No", "ExFtyDate", "Ghi chu"])
    det = detect_sheet(wb["TEST"], dictionary, HeaderMapper(dictionary))
    assert det.decision in ("ASK_USER", "IGNORE")
    assert not det.is_follow_up


def test_header_not_on_first_row(dictionary):
    wb = Workbook()
    ws = wb.active
    ws.title = "TEST"
    ws["A1"] = "BAO CAO FOLLOW UP THANG 9"
    ws["A2"] = None
    for i, h in enumerate(["No", "ExFtyDate", "JO", "Final CBM", "EELInvoice",
                           "FinalExportQty"], start=1):
        ws.cell(row=3, column=i, value=h)
    ws.cell(row=4, column=1, value="1")
    det = detect_sheet(ws, dictionary, HeaderMapper(dictionary))
    assert det.header_row == 3
    assert det.decision == "SCAN"


def test_sample_workbooks_detected(sample_dir, dictionary):
    """Bo demo: cac sheet Buyer phai duoc nhan, Summary/Report/Data phai bi bo qua."""
    mapper = HeaderMapper(dictionary)
    wb = load_workbook(sample_dir / "FollowUp_UserA.xlsx", data_only=True)
    try:
        dets = {d.sheet_name: d for d in detect_workbook(wb, dictionary, mapper)}
    finally:
        wb.close()
    assert dets["ABC"].decision == "SCAN"
    assert dets["XYZ"].decision == "SCAN"       # header viet kieu khac van nhan duoc
    assert dets["Summary"].decision == "IGNORE"
    assert dets["Data"].decision == "IGNORE"
