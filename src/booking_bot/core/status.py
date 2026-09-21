"""Booking Status Engine (SRS muc 27-35, 52, DMS muc 19-22).

    RED       Hold = Yes                                     (uu tien cao nhat)
    CANCELLED Cancel = Yes
    COMPLETED Da xuat xong toan bo: CDF + BL + (CONT + SEAL neu FCL) + ETD da qua
    PARTIAL   Da xuat mot phan (xuat toi nay 1 nua, mai xuat tiep) - theo ShippedQty
              hoac theo nhom cung NO: co dong da dong cont+seal, co dong chua (engine.py)
    WHITE     Chua booking
    YELLOW    Da booking nhung CDF hoac BL con thieu
    GREEN     Da booking + CDF + BL day du (~80% hoan thanh nghiep vu)

Moi ket qua deu kem "trace" de tra loi cau hoi 'Why Yellow?' (SRS muc 52).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .normalize import clean_text, has_value

# Thu tu uu tien khi so sanh / sap xep bao cao
PRIORITY = ["RED", "CANCELLED", "PARTIAL", "WHITE", "YELLOW", "GREEN", "COMPLETED"]

ALL_STATUSES = ["WHITE", "YELLOW", "GREEN", "RED", "COMPLETED", "PARTIAL", "CANCELLED"]


@dataclass(slots=True)
class StatusResult:
    status: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    booking_inferred: bool = False

    def explain(self) -> str:
        """Chuoi giai thich ngan gon cho popup / dashboard."""
        lines = [f"{t['check']} = {t['value']}" for t in self.trace]
        lines.append(f"-> {self.status}")
        if self.reason:
            lines.append(f"({self.reason})")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "trace": self.trace, "reason": self.reason,
                "booking_inferred": self.booking_inferred}


def _t(check: str, value: Any, note: str = "") -> dict[str, Any]:
    return {"check": check, "value": value, "note": note}


def infer_booking(values: dict[str, Any], blanks: set[str] | None = None) -> tuple[bool, bool]:
    """Suy ra 'da booking hay chua' khi file khong co cot Booking ro rang.

    Tra ve (booked, inferred). inferred=True nghia la Bot phai suy luan
    -> se ghi vao trace de User biet.
    """
    flag = values.get("BookingFlag")
    if isinstance(flag, bool):
        return flag, False
    if has_value(flag, blanks):
        return True, False
    # File that: cot BOOKING NO co so booking = da book
    if has_value(values.get("BookingNo"), blanks):
        return True, False

    # Khong co cot Booking: co CDF hoac BL thi chac chan da book
    if has_value(values.get("CDF"), blanks) or has_value(values.get("BL"), blanks):
        return True, True
    return False, True


def evaluate(
    values: dict[str, Any],
    *,
    blanks: set[str] | None = None,
    strict_booking_column: bool = False,
    today: dt.date | None = None,
    completion: dict[str, Any] | None = None,
) -> StatusResult:
    """Tinh trang thai Bot cho mot shipment.

    values: dict canonical field -> gia tri da chuan hoa.
    strict_booking_column=True: khong suy luan, thieu cot Booking coi nhu chua book.
    """
    trace: list[dict[str, Any]] = []

    hold = bool(values.get("HoldFlag"))
    cancel = bool(values.get("CancelFlag"))
    shipped = bool(values.get("ShippedFlag"))
    cdf_filled = has_value(values.get("CDF"), blanks)
    bl_filled = has_value(values.get("BL"), blanks)

    if strict_booking_column:
        booked, inferred = bool(values.get("BookingFlag")), False
    else:
        booked, inferred = infer_booking(values, blanks)

    # 1. HOLD - uu tien cao nhat (SRS muc 31)
    trace.append(_t("HOLD", "YES" if hold else "NO"))
    if hold:
        reason = clean_text(values.get("HoldReason")) or "khong ghi ly do"
        detail = clean_text(values.get("HoldReasonDetail"))
        txt = f"Dang hold: {reason}" + (f" - {detail}" if detail else "")
        return StatusResult("RED", trace, txt, inferred)

    # 2. CANCEL
    trace.append(_t("CANCEL", "YES" if cancel else "NO"))
    if cancel:
        return StatusResult("CANCELLED", trace, "Booking da bi huy - cho gop lo hoac dat lai", inferred)

    # 3. Da xuat thuc te
    final_qty = values.get("FinalExportQty")
    shipped_qty = values.get("ShippedQty")
    if shipped or shipped_qty is not None:
        trace.append(_t("SHIPPED", "YES" if shipped else "PARTIAL_DATA"))
        if shipped_qty is not None and final_qty:
            try:
                ratio = float(shipped_qty) / float(final_qty)
            except (TypeError, ValueError, ZeroDivisionError):
                ratio = 0.0
            trace.append(_t("ShippedQty/FinalQty", f"{shipped_qty:g}/{final_qty:g}"))
            if ratio >= 0.999:
                return StatusResult("COMPLETED", trace, "Da xuat du so luong", inferred)
            if ratio > 0:
                return StatusResult(
                    "PARTIAL", trace,
                    f"Moi xuat {ratio:.0%} - con lai se dong tiep chuyen sau", inferred,
                )
        elif shipped:
            return StatusResult("COMPLETED", trace, "Da danh dau xuat xong", inferred)

    # 4. Chua booking -> WHITE (SRS muc 28)
    trace.append(_t("BOOKING", "YES" if booked else "NO",
                    "suy ra tu CDF/BL vi file khong co cot Booking" if inferred else ""))
    if not booked:
        return StatusResult("WHITE", trace, "Chua thuc hien booking", inferred)

    # 5. CDF / BL
    trace.append(_t("CDF", "FILLED" if cdf_filled else "EMPTY", clean_text(values.get("CDF"))))
    trace.append(_t("BL", "FILLED" if bl_filled else "EMPTY", clean_text(values.get("BL"))))

    if cdf_filled and bl_filled:
        done = _check_completed(values, trace, blanks, today, completion)
        if done:
            return StatusResult("COMPLETED", trace, done, inferred)
        return StatusResult("GREEN", trace, "Da co ca CDF va BL - nghiep vu hoan thanh ~80%", inferred)

    missing = [n for n, ok in (("CDF", cdf_filled), ("BL", bl_filled)) if not ok]
    return StatusResult("YELLOW", trace, f"Da book nhung con thieu: {', '.join(missing)}", inferred)


def _check_completed(
    values: dict[str, Any],
    trace: list[dict[str, Any]],
    blanks: set[str] | None,
    today: dt.date | None,
    completion: dict[str, Any] | None,
) -> str:
    """Tra ve ly do neu lo hang da XUAT XONG, nguoc lai ''.

    Mac dinh: GREEN + (CONT + SEAL neu la hang FCL) + ETD <= hom nay.
    Tra loi cau hoi goc: "lam sao biet khi nao da xuat hang xong thanh cong".
    """
    if completion is not None and not completion.get("enabled", True):
        return ""
    cfg = completion or {}
    today = today or dt.date.today()
    mode = str(values.get("_booking_mode") or "").upper()
    need_cont = cfg.get("require_container_seal", True) and mode not in ("AIR", "LCL", "EXPRESS")

    if need_cont:
        cont = has_value(values.get("ContainerNo"), blanks)
        seal = has_value(values.get("SealNo"), blanks)
        trace.append(_t("CONT + SEAL", "FILLED" if cont and seal else "EMPTY",
                        f"{values.get('ContainerNo') or '-'} / {values.get('SealNo') or '-'}"))
        if not (cont and seal):
            return ""

    if cfg.get("require_etd_passed", True):
        etd = values.get("ETD")
        if not isinstance(etd, dt.date):
            trace.append(_t("ETD", "EMPTY", "chua co ngay tau chay"))
            return ""
        passed = etd <= today
        trace.append(_t("ETD", f"{etd:%d/%m/%Y}", "da qua" if passed else "chua toi"))
        if not passed:
            return ""

    parts = ["CDF", "BL"] + (["CONT", "SEAL"] if need_cont else []) + ["ETD da qua"]
    return "Da xuat xong: " + " + ".join(parts)


def effective_status(bot_status: str, user_override: str | None) -> tuple[str, str]:
    """USER > BOT (SRS muc 34-35). Tra ve (status, source)."""
    if user_override and user_override.upper() in ALL_STATUSES + ["*"]:
        return user_override.upper(), "USER"
    if user_override:
        return user_override, "USER"
    return bot_status, "BOT"


def transition_note(old: str | None, new: str) -> str:
    if not old or old == new:
        return ""
    return f"{old} -> {new}"


def is_upgrade(old: str, new: str) -> bool:
    """True neu trang thai tien trien tot len (Yellow -> Green)."""
    ladder = {"RED": 0, "CANCELLED": 0, "WHITE": 1, "YELLOW": 2, "PARTIAL": 3, "GREEN": 4, "COMPLETED": 5}
    return ladder.get(new, 0) > ladder.get(old, 0)


def status_counts(statuses: list[str]) -> dict[str, int]:
    out = {s: 0 for s in ALL_STATUSES}
    for s in statuses:
        out[s] = out.get(s, 0) + 1
    return out
