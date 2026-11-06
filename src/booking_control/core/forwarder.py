"""Buyer - Forwarder Rule (SRS v2 muc 9-10, test case 62).

    BUYER + MODE + DEST + PORT/WH (+ khoang EX-FACT DATE)  ->  FORWARDER

    1 forwarder khop   -> AUTO_ASSIGNED
    nhieu forwarder    -> NEED_CONFIRM   (khong tu chon dai)
    khong co           -> NOT_ASSIGNED
    Master != Outlook  -> CONFLICT       (khong tu sua Master)

Module thuan: khong doc DB, khong doc Excel.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .normalize import norm_key

AUTO_ASSIGNED = "AUTO_ASSIGNED"
NEED_CONFIRM = "NEED_CONFIRM"
NOT_ASSIGNED = "NOT_ASSIGNED"
MANUAL = "MANUAL"
CONFLICT = "CONFLICT"

_MODE_ALIASES = {"S": "SEA", "SEA": "SEA", "FCL": "SEA", "LCL": "SEA", "A": "AIR", "AIR": "AIR"}


def _mode(v: Any) -> str:
    k = norm_key(v).upper()
    return _MODE_ALIASES.get(k, k)


@dataclass(slots=True)
class ForwarderRule:
    forwarder: str
    buyer: list[str] = field(default_factory=list)
    mode: list[str] = field(default_factory=list)
    dest: list[str] = field(default_factory=list)
    port: list[str] = field(default_factory=list)
    exfty_from: dt.date | None = None
    exfty_to: dt.date | None = None
    # SRSv4 muc 14 + bang 1 cua muc 68: mot dong Buyer x Forwarder day du gom ca
    # dich vu, quy tac booking va bo doc email dung cho Forwarder do.
    service: str = ""          # vd FCL / LCL / AIR EXPRESS
    booking_rule: str = ""     # vd "gui truoc 12:00 T-14", "dat qua portal"
    parser: str = ""           # ten Parser Profile trong email_profiles.yaml
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ForwarderRule:
        def lst(x: Any) -> list[str]:
            if x in (None, ""):
                return []
            return [str(i) for i in (x if isinstance(x, list) else [x])]

        def date(x: Any) -> dt.date | None:
            if not x:
                return None
            return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))

        return cls(forwarder=str(d["forwarder"]), buyer=lst(d.get("buyer")), mode=lst(d.get("mode")),
                   dest=lst(d.get("dest")), port=lst(d.get("port")),
                   service=str(d.get("service") or ""), booking_rule=str(d.get("booking_rule") or ""),
                   parser=str(d.get("parser") or ""), note=str(d.get("note") or ""),
                   exfty_from=date(d.get("exfty_from")), exfty_to=date(d.get("exfty_to")))

    def matches(self, buyer: str, mode: str, dest: str, port: str, exfty: dt.date | None) -> bool:
        if self.buyer and norm_key(buyer) not in {norm_key(b) for b in self.buyer}:
            return False
        if self.mode and _mode(mode) not in {_mode(m) for m in self.mode}:
            return False
        if self.dest and norm_key(dest) not in {norm_key(d) for d in self.dest}:
            return False
        if self.port and norm_key(port) not in {norm_key(p) for p in self.port}:
            return False
        if self.exfty_from and (exfty is None or exfty < self.exfty_from):
            return False
        return not (self.exfty_to and (exfty is None or exfty > self.exfty_to))


@dataclass(slots=True)
class ForwarderMatch:
    status: str
    forwarder: str = ""
    candidates: list[str] = field(default_factory=list)
    reason: str = ""


def load_rules(raw: list[dict[str, Any]] | None) -> list[ForwarderRule]:
    return [ForwarderRule.from_dict(r) for r in (raw or []) if r.get("forwarder")]


def resolve_forwarder(
    rules: list[ForwarderRule], *, buyer: str, mode: str = "", dest: str = "", port: str = "",
    exfty: dt.date | None = None,
) -> ForwarderMatch:
    found: list[str] = []
    for r in rules:
        if r.matches(buyer, mode, dest, port, exfty) and r.forwarder not in found:
            found.append(r.forwarder)
    if len(found) == 1:
        return ForwarderMatch(AUTO_ASSIGNED, found[0], found, f"Rule {buyer}/{_mode(mode) or '*'} -> {found[0]}")
    if len(found) > 1:
        return ForwarderMatch(NEED_CONFIRM, "", found, f"{len(found)} forwarder cung khop: {', '.join(found)}")
    return ForwarderMatch(NOT_ASSIGNED, "", [], f"Khong co rule forwarder cho {buyer or '?'}")


def check_conflict(master: str, observed: str) -> str:
    """Forwarder theo Master Rule khac forwarder doc tu email -> CONFLICT."""
    if master and observed and norm_key(master) != norm_key(observed):
        return CONFLICT
    return ""
