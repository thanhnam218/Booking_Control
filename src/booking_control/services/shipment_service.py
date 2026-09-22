"""Doc / sua / tao lo hang tu web theo dung quyen (dung chung cho luoi Follow Up, API, export).

Quy tac (SRS v2 muc 35-36, 46, 53-54, 71):
    - Nguoi dung chi thay & sua lo duoc giao (chinh hoac du phong). Quan ly/Admin thay het.
    - Quyen sua tung cot theo config/followup_template.yaml (Field Ownership Matrix).
    - O sua tay tren web duoc danh dau USER/MANAGER -> Automation va Excel import khong ghi de.
    - Optimistic lock: moi lo co `version`; luu voi version cu -> 409, khong ghi de nguoi khac.
    - Moi thay doi deu vao Audit Log (gia tri cu -> moi, ai sua, luc nao).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from ..audit import log_audit, raise_exception
from ..core.normalize import clean_text, find_keyword, to_canonical_date
from ..core.shipment import group_key, make_shipment_code, shipment_key
from ..db.models import Shipment, utcnow
from ..followup import FollowUpTemplate
from ..web.auth import Principal
from .assignment import assign, auto_assign

MANUAL_SRC = ("USER", "MANAGER")


class ServiceError(ValueError):
    status = 400


class NotFound(ServiceError):
    status = 404


class Forbidden(ServiceError):
    status = 403


# ---------------------------------------------------------------------------
# Pham vi du lieu theo quyen (backend - SRS v2 muc 64)
# ---------------------------------------------------------------------------


def visible_stmt(p: Principal, *, include_archived: bool = False) -> Select:
    stmt = select(Shipment).where(Shipment.is_active.is_(True))
    if not include_archived:
        stmt = stmt.where(Shipment.archived.is_(False))
    if not p.can("shipments.view_all"):
        stmt = stmt.where(or_(Shipment.assigned_to == p.name, Shipment.backup_owner == p.name))
    return stmt


def can_view(p: Principal, sh: Shipment) -> bool:
    return p.can("shipments.view_all") or p.name in (sh.assigned_to or "", sh.backup_owner or "")


def can_edit(p: Principal, sh: Shipment) -> bool:
    if not p.can("shipments.edit"):
        return False
    return p.is_manager or p.name in (sh.assigned_to or "", sh.backup_owner or "")


def get_visible(s: Session, p: Principal, code: str) -> Shipment:
    sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one_or_none()
    if sh is None:
        raise NotFound(f"Không tìm thấy lô hàng {code}")
    if not can_view(p, sh):
        # Khong tiet lo lo hang ngoai quyen ton tai hay khong
        raise NotFound(f"Không tìm thấy lô hàng {code}")
    return sh


def apply_filters(stmt: Select, f: dict[str, Any]) -> Select:
    if f.get("assigned") == "__none__":
        stmt = stmt.where(Shipment.assigned_to == "")
    elif f.get("assigned"):
        stmt = stmt.where(or_(Shipment.assigned_to == f["assigned"], Shipment.backup_owner == f["assigned"]))
    if f.get("status"):
        sts = [x for x in str(f["status"]).split(",") if x]
        stmt = stmt.where(Shipment.effective_status.in_(sts))
    if f.get("buyer"):
        b = str(f["buyer"]).strip()
        stmt = stmt.where(or_(Shipment.buyer_code == b, Shipment.buyer_raw == b))
    if f.get("forwarder"):
        stmt = stmt.where(Shipment.forwarder == f["forwarder"])
    if f.get("mode"):
        stmt = stmt.where(Shipment.ship_mode == str(f["mode"]).upper())
    if f.get("date_from"):
        stmt = stmt.where(Shipment.exfty_date >= _date(f["date_from"]))
    if f.get("date_to"):
        stmt = stmt.where(Shipment.exfty_date <= _date(f["date_to"]))
    if f.get("hide_done"):
        stmt = stmt.where(Shipment.effective_status.not_in(["COMPLETED", "CANCELLED"]))
    if f.get("q"):
        like = f"%{str(f['q']).strip()}%"
        stmt = stmt.where(or_(Shipment.jo.like(like), Shipment.shipment_no.like(like),
                              Shipment.buyer_raw.like(like), Shipment.booking_no.like(like),
                              Shipment.po.like(like), Shipment.style.like(like)))
    return stmt


def _date(v: Any) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def order_like_followup(stmt: Select) -> Select:
    # Giong file Follow Up: theo ngay xuat, roi NO, roi JO
    return stmt.order_by(Shipment.exfty_date.is_(None), Shipment.exfty_date, Shipment.shipment_no,
                         Shipment.jo, Shipment.id)


# ---------------------------------------------------------------------------
# Dong luoi
# ---------------------------------------------------------------------------


def deadline_label(sh: Shipment, today: dt.date) -> str:
    if sh.effective_status in ("COMPLETED", "CANCELLED") or not sh.exfty_date:
        return ""
    days = (sh.exfty_date - today).days
    if days < 0:
        return f"Quá hạn {-days} ngày"
    return "Hôm nay" if days == 0 else f"D-{days}"


def booking_status(sh: Shipment) -> str:
    if sh.cancel_flag:
        return "CANCELLED"
    if sh.hold_flag:
        return "HOLD"
    if sh.booking_no or sh.booking_flag:
        return "BOOKED"
    return "NOT_BOOKED"


def grid_row(sh: Shipment, tmpl: FollowUpTemplate, today: dt.date, color_of) -> dict[str, Any]:
    data = sh.row_data or {}
    src = sh.field_src or {}
    return {
        "code": sh.shipment_code,
        "v": sh.version or 1,
        "cells": [data.get(f) for f in tmpl.fields],
        "status": sh.effective_status or "",
        "auto_status": sh.auto_status or "",
        "color": color_of(sh.effective_status),
        "reason": (sh.status_reason or {}).get("reason", ""),
        "assigned_to": sh.assigned_to or "",
        "backup_owner": sh.backup_owner or "",
        "forwarder": sh.forwarder or "",
        "forwarder_status": sh.forwarder_status or "",
        "deadline": deadline_label(sh, today),
        "origin": sh.origin or "EXCEL",
        "source": f"{sh.source_file_code}/{sh.source_sheet}!{sh.source_row}" if sh.origin != "WEB" else "WEB",
        "manual": sorted(f for f, m in src.items() if (m or {}).get("src") in MANUAL_SRC),
    }


# ---------------------------------------------------------------------------
# Sua o
# ---------------------------------------------------------------------------


@dataclass
class EditResult:
    ok: bool = True
    row: dict[str, Any] | None = None
    errors: dict[str, str] = field(default_factory=dict)
    conflict: bool = False
    changed: list[str] = field(default_factory=list)


def apply_remark_flags(values: dict[str, Any], dictionary, derived: dict[str, Any]) -> None:
    """HOLD / CANCEL trong REMARK (file that khong co cot HOLD). Tinh lai moi lan sua REMARK."""
    remark = values.get("Remark")
    for flag, extra in (("HoldFlag", "HoldReason"), ("CancelFlag", None)):
        explicit = flag in values and flag not in derived
        if explicit:
            continue
        values.pop(flag, None)
        if flag in derived:
            derived.pop(flag, None)
            if extra:
                values.pop(extra, None)
    if not clean_text(remark):
        return
    if "HoldFlag" not in values and dictionary.hold_keywords:
        kw = find_keyword(remark, dictionary.hold_keywords, dictionary.hold_negations)
        if kw:
            values["HoldFlag"] = True
            derived["HoldFlag"] = f"REMARK chua '{kw}'"
            values.setdefault("HoldReason", clean_text(remark))
    if "CancelFlag" not in values and dictionary.cancel_keywords:
        kw = find_keyword(remark, dictionary.cancel_keywords)
        if kw:
            values["CancelFlag"] = True
            derived["CancelFlag"] = f"REMARK chua '{kw}'"


def recompute_formulas(tmpl: FollowUpTemplate, sh: Shipment, values: dict[str, Any],
                       src: dict[str, Any]) -> dict[str, Any]:
    """Tinh lai cot cong thuc sau khi sua - chi cot dang 'song' va khong bi sua tay."""
    before = tmpl.row_from_json(sh.row_data or {})
    live = tmpl.live_formulas(before, always=sh.origin == "WEB")
    manual = {f for f, m in src.items() if (m or {}).get("src") in MANUAL_SRC}
    was_manual = {f for f, m in (sh.field_src or {}).items() if (m or {}).get("src") in MANUAL_SRC}
    live |= (was_manual - manual) & set(tmpl.formula_order)  # vua xoa gia tri tay -> cong thuc chay lai
    skip = manual | (set(tmpl.formula_order) - live)
    return tmpl.compute_formulas(values, skip=skip)


def apply_edits(
    s: Session, engine, p: Principal, sh: Shipment, changes: dict[str, Any], *,
    version: int | None = None, today: dt.date | None = None, reason: str = "",
) -> EditResult:
    today = today or dt.date.today()
    tmpl: FollowUpTemplate = engine.template
    color_of = engine.settings.status_color
    if not can_edit(p, sh):
        raise Forbidden("Bạn không được sửa lô hàng này")
    if version is not None and int(version) != int(sh.version or 1):
        return EditResult(ok=False, conflict=True, row=grid_row(sh, tmpl, today, color_of),
                          errors={"_": "Lô hàng vừa được người khác / Automation cập nhật. Đã tải lại dòng này."})

    row = dict(sh.row_data or {})
    src = {k: dict(v or {}) for k, v in (sh.field_src or {}).items()}
    res = EditResult()
    now = utcnow().isoformat(timespec="seconds")
    changed: list[tuple[str, Any, Any]] = []
    review: list[str] = []

    for fld, raw in changes.items():
        col = tmpl.by_field.get(fld)
        if col is None:
            res.errors[fld] = "Cột không tồn tại"
            continue
        if not col.can_edit(p.role):
            res.errors[fld] = f"Bạn không có quyền sửa cột {col.label}"
            continue
        value, err = tmpl.parse(fld, raw)
        if err:
            res.errors[fld] = err
            continue
        if col.required and value is None:
            res.errors[fld] = f"{col.label} không được để trống"
            continue
        old = row.get(fld)
        if col.formula is not None and value is None:
            # Xoa trang o cong thuc -> tra lai cho cong thuc tinh
            if fld in src:
                src.pop(fld)
                changed.append((fld, old, "(cong thuc)"))
            continue
        new = tmpl.to_json(fld, value)
        if new == old and (col.formula is None or (src.get(fld) or {}).get("src") in MANUAL_SRC):
            continue
        prev = src.get(fld) or {}
        src[fld] = {
            "src": "MANAGER" if p.is_manager else "USER", "by": p.name, "at": now,
            "excel": prev.get("excel", old if sh.origin != "WEB" else None),
        }
        row[fld] = new
        changed.append((fld, old, new))
        if col.needs_review(p.role):
            review.append(fld)

    if not changed:
        res.ok = not res.errors
        res.row = grid_row(sh, tmpl, today, color_of)
        return res

    values = tmpl.row_from_json(row)
    values.update(recompute_formulas(tmpl, sh, values, src))
    derived = dict(sh.derived_notes or {})
    apply_remark_flags(values, engine.dictionary, derived)

    if sh.origin == "WEB":
        new_key = shipment_key("web", "", clean_text(values.get("JO")), values.get("ExFtyDate"))
        if new_key != sh.shipment_key:
            clash = s.execute(select(Shipment.id).where(Shipment.shipment_key == new_key)).first()
            if clash:
                return EditResult(ok=False, errors={"JO": "Đã có lô hàng cùng JO + EX-FACT DATE"},
                                  row=grid_row(sh, tmpl, today, color_of))
            sh.shipment_key = new_key

    old_exfty = sh.exfty_date
    sh.field_src = src
    engine.apply_values(sh, values, today, derived=derived, pic_default=sh.pic, s=s)
    rule = engine.buyers.resolve(sh.buyer_raw)
    sh.group_key = group_key(values, rule)
    if old_exfty and sh.exfty_date and old_exfty != sh.exfty_date:
        sh.previous_exfty, sh.exfty_changed_at = old_exfty, utcnow()
    sh.version = (sh.version or 1) + 1
    sh.updated_at, sh.updated_by = utcnow(), p.name

    for fld, old, new in changed:
        log_audit(s, "FIELD_CHANGED", actor_type="USER", actor_name=p.name, entity="shipment",
                  entity_id=sh.shipment_code, field=fld, old_value=old, new_value=new,
                  source="web", note=reason)
    for fld in review:
        col = tmpl.by_field[fld]
        raise_exception(
            s, "EX016", f"{p.label} doi {col.label} cua {sh.jo}: '{row.get(fld)}'",
            severity="INFO", source_file="WEB", source_column=fld, value=row.get(fld),
            shipment_code=sh.shipment_code, suggested_action="Quan ly kiem tra va dong muc nay",
            dedup_key=f"EX016:{sh.shipment_code}:{fld}:{row.get(fld)}",
        )

    s.flush()
    engine.recompute(s, sh, today)
    res.changed = [c[0] for c in changed]
    res.ok = not res.errors
    res.row = grid_row(sh, tmpl, today, color_of)
    return res


# ---------------------------------------------------------------------------
# Tao lo hang moi tren web
# ---------------------------------------------------------------------------


def create_shipment(
    s: Session, engine, p: Principal, data: dict[str, Any], *, assign_to: str | None = None,
    today: dt.date | None = None,
) -> Shipment:
    today = today or dt.date.today()
    if not p.can("shipments.create"):
        raise Forbidden("Bạn không được tạo lô hàng")
    tmpl: FollowUpTemplate = engine.template
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for fld, raw in data.items():
        if fld not in tmpl.by_field:
            continue
        v, err = tmpl.parse(fld, raw)
        if err:
            errors[fld] = err
        elif v is not None:
            values[fld] = v
    for c in tmpl.columns:
        if c.required and values.get(c.field) in (None, ""):
            errors.setdefault(c.field, f"{c.label} bắt buộc")
    if errors:
        raise ServiceError("; ".join(f"{tmpl.by_field[k].label if k in tmpl.by_field else k}: {v}"
                                     for k, v in errors.items()))

    jo, exfty = clean_text(values["JO"]), values["ExFtyDate"]
    dup = s.execute(select(Shipment.shipment_code).where(
        Shipment.jo == jo, Shipment.exfty_date == exfty, Shipment.archived.is_(False))).first()
    if dup:
        raise ServiceError(f"Đã có lô hàng {dup[0]} cùng JO {jo} ngày {to_canonical_date(exfty)}")

    for c in tmpl.columns:
        values.setdefault(c.field, None)
    values.update(tmpl.compute_formulas(values))
    derived: dict[str, Any] = {}
    apply_remark_flags(values, engine.dictionary, derived)

    stamp = to_canonical_date(exfty).replace("-", "")
    seq = engine._next_seq(s, stamp) + 1
    sh = Shipment(
        shipment_code=make_shipment_code(exfty, seq), shipment_key=shipment_key("web", "", jo, exfty),
        origin="WEB", source_file_code="WEB", source_sheet="", source_row=0, owner_user=p.name,
        auto_status="", effective_status="", row_data={}, field_src={}, version=1,
    )
    s.add(sh)
    engine.apply_values(sh, values, today, derived=derived, pic_default=p.name, s=s)
    sh.group_key = group_key(values, engine.buyers.resolve(sh.buyer_raw))
    sh.updated_at, sh.updated_by = utcnow(), p.name
    s.flush()
    log_audit(s, "SHIPMENT_CREATED", actor_type="USER", actor_name=p.name, entity="shipment",
              entity_id=sh.shipment_code, new_value=f"{jo} / {to_canonical_date(exfty)}", source="web")

    target = assign_to if (assign_to is not None and p.can("shipments.assign")) else None
    if target is None and not p.is_manager:
        target = p.name  # nguoi dung tao lo -> tu dong la nguoi phu trach
    if target:
        assign(s, [sh], target, by=p.name, method="MANUAL", note="tao lo tren web")
    else:
        auto_assign(s, engine.settings.assignment, [sh])
    engine.recompute(s, sh, today)
    return sh


def set_archived(s: Session, p: Principal, shipments: list[Shipment], archived: bool, note: str = "") -> int:
    if not p.can("shipments.archive"):
        raise Forbidden("Chỉ Quản lý / Admin được lưu trữ lô hàng")
    n = 0
    for sh in shipments:
        if sh.archived == archived:
            continue
        sh.archived = archived
        sh.version = (sh.version or 1) + 1
        log_audit(s, "ARCHIVED" if archived else "UNARCHIVED", actor_type="USER", actor_name=p.name,
                  entity="shipment", entity_id=sh.shipment_code, note=note)
        n += 1
    return n


def accept_excel_value(s: Session, engine, p: Principal, sh: Shipment, fld: str, excel_value: Any,
                       today: dt.date | None = None) -> None:
    """EX015: Quan ly chon lay gia tri Excel -> bo danh dau sua tay, Excel lai la nguon."""
    if not p.is_manager:
        raise Forbidden("Chỉ Quản lý / Admin xử lý xung đột")
    src = dict(sh.field_src or {})
    src.pop(fld, None)
    sh.field_src = src
    row = dict(sh.row_data or {})
    old = row.get(fld)
    row[fld] = excel_value
    values = engine.template.row_from_json(row)
    values.update(recompute_formulas(engine.template, sh, values, src))
    engine.apply_values(sh, values, today or dt.date.today(), pic_default=sh.pic, s=s)
    sh.version = (sh.version or 1) + 1
    sh.updated_at, sh.updated_by = utcnow(), p.name
    log_audit(s, "FIELD_CHANGED", actor_type="USER", actor_name=p.name, entity="shipment",
              entity_id=sh.shipment_code, field=fld, old_value=old, new_value=excel_value,
              source="excel", note="Quan ly chon gia tri Excel (EX015)")
    engine.recompute(s, sh, today)
