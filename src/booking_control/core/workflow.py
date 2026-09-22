"""Mau quy trinh B1-B15.1 + Document Matrix (SRSv5 muc 30-31, SRSv4 muc 57, SRSv3 Document Matrix).

Module thuan: doc cau hinh config/workflow.yaml, tinh han, trang thai hien thi, chung tu bat buoc.
Workflow KHONG tuyen tinh cung: task chi BLOCKED khi task phu thuoc chua xong.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .forwarder import _mode
from .normalize import norm_key

DONE = {"COMPLETED", "SKIPPED", "CANCELLED"}


@dataclass(slots=True)
class StepDef:
    code: str
    name: str
    seq: int
    anchor: str = "EXFTY"
    offset: int = 0
    depends_on: list[str] = field(default_factory=list)
    required_docs: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    only_if: str = ""
    auto_done: list[str] = field(default_factory=list)  # dieu kien tu hoan thanh (xem AUTO_DONE)


def load_steps(cfg: dict[str, Any]) -> list[StepDef]:
    out = []
    for i, s in enumerate(cfg.get("steps") or []):
        out.append(StepDef(
            code=str(s["code"]), name=str(s.get("name", s["code"])), seq=i + 1,
            anchor=str(s.get("anchor", "EXFTY")).upper(), offset=int(s.get("offset", 0)),
            depends_on=[str(x) for x in s.get("depends_on") or []],
            required_docs=[str(x) for x in s.get("required_docs") or []],
            modes=[str(x).upper() for x in s.get("modes") or []], only_if=str(s.get("only_if", "") or ""),
            auto_done=[str(x) for x in ([s["auto_done"]] if isinstance(s.get("auto_done"), str)
                                        else s.get("auto_done") or [])],
        ))
    codes = {s.code for s in out}
    for s in out:
        bad = [d for d in s.depends_on if d not in codes]
        if bad:
            raise ValueError(f"workflow.yaml: {s.code} phu thuoc buoc khong ton tai {bad}")
        unknown = [a for a in s.auto_done if a not in AUTO_DONE]
        if unknown:
            raise ValueError(f"workflow.yaml: {s.code} auto_done khong hop le {unknown} (chon: {', '.join(AUTO_DONE)})")
    return out


# Task tu hoan thanh khi he thong da thay bang chung (khong can bam tay) - "chi dung cong nghe tu dong".
# ctx: booking_status, ship_status, effective_status, docs (set chung tu da co)
AUTO_DONE: dict[str, str] = {
    "shipment_created": "Lo da co trong Follow Up",
    "booking_requested": "Da co booking (da gui yeu cau tro len)",
    "booking_confirmed": "Booking da xac nhan / da book",
    "departed": "Tau da chay / da xuat du",
    "documents": "Du chung tu bat buoc cua buoc",
}
_BOOKED = {"BOOKED", "CONFIRMED", "AMENDED", "ON_HOLD", "DELAYED", "PARTIALLY_SHIPPED", "DEPARTED", "COMPLETED"}


def auto_done(step: StepDef, ctx: dict[str, Any]) -> bool:
    """Tat ca dieu kien auto_done cua buoc deu dung -> tu hoan thanh."""
    if not step.auto_done:
        return False
    bs, docs = ctx.get("booking_status") or "NOT_BOOKED", ctx.get("docs") or set()
    checks = {
        "shipment_created": True,
        "booking_requested": bs not in ("NOT_BOOKED", "CANCELLED", ""),
        "booking_confirmed": bs in _BOOKED,
        "departed": ctx.get("ship_status") == "FULLY_SHIPPED" or bs in ("DEPARTED", "COMPLETED")
        or ctx.get("effective_status") == "COMPLETED",
        "documents": bool(step.required_docs) and all(d in docs for d in step.required_docs),
    }
    return all(checks[a] for a in step.auto_done)


def applicable(step: StepDef, *, mode: str, co_required: bool) -> bool:
    if step.modes and "ALL" not in step.modes and _mode(mode or "SEA") not in {_mode(m) for m in step.modes}:
        return False
    return not (step.only_if == "co_required" and not co_required)


def anchor_date(anchor: str, anchors: dict[str, dt.date | None]) -> dt.date | None:
    """ETD chua co -> tam tinh theo EX-FACT (han se tu cap nhat khi co ETD)."""
    return anchors.get(anchor) or anchors.get("EXFTY")


def due_date(anchor: str, offset: int, anchors: dict[str, dt.date | None]) -> dt.date | None:
    base = anchor_date(anchor, anchors)
    return base + dt.timedelta(days=offset) if base else None


def display_status(status: str, *, deps_done: bool, due: dt.date | None, today: dt.date) -> str:
    if status in DONE:
        return status
    if not deps_done:
        return "BLOCKED"
    if due and due < today:
        return "OVERDUE"
    return status


def required_documents(rules: list[dict[str, Any]], *, mode: str, buyer: str, forwarder: str) -> list[str]:
    """Document Matrix: gop tat ca rule khop (Mode / Buyer / Forwarder), giu thu tu, khong trung."""
    out: list[str] = []
    for r in rules or []:
        if r.get("mode") and _mode(mode or "SEA") != _mode(r["mode"]):
            continue
        if r.get("buyer") and norm_key(buyer) not in {norm_key(b) for b in r["buyer"]}:
            continue
        if r.get("forwarder") and norm_key(forwarder) not in {norm_key(f) for f in r["forwarder"]}:
            continue
        for d in r.get("required") or []:
            if d not in out:
                out.append(str(d))
    return out


def document_due(doc: str, deadlines: dict[str, Any], anchors: dict[str, dt.date | None]) -> dt.date | None:
    spec = (deadlines or {}).get(doc)
    if not spec:
        return anchors.get("EXFTY")
    return due_date(str(spec.get("anchor", "EXFTY")).upper(), int(spec.get("offset", 0)), anchors)
