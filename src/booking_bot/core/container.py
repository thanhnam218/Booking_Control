"""Container Booking Engine (SRS muc 15-21, DMS muc 16-18).

Luat bat buoc:
    Estimated Capacity >= Total CBM     -> KHONG BAO GIO duoc thieu cho
    Du thua duoc phep
    Uu tien 40HC -> 40DC -> 20ST

Vi du chuan trong SRS (dung lam test nghiem thu):
    10  CBM -> 1 x 20ST              (capacity 23,  excess 13)
    30  CBM -> 1 x 40DC              (capacity 53,  excess 23)
    60  CBM -> 1 x 40HC              (capacity 63,  excess 3)
    160 CBM -> 2 x 40HC + 1 x 40DC   (capacity 179, excess 19)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any

DEFAULT_CAPACITY = {"40HC": 63.0, "40DC": 53.0, "20ST": 23.0}
DEFAULT_PRIORITY = ["40HC", "40DC", "20ST"]


@dataclass(slots=True)
class ContainerPlan:
    total_cbm: float = 0.0
    quantities: dict[str, int] = field(default_factory=dict)
    estimated_capacity: float = 0.0
    excess_capacity: float = 0.0
    booking_mode: str = ""  # FCL | LCL | AIR | EXPRESS
    container_type: str = ""  # chuoi hien thi cho cot TYPE
    allocation_status: str = "AUTO"  # AUTO | USER_CONFIRMED | NEED_REVIEW
    allocation_source: str = "BOT"  # BOT | USER
    note: str = ""
    trace: list[str] = field(default_factory=list)

    @property
    def qty_40hc(self) -> int:
        return self.quantities.get("40HC", 0)

    @property
    def qty_40dc(self) -> int:
        return self.quantities.get("40DC", 0)

    @property
    def qty_20st(self) -> int:
        return self.quantities.get("20ST", 0)

    @property
    def total_containers(self) -> int:
        return sum(self.quantities.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cbm": self.total_cbm,
            "qty_40hc": self.qty_40hc,
            "qty_40dc": self.qty_40dc,
            "qty_20st": self.qty_20st,
            "estimated_capacity": self.estimated_capacity,
            "excess_capacity": self.excess_capacity,
            "booking_mode": self.booking_mode,
            "container_type": self.container_type,
            "allocation_status": self.allocation_status,
            "allocation_source": self.allocation_source,
            "note": self.note,
        }


def format_type(quantities: dict[str, int], priority: list[str]) -> str:
    parts = [f"{quantities[t]}x{t}" for t in priority if quantities.get(t, 0) > 0]
    return " + ".join(parts)


def allocate_priority(
    cbm: float, capacity: dict[str, float], priority: list[str]
) -> tuple[dict[str, int], list[str]]:
    """Greedy theo thu tu uu tien - dung nhu vi du trong SRS.

    Buoc 1: lap day bang container lon nhat (40HC).
    Buoc 2: phan du -> chon container NHO NHAT ma van chua du.
    """
    trace: list[str] = []
    qty = {t: 0 for t in priority}
    if cbm <= 0:
        return qty, ["CBM <= 0, khong phan bo"]

    biggest = priority[0]
    cap_big = capacity[biggest]

    n = int(cbm // cap_big)
    if n:
        qty[biggest] = n
        trace.append(f"{n} x {biggest} ({n} x {cap_big:g} = {n * cap_big:g} CBM)")

    remainder = round(cbm - n * cap_big, 6)
    if remainder > 0:
        # container nho nhat ma capacity >= phan du
        ordered_small_first = sorted(priority, key=lambda t: capacity[t])
        chosen = next((t for t in ordered_small_first if capacity[t] >= remainder), biggest)
        qty[chosen] += 1
        trace.append(f"Phan du {remainder:g} CBM -> 1 x {chosen} (capacity {capacity[chosen]:g})")

    return qty, trace


def allocate_min_excess(
    cbm: float, capacity: dict[str, float], priority: list[str], max_units: int = 40
) -> tuple[dict[str, int], list[str]]:
    """Tim to hop du thua it nhat (van bao dam capacity >= CBM).

    Tie-break: it container hon -> container lon hon (theo priority).
    """
    if cbm <= 0:
        return {t: 0 for t in priority}, ["CBM <= 0, khong phan bo"]

    types = list(priority)
    caps = [capacity[t] for t in types]
    max_each = [min(max_units, int(cbm // c) + 1) for c in caps]

    best: tuple[float, int, int] | None = None
    best_qty: dict[str, int] = {}
    for combo in product(*[range(m + 1) for m in max_each]):
        total = sum(q * c for q, c in zip(combo, caps, strict=True))
        if total < cbm:
            continue
        n_units = sum(combo)
        if n_units == 0:
            continue
        # uu tien: excess nho -> it container -> nhieu container lon
        big_bias = -sum(q * (len(types) - i) for i, q in enumerate(combo))
        score = (round(total - cbm, 6), n_units, big_bias)
        if best is None or score < best:
            best = score
            best_qty = dict(zip(types, combo, strict=True))

    if not best_qty:  # pragma: no cover - chi xay ra khi capacity rong
        return {t: 0 for t in types}, ["Khong tim duoc to hop hop le"]
    return best_qty, [f"Toi uu du thua: {format_type(best_qty, priority)} (excess {best[0]:g} CBM)"]


def plan_containers(
    cbm: float | None,
    ship_mode: str = "SEA",
    *,
    capacity: dict[str, float] | None = None,
    priority: list[str] | None = None,
    strategy: str = "priority",
    lcl_threshold_cbm: float = 15.0,
    safety_factor: float = 1.0,
    air_type: str = "AC",
    lcl_type: str = "CFS",
    forced_type: str = "",
    needs_user_review: bool = False,
) -> ContainerPlan:
    """Tinh phuong an container cho mot lo hang.

    ship_mode AIR/EXPRESS -> khong tinh container bien, Type = AC (SRS muc 21).
    """
    capacity = capacity or dict(DEFAULT_CAPACITY)
    priority = priority or list(DEFAULT_PRIORITY)
    plan = ContainerPlan(total_cbm=round(float(cbm or 0.0), 3))
    mode = (ship_mode or "").strip().upper()

    if forced_type:
        plan.container_type = forced_type
        plan.booking_mode = "AIR" if forced_type.upper() == air_type.upper() else mode or "FCL"
        plan.allocation_status = "AUTO"
        plan.note = f"Buyer rule ep kieu: {forced_type}"
        plan.trace.append(plan.note)
        return plan

    if mode in ("AIR", "EXPRESS"):
        plan.booking_mode = mode
        plan.container_type = air_type
        plan.note = f"Ship Mode = {mode} -> Type = {air_type} (SRS muc 21)"
        plan.trace.append(plan.note)
        return plan

    if cbm is None or cbm <= 0:
        plan.allocation_status = "NEED_REVIEW"
        plan.booking_mode = mode or "SEA"
        plan.note = "Thieu hoac sai CBM -> Bot khong tu quyet dinh container (SRS muc 19)"
        plan.trace.append(plan.note)
        return plan

    target = round(float(cbm) * max(safety_factor, 1.0), 6)
    if safety_factor > 1.0:
        plan.trace.append(f"Ap he so an toan {safety_factor:g}: can capacity >= {target:g} CBM")

    if strategy == "min_excess":
        qty, trace = allocate_min_excess(target, capacity, priority)
    else:
        qty, trace = allocate_priority(target, capacity, priority)

    plan.quantities = qty
    plan.trace.extend(trace)
    plan.estimated_capacity = round(sum(q * capacity[t] for t, q in qty.items()), 3)
    plan.excess_capacity = round(plan.estimated_capacity - float(cbm), 3)
    plan.container_type = format_type(qty, priority)

    if float(cbm) <= lcl_threshold_cbm:
        plan.booking_mode = "LCL"
        plan.container_type = lcl_type
        plan.note = (
            f"CBM {cbm:g} <= nguong LCL {lcl_threshold_cbm:g} -> de xuat {lcl_type}. "
            f"Phuong an FCL tuong duong: {format_type(qty, priority)}"
        )
        plan.trace.append(plan.note)
    else:
        plan.booking_mode = "FCL"

    # Bat buoc kiem tra lai: khong duoc thieu cho
    if plan.estimated_capacity + 1e-9 < float(cbm):  # pragma: no cover - chan loi lap trinh
        plan.allocation_status = "NEED_REVIEW"
        plan.note = "LOI: capacity nho hon CBM - khong duoc phep"
        plan.trace.append(plan.note)
        return plan

    if needs_user_review:
        plan.allocation_status = "NEED_REVIEW"
        plan.note = (plan.note + " | " if plan.note else "") + "Buyer rule yeu cau User duyet container"
        plan.trace.append("Buyer rule = user_review -> Bot khong tu chot")

    return plan


def apply_user_allocation(plan: ContainerPlan, user_quantities: dict[str, int],
                          capacity: dict[str, float], priority: list[str]) -> ContainerPlan:
    """User chot lai container -> USER > BOT (SRS muc 35)."""
    qty = {t: int(user_quantities.get(t, 0)) for t in priority}
    plan.quantities = qty
    plan.estimated_capacity = round(sum(q * capacity[t] for t, q in qty.items()), 3)
    plan.excess_capacity = round(plan.estimated_capacity - plan.total_cbm, 3)
    plan.container_type = format_type(qty, priority)
    plan.allocation_status = "USER_CONFIRMED"
    plan.allocation_source = "USER"
    plan.trace.append(f"User chot: {plan.container_type}")
    if plan.estimated_capacity + 1e-9 < plan.total_cbm:
        plan.note = "CANH BAO: phuong an cua User co capacity nho hon CBM"
    return plan
