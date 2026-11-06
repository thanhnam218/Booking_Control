"""Nhap file Excel NGOAI (vd "GARMENT SHIPMENT SCHEDULE" 61 cot).

Khac trang "Nhap file Follow Up": file ngoai khong theo khung 47 cot nen khong the tu
nhan dien. Quy trinh 3 buoc:

    1. Tai file len   -> may chu liet ke sheet, doan dong tieu de, doc thu vai tram dong
    2. Chon cot       -> hien TAT CA cot kem ty le co du lieu + gia tri mau + goi y anh xa;
                         nguoi dung chi tick nhung cot can. Luu lai thanh "bo map" dung lai.
    3. Xem truoc      -> bao nhieu dong them moi / cap nhat / bo qua va vi sao, roi moi Nhap.

Chi cot duoc chon moi vao he thong. Cot khong chon = khong doc den.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..audit import log_audit
from ..db import session_scope
from ..db.models import utcnow
from ..db.models_v3 import ImportProfile
from ..excel import external as ex
from .deps import engine, render, require, settings
from .routes_grid import _user_options

router = APIRouter()

TOKEN_RE = re.compile(r"\d{14}_[0-9a-f]{8}")
# Chi tu chon san goi y that chac chan; goi y mo ho de nguoi dung tu quyet
AUTO_PICK_CONFIDENCE = 0.9
MAX_UPLOAD_MB = 40


def _dir() -> Path:
    d = settings().data_dir / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _saved_path(token: str) -> Path | None:
    if not TOKEN_RE.fullmatch(token or ""):
        return None
    hits = list(_dir().glob(f"{token}__*"))
    return hits[0] if hits else None


def _field_choices() -> list[dict[str, str]]:
    tmpl = engine().template
    return [{"field": c.field, "label": f"{c.letter} · {c.label}"} for c in tmpl.columns]


def _profiles(s) -> list[dict[str, Any]]:
    rows = s.execute(select(ImportProfile).order_by(ImportProfile.used_at.desc().nullslast(),
                                                    ImportProfile.id.desc())).scalars()
    return [{"id": p.id, "name": p.name, "sheet": p.sheet_name, "header_row": p.header_row,
             "cols": len(p.mapping or {}), "by": p.created_by, "used": p.use_count,
             "note": p.note} for p in rows]


def _page(request: Request, **kw):
    with session_scope() as s:
        users = _user_options(s)
        profiles = _profiles(s)
    ctx = {"users": users, "profiles": profiles, "fields": _field_choices(),
           "step": "upload", "error": "", "info": None, "result": None,
           "token": "", "filename": "", "owner": "", "mapping": {}, "profile_name": ""}
    ctx.update(kw)
    return render(request, "import_external.html", **ctx)


def _parse_mapping(raw: str) -> dict[int, str]:
    """Chuoi JSON tu form -> {chi so cot: truong}. Bo cot trung truong."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    out: dict[int, str] = {}
    seen: set[str] = set()
    fields = {c.field for c in engine().template.columns}
    for k, v in data.items():
        if not v or v not in fields or v in seen:
            continue
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if idx > 0:
            out[idx] = v
            seen.add(v)
    return out


# ---------------------------------------------------------------------------


@router.get("/import/external", response_class=HTMLResponse)
def external_page(request: Request):
    require(request, "import.excel")
    return _page(request)


@router.post("/import/external/inspect", response_class=HTMLResponse)
async def external_inspect(request: Request, file: UploadFile = File(...),
                           owner: str = Form(""), profile_id: str = Form("")):
    p = require(request, "import.excel")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(file.filename or "upload.xlsx").name)[:80]
    if not name.lower().endswith((".xlsx", ".xlsm")):
        return _page(request, error="Chỉ nhận file .xlsx / .xlsm")
    content = await file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        return _page(request, error=f"File quá lớn (> {MAX_UPLOAD_MB}MB)")
    token = f"{dt.datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(4)}"
    path = _dir() / f"{token}__{name}"
    path.write_bytes(content)
    return _show_mapping(request, token, name, owner or p.name, profile_id=profile_id)


@router.post("/import/external/map", response_class=HTMLResponse)
def external_remap(request: Request, token: str = Form(...), filename: str = Form(""),
                   owner: str = Form(""), sheet: str = Form(""), header_row: int = Form(0),
                   profile_id: str = Form("")):
    """Doc lai voi sheet / dong tieu de khac."""
    require(request, "import.excel")
    return _show_mapping(request, token, filename, owner, sheet=sheet,
                         header_row=header_row or None, profile_id=profile_id)


def _show_mapping(request: Request, token: str, filename: str, owner: str,
                  sheet: str = "", header_row: int | None = None, profile_id: str = ""):
    path = _saved_path(token)
    if path is None:
        return _page(request, error="File tải lên đã hết hạn, hãy tải lại")
    preset: dict[int, str] = {}
    profile_name = ""
    if profile_id:
        with session_scope() as s:
            prof = s.get(ImportProfile, int(profile_id)) if profile_id.isdigit() else None
            if prof:
                preset = {int(k): v for k, v in (prof.mapping or {}).items()}
                sheet = sheet or prof.sheet_name
                header_row = header_row or prof.header_row
                profile_name = prof.name
    try:
        sheets = ex.list_sheets(path)
        info = ex.inspect_sheet(path, sheet=sheet, header_row=header_row,
                                dictionary=engine().dictionary)
    except Exception as exc:  # noqa: BLE001
        return _page(request, error=f"Không đọc được file: {exc}")

    used: set[str] = set(preset.values())
    cols = []
    for c in info.columns:
        chosen = preset.get(c.index, "")
        if not chosen and c.suggestion and c.confidence >= AUTO_PICK_CONFIDENCE \
                and c.suggestion not in used and c.filled:
            chosen = c.suggestion
            used.add(chosen)
        d = c.as_dict()
        d["chosen"] = chosen
        cols.append(d)
    return _page(request, step="map", token=token, filename=filename, owner=owner,
                 info={"sheets": sheets, "sheet": info.name, "header_row": info.header_row,
                       "data_rows": info.data_rows, "columns": cols},
                 profile_name=profile_name)


@router.post("/import/external/preview", response_class=HTMLResponse)
def external_preview(request: Request, token: str = Form(...), filename: str = Form(""),
                     owner: str = Form(...), sheet: str = Form(...), header_row: int = Form(1),
                     mapping: str = Form("")):
    require(request, "import.excel")
    path = _saved_path(token)
    if path is None:
        return _page(request, error="File tải lên đã hết hạn, hãy tải lại")
    mp = _parse_mapping(mapping)
    missing = [f for f in ("JO", "ExFtyDate", "Buyer") if f not in mp.values()]
    if missing:
        labels = {c.field: c.label for c in engine().template.columns}
        return _page(request, step="map", token=token, filename=filename, owner=owner,
                     error="Cần chọn cột cho: " + ", ".join(labels.get(f, f) for f in missing),
                     info=_reinspect(path, sheet, header_row, mp))
    try:
        result = engine().import_external(
            path, sheet=sheet, header_row=header_row, mapping=mp, owner=owner,
            file_label=filename or path.name, dry_run=True)
    except Exception as exc:  # noqa: BLE001
        return _page(request, error=f"Không đọc được file: {exc}")
    return _page(request, step="preview", token=token, filename=filename, owner=owner,
                 result=result, mapping=mp, sheet=sheet, header_row=header_row,
                 mapping_json=json.dumps({str(k): v for k, v in mp.items()}))


def _reinspect(path: Path, sheet: str, header_row: int, mp: dict[int, str]) -> dict[str, Any]:
    info = ex.inspect_sheet(path, sheet=sheet, header_row=header_row, dictionary=engine().dictionary)
    cols = []
    for c in info.columns:
        d = c.as_dict()
        d["chosen"] = mp.get(c.index, "")
        cols.append(d)
    return {"sheets": ex.list_sheets(path), "sheet": info.name, "header_row": info.header_row,
            "data_rows": info.data_rows, "columns": cols}


@router.post("/import/external/apply", response_class=HTMLResponse)
def external_apply(request: Request, token: str = Form(...), filename: str = Form(""),
                   owner: str = Form(...), sheet: str = Form(...), header_row: int = Form(1),
                   mapping: str = Form(""), save_profile: str = Form(""),
                   profile_name: str = Form("")):
    p = require(request, "import.excel")
    path = _saved_path(token)
    if path is None:
        return _page(request, error="File tải lên đã hết hạn, hãy tải lại")
    mp = _parse_mapping(mapping)
    if not mp:
        return _page(request, error="Chưa chọn cột nào")
    result = engine().import_external(
        path, sheet=sheet, header_row=header_row, mapping=mp, owner=owner,
        file_label=filename or path.name, dry_run=False)

    if save_profile and profile_name.strip():
        with session_scope() as s:
            name = profile_name.strip()[:120]
            prof = s.execute(select(ImportProfile).where(ImportProfile.name == name)).scalar_one_or_none()
            if prof is None:
                prof = ImportProfile(name=name, created_by=p.name)
                s.add(prof)
            prof.sheet_name = sheet
            prof.header_row = header_row
            prof.mapping = {str(k): v for k, v in mp.items()}
            prof.note = f"{filename} · {len(mp)} cột"
            prof.used_at = utcnow()
            prof.use_count = (prof.use_count or 0) + 1
            log_audit(s, "IMPORT_PROFILE_SAVED", actor_type="USER", actor_name=p.name,
                      entity="import_profile", entity_id=name, new_value=str(len(mp)) + " cot")
    return _page(request, step="done", result=result, filename=filename, owner=owner)


@router.post("/import/external/profile/delete")
def external_profile_delete(request: Request, profile_id: int = Form(...)):
    p = require(request, "import.excel")
    with session_scope() as s:
        prof = s.get(ImportProfile, profile_id)
        if prof is not None:
            log_audit(s, "IMPORT_PROFILE_DELETED", actor_type="USER", actor_name=p.name,
                      entity="import_profile", entity_id=prof.name, old_value=prof.name)
            s.delete(prof)
    return RedirectResponse("/import/external", status_code=303)
