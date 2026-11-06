"""Luoi Follow Up 47 cot giong Excel + API luu tu dong + phan cong hang loat + xuat/nhap Excel."""

from __future__ import annotations

import datetime as dt
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..audit import log_audit
from ..db import session_scope
from ..db.models import Shipment, User, utcnow
from ..services import shipment_service as svc
from ..services.assignment import AssignmentError, active_users, assign
from ..services.excel_export import build_workbook, export_filename
from .deps import current, engine, render, require, settings

router = APIRouter()


def _filters(request: Request) -> dict[str, Any]:
    q = request.query_params
    keys = ("assigned", "status", "biz", "warn", "buyer", "forwarder", "mode",
            "date_from", "date_to", "q")
    f = {k: q.get(k, "") for k in keys if q.get(k)}
    if q.get("hide_done") in ("1", "true", "yes"):
        f["hide_done"] = True
    return f


def _user_options(s) -> list[dict[str, str]]:
    return [{"name": u.name, "label": u.label, "role": u.role}
            for u in sorted(active_users(s).values(), key=lambda x: x.label.lower())
            if u.role in ("user", "manager", "admin")]


# ---------------------------------------------------------------------------
# Trang luoi
# ---------------------------------------------------------------------------


@router.get("/followup", response_class=HTMLResponse)
def followup_page(request: Request):
    p = current(request)
    tmpl = engine().template
    with session_scope() as s:
        users = _user_options(s) if p.can("shipments.view_all") else []
        buyers = sorted({b for (b,) in s.execute(
            svc.visible_stmt(p).with_only_columns(Shipment.buyer_code).distinct()).all() if b})
    cfg = settings()
    colors = {k: "#" + v[-6:] for k, v in cfg.status.colors.items()}
    for c in cfg.status.custom:
        colors[c.name] = "#" + c.color[-6:]
    grid_cfg = {
        **tmpl.client_config(p.role),
        "colors": colors,
        "me": {"name": p.name, "role": p.role, "manager": p.is_manager,
               "can_assign": p.can("shipments.assign"), "can_archive": p.can("shipments.archive"),
               "can_create": p.can("shipments.create"), "can_edit": p.can("shipments.edit")},
        "users": users,
        "page_size": cfg.web.grid_page_size,
    }
    return render(request, "followup.html", grid_cfg=grid_cfg, buyers=buyers, users=users,
                  filters=_filters(request),
                  booking_warn_days=cfg.deadlines.booking_window.end_days_before)


@router.get("/api/followup/rows")
def followup_rows(request: Request, page: int = 1):
    """Phan trang phia may chu (grid_page_size dong / trang) - 10.000+ lo van mo nhanh."""
    p = current(request)
    today = dt.date.today()
    tmpl = engine().template
    limit = max(10, settings().web.grid_page_size)
    page = max(1, page)
    with session_scope() as s:
        base = svc.apply_filters(svc.visible_stmt(p), _filters(request))
        total = s.execute(select(func.count()).select_from(base.order_by(None).subquery())).scalar_one()
        stmt = svc.order_like_followup(base).offset((page - 1) * limit).limit(limit)
        ships = list(s.execute(stmt).scalars())
        rows = [svc.grid_row(sh, tmpl, today, settings().status_color) for sh in ships]
    pages = max(1, -(-total // limit))
    return {"rows": rows, "truncated": total > page * limit, "limit": limit, "page": page, "pages": pages,
            "total": total}


class RowChange(BaseModel):
    code: str
    v: int | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class SaveBody(BaseModel):
    changes: list[RowChange]
    reason: str = ""


@router.post("/api/followup/save")
def followup_save(request: Request, body: SaveBody):
    p = current(request)
    if not p.can("shipments.edit"):
        return JSONResponse({"error": "Tài khoản chỉ xem, không sửa được"}, status_code=403)
    out: dict[str, Any] = {"rows": [], "errors": {}, "conflicts": []}
    for ch in body.changes[:500]:
        with session_scope() as s:
            try:
                sh = svc.get_visible(s, p, ch.code)
                res = svc.apply_edits(s, engine(), p, sh, ch.values, version=ch.v, reason=body.reason)
            except svc.ServiceError as exc:
                out["errors"][ch.code] = {"_": str(exc)}
                continue
            if res.row:
                out["rows"].append(res.row)
            if res.errors:
                out["errors"][ch.code] = res.errors
            if res.conflict:
                out["conflicts"].append(ch.code)
    return out


class CreateBody(BaseModel):
    values: dict[str, Any]
    assign_to: str | None = None


@router.post("/api/followup/create")
def followup_create(request: Request, body: CreateBody):
    p = current(request)
    with session_scope() as s:
        try:
            sh = svc.create_shipment(s, engine(), p, body.values, assign_to=body.assign_to)
        except svc.ServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        row = svc.grid_row(sh, engine().template, dt.date.today(), settings().status_color)
    return {"row": row}


class AssignBody(BaseModel):
    codes: list[str] = Field(default_factory=list)
    to_user: str = ""
    kind: str = "PRIMARY"
    note: str = ""
    # Bulk theo dieu kien (SRS v2 muc 17): Buyer / Forwarder / EX-FACT / Mode / chua phan cong
    by_filter: dict[str, Any] | None = None


@router.post("/api/followup/assign")
def followup_assign(request: Request, body: AssignBody):
    p = require(request, "shipments.assign")
    with session_scope() as s:
        if body.by_filter:
            stmt = svc.apply_filters(svc.visible_stmt(p), body.by_filter)
            ships = list(s.execute(stmt).scalars())
        else:
            ships = list(s.execute(select(Shipment).where(Shipment.shipment_code.in_(body.codes[:2000]))).scalars())
        try:
            out = assign(s, ships, body.to_user, by=p.name, kind=body.kind,
                         method="BULK" if len(ships) > 1 else "MANUAL", note=body.note)
        except AssignmentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        rows = [svc.grid_row(sh, engine().template, dt.date.today(), settings().status_color)
                for sh in ships if sh.shipment_code in out.changed]
    return {"changed": len(out.changed), "unchanged": len(out.unchanged), "rows": rows}


class ArchiveBody(BaseModel):
    codes: list[str]
    archived: bool = True
    note: str = ""


@router.post("/api/followup/archive")
def followup_archive(request: Request, body: ArchiveBody):
    p = require(request, "shipments.archive")
    with session_scope() as s:
        ships = list(s.execute(select(Shipment).where(Shipment.shipment_code.in_(body.codes[:2000]))).scalars())
        n = svc.set_archived(s, p, ships, body.archived, body.note)
    return {"changed": n}


# ---------------------------------------------------------------------------
# Dung chung cung luc: keo thay doi cua nguoi khac ve + bao ai dang sua dong nao
#
# Ca hai deu la "nhe": khong WebSocket, chi mot lan goi moi vai giay tu trinh duyet.
# Danh sach nguoi dang xem nam trong bo nho tien trinh may chu (mat khi khoi dong lai,
# khong chia se giua nhieu worker) - du cho mot phong vai chuc nguoi.
# ---------------------------------------------------------------------------

PRESENCE_TTL = dt.timedelta(seconds=25)
_PRESENCE: dict[str, dict[str, Any]] = {}


def _presence_sweep(now: dt.datetime) -> None:
    for name in [n for n, v in _PRESENCE.items() if now - v["at"] > PRESENCE_TTL]:
        _PRESENCE.pop(name, None)


@router.get("/api/followup/changes")
def followup_changes(request: Request, since: str = "", limit: int = 400):
    """Dong nao vua doi ke tu `since` (do nguoi khac hoac Automation sua)."""
    p = current(request)
    now = utcnow()
    try:
        mark = dt.datetime.fromisoformat(since) if since else now - dt.timedelta(minutes=5)
    except ValueError:
        mark = now - dt.timedelta(minutes=5)
    today = dt.date.today()
    tmpl = engine().template
    with session_scope() as s:
        stmt = (svc.apply_filters(svc.visible_stmt(p), _filters(request))
                .where(Shipment.updated_at.is_not(None), Shipment.updated_at > mark)
                .order_by(Shipment.updated_at)
                .limit(max(1, min(limit, 1000))))
        ships = list(s.execute(stmt).scalars())
        rows = [svc.grid_row(sh, tmpl, today, settings().status_color) for sh in ships]
        by = sorted({sh.updated_by for sh in ships if sh.updated_by and sh.updated_by != p.name})
    return {"now": now.isoformat(timespec="seconds"), "rows": rows, "by": by}


class PresenceBody(BaseModel):
    codes: list[str] = Field(default_factory=list)   # cac dong dang hien tren man hinh
    editing: str = ""                                # dong dang go do


@router.post("/api/followup/presence")
def followup_presence(request: Request, body: PresenceBody):
    """Bao may chu "toi dang xem cac dong nay" va nhan lai ai dang sua dong nao."""
    p = current(request)
    now = utcnow()
    _presence_sweep(now)
    _PRESENCE[p.name] = {"at": now, "label": p.label, "editing": body.editing[:40],
                         "codes": set(body.codes[:3000])}
    mine = _PRESENCE[p.name]["codes"]
    editors: dict[str, str] = {}
    viewers = 0
    for name, v in _PRESENCE.items():
        if name == p.name:
            continue
        viewers += 1
        if v["editing"] and v["editing"] in mine:
            editors[v["editing"]] = v["label"]
    return {"editors": editors, "viewers": viewers}


# ---------------------------------------------------------------------------
# Xuat Excel - quyen kiem tra o backend (SRS v2 muc 64)
# ---------------------------------------------------------------------------


@router.get("/export/followup.xlsx")
def export_followup(request: Request, scope: str = "mine", system_columns: int = 1, formulas: int = 1):
    p = current(request)
    f = _filters(request)
    today = dt.date.today()
    with session_scope() as s:
        stmt = svc.visible_stmt(p)
        if scope == "mine" or not p.can("export.all"):
            # "Export My Shipments": chi lo minh phu trach (chinh hoac du phong)
            from sqlalchemy import or_

            stmt = stmt.where(or_(Shipment.assigned_to == p.name, Shipment.backup_owner == p.name))
            label = p.name
        else:
            label = "Team" if not f.get("assigned") else f["assigned"]
        stmt = svc.order_like_followup(svc.apply_filters(stmt, f))
        ships = list(s.execute(stmt).scalars())
        labels = {u.name: u.label for u in s.execute(select(User)).scalars()}
        data = build_workbook(ships, engine().template, status_color=settings().status_color, today=today,
                              include_system_columns=bool(system_columns), write_formulas=bool(formulas),
                              user_labels=labels, title=f"Follow Up - {label}")
        log_audit(s, "EXCEL_EXPORTED", actor_type="USER", actor_name=p.name, entity="export",
                  entity_id=scope, new_value=f"{len(ships)} lo", note=str(f)[:300])
    name = export_filename(label, today)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# Nhap Excel Follow Up (upload) - xem truoc roi moi nhap
# ---------------------------------------------------------------------------


def _import_dir() -> Path:
    d = settings().data_dir / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    require(request, "import.excel")
    with session_scope() as s:
        users = _user_options(s)
    return render(request, "import.html", users=users, result=None, token="", error="")


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(request: Request, file: UploadFile = File(...), owner: str = Form(...)):
    require(request, "import.excel")
    with session_scope() as s:
        users = _user_options(s)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(file.filename or "upload.xlsx").name)[:80]
    if not name.lower().endswith((".xlsx", ".xlsm")):
        return render(request, "import.html", status_code=400, users=users, result=None, token="",
                      error="Chỉ nhận file .xlsx / .xlsm")
    content = await file.read()
    if len(content) > 30 * 1024 * 1024:
        return render(request, "import.html", status_code=400, users=users, result=None, token="",
                      error="File quá lớn (> 30MB)")
    token = f"{dt.datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(4)}"
    path = _import_dir() / f"{token}__{name}"
    path.write_bytes(content)
    try:
        result = engine().import_followup(path, owner=owner, file_label=name, dry_run=True)
    except Exception as exc:  # noqa: BLE001
        return render(request, "import.html", status_code=400, users=users, result=None, token="",
                      error=f"Không đọc được file: {exc}")
    return render(request, "import.html", users=users, result=result, token=token, owner=owner,
                  filename=name, error="")


@router.post("/import/apply", response_class=HTMLResponse)
def import_apply(request: Request, token: str = Form(...), owner: str = Form(...)):
    p = require(request, "import.excel")
    if not re.fullmatch(r"\d{14}_[0-9a-f]{8}", token):
        return render(request, "error.html", status_code=400, message="Phiên nhập không hợp lệ")
    matches = list(_import_dir().glob(f"{token}__*"))
    if not matches:
        return render(request, "error.html", status_code=404, message="File tải lên đã hết hạn, hãy tải lại")
    path = matches[0]
    result = engine().import_followup(path, owner=owner, file_label=path.name.split("__", 1)[1],
                                      dry_run=False)
    with session_scope() as s:
        log_audit(s, "EXCEL_IMPORT_APPLIED", actor_type="USER", actor_name=p.name, entity="file",
                  entity_id=result["file"], new_value=f"{result['new']} moi / {result['update']} cap nhat",
                  note=f"owner={owner}")
        users = _user_options(s)
    return render(request, "import.html", users=users, result=result, token="", owner=owner,
                  filename=path.name.split("__", 1)[1], error="", applied=True)
