"""Doc file Follow Up -> ban ghi da chuan hoa (Canonical Record).

Ket qua tra ve la du lieu "sach": moi truong deu da qua normalize va co
trang thai VALID / BLANK / NEED_REVIEW / INVALID (SRS muc 56).

Nhung dac diem cua file Follow Up THAT duoc xu ly o day:
  - O merge (CONT TYPE D2:D5 cho ca nhom 4 dong cung NO)
      -> gia tri lan xuong moi dong, kem "origin" de biet 4 dong dung CHUNG 1 o
  - Cot la CONG THUC (NO, 95% FOB, CDF AMOUNT, TSN AMOUNT)
      -> doc gia tri tinh san; kiem tra cong thuc con nguyen ven hay bi go de
  - NO = RIGHT(INVOICE,5)-2108
      -> neu gia tri tinh san bi mat thi tu tinh lai; neu go tay sai thi bao
  - Khong co cot HOLD / CANCEL
      -> doc tu khoa trong REMARK
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..core.normalize import (
    ParseResult,
    apply_derived_rule,
    clean_text,
    find_keyword,
    is_blank,
    parse_by_type,
    parse_ship_mode,
    split_invoices,
)
from ..logging_setup import get_logger
from .backup import row_hash, snapshot
from .detector import SheetDetection, detect_workbook
from .mapping import FieldDictionary, HeaderMapper, col_letter
from .xlsx_patch import normalize_formula

log = get_logger("excel.reader")


@dataclass(slots=True)
class CanonicalRow:
    file_code: str
    file_path: str
    sheet: str
    row_index: int
    values: dict[str, Any] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    invoices: list[str] = field(default_factory=list)
    data_hash: str = ""
    source_version: str = ""
    owner_user: str = ""
    fill_color: str = ""
    # field -> "D2:D5" neu gia tri den tu o merge (dung chung cho nhieu dong)
    merged_from: dict[str, str] = field(default_factory=dict)
    # ghi chu nhung gi Bot suy ra (NO tu invoice, HOLD tu REMARK...)
    derived: dict[str, str] = field(default_factory=dict)
    last_col: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        v = self.values.get(key, default)
        return default if v is None else v

    def origin(self, fld: str) -> str:
        """Dinh danh o nguon: merge range neu co, khong thi toa do rieng cua dong."""
        return self.merged_from.get(fld) or f"{self.sheet}!{fld}{self.row_index}"

    @property
    def confidence(self) -> str:
        if any(i["severity"] == "INVALID" for i in self.issues):
            return "INVALID"
        if any(i["severity"] == "NEED_REVIEW" for i in self.issues):
            return "NEED_REVIEW"
        return "VALID"


@dataclass(slots=True)
class FormulaColumn:
    """Mot cot dang dung cong thuc (vd NO, 95% FOB)."""

    field: str
    column: str
    pattern: str
    formula_rows: int
    stale_rows: int = 0


@dataclass(slots=True)
class SheetReadResult:
    detection: SheetDetection
    rows: list[CanonicalRow] = field(default_factory=list)
    skipped_rows: int = 0
    formula_columns: list[FormulaColumn] = field(default_factory=list)
    merged_ranges: list[str] = field(default_factory=list)
    stale_formula_cache: bool = False
    sheet_issues: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class WorkbookReadResult:
    file_code: str
    file_path: str
    version_token: str
    sheets: list[SheetReadResult] = field(default_factory=list)
    needs_user_confirmation: list[SheetDetection] = field(default_factory=list)
    ignored: list[SheetDetection] = field(default_factory=list)

    @property
    def rows(self) -> list[CanonicalRow]:
        out: list[CanonicalRow] = []
        for s in self.sheets:
            out.extend(s.rows)
        return out


def _cell_fill(ws, row: int, col: int) -> str:
    try:
        f = ws.cell(row=row, column=col).fill
        if f is not None and f.fill_type == "solid" and f.fgColor is not None and f.fgColor.type == "rgb":
            return str(f.fgColor.rgb or "")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    return ""


def read_workbook(
    path: str | Path,
    file_code: str,
    dictionary: FieldDictionary,
    db_overrides: dict[str, str] | None = None,
    confirmed_sheets: dict[str, bool] | None = None,
    owner_user: str = "",
    max_blank_streak: int = 25,
) -> WorkbookReadResult:
    """Doc toan bo file Follow Up (CHI DOC - khong bao gio luu).

    confirmed_sheets: {sheet_name: True/False} - quyet dinh User da xac nhan truoc do.
    """
    p = Path(path)
    snap = snapshot(p, with_hash=False)
    mapper = HeaderMapper(dictionary, db_overrides)

    wb = load_workbook(p, data_only=True)          # gia tri tinh san
    wb_f = load_workbook(p, data_only=False)       # cong thuc
    try:
        detections = detect_workbook(wb, dictionary, mapper)
        result = WorkbookReadResult(file_code=file_code, file_path=str(p), version_token=snap.version_token)
        confirmed = confirmed_sheets or {}

        for det in detections:
            user_choice = confirmed.get(det.sheet_name)
            if user_choice is True:
                det.is_follow_up, det.decision = True, "SCAN"
                det.note += " | User da xac nhan la Follow Up"
            elif user_choice is False:
                det.is_follow_up, det.decision = False, "IGNORE"
                det.note += " | User da danh dau bo qua"

            if det.decision == "ASK_USER":
                result.needs_user_confirmation.append(det)
                continue
            if det.decision != "SCAN" or det.header_map is None:
                result.ignored.append(det)
                continue

            sr = _read_sheet(wb[det.sheet_name], wb_f[det.sheet_name], det, dictionary, file_code,
                             str(p), snap.version_token, owner_user, max_blank_streak)
            result.sheets.append(sr)

        return result
    finally:
        wb.close()
        wb_f.close()


def _merged_map(ws, header_row: int) -> tuple[dict[tuple[int, int], tuple[Any, str]], list[str]]:
    """(row, col) -> (gia tri o goc tren-trai, 'D2:D5') cho moi o nam trong vung merge."""
    out: dict[tuple[int, int], tuple[Any, str]] = {}
    ranges: list[str] = []
    for rng in ws.merged_cells.ranges:
        if rng.max_row <= header_row:
            continue
        ranges.append(str(rng))
        top = ws.cell(row=rng.min_row, column=rng.min_col).value
        for r in range(max(rng.min_row, header_row + 1), rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                out[(r, c)] = (top, str(rng))
    return out, ranges


def _read_sheet(
    ws,
    ws_f,
    det: SheetDetection,
    dictionary: FieldDictionary,
    file_code: str,
    file_path: str,
    version_token: str,
    owner_user: str,
    max_blank_streak: int,
) -> SheetReadResult:
    hmap = det.header_map
    assert hmap is not None
    ctx = dictionary.parse_ctx
    col_of = {m.canonical_field: m.column_index for m in hmap.matches}
    field_of_col = {m.column_index: m.canonical_field for m in hmap.matches}
    anchor_cols = [col_of[f] for f in ("JO", "ExFtyDate", "Invoice") if f in col_of] or [
        m.column_index for m in hmap.matches[:3]
    ]
    header_last_col = max(
        (c for c in range(1, (ws.max_column or 1) + 1) if clean_text(ws.cell(det.header_row, c).value)),
        default=ws.max_column or 1,
    )

    merged, merged_ranges = _merged_map(ws, det.header_row)
    out = SheetReadResult(detection=det, merged_ranges=merged_ranges)

    def value_at(r: int, c: int) -> tuple[Any, str]:
        if (r, c) in merged:
            return merged[(r, c)]
        return ws.cell(row=r, column=c).value, ""

    def formula_at(r: int, c: int) -> str | None:
        v = ws_f.cell(row=r, column=c).value
        return v if isinstance(v, str) and v.startswith("=") else None

    # cot -> [(row, cong thuc | None, gia tri)]
    col_trace: dict[int, list[tuple[int, str | None, Any]]] = {c: [] for c in field_of_col}
    rows_by_index: dict[int, CanonicalRow] = {}
    blank_streak = 0
    max_row = ws.max_row or det.header_row

    for r in range(det.header_row + 1, max_row + 1):
        if all(is_blank(value_at(r, c)[0]) for c in anchor_cols):
            blank_streak += 1
            out.skipped_rows += 1
            if blank_streak >= max_blank_streak:
                break
            continue
        blank_streak = 0

        row = CanonicalRow(
            file_code=file_code, file_path=file_path, sheet=det.sheet_name, row_index=r,
            source_version=version_token, owner_user=owner_user, last_col=header_last_col,
        )

        for m in hmap.matches:
            spec = dictionary.spec(m.canonical_field)
            raw, merge_ref = value_at(r, m.column_index)
            if merge_ref:
                row.merged_from[m.canonical_field] = merge_ref
            col_trace[m.column_index].append((r, formula_at(r, m.column_index), raw))

            if m.canonical_field == "ShipMode":
                # 'S' / 'A' / 'By Sea'... -> SEA / AIR (value_maps.ship_mode)
                res: ParseResult = parse_ship_mode(raw, dictionary.ship_mode_map or None)
            else:
                res = parse_by_type(raw, spec.data_type if spec else "string", ctx)
            row.values[m.canonical_field] = res.value
            row.states[m.canonical_field] = res.state

            if res.state in ("NEED_REVIEW", "INVALID"):
                row.issues.append({
                    "field": m.canonical_field, "column": m.column_letter, "severity": res.state,
                    "raw": clean_text(raw), "reason": res.reason,
                })
            if spec and spec.required and res.state == "BLANK":
                row.issues.append({
                    "field": m.canonical_field, "column": m.column_letter, "severity": "INVALID",
                    "raw": "", "reason": f"Truong bat buoc '{m.canonical_field}' bi trong",
                })

        if not clean_text(row.values.get("Buyer")):
            row.values["Buyer"] = det.sheet_name.strip()
            row.states["Buyer"] = "VALID"

        if not clean_text(row.values.get("PIC")) and owner_user:
            row.values["PIC"] = owner_user
            row.states["PIC"] = "VALID"

        _apply_remark_keywords(row, dictionary, col_of)

        row.invoices = split_invoices(row.values.get("Invoice"))
        fill_col = col_of.get("JO") or anchor_cols[0]
        row.fill_color = _cell_fill(ws, r, fill_col)

        out.rows.append(row)
        rows_by_index[r] = row

    _check_formulas(out, col_trace, field_of_col, rows_by_index)
    _apply_derived(out, dictionary, col_of)

    for row in out.rows:
        row.data_hash = row_hash({k: v for k, v in row.values.items() if k not in ("Remark",)})

    log.debug("Doc sheet %s: %s dong du lieu", det.sheet_name, len(out.rows))
    return out


# ---------------------------------------------------------------------------
# REMARK -> HOLD / CANCEL
# ---------------------------------------------------------------------------


def _apply_remark_keywords(row: CanonicalRow, dictionary: FieldDictionary, col_of: dict[str, int]) -> None:
    remark = row.values.get("Remark")
    if not clean_text(remark):
        return
    if "HoldFlag" not in col_of and dictionary.hold_keywords:
        kw = find_keyword(remark, dictionary.hold_keywords, dictionary.hold_negations)
        if kw:
            row.values["HoldFlag"] = True
            row.derived["HoldFlag"] = f"REMARK chua '{kw}'"
            if "HoldReason" not in col_of:
                row.values["HoldReason"] = clean_text(remark)
    if "CancelFlag" not in col_of and dictionary.cancel_keywords:
        kw = find_keyword(remark, dictionary.cancel_keywords)
        if kw:
            row.values["CancelFlag"] = True
            row.derived["CancelFlag"] = f"REMARK chua '{kw}'"


# ---------------------------------------------------------------------------
# Kiem tra toan ven cong thuc
# ---------------------------------------------------------------------------


def _check_formulas(
    out: SheetReadResult,
    col_trace: dict[int, list[tuple[int, str | None, Any]]],
    field_of_col: dict[int, str],
    rows_by_index: dict[int, CanonicalRow],
) -> None:
    total_formula, total_stale = 0, 0

    for col, entries in col_trace.items():
        formulas = [(r, f, v) for r, f, v in entries if f]
        if not formulas:
            continue
        stale = sum(1 for _, _, v in formulas if v is None)
        total_formula += len(formulas)
        total_stale += stale

        # Chi coi la "cot cong thuc" khi da so dong dung cong thuc
        if len(formulas) < 2 or len(formulas) * 2 < len(entries):
            continue
        patterns = Counter(normalize_formula(f, r) for r, f, _ in formulas)
        majority, _ = patterns.most_common(1)[0]
        fld = field_of_col[col]
        letter = col_letter(col)
        out.formula_columns.append(FormulaColumn(fld, letter, "=" + majority, len(formulas), stale))

        for r, f, v in entries:
            row = rows_by_index.get(r)
            if row is None:
                continue
            if f is None and not is_blank(v):
                reason = f"Cot {letter} ({fld}) phai la cong thuc nhung dong {r} bi go de gia tri '{clean_text(v)}'"
            elif f is None:
                reason = f"Dong {r} chua co cong thuc cot {letter} ({fld}) - dong moi quen keo cong thuc?"
            elif normalize_formula(f, r) != majority:
                reason = f"Cong thuc cot {letter} dong {r} khac cac dong con lai: {f}"
            else:
                continue
            row.issues.append({
                "code": "EX013", "field": fld, "column": letter, "severity": "WARNING",
                "raw": f or clean_text(v), "reason": reason,
                "expected": "=" + majority.replace("{r}", str(r)),
            })

    # Tat ca cong thuc deu khong co gia tri tinh san -> file vua bi cong cu khac luu
    if total_formula and total_stale == total_formula:
        out.stale_formula_cache = True
        out.sheet_issues.append({
            "code": "EX013", "severity": "WARNING",
            "reason": (f"{total_formula} o cong thuc chua co gia tri tinh san (file duoc luu boi "
                       "cong cu khac Excel). Mo file bang Excel va Save de cap nhat."),
        })


# ---------------------------------------------------------------------------
# Truong suy ra (NO tu INVOICE)
# ---------------------------------------------------------------------------


def _apply_derived(out: SheetReadResult, dictionary: FieldDictionary, col_of: dict[str, int]) -> None:
    for fld, rule in dictionary.derived_fields.items():
        source = rule.get("source", "")
        if source not in col_of:
            continue
        for row in out.rows:
            derived = apply_derived_rule(rule, row.values.get(source))
            current = row.values.get(fld)

            if is_blank(current):
                if derived.value:
                    row.values[fld] = derived.value
                    row.states[fld] = "VALID"
                    row.derived[fld] = f"tinh tu {source} theo {rule.get('excel_formula', rule.get('rule'))}"
                continue

            if derived.state == "VALID" and clean_text(current) != clean_text(derived.value):
                letter = col_letter(col_of[fld]) if fld in col_of else fld
                detail = (f"gia tri {clean_text(current)} khong khop cong thuc tu {source} "
                          f"'{clean_text(row.values.get(source))}' (dung ra la {derived.value})")
                prev = next((i for i in row.issues if i.get("code") == "EX013" and i["field"] == fld), None)
                if prev is not None:
                    prev["reason"] += f"; {detail}"
                    continue
                row.issues.append({
                    "code": "EX013", "field": fld, "column": letter, "severity": "WARNING",
                    "raw": clean_text(current),
                    "reason": (f"{fld} = {clean_text(current)} khong khop cong thuc tu {source} "
                               f"'{clean_text(row.values.get(source))}' (phai la {derived.value})"),
                })
