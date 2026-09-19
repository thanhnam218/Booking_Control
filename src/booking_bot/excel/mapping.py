"""Header Mapping Engine (SRS muc 8, DMS muc 4-5, 34-36).

Bien header thuc te trong Excel -> Canonical Field, kem Mapping Confidence.
KHONG hard-code cot A/B/C. KHONG tu map mo ho: duoi nguong thi hoi User.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from ..core.normalize import clean_text, norm_key


@dataclass(slots=True)
class FieldSpec:
    name: str
    data_type: str = "string"
    required: bool = False
    description: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HeaderMatch:
    source_header: str
    column_index: int  # 1-based
    column_letter: str
    canonical_field: str
    confidence: float
    method: str  # exact | alias | fuzzy | user | db


@dataclass(slots=True)
class SheetHeaderMap:
    header_row: int
    matches: list[HeaderMatch]
    unmatched_headers: list[str]
    ambiguous: list[HeaderMatch]  # can User xac nhan

    def index_of(self, canonical: str) -> int | None:
        for m in self.matches:
            if m.canonical_field == canonical:
                return m.column_index
        return None

    def canonical_fields(self) -> set[str]:
        return {m.canonical_field for m in self.matches}

    def signature(self) -> str:
        import hashlib

        s = "|".join(sorted(f"{m.canonical_field}:{m.column_index}" for m in self.matches))
        return hashlib.sha256(s.encode()).hexdigest()[:32]


def col_letter(idx: int) -> str:
    """1 -> A, 27 -> AA."""
    out = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class FieldDictionary:
    """Nap config/field_mapping.yaml thanh cau truc tra cuu nhanh."""

    def __init__(self, field_mapping_cfg: dict[str, Any]):
        self.raw = field_mapping_cfg or {}
        self.matching = self.raw.get("matching", {})
        self.fuzzy_accept: float = float(self.matching.get("fuzzy_accept", 0.88))
        self.fuzzy_review: float = float(self.matching.get("fuzzy_review", 0.65))

        self.fields: dict[str, FieldSpec] = {}
        self._alias_index: dict[str, str] = {}
        for name, spec in (self.raw.get("fields") or {}).items():
            spec = spec or {}
            fs = FieldSpec(
                name=name,
                data_type=spec.get("type", "string"),
                required=bool(spec.get("required", False)),
                description=spec.get("description", ""),
                aliases=list(spec.get("aliases") or []),
            )
            self.fields[name] = fs
            for alias in [name, *fs.aliases]:
                self._alias_index.setdefault(norm_key(alias), name)

        vm = self.raw.get("value_maps") or {}
        self.ship_mode_map: dict[str, list[str]] = vm.get("ship_mode") or {}
        self.truthy = set(vm.get("truthy") or [])
        self.falsy = set(vm.get("falsy") or [])
        self.blanks = set(vm.get("blank_equivalents") or [])

        dp = self.raw.get("date_parsing") or {}
        self.day_first: bool = bool(dp.get("day_first", True))
        self.date_formats: list[str] = list(dp.get("formats") or [])

        sd = self.raw.get("sheet_detection") or {}
        self.sheet_required_any: list[str] = list(sd.get("required_any") or [])
        self.sheet_min_hits: int = int(sd.get("min_required_hits", 3))
        self.sheet_auto_accept: float = float(sd.get("auto_accept_confidence", 0.8))
        self.sheet_ignore_below: float = float(sd.get("ignore_below_confidence", 0.35))
        self.ignore_sheet_patterns: list[str] = list(sd.get("ignore_sheet_patterns") or [])
        self.header_search_rows: int = int(sd.get("header_search_rows", 15))

    @property
    def parse_ctx(self) -> dict[str, Any]:
        return {
            "date_formats": self.date_formats or None,
            "day_first": self.day_first,
            "truthy": self.truthy or None,
            "falsy": self.falsy or None,
            "blanks": self.blanks or None,
        }

    def spec(self, canonical: str) -> FieldSpec | None:
        return self.fields.get(canonical)

    def required_fields(self) -> list[str]:
        return [n for n, f in self.fields.items() if f.required]

    # -- so khop 1 header --------------------------------------------------

    def match_header(self, header: str) -> tuple[str | None, float, str]:
        """Tra ve (canonical_field, confidence, method)."""
        text = clean_text(header)
        if not text:
            return None, 0.0, "empty"

        key = norm_key(text)
        if not key:
            return None, 0.0, "empty"

        if key in self._alias_index:
            return self._alias_index[key], 1.0, "alias"

        # fuzzy tren toan bo alias
        best_field, best_score = None, 0.0
        for alias_key, fname in self._alias_index.items():
            score = similarity(key, alias_key)
            # Header chua tron ven alias (vd "FINALCBMM3" chua "FINALCBM").
            # Chi coi la khop manh khi alias chiem phan lon header - tranh viec
            # "GHICHUNOIBOCUAPHONGKETOAN" bi ep thanh "Remark" chi vi chua "GHICHU".
            if alias_key and len(alias_key) >= 4 and alias_key in key:
                coverage = len(alias_key) / max(len(key), 1)
                score = max(score, 0.90 if coverage >= 0.6 else 0.80)
            if score > best_score:
                best_field, best_score = fname, score

        if best_score >= self.fuzzy_accept:
            return best_field, round(best_score, 3), "fuzzy"
        if best_score >= self.fuzzy_review:
            return best_field, round(best_score, 3), "fuzzy_review"
        return None, round(best_score, 3), "no_match"


class HeaderMapper:
    """Ap dung FieldDictionary + override tu DB len 1 dong header thuc te."""

    def __init__(self, dictionary: FieldDictionary, db_overrides: dict[str, str] | None = None):
        self.dict = dictionary
        # {norm_key(header): canonical}  - mapping User da xac nhan, uu tien tuyet doi
        self.db_overrides = {norm_key(k): v for k, v in (db_overrides or {}).items()}

    def map_row(self, header_values: list[Any], header_row: int) -> SheetHeaderMap:
        matches: list[HeaderMatch] = []
        ambiguous: list[HeaderMatch] = []
        unmatched: list[str] = []
        taken: dict[str, HeaderMatch] = {}

        for i, raw in enumerate(header_values, start=1):
            text = clean_text(raw)
            if not text:
                continue

            key = norm_key(text)
            if key in self.db_overrides:
                canonical, conf, method = self.db_overrides[key], 1.0, "user"
            else:
                canonical, conf, method = self.dict.match_header(text)

            if canonical is None:
                unmatched.append(text)
                continue

            hm = HeaderMatch(
                source_header=text,
                column_index=i,
                column_letter=col_letter(i),
                canonical_field=canonical,
                confidence=conf,
                method=method,
            )

            if method == "fuzzy_review":
                ambiguous.append(hm)
                continue

            # Neu 2 cot cung map 1 field -> giu cot co confidence cao hon
            prev = taken.get(canonical)
            if prev is None or conf > prev.confidence:
                if prev is not None:
                    matches.remove(prev)
                    unmatched.append(prev.source_header)
                taken[canonical] = hm
                matches.append(hm)
            else:
                unmatched.append(text)

        matches.sort(key=lambda m: m.column_index)
        return SheetHeaderMap(
            header_row=header_row,
            matches=matches,
            unmatched_headers=unmatched,
            ambiguous=ambiguous,
        )
