"""Xuat du lieu tren web ra file Excel theo khung Follow Up (SRS v2 muc 18-20, 51, 63-64, 85).

    Database -> (loc theo quyen o backend) -> nhom theo Buyer -> 1 Buyer = 1 sheet
             -> 47 cot Follow Up DUNG THU TU + cac cot he thong o cuoi -> file .xlsx

Giu cam giac file Follow Up: dong tieu de, do rong cot, co dinh cot A..J, dinh dang ngay,
cot NO to vang, mau dong theo trang thai, o CONT TYPE merge theo nhom, va cot
NO / 95% FOB / CDF AMOUNT / TSN AMOUNT la CONG THUC Excel that (giong file goc).
"""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..db.models import Shipment
from ..followup import FollowUpTemplate
from .shipment_service import MANUAL_SRC, booking_status, deadline_label

INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
THIN = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def sheet_name(raw: str, used: set[str]) -> str:
    """Ten sheet Excel: toi da 31 ky tu, khong ky tu cam, khong trung (BUYER_A, BUYER_A_2...)."""
    name = INVALID_SHEET_CHARS.sub(" ", (raw or "").strip()).strip("' ") or "KHONG RO BUYER"
    name = re.sub(r"\s+", " ", name)
    if len(name) > 31:
        cut = name[:31]
        name = cut.rsplit(" ", 1)[0] if " " in cut and len(cut.rsplit(" ", 1)[0]) >= 10 else cut
        name = name.strip()
    base, n = name, 1
    while name.upper() in used:
        n += 1
        suffix = f"_{n}"
        name = base[: 31 - len(suffix)] + suffix
    used.add(name.upper())
    return name


def export_filename(label: str, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_") or "Export"
    return f"{safe}_Shipment_{today:%Y_%m_%d}.xlsx"


def build_workbook(
    shipments: list[Shipment], tmpl: FollowUpTemplate, *, status_color, today: dt.date | None = None,
    include_system_columns: bool = True, write_formulas: bool = True, color_rows: bool = True,
    user_labels: dict[str, str] | None = None, title: str = "",
) -> bytes:
    today = today or dt.date.today()
    user_labels = user_labels or {}
    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.creator = "Booking Control"
    wb.properties.title = title or "Follow Up export"

    groups: dict[str, list[Shipment]] = {}
    for sh in shipments:
        groups.setdefault(sh.buyer_code or sh.buyer_raw or "KHONG RO BUYER", []).append(sh)

    used: set[str] = set()
    if not groups:
        ws = wb.create_sheet(sheet_name("KHONG CO DU LIEU", used))
        _write_header(ws, tmpl, include_system_columns)
    for buyer in sorted(groups):
        ws = wb.create_sheet(sheet_name(buyer, used))
        _write_header(ws, tmpl, include_system_columns)
        rows = sorted(groups[buyer], key=lambda x: (x.exfty_date or dt.date.max, x.shipment_no or "",
                                                     x.jo or ""))
        _write_rows(ws, rows, tmpl, status_color=status_color, today=today,
                    include_system_columns=include_system_columns, write_formulas=write_formulas,
                    color_rows=color_rows, user_labels=user_labels)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_header(ws, tmpl: FollowUpTemplate, include_system: bool) -> None:
    head_fill = PatternFill("solid", start_color=tmpl.header_fill, end_color=tmpl.header_fill)
    sys_fill = PatternFill("solid", start_color="FFDDEBF7", end_color="FFDDEBF7")
    font = Font(bold=True, size=10)
    align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for c in tmpl.columns:
        cell = ws.cell(1, c.index + 1, c.header)
        cell.fill, cell.font, cell.alignment, cell.border = head_fill, font, align, BORDER
        ws.column_dimensions[c.letter].width = c.width
    if include_system:
        start = len(tmpl.columns) + 1
        for i, sc in enumerate(tmpl.system_columns):
            cell = ws.cell(1, start + i, sc.header)
            cell.fill, cell.font, cell.alignment, cell.border = sys_fill, font, align, BORDER
            ws.column_dimensions[get_column_letter(start + i)].width = sc.width
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = f"{get_column_letter(tmpl.freeze_columns + 1)}2"
    last = len(tmpl.columns) + (len(tmpl.system_columns) if include_system else 0)
    ws.auto_filter.ref = f"A1:{get_column_letter(last)}1"


def _excel_value(col, value: Any) -> Any:
    if value is None or value == "":
        return None
    if col.type == "date" and isinstance(value, str):
        try:
            d = dt.date.fromisoformat(value[:10])
            return dt.datetime(d.year, d.month, d.day)
        except ValueError:
            return value
    return value


def _write_rows(ws, rows: list[Shipment], tmpl: FollowUpTemplate, *, status_color, today: dt.date,
                include_system_columns: bool, write_formulas: bool, color_rows: bool,
                user_labels: dict[str, str]) -> None:
    letter_of = {c.field: c.letter for c in tmpl.columns}
    no_fill = (PatternFill("solid", start_color=tmpl.no_column_fill, end_color=tmpl.no_column_fill)
               if tmpl.no_column_fill else None)
    fills: dict[str, PatternFill] = {}
    body_font = Font(size=10)
    merge_cols = [c for c in tmpl.columns if c.merge_group]
    first_col = tmpl.columns[0].field if tmpl.columns else ""

    for i, sh in enumerate(rows):
        r = i + 2
        data = sh.row_data or {}
        manual = {f for f, m in (sh.field_src or {}).items() if (m or {}).get("src") in MANUAL_SRC}
        color = status_color(sh.effective_status or "WHITE") if color_rows else ""
        if color and color not in fills:
            fills[color] = PatternFill("solid", start_color=color, end_color=color)
        for c in tmpl.columns:
            cell = ws.cell(r, c.index + 1)
            if write_formulas and c.formula is not None and c.field not in manual:
                formula = c.formula.excel(letter_of, r)
                cell.value = formula if formula else _excel_value(c, data.get(c.field))
            else:
                cell.value = _excel_value(c, data.get(c.field))
            if c.type == "date":
                cell.number_format = c.excel_format or "dd/mm/yyyy"
            cell.font = body_font
            cell.border = BORDER
            if c.field == first_col and no_fill is not None:
                cell.fill = no_fill
            elif color:
                cell.fill = fills[color]
        if include_system_columns:
            start = len(tmpl.columns) + 1
            values = {
                "assigned_to": user_labels.get(sh.assigned_to, sh.assigned_to),
                "backup_owner": user_labels.get(sh.backup_owner, sh.backup_owner),
                "forwarder": sh.forwarder or (sh.forwarder_status or ""),
                "booking_status": booking_status(sh),
                "effective_status": sh.effective_status,
                "deadline_status": deadline_label(sh, today),
                "updated_at": (sh.updated_at or sh.last_seen_at),
            }
            for j, sc in enumerate(tmpl.system_columns):
                v = values.get(sc.key, getattr(sh, sc.key, ""))
                cell = ws.cell(r, start + j, v if v not in ("",) else None)
                cell.font, cell.border = body_font, BORDER
                if isinstance(v, dt.datetime):
                    cell.number_format = "dd/mm/yyyy hh:mm"

    # Merge CONT TYPE cho cac dong lien nhau cung nhom va cung gia tri (giong file goc)
    for c in merge_cols:
        start = 0
        for i in range(1, len(rows) + 1):
            end_run = (
                i == len(rows)
                or rows[i].group_key != rows[start].group_key
                or (rows[i].row_data or {}).get(c.field) != (rows[start].row_data or {}).get(c.field)
            )
            if end_run:
                val = (rows[start].row_data or {}).get(c.field)
                if i - start > 1 and val not in (None, ""):
                    col = c.index + 1
                    ws.merge_cells(start_row=start + 2, start_column=col, end_row=i + 1, end_column=col)
                    ws.cell(start + 2, col).alignment = Alignment(vertical="center", horizontal="center")
                start = i
