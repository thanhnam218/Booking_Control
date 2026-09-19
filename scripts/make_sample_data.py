"""Tao bo 5 file Excel DEMO giong cau truc that de chay thu ngay.

    python scripts/make_sample_data.py           -> ./sample_data/
    bookingbot sample                            -> tuong duong

Bo du lieu nay co chu dich chua cac tinh huong kho:
  - Moi sheet Buyer co ten header hoi khac nhau (ExFtyDate / EX FTY DATE / Job Order...)
  - JO tach lam 2 ngay xuat
  - Dong nghi ngo trung lap
  - CBM ghi "25?" -> NEED_REVIEW
  - Ngay ghi sai -> Exception
  - Lo dang HOLD, lo da CANCEL, lo xuat mot phan
  - Sheet Summary / Report khong phai Follow Up -> Bot phai tu bo qua
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", start_color="FF1F4E79", end_color="FF1F4E79")
HEADER_FONT = Font(color="FFFFFFFF", bold=True, size=10)

BUYERS = ["ABC", "XYZ", "DEF", "IKEA", "SAMSUNG"]
DESTS = {"ABC": "SINGAPORE", "XYZ": "LOS ANGELES", "DEF": "HAMBURG",
         "IKEA": "GOTHENBURG", "SAMSUNG": "INCHEON"}


def _style_header(ws, row: int, ncols: int, widths: list[int] | None = None) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    for i in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths[i - 1] if widths else 14)


def _write_rows(ws, header: list[str], rows: list[list], header_row: int = 1,
                widths: list[int] | None = None) -> None:
    for i, h in enumerate(header, start=1):
        ws.cell(row=header_row, column=i, value=h)
    _style_header(ws, header_row, len(header), widths)
    for r, values in enumerate(rows, start=header_row + 1):
        for c, v in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=v)


# ---------------------------------------------------------------------------
# Follow Up
# ---------------------------------------------------------------------------

# Moi bo header mo phong cach ghi khac nhau cua tung nguoi / tung buyer
HEADER_SETS = {
    "standard": ["No", "ExFtyDate", "Ship", "Destination", "JO", "Customs Code(GO)", "OrderQty",
                 "EELInvoice", "FinalExportQty", "Final CartonQty", "Gross Weight (GW)",
                 "Final CBM", "Price", "Discount Price", "Booking", "CDF", "BL",
                 "Hold", "Hold Reason", "Cancel", "Shipped Qty", "Remark"],
    "variant1": ["NO.", "EX FTY DATE", "Ship Mode", "POD", "Job Order", "HS Code", "Order Qty",
                 "Invoice No", "Final Qty", "Carton", "G.W", "Final CBM (M3)", "Unit Price",
                 "Net Price", "Booked", "C/D", "B/L", "On Hold", "Ly do", "Cancelled",
                 "Shipped Qty", "Ghi chu"],
    "variant2": ["STT", "Ngay xuat", "By", "Country", "JO No", "Customs Code", "OrderQty",
                 "INV", "FinalExportQty", "Final CartonQty", "GW", "CBM", "Price",
                 "Discount Price", "Booking No", "CDF", "AWB", "HOLD", "Hold Reason",
                 "Huy", "Da xuat", "Note"],
}

FU_WIDTHS = [7, 12, 7, 14, 13, 13, 10, 14, 11, 10, 9, 10, 9, 10, 12, 12, 12, 7, 16, 7, 10, 22]


def _fu_row(no, exfty, ship, buyer, jo, hs, order_qty, inv, final_qty, ctn, gw, cbm,
            price, disc, booking, cdf, bl, hold, hold_reason, cancel, shipped, remark):
    return [no, exfty, ship, DESTS.get(buyer, ""), jo, hs, order_qty, inv, final_qty, ctn,
            gw, cbm, price, disc, booking, cdf, bl, hold, hold_reason, cancel, shipped, remark]


def build_follow_up_a(path: Path, today: dt.date, rnd: random.Random) -> Path:
    wb = Workbook()
    wb.remove(wb.active)

    def d(offset: int) -> dt.datetime:
        x = today + dt.timedelta(days=offset)
        return dt.datetime(x.year, x.month, x.day)

    # --- Sheet ABC: cau truc chuan ---------------------------------------
    ws = wb.create_sheet("ABC")
    rows = [
        # Chua book, dang trong cua so T-20..T-14 -> canh bao booking
        _fu_row("10001", d(18), "SEA", "ABC", "JO-A1001", "6204.62.00", 5000, "", None, None,
                None, 62.5, 4.20, 3.95, "", "", "", "", "", "", None, ""),
        # Chua book nhung da qua T-14 -> TRE BOOKING (critical)
        _fu_row("10002", d(11), "SEA", "ABC", "JO-A1002", "6203.42.00", 3200, "", None, None,
                None, 28.0, 5.10, None, "", "", "", "", "", "", None, ""),
        # Da book, thieu BL -> YELLOW
        _fu_row("10003", d(9), "SEA", "ABC", "JO-A1003", "6109.10.00", 8000, "INV-A3301",
                7980, 320, 4100, 96.4, 3.80, 3.60, "BKG-77120", "CDF-556677", "", "", "", "", None, ""),
        # Da co CDF + BL -> GREEN
        _fu_row("10004", d(5), "SEA", "ABC", "JO-A1004", "6110.20.00", 6400, "INV-A3302, INV-A3303",
                6400, 260, 3300, 47.2, 4.05, 3.90, "BKG-77121", "CDF-556680", "MAEU-8891234",
                "", "", "", None, "2 invoice / 1 to khai"),
        # Dang HOLD -> RED (uu tien cao nhat du da co CDF/BL)
        _fu_row("10005", d(7), "SEA", "ABC", "JO-A1005", "6204.63.00", 2100, "INV-A3310",
                2100, 88, 1150, 18.6, 6.10, None, "BKG-77122", "CDF-556690", "MAEU-8891250",
                "Yes", "CUSTOMER", "", None, "Khach chua chot mau"),
        # T-3: thieu Carton + GW -> canh bao kiem tra thong tin
        _fu_row("10006", d(2), "SEA", "ABC", "JO-A1006", "6205.20.00", 4400, "INV-A3315",
                4400, None, None, 33.5, 3.70, 3.50, "BKG-77123", "CDF-556701", "", "", "", "", None, ""),
        # T-1: sap xuat
        _fu_row("10007", d(1), "SEA", "ABC", "JO-A1007", "6206.30.00", 5200, "INV-A3320",
                5200, 210, 2650, 160.0, 4.40, 4.10, "BKG-77124", "CDF-556710", "ONEY-5567781",
                "", "", "", None, "160 CBM -> 2x40HC + 1x40DC"),
        # CBM ghi khong chac chan -> NEED_REVIEW
        _fu_row("10008", d(12), "SEA", "ABC", "JO-A1008", "6104.43.00", 1800, "", None, None,
                None, "25?", 5.60, None, "", "", "", "", "", "", None, "Chua do lai khoi"),
    ]
    _write_rows(ws, HEADER_SETS["standard"], rows, widths=FU_WIDTHS)

    # --- Sheet XYZ: header kieu khac -------------------------------------
    ws = wb.create_sheet("XYZ")
    rows = [
        _fu_row("20001", d(16), "AIR", "XYZ", "JO-X2001", "8517.62.00", 900, "", None, None,
                None, 2.4, 22.00, None, "", "", "", "", "", "", None, "Hang AIR -> Type AC"),
        _fu_row("20002", d(6), "AIR", "XYZ", "JO-X2002", "8517.70.00", 1500, "INV-X9901",
                1500, 60, 480, 3.8, 19.50, 18.90, "BKG-AIR-441", "CDF-771020", "157-88991234",
                "", "", "", None, ""),
        # JO tach lam 2 ngay xuat - dong 1
        _fu_row("20003", d(4), "SEA", "XYZ", "JO-X2003", "9403.60.00", 2400, "INV-X9910",
                1200, 50, 900, 21.0, 12.00, None, "BKG-88101", "CDF-771030", "", "", "", "", None,
                "JO tach 2 dot - dot 1"),
        # JO tach lam 2 ngay xuat - dong 2 (cung JO, khac ExFtyDate)
        _fu_row("20004", d(10), "SEA", "XYZ", "JO-X2003", "9403.60.00", 2400, "INV-X9911",
                1200, 50, 900, 21.0, 12.00, None, "", "", "", "", "", "", None,
                "JO tach 2 dot - dot 2"),
        # Da xuat mot phan -> PARTIAL
        _fu_row("20005", d(0), "SEA", "XYZ", "JO-X2005", "9401.61.00", 3000, "INV-X9920",
                3000, 120, 2200, 52.0, 9.80, 9.40, "BKG-88105", "CDF-771040", "OOLU-3345567",
                "", "", "", 1500, "Toi nay xuat 1 nua, mai dong tiep"),
        # Da xuat xong -> COMPLETED
        _fu_row("20006", d(-2), "SEA", "XYZ", "JO-X2006", "9401.71.00", 1800, "INV-X9925",
                1800, 75, 1400, 30.5, 10.20, None, "BKG-88106", "CDF-771050", "OOLU-3345590",
                "", "", "", 1800, "Da xuat xong"),
        # Ngay xuat ghi sai -> Exception EX001
        _fu_row("20007", "31/02/2026", "SEA", "XYZ", "JO-X2007", "9401.80.00", 1200, "", None,
                None, None, 15.0, 8.80, None, "", "", "", "", "", "", None, "Ngay sai"),
    ]
    _write_rows(ws, HEADER_SETS["variant1"], rows, widths=FU_WIDTHS)

    # --- Sheet khong phai Follow Up --------------------------------------
    ws = wb.create_sheet("Summary")
    _write_rows(ws, ["Thang", "So lo", "Tong CBM", "Ghi chu"],
                [["09/2026", 15, 640.2, "Tong hop thang"], ["10/2026", 12, 512.0, ""]])

    ws = wb.create_sheet("Data")
    _write_rows(ws, ["Ma", "Gia tri"], [["TY_GIA", 25400], ["PHI_THU_TUC", 1500000]])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def build_follow_up_b(path: Path, today: dt.date, rnd: random.Random) -> Path:
    wb = Workbook()
    wb.remove(wb.active)

    def d(offset: int) -> dt.datetime:
        x = today + dt.timedelta(days=offset)
        return dt.datetime(x.year, x.month, x.day)

    # --- IKEA: gom lo theo ngay xuat (by_exfty trong buyer_rules) --------
    ws = wb.create_sheet("IKEA")
    rows = [
        _fu_row("30001", d(19), "SEA", "IKEA", "JO-I3001", "9403.20.00", 2600, "", None, None,
                None, 34.0, 7.20, None, "", "", "", "", "", "", None, ""),
        _fu_row("30002", d(19), "SEA", "IKEA", "JO-I3002", "9403.30.00", 1900, "", None, None,
                None, 26.5, 6.90, None, "", "", "", "", "", "", None, "Cung ngay -> gom chung container"),
        _fu_row("30003", d(8), "SEA", "IKEA", "JO-I3003", "9403.40.00", 4200, "INV-I7701",
                4200, 175, 3100, 58.0, 8.10, 7.80, "BKG-IK-9021", "CDF-990110", "", "", "", "", None, ""),
        # Da book roi nhung lai HOLD de doi gop lo sau
        _fu_row("30004", d(13), "SEA", "IKEA", "JO-I3004", "9403.50.00", 1100, "INV-I7705",
                1100, 45, 800, 12.2, 9.00, None, "BKG-IK-9022", "", "", "Yes", "MERGE_WAIT",
                "", None, "Cho lo sau xuat chung 1 ngay"),
        # Da book roi nhung bi CANCEL
        _fu_row("30005", d(15), "SEA", "IKEA", "JO-I3005", "9403.60.00", 800, "", None, None,
                None, 9.5, 9.40, None, "BKG-IK-9023", "", "", "", "", "Yes", None,
                "Khach doi lich, huy booking"),
    ]
    _write_rows(ws, HEADER_SETS["standard"], rows, widths=FU_WIDTHS)

    # --- SAMSUNG: buyer ep Type = AC -------------------------------------
    ws = wb.create_sheet("SAMSUNG")
    rows = [
        _fu_row("40001", d(17), "AIR", "SAMSUNG", "JO-S4001", "8471.30.00", 600, "", None, None,
                None, 1.8, 120.00, None, "", "", "", "", "", "", None, ""),
        _fu_row("40002", d(3), "AIR", "SAMSUNG", "JO-S4002", "8471.41.00", 450, "INV-S5501",
                450, 18, 210, 1.2, 135.00, 130.00, "BKG-AIR-990", "CDF-660120", "180-77112233",
                "", "", "", None, ""),
    ]
    _write_rows(ws, HEADER_SETS["variant2"], rows, widths=FU_WIDTHS)

    # --- DEF: buyer yeu cau User duyet container -------------------------
    ws = wb.create_sheet("DEF")
    rows = [
        _fu_row("50001", d(14), "SEA", "DEF", "JO-D5001", "4202.92.00", 3400, "INV-D2201",
                3400, 140, 1900, 44.0, 11.20, None, "BKG-DE-3301", "", "", "", "", "", None,
                "Buyer nay phai User duyet container"),
        # Dong nghi ngo trung lap (cung NO + JO + ExFty + INV voi dong tren)
        _fu_row("50001", d(14), "SEA", "DEF", "JO-D5001", "4202.92.00", 3400, "INV-D2201",
                3400, 140, 1900, 44.0, 11.20, None, "BKG-DE-3301", "", "", "", "", "", None,
                "Dong bi nhap trung"),
        _fu_row("50002", d(20), "SEA", "DEF", "JO-D5002", "4202.22.00", 2200, "", None, None,
                None, 19.4, 13.50, None, "", "", "", "", "", "", None, ""),
    ]
    _write_rows(ws, HEADER_SETS["standard"], rows, widths=FU_WIDTHS)

    ws = wb.create_sheet("Report")
    _write_rows(ws, ["Tuan", "Da xuat", "Con lai"], [["W38", 6, 9], ["W39", 4, 11]])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# File dung chung
# ---------------------------------------------------------------------------


def build_weekly_planning(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    _write_rows(
        ws,
        ["NO", "EXFTY", "BUYER", "JO", "PCS", "CBM", "MODE", "TYPE", "PIC", "CO", "CO FORM", "STATUS"],
        [],
        widths=[9, 12, 12, 14, 9, 9, 8, 18, 16, 6, 10, 12],
    )
    wb.save(path)
    return path


def build_custom_code(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "HS CODE"
    _write_rows(ws, ["NO", "JO", "BUYER", "CUSTOMS CODE", "EXFTY", "PIC"], [],
                widths=[9, 14, 12, 16, 12, 16])
    wb.save(path)
    return path


def build_mm_daily(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "DAILY"
    _write_rows(
        ws,
        ["DATE", "NO", "BUYER", "JO", "PCS", "CTN", "GW", "CBM", "MODE", "TYPE", "PIC", "REMARK"],
        [],
        widths=[12, 9, 12, 14, 9, 8, 9, 9, 8, 18, 16, 16],
    )
    wb.save(path)
    return path


def build_shipping_schedule(path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for site, label in (("TDV", "Co so 2"), ("TGV", "Co so 1")):
        ws = wb.create_sheet(site)
        _write_rows(ws, ["NO", "EXFTY", "BUYER", "JO", "CBM", "TYPE", "PIC"], [],
                    widths=[9, 12, 12, 14, 9, 18, 16])
        ws.cell(row=1, column=9, value=f"{site} = {label}")
    wb.save(path)
    return path


# ---------------------------------------------------------------------------


def build_all(out_dir: Path, seed: int = 42, today: dt.date | None = None) -> list[Path]:
    rnd = random.Random(seed)
    today = today or dt.date.today()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        build_follow_up_a(out_dir / "FollowUp_UserA.xlsx", today, rnd),
        build_follow_up_b(out_dir / "FollowUp_UserB.xlsx", today, rnd),
        build_weekly_planning(out_dir / "WEEKLY_PLANNING_2026.xlsx"),
        build_custom_code(out_dir / "CUSTOM_CODE.xlsx"),
        build_mm_daily(out_dir / "MM_DAILY_PLANNING_SEP_2026_GV.xlsx"),
        build_shipping_schedule(out_dir / "LICH_XUAT_HANG_IP_DV.xlsx"),
    ]


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample_data")
    for p in build_all(target):
        print("Da tao:", p)
