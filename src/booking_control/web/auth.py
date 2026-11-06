"""Dang nhap, phien lam viec, CSRF va phan quyen (SRS v2 muc 45, 53, 64, 77).

    Vai tro:
        admin    Toan quyen: nguoi dung, vai tro, buyer, rule, mapping, cau hinh, audit
        manager  Xem tat ca lo hang, phan cong / giao lai, duyet override, xu ly exception, export
        user     Chi xem & sua lo hang duoc giao (chinh hoac du phong), export lo cua minh
        viewer   Chi xem (tuy chon - vd ke toan, kho)
        auditor  Kiem soat: chi xem tat ca lo / booking / email / chung tu / audit log, khong sua (SRSv5 muc 63)

    - Mat khau luu dang PBKDF2-SHA256 (thu vien chuan Python, khong can cai them).
    - Cookie chi chua token ngau nhien; DB luu SHA-256 cua token.
    - Moi request ghi (POST/PUT/DELETE) phai kem CSRF token cua phien.
    - Quyen luon kiem tra o backend - giao dien chi an nut cho gon.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import User, WebSession, utcnow

COOKIE_NAME = "bc_session"
ROLES = ("admin", "manager", "user", "viewer", "auditor")
ROLE_LABELS = {"admin": "Quản trị", "manager": "Quản lý", "user": "Nhân viên", "viewer": "Chỉ xem",
               "auditor": "Kiểm soát (Auditor)"}
READ_ALL = {"admin", "manager", "viewer", "auditor"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,39}$")
PBKDF2_ITER = 260_000

PERMISSIONS: dict[str, set[str]] = {
    # Lo hang
    "shipments.view_all": READ_ALL,
    "shipments.edit": {"admin", "manager", "user"},
    "shipments.create": {"admin", "manager", "user"},
    "shipments.archive": {"admin", "manager"},
    "shipments.assign": {"admin", "manager"},
    # Booking (nguoi dung: chi lo duoc giao)
    "booking.edit": {"admin", "manager", "user"},
    "booking.cancel": {"admin", "manager", "user"},
    "execution.record": {"admin", "manager", "user"},
    # Email Outlook
    "email.review": {"admin", "manager", "user"},
    "email.view_all": {"admin", "manager", "auditor"},
    # Chung tu + quy trinh
    "document.upload": {"admin", "manager", "user"},
    "document.approve": {"admin", "manager"},
    "workflow.complete": {"admin", "manager", "user"},
    "tasks.view_all": READ_ALL,
    "weekly.confirm": {"admin", "manager"},
    # Exception / automation / bao cao
    "exceptions.view_all": READ_ALL,
    "exceptions.resolve": {"admin", "manager"},
    "automation.run": {"admin", "manager"},
    "automation.pause": {"admin"},
    "export.all": READ_ALL,
    "import.excel": {"admin", "manager"},
    "audit.view": {"admin", "manager", "auditor"},
    "reports.team": READ_ALL,
    # Quan tri
    # 5 bang "xuong song" (SRSv4 muc 68): Quan ly duoc XEM de doi chieu nghiep vu,
    # sua van chi Admin (sua trong file YAML).
    "rules.view": {"admin", "manager", "auditor"},
    "config.mapping": {"admin"},
    "config.buyers": {"admin"},
    "config.settings": {"admin"},
    "config.backups": {"admin"},
    "users.manage": {"admin"},
}

PERMISSION_LABELS = {
    "shipments.view_all": "Xem tất cả lô hàng", "shipments.edit": "Sửa lô hàng (người dùng: lô được giao)",
    "shipments.create": "Tạo lô hàng mới", "shipments.archive": "Lưu trữ lô hàng",
    "shipments.assign": "Phân công / giao lại lô", "booking.edit": "Tạo / sửa / tách / gộp booking",
    "booking.cancel": "Hủy / rebook / hold booking", "execution.record": "Ghi nhận xuất hàng (một phần)",
    "email.review": "Duyệt email Outlook (ghép lô, áp dụng)", "email.view_all": "Xem mọi email",
    "rules.view": "Xem 5 bảng xương sống (Buyer×Forwarder, Email, Deadline, Chứng từ, Quyền cột)",
    "document.upload": "Tải lên chứng từ", "document.approve": "Duyệt / trả lại chứng từ",
    "workflow.complete": "Hoàn thành task B1–B15.1", "tasks.view_all": "Xem task của mọi người",
    "weekly.confirm": "Chốt Weekly Planning", "exceptions.view_all": "Xem mọi exception",
    "exceptions.resolve": "Xử lý exception", "automation.run": "Chạy job tự động",
    "automation.pause": "Tạm dừng Automation", "export.all": "Xuất Excel toàn bộ",
    "import.excel": "Nhập file Excel", "audit.view": "Xem Audit Log", "reports.team": "Báo cáo team",
    "config.mapping": "Cấu hình mapping", "config.buyers": "Cấu hình Buyer / Forwarder",
    "config.settings": "Cài đặt hệ thống", "config.backups": "Backup / khôi phục",
    "users.manage": "Quản lý người dùng & vai trò",
}


class AuthError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Mat khau
# ---------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITER) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    b64 = base64.b64encode
    return f"pbkdf2_sha256${iterations}${b64(salt).decode()}${b64(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(it))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def check_password_policy(password: str, min_length: int = 8) -> None:
    if len(password or "") < min_length:
        raise AuthError(f"Mật khẩu phải có ít nhất {min_length} ký tự")
    if password.isdigit() or password.isalpha():
        raise AuthError("Mật khẩu cần có cả chữ và số")


def set_password(user: User, password: str, min_length: int = 8, *, must_change: bool = False) -> None:
    check_password_policy(password, min_length)
    user.password_hash = hash_password(password)
    user.must_change_password = must_change


def upsert_user(
    s: Session, name: str, *, role: str = "user", password: str = "", display_name: str = "",
    email: str = "", team: str = "", min_length: int = 8, must_change: bool = False,
) -> User:
    name = (name or "").strip()
    if not USERNAME_RE.match(name):
        raise AuthError("Tên đăng nhập 2-40 ký tự: chữ, số, dấu . _ - (không dấu cách, không dấu tiếng Việt)")
    if role not in ROLES:
        raise AuthError(f"Vai trò phải là một trong {', '.join(ROLES)}")
    u = s.execute(select(User).where(User.name == name)).scalar_one_or_none()
    if u is None:
        u = User(name=name, role=role, active=True)
        s.add(u)
    u.role = role
    if display_name:
        u.display_name = display_name
    if email:
        u.email = email
    if team:
        u.team = team
    if password:
        set_password(u, password, min_length, must_change=must_change)
    s.flush()
    return u


# ---------------------------------------------------------------------------
# Phien dang nhap
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    s: Session, user: User, *, hours: int, ip: str = "", user_agent: str = ""
) -> tuple[str, WebSession]:
    token = secrets.token_urlsafe(32)
    sess = WebSession(
        token_hash=_hash_token(token), user_id=user.id, csrf=secrets.token_urlsafe(24),
        expires_at=utcnow() + dt.timedelta(hours=hours), ip=ip[:64], user_agent=(user_agent or "")[:300],
    )
    s.add(sess)
    user.last_login_at = utcnow()
    s.flush()
    return token, sess


def load_session(s: Session, token: str | None) -> tuple[WebSession, User] | None:
    if not token:
        return None
    sess = s.execute(
        select(WebSession).where(WebSession.token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if sess is None or sess.expires_at < utcnow():
        return None
    user = s.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    if (utcnow() - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = utcnow()
    return sess, user


def destroy_session(s: Session, token: str | None) -> None:
    if not token:
        return
    sess = s.execute(
        select(WebSession).where(WebSession.token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if sess is not None:
        s.delete(sess)


def destroy_user_sessions(s: Session, user_id: int, keep_id: int | None = None) -> None:
    for sess in s.execute(select(WebSession).where(WebSession.user_id == user_id)).scalars():
        if sess.id != keep_id:
            s.delete(sess)


def needs_setup(s: Session) -> bool:
    return s.execute(
        select(User.id).where(User.role == "admin", User.active.is_(True), User.password_hash != "")
    ).first() is None


# ---------------------------------------------------------------------------
# Chong do mat khau (dem sai theo user + IP, luu trong bo nho)
# ---------------------------------------------------------------------------


class LoginThrottle:
    def __init__(self, max_failed: int = 5, lock_minutes: int = 10):
        self.max_failed = max_failed
        self.lock = dt.timedelta(minutes=lock_minutes)
        self._fails: dict[str, list] = {}
        self._mu = threading.Lock()

    def locked_for(self, key: str) -> int:
        with self._mu:
            rec = self._fails.get(key)
            if not rec or not rec[1]:
                return 0
            left = (rec[1] - dt.datetime.now()).total_seconds()
            if left <= 0:
                self._fails.pop(key, None)
                return 0
            return int(left // 60) + 1

    def fail(self, key: str) -> None:
        with self._mu:
            rec = self._fails.setdefault(key, [0, None])
            rec[0] += 1
            if rec[0] >= self.max_failed:
                rec[1] = dt.datetime.now() + self.lock

    def ok(self, key: str) -> None:
        with self._mu:
            self._fails.pop(key, None)


# ---------------------------------------------------------------------------
# Nguoi dang dang nhap
# ---------------------------------------------------------------------------


@dataclass
class Principal:
    id: int
    name: str
    display_name: str
    role: str
    email: str = ""
    team: str = ""
    csrf: str = ""
    session_id: int | None = None
    must_change_password: bool = False
    perms: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_manager(self) -> bool:
        return self.role in ("admin", "manager")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can(self, perm: str) -> bool:
        return perm in self.perms

    @classmethod
    def of(cls, user: User, sess: WebSession | None = None) -> Principal:
        return cls(
            id=user.id, name=user.name, display_name=user.display_name, role=user.role, email=user.email,
            team=user.team, csrf=sess.csrf if sess else "", session_id=sess.id if sess else None,
            must_change_password=user.must_change_password,
            perms={p for p, roles in PERMISSIONS.items() if user.role in roles},
        )


def can(role: str, perm: str) -> bool:
    return role in PERMISSIONS.get(perm, set())


def owns(principal: Principal, assigned_to: str, backup_owner: str = "") -> bool:
    """Nguoi dung thuong chi thao tac tren lo duoc giao (chinh hoac du phong)."""
    if principal.can("shipments.view_all") and principal.can("shipments.edit"):
        return True
    return principal.name in (assigned_to or "", backup_owner or "")
