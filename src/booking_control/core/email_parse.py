"""Phan loai email + trich xuat truong tu than email / PDF (SRSv5 muc 19-26).

Module thuan (khong doc Outlook, khong doc DB). Parser dua tren cau hinh
config/email_profiles.yaml, khong hard-code dinh dang cua tung forwarder:

    "VSL: ABC 2609N", "Vessel/Voyage : ABC / 2609N", "CY COT: 02/10 17:00"
        -> vessel="ABC", voyage="2609N", cy_cutoff="02/10 17:00"
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from .normalize import norm_key, parse_date, strip_accents

CATEGORIES = ("BOOKING_REQUEST", "BOOKING_CONFIRMATION", "CANCEL", "REBOOK", "HOLD", "RELEASE_HOLD", "DELAY",
              "AMENDMENT", "OTHER")

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd|tr|aw|sv)\s*:\s*)+", re.I)
_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ./#&()'_-]{0,40}?)\s*(?::|=|\t| {2,}|-{1,2}>)\s*(.+?)\s*$")
_BOOKING_FALLBACK = re.compile(
    r"\b(?:BOOKING|BKG|S/O)\s*(?:NO\.?|NUMBER|REF|#)?\s*[:#.]?\s*([A-Z0-9][A-Z0-9-]{5,24})\b", re.I)
_CONTAINERS = re.compile(r"\b(\d{1,2})\s*[xX*]\s*(20|40|45)\s*'?\s*(GP|DC|ST|HC|HQ|RF)?\b")


@dataclass
class ParsedMail:
    category: str = "OTHER"
    category_keyword: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


def clean_subject(subject: str) -> str:
    return _SUBJECT_PREFIX.sub("", subject or "").strip()


def _norm_text(s: str) -> str:
    return " " + re.sub(r"\s+", " ", strip_accents(s or "").lower()) + " "


def classify(subject: str, body: str, categories: dict[str, list[str]]) -> tuple[str, str]:
    """Tra (loai, tu khoa trung). Xet tieu de truoc, roi than email; thu tu = thu tu trong config."""
    subj = _norm_text(clean_subject(subject))
    text = _norm_text(body[:4000])
    for where in (subj, text):
        for cat, words in categories.items():
            for w in words:
                if _norm_text(w).strip() and _norm_text(w).strip() in where:
                    return cat, w
    return "OTHER", ""


# ---------------------------------------------------------------------------
# Ngay thang trong email: "02 OCT", "02-Oct-2026", "OCT 02", "2026-10-02", "02/10/2026", "02/10"
# ---------------------------------------------------------------------------

_D_MON = re.compile(r"\b(\d{1,2})[\s\-./]*([A-Za-z]{3})[A-Za-z]*[\s\-./,]*(\d{2,4})?\b")
_MON_D = re.compile(r"\b([A-Za-z]{3})[A-Za-z]*[\s\-./]*(\d{1,2})(?:st|nd|rd|th)?[\s,]*(\d{4})?\b")
_DMY = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _year_for(month: int, day: int, ref: dt.date) -> int:
    """Nam gan ngay nhan email nhat (email thang 12 noi '02 JAN' -> nam sau)."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - ref).days) < abs((best - ref).days):
            best = d
    return best.year if best else ref.year


def parse_mail_date(text: str, ref: dt.date) -> dt.date | None:
    if not text:
        return None
    s = str(text).strip()
    m = _ISO.search(s)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = _D_MON.search(s)
    if m and m[2].upper()[:3] in MONTHS:
        day, mon = int(m[1]), MONTHS[m[2].upper()[:3]]
        year = int(m[3]) if m[3] else _year_for(mon, day, ref)
        year = year + 2000 if year < 100 else year
        try:
            return dt.date(year, mon, day)
        except ValueError:
            return None
    m = _MON_D.search(s)
    if m and m[1].upper()[:3] in MONTHS:
        mon, day = MONTHS[m[1].upper()[:3]], int(m[2])
        year = int(m[3]) if m[3] else _year_for(mon, day, ref)
        try:
            return dt.date(year, mon, day)
        except ValueError:
            return None
    m = _DMY.search(s)
    if m:
        day, mon = int(m[1]), int(m[2])
        if m[3]:
            res = parse_date(m[0])
            return res.value if res.value else None
        try:
            return dt.date(_year_for(mon, day, ref), mon, day)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Trich xuat truong
# ---------------------------------------------------------------------------


def build_alias_index(aliases: dict[str, list[str]], extra: dict[str, list[str]] | None = None) -> dict[str, str]:
    idx: dict[str, str] = {}
    for src in (aliases or {}), (extra or {}):
        for fld, names in src.items():
            for n in names:
                idx[norm_key(n)] = fld
            idx.setdefault(norm_key(fld), fld)
    return idx


def extract_fields(text: str, alias_index: dict[str, str], ref: dt.date) -> dict[str, Any]:
    """Doc cac dong "Nhan : gia tri" + vai mau chung (booking no, container)."""
    out: dict[str, Any] = {}
    for raw in (text or "").splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        label, value = m[1], m[2].strip(" .;")
        fld = alias_index.get(norm_key(label))
        if not fld or not value or fld in out:
            continue
        if fld == "vessel" and "/" in value and "voyage" not in out:
            v, _, voy = value.partition("/")
            value, out["voyage"] = v.strip(), voy.strip()
        out[fld] = value

    if "booking_no" not in out:
        m = _BOOKING_FALLBACK.search(text or "")
        if m and any(ch.isdigit() for ch in m[1]):
            out["booking_no"] = m[1].upper()
    elif isinstance(out["booking_no"], str):
        out["booking_no"] = out["booking_no"].split()[0].upper().strip("#:")

    cont = _CONTAINERS.findall(text or "")
    if cont and "containers" not in out:
        out["containers"] = "+".join(f"{q}x{size}{(kind or '').upper().replace('HQ', 'HC')}" for q, size, kind in cont)

    for dfield in ("etd", "eta"):
        if dfield in out:
            d = parse_mail_date(str(out[dfield]), ref)
            out[dfield] = d.isoformat() if d else None
            if out[dfield] is None:
                out.pop(dfield)
    return out


# Truong so sanh email vs PDF (khac nhau -> CONFLICT, SRSv5 muc 23)
COMPARE_FIELDS = ("booking_no", "etd", "eta", "vessel", "voyage", "cy_cutoff", "si_vgm_cutoff", "closing_time")


def _cmp_norm(fld: str, v: Any) -> str:
    s = norm_key(v)
    return s


def find_conflicts(body_fields: dict[str, Any], pdf_fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in COMPARE_FIELDS:
        a, b = body_fields.get(f), pdf_fields.get(f)
        if a in (None, "") or b in (None, ""):
            continue
        if _cmp_norm(f, a) != _cmp_norm(f, b):
            out[f] = {"email": a, "pdf": b}
    return out


def merge_fields(body_fields: dict[str, Any], pdf_fields: dict[str, Any]) -> dict[str, Any]:
    """Khong mau thuan: gop, PDF bo sung truong email khong co."""
    merged = dict(pdf_fields)
    merged.update({k: v for k, v in body_fields.items() if v not in (None, "")})
    return merged
