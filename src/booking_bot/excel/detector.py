"""Tu dong nhan dien sheet Follow Up + dong header (SRS muc 6-7, DMS muc 3).

Bot KHONG duoc dua vao ten sheet. Bot do header roi cham diem confidence.
Sheet trong vung "xam" se duoc dua ra cho User xac nhan mot lan (SRS muc 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from ..core.normalize import clean_text
from .mapping import FieldDictionary, HeaderMapper, SheetHeaderMap


@dataclass(slots=True)
class SheetDetection:
    sheet_name: str
    is_follow_up: bool
    confidence: float
    header_row: int
    header_map: SheetHeaderMap | None
    decision: str  # SCAN | ASK_USER | IGNORE
    note: str = ""
    matched_fields: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)


def _row_values(ws: Worksheet, row: int, max_col: int) -> list[Any]:
    return [ws.cell(row=row, column=c).value for c in range(1, max_col + 1)]


def find_header_row(
    ws: Worksheet, dictionary: FieldDictionary, mapper: HeaderMapper, max_rows: int | None = None
) -> tuple[int, SheetHeaderMap | None, float]:
    """Do N dong dau, chon dong co nhieu canonical field nhat.

    Tra ve (header_row, header_map, score). score = ty le truong required_any tim thay.
    """
    max_rows = max_rows or dictionary.header_search_rows
    max_col = min(ws.max_column or 1, 80)
    limit = min(ws.max_row or 1, max_rows)

    best_row, best_map, best_score = 0, None, 0.0
    required = set(dictionary.sheet_required_any)

    for r in range(1, limit + 1):
        values = _row_values(ws, r, max_col)
        if not any(clean_text(v) for v in values):
            continue
        hmap = mapper.map_row(values, r)
        found = hmap.canonical_fields()
        hits = len(found & required)
        if not required:
            score = min(1.0, len(found) / 6)
        else:
            score = hits / len(required)
        # thuong them khi map duoc nhieu cot noi chung
        score += min(0.15, 0.02 * len(found))
        if score > best_score:
            best_row, best_map, best_score = r, hmap, score

    return best_row, best_map, min(best_score, 1.0)


def detect_sheet(
    ws: Worksheet, dictionary: FieldDictionary, mapper: HeaderMapper
) -> SheetDetection:
    name = ws.title

    for pat in dictionary.ignore_sheet_patterns:
        try:
            if re.search(pat, name.strip(), re.IGNORECASE):
                return SheetDetection(
                    sheet_name=name,
                    is_follow_up=False,
                    confidence=0.0,
                    header_row=0,
                    header_map=None,
                    decision="IGNORE",
                    note=f"Ten sheet khop pattern bo qua: {pat}",
                )
        except re.error:
            continue

    if ws.sheet_state != "visible":
        return SheetDetection(name, False, 0.0, 0, None, "IGNORE", "Sheet bi an")

    if (ws.max_row or 0) < 2:
        return SheetDetection(name, False, 0.0, 0, None, "IGNORE", "Sheet rong")

    header_row, hmap, score = find_header_row(ws, dictionary, mapper)
    if hmap is None or header_row == 0:
        return SheetDetection(name, False, 0.0, 0, None, "IGNORE", "Khong tim thay dong header")

    found = hmap.canonical_fields()
    required = set(dictionary.sheet_required_any)
    hits = sorted(found & required)
    missing = sorted(required - found)

    if len(hits) < dictionary.sheet_min_hits:
        decision = "IGNORE" if score < dictionary.sheet_ignore_below else "ASK_USER"
        return SheetDetection(
            sheet_name=name,
            is_follow_up=False,
            confidence=round(score, 3),
            header_row=header_row,
            header_map=hmap,
            decision=decision,
            note=f"Chi khop {len(hits)}/{dictionary.sheet_min_hits} truong bat buoc toi thieu",
            matched_fields=hits,
            missing_required=missing,
        )

    if score >= dictionary.sheet_auto_accept:
        decision, is_fu = "SCAN", True
        note = f"Khop {len(hits)}/{len(required)} truong nhan dien"
    elif score >= dictionary.sheet_ignore_below:
        decision, is_fu = "ASK_USER", False
        note = f"Confidence {score:.0%} nam trong vung can User xac nhan"
    else:
        decision, is_fu = "IGNORE", False
        note = f"Confidence {score:.0%} qua thap"

    return SheetDetection(
        sheet_name=name,
        is_follow_up=is_fu,
        confidence=round(score, 3),
        header_row=header_row,
        header_map=hmap,
        decision=decision,
        note=note,
        matched_fields=hits,
        missing_required=missing,
    )


def detect_workbook(wb, dictionary: FieldDictionary, mapper: HeaderMapper) -> list[SheetDetection]:
    return [detect_sheet(wb[sn], dictionary, mapper) for sn in wb.sheetnames]
