"""Hang cho duyet email Outlook (SRSv5 muc 19-27, BR-006/007/019).

    /emails?tab=REVIEW|CONFLICT|UNMATCHED|APPLIED|IGNORED|ALL
    /emails/{id}                  Truong trich tu email vs PDF, lo goi y + ly do cham diem, ap dung / bo qua
    POST /emails/{id}/apply       Chon lo (1 hoac nhieu - booking gop), loai email, gia tri khi mau thuan
    POST /emails/{id}/ignore      Email khong lien quan
    GET  /emails/{id}/files/{aid} Tai file dinh kem da luu

Nguoi dung thay email ghep / goi y vao lo cua minh + email chua ghep duoc; Quan ly / Auditor thay tat ca.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select

from ..core import email_parse as ep
from ..db import session_scope
from ..db.models import Shipment
from ..db.models_v3 import Booking, Email, EmailAttachment
from ..services import shipment_service as svc
from ..services.email_service import EmailError, EmailProcessor
from .deps import PermissionDenied, current, engine, render
from .routes_booking import _back, _shipment_options

router = APIRouter()

TABS = [("REVIEW", "Chờ duyệt"), ("CONFLICT", "Email ≠ PDF"), ("UNMATCHED", "Chưa ghép"),
        ("APPLIED", "Đã áp dụng"), ("IGNORED", "Bỏ qua"), ("ALL", "Tất cả")]
CATEGORIES = ["BOOKING_CONFIRMATION", "AMENDMENT", "DELAY", "REBOOK", "HOLD", "RELEASE_HOLD", "CANCEL",
              "BOOKING_REQUEST", "OTHER"]
CATEGORY_LABELS = {"BOOKING_CONFIRMATION": "Xác nhận booking", "AMENDMENT": "Điều chỉnh", "DELAY": "Trễ lịch",
                   "REBOOK": "Rebook", "HOLD": "Hold", "RELEASE_HOLD": "Giải tỏa hold", "CANCEL": "Hủy booking",
                   "BOOKING_REQUEST": "Yêu cầu booking", "OTHER": "Khác"}
FIELD_LABELS = {"booking_no": "Booking No", "vessel": "Vessel", "voyage": "Voyage", "etd": "ETD", "eta": "ETA",
                "cy_cutoff": "CY cut-off", "si_vgm_cutoff": "SI/VGM cut-off", "closing_time": "Closing",
                "port": "Port", "containers": "Container", "forwarder": "Forwarder"}


def _visible_codes(s, p) -> set[str] | None:
    """None = thay tat ca."""
    if p.can("email.view_all"):
        return None
    return {c for (c,) in s.execute(svc.visible_stmt(p).with_only_columns(Shipment.shipment_code)).all()}


def _codes_of(e: Email) -> set[str]:
    return set(e.shipment_codes or []) | {c.get("code") for c in e.candidates or [] if c.get("code")}


def _can_see(e: Email, mine: set[str] | None) -> bool:
    if mine is None:
        return True
    codes = _codes_of(e)
    return bool(codes & mine) or (e.match_status == "UNMATCHED" and e.category != "OTHER")


def _get(s, p, email_id: int) -> Email:
    e = s.get(Email, email_id)
    if e is None or not _can_see(e, _visible_codes(s, p)):
        raise svc.NotFound("Không tìm thấy email")
    return e


def queue_counts(s, p) -> dict[str, int]:
    mine = _visible_codes(s, p)
    out = {k: 0 for k, _ in TABS}
    if mine is None:
        for st, n in s.execute(select(Email.match_status, func.count()).group_by(Email.match_status)).all():
            out[st] = n
    else:
        for e in s.execute(select(Email).where(Email.match_status.in_(("REVIEW", "CONFLICT", "UNMATCHED")))
                           .order_by(Email.id.desc()).limit(2000)).scalars():
            if _can_see(e, mine):
                out[e.match_status] += 1
    out["ALL"] = sum(v for k, v in out.items() if k != "ALL")
    return out


@router.get("/emails", response_class=HTMLResponse)
def emails_page(request: Request, tab: str = "REVIEW", q: str = "", msg: str = "", err: str = ""):
    p = current(request)
    with session_scope() as s:
        mine = _visible_codes(s, p)
        stmt = select(Email)
        if tab != "ALL":
            stmt = stmt.where(Email.match_status == tab)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(Email.subject.ilike(like) | Email.sender.ilike(like))
        found = [e for e in s.execute(stmt.order_by(Email.received_at.desc(), Email.id.desc()).limit(1500)).scalars()
                 if _can_see(e, mine)][:300]
        jo = {sh.shipment_code: sh.jo for sh in s.execute(select(Shipment).where(Shipment.shipment_code.in_(
            list({c for e in found for c in _codes_of(e)}) or ["-"]))).scalars()}
        rows = [{"id": e.id, "received": e.received_at, "direction": e.direction, "sender": e.sender,
                 "subject": e.subject, "category": e.category, "status": e.match_status, "score": e.match_score,
                 "forwarder": e.forwarder, "conflicts": list((e.conflicts or {}).keys()),
                 "shipments": [jo.get(c, c) for c in (e.shipment_codes or [])] or
                              [f"{jo.get(c['code'], c['code'])} ({c['score']})" for c in (e.candidates or [])[:2]],
                 "booking_id": e.booking_id}
                for e in found]
        counts = queue_counts(s, p)
    return render(request, "emails.html", rows=rows, tab=tab, tabs=TABS, counts=counts, q=q,
                  cat_labels=CATEGORY_LABELS, msg=msg, err=err,
                  outlook_on=engine().settings.outlook.enabled)


@router.get("/emails/{email_id}", response_class=HTMLResponse)
def email_detail(request: Request, email_id: int, msg: str = "", err: str = ""):
    p = current(request)
    with session_scope() as s:
        e = _get(s, p, email_id)
        body, pdf = e.extracted or {}, e.attachment_extracted or {}
        merged = ep.merge_fields(body, pdf)
        fields = [{"key": k, "label": FIELD_LABELS.get(k, k), "email": body.get(k, ""), "pdf": pdf.get(k, ""),
                   "value": merged.get(k, ""), "conflict": k in (e.conflicts or {})}
                  for k in FIELD_LABELS if body.get(k) or pdf.get(k)]
        codes = _codes_of(e)
        ships = {sh.shipment_code: sh for sh in s.execute(select(Shipment).where(
            Shipment.shipment_code.in_(list(codes) or ["-"]))).scalars()}
        cands = []
        for c in e.candidates or []:
            sh = ships.get(c["code"])
            cands.append({**c, "jo": sh.jo if sh else c["code"], "buyer": (sh.buyer_code if sh else ""),
                          "exfty": sh.exfty_date if sh else None, "owner": sh.assigned_to if sh else "",
                          "booking_no": sh.booking_no if sh else "",
                          "checked": c["code"] in (e.shipment_codes or [])})
        for c in e.shipment_codes or []:
            if c not in {x["code"] for x in cands}:
                sh = ships.get(c)
                cands.append({"code": c, "score": None, "reasons": ["Chọn tay"], "jo": sh.jo if sh else c,
                              "buyer": sh.buyer_code if sh else "", "exfty": sh.exfty_date if sh else None,
                              "owner": sh.assigned_to if sh else "", "booking_no": sh.booking_no if sh else "",
                              "checked": True})
        atts = [{"id": a.id, "name": a.filename, "size": a.size, "status": a.parse_status, "error": a.parse_error}
                for a in s.execute(select(EmailAttachment).where(EmailAttachment.email_id == e.id)).scalars()]
        thread = []
        if e.conversation_id:
            thread = [{"id": x.id, "subject": x.subject, "received": x.received_at, "direction": x.direction,
                       "category": x.category, "status": x.match_status}
                      for x in s.execute(select(Email).where(Email.conversation_id == e.conversation_id,
                                                             Email.id != e.id)
                                         .order_by(Email.received_at)).scalars()]
        booking = s.get(Booking, e.booking_id) if e.booking_id else None
        data = {"id": e.id, "subject": e.subject, "sender": e.sender, "recipients": e.recipients,
                "received": e.received_at, "direction": e.direction, "folder": e.folder, "category": e.category,
                "status": e.match_status, "score": e.match_score, "forwarder": e.forwarder,
                "conversation_id": e.conversation_id, "body": e.body_excerpt, "conflicts": e.conflicts or {},
                "applied_by": e.applied_by, "applied_at": e.applied_at,
                "booking": {"id": booking.id, "label": booking.label, "status": booking.status} if booking else None}
        can_review = p.can("email.review") and e.match_status not in ("APPLIED", "IGNORED")
        options = _shipment_options(s, p) if can_review else []
    return render(request, "email_detail.html", e=data, fields=fields, cands=cands, atts=atts, thread=thread,
                  can_review=can_review, options=options, categories=CATEGORIES, cat_labels=CATEGORY_LABELS,
                  msg=msg, err=err)


@router.post("/emails/{email_id}/apply")
async def apply_email(request: Request, email_id: int):
    p = current(request)
    if not p.can("email.review"):
        raise PermissionDenied()
    form = await request.form()
    codes = list(dict.fromkeys(str(c).strip() for c in [*form.getlist("code"), form.get("extra_code") or ""]
                               if str(c).strip()))
    category = str(form.get("category") or "")
    with session_scope() as s:
        e = _get(s, p, email_id)
        if e.match_status in ("APPLIED", "IGNORED"):
            return _back(f"/emails/{email_id}", err="Email này đã được xử lý")
        for c in codes:
            sh = svc.get_visible(s, p, c)
            if not svc.can_edit(p, sh):
                raise PermissionDenied(f"Lô {sh.jo} không được giao cho bạn")
        choices = {k: str(form.get(f"choice_{k}")) for k in (e.conflicts or {}) if form.get(f"choice_{k}")}
        try:
            EmailProcessor(engine()).apply(s, e, codes, actor=p.name, category=category or None,
                                           choices=choices or None, today=dt.date.today())
        except (EmailError, ValueError) as exc:
            s.rollback()
            return _back(f"/emails/{email_id}", err=str(exc))
    return _back(f"/emails/{email_id}", msg="Đã áp dụng vào booking / lô hàng - lần sau email cùng Conversation "
                                             "sẽ tự ghép")


@router.post("/emails/{email_id}/ignore")
def ignore_email(request: Request, email_id: int):
    p = current(request)
    if not p.can("email.review"):
        raise PermissionDenied()
    with session_scope() as s:
        e = _get(s, p, email_id)
        EmailProcessor(engine()).ignore(s, e, actor=p.name)
    return _back("/emails", msg="Đã bỏ qua email")


@router.get("/emails/{email_id}/files/{att_id}")
def download_attachment(request: Request, email_id: int, att_id: int):
    p = current(request)
    with session_scope() as s:
        e = _get(s, p, email_id)
        a = s.get(EmailAttachment, att_id)
        if a is None or a.email_id != e.id or not a.stored_path or not Path(a.stored_path).exists():
            raise svc.NotFound("Không tìm thấy file")
        path, name = a.stored_path, a.filename
    return FileResponse(path, filename=name)
