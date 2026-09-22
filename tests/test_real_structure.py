"""Kiem thu theo CAU TRUC FILE FOLLOW UP THAT (47 cot, cong thuc, o merge).

Du lieu dung o day la GIA LAP (scripts/make_sample_data.build_follow_up_real) nhung
giu dung dac diem cua file that: header xuong dong, cot NO la cong thuc tu INVOICE,
cot 95% FOB / CDF AMOUNT / TSN AMOUNT la cong thuc, CONT TYPE merge theo nhom NO.
"""

from __future__ import annotations

import datetime as dt
import zipfile

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from booking_control.core.container import parse_container_type
from booking_control.core.normalize import derive_invoice_suffix, find_keyword
from booking_control.core.status import evaluate
from booking_control.db import session_scope
from booking_control.db.models import ContainerAllocation, ExceptionRecord, Shipment, UserOverride
from booking_control.excel.mapping import HeaderMapper
from booking_control.excel.reader import read_workbook
from booking_control.excel.writer import ColorOp, ExcelTargetWriter, FollowUpColorWriter
from booking_control.excel.xlsx_patch import (
    XlsxPackage,
    inspect_package,
    normalize_formula,
    shift_formula,
)

TODAY = dt.date(2026, 9, 18)
NO_FORMULA = 'IF(LEFT(M2,4)="TEGV",RIGHT(M2,5)-2108,"")'

# Dung 47 header cua file that (chi ten cot, khong co du lieu)
REAL_HEADERS = [
    "NO", "EX-FACT\nDATE", "BUYER", "CONT \nTYPE", "MODE", "DEST", "JO", "GO", "PO", "STYLE",
    "Description", "ORDER \nQTY", "INVOICE", "FinaL \nQty", "CTNS", "G.W", "CBM", "ETD", "ETA",
    "Color code", "Cust Color", "BOOKING NO", "100% \nFOB", "95%\nFOB", "CDF\nAMOUNT",
    "TSN AMOUNT", "CURRENCY", "HS CODE", "CDF#", "BL#", "VESSEL", "E-SHIPMENT", " TERM",
    "VAT INV", "VAT DATE", "VAT AMOUNT", "SQ", "Booking \ndate", "Cargos closing time",
    "PORT/WH", "CO NO", "CO FORM", "T/C CO", "CO granted date", "CONT ", "SEAL", "REMARK",
]


@pytest.fixture
def real_file(sample_dir):
    return sample_dir / "FollowUp_UserC_CauTrucThat.xlsx"


@pytest.fixture
def scanned(engine):
    engine.run_scan(today=TODAY, deliver_notifications=False)
    return engine


def ship(jo: str) -> Shipment:
    with session_scope() as s:
        return s.execute(select(Shipment).where(Shipment.jo == jo)).scalar_one()


def alloc_of(jo: str) -> ContainerAllocation:
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == jo)).scalar_one()
        return s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one()


# ---------------------------------------------------------------------------
# 1. Mapping 47 cot
# ---------------------------------------------------------------------------


def test_all_real_headers_map_with_full_confidence(dictionary):
    m = HeaderMapper(dictionary).map_row(REAL_HEADERS, 1)
    got = {x.source_header.replace("\n", " ").strip(): x.canonical_field for x in m.matches}
    assert len(m.matches) == 47
    assert m.unmatched_headers == []
    assert not m.ambiguous
    assert all(x.confidence == 1.0 for x in m.matches)
    expected = {
        "NO": "ShipmentNo", "EX-FACT DATE": "ExFtyDate", "CONT TYPE": "ContainerType",
        "INVOICE": "Invoice", "FinaL Qty": "FinalExportQty", "CTNS": "FinalCartonQty",
        "100% FOB": "Price", "95% FOB": "DiscountPrice", "HS CODE": "CustomsCode",
        "CDF#": "CDF", "BL#": "BL", "BOOKING NO": "BookingNo", "CONT": "ContainerNo",
        "SEAL": "SealNo", "CO FORM": "CoForm", "REMARK": "Remark", "SQ": "SQ",
    }
    for header, fld in expected.items():
        assert got[header] == fld, header


@pytest.mark.parametrize("header, must_not_be", [
    ("GO", "CustomsCode"),           # GO la Garment Order, KHONG phai ma HS
    ("CDF\nAMOUNT", "Price"),        # tri gia to khai, khong phai don gia
    ("TSN AMOUNT", "Price"),
    ("Booking \ndate", "BookingFlag"),
    ("CO NO", "ShipmentNo"),
    ("CONT ", "ContainerType"),      # so container, khong phai loai cont
])
def test_no_dangerous_mis_mapping(dictionary, header, must_not_be):
    fld, _, _ = dictionary.match_header(header)
    assert fld != must_not_be


# ---------------------------------------------------------------------------
# 2. Doc file: merge, cong thuc, NO tu invoice, REMARK
# ---------------------------------------------------------------------------


@pytest.fixture
def read_real(real_file, dictionary):
    res = read_workbook(real_file, "F01-C", dictionary, owner_user="user_c")
    rows = {r.values["JO"]: r for r in res.rows}
    return res, rows


def test_merged_cont_type_is_shared_by_group(read_real):
    _, rows = read_real
    grp = [rows[j] for j in ("26V09001KR01", "26V09001KR02", "26V09002KR01")]
    assert {r.values["ContainerType"] for r in grp} == {"1x40HC"}
    assert {r.origin("ContainerType") for r in grp} == {"D2:D4"}   # 1 o duy nhat


def test_formula_columns_detected(read_real):
    res, _ = read_real
    fc = {f.column: f for f in res.sheets[0].formula_columns}
    assert set(fc) == {"A", "X", "Y", "Z"}
    assert fc["A"].pattern == '=IF(LEFT(M{r},4)="TEGV",RIGHT(M{r},5)-2108,"")'
    assert fc["X"].pattern == "=ROUND(W{r}*0.95,2)"


def test_cached_formula_values_are_read(read_real):
    _, rows = read_real
    assert rows["26V09001KR01"].values["ShipmentNo"] == "893"
    assert rows["26V09001KR01"].values["DiscountPrice"] == pytest.approx(10.64)


def test_overwritten_formula_is_flagged(read_real):
    _, rows = read_real
    issues = [i for i in rows["26V09010KR01"].issues if i.get("code") == "EX013"]
    assert len(issues) == 1
    assert "go de" in issues[0]["reason"] and "902" in issues[0]["reason"]


def test_missing_formula_flagged_and_no_derived_from_invoice(read_real):
    _, rows = read_real
    r = rows["26V09011KR01"]
    assert any("chua co cong thuc" in i["reason"] for i in r.issues)
    assert r.values["ShipmentNo"] == "903"          # RIGHT("TEGV03011",5)-2108
    assert "ShipmentNo" in r.derived


def test_pre_invoice_row_has_blank_no_but_is_read(read_real):
    _, rows = read_real
    r = rows["26V09005KR01"]
    assert not r.values.get("ShipmentNo")
    assert r.values["BookingNo"] == "VN0200009"


def test_hold_from_remark(read_real):
    _, rows = read_real
    r = rows["26V09007KR01"]
    assert r.values["HoldFlag"] is True
    assert "HOLD" in r.values["HoldReason"]


def test_stale_formula_cache_detected_and_no_rebuilt(tmp_path, dictionary):
    """File luu boi openpyxl (khong co gia tri tinh san) -> canh bao + tu tinh lai NO."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "KR"          # "Sheet" mac dinh nam trong danh sach sheet bo qua
    for i, h in enumerate(REAL_HEADERS, start=1):
        ws.cell(1, i, h)
    for r, (inv, jo) in enumerate((("TEGV03001", "J1"), ("TEGV03002", "J2")), start=2):
        ws.cell(r, 1, f'=IF(LEFT(M{r},4)="TEGV",RIGHT(M{r},5)-2108,"")')
        ws.cell(r, 2, dt.datetime(2026, 10, 1))
        ws.cell(r, 3, "KRB")
        ws.cell(r, 7, jo)
        ws.cell(r, 13, inv)
        ws.cell(r, 14, 100)
        ws.cell(r, 17, 5.0)
    p = tmp_path / "stale.xlsx"
    wb.save(p)
    res = read_workbook(p, "T", dictionary)
    assert res.sheets[0].stale_formula_cache
    assert [r.values["ShipmentNo"] for r in res.rows] == ["893", "894"]


# ---------------------------------------------------------------------------
# 3. Nghiep vu tren cau truc that
# ---------------------------------------------------------------------------


def test_derive_matches_excel_formula():
    assert derive_invoice_suffix("TEGV03001").value == "893"
    assert derive_invoice_suffix("ABC00001").value == ""            # IF(...,"") nhanh sai
    assert derive_invoice_suffix("TEGVAB12X").state == "INVALID"    # #VALUE!


def test_remark_keyword_respects_negation():
    assert find_keyword("HOLD - cho khach", ["HOLD"], ["UNHOLD"]) == "HOLD"
    assert find_keyword("da UNHOLD", ["HOLD"], ["UNHOLD"]) == ""
    assert find_keyword("HOUSEHOLD goods", ["HOLD"]) == ""


@pytest.mark.parametrize("raw, label, cap", [
    ("1x20", "1x20ST", 23), ("1X40HQ", "1x40HC", 63), ("2x40HC+1x20", "2x40HC + 1x20ST", 149),
    ("1x40'", "1x40DC", 53), ("40HC", "1x40HC", 63),
])
def test_parse_user_container_type(raw, label, cap):
    spec = parse_container_type(raw)
    assert spec.valid and spec.label == label and spec.capacity({"40HC": 63, "40DC": 53, "20ST": 23}) == cap


def test_parse_container_type_rejects_unknown():
    assert not parse_container_type("3x45").valid
    assert parse_container_type("LCL").mode == "LCL"


def test_completed_needs_cont_seal_and_etd():
    base = {"BookingNo": "VN1", "CDF": "C1", "BL": "B1", "ContainerNo": "CONT1",
            "SealNo": "S1", "ETD": dt.date(2026, 9, 12)}
    assert evaluate(base, today=dt.date(2026, 9, 21)).status == "COMPLETED"
    assert evaluate({**base, "SealNo": ""}, today=dt.date(2026, 9, 21)).status == "GREEN"
    assert evaluate(base, today=dt.date(2026, 9, 10)).status == "GREEN"      # tau chua chay
    air = {**base, "ContainerNo": "", "SealNo": "", "_booking_mode": "AIR"}
    assert evaluate(air, today=dt.date(2026, 9, 21)).status == "COMPLETED"   # AIR khong can cont


def test_real_structure_statuses(scanned):
    assert ship("26V09001KR01").effective_status == "GREEN"
    assert ship("26V09005KR01").effective_status == "YELLOW"    # co booking, chua CDF/BL
    assert ship("26V09006KR01").effective_status == "WHITE"
    assert ship("26V09007KR01").effective_status == "RED"       # HOLD tu REMARK
    assert ship("26V08990KR01").effective_status == "COMPLETED"


def test_group_partial_when_half_loaded(scanned):
    """NO 895: 1/2 dong da dong cont + seal -> ca nhom PARTIAL (xuat toi nay 1 nua)."""
    for jo in ("26V09004KR01", "26V09004KR02"):
        sh = ship(jo)
        assert sh.effective_status == "PARTIAL"
        assert "1/2" in sh.status_reason["reason"]


def test_split_jo_same_invoice_is_one_container_group(scanned):
    """JO tach KR01/KR02 van xuat chung 1 NO -> 1 nhom, 1 cont (o merge tinh 1 lan)."""
    a = alloc_of("26V09001KR01")
    assert alloc_of("26V09001KR02").id == a.id == alloc_of("26V09002KR01").id
    assert a.container_type == "1x40HC"
    assert a.qty_40hc == 1                    # KHONG phai 3x40HC
    assert a.allocation_source == "USER_EXCEL"
    assert a.allocation_status == "USER_CONFIRMED"


def test_user_container_shortage_is_critical(scanned):
    a = alloc_of("26V09003KR01")              # 1x20 cho 27.1 CBM
    assert a.allocation_status == "NEED_REVIEW"
    assert "THIEU" in a.note
    with session_scope() as s:
        ex = s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX014")).scalars().all()
    assert any(e.severity == "CRITICAL" for e in ex)


def test_pre_invoice_row_grouped_by_booking(scanned):
    sh = ship("26V09005KR01")
    assert not sh.shipment_no
    assert alloc_of("26V09005KR01").container_type == "1x40DC"   # 30 CBM, Automation de xuat


def test_co_form_taken_from_file(scanned):
    assert ship("26V09001KR01").co_form == "VK"
    assert ship("26V09001KR01").co_required


def test_key_survives_invoice_arriving_later(engine, real_file):
    """NO la cong thuc tu invoice: khi invoice xuat hien, lo hang KHONG duoc tach thanh lo moi."""
    engine.run_scan(today=TODAY, deliver_notifications=False)
    code_before = ship("26V09005KR01").shipment_code

    pkg = XlsxPackage(real_file)
    sh = pkg.sheet(pkg.sheet_names()[0])
    sh.set_value(9, 13, "TEGV03020")          # them invoice cho dong 9
    sh.set_cached_value(9, 1, 912)             # Excel se tinh NO = 912
    tmp = real_file.with_suffix(".tmp.xlsx")
    pkg.save(tmp)
    tmp.replace(real_file)

    res = engine.run_scan(today=TODAY, deliver_notifications=False)
    after = ship("26V09005KR01")
    assert after.shipment_code == code_before
    assert after.shipment_no == "912"
    assert res.shipments_new == 0


# ---------------------------------------------------------------------------
# 4. Mau User to tay trong Excel (USER > AUTO)
# ---------------------------------------------------------------------------


def _paint(path, row: int, rgb: str) -> None:
    pkg = XlsxPackage(path)
    sh = pkg.sheet(pkg.sheet_names()[0])
    sh.fill_row(row, rgb, list(range(2, 48)))
    tmp = path.with_suffix(".tmp.xlsx")
    pkg.save(tmp)
    tmp.replace(path)


def test_user_paints_red_then_back(engine, real_file):
    engine.run_scan(today=TODAY, deliver_notifications=False)
    assert ship("26V09001KR01").effective_status == "GREEN"

    _paint(real_file, 2, "FFFF0000")              # User to do dong 2 = HOLD
    engine.run_scan(today=TODAY, deliver_notifications=False)
    sh = ship("26V09001KR01")
    assert sh.auto_status == "GREEN" and sh.effective_status == "RED"

    engine.run_scan(today=TODAY, deliver_notifications=False)   # quet lai: van RED, khong dao qua lai
    assert ship("26V09001KR01").effective_status == "RED"

    _paint(real_file, 2, "FF92D050")              # User to lai mau xanh cua Automation -> tra quyen cho Automation
    engine.run_scan(today=TODAY, deliver_notifications=False)
    assert ship("26V09001KR01").effective_status == "GREEN"
    with session_scope() as s:
        active = s.execute(select(UserOverride).where(UserOverride.active.is_(True))).scalars().all()
    assert not [o for o in active if o.shipment_code == ship("26V09001KR01").shipment_code]


def test_unknown_user_color_is_left_alone(engine, real_file):
    engine.run_scan(today=TODAY, deliver_notifications=False)
    _paint(real_file, 3, "FF7F7F7F")              # mau khong co trong bang trang thai
    engine.run_scan(today=TODAY, deliver_notifications=False)
    ws = load_workbook(real_file).worksheets[0]
    assert ws["G3"].fill.fgColor.rgb == "FF7F7F7F"  # Automation khong to de


# ---------------------------------------------------------------------------
# 5. Ghi LOSSLESS
# ---------------------------------------------------------------------------


def _parts(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_color_write_is_lossless(real_file, tmp_path):
    # Them 1 part "la" (mo phong external link / printer settings) de chung minh no duoc giu nguyen
    extra = b"\x00BINARY-PRINTER-SETTINGS\xff" * 50
    with zipfile.ZipFile(real_file, "a") as z:
        z.writestr("xl/printerSettings/printerSettings1.bin", extra)
    before = _parts(real_file)
    v_before = load_workbook(real_file, data_only=True).worksheets[0]
    no_before = [v_before.cell(r, 1).value for r in range(2, 15)]

    w = FollowUpColorWriter(real_file, "F01-C", backup_dir=tmp_path / "bk", lock_retry=[0],
                            skip_columns=["A"])
    rep = w.apply([ColorOp(sheet="KRB+KRX(KR)", row=r, color="FF00B0F0", status="COMPLETED",
                           last_col=47) for r in (2, 3, 4)])
    assert not rep.error
    after = _parts(real_file)
    changed = sorted(n for n in before if before[n] != after.get(n))
    assert changed == sorted(["xl/styles.xml", "xl/worksheets/sheet1.xml"])
    assert set(before) == set(after)
    assert after["xl/printerSettings/printerSettings1.bin"] == extra

    v = load_workbook(real_file, data_only=True).worksheets[0]
    f = load_workbook(real_file).worksheets[0]
    assert [v.cell(r, 1).value for r in range(2, 15)] == no_before       # gia tri cong thuc con
    assert f["A2"].value.startswith("=IF(LEFT(M2")                        # cong thuc con
    assert f["A2"].fill.fgColor.rgb == "FFFFFF00"                         # cot A khong bi to
    assert f["G2"].fill.fgColor.rgb == "FF00B0F0"
    assert str(f.merged_cells.ranges).count("D2:D4") == 1


def test_color_write_twice_is_noop(real_file, tmp_path):
    w = FollowUpColorWriter(real_file, "F01-C", backup_dir=tmp_path / "bk", lock_retry=[0])
    ops = [ColorOp(sheet="KRB+KRX(KR)", row=2, color="FF92D050", status="GREEN", last_col=47)]
    w.apply(ops)
    mtime = real_file.stat().st_mtime_ns
    rep = w.apply(ops)
    assert rep.colored == 0 and real_file.stat().st_mtime_ns == mtime     # khong dung toi file


def test_target_insert_extends_formulas_and_keeps_existing(tmp_path):
    """File dich co cot cong thuc: dong moi duoc keo cong thuc, dong cu giu nguyen gia tri."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    for i, h in enumerate(["JO", "EXFTY", "CBM", "WEEK"], start=1):
        ws.cell(1, i, h)
    ws["A2"], ws["B2"], ws["C2"], ws["D2"] = "OLD", dt.datetime(2026, 9, 1), 5, "=WEEKNUM(B2)"
    p = tmp_path / "wp.xlsx"
    wb.save(p)
    pkg = XlsxPackage(p)
    pkg.sheet("2026").set_cached_value(2, 4, 36)
    pkg.save(tmp_path / "wp2.xlsx")
    (tmp_path / "wp2.xlsx").replace(p)

    w = ExcelTargetWriter(p, "F02", "2026", ["JO", "EXFTY", "CBM", "WEEK"], ["JO", "EXFTY"],
                          backup_dir=tmp_path / "bk", lock_retry=[0])
    rep = w.apply([{"JO": "NEW", "EXFTY": dt.date(2026, 9, 25), "CBM": 12.5, "WEEK": 99}])
    assert rep.inserted == 1 and rep.formulas_copied == 1 and not rep.error
    f = load_workbook(p).worksheets[0]
    v = load_workbook(p, data_only=True).worksheets[0]
    assert f["D3"].value == "=WEEKNUM(B3)"            # keo cong thuc, KHONG ghi de bang 99
    assert v["D2"].value == 36                         # dong cu giu gia tri tinh san
    assert v["B3"].value == dt.datetime(2026, 9, 25)   # ngay ghi dung
    assert v["C3"].value == 12.5


def test_formula_cell_never_overwritten(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["B1"], ws["A2"], ws["B2"] = "JO", "TOTAL", "J1", "=1+1"
    p = tmp_path / "f.xlsx"
    wb.save(p)
    pkg = XlsxPackage(p)
    assert pkg.sheet("Sheet").set_value(2, 2, 999) is False


def test_shift_and_normalize_formula():
    assert shift_formula(NO_FORMULA, 3) == 'IF(LEFT(M5,4)="TEGV",RIGHT(M5,5)-2108,"")'
    assert shift_formula("ROUND(W2*0.95,2)", 1) == "ROUND(W3*0.95,2)"
    assert shift_formula("$A$1+A$1+$A1", 5) == "$A$1+A$1+$A6"
    assert shift_formula("LOG10(A2)", 1) == "LOG10(A3)"
    assert normalize_formula("=X2*N2", 2) == normalize_formula("=X7*N7", 7) == "X{r}*N{r}"


def test_inspect_package_reports_openpyxl_risks(real_file):
    rep = inspect_package(real_file)
    assert rep.formulas >= 13 * 3
    assert any("cong thuc" in x for x in rep.openpyxl_would_lose)


def test_styles_do_not_grow_on_repeated_runs(real_file, tmp_path):
    w = FollowUpColorWriter(real_file, "F01-C", backup_dir=tmp_path / "bk", lock_retry=[0])
    for color in ("FF92D050", "FFFFFF00", "FF92D050", "FFFFFF00"):
        w.apply([ColorOp(sheet="KRB+KRX(KR)", row=2, color=color, status="X", last_col=47)])
    size_4 = len(_parts(real_file)["xl/styles.xml"])
    for color in ("FF92D050", "FFFFFF00", "FF92D050", "FFFFFF00"):
        w.apply([ColorOp(sheet="KRB+KRX(KR)", row=2, color=color, status="X", last_col=47)])
    assert len(_parts(real_file)["xl/styles.xml"]) == size_4


def test_ship_mode_letters_are_normalized(read_real):
    """File that ghi MODE = 'S' / 'A' -> phai hieu la SEA / AIR (AIR -> Type AC)."""
    _, rows = read_real
    assert rows["26V09001KR01"].values["ShipMode"] == "SEA"
