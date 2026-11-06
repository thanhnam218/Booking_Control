"""Quan tri: nguoi dung, phan cong, Automation (job), mapping, buyer, cai dat, backup."""

from __future__ import annotations

import datetime as dt
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

from ..audit import log_audit
from ..db import session_scope
from ..db.models import (
    AssignmentHistory,
    ColumnMapping,
    JobRun,
    SheetMap,
    Shipment,
    SourceFile,
    User,
)
from ..excel.backup import list_backups, rollback
from ..services import shipment_service as svc
from ..services.assignment import workload
from .auth import ROLES, AuthError, destroy_user_sessions, set_password, upsert_user
from .deps import CTX, engine, render, require, settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Nguoi dung (Admin)
# ---------------------------------------------------------------------------


def _users_ctx(s) -> list[dict]:
    counts = dict(s.execute(
        select(Shipment.assigned_to, func.count()).where(Shipment.archived.is_(False),
                                                         Shipment.is_active.is_(True))
        .group_by(Shipment.assigned_to)).all())
    return [{"id": u.id, "name": u.name, "display_name": u.display_name, "email": u.email, "role": u.role,
             "team": u.team, "active": u.active, "has_pw": bool(u.password_hash),
             "must_change": u.must_change_password, "last_login": u.last_login_at,
             "shipments": counts.get(u.name, 0)}
            for u in s.execute(select(User).order_by(User.active.desc(), User.role, User.name)).scalars()]


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, msg: str = ""):
    require(request, "users.manage")
    with session_scope() as s:
        rows = _users_ctx(s)
    return render(request, "users.html", rows=rows, roles=ROLES, msg=msg, error="", temp="", temp_user="")


def _temp_password() -> str:
    return "Bc-" + secrets.token_urlsafe(6).replace("-", "x").replace("_", "y") + "9"


@router.post("/admin/users")
def create_user(request: Request, name: str = Form(...), display_name: str = Form(""), email: str = Form(""),
                role: str = Form("user"), team: str = Form(""), password: str = Form("")):
    p = require(request, "users.manage")
    temp = password or _temp_password()
    try:
        with session_scope() as s:
            if s.execute(select(User).where(User.name == name.strip())).scalar_one_or_none():
                raise AuthError(f"Đã có tài khoản {name}")
            u = upsert_user(s, name, role=role, password=temp, display_name=display_name, email=email,
                            team=team, min_length=settings().auth.min_password_length, must_change=True)
            log_audit(s, "USER_CREATED", actor_type="USER", actor_name=p.name, entity="user",
                      entity_id=u.name, new_value=role)
    except AuthError as exc:
        with session_scope() as s:
            rows = _users_ctx(s)
        return render(request, "users.html", status_code=400, rows=rows, roles=ROLES, msg="", error=str(exc),
                      temp="", temp_user="")
    # Hien mat khau tam 1 lan tren trang (khong dua vao URL / log)
    with session_scope() as s:
        rows = _users_ctx(s)
    return render(request, "users.html", rows=rows, roles=ROLES, msg="Đã tạo tài khoản", error="",
                  temp=temp, temp_user=name.strip())


@router.post("/admin/users/{uid}")
def update_user(request: Request, uid: int, role: str = Form(...), display_name: str = Form(""),
                email: str = Form(""), team: str = Form(""), active: str = Form("")):
    p = require(request, "users.manage")
    with session_scope() as s:
        u = s.get(User, uid)
        if u is None:
            return RedirectResponse("/admin/users", status_code=303)
        if u.id == p.id and (role != "admin" or not active):
            return RedirectResponse("/admin/users?msg=Không tự hạ quyền / khóa chính mình", status_code=303)
        old = f"{u.role}/{'on' if u.active else 'off'}"
        u.role = role if role in ROLES else u.role
        u.display_name, u.email, u.team = display_name.strip(), email.strip(), team.strip()
        u.active = bool(active)
        if not u.active:
            destroy_user_sessions(s, u.id)
        log_audit(s, "USER_UPDATED", actor_type="USER", actor_name=p.name, entity="user", entity_id=u.name,
                  old_value=old, new_value=f"{u.role}/{'on' if u.active else 'off'}")
    return RedirectResponse("/admin/users?msg=Đã lưu", status_code=303)


@router.post("/admin/users/{uid}/reset-password")
def reset_password(request: Request, uid: int):
    p = require(request, "users.manage")
    temp = _temp_password()
    with session_scope() as s:
        u = s.get(User, uid)
        if u is None:
            return RedirectResponse("/admin/users", status_code=303)
        set_password(u, temp, settings().auth.min_password_length, must_change=True)
        destroy_user_sessions(s, u.id)
        log_audit(s, "PASSWORD_RESET", actor_type="USER", actor_name=p.name, entity="user", entity_id=u.name)
        name = u.name
    with session_scope() as s:
        rows = _users_ctx(s)
    return render(request, "users.html", rows=rows, roles=ROLES, msg="Đã đặt mật khẩu tạm", error="",
                  temp=temp, temp_user=name)


# ---------------------------------------------------------------------------
# Phan cong (Quan ly)
# ---------------------------------------------------------------------------


@router.get("/assignments", response_class=HTMLResponse)
def assignments_page(request: Request):
    require(request, "shipments.assign")
    today = dt.date.today()
    with session_scope() as s:
        team = workload(s, today)
        opts = {
            "buyers": sorted({b for (b,) in s.execute(
                select(Shipment.buyer_code).where(Shipment.is_active.is_(True)).distinct()).all() if b}),
            "forwarders": sorted({f for (f,) in s.execute(select(Shipment.forwarder).distinct()).all() if f}),
            "modes": sorted({m for (m,) in s.execute(select(Shipment.ship_mode).distinct()).all() if m}),
            "statuses": sorted({x for (x,) in s.execute(select(Shipment.effective_status).distinct()).all() if x}),
        }
        users = [(u["user"], u["label"]) for u in team if u["user"]]
        history = [{"ts": h.ts, "code": h.shipment_code, "kind": h.kind, "from": h.from_user,
                    "to": h.to_user, "by": h.by_user, "method": h.method}
                   for h in s.execute(select(AssignmentHistory).order_by(AssignmentHistory.id.desc())
                                      .limit(30)).scalars()]
    rules = settings().assignment.rules
    return render(request, "assignments.html", team=team, opts=opts, users=users,
                  history=history, rules=rules)


def _assign_filters(q) -> dict[str, Any]:
    """Bo loc chung cho ca xem truoc va giao that - mot nguon duy nhat."""
    keys = ("assigned", "status", "biz", "warn", "buyer", "forwarder", "mode",
            "date_from", "date_to", "q")
    f = {k: q.get(k, "") for k in keys if q.get(k)}
    if str(q.get("hide_done", "")) in ("1", "true", "yes"):
        f["hide_done"] = True
    return f


@router.get("/api/assign/preview")
def assign_preview(request: Request):
    """Bo loc nay khop bao nhieu lo, cua ai, vai vi du - xem TRUOC khi giao hang tram lo."""
    p = require(request, "shipments.assign")
    f = _assign_filters(request.query_params)
    with session_scope() as s:
        stmt = svc.apply_filters(svc.visible_stmt(p), f)
        total = s.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
        ships = list(s.execute(svc.order_like_followup(stmt).limit(12)).scalars())
        by_owner: dict[str, int] = {}
        for (owner,) in s.execute(
                stmt.order_by(None).with_only_columns(Shipment.assigned_to)).all():
            by_owner[owner or ""] = by_owner.get(owner or "", 0) + 1
        labels = {u.name: u.label for u in s.execute(select(User)).scalars()}
        sample = [{"code": sh.shipment_code, "jo": sh.jo, "buyer": sh.buyer_code or sh.buyer_raw,
                   "exfty": sh.exfty_date.strftime("%d/%m/%Y") if sh.exfty_date else "",
                   "mode": sh.ship_mode, "status": sh.effective_status,
                   "owner": labels.get(sh.assigned_to, sh.assigned_to) or "(chưa phân công)"}
                  for sh in ships]
    current = sorted(({"user": k, "label": labels.get(k, k) or "(chưa phân công)", "n": v}
                      for k, v in by_owner.items()), key=lambda x: -x["n"])
    return {"total": total, "sample": sample, "current": current}


@router.get("/api/assign/board")
def assign_board(request: Request):
    """Bang keo-tha: moi the la MOT NHOM lo (buyer + tuan EX-FACT), keo 1 lan giao ca nhom.

    Voi 2000+ lo/thang thi keo tung dong la khong the - nen the o day la nhom, con so tren
    the la so lo se duoc giao khi tha.
    """
    p = require(request, "shipments.assign")
    today = dt.date.today()
    only = request.query_params.get("scope", "unassigned")
    with session_scope() as s:
        stmt = svc.visible_stmt(p).where(
            Shipment.effective_status.notin_(("COMPLETED", "CANCELLED")))
        if only == "unassigned":
            stmt = stmt.where(Shipment.assigned_to == "")
        rows = s.execute(stmt.with_only_columns(
            Shipment.buyer_code, Shipment.buyer_raw, Shipment.exfty_date,
            Shipment.assigned_to, Shipment.effective_status)).all()
        team = workload(s, today)

    groups: dict[tuple, dict[str, Any]] = {}
    for buyer_code, buyer_raw, exfty, owner, status in rows:
        buyer = buyer_code or buyer_raw or "(không rõ buyer)"
        if exfty:
            monday = exfty - dt.timedelta(days=exfty.weekday())
            week = monday.isocalendar()
            key = (buyer, monday)
            label_week = f"tuần {week[1]} · {monday:%d/%m}–{monday + dt.timedelta(days=6):%d/%m}"
            date_from, date_to = monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()
        else:
            key = (buyer, None)
            label_week = "chưa có EX-FACT"
            date_from = date_to = ""
        g = groups.setdefault(key, {
            "buyer": buyer, "week": label_week, "date_from": date_from, "date_to": date_to,
            "n": 0, "urgent": 0, "owner": owner or "",
        })
        g["n"] += 1
        if status in ("RED", "WHITE"):
            g["urgent"] += 1
    out = sorted(groups.values(), key=lambda g: (g["date_from"] or "9999", -g["n"]))
    return {"groups": out[:200], "team": team, "total_groups": len(out)}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Automation (job nen)
# ---------------------------------------------------------------------------


@router.get("/automation", response_class=HTMLResponse)
def automation_page(request: Request):
    require(request, "automation.run")
    sched = CTX["scheduler"]
    with session_scope() as s:
        runs = [{"id": r.id, "job": r.job_name, "by": r.triggered_by, "start": r.started_at,
                 "end": r.finished_at, "status": r.status, "retry": r.retry_count, "error": r.error,
                 "summary": r.summary or {}}
                for r in s.execute(select(JobRun).order_by(JobRun.id.desc()).limit(80)).scalars()]
    jobs = sched.jobs() if sched else []
    return render(request, "automation.html", runs=runs, jobs=jobs)


@router.post("/actions/scan")
def action_scan(request: Request):
    p = require(request, "automation.run")
    sched = CTX["scheduler"]
    result = sched.scan_now(dry_run=False, triggered_by=p.name) if sched else engine().run_scan(dry_run=False)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(result.as_dict())
    return RedirectResponse(request.headers.get("referer", "/") or "/", status_code=303)


@router.post("/actions/job/{name}")
def run_job(request: Request, name: str):
    p = require(request, "automation.run")
    sched = CTX["scheduler"]
    if sched is None:
        return RedirectResponse("/automation", status_code=303)
    fns = {"notification_dispatch": sched._flush_notifications, "excel_write_retry": sched._retry_pending,
           "housekeeping": sched._housekeeping, "outlook_scan": sched._outlook_scan,
           "weekly_planning_check": sched._weekly_check}
    if name == "followup_scan":
        sched.scan_now(triggered_by=p.name)
    elif name in fns:
        sched.run_job(name, fns[name], triggered_by=p.name, retries=0)
    return RedirectResponse("/automation", status_code=303)


@router.get("/dryrun", response_class=HTMLResponse)
def dryrun_page(request: Request):
    require(request, "automation.run")
    result = engine().run_scan(dry_run=True, deliver_notifications=False)
    return render(request, "dryrun.html", report=result.render_text(), data=result.as_dict())


@router.post("/actions/pause")
def action_pause(request: Request):
    require(request, "automation.pause")
    if CTX["scheduler"]:
        CTX["scheduler"].pause()
    return RedirectResponse("/automation", status_code=303)


@router.post("/actions/resume")
def action_resume(request: Request):
    require(request, "automation.pause")
    if CTX["scheduler"]:
        CTX["scheduler"].resume()
    return RedirectResponse("/automation", status_code=303)


# ---------------------------------------------------------------------------
# Mapping / Buyer / Cai dat / Backup (Admin)
# ---------------------------------------------------------------------------


@router.get("/mapping", response_class=HTMLResponse)
def mapping_page(request: Request):
    require(request, "config.mapping")
    with session_scope() as s:
        files = list(s.execute(select(SourceFile).order_by(SourceFile.code)).scalars())
        sheets = []
        for f in files:
            for sm in s.execute(select(SheetMap).where(SheetMap.file_id == f.id).order_by(SheetMap.sheet_name)
                                ).scalars():
                sheets.append({"id": sm.id, "file": f.code, "file_name": f.display_name, "sheet": sm.sheet_name,
                               "is_fu": sm.is_follow_up, "conf": sm.confidence,
                               "confirmed": sm.confirmed_by_user, "note": sm.detection_note,
                               "header_row": sm.header_row})
        cols = [{"id": c.id, "file": c.file_code, "sheet": c.sheet_name, "header": c.source_header,
                 "field": c.canonical_field, "conf": c.confidence, "confirmed": c.confirmed_by_user}
                for c in s.execute(select(ColumnMapping).order_by(ColumnMapping.file_code)).scalars()]
    return render(request, "mapping.html", sheets=sheets, cols=cols, fields=sorted(engine().dictionary.fields))


@router.post("/mapping/sheet/{sheet_id}")
def confirm_sheet(request: Request, sheet_id: int, is_follow_up: str = Form("no")):
    p = require(request, "config.mapping")
    with session_scope() as s:
        sm = s.get(SheetMap, sheet_id)
        if sm is not None:
            sm.is_follow_up = is_follow_up == "yes"
            sm.confirmed_by_user = True
            log_audit(s, "CONFIRM_SHEET", actor_type="USER", actor_name=p.name, entity="sheet",
                      entity_id=str(sheet_id), new_value="FollowUp" if sm.is_follow_up else "Ignore",
                      field=sm.sheet_name)
    return RedirectResponse("/mapping", status_code=303)


@router.post("/mapping/column")
def add_column_mapping(request: Request, file_code: str = Form(...), sheet: str = Form("*"),
                       header: str = Form(...), field: str = Form(...)):
    p = require(request, "config.mapping")
    with session_scope() as s:
        existing = s.execute(select(ColumnMapping).where(
            ColumnMapping.file_code == file_code, ColumnMapping.sheet_name == sheet,
            ColumnMapping.source_header == header)).scalar_one_or_none()
        if existing is None:
            s.add(ColumnMapping(file_code=file_code, sheet_name=sheet, source_header=header,
                                canonical_field=field, confidence=1.0, confirmed_by_user=True))
        else:
            existing.canonical_field, existing.confirmed_by_user = field, True
        log_audit(s, "MAP_COLUMN", actor_type="USER", actor_name=p.name, entity="column",
                  entity_id=f"{file_code}/{sheet}", field=header, new_value=field)
    return RedirectResponse("/mapping", status_code=303)


@router.get("/buyers", response_class=HTMLResponse)
def buyers_page(request: Request):
    require(request, "config.buyers")
    fw_rules = [{"forwarder": r.forwarder, "buyer": r.buyer, "mode": r.mode, "dest": r.dest, "port": r.port}
                for r in engine().forwarder_rules]
    return render(request, "buyers.html", rules=engine().buyers.to_dicts(), fw_rules=fw_rules,
                  assign_rules=settings().assignment.rules)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    require(request, "config.settings")
    s_ = settings()
    return render(request, "settings.html", cfg=s_, backups_files=[f.id for f in s_.files])


@router.post("/settings")
def save_settings(request: Request, scan_interval_hours: int = Form(3), notify_start: str = Form("08:00"),
                  notify_end: str = Form("16:00"), hold_reminder_days: int = Form(3),
                  booking_start: int = Form(20), booking_end: int = Form(14), write_color: str = Form("yes"),
                  dry_run_default: str = Form("no")):
    p = require(request, "config.settings")
    s_ = settings()
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
        log_audit(sess, "UPDATE_SETTINGS", actor_type="USER", actor_name=p.name, entity="settings",
                  new_value=f"scan={s_.scheduler.scan_interval_hours}h window={notify_start}-{notify_end} "
                            f"hold={hold_reminder_days}d")
    return RedirectResponse("/settings", status_code=303)


@router.get("/backups", response_class=HTMLResponse)
def backups_page(request: Request, file_code: str = ""):
    require(request, "config.backups")
    s_ = settings()
    codes = [f.id for f in s_.files]
    code = file_code or (codes[0] if codes else "")
    rows = list_backups(s_.backup_dir, code) if code else []
    return render(request, "backups.html", codes=codes, code=code, rows=rows)


@router.post("/backups/rollback")
def do_rollback(request: Request, file_code: str = Form(...), backup_path: str = Form(...)):
    p = require(request, "config.backups")
    s_ = settings()
    fcfg = s_.file_by_code(file_code)
    if fcfg is None:
        return JSONResponse({"ok": False, "error": "file_code khong ton tai"}, status_code=400)
    bpath = Path(backup_path).resolve()
    # Chi cho khoi phuc tu thu muc backup cua he thong (khong nhan duong dan tuy y)
    if s_.backup_dir.resolve() not in bpath.parents:
        return JSONResponse({"ok": False, "error": "Duong dan backup khong hop le"}, status_code=400)
    target = s_.resolve_path(fcfg.path)
    rollback(bpath, target, s_.backup_dir, file_code)
    with session_scope() as sess:
        log_audit(sess, "ROLLBACK", actor_type="USER", actor_name=p.name, entity="file", entity_id=file_code,
                  new_value=bpath.name, source=str(target))
    return RedirectResponse(f"/backups?file_code={file_code}", status_code=303)
