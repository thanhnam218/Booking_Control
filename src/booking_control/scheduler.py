"""Automation Engine - cac tac vu nen chay trong may chu (SRS v2 muc 54-56, 80-81).

Khong co "Bot". Thay vao do la cac job co ten ro rang, moi lan chay deu ghi JobRun
(Job ID, Start, End, Status, Error, Retry Count) de xem tren trang Automation:

    followup_scan          Quet Follow Up -> trang thai -> deadline -> Planning/HS Code/MM/Lich xuat
    notification_dispatch  Phat cac thong bao bi don ngoai khung gio 08:00-16:00
    excel_write_retry      Ghi lai cac file dich bi khoa (Pending Queue)
    outlook_scan           Doc email Outlook desktop (Inbox + Sent) -> ghep lo -> cap nhat booking (neu bat)
    weekly_planning_check  Nhac Weekly Planning tuan sau (thu 5, thu 6 08:00 / 11:00), bao tre sau 12:00 thu 6
    housekeeping           Don phien dang nhap het han, lich su job cu

Lich quet Follow Up neo theo scan_anchor_hour: 09:00, 12:00, 15:00...
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select

from .db import session_scope
from .db.models import JobRun, WebSession, utcnow
from .engine import AutomationEngine, ScanResult
from .logging_setup import get_logger

log = get_logger("automation")

JOB_TITLES: dict[str, str] = {
    "followup_scan": "Quet Follow Up + trang thai + deadline + ghi Planning",
    "notification_dispatch": "Phat thong bao bi don ngoai khung gio",
    "excel_write_retry": "Ghi lai file dich dang bi khoa (Pending Queue)",
    "outlook_scan": "Doc email Outlook -> ghep lo -> cap nhat booking",
    "weekly_planning_check": "Nhac chot Weekly Planning (han 12:00 thu 6)",
    "housekeeping": "Don phien dang nhap het han + lich su cu",
}


@dataclass
class AutomationState:
    running: bool = False
    paused: bool = False
    last_scan: dt.datetime | None = None
    next_scan: dt.datetime | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    scan_count: int = 0


class AutomationScheduler:
    def __init__(self, engine: AutomationEngine, on_scan_done=None, sleep=time.sleep):
        self.engine = engine
        self.settings = engine.settings
        self.state = AutomationState()
        self.on_scan_done = on_scan_done
        from zoneinfo import ZoneInfo

        # Truyen timezone ro rang cho moi trigger - khong phu thuoc tzlocal cua may
        self._tz = ZoneInfo(self.settings.app.timezone)
        self._sched = BackgroundScheduler(timezone=self._tz)
        self._lock = threading.Lock()
        self._sleep = sleep

    # ------------------------------------------------------------------

    def _anchor_start(self) -> dt.datetime:
        sch = self.settings.scheduler
        now = dt.datetime.now(self._tz)
        anchor = now.replace(hour=sch.scan_anchor_hour, minute=0, second=0, microsecond=0)
        step = dt.timedelta(hours=sch.scan_interval_hours)
        while anchor <= now:
            anchor += step
        return anchor

    def start(self) -> None:
        sch = self.settings.scheduler
        opts = {"max_instances": 1, "coalesce": True}
        self._sched.add_job(self.scan_now, IntervalTrigger(hours=sch.scan_interval_hours, timezone=self._tz),
                            id="followup_scan", next_run_time=self._anchor_start(), **opts)
        self._sched.add_job(lambda: self.run_job("notification_dispatch", self._flush_notifications),
                            IntervalTrigger(minutes=15, timezone=self._tz), id="notification_dispatch", **opts)
        self._sched.add_job(lambda: self.run_job("excel_write_retry", self._retry_pending),
                            IntervalTrigger(minutes=30, timezone=self._tz), id="excel_write_retry", **opts)
        self._sched.add_job(lambda: self.run_job("housekeeping", self._housekeeping),
                            IntervalTrigger(hours=6, timezone=self._tz), id="housekeeping", **opts)
        if self.settings.weekly_planning.enabled:
            # moi 15 phut: kip moc 11:00 / 12:00 thu 6 (moi moc chi nhac 1 lan)
            self._sched.add_job(lambda: self.run_job("weekly_planning_check", self._weekly_check),
                                IntervalTrigger(minutes=15, timezone=self._tz), id="weekly_planning_check", **opts)
        if self.settings.outlook.enabled:
            self._sched.add_job(lambda: self.run_job("outlook_scan", self._outlook_scan),
                                IntervalTrigger(minutes=self.settings.jobs.outlook_interval_minutes, timezone=self._tz),
                                id="outlook_scan", **opts)
        self._sched.start()
        self.state.running = True
        self._refresh_next()
        log.info("Automation bat dau. Quet Follow Up moi %s gio, moc dau tien %s",
                 sch.scan_interval_hours, self.state.next_scan)
        if sch.run_on_start:
            threading.Thread(target=self.scan_now, daemon=True).start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)
        self.state.running = False
        log.info("Automation da dung")

    def pause(self) -> None:
        self.state.paused = True
        if self._sched.running:
            self._sched.pause()
        log.info("Automation tam dung")

    def resume(self) -> None:
        self.state.paused = False
        if self._sched.running:
            self._sched.resume()
        self._refresh_next()
        log.info("Automation chay tiep")

    def jobs(self) -> list[dict[str, Any]]:
        out = []
        for name, title in JOB_TITLES.items():
            job = self._sched.get_job(name) if self._sched.running else None
            if name == "outlook_scan" and not self.settings.outlook.enabled:
                continue
            if name == "weekly_planning_check" and not self.settings.weekly_planning.enabled:
                continue
            out.append({"name": name, "title": title,
                        "next_run": getattr(job, "next_run_time", None) if job else None})
        return out

    # ------------------------------------------------------------------
    # Chay 1 job + ghi JobRun + thu lai
    # ------------------------------------------------------------------

    def run_job(self, name: str, fn: Callable[[], Any], *, triggered_by: str = "scheduler",
                retries: int | None = None) -> JobRun | None:
        retries = self.settings.jobs.max_retries if retries is None else retries
        with session_scope() as s:
            run = JobRun(job_name=name, triggered_by=triggered_by, status="RUNNING")
            s.add(run)
            s.flush()
            run_id = run.id

        attempt, error, summary, status = 0, "", {}, "SUCCESS"
        while True:
            try:
                out = fn()
                summary = out if isinstance(out, dict) else ({"count": out} if out is not None else {})
                status, error = "SUCCESS", ""
                break
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                log.warning("Job %s loi (lan %s): %s", name, attempt + 1, error)
                if attempt >= retries:
                    status = "FAILED"
                    break
                attempt += 1
                self._sleep(self.settings.jobs.retry_backoff_seconds * attempt)

        with session_scope() as s:
            run = s.get(JobRun, run_id)
            run.finished_at = utcnow()
            run.status = status
            run.error = error
            run.retry_count = attempt
            run.summary = _jsonable(summary)
            s.flush()
            s.expunge(run)
        return run

    # ------------------------------------------------------------------

    def scan_now(self, dry_run: bool = False, triggered_by: str = "scheduler") -> ScanResult:
        if self.state.paused and not dry_run:
            log.info("Automation dang tam dung - bo qua lan quet nay")
            return ScanResult(dry_run=dry_run)
        holder: dict[str, ScanResult] = {}

        def work() -> dict[str, Any]:
            result = self.engine.run_scan(dry_run=dry_run)
            holder["r"] = result
            if result.errors:
                # Loi trong luc quet (vd file hong) da duoc ghi ScanRun; khong thu lai vo ich
                self.state.last_error = "; ".join(result.errors)
            return {k: v for k, v in result.as_dict().items() if k not in ("writes",)}

        with self._lock:
            log.info("--- Bat dau %s ---", "DRY RUN" if dry_run else "QUET FOLLOW UP")
            if dry_run:
                try:
                    result = self.engine.run_scan(dry_run=True)
                except Exception as exc:  # noqa: BLE001
                    result = ScanResult(dry_run=True)
                    result.errors.append(str(exc))
                return result
            run = self.run_job("followup_scan", work, triggered_by=triggered_by, retries=0)
            result = holder.get("r") or ScanResult()
            if run is not None and run.status == "FAILED":
                result.errors.append(run.error)
                self.state.last_error = run.error
            elif not result.errors:
                self.state.last_error = ""
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
        job = self._sched.get_job("followup_scan") if self._sched.running else None
        self.state.next_scan = getattr(job, "next_run_time", None)

    def _flush_notifications(self) -> int:
        with session_scope() as s:
            return self.engine.notifier.flush_queued(s)

    def _retry_pending(self) -> int:
        return self.engine.retry_pending()

    def _outlook_scan(self) -> dict[str, Any]:
        from .integrations.outlook import scan_outlook

        return scan_outlook(self.settings, self.engine) or {}

    def _weekly_check(self) -> dict[str, Any]:
        from .services.weekly_service import check_reminders

        with session_scope() as s:
            return check_reminders(s, self.settings)

    def _housekeeping(self) -> dict[str, Any]:
        cutoff = utcnow() - dt.timedelta(days=self.settings.jobs.keep_days)
        with session_scope() as s:
            n_sess = s.execute(delete(WebSession).where(WebSession.expires_at < utcnow())).rowcount
            old = [r.id for r in s.execute(select(JobRun).where(JobRun.started_at < cutoff)).scalars()]
            if old:
                s.execute(delete(JobRun).where(JobRun.id.in_(old)))
        return {"sessions_removed": n_sess or 0, "jobs_removed": len(old)}

    # ------------------------------------------------------------------

    def reschedule(self, interval_hours: int) -> None:
        """Doi chu ky quet (1-4 gio) tu trang Cai dat."""
        interval_hours = max(1, min(4, int(interval_hours)))
        self.settings.scheduler.scan_interval_hours = interval_hours
        if self._sched.running:
            self._sched.reschedule_job("followup_scan", trigger=IntervalTrigger(hours=interval_hours, timezone=self._tz))
            self._refresh_next()
        log.info("Doi chu ky quet thanh %s gio", interval_hours)


def _jsonable(d: Any) -> Any:
    if isinstance(d, dict):
        return {str(k): _jsonable(v) for k, v in d.items()}
    if isinstance(d, (list, tuple)):
        return [_jsonable(x) for x in d]
    if isinstance(d, (dt.date, dt.datetime)):
        return d.isoformat()
    if isinstance(d, (str, int, float, bool)) or d is None:
        return d
    return str(d)
