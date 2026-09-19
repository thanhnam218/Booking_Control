"""Nghiem thu Container Engine - SRS muc 15-19, 66, 73."""

from __future__ import annotations

import pytest

from booking_bot.core.container import DEFAULT_CAPACITY, apply_user_allocation, plan_containers

CAP = DEFAULT_CAPACITY
PRI = ["40HC", "40DC", "20ST"]


@pytest.mark.parametrize(
    "cbm, hc, dc, st, capacity, excess",
    [
        (10, 0, 0, 1, 23, 13),     # SRS vi du 1
        (30, 0, 1, 0, 53, 23),     # SRS vi du 2
        (60, 1, 0, 0, 63, 3),      # SRS vi du 3
        (160, 2, 1, 0, 179, 19),   # SRS vi du 4 - tieu chi nghiem thu quan trong nhat
    ],
)
def test_srs_examples(cbm, hc, dc, st, capacity, excess):
    p = plan_containers(cbm, "SEA", lcl_threshold_cbm=0)
    assert (p.qty_40hc, p.qty_40dc, p.qty_20st) == (hc, dc, st)
    assert p.estimated_capacity == capacity
    assert p.excess_capacity == excess
    assert p.allocation_status == "AUTO"


@pytest.mark.parametrize("cbm", [0.5, 1, 22.9, 23, 23.1, 52.9, 53, 63, 63.1, 99, 126, 127, 250, 999])
def test_capacity_never_short(cbm):
    """Luat bat buoc: capacity >= CBM. Thieu cho la loi nghiem trong."""
    p = plan_containers(cbm, "SEA", lcl_threshold_cbm=0)
    assert p.estimated_capacity >= cbm, f"{cbm} CBM -> capacity {p.estimated_capacity}"
    assert p.excess_capacity >= 0


def test_excess_is_allowed_but_bounded():
    """Du thua duoc phep nhung khong duoc phi qua mot container lon."""
    for cbm in range(1, 200):
        p = plan_containers(cbm, "SEA", lcl_threshold_cbm=0)
        assert p.excess_capacity < CAP["40HC"] + CAP["20ST"]


def test_boundaries_around_thresholds():
    assert plan_containers(22.9, "SEA", lcl_threshold_cbm=0).qty_20st == 1
    assert plan_containers(23.1, "SEA", lcl_threshold_cbm=0).qty_40dc == 1
    assert plan_containers(53.1, "SEA", lcl_threshold_cbm=0).qty_40hc == 1
    p = plan_containers(63.1, "SEA", lcl_threshold_cbm=0)
    assert (p.qty_40hc, p.qty_20st) == (1, 1)


def test_air_becomes_ac():
    """SRS muc 21: Ship Mode = AIR -> Type = AC, khong tinh container bien."""
    p = plan_containers(120, "AIR")
    assert p.container_type == "AC"
    assert p.booking_mode == "AIR"
    assert p.total_containers == 0


def test_forced_type_from_buyer_rule():
    p = plan_containers(80, "SEA", forced_type="AC")
    assert p.container_type == "AC"


def test_lcl_suggestion_under_threshold():
    p = plan_containers(9, "SEA", lcl_threshold_cbm=15)
    assert p.booking_mode == "LCL"
    assert p.container_type == "CFS"
    assert "1x20ST" in p.note  # van cho biet phuong an FCL tuong duong


def test_missing_cbm_needs_review():
    """SRS muc 19: Bot khong tu quyet dinh khi thieu du lieu."""
    for bad in (None, 0, -5):
        p = plan_containers(bad, "SEA")
        assert p.allocation_status == "NEED_REVIEW"


def test_user_review_rule():
    p = plan_containers(44, "SEA", needs_user_review=True, lcl_threshold_cbm=0)
    assert p.allocation_status == "NEED_REVIEW"
    assert p.qty_40dc == 1  # van de xuat phuong an cho User xem


def test_min_excess_strategy_differs_and_is_valid():
    greedy = plan_containers(160, "SEA", strategy="priority", lcl_threshold_cbm=0)
    lean = plan_containers(160, "SEA", strategy="min_excess", lcl_threshold_cbm=0)
    assert greedy.excess_capacity >= lean.excess_capacity
    assert lean.estimated_capacity >= 160


def test_user_allocation_overrides_bot():
    """SRS muc 35: USER > BOT."""
    p = plan_containers(160, "SEA", lcl_threshold_cbm=0)
    p = apply_user_allocation(p, {"40HC": 3, "40DC": 0, "20ST": 0}, CAP, PRI)
    assert p.allocation_status == "USER_CONFIRMED"
    assert p.allocation_source == "USER"
    assert p.container_type == "3x40HC"


def test_safety_factor():
    p = plan_containers(60, "SEA", safety_factor=1.1, lcl_threshold_cbm=0)
    assert p.estimated_capacity >= 60 * 1.1
