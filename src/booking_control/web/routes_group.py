"""Export Shipment Group - nhom lo xuat (SRSv5.1 muc 2-4, SRSv5.5 muc 4).

Nhom = BUYER + EX-FACT DATE + DEST + MODE. Trang nay tra loi dung mot cau hoi:

    "Ngay do, buyer do, di cang do, bang duong do - tat ca gom lai thi bao nhieu?"

Tong PCS / CTNS / G.W / CBM, so JO, so GO va container du kien duoc cong san, nhan vien
khong phai cong tay.

Nhom KHONG phai booking (SRSv5.1 muc 3): mot nhom co the ung voi nhieu booking, nen
trang chi hien so booking dang co chu khong tu tao booking.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ..db import session_scope
from ..db.models import Shipment
from ..db.models_v3 import ExportShipmentGroup
from ..services import shipment_service as svc
from .deps import current, render, settings

router = APIRouter()


@router.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request):
    p = current(request)
    today = dt.date.today()
    q = request.query_params
    with session_scope() as s:
        # Chi hien nhom co it nhat mot lo NGUOI NAY duoc xem (phan quyen o backend)
        visible_keys = {
            k for (k,) in s.execute(
                svc.visible_stmt(p).with_only_columns(Shipment.export_group_key).distinct()
            ).all() if k
        }
        stmt = select(ExportShipmentGroup).order_by(
            ExportShipmentGroup.exfty_date, ExportShipmentGroup.buyer_code)
        if q.get("buyer"):
            stmt = stmt.where(ExportShipmentGroup.buyer_code == q["buyer"])
        if q.get("mode"):
            stmt = stmt.where(ExportShipmentGroup.ship_mode == q["mode"])
        if q.get("date_from"):
            stmt = stmt.where(ExportShipmentGroup.exfty_date >= dt.date.fromisoformat(q["date_from"]))
        if q.get("date_to"):
            stmt = stmt.where(ExportShipmentGroup.exfty_date <= dt.date.fromisoformat(q["date_to"]))
        rows = [g for g in s.execute(stmt).scalars() if g.group_key in visible_keys]
        groups = [_as_dict(g, today) for g in rows]
        buyers = sorted({g.buyer_code for g in rows if g.buyer_code})
        modes = sorted({g.ship_mode for g in rows if g.ship_mode})
    totals = {
        "groups": len(groups),
        "shipments": sum(g["shipment_count"] for g in groups),
        "cbm": round(sum(g["total_cbm"] for g in groups), 2),
        "jo": sum(g["jo_count"] for g in groups),
    }
    return render(request, "groups.html", groups=groups, totals=totals, buyers=buyers,
                  modes=modes, filters=dict(q), nav_override="groups")


@router.get("/groups/{key}", response_class=HTMLResponse)
def group_detail(request: Request, key: str):
    p = current(request)
    today = dt.date.today()
    with session_scope() as s:
        group = s.execute(select(ExportShipmentGroup).where(
            ExportShipmentGroup.group_key == key)).scalar_one_or_none()
        if group is None:
            return render(request, "error.html", status_code=404, message="Không tìm thấy nhóm")
        ships = list(s.execute(svc.order_like_followup(
            svc.visible_stmt(p).where(Shipment.export_group_key == key))).scalars())
        if not ships:
            return render(request, "error.html", status_code=404,
                          message="Nhóm này không có lô hàng nào bạn được xem")
        rows = [{
            "code": sh.shipment_code, "jo": sh.jo, "go": sh.go, "no": sh.shipment_no,
            "pcs": sh.final_export_qty if sh.final_export_qty is not None else sh.order_qty,
            "ctns": sh.final_carton_qty, "gw": sh.gross_weight, "cbm": sh.final_cbm,
            "status": sh.effective_status, "biz": svc.business_status(sh),
            "warn": sh.deadline_warning or "", "booking_no": sh.booking_no,
            "assigned_to": sh.assigned_to, "cont_type": sh.container_type_user,
        } for sh in ships]
        data = _as_dict(group, today)
    return render(request, "group_detail.html", g=data, rows=rows,
                  colors=settings().status.colors, nav_override="groups",
                  labels=svc.BUSINESS_STATUS_LABELS)


def _as_dict(g: ExportShipmentGroup, today: dt.date) -> dict:
    days = (g.exfty_date - today).days if g.exfty_date else None
    return {
        "key": g.group_key, "label": g.label, "buyer": g.buyer_code,
        "exfty": g.exfty_date, "dest": g.destination, "mode": g.ship_mode,
        "shipment_count": g.shipment_count, "jo_count": g.jo_count, "go_count": g.go_count,
        "total_pcs": g.total_pcs or 0, "total_ctns": g.total_ctns or 0,
        "total_gw": g.total_gross_weight or 0, "total_cbm": g.total_cbm or 0,
        "missing_cbm": g.missing_cbm, "plan": g.container_plan or {},
        "bookings": g.booking_numbers or [], "details": g.details or {},
        "days": days,
        "deadline": "" if days is None else (f"Quá hạn {-days} ngày" if days < 0
                                             else ("Hôm nay" if days == 0 else f"D-{days}")),
    }


def container_summary(plan: dict) -> str:
    """'2 × 40HC + 1 × 40DC' - dung trong template."""
    qty = (plan or {}).get("quantities") or {}
    parts = [f"{n} × {t}" for t, n in qty.items() if n]
    return " + ".join(parts) or (plan.get("type") or "—")

