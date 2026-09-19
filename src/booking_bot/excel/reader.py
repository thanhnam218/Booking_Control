"""Doc file Follow Up -> ban ghi da chuan hoa (Canonical Record).

Ket qua tra ve la du lieu "sach": moi truong deu da qua normalize va co
trang thai VALID / BLANK / NEED_REVIEW / INVALID (SRS muc 56).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..core.normalize import ParseResult, clean_text, is_blank, parse_by_type, split_invoices
from ..logging_setup import get_logger
from .backup import row_hash, snapshot
from .detector import SheetDetection, detect_workbook
from .mapping import FieldDictionary, HeaderMapper

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

    def get(self, key: str, default: Any = None) -> Any:
        v = self.values.get(key, default)
        return default if v is None else v

    @property
    def confidence(self) -> str:
        if any(i["severity"] == "INVALID" for i in self.issues):
            return "INVALID"
        if any(i["severity"] == "NEED_REVIEW" for i in self.issues):
            return "NEED_REVIEW"
        return "VALID"


@dataclass(slots=True)
class SheetReadResult:
    detection: SheetDetection
    rows: list[CanonicalRow] = field(default_factory=list)
    skipped_rows: int = 0


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
        if f is not None and f.fgColor is not None and f.fgColor.type == "rgb":
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
    """Doc toan bo file Follow Up.

    confirmed_sheets: {sheet_name: True/False} - quyet dinh User da xac nhan truoc do.
    Uu tien tuyet doi hon ket qua tu dong cua Bot.
    """
    p = Path(path)
    snap = snapshot(p, with_hash=False)
    mapper = HeaderMapper(dictionary, db_overrides)

    wb = load_workbook(p, data_only=True, read_only=False)
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

            sr = _read_sheet(wb[det.sheet_name], det, dictionary, file_code, str(p),
                             snap.version_token, owner_user, max_blank_streak)
            result.sheets.append(sr)

        return result
    finally:
        wb.close()


def _read_sheet(
    ws,
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
    # cot dung lam moc kiem tra dong rong
    anchor_cols = [col_of[f] for f in ("ShipmentNo", "JO", "ExFtyDate") if f in col_of] or [
        m.column_index for m in hmap.matches[:3]
    ]

    out = SheetReadResult(detection=det)
    blank_streak = 0
    max_row = ws.max_row or det.header_row

    for r in range(det.header_row + 1, max_row + 1):
        if all(is_blank(ws.cell(row=r, column=c).value) for c in anchor_cols):
            blank_streak += 1
            out.skipped_rows += 1
            if blank_streak >= max_blank_streak:
                break
            continue
        blank_streak = 0

        row = CanonicalRow(
            file_code=file_code,
            file_path=file_path,
            sheet=det.sheet_name,
            row_index=r,
            source_version=version_token,
            owner_user=owner_user,
        )

        for m in hmap.matches:
            spec = dictionary.spec(m.canonical_field)
            raw = ws.cell(row=r, column=m.column_index).value
            res: ParseResult = parse_by_type(raw, spec.data_type if spec else "string", ctx)
            row.values[m.canonical_field] = res.value
            row.states[m.canonical_field] = res.state

            if res.state in ("NEED_REVIEW", "INVALID"):
                row.issues.append(
                    {
                        "field": m.canonical_field,
                        "column": m.column_letter,
                        "severity": res.state,
                        "raw": clean_text(raw),
                        "reason": res.reason,
                    }
                )
            if spec and spec.required and res.state == "BLANK":
                row.issues.append(
                    {
                        "field": m.canonical_field,
                        "column": m.column_letter,
                        "severity": "INVALID",
                        "raw": "",
                        "reason": f"Truong bat buoc '{m.canonical_field}' bi trong",
                    }
                )

        # Buyer lay tu ten sheet neu cot Buyer khong co / trong
        if not clean_text(row.values.get("Buyer")):
            row.values["Buyer"] = det.sheet_name.strip()
            row.states["Buyer"] = "VALID"

        if not clean_text(row.values.get("PIC")) and owner_user:
            row.values["PIC"] = owner_user
            row.states["PIC"] = "VALID"

        row.invoices = split_invoices(row.values.get("Invoice"))

        # Mau nen hien tai cua dong (de biet User da tu to mau chua)
        if anchor_cols:
            row.fill_color = _cell_fill(ws, r, anchor_cols[0])

        row.data_hash = row_hash(
            {k: v for k, v in row.values.items() if k not in ("Remark",)}
        )
        out.rows.append(row)

    log.debug("Doc sheet %s: %s dong du lieu", det.sheet_name, len(out.rows))
    return out
