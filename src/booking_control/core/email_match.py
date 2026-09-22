"""Ghep email vao lo hang bang diem tin cay (SRSv5 muc 20-22, 27, BR-003, BR-007).

Khong can biet dinh dang JO/GO/PO cua cong ty: lay cac "tu" trong email roi so voi
ma CO THAT trong database (JO, GO, PO, INVOICE, Booking No). Conversation / Reply
cua email da ghep truoc do la dau hieu manh nhat.

    >= auto (85)      AUTO_MATCHED   (tu ap dung neu email va PDF khong mau thuan)
    review..auto      REVIEW         (nguoi dung chon)
    < review (60)     UNMATCHED
    2 lo sat diem     REVIEW         (khong tu chon - BR-007)
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from .normalize import norm_key

_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9\-/._]{2,40}")


def tokenize(text: str) -> set[str]:
    out: set[str] = set()
    for t in _TOKEN.findall((text or "").upper()):
        t = t.strip("-/._")
        if len(t) >= 4 and any(c.isdigit() for c in t):
            out.add(norm_key(t))
            for part in re.split(r"[/_.]", t):  # "JO123/JO124" -> ca 2
                if len(part) >= 4 and any(c.isdigit() for c in part):
                    out.add(norm_key(part))
    return out


@dataclass(slots=True)
class ShipmentRef:
    code: str
    jo: str = ""
    go: str = ""
    po: str = ""
    invoices: tuple[str, ...] = ()
    booking_nos: tuple[str, ...] = ()
    buyer: str = ""
    etd: dt.date | None = None
    vessel: str = ""
    destination: str = ""
    forwarder: str = ""


@dataclass
class Candidate:
    code: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    strong: bool = False  # co it nhat 1 dau hieu dinh danh (conversation / reply / booking / ma)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "score": self.score, "reasons": self.reasons}


@dataclass
class MatchDecision:
    status: str  # AUTO_MATCHED | REVIEW | UNMATCHED
    codes: list[str]
    score: int
    candidates: list[Candidate]
    reason: str = ""


DEFAULT_WEIGHTS = {"conversation": 70, "reply_to": 35, "booking_no": 70, "jo": 45, "invoice": 35, "go": 25,
                   "po": 20, "buyer": 5, "etd": 10, "vessel": 5, "destination": 5, "forwarder": 10}


def score_candidates(
    refs: list[ShipmentRef], *, tokens: set[str], text_norm: str, fields: dict[str, Any],
    conversation_codes: set[str], reply_codes: set[str], weights: dict[str, int] | None = None,
) -> list[Candidate]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    booking_no = norm_key(fields.get("booking_no"))
    etd = fields.get("etd")
    vessel = norm_key(fields.get("vessel"))
    forwarder = norm_key(fields.get("_forwarder"))
    out: list[Candidate] = []
    for r in refs:
        c = Candidate(r.code)

        def add(key: str, why: str, strong: bool = True, c: Candidate = c) -> None:
            c.score += int(w.get(key, 0))
            c.reasons.append(f"{why} (+{w.get(key, 0)})")
            c.strong = c.strong or strong

        if r.code in conversation_codes:
            add("conversation", "Cung Conversation")
        if r.code in reply_codes:
            add("reply_to", "Tra loi email da ghep")
        bks = {norm_key(b) for b in r.booking_nos if b}
        if bks and (booking_no in bks or bks & tokens):
            add("booking_no", "Booking No")
        if r.jo and norm_key(r.jo) in tokens:
            add("jo", f"JO {r.jo}")
        if r.go and norm_key(r.go) in tokens:
            add("go", f"GO {r.go}")
        if any(norm_key(i) in tokens for i in r.invoices if i):
            add("invoice", "INVOICE/NO")
        if r.po and len(norm_key(r.po)) >= 5 and norm_key(r.po) in tokens:
            add("po", f"PO {r.po}")
        if not c.strong:
            continue  # chi dung Buyer/ETD/Vessel/Dest de cong them, khong de tim lo
        if r.buyer and len(norm_key(r.buyer)) >= 3 and norm_key(r.buyer) in text_norm:
            add("buyer", "Buyer", strong=False)
        if etd and r.etd and str(r.etd) == str(etd):
            add("etd", "ETD", strong=False)
        if vessel and r.vessel and (vessel in norm_key(r.vessel) or norm_key(r.vessel) in vessel):
            add("vessel", "Vessel", strong=False)
        if r.destination and len(norm_key(r.destination)) >= 3 and norm_key(r.destination) in text_norm:
            add("destination", "Destination", strong=False)
        if forwarder and r.forwarder and norm_key(r.forwarder) == forwarder:
            add("forwarder", "Email tu dung Forwarder cua lo", strong=False)
        out.append(c)
    out.sort(key=lambda x: (-x.score, x.code))
    return out


def decide(cands: list[Candidate], *, auto: int = 85, review: int = 60, margin: int = 10) -> MatchDecision:
    if not cands or cands[0].score < review:
        top = cands[0].score if cands else 0
        return MatchDecision("UNMATCHED", [], top, cands, "Khong du bang chung de ghep")
    top = cands[0]
    if top.score < auto:
        return MatchDecision("REVIEW", [top.code], top.score, cands, "Diem chua du de tu ghep")
    high = [c for c in cands if c.score >= auto]
    if len(high) > 1:
        # Booking gop nhieu lo: cung Conversation / cung Booking No -> ghep tat ca
        shared = all(any(r.startswith(("Cung Conversation", "Booking No")) for r in c.reasons) for c in high)
        if shared:
            return MatchDecision("AUTO_MATCHED", [c.code for c in high], top.score, cands,
                                 f"Booking chung {len(high)} lo")
        return MatchDecision("REVIEW", [c.code for c in high], top.score, cands,
                             f"{len(high)} lo cung du diem - can nguoi chon")
    second = cands[1].score if len(cands) > 1 else 0
    if second and top.score - second < margin and second >= review:
        return MatchDecision("REVIEW", [top.code], top.score, cands, "2 lo sat diem nhau")
    return MatchDecision("AUTO_MATCHED", [top.code], top.score, cands, "Du diem tin cay")
