"""Weekly Planning: tuan duoc len ke hoach, han chot 12:00 thu 6, cac moc nhac (SRSv5 muc 36).

Module thuan. Quy uoc:
    - Tu thu 5 (include_next_week_from) Weekly Planning ghi luon cac lo cua TUAN SAU.
    - Tuan duoc chot vao thu 6 = tuan sau (thu 2 -> chu nhat).
    - Nhac: thu 5 08:00 (INFO), thu 6 08:00 (WARNING), thu 6 11:00 (CRITICAL); qua 12:00 -> tre han.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "t2": 0, "t3": 1, "t4": 2, "t5": 3, "t6": 4, "t7": 5, "cn": 6}


def parse_at(text: str) -> tuple[int, dt.time]:
    """'fri 12:00' -> (4, 12:00)."""
    parts = (text or "").strip().lower().split()
    if not parts or parts[0] not in DAYS:
        raise ValueError(f"Moc thoi gian khong hop le: {text!r} (vd 'fri 12:00')")
    hh, mm = (parts[1] if len(parts) > 1 else "00:00").split(":")
    return DAYS[parts[0]], dt.time(int(hh), int(mm))


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def planning_week(today: dt.date) -> tuple[dt.date, dt.date]:
    """Tuan can chot trong tuan nay = tuan sau (thu 2..chu nhat)."""
    start = monday(today) + dt.timedelta(days=7)
    return start, start + dt.timedelta(days=6)


def week_label(start: dt.date) -> str:
    y, w, _ = start.isocalendar()
    return f"{y}-W{w:02d}"


def deadline_at(today: dt.date, cfg) -> dt.datetime:
    day, t = parse_at(cfg.deadline)
    return dt.datetime.combine(monday(today) + dt.timedelta(days=day), t)


def horizon(today: dt.date, cfg) -> dt.date | None:
    """Tu thu 5 tro di: Weekly Planning lay toi het tuan sau. Truoc do: None (theo moc T7 nhu cu)."""
    if not cfg.enabled:
        return None
    start_day = DAYS.get((cfg.include_next_week_from or "thu").lower(), 3)
    if today.weekday() >= start_day:
        return planning_week(today)[1]
    return None


@dataclass(slots=True)
class Reminder:
    stage: int
    at: dt.datetime
    level: str


def reminders(today: dt.date, cfg) -> list[Reminder]:
    out = []
    base = monday(today)
    for i, r in enumerate(cfg.reminders or []):
        day, t = parse_at(r.get("at", ""))
        out.append(Reminder(i, dt.datetime.combine(base + dt.timedelta(days=day), t), str(r.get("level", "INFO"))))
    return out


def due_reminder(now: dt.datetime, cfg) -> Reminder | None:
    """Moc nhac moi nhat da toi (va chua qua han chot) - moi moc chi nhac 1 lan (dedup o service)."""
    passed = [r for r in reminders(now.date(), cfg) if r.at <= now]
    if not passed or now >= deadline_at(now.date(), cfg):
        return None
    return passed[-1]


def missing_items(row: dict[str, Any]) -> list[str]:
    """Lo hang trong Weekly Planning con thieu gi (hien thi + dem)."""
    miss = []
    if not row.get("assigned_to"):
        miss.append("Chưa phân công")
    if row.get("booking_status") in ("NOT_BOOKED", "CANCELLED", "", None):
        miss.append("Chưa có booking" if row.get("booking_status") != "CANCELLED" else "Booking đã hủy")
    if not row.get("forwarder"):
        miss.append("Chưa có forwarder")
    if not (row.get("qty") or 0):
        miss.append("Thiếu số lượng")
    if row.get("mode") != "AIR" and not row.get("container"):
        miss.append("Chưa có kế hoạch container")
    return miss
