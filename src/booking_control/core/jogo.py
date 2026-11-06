"""Quan he JO - GO (SRSv5.1 muc 5, SRSv5.4 muc 8).

SRS noi ro: "Khong nen chi luu GO duoi dang text trong Shipment" - mot GO co the lien
quan den nhieu JO, va quan he do phai tra cuu duoc theo ca hai chieu.

Cach nhan biet: JO va GO dung chung mot phan ma goc.

    JO 26V02426KR01 ─┐
                     ├─ ma goc 26V02426 ─ GO S26V02426
    JO 26V02426KR02 ─┘

Quy tac tach ma goc nam trong config/field_mapping.yaml > jo_go, khong hard-code, vi
moi cong ty danh ma mot kieu.

Nguon cua quan he (SRS):
    IMPORT      - file Excel ghi JO va GO tren cung mot dong
    SYSTEM_RULE - suy ra tu ma goc giong nhau
    USER        - nguoi dung tu noi
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .normalize import clean_text

SOURCE_IMPORT = "IMPORT"
SOURCE_RULE = "SYSTEM_RULE"
SOURCE_USER = "USER"

# Do tin cay khi hai ma cung goc nhung khong nam cung dong Excel
RULE_CONFIDENCE = 0.8

# Mac dinh theo file that: JO co duoi <2 chu nuoc><2 so>, GO co tien to S
DEFAULT_JO_PATTERN = r"^(?P<core>.+?)[A-Z]{2}\d{1,3}$"
DEFAULT_GO_PATTERN = r"^[A-Z]?(?P<core>.+)$"
MIN_CORE_LEN = 4


@dataclass(slots=True)
class JoGoRules:
    jo_pattern: str = DEFAULT_JO_PATTERN
    go_pattern: str = DEFAULT_GO_PATTERN
    min_core_len: int = MIN_CORE_LEN

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> JoGoRules:
        cfg = cfg or {}
        return cls(
            jo_pattern=str(cfg.get("jo_pattern") or DEFAULT_JO_PATTERN),
            go_pattern=str(cfg.get("go_pattern") or DEFAULT_GO_PATTERN),
            min_core_len=int(cfg.get("min_core_len") or MIN_CORE_LEN),
        )

    def core_of_jo(self, jo: Any) -> str:
        return _core(jo, self.jo_pattern, self.min_core_len)

    def core_of_go(self, go: Any) -> str:
        return _core(go, self.go_pattern, self.min_core_len)


def _core(value: Any, pattern: str, min_len: int) -> str:
    """Tach ma goc. Khong khop hoac qua ngan -> tra chuoi rong (khong doan bua)."""
    text = clean_text(value).upper().replace(" ", "")
    if not text:
        return ""
    try:
        m = re.match(pattern, text)
    except re.error:
        return ""
    core = (m.group("core") if m and "core" in (m.groupdict() or {}) else "") or ""
    core = core.strip("-_/")
    return core if len(core) >= min_len else ""


@dataclass(slots=True)
class JoGoLink:
    jo: str
    go: str
    core: str
    source: str
    confidence: float = 1.0

    def key(self) -> tuple[str, str]:
        return (self.jo.upper(), self.go.upper())


def links_from_rows(rows: list[tuple[str, str]], rules: JoGoRules) -> list[JoGoLink]:
    """Tu cac cap (JO, GO) doc duoc trong file -> danh sach quan he.

    Buoc 1: JO va GO nam cung mot dong  -> IMPORT (chac chan).
    Buoc 2: JO khac cung ma goc voi mot GO da biet -> SYSTEM_RULE (de xuat).
    """
    out: dict[tuple[str, str], JoGoLink] = {}
    go_by_core: dict[str, set[str]] = {}
    jo_by_core: dict[str, set[str]] = {}

    for raw_jo, raw_go in rows:
        jo, go = clean_text(raw_jo).upper(), clean_text(raw_go).upper()
        if jo:
            core = rules.core_of_jo(jo)
            if core:
                jo_by_core.setdefault(core, set()).add(jo)
        if go:
            core = rules.core_of_go(go)
            if core:
                go_by_core.setdefault(core, set()).add(go)
        if jo and go:
            core = rules.core_of_jo(jo) or rules.core_of_go(go)
            link = JoGoLink(jo=jo, go=go, core=core, source=SOURCE_IMPORT, confidence=1.0)
            out[link.key()] = link

    for core, gos in go_by_core.items():
        for jo in jo_by_core.get(core, set()):
            for go in gos:
                link = JoGoLink(jo=jo, go=go, core=core, source=SOURCE_RULE,
                                confidence=RULE_CONFIDENCE)
                out.setdefault(link.key(), link)   # IMPORT da co thi giu nguyen
    return sorted(out.values(), key=lambda x: (x.core, x.go, x.jo))


def group_by_go(links: list[JoGoLink]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for link in links:
        out.setdefault(link.go, []).append(link.jo)
    return {go: sorted(set(jos)) for go, jos in out.items()}
