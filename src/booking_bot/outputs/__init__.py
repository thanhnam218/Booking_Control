from . import custom_code, mm_daily, shipping_schedule, weekly_planning
from .base import AllocationLike, OutputSpec, ShipmentLike

REGISTRY = {
    weekly_planning.SPEC.role: weekly_planning,
    custom_code.SPEC.role: custom_code,
    mm_daily.SPEC.role: mm_daily,
    shipping_schedule.SPEC.role: shipping_schedule,
}

__all__ = [
    "REGISTRY",
    "OutputSpec",
    "AllocationLike",
    "ShipmentLike",
    "weekly_planning",
    "custom_code",
    "mm_daily",
    "shipping_schedule",
]
