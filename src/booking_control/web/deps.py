"""Tien ich dung chung cho cac router web: context, render, dang nhap, quyen, CSRF."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..db import session_scope
from ..db.models import Notification
from .auth import COOKIE_NAME, ROLE_LABELS, LoginThrottle, Principal, load_session

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
CTX: dict[str, Any] = {"engine": None, "scheduler": None, "throttle": LoginThrottle()}

PUBLIC_PREFIXES = ("/static/", "/login", "/setup", "/favicon.ico", "/healthz")

STATUS_META = {
    "WHITE": ("Chưa book", "#9e9e9e"),
    "YELLOW": ("Đã book, thiếu CDF/BL", "#f9a825"),
    "GREEN": ("Đủ CDF + BL", "#43a047"),
    "RED": ("Đang hold", "#e53935"),
    "COMPLETED": ("Đã xuất xong", "#039be5"),
    "PARTIAL": ("Xuất một phần", "#fb8c00"),
    "CANCELLED": ("Đã hủy", "#757575"),
}


class LoginRequired(Exception):
    pass


class PasswordChangeRequired(Exception):
    pass


class PermissionDenied(Exception):
    def __init__(self, message: str = "Bạn không có quyền thực hiện thao tác này"):
        super().__init__(message)
        self.message = message


def engine():
    return CTX["engine"]


def settings():
    return CTX["engine"].settings


def is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def is_local(request: Request) -> bool:
    return client_ip(request) in ("127.0.0.1", "::1", "localhost", "testclient")


# ---------------------------------------------------------------------------
# Dependency toan cuc: dang nhap + CSRF
# ---------------------------------------------------------------------------


async def auth_guard(request: Request) -> None:
    path = request.url.path
    if any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return
    token = request.cookies.get(COOKIE_NAME)
    with session_scope() as s:
        found = load_session(s, token)
        if found is None:
            raise LoginRequired()
        sess, user = found
        principal = Principal.of(user, sess)
    request.state.principal = principal

    if principal.must_change_password and path not in ("/account", "/account/password", "/logout"):
        raise PasswordChangeRequired()

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token_sent = request.headers.get("x-csrf-token", "")
        if not token_sent:
            ctype = request.headers.get("content-type", "")
            if "form" in ctype:
                form = await request.form()
                token_sent = str(form.get("_csrf", ""))
        if not token_sent or token_sent != principal.csrf:
            raise PermissionDenied("Phiên làm việc không hợp lệ (CSRF). Hãy tải lại trang và thử lại.")


def current(request: Request) -> Principal:
    p = getattr(request.state, "principal", None)
    if p is None:
        raise LoginRequired()
    return p


def require(request: Request, perm: str) -> Principal:
    p = current(request)
    if not p.can(perm):
        raise PermissionDenied()
    return p


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def unread_count(p: Principal) -> int:
    with session_scope() as s:
        stmt = select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
        if p.is_manager:
            stmt = stmt.where((Notification.recipient == p.name) | (Notification.recipient == ""))
        else:
            stmt = stmt.where(Notification.recipient == p.name)
        return int(s.execute(stmt).scalar_one())


def render(request: Request, template: str, status_code: int = 200, **kw) -> HTMLResponse:
    eng = CTX["engine"]
    sched = CTX["scheduler"]
    p = getattr(request.state, "principal", None)
    base = {
        "request": request,
        "app_name": eng.settings.app.name if eng else "Booking Control",
        "status_meta": STATUS_META,
        "nav": template.replace(".html", ""),
        "now": dt.datetime.now(),
        "state": sched.state if sched else None,
        "me": p,
        "csrf": p.csrf if p else "",
        "role_labels": ROLE_LABELS,
        "unread": unread_count(p) if p else 0,
    }
    return TEMPLATES.TemplateResponse(request, template, {**base, **kw}, status_code=status_code)
