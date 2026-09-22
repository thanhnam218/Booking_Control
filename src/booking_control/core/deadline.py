"""Deadline Engine (SRS muc 44-48, DMS muc 27-29).

    T-20 .. T-14   Booking reminder (cua so, khong phai 1 ngay co dinh)
    T-7            Day du lieu sang Weekly Planning + Custom Code
    T-3            Kiem tra Inv/FinalQty/Carton/GW/CBM/Price/DiscountPrice
    T-1            Cap nhat Lich xuat hang TDV/TGV
    Hold           Nhac lai moi N ngay (mac dinh 3, chinh 1-7)

Khi ExFtyDate thay doi -> TAT CA deadline phai duoc tinh lai (SRS muc 48).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

# Cac truong phai day du o moc T-3 (SRS muc 46-47)
T3_FIELDS = [
    ("Invoice", "Inv"),
    ("FinalExportQty", "FinalQty"),
    ("FinalCartonQty", "Carton"),
    ("GrossWeight", "GW"),
    ("FinalCBM", "CBM"),
    ("Price", "Price"),
    ("DiscountPrice", "Discount Price"),
]


@dataclass(slots=True)
class DeadlineEvent:
    category: str  # BOOKING_DUE | BOOKING_OVERDUE | HOLD_REMINDER | T7 | T3 | T1 | OVERDUE
    severity: str  # CRITICAL | WARNING | INFO
    title: str
    body: str
    days_left: int
    due_date: dt.date | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    dedup_key: str = ""


@dataclass(slots=True)
class Milestones:
    exfty: dt.date
    booking_start: dt.date
    booking_end: dt.date
    t7: dt.date
    t3: dt.date
    t1: dt.date

    def as_dict(self) -> dict[str, str]:
        return {
            "ExFtyDate": self.exfty.isoformat(),
            "BookingWindowStart": self.booking_start.isoformat(),
            "BookingWindowEnd": self.booking_end.isoformat(),
            "T-7": self.t7.isoformat(),
            "T-3": self.t3.isoformat(),
            "T-1": self.t1.isoformat(),
        }


def compute_milestones(exfty: dt.date, cfg) -> Milestones:
    bw = cfg.booking_window
    ms = cfg.milestones
    return Milestones(
        exfty=exfty,
        booking_start=exfty - dt.timedelta(days=bw.start_days_before),
        booking_end=exfty - dt.timedelta(days=bw.end_days_before),
        t7=exfty - dt.timedelta(days=ms.weekly_planning_days_before),
        t3=exfty - dt.timedelta(days=ms.final_info_days_before),
        t1=exfty - dt.timedelta(days=ms.shipping_schedule_days_before),
    )


def days_until(exfty: dt.date, today: dt.date | None = None) -> int:
    return (exfty - (today or dt.date.today())).days


# ---------------------------------------------------------------------------
# Kiem tra du lieu moc T-3 (SRS muc 47)
# ---------------------------------------------------------------------------


def check_t3_fields(values: dict[str, Any], states: dict[str, str]) -> dict[str, str]:
    """Moi truong -> FOUND | MISSING | INVALID (khong chi co/khong)."""
    out: dict[str, str] = {}
    for canonical, label in T3_FIELDS:
        state = states.get(canonical, "")
        value = values.get(canonical)
        if state in ("INVALID", "NEED_REVIEW"):
            out[label] = "INVALID"
        elif value is None or (isinstance(value, str) and not value.strip()):
            out[label] = "MISSING"
        elif isinstance(value, (int, float)) and canonical != "DiscountPrice" and value <= 0:
            out[label] = "INVALID"
        else:
            out[label] = "FOUND"
    return out


def t3_summary(check: dict[str, str]) -> str:
    missing = [k for k, v in check.items() if v == "MISSING"]
    invalid = [k for k, v in check.items() if v == "INVALID"]
    parts = []
    if missing:
        parts.append("thieu " + ", ".join(missing))
    if invalid:
        parts.append(", ".join(invalid) + " khong hop le")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Sinh su kien deadline cho 1 shipment
# ---------------------------------------------------------------------------


def evaluate_shipment(
    *,
    shipment_code: str,
    jo: str,
    buyer: str,
    exfty: dt.date | None,
    status: str,
    cfg,
    today: dt.date | None = None,
    hold_since: dt.date | None = None,
    last_hold_notice: dt.date | None = None,
    t3_check: dict[str, str] | None = None,
    hold_reason: str = "",
) -> list[DeadlineEvent]:
    today = today or dt.date.today()
    events: list[DeadlineEvent] = []

    if exfty is None:
        events.append(
            DeadlineEvent(
                category="INVALID_DATE",
                severity="CRITICAL",
                title=f"{jo or shipment_code}: thieu / sai ExFtyDate",
                body="Khong tinh duoc deadline vi ExFtyDate khong hop le.",
                days_left=9999,
                dedup_key=f"INVALID_DATE:{shipment_code}",
            )
        )
        return events

    ms = compute_milestones(exfty, cfg)
    d = days_until(exfty, today)
    head = f"{jo or shipment_code} / {buyer}"
    ctx = {"shipment_code": shipment_code, "jo": jo, "buyer": buyer,
           "exfty": exfty.isoformat(), "days_left": d, "milestones": ms.as_dict()}

    # --- Qua han -----------------------------------------------------------
    if d < 0:
        if status not in ("COMPLETED", "CANCELLED") and abs(d) <= cfg.overdue_grace_days:
            events.append(
                DeadlineEvent(
                    "OVERDUE", "CRITICAL",
                    f"{head}: da qua ngay xuat {abs(d)} ngay",
                    f"ExFtyDate {exfty:%d/%m/%Y} da qua nhung trang thai van la {status}. "
                    "Kiem tra da xuat xong chua hoac cap nhat lai ngay xuat.",
                    d, exfty, ctx, f"OVERDUE:{shipment_code}:{today.isoformat()}",
                )
            )
        return events

    # --- Hold reminder (SRS muc 45) ---------------------------------------
    if status == "RED":
        due = True
        if last_hold_notice is not None:
            due = (today - last_hold_notice).days >= cfg.hold_reminder_days
        if due:
            since = f" (hold tu {hold_since:%d/%m/%Y})" if hold_since else ""
            events.append(
                DeadlineEvent(
                    "HOLD_REMINDER", "CRITICAL",
                    f"{head}: dang HOLD - can kiem tra lai de booking",
                    f"Ly do: {hold_reason or 'chua ghi'}{since}. Con {d} ngay den ExFtyDate "
                    f"{exfty:%d/%m/%Y}. Nhac lai moi {cfg.hold_reminder_days} ngay.",
                    d, exfty, ctx, f"HOLD:{shipment_code}:{today.isoformat()}",
                )
            )

    # --- Booking window T-20..T-14 (SRS muc 44) ---------------------------
    bw = cfg.booking_window
    if status == "WHITE":
        if bw.end_days_before <= d <= bw.start_days_before:
            events.append(
                DeadlineEvent(
                    "BOOKING_DUE", "WARNING",
                    f"{head}: chua booking - con {d} ngay",
                    f"Dang trong cua so booking T-{bw.start_days_before}..T-{bw.end_days_before}. "
                    f"ExFtyDate {exfty:%d/%m/%Y}. Han chot dat booking: {ms.booking_end:%d/%m/%Y}.",
                    d, ms.booking_end, ctx, f"BOOKING_DUE:{shipment_code}",
                )
            )
        elif d < bw.end_days_before:
            events.append(
                DeadlineEvent(
                    "BOOKING_OVERDUE", "CRITICAL",
                    f"{head}: TRE BOOKING - chi con {d} ngay",
                    f"Da qua han booking T-{bw.end_days_before} ma van chua book. "
                    f"ExFtyDate {exfty:%d/%m/%Y}.",
                    d, ms.booking_end, ctx, f"BOOKING_OVERDUE:{shipment_code}",
                )
            )

    # --- T-7: Weekly Planning + Custom Code -------------------------------
    if d <= cfg.milestones.weekly_planning_days_before and status not in ("CANCELLED",):
        events.append(
            DeadlineEvent(
                "T7", "INFO",
                f"{head}: den moc T-7",
                f"Day du lieu sang WEEKLY PLANNING va CUSTOM CODE (ExFtyDate {exfty:%d/%m/%Y}).",
                d, ms.t7, ctx, f"T7:{shipment_code}",
            )
        )

    # --- T-3: kiem tra thong tin cuoi -------------------------------------
    if d <= cfg.milestones.final_info_days_before and status not in ("CANCELLED", "COMPLETED"):
        if t3_check:
            problems = t3_summary(t3_check)
            if problems:
                events.append(
                    DeadlineEvent(
                        "T3", "CRITICAL",
                        f"{head}: T-3 con thieu thong tin",
                        f"{problems}. ExFtyDate {exfty:%d/%m/%Y}.",
                        d, ms.t3, {**ctx, "t3_check": t3_check}, f"T3:{shipment_code}",
                    )
                )

    # --- T-1: lich xuat TDV/TGV -------------------------------------------
    if d <= cfg.milestones.shipping_schedule_days_before and status not in ("CANCELLED",):
        events.append(
            DeadlineEvent(
                "T1", "INFO",
                f"{head}: den moc T-1",
                f"Cap nhat Lich xuat hang (TDV/TGV) cho ngay {exfty:%d/%m/%Y}.",
                d, ms.t1, ctx, f"T1:{shipment_code}",
            )
        )

    return events


def due_milestones(exfty: dt.date | None, cfg, today: dt.date | None = None) -> set[str]:
    """Tra ve cac moc DA DEN han cho shipment nay: {'T7','T3','T1'}."""
    if exfty is None:
        return set()
    d = days_until(exfty, today)
    out: set[str] = set()
    if d <= cfg.milestones.weekly_planning_days_before:
        out.add("T7")
    if d <= cfg.milestones.final_info_days_before:
        out.add("T3")
    if d <= cfg.milestones.shipping_schedule_days_before:
        out.add("T1")
    return out
