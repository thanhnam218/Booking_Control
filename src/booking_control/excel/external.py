"""Doc file Excel NGOAI (khong phai Follow Up): "Shipment schedule" va tuong tu.

File ngoai thuong co hang tram cot may xuat ra, phan lon khong dung den; tieu de co the
lech so voi du lieu ben duoi; nhieu o la loi cong thuc (#REF!, #N/A); va duoi vung du lieu
that con hang nghin dong trong chi co dinh dang.

Module nay KHONG doan bua. No chi:
    1. liet ke sheet,
    2. doan dong tieu de,
    3. dua ra TAT CA cot kem ty le co du lieu + vai gia tri mau,
    4. goi y anh xa cot bang FieldDictionary (nguoi dung van la nguoi chot),
    5. doc du lieu theo dung bo map nguoi dung da chon.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..core.normalize import clean_text
from .mapping import col_letter

# Gia tri loi cua Excel - coi nhu o trong
ERROR_VALUES = {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}

# Bao nhieu dong trong lien tiep thi coi la het du lieu
BLANK_RUN_STOP = 40
MAX_SCAN_ROWS = 100_000


def is_blank(v: Any) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return not s or s in ERROR_VALUES


@dataclass(slots=True)
class ColumnInfo:
    index: int          # 1-based
    letter: str
    header: str
    filled: int         # so dong co du lieu trong pham vi da doc
    samples: list[str] = field(default_factory=list)
    suggestion: str = ""       # truong chuan duoc goi y
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "letter": self.letter, "header": self.header,
                "filled": self.filled, "samples": self.samples,
                "suggestion": self.suggestion, "confidence": round(self.confidence, 2)}


@dataclass(slots=True)
class SheetInfo:
    name: str
    header_row: int
    data_rows: int
    columns: list[ColumnInfo]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "header_row": self.header_row, "data_rows": self.data_rows,
                "columns": [c.as_dict() for c in self.columns]}


def list_sheets(path: str | Path) -> list[dict[str, Any]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return [{"name": ws.title, "rows": ws.max_row or 0, "cols": ws.max_column or 0}
                for ws in wb.worksheets]
    finally:
        wb.close()


def guess_header_row(rows: list[tuple], max_check: int = 12) -> int:
    """Dong tieu de = dong dau tien co nhieu o CHU khac nhau nhat trong 12 dong dau."""
    best_row, best_score = 1, -1.0
    for i, row in enumerate(rows[:max_check], start=1):
        texts = [str(v).strip() for v in row if not is_blank(v)]
        if len(texts) < 3:
            continue
        # uu tien dong toan chu (tieu de), tru diem neu nhieu o la so / ngay
        wordy = sum(1 for t in texts if not _looks_numeric(t))
        score = wordy + len(set(texts)) * 0.5 - (len(texts) - wordy) * 0.8
        if score > best_score:
            best_row, best_score = i, score
    return best_row


def _looks_numeric(t: str) -> bool:
    if isinstance(t, (int, float)):
        return True
    s = t.replace(",", "").replace(" ", "")
    try:
        float(s)
        return True
    except ValueError:
        return s[:10].count("-") == 2 and s[:4].isdigit()


def inspect_sheet(path: str | Path, sheet: str = "", header_row: int | None = None,
                  dictionary=None, scan_rows: int = 400, samples: int = 4) -> SheetInfo:
    """Doc phan dau cua sheet de nguoi dung nhin thay tung cot co gi ben trong."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        raw = []
        for i, row in enumerate(ws.iter_rows(max_row=scan_rows, values_only=True)):
            raw.append(row)
            del i
        if not raw:
            return SheetInfo(ws.title, 1, 0, [])
        hrow = header_row or guess_header_row(raw)
        header = raw[hrow - 1] if hrow - 1 < len(raw) else ()
        body = raw[hrow:]
        width = max((len(r) for r in raw), default=0)

        cols: list[ColumnInfo] = []
        data_rows = 0
        for r in body:
            if any(not is_blank(v) for v in r):
                data_rows += 1
        for idx in range(width):
            head = "" if idx >= len(header) or is_blank(header[idx]) else str(header[idx]).strip()
            filled, samp = 0, []
            for r in body:
                v = r[idx] if idx < len(r) else None
                if is_blank(v):
                    continue
                filled += 1
                if len(samp) < samples:
                    samp.append(_fmt(v))
            info = ColumnInfo(index=idx + 1, letter=col_letter(idx + 1), header=head,
                              filled=filled, samples=samp)
            if dictionary is not None and head:
                info.suggestion, info.confidence = _suggest(dictionary, head)
            cols.append(info)
        return SheetInfo(ws.title, hrow, data_rows, cols)
    finally:
        wb.close()


def _fmt(v: Any) -> str:
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)[:40]


def _suggest(dictionary, header: str) -> tuple[str, float]:
    """Dung Header Mapping Engine co san de goi y truong chuan cho mot tieu de."""
    field_name, confidence, _method = dictionary.match_header(header)
    return (field_name or ""), float(confidence)


def read_mapped_rows(path: str | Path, sheet: str, header_row: int,
                     mapping: dict[int, str], limit: int = 20000) -> list[dict[str, Any]]:
    """Doc du lieu theo bo map nguoi dung chon.

    mapping: chi so cot (1-based) -> ten truong chuan.
    Tra ve danh sach {"_row": so dong trong file, <truong>: gia tri tho}.
    """
    if not mapping:
        return []
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        out: list[dict[str, Any]] = []
        blank_run = 0
        for n, row in enumerate(ws.iter_rows(min_row=header_row + 1, max_row=MAX_SCAN_ROWS,
                                             values_only=True), start=header_row + 1):
            if all(is_blank(v) for v in row):
                blank_run += 1
                if blank_run >= BLANK_RUN_STOP:
                    break
                continue
            blank_run = 0
            rec: dict[str, Any] = {"_row": n}
            has_any = False
            for idx, fld in mapping.items():
                v = row[idx - 1] if 0 < idx <= len(row) else None
                if is_blank(v):
                    continue
                rec[fld] = v
                has_any = True
            if has_any:
                out.append(rec)
            if len(out) >= limit:
                break
        return out
    finally:
        wb.close()


def describe_mapping(mapping: dict[int, str]) -> str:
    return ", ".join(f"{col_letter(i)}->{f}" for i, f in sorted(mapping.items()))


def clean_value(v: Any) -> Any:
    """Bo gia tri loi Excel, cat khoang trang."""
    if is_blank(v):
        return None
    if isinstance(v, str):
        return clean_text(v)
    return v
