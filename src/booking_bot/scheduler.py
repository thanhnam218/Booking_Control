"""Scheduler chay ngam (SRS muc 42-43).

    - Quet Follow Up moi N gio (mac dinh 3, cho phep 1-4).
    - Moc quet neo theo scan_anchor_hour: 09:00, 12:00, 15:00, 18:00...
    - Moi 15 phut kiem tra xem da vao khung gio thong bao chua de phat cac
      thong bao bi don lai ngoai gio.
    - Moi 30 phut thu lai Pending Queue (file bi lock).
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .db import session_scope
from .engine import BookingBotEngine, ScanResult
from .logging_setup import get_logger

log = get_logger("scheduler")


@dataclass
class BotState:
    running: bool = False
    paused: bool = False
    last_scan: dt.datetime | None = None
    next_scan: dt.datetime | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    scan_count: int = 0

    def tray_text(self) -> str:
        if not self.running:
            return "Bot: dang dung"
        if self.paused:
            return "Bot: tam dung"
        last = self.last_scan.strftime("%H:%M") if self.last_scan else "--:--"
        nxt = self.next_scan.strftime("%H:%M") if self.next_scan else "--:--"
        return f"Booking Bot\nLast Scan: {last}\nNext Scan: {nxt}"


class BotScheduler:
    def __init__(self, engine: BookingBotEngine, on_scan_done=None):
        self.engine = engine
        self.settings = engine.settings
        self.state = BotState()
        self.on_scan_done = on_scan_done
        self._sched = BackgroundScheduler(timezone=self.settings.app.timezone)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    def _anchor_start(self) -> dt.datetime:
        sch = self.settings.scheduler
        now = dt.datetime.now()
        anchor = now.replace(hour=sch.scan_anchor_hour, minute=0, second=0, microsecond=0)
        step = dt.timedelta(hours=sch.scan_interval_hours)
        while anchor <= now:
            anchor += step
        return anchor

    def start(self) -> None:
        sch = self.settings.scheduler
        self._sched.add_job(
            self.scan_now, IntervalTrigger(hours=sch.scan_interval_hours),
            id="scan", next_run_time=self._anchor_start(), max_instances=1, coalesce=True,
        )
        self._sched.add_job(self._flush_notifications, IntervalTrigger(minutes=15),
                            id="flush", max_instances=1, coalesce=True)
        self._sched.add_job(self._retry_pending, IntervalTrigger(minutes=30),
                            id="pending", max_instances=1, coalesce=True)
        self._sched.start()
        self.state.running = True
        self._refresh_next()
        log.info("Scheduler bat dau. Quet moi %s gio, moc dau tien %s",
                 sch.scan_interval_hours, self.state.next_scan)
        if sch.run_on_start:
            threading.Thread(target=self.scan_now, daemon=True).start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)
        self.state.running = False
        log.info("Scheduler da dung")

    def pause(self) -> None:
        self.state.paused = True
        self._sched.pause()
        log.info("Bot tam dung")

    def resume(self) -> None:
        self.state.paused = False
        self._sched.resume()
        self._refresh_next()
        log.info("Bot chay tiep")

    # ------------------------------------------------------------------

    def scan_now(self, dry_run: bool = False) -> ScanResult:
        if self.state.paused and not dry_run:
            log.info("Bot dang tam dung - bo qua lan quet nay")
            return ScanResult(dry_run=dry_run)
        with self._lock:
            log.info("--- Bat dau %s ---", "DRY RUN" if dry_run else "SCAN")
            try:
                result = self.engine.run_scan(dry_run=dry_run)
                self.state.last_error = "; ".join(result.errors)
            except Exception as exc:  # noqa: BLE001
                log.exception("Scan loi")
                self.state.last_error = str(exc)
                result = ScanResult(dry_run=dry_run)
                result.errors.append(str(exc))

            if not dry_run:
                self.state.last_scan = dt.datetime.now()
                self.state.scan_count += 1
            self.state.last_result = result.as_dict()
            self._refresh_next()

            if self.on_scan_done:
                try:
                    self.on_scan_done(result, self.state)
                except Exception:  # noqa: BLE001
                    log.warning("on_scan_done callback loi", exc_info=True)
            return result

    def _refresh_next(self) -> None:
        job = self._sched.get_job("scan") if self._sched.running else None
        self.state.next_scan = getattr(job, "next_run_time", None)

    def _flush_notifications(self) -> None:
        try:
            with session_scope() as s:
                n = self.engine.notifier.flush_queued(s)
                if n:
                    log.info("Phat lai %s thong bao bi don ngoai gio", n)
        except Exception:  # noqa: BLE001
            log.warning("Flush notification loi", exc_info=True)

    def _retry_pending(self) -> None:
        try:
            n = self.engine.retry_pending()
            if n:
                log.info("Pending Queue: ghi thanh cong %s lo", n)
        except Exception:  # noqa: BLE001
            log.warning("Retry pending loi", exc_info=True)

    # ------------------------------------------------------------------

    def reschedule(self, interval_hours: int) -> None:
        """Doi chu ky quet khi User chinh trong Dashboard (1-4 gio)."""
        interval_hours = max(1, min(4, int(interval_hours)))
        self.settings.scheduler.scan_interval_hours = interval_hours
        if self._sched.running:
            self._sched.reschedule_job("scan", trigger=IntervalTrigger(hours=interval_hours))
            self._refresh_next()
        log.info("Doi chu ky quet thanh %s gio", interval_hours)
