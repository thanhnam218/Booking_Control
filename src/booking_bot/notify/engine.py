"""Notification Engine (SRS muc 43, 49-51, DMS muc 28).

Nhiem vu:
  - Phan loai CRITICAL / WARNING / INFO -> chon kenh.
  - Chong spam: dedup theo dedup_key trong N gio.
  - Ton trong khung gio thong bao (mac dinh 08:00-16:00) va ngay lam viec.
    Ngoai khung gio: van luu vao Notification Center, phat lai khi den gio.
  - Gom nhom WARNING: 20 canh bao -> 1 thong bao tong hop.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.deadline import DeadlineEvent
from ..db.models import Notification, utcnow
from ..logging_setup import get_logger
from .channels import Channel, ConsoleChannel, NotificationPayload, PopupChannel, ToastChannel

log = get_logger("notify.engine")

WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_hhmm(text: str) -> dt.time:
    h, m = (text or "08:00").split(":")[:2]
    return dt.time(int(h), int(m))


@dataclass(slots=True)
class DispatchResult:
    created: int = 0
    sent: int = 0
    suppressed_dedup: int = 0
    queued_outside_window: int = 0
    grouped: int = 0
    details: list[str] = field(default_factory=list)


class NotificationEngine:
    def __init__(self, settings, channels: dict[str, Channel] | None = None, on_ack=None):
        self.settings = settings
        ncfg = settings.notification
        self.channels: dict[str, Channel] = channels or {}
        if not self.channels:
            if ncfg.channels.get("console", True):
                self.channels["console"] = ConsoleChannel()
            if ncfg.channels.get("toast", True):
                self.channels["toast"] = ToastChannel(app_id=settings.app.name)
            if ncfg.channels.get("popup", True):
                self.channels["popup"] = PopupChannel(on_ack=on_ack)
        self._popups_this_run = 0

    # -- khung gio ---------------------------------------------------------

    def in_window(self, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now()
        sch = self.settings.scheduler
        allowed = {WEEKDAY_MAP[d.lower()[:3]] for d in sch.active_weekdays if d.lower()[:3] in WEEKDAY_MAP}
        if allowed and now.weekday() not in allowed:
            return False
        start = parse_hhmm(sch.notification_window.start)
        end = parse_hhmm(sch.notification_window.end)
        return start <= now.time() <= end

    # -- dedup -------------------------------------------------------------

    def _is_duplicate(self, session: Session, dedup_key: str, hours: int) -> bool:
        if not dedup_key:
            return False
        cutoff = utcnow() - dt.timedelta(hours=hours)
        stmt = (
            select(Notification)
            .where(Notification.dedup_key == dedup_key)
            .where(Notification.created_at >= cutoff)
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none() is not None

    # -- API chinh ---------------------------------------------------------

    def dispatch_events(
        self,
        session: Session,
        events: list[DeadlineEvent],
        *,
        source_lookup: dict[str, dict[str, Any]] | None = None,
        now: dt.datetime | None = None,
        deliver: bool = True,
    ) -> DispatchResult:
        now = now or dt.datetime.now()
        result = DispatchResult()
        dedup_hours = self.settings.deadlines.dedup_hours
        self._popups_this_run = 0
        source_lookup = source_lookup or {}

        fresh: list[tuple[DeadlineEvent, Notification]] = []
        for ev in events:
            if self._is_duplicate(session, ev.dedup_key, dedup_hours):
                result.suppressed_dedup += 1
                continue
            src = source_lookup.get(ev.payload.get("shipment_code", ""), {})
            notif = Notification(
                category=ev.category,
                severity=ev.severity,
                title=ev.title,
                body=ev.body,
                shipment_code=ev.payload.get("shipment_code", ""),
                context={**ev.payload, **src},
                dedup_key=ev.dedup_key,
            )
            session.add(notif)
            fresh.append((ev, notif))
            result.created += 1
        session.flush()

        if not deliver:
            return result

        if not self.in_window(now):
            if self.settings.scheduler.queue_outside_window:
                result.queued_outside_window = len(fresh)
                log.info("Ngoai khung gio thong bao - don %s thong bao lai", len(fresh))
                return result

        criticals = [(e, n) for e, n in fresh if e.severity == "CRITICAL"]
        warnings = [(e, n) for e, n in fresh if e.severity == "WARNING"]
        infos = [(e, n) for e, n in fresh if e.severity == "INFO"]

        for ev, notif in criticals:
            if self._deliver(notif, ev):
                result.sent += 1

        if self.settings.notification.group_warnings and len(warnings) > 3:
            titles = [e.title for e, _ in warnings][:12]
            more = len(warnings) - len(titles)
            payload = NotificationPayload(
                title=f"{len(warnings)} lo hang can chu y",
                body="\n".join(f"- {t}" for t in titles) + (f"\n... va {more} lo khac" if more else ""),
                severity="WARNING",
                category="GROUPED",
            )
            self._send_to(["toast", "console"], payload)
            result.grouped = len(warnings)
            result.sent += 1
            for _, notif in warnings:
                notif.delivered_at = utcnow()
        else:
            for ev, notif in warnings:
                if self._deliver(notif, ev):
                    result.sent += 1

        for ev, notif in infos:
            notif.delivered_at = utcnow()
            result.details.append(ev.title)

        session.flush()
        return result

    # -- noi bo ------------------------------------------------------------

    def _deliver(self, notif: Notification, ev: DeadlineEvent) -> bool:
        ctx = notif.context or {}
        payload = NotificationPayload(
            title=notif.title,
            body=notif.body,
            severity=notif.severity,
            category=notif.category,
            context={
                "jo": ctx.get("jo", ""),
                "buyer": ctx.get("buyer", ""),
                "exfty": ctx.get("exfty", ""),
                "days_left": ctx.get("days_left", ""),
                "status": ctx.get("status", ""),
                "hold_reason": ctx.get("hold_reason", ""),
            },
            source_file=ctx.get("source_file", ""),
            source_sheet=ctx.get("source_sheet", ""),
            source_row=ctx.get("source_row"),
            shipment_code=notif.shipment_code,
        )
        targets = list(self.settings.notification.severity_to_channel.get(notif.severity, ["console"]))
        if "console" not in targets and self.settings.notification.channels.get("console", True):
            targets.append("console")

        if "popup" in targets:
            if self._popups_this_run >= self.settings.notification.max_popups_per_scan:
                targets = [t for t in targets if t != "popup"]
            else:
                self._popups_this_run += 1

        ok = self._send_to(targets, payload)
        if ok:
            notif.delivered_at = utcnow()
        return ok

    def _send_to(self, targets: list[str], payload: NotificationPayload) -> bool:
        sent = False
        for name in targets:
            ch = self.channels.get(name)
            if ch is None:
                continue
            try:
                sent = ch.send(payload) or sent
            except Exception as exc:  # noqa: BLE001
                log.warning("Kenh %s loi: %s", name, exc)
        if not sent and "console" in self.channels:
            sent = self.channels["console"].send(payload)
        return sent

    # -- phat lai thong bao bi don ----------------------------------------

    def flush_queued(self, session: Session, now: dt.datetime | None = None) -> int:
        """Goi khi den khung gio: phat cac thong bao chua delivered."""
        now = now or dt.datetime.now()
        if not self.in_window(now):
            return 0
        cutoff = utcnow() - dt.timedelta(days=2)
        stmt = (
            select(Notification)
            .where(Notification.delivered_at.is_(None))
            .where(Notification.created_at >= cutoff)
            .order_by(Notification.severity.desc(), Notification.created_at)
            .limit(30)
        )
        pending = list(session.execute(stmt).scalars())
        count = 0
        self._popups_this_run = 0
        for notif in pending:
            ev = DeadlineEvent(
                category=notif.category, severity=notif.severity, title=notif.title,
                body=notif.body, days_left=int((notif.context or {}).get("days_left", 0) or 0),
                payload=notif.context or {}, dedup_key=notif.dedup_key,
            )
            if self._deliver(notif, ev):
                count += 1
        session.flush()
        return count
