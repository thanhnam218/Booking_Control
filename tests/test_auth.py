"""Mat khau, chong do mat khau, ma tran quyen (SRS v2 muc 53)."""

from __future__ import annotations

import pytest

from booking_control.web.auth import (
    PERMISSIONS,
    AuthError,
    LoginThrottle,
    can,
    check_password_policy,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip_and_salt():
    h1, h2 = hash_password("Abcdef12", iterations=1000), hash_password("Abcdef12", iterations=1000)
    assert h1 != h2  # moi lan 1 salt khac
    assert verify_password("Abcdef12", h1) and not verify_password("abcdef12", h1)
    assert not verify_password("x", "rac-khong-hop-le")


@pytest.mark.parametrize("pw", ["short1", "12345678", "abcdefgh"])
def test_weak_passwords_rejected(pw):
    with pytest.raises(AuthError):
        check_password_policy(pw, 8)


def test_throttle_locks_after_max_failures():
    t = LoginThrottle(max_failed=3, lock_minutes=5)
    for _ in range(2):
        t.fail("u|ip")
    assert t.locked_for("u|ip") == 0
    t.fail("u|ip")
    assert t.locked_for("u|ip") >= 1
    t.ok("u|ip")
    assert t.locked_for("u|ip") == 0


def test_permission_matrix_matches_srs():
    assert can("manager", "shipments.assign") and not can("user", "shipments.assign")
    assert can("user", "shipments.edit") and not can("viewer", "shipments.edit")
    assert can("admin", "users.manage") and not can("manager", "users.manage")
    assert not can("manager", "config.settings")  # SRS: Manager khong mac dinh duoc system config
    assert all("admin" in roles for roles in PERMISSIONS.values())  # Admin toan quyen
