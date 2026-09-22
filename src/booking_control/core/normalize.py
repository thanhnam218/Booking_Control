"""Chuan hoa gia tri doc tu Excel (DMS muc 6-7, 15).

Moi gia tri tho tu Excel deu di qua day truoc khi vao Database.
Khong doan bua: khong parse duoc -> tra ve None + ly do -> Exception Center.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# Cac gia tri duoc coi la "o trong" mac dinh (co the ghi de bang config)
DEFAULT_BLANKS = {
    "",
    "-",
    "--",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "TBA",
    "TBC",
    "PENDING",
    "WAITING",
    "CHUA CO",
    "?",
    "X X",
}

DEFAULT_TRUTHY = {"YES", "Y", "TRUE", "1", "X", "V", "OK", "DONE", "CO", "BOOKED", "DA BOOK"}
DEFAULT_FALSY = {"NO", "N", "FALSE", "0", "-", "NA", "N/A", "KHONG", "CHUA", "PENDING"}

DEFAULT_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%b %d, %Y",
]

_NUM_CLEAN = re.compile(r"[^\d,.\-]")
_WS = re.compile(r"\s+")


@dataclass(slots=True)
class ParseResult:
    """Ket qua chuan hoa. state: VALID | BLANK | NEED_REVIEW | INVALID."""

    value: Any
    state: str = "VALID"
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state in ("VALID", "BLANK")


# ---------------------------------------------------------------------------
# Chuoi
# ---------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    """Bo dau tieng Viet de so sanh header: 'Ngay xuat' == 'Ngày xuất'."""
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D")


def norm_key(text: Any) -> str:
    """Chuan hoa de so khop header/alias: bo dau, bo ky tu dac biet, viet hoa."""
    if text is None:
        return ""
    s = strip_accents(str(text))
    s = re.sub(r"[^0-9a-zA-Z]+", "", s)
    return s.upper()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).replace(" ", " ").strip()
    return _WS.sub(" ", s)


def is_blank(value: Any, blanks: set[str] | None = None) -> bool:
    """SRS/DMS muc 20-21: CDF/BL 'da dien' nghia la khac rong VA khac cac gia tri gia."""
    s = clean_text(value)
    if not s:
        return True
    pool = blanks if blanks is not None else DEFAULT_BLANKS
    return strip_accents(s).upper() in {strip_accents(b).upper() for b in pool}


def has_value(value: Any, blanks: set[str] | None = None) -> bool:
    return not is_blank(value, blanks)


# ---------------------------------------------------------------------------
# Ngay thang
# ---------------------------------------------------------------------------


def parse_date(value: Any, formats: list[str] | None = None, day_first: bool = True) -> ParseResult:
    """Excel co the tra ve datetime, so serial, hoac chuoi nhieu dinh dang."""
    if is_blank(value):
        return ParseResult(None, "BLANK", "o trong")

    if isinstance(value, dt.datetime):
        return ParseResult(value.date())
    if isinstance(value, dt.date):
        return ParseResult(value)

    # So serial cua Excel (1900 date system)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = float(value)
        if 20000 <= n <= 80000:  # ~1954 .. ~2119
            try:
                base = dt.date(1899, 12, 30)
                return ParseResult(base + dt.timedelta(days=int(n)))
            except (OverflowError, ValueError):
                return ParseResult(None, "INVALID", f"so serial khong hop le: {value}")
        return ParseResult(None, "NEED_REVIEW", f"so khong giong ngay: {value}")

    s = clean_text(value)
    fmts = list(formats or DEFAULT_DATE_FORMATS)
    if not day_first:
        fmts = ["%m/%d/%Y"] + [f for f in fmts if f != "%m/%d/%Y"]

    for fmt in fmts:
        try:
            return ParseResult(dt.datetime.strptime(s, fmt).date())
        except ValueError:
            continue

    # Thu tach so: 20/09/2026, 20.9.26, 2026-9-20
    m = re.match(r"^(\d{1,4})\D(\d{1,2})\D(\d{1,4})", s)
    if m:
        a, b, c = (int(x) for x in m.groups())
        try:
            if a > 31:  # YYYY-MM-DD
                return ParseResult(dt.date(a, b, c))
            year = c if c > 99 else 2000 + c
            if day_first:
                return ParseResult(dt.date(year, b, a))
            return ParseResult(dt.date(year, a, b))
        except ValueError:
            return ParseResult(None, "INVALID", f"ngay khong ton tai: {s}")

    return ParseResult(None, "NEED_REVIEW", f"khong doc duoc ngay: '{s}'")


def to_canonical_date(d: dt.date | None) -> str:
    return d.strftime("%Y-%m-%d") if d else ""


# ---------------------------------------------------------------------------
# So
# ---------------------------------------------------------------------------


def parse_decimal(value: Any) -> ParseResult:
    """Ho tro '1,234.56', '1.234,56', '25 CBM', '25?' -> NEED_REVIEW."""
    if is_blank(value):
        return ParseResult(None, "BLANK", "o trong")
    if isinstance(value, bool):
        return ParseResult(None, "INVALID", "gia tri boolean")
    if isinstance(value, (int, float)):
        return ParseResult(float(value))

    s = clean_text(value)
    suspicious = bool(re.search(r"[?~]|approx|khoang", s, re.IGNORECASE))
    cleaned = _NUM_CLEAN.sub("", s)
    if not cleaned or cleaned in {"-", ".", ","}:
        return ParseResult(None, "NEED_REVIEW", f"khong co so trong '{s}'")

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):  # 1.234,56 (EU)
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:  # 1,234.56 (US)
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = cleaned.replace(",", "." if len(parts[-1]) != 3 else "")

    try:
        num = float(cleaned)
    except ValueError:
        return ParseResult(None, "NEED_REVIEW", f"khong parse duoc so: '{s}'")

    if suspicious:
        return ParseResult(num, "NEED_REVIEW", f"gia tri co ky tu khong chac chan: '{s}'")
    return ParseResult(num)


def parse_int(value: Any) -> ParseResult:
    r = parse_decimal(value)
    if r.value is None:
        return r
    return ParseResult(int(round(r.value)), r.state, r.reason)


# ---------------------------------------------------------------------------
# Co (flag)
# ---------------------------------------------------------------------------


def parse_flag(
    value: Any, truthy: set[str] | None = None, falsy: set[str] | None = None
) -> ParseResult:
    """None = khong xac dinh duoc (khac han voi False)."""
    if is_blank(value):
        return ParseResult(None, "BLANK", "o trong")
    if isinstance(value, bool):
        return ParseResult(value)
    if isinstance(value, (int, float)):
        return ParseResult(bool(value))

    s = strip_accents(clean_text(value)).upper()
    t = {strip_accents(x).upper() for x in (truthy or DEFAULT_TRUTHY)}
    f = {strip_accents(x).upper() for x in (falsy or DEFAULT_FALSY)}
    if s in t:
        return ParseResult(True)
    if s in f:
        return ParseResult(False)
    # Co noi dung khac -> coi nhu "co du lieu" = True (vd o Booking ghi so booking)
    return ParseResult(True, "VALID", f"suy ra True vi o co noi dung: '{s}'")


# ---------------------------------------------------------------------------
# Ship mode
# ---------------------------------------------------------------------------


def parse_ship_mode(value: Any, value_maps: dict[str, list[str]] | None = None) -> ParseResult:
    if is_blank(value):
        return ParseResult("", "BLANK", "o trong")
    s = strip_accents(clean_text(value)).upper()
    maps = value_maps or {
        "AIR": ["AIR", "A", "BY AIR", "AIRFREIGHT"],
        "SEA": ["SEA", "S", "BY SEA", "OCEAN", "VESSEL"],
        "EXPRESS": ["EXPRESS", "DHL", "FEDEX", "UPS"],
    }
    for canonical, aliases in maps.items():
        for a in aliases:
            au = strip_accents(str(a)).upper()
            if s == au or s.startswith(au + " ") or au in s.split():
                return ParseResult(canonical)
    return ParseResult(s, "NEED_REVIEW", f"ship mode khong xac dinh: '{s}'")


# ---------------------------------------------------------------------------
# Invoice nhieu gia tri
# ---------------------------------------------------------------------------

_INV_SPLIT = re.compile(r"[,;/|\n]+")


def split_invoices(value: Any) -> list[str]:
    """'INV001, INV002 ; INV003' -> ['INV001','INV002','INV003'] (SRS muc 10)."""
    if is_blank(value):
        return []
    parts = [clean_text(p) for p in _INV_SPLIT.split(clean_text(value))]
    out: list[str] = []
    for p in parts:
        if p and not is_blank(p) and p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_by_type(value: Any, data_type: str, ctx: dict[str, Any] | None = None) -> ParseResult:
    ctx = ctx or {}
    dt_ = (data_type or "string").lower()
    if dt_ == "date":
        return parse_date(value, ctx.get("date_formats"), ctx.get("day_first", True))
    if dt_ in ("decimal", "float", "number"):
        return parse_decimal(value)
    if dt_ == "int":
        return parse_int(value)
    if dt_ in ("bool", "flag"):
        return parse_flag(value, ctx.get("truthy"), ctx.get("falsy"))
    if is_blank(value, ctx.get("blanks")):
        return ParseResult("", "BLANK", "o trong")
    return ParseResult(clean_text(value))


# ---------------------------------------------------------------------------
# Truong suy ra tu cong thuc Excel (derived_fields trong field_mapping.yaml)
# ---------------------------------------------------------------------------


def derive_invoice_suffix(
    invoice: Any, prefix: str = "TEGV", last_digits: int = 5, offset: int = -2108
) -> ParseResult:
    """Tai tao dung cong thuc =IF(LEFT(INV,4)="TEGV",RIGHT(INV,5)-2108,"").

    - Invoice khong bat dau bang prefix -> "" (giong Excel tra chuoi rong)
    - Phan duoi khong phai so        -> INVALID (Excel se ra #VALUE!)
    """
    s = clean_text(invoice)
    if not s:
        return ParseResult("", "BLANK", "chua co invoice")
    if not s.upper().startswith(prefix.upper()):
        return ParseResult("", "BLANK", f"invoice khong bat dau bang {prefix}")
    tail = s[-last_digits:]
    if not tail.isdigit():
        return ParseResult(None, "INVALID", f"{last_digits} ky tu cuoi cua '{s}' khong phai so (#VALUE!)")
    return ParseResult(str(int(tail) + offset))


def apply_derived_rule(rule: dict[str, Any], source_value: Any) -> ParseResult:
    kind = (rule or {}).get("rule", "")
    if kind == "invoice_suffix":
        return derive_invoice_suffix(
            source_value,
            prefix=str(rule.get("prefix", "")),
            last_digits=int(rule.get("last_digits", 5)),
            offset=int(rule.get("offset", 0)),
        )
    return ParseResult(None, "NEED_REVIEW", f"rule '{kind}' chua duoc ho tro")


def find_keyword(text: Any, keywords: list[str], negations: list[str] | None = None) -> str:
    """Tra ve tu khoa tim thay trong text (bo dau, khong phan biet hoa thuong)."""
    s = f" {strip_accents(clean_text(text)).upper()} "
    if not s.strip():
        return ""
    for neg in negations or []:
        if strip_accents(neg).upper() in s:
            return ""
    for kw in keywords:
        k = strip_accents(kw).upper()
        if re.search(rf"(?<![A-Z]){re.escape(k)}(?![A-Z])", s):
            return kw
    return ""
