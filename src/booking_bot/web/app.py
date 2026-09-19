"""Web Dashboard - BOOKING CONTROL CENTER (SRS muc 55, 58-60).

Chay local tai http://127.0.0.1:8765 - khong mo ra internet.
Man hinh:
    /                Booking Control Center (dem theo trang thai + loc)
    /shipments       Danh sach lo hang
    /shipments/{id}  Chi tiet + "Why Yellow?" + override + container
    /exceptions      Exception Center
    /notifications   Notification Center
    /audit           Audit Log
    /mapping         Xac nhan sheet / anh xa cot
    /buyers          Buyer Rule
    /settings        Chu ky quet, khung gio, deadline
    /backups         Backup & Rollback
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..audit import log_audit, open_exceptions, resolve_exception
from ..core import status as st
from ..core.deadline import compute_milestones, days_until
from ..db import session_scope
from ..db.models import (
    AuditLog,
    ColumnMapping,
    ContainerAllocation,
    ExceptionRecord,
    Notification,
    ScanRun,
    SheetMap,
    Shipment,
    SourceFile,
    UserOverride,
    utcnow,
)
from ..excel.backup import list_backups, rollback
from ..logging_setup import get_logger
from ..notify.channels import open_file

log = get_logger("web")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

CTX: dict[str, Any] = {"engine": None, "scheduler": None}

STATUS_META = {
    "WHITE": ("Chua book", "#9e9e9e"),
    "YELLOW": ("Da book, thieu CDF/BL", "#f9a825"),
    "GREEN": ("CDF + BL du", "#43a047"),
    "RED": ("Dang hold", "#e53935"),
    "COMPLETED": ("Da xuat xong", "#039be5"),
    "PARTIAL": ("Xuat mot phan", "#fb8c00"),
    "CANCELLED": ("Da huy", "#757575"),
}


def create_app(engine=None, scheduler=None) -> FastAPI:
    CTX["engine"] = engine
    CTX["scheduler"] = scheduler
    settings = engine.settings if engine else None
    app = FastAPI(title="Booking Deadline Bot", docs_url="/api/docs")

    def render(request: Request, template: str, **kw) -> HTMLResponse:
        base = {
            "request": request,
            "app_name": settings.app.name if settings else "Booking Bot",
            "status_meta": STATUS_META,
            "nav": template.replace(".html", ""),
            "now": dt.datetime.now(),
            "state": scheduler.state if scheduler else None,
        }
        # Starlette >= 0.29 dung chu ky (request, name, context)
        return TEMPLATES.TemplateResponse(request, template, {**base, **kw})

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        today = dt.date.today()
        with session_scope() as s:
            counts = dict(
                s.execute(
                    select(Shipment.effective_status, func.count())
                    .where(Shipment.is_active.is_(True))
                    .group_by(Shipment.effective_status)
                ).all()
            )
            total = sum(counts.values())
            upcoming = list(
                s.execute(
                    select(Shipment)
                    .where(Shipment.is_active.is_(True), Shipment.exfty_date.is_not(None))
                    .where(Shipment.exfty_date >= today)
                    .order_by(Shipment.exfty_date)
                    .limit(15)
                ).scalars()
            )
            late = list(
                s.execute(
                    select(Shipment)
                    .where(Shipment.is_active.is_(True), Shipment.effective_status.in_(["WHITE", "RED"]))
                    .where(Shipment.exfty_date.is_not(None))
                    .order_by(Shipment.exfty_date)
                    .limit(15)
                ).scalars()
            )
            exc_count = s.execute(
                select(func.count()).select_from(ExceptionRecord).where(ExceptionRecord.status == "OPEN")
            ).scalar_one()
            last_run = s.execute(select(ScanRun).order_by(ScanRun.id.desc()).limit(1)).scalar_one_or_none()
            rows_up = [_row(sh, today) for sh in upcoming]
            rows_late = [_row(sh, today) for sh in late]
            last = _scan_run_dict(last_run)

        return render(
            request, "dashboard.html", counts=counts, total=total, upcoming=rows_up,
            late=rows_late, exc_count=exc_count, last_run=last,
        )

    # ------------------------------------------------------------------
    # Shipments
    # ------------------------------------------------------------------

    @app.get("/shipments", response_class=HTMLResponse)
    def shipments(
        request: Request, status: str = "", buyer: str = "", pic: str = "",
        site: str = "", q: str = "", days: int = 0,
    ):
        today = dt.date.today()
        with session_scope() as s:
            stmt = select(Shipment).where(Shipment.is_active.is_(True))
            if status:
                stmt = stmt.where(Shipment.effective_status == status)
            if buyer:
                stmt = stmt.where(Shipment.buyer_code == buyer)
            if pic:
                stmt = stmt.where(Shipment.pic == pic)
            if site:
                stmt = stmt.where(Shipment.site == site)
            if q:
                like = f"%{q}%"
                stmt = stmt.where(Shipment.jo.like(like) | Shipment.shipment_no.like(like))
            if days:
                stmt = stmt.where(
                    Shipment.exfty_date.is_not(None),
                    Shipment.exfty_date <= today + dt.timedelta(days=days),
                    Shipment.exfty_date >= today,
                )
            stmt = stmt.order_by(Shipment.exfty_date.is_(None), Shipment.exfty_date).limit(500)
            rows = [_row(sh, today) for sh in s.execute(stmt).scalars()]
            buyers = [b for (b,) in s.execute(
                select(Shipment.buyer_code).distinct().order_by(Shipment.buyer_code)
            ).all() if b]
            pics = [p for (p,) in s.execute(
                select(Shipment.pic).distinct().order_by(Shipment.pic)
            ).all() if p]

        return render(
            request, "shipments.html", rows=rows, buyers=buyers, pics=pics,
            f={"status": status, "buyer": buyer, "pic": pic, "site": site, "q": q, "days": days},
            statuses=st.ALL_STATUSES,
        )

    @app.get("/shipments/{code}", response_class=HTMLResponse)
    def shipment_detail(request: Request, code: str):
        today = dt.date.today()
        with session_scope() as s:
            sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one_or_none()
            if sh is None:
                return HTMLResponse("Khong tim thay shipment", status_code=404)
            alloc = s.execute(
                select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
            ).scalar_one_or_none()
            overrides = list(s.execute(
                select(UserOverride).where(UserOverride.shipment_code == code, UserOverride.active.is_(True))
            ).scalars())
            audits = list(s.execute(
                select(AuditLog).where(AuditLog.entity_id == code).order_by(AuditLog.id.desc()).limit(40)
            ).scalars())
            notes = list(s.execute(
                select(Notification).where(Notification.shipment_code == code)
                .order_by(Notification.id.desc()).limit(20)
            ).scalars())

            ms = compute_milestones(sh.exfty_date, CTX["engine"].settings.deadlines) if sh.exfty_date else None
            data = _row(sh, today)
            data.update({
                "status_reason": sh.status_reason or {},
                "invoices": [i.invoice_no for i in sh.invoices],
                "declarations": [d.declaration_no for d in sh.declarations],
                "source": f"{sh.source_file_code} / {sh.source_sheet} / dong {sh.source_row}",
                "data_confidence": sh.data_confidence,
                "group_key": sh.group_key,
                "remark": sh.remark,
                "hold_reason": sh.hold_reason,
            })
            alloc_d = _alloc_dict(alloc)
            ovs = [{"field": o.field, "value": o.user_value, "user": o.user_name,
                    "note": o.note, "at": o.created_at} for o in overrides]
            aud = [{"ts": a.ts, "actor": f"{a.actor_type}:{a.actor_name}", "action": a.action,
                    "field": a.field, "old": a.old_value, "new": a.new_value, "note": a.note}
                   for a in audits]
            nts = [{"ts": n.created_at, "cat": n.category, "sev": n.severity, "title": n.title,
                    "body": n.body} for n in notes]

        return render(
            request, "shipment_detail.html", sh=data, alloc=alloc_d, overrides=ovs,
            audits=aud, notifications=nts, milestones=ms.as_dict() if ms else None,
            statuses=st.ALL_STATUSES,
        )

    @app.post("/shipments/{code}/override")
    def set_override(
        code: str, field: str = Form(...), value: str = Form(""),
        user: str = Form("user"), note: str = Form(""),
    ):
        with session_scope() as s:
            old = s.execute(
                select(UserOverride).where(
                    UserOverride.shipment_code == code, UserOverride.field == field,
                    UserOverride.active.is_(True),
                )
            ).scalar_one_or_none()
            if old is not None:
                old.active = False
            if value:
                s.add(UserOverride(shipment_code=code, field=field, user_value=value,
                                   user_name=user, note=note))
            sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one_or_none()
            if sh is not None and field == "status":
                sh.effective_status = value or sh.bot_status
            log_audit(s, "USER_OVERRIDE", actor_type="USER", actor_name=user, entity="shipment",
                      entity_id=code, field=field,
                      old_value=old.user_value if old else "", new_value=value or "(bo override)",
                      note=note or "USER > BOT")
        return RedirectResponse(f"/shipments/{code}", status_code=303)

    @app.post("/shipments/{code}/open-source")
    def open_source(code: str):
        with session_scope() as s:
            sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one_or_none()
            if sh is None:
                return JSONResponse({"ok": False}, status_code=404)
            fcfg = CTX["engine"].settings.file_by_code(sh.source_file_code)
            path = str(CTX["engine"].settings.resolve_path(fcfg.path)) if fcfg else ""
            ok = open_file(path, sh.source_sheet, sh.source_row)
        return JSONResponse({"ok": ok, "path": path})

    # ------------------------------------------------------------------
    # Exception Center / Notifications / Audit
    # ------------------------------------------------------------------

    @app.get("/exceptions", response_class=HTMLResponse)
    def exceptions_page(request: Request, show: str = "OPEN"):
        with session_scope() as s:
            if show == "ALL":
                rows = list(s.execute(
                    select(ExceptionRecord).order_by(ExceptionRecord.id.desc()).limit(400)
                ).scalars())
            else:
                rows = open_exceptions(s)
            data = [{
                "id": r.id, "code": r.code, "severity": r.severity, "reason": r.reason,
                "file": Path(r.source_file).name if r.source_file else "",
                "sheet": r.source_sheet, "row": r.source_row, "col": r.source_column,
                "value": r.value, "action": r.suggested_action, "status": r.status,
                "created": r.created_at, "shipment": r.shipment_code,
            } for r in rows]
        return render(request, "exceptions.html", rows=data, show=show)

    @app.post("/exceptions/{exc_id}/resolve")
    def resolve_exc(exc_id: int, user: str = Form("user"), note: str = Form(""),
                    status: str = Form("RESOLVED")):
        with session_scope() as s:
            resolve_exception(s, exc_id, user, note, status)
        return RedirectResponse("/exceptions", status_code=303)

    @app.get("/notifications", response_class=HTMLResponse)
    def notifications_page(request: Request):
        with session_scope() as s:
            rows = list(s.execute(
                select(Notification).order_by(Notification.id.desc()).limit(200)
            ).scalars())
            data = [{
                "id": n.id, "ts": n.created_at, "cat": n.category, "sev": n.severity,
                "title": n.title, "body": n.body, "shipment": n.shipment_code,
                "delivered": n.delivered_at, "ack": n.acknowledged_at,
            } for n in rows]
        return render(request, "notifications.html", rows=data)

    @app.post("/notifications/{nid}/ack")
    def ack_notification(nid: int):
        with session_scope() as s:
            n = s.get(Notification, nid)
            if n is not None:
                n.acknowledged_at = utcnow()
                n.read_at = n.read_at or utcnow()
        return RedirectResponse("/notifications", status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request, q: str = ""):
        with session_scope() as s:
            stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(400)
            if q:
                like = f"%{q}%"
                stmt = select(AuditLog).where(
                    AuditLog.entity_id.like(like) | AuditLog.action.like(like)
                ).order_by(AuditLog.id.desc()).limit(400)
            rows = [{
                "ts": a.ts, "actor": f"{a.actor_type}:{a.actor_name}", "action": a.action,
                "entity": a.entity, "entity_id": a.entity_id, "field": a.field,
                "old": a.old_value, "new": a.new_value, "note": a.note, "source": a.source,
            } for a in s.execute(stmt).scalars()]
        return render(request, "audit.html", rows=rows, q=q)

    # ------------------------------------------------------------------
    # Mapping / Buyers / Settings / Backups
    # ------------------------------------------------------------------

    @app.get("/mapping", response_class=HTMLResponse)
    def mapping_page(request: Request):
        with session_scope() as s:
            files = list(s.execute(select(SourceFile).order_by(SourceFile.code)).scalars())
            sheets = []
            for f in files:
                for sm in s.execute(
                    select(SheetMap).where(SheetMap.file_id == f.id).order_by(SheetMap.sheet_name)
                ).scalars():
                    sheets.append({
                        "id": sm.id, "file": f.code, "file_name": f.display_name,
                        "sheet": sm.sheet_name, "is_fu": sm.is_follow_up,
                        "conf": sm.confidence, "confirmed": sm.confirmed_by_user,
                        "note": sm.detection_note, "header_row": sm.header_row,
                    })
            cols = [{
                "id": c.id, "file": c.file_code, "sheet": c.sheet_name,
                "header": c.source_header, "field": c.canonical_field,
                "conf": c.confidence, "confirmed": c.confirmed_by_user,
            } for c in s.execute(select(ColumnMapping).order_by(ColumnMapping.file_code)).scalars()]
        fields = sorted(CTX["engine"].dictionary.fields) if CTX["engine"] else []
        return render(request, "mapping.html", sheets=sheets, cols=cols, fields=fields)

    @app.post("/mapping/sheet/{sheet_id}")
    def confirm_sheet(sheet_id: int, is_follow_up: str = Form("no"), user: str = Form("user")):
        with session_scope() as s:
            sm = s.get(SheetMap, sheet_id)
            if sm is not None:
                sm.is_follow_up = is_follow_up == "yes"
                sm.confirmed_by_user = True
                log_audit(s, "CONFIRM_SHEET", actor_type="USER", actor_name=user,
                          entity="sheet", entity_id=str(sheet_id),
                          new_value="FollowUp" if sm.is_follow_up else "Ignore", field=sm.sheet_name)
        return RedirectResponse("/mapping", status_code=303)

    @app.post("/mapping/column")
    def add_column_mapping(
        file_code: str = Form(...), sheet: str = Form("*"), header: str = Form(...),
        field: str = Form(...), user: str = Form("user"),
    ):
        with session_scope() as s:
            existing = s.execute(
                select(ColumnMapping).where(
                    ColumnMapping.file_code == file_code, ColumnMapping.sheet_name == sheet,
                    ColumnMapping.source_header == header,
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(ColumnMapping(file_code=file_code, sheet_name=sheet, source_header=header,
                                    canonical_field=field, confidence=1.0, confirmed_by_user=True))
            else:
                existing.canonical_field, existing.confirmed_by_user = field, True
            log_audit(s, "MAP_COLUMN", actor_type="USER", actor_name=user, entity="column",
                      entity_id=f"{file_code}/{sheet}", field=header, new_value=field)
        return RedirectResponse("/mapping", status_code=303)

    @app.get("/buyers", response_class=HTMLResponse)
    def buyers_page(request: Request):
        rules = CTX["engine"].buyers.to_dicts() if CTX["engine"] else []
        return render(request, "buyers.html", rules=rules)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        s_ = CTX["engine"].settings
        return render(request, "settings.html", cfg=s_, backups_files=[f.id for f in s_.files])

    @app.post("/settings")
    def save_settings(
        scan_interval_hours: int = Form(3), notify_start: str = Form("08:00"),
        notify_end: str = Form("16:00"), hold_reminder_days: int = Form(3),
        booking_start: int = Form(20), booking_end: int = Form(14),
        write_color: str = Form("yes"), dry_run_default: str = Form("no"),
        user: str = Form("user"),
    ):
        s_ = CTX["engine"].settings
        s_.scheduler.scan_interval_hours = max(1, min(4, scan_interval_hours))
        s_.scheduler.notification_window.start = notify_start
        s_.scheduler.notification_window.end = notify_end
        s_.deadlines.hold_reminder_days = max(1, min(7, hold_reminder_days))
        s_.deadlines.booking_window.start_days_before = booking_start
        s_.deadlines.booking_window.end_days_before = booking_end
        s_.status.write_color_to_excel = write_color == "yes"
        s_.excel_write.dry_run_default = dry_run_default == "yes"
        if CTX["scheduler"]:
            CTX["scheduler"].reschedule(s_.scheduler.scan_interval_hours)
        with session_scope() as sess:
            log_audit(sess, "UPDATE_SETTINGS", actor_type="USER", actor_name=user,
                      entity="settings",
                      new_value=f"scan={s_.scheduler.scan_interval_hours}h "
                                f"window={notify_start}-{notify_end} hold={hold_reminder_days}d")
        return RedirectResponse("/settings", status_code=303)

    @app.get("/backups", response_class=HTMLResponse)
    def backups_page(request: Request, file_code: str = ""):
        s_ = CTX["engine"].settings
        codes = [f.id for f in s_.files]
        code = file_code or (codes[0] if codes else "")
        rows = list_backups(s_.backup_dir, code) if code else []
        return render(request, "backups.html", codes=codes, code=code, rows=rows)

    @app.post("/backups/rollback")
    def do_rollback(file_code: str = Form(...), backup_path: str = Form(...), user: str = Form("user")):
        s_ = CTX["engine"].settings
        fcfg = s_.file_by_code(file_code)
        if fcfg is None:
            return JSONResponse({"ok": False, "error": "file_code khong ton tai"}, status_code=400)
        target = s_.resolve_path(fcfg.path)
        rollback(Path(backup_path), target, s_.backup_dir, file_code)
        with session_scope() as sess:
            log_audit(sess, "ROLLBACK", actor_type="USER", actor_name=user, entity="file",
                      entity_id=file_code, new_value=Path(backup_path).name, source=str(target))
        return RedirectResponse(f"/backups?file_code={file_code}", status_code=303)

    # ------------------------------------------------------------------
    # Hanh dong + API
    # ------------------------------------------------------------------

    @app.post("/actions/scan")
    def action_scan():
        sched = CTX["scheduler"]
        if sched:
            result = sched.scan_now(dry_run=False)
        else:
            result = CTX["engine"].run_scan(dry_run=False)
        return JSONResponse(result.as_dict())

    @app.post("/actions/dryrun")
    def action_dryrun():
        sched = CTX["scheduler"]
        result = sched.scan_now(dry_run=True) if sched else CTX["engine"].run_scan(dry_run=True)
        return JSONResponse({"text": result.render_text(), **result.as_dict()})

    @app.get("/dryrun", response_class=HTMLResponse)
    def dryrun_page(request: Request):
        result = CTX["engine"].run_scan(dry_run=True, deliver_notifications=False)
        return render(request, "dryrun.html", report=result.render_text(), data=result.as_dict())

    @app.post("/actions/pause")
    def action_pause():
        if CTX["scheduler"]:
            CTX["scheduler"].pause()
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/resume")
    def action_resume():
        if CTX["scheduler"]:
            CTX["scheduler"].resume()
        return RedirectResponse("/", status_code=303)

    @app.get("/api/status")
    def api_status():
        sched = CTX["scheduler"]
        state = sched.state if sched else None
        with session_scope() as s:
            counts = dict(
                s.execute(
                    select(Shipment.effective_status, func.count())
                    .where(Shipment.is_active.is_(True)).group_by(Shipment.effective_status)
                ).all()
            )
            open_exc = s.execute(
                select(func.count()).select_from(ExceptionRecord).where(ExceptionRecord.status == "OPEN")
            ).scalar_one()
        return {
            "running": bool(state and state.running),
            "paused": bool(state and state.paused),
            "last_scan": state.last_scan.isoformat() if state and state.last_scan else None,
            "next_scan": state.next_scan.isoformat() if state and state.next_scan else None,
            "scan_count": state.scan_count if state else 0,
            "counts": counts,
            "open_exceptions": open_exc,
        }

    return app


# ---------------------------------------------------------------------------


def _row(sh: Shipment, today: dt.date) -> dict[str, Any]:
    d = days_until(sh.exfty_date, today) if sh.exfty_date else None
    return {
        "code": sh.shipment_code,
        "no": sh.shipment_no,
        "jo": sh.jo,
        "buyer": sh.buyer_code or sh.buyer_raw,
        "exfty": sh.exfty_date,
        "days_left": d,
        "status": sh.effective_status,
        "bot_status": sh.bot_status,
        "cbm": sh.final_cbm,
        "pcs": sh.final_export_qty,
        "mode": sh.ship_mode,
        "booking_mode": sh.booking_mode,
        "cdf": sh.cdf,
        "bl": sh.bl,
        "pic": sh.pic,
        "site": sh.site,
        "co": "Yes" if sh.co_required else "No",
        "co_form": sh.co_form,
        "overridden": sh.effective_status != sh.bot_status,
    }


def _alloc_dict(alloc: ContainerAllocation | None) -> dict[str, Any] | None:
    if alloc is None:
        return None
    return {
        "total_cbm": alloc.total_cbm, "qty_40hc": alloc.qty_40hc, "qty_40dc": alloc.qty_40dc,
        "qty_20st": alloc.qty_20st, "capacity": alloc.estimated_capacity,
        "excess": alloc.excess_capacity, "type": alloc.container_type,
        "mode": alloc.booking_mode, "status": alloc.allocation_status,
        "source": alloc.allocation_source, "note": alloc.note,
    }


def _scan_run_dict(run: ScanRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "started": run.started_at, "finished": run.finished_at, "mode": run.mode,
        "files": run.files_scanned, "sheets": run.sheets_scanned,
        "shipments": run.shipments_found, "new": run.shipments_new,
        "changed": run.shipments_changed, "notifications": run.notifications,
        "exceptions": run.exceptions, "ok": run.ok, "error": run.error,
    }
