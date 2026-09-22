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

import re
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
    allocation_source: str = "AUTO"  # AUTO | USER
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
        plan.note = "Thieu hoac sai CBM -> Automation khong tu quyet dinh container (SRS muc 19)"
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
        plan.trace.append("Buyer rule = user_review -> Automation khong tu chot")

    return plan


def apply_user_allocation(plan: ContainerPlan, user_quantities: dict[str, int],
                          capacity: dict[str, float], priority: list[str]) -> ContainerPlan:
    """User chot lai container -> USER > AUTO (SRS muc 35)."""
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


# ---------------------------------------------------------------------------
# Doc cot CONT TYPE User da go trong Follow Up ("1x20", "2x40HC", "LCL"...)
# ---------------------------------------------------------------------------

_TYPE_ALIASES = {
    "20": "20ST", "20ST": "20ST", "20DC": "20ST", "20GP": "20ST", "20DV": "20ST",
    "40": "40DC", "40DC": "40DC", "40GP": "40DC", "40ST": "40DC", "40DV": "40DC",
    "40HC": "40HC", "40HQ": "40HC", "40H": "40HC", "HC": "40HC", "HQ": "40HC",
}
_PART = re.compile(r"^(?:(\d+)\s*[X*]\s*)?(\d{2})?\s*'?\s*(HC|HQ|H|DC|GP|ST|DV)?$")


@dataclass(slots=True)
class ContainerSpec:
    raw: str
    quantities: dict[str, int] = field(default_factory=dict)
    mode: str = ""  # FCL | LCL | AIR
    valid: bool = True
    reason: str = ""

    @property
    def label(self) -> str:
        if self.mode in ("LCL", "AIR"):
            return self.mode
        return format_type(self.quantities, list(DEFAULT_PRIORITY))

    def capacity(self, capacity: dict[str, float]) -> float:
        return sum(q * capacity.get(t, 0) for t, q in self.quantities.items())


def parse_container_type(text: Any) -> ContainerSpec:
    """'1x20' -> {20ST:1}; '2x40HC+1x20' -> {40HC:2, 20ST:1}; 'LCL' -> mode LCL."""
    raw = "" if text is None else str(text).strip()
    spec = ContainerSpec(raw=raw)
    if not raw:
        spec.valid, spec.reason = False, "trong"
        return spec

    s = raw.upper().replace("’", "'").replace("×", "X").replace(" ", "")
    if s in ("LCL", "CFS", "LCL/CFS"):
        spec.mode = "LCL"
        return spec
    if s in ("AIR", "AC", "AIRFREIGHT"):
        spec.mode = "AIR"
        return spec

    qty: dict[str, int] = {}
    for part in re.split(r"[+,;&]|AND", s):
        if not part:
            continue
        m = _PART.match(part.replace("'", ""))
        if not m or not (m.group(2) or m.group(3)):
            spec.valid, spec.reason = False, f"khong hieu '{part}'"
            return spec
        n = int(m.group(1) or 1)
        size = m.group(2) or "40"
        suffix = m.group(3) or ""
        key = _TYPE_ALIASES.get(size + suffix) or _TYPE_ALIASES.get(size + ("HC" if suffix == "H" else suffix))
        if key is None:
            spec.valid, spec.reason = False, f"loai cont '{size}{suffix}' chua duoc ho tro"
            return spec
        qty[key] = qty.get(key, 0) + n

    spec.quantities = qty
    spec.mode = "FCL"
    return spec


def merge_specs(specs: list[ContainerSpec]) -> ContainerSpec:
    """Cong nhieu o CONT TYPE (khac nhau) cua cung 1 nhom thanh 1 phuong an."""
    out = ContainerSpec(raw=" + ".join(s.raw for s in specs), mode="FCL")
    modes = {s.mode for s in specs}
    if len(modes) == 1 and modes & {"LCL", "AIR"}:
        out.mode = modes.pop()
        return out
    for s in specs:
        if not s.valid:
            out.valid, out.reason = False, s.reason
        for t, q in s.quantities.items():
            out.quantities[t] = out.quantities.get(t, 0) + q
    return out
