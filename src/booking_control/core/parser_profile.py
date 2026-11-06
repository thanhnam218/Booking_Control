"""Forwarder Parser Profile (SRSv4 muc 15, 68 bang 2 · SRSv5 muc 25-26 · BR-020).

SRS noi ro: "Khong dung mot parser cho tat ca" va "Moi Forwarder phai duoc cau hinh
bang Parser Profile/Mapping, khong hard-code format".

    Email
      ↓ sender / domain
    Forwarder Profile
      ↓ dia chi nguoi gui cu the
    Contact Profile          (mot Forwarder co nhieu nhan vien, moi nguoi mot kieu)
      ↓
    Field Extraction

Moi profile co the khai:
    field_aliases   nhan cot kieu "VSL:" -> vessel
    patterns        bieu thuc chinh quy rieng cho tung truong
    subject_must    email phai co tu nay trong tieu de moi ap profile
    attachment      ten file dinh kem cua Forwarder nay

Thu tu uu tien khi lay gia tri mot truong:
    Contact pattern > Forwarder pattern > alias chung
vi mau cang cu the cang dang tin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Truong duoc phep khai pattern - trung voi COMPARE_FIELDS cua email_parse
PATTERN_FIELDS = (
    "booking_no", "jo", "go", "po", "etd", "eta", "vessel", "voyage",
    "cy_cutoff", "si_vgm_cutoff", "closing_time", "port", "container_no", "seal_no",
)

MAX_PATTERN_LEN = 300


class ProfileError(ValueError):
    """Pattern sai cu phap hoac khai truong khong ho tro."""


def _compile(name: str, pattern: str, *, require_group: bool = True) -> re.Pattern[str]:
    if len(pattern) > MAX_PATTERN_LEN:
        raise ProfileError(f"Pattern cho '{name}' qua dai (toi da {MAX_PATTERN_LEN} ky tu)")
    try:
        rx = re.compile(pattern, re.I | re.M)
    except re.error as exc:
        raise ProfileError(f"Pattern cho '{name}' sai cu phap: {exc}") from exc
    # Pattern cua mot TRUONG phai bat duoc gia tri; pattern ten file chi de kiem tra khop
    if require_group and rx.groups < 1:
        raise ProfileError(f"Pattern cho '{name}' phai co it nhat mot nhom bat gia tri, "
                           f"vi du: BKG[ :]+(?P<v>[A-Z0-9-]+)")
    return rx


@dataclass(slots=True)
class ParserProfile:
    name: str
    domains: list[str] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    field_aliases: dict[str, list[str]] = field(default_factory=dict)
    patterns: dict[str, re.Pattern[str]] = field(default_factory=dict)
    subject_must: list[str] = field(default_factory=list)
    attachment_pattern: re.Pattern[str] | None = None
    contacts: list[ParserProfile] = field(default_factory=list)
    note: str = ""

    # -- nhan dien -------------------------------------------------------
    def matches(self, sender: str) -> bool:
        addr = (sender or "").lower().strip()
        if not addr:
            return False
        if addr in [x.lower() for x in self.senders]:
            return True
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        return bool(domain) and domain in [x.lower() for x in self.domains]

    def contact_for(self, sender: str) -> ParserProfile | None:
        """Nhan vien cu the cua Forwarder nay (neu co khai rieng)."""
        for c in self.contacts:
            if c.matches(sender):
                return c
        return None

    def applies_to_subject(self, subject: str) -> bool:
        if not self.subject_must:
            return True
        text = (subject or "").lower()
        return any(k.lower() in text for k in self.subject_must)

    def wants_attachment(self, filename: str) -> bool:
        if self.attachment_pattern is None:
            return True
        return bool(self.attachment_pattern.search(filename or ""))

    # -- doc du lieu -----------------------------------------------------
    def extract(self, text: str) -> dict[str, str]:
        """Chay cac pattern cua profile. Chi tra truong tim duoc, khong bia."""
        out: dict[str, str] = {}
        for fld, rx in self.patterns.items():
            m = rx.search(text or "")
            if not m:
                continue
            # Uu tien nhom co ten (?P<v>...), khong co thi lay nhom dau tien
            value = (m.groupdict().get("v") or m.group(1) or "").strip()
            if value:
                out[fld] = value
        return out

    def aliases(self) -> dict[str, list[str]]:
        return dict(self.field_aliases)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "domains": self.domains, "senders": self.senders,
            "aliases": {k: list(v) for k, v in self.field_aliases.items()},
            "patterns": {k: v.pattern for k, v in self.patterns.items()},
            "subject_must": self.subject_must,
            "attachment": self.attachment_pattern.pattern if self.attachment_pattern else "",
            "contacts": [c.as_dict() for c in self.contacts],
            "note": self.note,
        }


def _one(cfg: dict[str, Any], *, is_contact: bool = False) -> ParserProfile:
    def lst(key: str) -> list[str]:
        v = cfg.get(key)
        if not v:
            return []
        return [str(x) for x in (v if isinstance(v, list) else [v])]

    patterns: dict[str, re.Pattern[str]] = {}
    for fld, pat in (cfg.get("patterns") or {}).items():
        if fld not in PATTERN_FIELDS:
            raise ProfileError(f"Khong ho tro pattern cho truong '{fld}'. "
                               f"Truong cho phep: {', '.join(PATTERN_FIELDS)}")
        patterns[fld] = _compile(fld, str(pat))

    att = cfg.get("attachment_pattern") or cfg.get("attachment")
    return ParserProfile(
        name=str(cfg.get("name") or cfg.get("sender") or ("contact" if is_contact else "")),
        domains=lst("domains"),
        senders=lst("senders") or lst("sender"),
        field_aliases={k: [str(x) for x in (v if isinstance(v, list) else [v])]
                       for k, v in (cfg.get("field_aliases") or {}).items()},
        patterns=patterns,
        subject_must=lst("subject_must"),
        attachment_pattern=_compile("attachment", str(att), require_group=False) if att else None,
        contacts=[_one(c, is_contact=True) for c in (cfg.get("contacts") or [])],
        note=str(cfg.get("note") or ""),
    )


def load(profiles_cfg: list[dict[str, Any]] | None) -> list[ParserProfile]:
    """Nap danh sach profile tu config/email_profiles.yaml > forwarders."""
    return [_one(p) for p in (profiles_cfg or [])]


@dataclass(slots=True)
class Resolved:
    """Ket qua nhan dien mot email den tu dau."""

    forwarder: str = ""
    profile: ParserProfile | None = None
    contact: ParserProfile | None = None

    @property
    def contact_name(self) -> str:
        return self.contact.name if self.contact else ""

    def aliases(self) -> dict[str, list[str]]:
        """Alias cua Forwarder, nhan vien khai rieng thi de len tren."""
        out: dict[str, list[str]] = {}
        if self.profile:
            out.update(self.profile.aliases())
        if self.contact:
            out.update(self.contact.aliases())
        return out

    def extract(self, text: str) -> dict[str, str]:
        """Pattern cua nhan vien thang pattern cua Forwarder (cu the hon = dang tin hon)."""
        out: dict[str, str] = {}
        if self.profile:
            out.update(self.profile.extract(text))
        if self.contact:
            out.update(self.contact.extract(text))
        return out


def resolve(profiles: list[ParserProfile], sender: str, subject: str = "") -> Resolved:
    for p in profiles:
        if not p.matches(sender):
            continue
        if not p.applies_to_subject(subject):
            continue
        return Resolved(forwarder=p.name, profile=p, contact=p.contact_for(sender))
    return Resolved()
