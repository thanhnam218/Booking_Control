"""Nghiep vu Booking: tao, xac nhan, dieu chinh, hold, huy, rebook, split / gop, xuat mot phan.

Dung chung cho trang Booking, email Outlook va dong bo tu Excel / luoi Follow Up.
Khong xoa booking (BR-008); rebook tao booking moi (BR-009); moi thay doi -> shipment_events
+ audit_logs; thong bao cho nguoi phu trach lo.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_audit, raise_exception
from ..core import booking as bk
from ..core.normalize import clean_text, norm_key
from ..db.models import Notification, Shipment, utcnow
from ..db.models_v3 import Booking, BookingShipment, ShipmentEvent, ShipmentExecution

EDITABLE_FIELDS = ("booking_no", "forwarder", "mode", "container_plan", "vessel", "voyage", "etd", "eta",
                   "booking_date", "cy_cutoff", "si_vgm_cutoff", "closing_time", "port", "remark")
DATE_FIELDS = ("etd", "eta", "booking_date")
EVENT_OF_FIELD = {"etd": "ETD_CHANGED", "eta": "ETA_CHANGED", "vessel": "VESSEL_CHANGED",
                  "cy_cutoff": "CUTOFF_CHANGED", "si_vgm_cutoff": "CUTOFF_CHANGED",
                  "closing_time": "CUTOFF_CHANGED", "booking_no": "BOOKING_NO_CHANGED"}
EVENT_OF_STATUS = {"REQUESTED": "BOOKING_REQUESTED", "BOOKED": "BOOKED", "CONFIRMED": "BOOKING_CONFIRMED",
                   "AMENDED": "BOOKING_AMENDED", "ON_HOLD": "BOOKING_HOLD", "DELAYED": "BOOKING_DELAYED",
                   "CANCEL_REQUESTED": "CANCEL_REQUESTED", "CANCELLED": "BOOKING_CANCELLED",
                   "REBOOK_REQUESTED": "REBOOK_REQUESTED", "REBOOKED": "BOOKING_REBOOKED",
                   "PARTIALLY_SHIPPED": "PARTIAL_SHIPMENT", "DEPARTED": "DEPARTED", "COMPLETED": "BOOKING_COMPLETED"}
# Booking -> 47 cot Follow Up (cot auto: true trong followup_template.yaml)
FOLLOWUP_FIELDS = {"booking_no": "BookingNo", "etd": "ETD", "eta": "ETA", "vessel": "Vessel",
                   "booking_date": "BookingDate", "closing_time": "ClosingTime"}


class BookingError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Su kien + thong bao
# ---------------------------------------------------------------------------


def log_event(s: Session, event_type: str, *, shipment_code: str = "", booking_id: int | None = None,
              email_id: int | None = None, source: str = "USER", actor: str = "", old: Any = "",
              new: Any = "", note: str = "", payload: dict[str, Any] | None = None) -> ShipmentEvent:
    ev = ShipmentEvent(event_type=event_type, shipment_code=shipment_code, booking_id=booking_id,
                       email_id=email_id, source=source, actor=actor, old_value="" if old is None else str(old),
                       new_value="" if new is None else str(new), note=note, payload=payload or {})
    s.add(ev)
    return ev


def _notify_owners(s: Session, codes: list[str], *, category: str, severity: str, title: str, body: str,
                   dedup: str) -> None:
    ships = s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes or ["-"]))).scalars().all()
    recipients = {x for sh in ships for x in (sh.assigned_to, sh.backup_owner) if x} or {""}
    for r in recipients:
        key = f"{dedup}:{r}"
        if s.execute(select(Notification.id).where(Notification.dedup_key == key)).first():
            continue
        s.add(Notification(category=category, severity=severity, title=title, body=body, recipient=r,
                           shipment_code=codes[0] if len(codes) == 1 else "", dedup_key=key,
                           context={"shipments": codes}, delivered_at=utcnow()))


# ---------------------------------------------------------------------------
# Doc
# ---------------------------------------------------------------------------


def links_of(s: Session, booking: Booking) -> list[BookingShipment]:
    return list(s.execute(select(BookingShipment).where(BookingShipment.booking_id == booking.id)).scalars())


def shipment_codes_of(s: Session, booking: Booking) -> list[str]:
    return [link.shipment_code for link in links_of(s, booking)]


def bookings_for(s: Session, codes: list[str]) -> dict[str, list[tuple[Booking, BookingShipment]]]:
    out: dict[str, list[tuple[Booking, BookingShipment]]] = defaultdict(list)
    if not codes:
        return out
    rows = s.execute(
        select(Booking, BookingShipment).join(BookingShipment, BookingShipment.booking_id == Booking.id)
        .where(BookingShipment.shipment_code.in_(codes))
    ).all()
    for b, link in rows:
        out[link.shipment_code].append((b, link))
    return out


def shipped_totals(s: Session, codes: list[str]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    if not codes:
        return out
    for e in s.execute(select(ShipmentExecution).where(ShipmentExecution.shipment_code.in_(codes))).scalars():
        out[e.shipment_code] += float(e.shipped_qty or 0)
    return out


def summaries(s: Session, ships: list[Shipment]) -> dict[str, tuple[bk.ShipmentBookingSummary, float]]:
    """Tong hop booking + so luong da xuat cho nhieu lo (1-2 truy van)."""
    codes = [sh.shipment_code for sh in ships]
    by_code = bookings_for(s, codes)
    shipped = shipped_totals(s, codes)
    out = {}
    for sh in ships:
        views = [bk.BookingView(id=b.id, status=b.status, booking_no=b.booking_no, etd=b.etd,
                                created_at=b.created_at, allocated_qty=link.allocated_qty)
                 for b, link in by_code.get(sh.shipment_code, [])]
        out[sh.shipment_code] = (bk.summarize(views, sh.final_export_qty or sh.order_qty),
                                 shipped.get(sh.shipment_code, 0.0))
    return out


# ---------------------------------------------------------------------------
# Ghi
# ---------------------------------------------------------------------------


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (fields or {}).items():
        if k not in EDITABLE_FIELDS:
            continue
        if k in DATE_FIELDS:
            if isinstance(v, dt.datetime):
                v = v.date()
            elif isinstance(v, str):
                v = v.strip()
                v = dt.date.fromisoformat(v[:10]) if v else None
        else:
            v = clean_text(v)
            if k == "booking_no":
                v = v.upper()
        out[k] = v
    return out


def create_booking(
    s: Session, *, shipments: list[tuple[str, float | None]], fields: dict[str, Any], status: str = "REQUESTED",
    source: str = "USER", actor: str = "", conversation_id: str = "", email_id: int | None = None,
    original: Booking | None = None,
) -> Booking:
    f = _clean_fields(fields)
    if status not in bk.BOOKING_STATUSES_ALL:
        raise BookingError(f"Trang thai booking khong hop le: {status}")
    codes = [c for c, _ in shipments]
    found = {sh.shipment_code for sh in s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes or ["-"])))
             .scalars()}
    missing = [c for c in codes if c not in found]
    if missing or not codes:
        raise BookingError(f"Khong tim thay lo hang: {', '.join(missing) or '(chua chon lo)'}")
    if f.get("booking_no"):
        dup = s.execute(select(Booking).where(Booking.booking_no == f["booking_no"],
                                              Booking.status.not_in(tuple(bk.CLOSED)))).scalars().first()
        if dup is not None:
            raise BookingError(f"Booking {f['booking_no']} da ton tai (#{dup.id}) - hay them lo vao booking do")
    b = Booking(status=status, source=source, created_by=actor, conversation_id=conversation_id,
                original_booking_id=original.id if original else None, **f)
    s.add(b)
    s.flush()
    for code, qty in shipments:
        s.add(BookingShipment(booking_id=b.id, shipment_code=code, allocated_qty=qty))
        log_event(s, "REBOOKED" if original else "BOOKING_CREATED", shipment_code=code, booking_id=b.id,
                  email_id=email_id, source=source, actor=actor, new=f"{b.label} [{status}]",
                  note=f"thay cho {original.label}" if original else "")
    log_audit(s, "BOOKING_CREATED", actor_type="USER" if source == "USER" else "AUTO", actor_name=actor or source,
              entity="booking", entity_id=str(b.id), new_value=f"{b.label} {status}", note=",".join(codes))
    return b


def update_fields(s: Session, b: Booking, fields: dict[str, Any], *, actor: str, source: str = "USER",
                  email_id: int | None = None) -> dict[str, tuple[Any, Any]]:
    f = _clean_fields(fields)
    changed: dict[str, tuple[Any, Any]] = {}
    codes = shipment_codes_of(s, b)
    if f.get("booking_no") and f["booking_no"] != b.booking_no:
        dup = s.execute(select(Booking).where(Booking.booking_no == f["booking_no"], Booking.id != b.id,
                                              Booking.status.not_in(tuple(bk.CLOSED)))).scalars().first()
        if dup is not None:
            raise BookingError(f"Booking {f['booking_no']} da ton tai (#{dup.id})")
    for k, v in f.items():
        old = getattr(b, k)
        if v in (None, "") and k not in ("remark",):
            continue  # email khong nhac toi truong nay -> giu nguyen
        if old == v:
            continue
        setattr(b, k, v)
        changed[k] = (old, v)
        ev = EVENT_OF_FIELD.get(k)
        if ev:
            for code in codes or [""]:
                log_event(s, ev, shipment_code=code, booking_id=b.id, email_id=email_id, source=source,
                          actor=actor, old=old, new=v, note=b.label)
        log_audit(s, "BOOKING_FIELD", actor_type="USER" if source == "USER" else "AUTO", actor_name=actor or source,
                  entity="booking", entity_id=str(b.id), field=k, old_value=old, new_value=v, source=source)
    if changed:
        b.version = (b.version or 1) + 1
        b.updated_at = utcnow()
        if "etd" in changed and changed["etd"][0]:
            old, new = changed["etd"]
            _notify_owners(s, codes, category="ETD_CHANGED", severity="WARNING",
                           title=f"{b.label}: ETD doi {old:%d/%m} -> {new:%d/%m}",
                           body="Kiem tra lai lich dong hang / cut-off", dedup=f"BK{b.id}:ETD:{new}")
    return changed


def set_status(s: Session, b: Booking, new: str, *, actor: str, source: str = "USER", reason: str = "",
               email_id: int | None = None, hold_days: int = 3, force: bool = False) -> None:
    old = b.status
    if old == new:
        return
    if not force:
        try:
            bk.check_transition(old, new)
        except bk.BookingRuleError as exc:
            raise BookingError(str(exc)) from exc
    b.status = new
    b.version = (b.version or 1) + 1
    b.updated_at = utcnow()
    if new == "ON_HOLD":
        b.hold_reason = reason or b.hold_reason
        b.hold_review_date = dt.date.today() + dt.timedelta(days=hold_days)
    if new == "CANCELLED":
        b.cancel_reason = reason or b.cancel_reason
    codes = shipment_codes_of(s, b)
    ev = "BOOKING_RELEASED" if old == "ON_HOLD" and new in ("CONFIRMED", "AMENDED") else EVENT_OF_STATUS.get(new, new)
    for code in codes or [""]:
        log_event(s, ev, shipment_code=code, booking_id=b.id, email_id=email_id, source=source, actor=actor,
                  old=old, new=new, note=reason or b.label)
    log_audit(s, "BOOKING_STATUS", actor_type="USER" if source == "USER" else "AUTO", actor_name=actor or source,
              entity="booking", entity_id=str(b.id), field="status", old_value=old, new_value=new, note=reason)
    if new == "CANCELLED":
        _notify_owners(s, codes, category="BOOKING_CANCELLED", severity="CRITICAL",
                       title=f"Booking {b.label} bi huy - can rebook hoac gop lo sau",
                       body=reason or "Mo trang Booking > Rebook", dedup=f"BK{b.id}:CANCELLED")
    elif new == "ON_HOLD":
        _notify_owners(s, codes, category="BOOKING_HOLD", severity="WARNING",
                       title=f"Booking {b.label} dang HOLD", body=reason or "", dedup=f"BK{b.id}:HOLD:{b.version}")
    elif new == "DELAYED":
        _notify_owners(s, codes, category="BOOKING_DELAYED", severity="WARNING",
                       title=f"Booking {b.label} bi tre lich", body=reason or "", dedup=f"BK{b.id}:DELAY:{b.version}")


def rebook(s: Session, old: Booking, fields: dict[str, Any], *, actor: str, source: str = "USER",
           email_id: int | None = None, reason: str = "") -> Booking:
    """Huy booking cu (neu chua huy) + tao booking moi tro ve booking cu. Lich su giu nguyen."""
    if old.status not in ("CANCELLED", "CANCEL_REQUESTED", "REBOOK_REQUESTED"):
        set_status(s, old, "CANCELLED", actor=actor, source=source, reason=reason or "Rebook", email_id=email_id,
                   force=True)
    links = links_of(s, old)
    base = {k: getattr(old, k) for k in ("forwarder", "mode", "container_plan", "port")}
    base.update({k: v for k, v in (fields or {}).items() if v not in (None, "")})
    status = "CONFIRMED" if base.get("booking_no") else "REQUESTED"
    new = create_booking(s, shipments=[(link.shipment_code, link.allocated_qty) for link in links], fields=base,
                         status=status, source=source, actor=actor, conversation_id=old.conversation_id,
                         email_id=email_id, original=old)
    old.replacement_booking_id = new.id
    return new


def link_shipment(s: Session, b: Booking, code: str, qty: float | None, *, actor: str, source: str = "USER") -> None:
    if not s.execute(select(Shipment.id).where(Shipment.shipment_code == code)).first():
        raise BookingError(f"Khong tim thay lo {code}")
    link = s.execute(select(BookingShipment).where(BookingShipment.booking_id == b.id,
                                                   BookingShipment.shipment_code == code)).scalar_one_or_none()
    if link is None:
        s.add(BookingShipment(booking_id=b.id, shipment_code=code, allocated_qty=qty))
        log_event(s, "SHIPMENT_LINKED", shipment_code=code, booking_id=b.id, source=source, actor=actor,
                  new=f"{b.label} qty={qty if qty is not None else 'ca lo'}")
    elif link.allocated_qty != qty:
        log_event(s, "ALLOCATION_CHANGED", shipment_code=code, booking_id=b.id, source=source, actor=actor,
                  old=link.allocated_qty, new=qty)
        link.allocated_qty = qty
    b.version = (b.version or 1) + 1


def unlink_shipment(s: Session, b: Booking, code: str, *, actor: str) -> None:
    link = s.execute(select(BookingShipment).where(BookingShipment.booking_id == b.id,
                                                   BookingShipment.shipment_code == code)).scalar_one_or_none()
    if link is None:
        return
    if len(links_of(s, b)) <= 1:
        raise BookingError("Booking phai con it nhat 1 lo - muon bo thi Huy booking")
    s.delete(link)
    log_event(s, "SHIPMENT_UNLINKED", shipment_code=code, booking_id=b.id, actor=actor, old=b.label)
    b.version = (b.version or 1) + 1


def record_execution(s: Session, sh: Shipment, *, qty: float, ship_date: dt.date, actor: str,
                     booking_id: int | None = None, ctns: float | None = None, cbm: float | None = None,
                     container: str = "", seal: str = "", note: str = "") -> ShipmentExecution:
    if qty <= 0:
        raise BookingError("So luong xuat phai > 0")
    e = ShipmentExecution(shipment_code=sh.shipment_code, booking_id=booking_id, ship_date=ship_date,
                          shipped_qty=qty, shipped_ctns=ctns, shipped_cbm=cbm, container_no=clean_text(container),
                          seal_no=clean_text(seal), note=note, created_by=actor)
    s.add(e)
    s.flush()
    total = shipped_totals(s, [sh.shipment_code]).get(sh.shipment_code, 0.0)
    final = sh.final_export_qty or sh.order_qty
    state = bk.ship_status(total, final)
    log_event(s, "SHIPMENT_COMPLETED" if state == "FULLY_SHIPPED" else "PARTIAL_SHIPMENT",
              shipment_code=sh.shipment_code, booking_id=booking_id, actor=actor,
              new=f"{total:g}/{final:g}" if final else f"{total:g}", note=note or f"xuat {qty:g} ngay {ship_date:%d/%m}")
    return e


# ---------------------------------------------------------------------------
# Booking -> 47 cot Follow Up (USER > AUTOMATION)
# ---------------------------------------------------------------------------

PROTECTED_SRC = ("USER", "MANAGER")


def push_to_shipments(s: Session, engine, b: Booking, *, actor: str, source: str = "OUTLOOK",
                      today: dt.date | None = None, codes: list[str] | None = None,
                      override_user: bool = False) -> list[str]:
    """Ghi Booking No / ETD / ETA / Vessel / Closing vao lo.

    Nguon tu dong (email): o User da sua tay -> giu, bao lai (USER > AUTOMATION).
    override_user=True: chinh nguoi dung vua sua booking tren web -> ghi de (nguoi quyet dinh moi nhat).
    """
    today = today or dt.date.today()
    tmpl = engine.template
    kept: list[str] = []
    codes = codes or shipment_codes_of(s, b)
    now = utcnow().isoformat(timespec="seconds")
    for sh in s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes or ["-"]))).scalars():
        row = dict(sh.row_data or {})
        src = {k: dict(v or {}) for k, v in (sh.field_src or {}).items()}
        changed = []
        for bf, fld in FOLLOWUP_FIELDS.items():
            val = getattr(b, bf)
            if bf == "vessel" and b.voyage and val:
                val = f"{val} {b.voyage}".strip()
            if val in (None, ""):
                continue
            new = tmpl.to_json(fld, val)
            old = row.get(fld)
            if new == old:
                continue
            if not override_user and (src.get(fld) or {}).get("src") in PROTECTED_SRC:
                kept.append(f"{sh.jo}: {fld} giu '{old}' (User da sua), booking bao '{new}'")
                log_event(s, "USER_VALUE_KEPT", shipment_code=sh.shipment_code, booking_id=b.id, source=source,
                          actor=actor, old=old, new=new, note=fld)
                continue
            prev = src.get(fld) or {}
            src[fld] = {"src": source, "by": actor or source.lower(), "at": now,
                        "excel": prev.get("excel", old if sh.origin != "WEB" else None)}
            row[fld] = new
            changed.append((fld, old, new))
        if not changed:
            continue
        values = tmpl.row_from_json(row)
        sh.field_src = src
        engine.apply_values(sh, values, today, pic_default=sh.pic, s=s)
        sh.version = (sh.version or 1) + 1
        sh.updated_at, sh.updated_by = utcnow(), actor or source.lower()
        for fld, old, new in changed:
            log_audit(s, "FIELD_CHANGED", actor_type="AUTO" if source != "USER" else "USER",
                      actor_name=actor or source.lower(), entity="shipment", entity_id=sh.shipment_code,
                      field=fld, old_value=old, new_value=new, source=source.lower(), note=b.label)
    return kept


def refresh(s: Session, engine, codes: list[str], today: dt.date | None = None) -> None:
    """Tinh lai trang thai lo (mau dong) sau khi booking / lan xuat thay doi."""
    today = today or dt.date.today()
    ships = list(s.execute(select(Shipment).where(Shipment.shipment_code.in_(codes or ["-"]))).scalars())
    for sh in ships:
        engine.recompute(s, sh, today)
    # Task tu hoan thanh ngay (vd co booking -> B2, tau chay -> B13), khong cho lan quet sau
    from .workflow_service import ensure_tasks

    s.flush()
    ensure_tasks(s, engine.settings, ships, today)


def sync_from_row(s: Session, sh: Shipment, *, actor: str = "automation", source: str = "EXCEL") -> Booking | None:
    """BOOKING NO go trong Excel / luoi ma chua co booking -> tao / noi booking (khong huy booking khac)."""
    return sync_rows(s, [sh], actor=actor, source=source).get(sh.shipment_code)


def sync_rows(s: Session, ships: list[Shipment], *, actor: str = "automation",
              source: str = "EXCEL") -> dict[str, Booking | None]:
    """Nhieu lo 1 lan (moi lan quet): 2 truy van doc truoc, lo da dong bo thi khong truy van them."""
    rows = [(sh, clean_text(sh.booking_no).upper()) for sh in ships if clean_text(sh.booking_no)]
    if not rows:
        return {}
    by_code = bookings_for(s, [sh.shipment_code for sh, _ in rows])
    nos = sorted({no for _, no in rows})
    by_no: dict[str, list[Booking]] = defaultdict(list)
    for b in s.execute(select(Booking).where(Booking.booking_no.in_(nos))).scalars():
        by_no[b.booking_no].append(b)
    out: dict[str, Booking | None] = {}
    for sh, no in rows:
        mine = by_code.get(sh.shipment_code, [])
        known = next((b for b, _ in mine if norm_key(b.booking_no) == norm_key(no)), None)
        if known is not None:
            out[sh.shipment_code] = known  # da biet (ke ca da huy - khong "hoi sinh" booking da huy)
            continue
        same_no = by_no.get(no, [])
        existing = next((b for b in same_no if b.status not in bk.CLOSED), None)
        if existing is None and same_no:
            out[sh.shipment_code] = None  # so booking nay da bi huy o lo khac -> khong tu tao lai
            continue
        if existing is not None:  # booking gop nhieu lo
            link_shipment(s, existing, sh.shipment_code, None, actor=actor, source=source)
            target = existing
        else:
            target = create_booking(
                s, shipments=[(sh.shipment_code, None)], status="BOOKED", source=source, actor=actor,
                fields={"booking_no": no, "etd": sh.etd, "eta": sh.eta, "vessel": sh.vessel,
                        "booking_date": sh.booking_date, "closing_time": sh.closing_time, "port": sh.port,
                        "mode": sh.ship_mode, "forwarder": sh.forwarder, "container_plan": sh.container_type_user})
            by_no[no].append(target)
        s.flush()
        others = [b for b, link in mine if b.status in bk.ACTIVE and b.id != target.id and link.allocated_qty is None]
        for b in others:
            raise_exception(
                s, "EX019", f"{sh.jo}: BOOKING NO doi {b.label} -> {no}. Neu la rebook hay Huy booking {b.label}",
                shipment_code=sh.shipment_code, value=f"{b.label} -> {no}", source_file=sh.source_file_code,
                source_sheet=sh.source_sheet, source_row=sh.source_row,
                suggested_action="Trang Booking: Huy booking cu (giu lich su) hoac chia so luong (split)",
                dedup_key=f"EX019:{sh.shipment_code}:{b.id}:{no}",
            )
        out[sh.shipment_code] = target
    return out


def allocation_warnings(s: Session, ships: list[Shipment]) -> None:
    """Split booking chua phan bo du so luong -> EX018 (SRSv5 muc 9)."""
    for code, (summary, _shipped) in summaries(s, ships).items():
        if summary.allocation == "PARTIAL":
            raise_exception(
                s, "EX018", f"Booking moi phan bo {summary.allocated_qty:g}, con thieu {summary.missing_qty:g}",
                shipment_code=code, value=summary.allocated_qty, severity="WARNING",
                suggested_action="Trang Booking: them booking / tang so luong phan bo",
                dedup_key=f"EX018:{code}:{summary.allocated_qty}",
            )
        elif summary.allocation == "OVER":
            raise_exception(
                s, "EX018", f"Booking phan bo {summary.allocated_qty:g} vuot so luong lo",
                shipment_code=code, value=summary.allocated_qty, severity="INFO",
                dedup_key=f"EX018:OVER:{code}:{summary.allocated_qty}",
            )
