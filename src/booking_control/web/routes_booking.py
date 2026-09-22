"""Booking tach rieng khoi lo hang (SRSv5 muc 7-18).

    /bookings                    Danh sach booking (loc trang thai / forwarder / tim so booking, JO)
    /bookings/new                Tao booking cho 1 hoac nhieu lo (gop), moi lo co the ghi so luong (split)
    /bookings/{id}               Chi tiet: lo hang + so luong phan bo, lich su, email, chuoi rebook
    POST /bookings/{id}/update   Sua Booking No / ETD / Vessel / Cut-off... -> day sang lo hang
    POST /bookings/{id}/status   Xac nhan / Hold / Giai toa / Tre / Xin huy / Huy / Tau chay / Hoan tat
    POST /bookings/{id}/rebook   Huy booking cu + tao booking moi tro ve booking cu
    POST /bookings/{id}/link     Them lo / doi so luong phan bo;  /unlink  bo lo khoi booking
    POST /shipments/{code}/executions   Ghi nhan xuat hang (mot phan hoac du)

Nguoi dung chi thao tac booking co lo duoc giao; Quan ly / Admin: tat ca; Viewer / Auditor: chi xem.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select

from ..core import booking as bk
from ..db import session_scope
from ..db.models import Shipment
from ..db.models_v3 import Booking, BookingShipment, Email, ShipmentEvent, ShipmentExecution
from ..services import booking_service as bs
from ..services import shipment_service as svc
from .deps import PermissionDenied, current, engine, render

router = APIRouter()

STATUS_ACTIONS = {
    "CONFIRMED": "Xác nhận", "BOOKED": "Đã book", "PENDING": "Chờ xác nhận", "AMENDED": "Đã điều chỉnh",
    "ON_HOLD": "Hold", "DELAYED": "Trễ lịch", "CANCEL_REQUESTED": "Xin hủy", "CANCELLED": "Hủy booking",
    "PARTIALLY_SHIPPED": "Xuất một phần", "DEPARTED": "Tàu đã chạy", "COMPLETED": "Hoàn tất",
    "REBOOK_REQUESTED": "Cần rebook",
}
CANCEL_LIKE = {"CANCELLED", "CANCEL_REQUESTED", "ON_HOLD", "REBOOK_REQUESTED"}


def _back(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if msg:
        url += f"{sep}msg={quote(msg)}"
    elif err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url, status_code=303)


def _linked(s, b: Booking) -> list[tuple[BookingShipment, Shipment | None]]:
    links = bs.links_of(s, b)
    ships = {sh.shipment_code: sh for sh in s.execute(select(Shipment).where(
        Shipment.shipment_code.in_([x.shipment_code for x in links] or ["-"]))).scalars()}
    return [(x, ships.get(x.shipment_code)) for x in links]


def _access(s, p, b: Booking) -> tuple[bool, bool]:
    """(xem duoc, sua duoc) booking - theo cac lo cua booking."""
    ships = [sh for _, sh in _linked(s, b) if sh is not None]
    view = p.can("shipments.view_all") or any(svc.can_view(p, sh) for sh in ships)
    edit = p.can("booking.edit") and any(svc.can_edit(p, sh) for sh in ships)
    return view, edit


def _get(s, p, booking_id: int, *, edit: bool = False) -> Booking:
    b = s.get(Booking, booking_id)
    if b is None:
        raise svc.NotFound("Không tìm thấy booking")
    view, can_edit = _access(s, p, b)
    if not view:
        raise svc.NotFound("Không tìm thấy booking")
    if edit and not can_edit:
        raise PermissionDenied("Bạn chỉ sửa được booking của lô được giao cho mình")
    return b


def _fields(form: dict) -> dict:
    return {k: form.get(k) for k in bs.EDITABLE_FIELDS if k in form}


def _after_change(s, codes: list[str]) -> None:
    today = dt.date.today()
    bs.refresh(s, engine(), codes, today)
    ships = list(s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes or ["-"]))).scalars())
    bs.allocation_warnings(s, ships)


def booking_row(b: Booking, links: list[tuple[BookingShipment, Shipment | None]]) -> dict:
    return {
        "id": b.id, "label": b.label, "booking_no": b.booking_no, "status": b.status,
        "status_label": bk.STATUS_LABELS.get(b.status, b.status), "forwarder": b.forwarder, "mode": b.mode,
        "vessel": b.vessel, "voyage": b.voyage, "etd": b.etd, "eta": b.eta, "cy_cutoff": b.cy_cutoff,
        "si_vgm_cutoff": b.si_vgm_cutoff, "closing_time": b.closing_time, "container_plan": b.container_plan,
        "port": b.port, "source": b.source, "updated_at": b.updated_at, "created_by": b.created_by,
        "hold_reason": b.hold_reason, "hold_review_date": b.hold_review_date, "cancel_reason": b.cancel_reason,
        "original_booking_id": b.original_booking_id, "replacement_booking_id": b.replacement_booking_id,
        "conversation_id": b.conversation_id, "remark": b.remark, "booking_date": b.booking_date,
        "version": b.version,
        "shipments": [{"code": x.shipment_code, "jo": sh.jo if sh else x.shipment_code,
                       "buyer": (sh.buyer_code or sh.buyer_raw) if sh else "", "qty": x.allocated_qty,
                       "ship_qty": (sh.final_export_qty or sh.order_qty) if sh else None,
                       "owner": sh.assigned_to if sh else "", "exfty": sh.exfty_date if sh else None}
                      for x, sh in links],
    }


# ---------------------------------------------------------------------------
# Danh sach
# ---------------------------------------------------------------------------


@router.get("/bookings", response_class=HTMLResponse)
def bookings_page(request: Request, status: str = "ACTIVE", q: str = "", forwarder: str = "", msg: str = "",
                  err: str = ""):
    p = current(request)
    with session_scope() as s:
        stmt = (select(Booking).join(BookingShipment, BookingShipment.booking_id == Booking.id)
                .join(Shipment, Shipment.shipment_code == BookingShipment.shipment_code))
        if not p.can("shipments.view_all"):
            stmt = stmt.where(or_(Shipment.assigned_to == p.name, Shipment.backup_owner == p.name))
        if status == "ACTIVE":
            stmt = stmt.where(Booking.status.not_in(tuple(bk.CLOSED)))
        elif status == "ATTENTION":
            stmt = stmt.where(Booking.status.in_(("REQUESTED", "PENDING", "ON_HOLD", "DELAYED", "CANCEL_REQUESTED",
                                                  "REBOOK_REQUESTED")))
        elif status and status != "ALL":
            stmt = stmt.where(Booking.status == status)
        if forwarder:
            stmt = stmt.where(Booking.forwarder == forwarder)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Booking.booking_no.ilike(like), Booking.vessel.ilike(like),
                                  Shipment.jo.ilike(like), Shipment.shipment_no.ilike(like)))
        found = list(dict.fromkeys(s.execute(stmt.order_by(Booking.etd.is_(None), Booking.etd, Booking.id.desc())
                                             .limit(1500)).scalars()))[:500]
        links: dict[int, list] = {}
        all_links = s.execute(select(BookingShipment).where(
            BookingShipment.booking_id.in_([b.id for b in found] or [-1]))).scalars().all()
        ships = {sh.shipment_code: sh for sh in s.execute(select(Shipment).where(
            Shipment.shipment_code.in_([x.shipment_code for x in all_links] or ["-"]))).scalars()}
        for x in all_links:
            links.setdefault(x.booking_id, []).append((x, ships.get(x.shipment_code)))
        rows = [booking_row(b, links.get(b.id, [])) for b in found]
        forwarders = sorted({f for (f,) in s.execute(select(Booking.forwarder).distinct()).all() if f})
    return render(request, "bookings.html", rows=rows, f={"status": status, "q": q, "forwarder": forwarder},
                  statuses=bk.BOOKING_STATUSES_ALL, labels=bk.STATUS_LABELS, forwarders=forwarders,
                  msg=msg, err=err)


# ---------------------------------------------------------------------------
# Tao
# ---------------------------------------------------------------------------


def _shipment_options(s, p) -> list[dict]:
    stmt = svc.visible_stmt(p).where(Shipment.effective_status != "CANCELLED")
    if not p.is_manager:
        stmt = stmt.where(or_(Shipment.assigned_to == p.name, Shipment.backup_owner == p.name))
    return [{"code": sh.shipment_code, "label": f"{sh.jo or sh.shipment_code} · {sh.buyer_code or sh.buyer_raw} · "
             f"{sh.exfty_date:%d/%m}" if sh.exfty_date else f"{sh.jo or sh.shipment_code}",
             "qty": sh.final_export_qty or sh.order_qty}
            for sh in s.execute(svc.order_like_followup(stmt).limit(3000)).scalars()]


@router.get("/bookings/new", response_class=HTMLResponse)
def new_booking(request: Request, code: str = "", err: str = ""):
    p = current(request)
    if not p.can("booking.edit"):
        raise PermissionDenied()
    with session_scope() as s:
        pre = []
        for c in [x for x in code.split(",") if x]:
            sh = svc.get_visible(s, p, c)
            pre.append({"code": sh.shipment_code, "jo": sh.jo, "qty": None, "ship_qty": sh.final_export_qty or sh.order_qty,
                        "fields": {"etd": sh.etd, "vessel": sh.vessel, "forwarder": sh.forwarder,
                                   "mode": sh.ship_mode, "port": sh.port,
                                   "container_plan": sh.container_type_user}})
        options = _shipment_options(s, p)
    base = pre[0]["fields"] if pre else {}
    return render(request, "booking_new.html", pre=pre, options=options, base=base, err=err,
                  statuses=("REQUESTED", "BOOKED", "CONFIRMED"), labels=bk.STATUS_LABELS)


@router.post("/bookings/new")
async def create_booking(request: Request):
    p = current(request)
    if not p.can("booking.edit"):
        raise PermissionDenied()
    form = await request.form()
    codes = [str(x).strip() for x in form.getlist("code")]
    qtys = [str(x).strip() for x in form.getlist("qty")]
    items: list[tuple[str, float | None]] = []
    for i, c in enumerate(codes):
        if not c:
            continue
        q = qtys[i] if i < len(qtys) else ""
        try:
            items.append((c, float(q.replace(",", "")) if q else None))
        except ValueError:
            return _back("/bookings/new", err=f"Số lượng không hợp lệ: {q}")
    status = str(form.get("status") or "REQUESTED")
    with session_scope() as s:
        for c, _ in items:
            sh = svc.get_visible(s, p, c)
            if not svc.can_edit(p, sh):
                raise PermissionDenied(f"Lô {sh.jo} không được giao cho bạn")
        try:
            b = bs.create_booking(s, shipments=items, fields=_fields(dict(form)), status=status, source="USER",
                                  actor=p.name)
        except (bs.BookingError, ValueError) as exc:
            return _back("/bookings/new?code=" + ",".join(c for c, _ in items), err=str(exc))
        s.flush()
        bs.push_to_shipments(s, engine(), b, actor=p.name, source="BOOKING", override_user=True)
        _after_change(s, [c for c, _ in items])
        bid = b.id
    return _back(f"/bookings/{bid}", msg="Đã tạo booking")


# ---------------------------------------------------------------------------
# Chi tiet + thao tac
# ---------------------------------------------------------------------------


@router.get("/bookings/{booking_id}", response_class=HTMLResponse)
def booking_detail(request: Request, booking_id: int, msg: str = "", err: str = ""):
    p = current(request)
    with session_scope() as s:
        try:
            b = _get(s, p, booking_id)
        except svc.NotFound:
            return render(request, "error.html", status_code=404, message="Không tìm thấy booking")
        _, can_edit = _access(s, p, b)
        links = _linked(s, b)
        row = booking_row(b, links)
        codes = [x.shipment_code for x, _ in links]
        summaries = bs.summaries(s, [sh for _, sh in links if sh is not None])
        for r in row["shipments"]:
            sm = summaries.get(r["code"])
            if sm:
                r["allocation"], r["allocated"], r["missing"] = sm[0].allocation, sm[0].allocated_qty, sm[0].missing_qty
                r["shipped"] = sm[1]
        chain = []
        cur = b
        seen = {b.id}
        while cur.original_booking_id and cur.original_booking_id not in seen:
            cur = s.get(Booking, cur.original_booking_id)
            if cur is None:
                break
            seen.add(cur.id)
            chain.append({"id": cur.id, "label": cur.label, "status": cur.status})
        events = [{"ts": e.ts, "type": e.event_type, "code": e.shipment_code, "source": e.source, "actor": e.actor,
                   "old": e.old_value, "new": e.new_value, "note": e.note, "email_id": e.email_id}
                  for e in s.execute(select(ShipmentEvent).where(ShipmentEvent.booking_id == b.id)
                                     .order_by(ShipmentEvent.id.desc()).limit(200)).scalars()]
        emails = [{"id": e.id, "subject": e.subject, "received": e.received_at, "category": e.category,
                   "status": e.match_status, "direction": e.direction}
                  for e in s.execute(select(Email).where(
                      or_(Email.booking_id == b.id, Email.conversation_id == b.conversation_id)
                      if b.conversation_id else Email.booking_id == b.id)
                      .order_by(Email.received_at.desc()).limit(50)).scalars()]
        execs = [{"code": x.shipment_code, "date": x.ship_date, "qty": x.shipped_qty, "cont": x.container_no,
                  "seal": x.seal_no, "by": x.created_by, "note": x.note}
                 for x in s.execute(select(ShipmentExecution).where(ShipmentExecution.booking_id == b.id)
                                    .order_by(ShipmentExecution.ship_date)).scalars()]
        allowed = bk.TRANSITIONS.get(b.status, set()) - {b.status, "REBOOKED"}
        nxt = [x for x in bk.ACTION_ORDER if x in allowed]
        options = _shipment_options(s, p) if can_edit else []
    actions = [(st, STATUS_ACTIONS.get(st, st)) for st in nxt
               if st not in CANCEL_LIKE or p.can("booking.cancel")]
    return render(request, "booking_detail.html", b=row, chain=chain, events=events, emails=emails,
                  execs=execs, actions=actions, can_edit=can_edit, can_cancel=can_edit and p.can("booking.cancel"),
                  options=options, codes=codes, labels=bk.STATUS_LABELS, msg=msg, err=err)


@router.post("/bookings/{booking_id}/update")
async def update_booking(request: Request, booking_id: int):
    p = current(request)
    form = dict(await request.form())
    with session_scope() as s:
        b = _get(s, p, booking_id, edit=True)
        if form.get("version") and str(b.version) != str(form["version"]):
            return _back(f"/bookings/{booking_id}", err="Booking vừa được người khác / email cập nhật - "
                                                        "tải lại trang rồi sửa lại")
        try:
            changed = bs.update_fields(s, b, _fields(form), actor=p.name, source="USER")
        except bs.BookingError as exc:
            return _back(f"/bookings/{booking_id}", err=str(exc))
        if changed:
            bs.push_to_shipments(s, engine(), b, actor=p.name, source="BOOKING", override_user=True)
            _after_change(s, bs.shipment_codes_of(s, b))
    return _back(f"/bookings/{booking_id}", msg=f"Đã lưu {len(changed)} thay đổi" if changed else "Không có gì thay đổi")


@router.post("/bookings/{booking_id}/status")
def change_status(request: Request, booking_id: int, new_status: str = Form(...), reason: str = Form("")):
    p = current(request)
    if new_status in CANCEL_LIKE and not p.can("booking.cancel"):
        raise PermissionDenied()
    with session_scope() as s:
        b = _get(s, p, booking_id, edit=True)
        if new_status in ("CANCELLED", "ON_HOLD") and not reason.strip():
            return _back(f"/bookings/{booking_id}", err="Nhập lý do hủy / hold")
        try:
            bs.set_status(s, b, new_status, actor=p.name, source="USER", reason=reason.strip(),
                          hold_days=engine().settings.deadlines.hold_reminder_days)
        except bs.BookingError as exc:
            return _back(f"/bookings/{booking_id}", err=str(exc))
        _after_change(s, bs.shipment_codes_of(s, b))
    return _back(f"/bookings/{booking_id}", msg=f"Booking -> {bk.STATUS_LABELS.get(new_status, new_status)}")


@router.post("/bookings/{booking_id}/rebook")
async def rebook(request: Request, booking_id: int):
    p = current(request)
    if not p.can("booking.cancel"):
        raise PermissionDenied()
    form = dict(await request.form())
    with session_scope() as s:
        b = _get(s, p, booking_id, edit=True)
        if b.status == "REBOOKED" or b.replacement_booking_id:
            return _back(f"/bookings/{booking_id}", err="Booking này đã được rebook")
        try:
            new = bs.rebook(s, b, _fields(form), actor=p.name, source="USER",
                            reason=str(form.get("reason") or "Rebook"))
        except bs.BookingError as exc:
            return _back(f"/bookings/{booking_id}", err=str(exc))
        s.flush()
        bs.push_to_shipments(s, engine(), new, actor=p.name, source="BOOKING", override_user=True)
        _after_change(s, bs.shipment_codes_of(s, new))
        nid = new.id
    return _back(f"/bookings/{nid}", msg="Đã rebook - booking cũ giữ nguyên trong lịch sử")


@router.post("/bookings/{booking_id}/link")
def link(request: Request, booking_id: int, code: str = Form(...), qty: str = Form("")):
    p = current(request)
    with session_scope() as s:
        b = _get(s, p, booking_id, edit=True)
        sh = svc.get_visible(s, p, code.strip())
        if not svc.can_edit(p, sh):
            raise PermissionDenied(f"Lô {sh.jo} không được giao cho bạn")
        try:
            q = float(qty.replace(",", "")) if qty.strip() else None
            bs.link_shipment(s, b, sh.shipment_code, q, actor=p.name)
        except (bs.BookingError, ValueError) as exc:
            return _back(f"/bookings/{booking_id}", err=str(exc))
        s.flush()
        _after_change(s, bs.shipment_codes_of(s, b))
    return _back(f"/bookings/{booking_id}", msg="Đã cập nhật lô / số lượng")


@router.post("/bookings/{booking_id}/unlink")
def unlink(request: Request, booking_id: int, code: str = Form(...)):
    p = current(request)
    with session_scope() as s:
        b = _get(s, p, booking_id, edit=True)
        try:
            bs.unlink_shipment(s, b, code, actor=p.name)
        except bs.BookingError as exc:
            return _back(f"/bookings/{booking_id}", err=str(exc))
        s.flush()
        _after_change(s, [code, *bs.shipment_codes_of(s, b)])
    return _back(f"/bookings/{booking_id}", msg="Đã bỏ lô khỏi booking")


@router.post("/shipments/{code}/executions")
def record_execution(request: Request, code: str, qty: str = Form(...), ship_date: str = Form(...),
                     booking_id: str = Form(""), container: str = Form(""), seal: str = Form(""),
                     note: str = Form("")):
    p = current(request)
    if not p.can("execution.record"):
        raise PermissionDenied()
    with session_scope() as s:
        sh = svc.get_visible(s, p, code)
        if not svc.can_edit(p, sh):
            raise PermissionDenied()
        try:
            bs.record_execution(s, sh, qty=float(qty.replace(",", "")), ship_date=dt.date.fromisoformat(ship_date),
                                actor=p.name, booking_id=int(booking_id) if booking_id else None,
                                container=container, seal=seal, note=note)
        except (bs.BookingError, ValueError) as exc:
            return _back(f"/shipments/{code}#xuat-hang", err=str(exc))
        _after_change(s, [code])
    return _back(f"/shipments/{code}", msg="Đã ghi nhận xuất hàng")
