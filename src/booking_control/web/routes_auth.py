"""Dang nhap / dang xuat / tao Admin dau tien / tai khoan ca nhan."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..audit import log_audit
from ..db import session_scope
from ..db.models import User
from .auth import (
    COOKIE_NAME,
    AuthError,
    create_session,
    destroy_session,
    destroy_user_sessions,
    needs_setup,
    set_password,
    upsert_user,
    verify_password,
)
from .deps import CTX, client_ip, current, is_local, render, settings

router = APIRouter()


def _safe_next(nxt: str) -> str:
    # Chi cho chuyen huong noi bo (chong open redirect)
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", msg: str = ""):
    with session_scope() as s:
        if needs_setup(s):
            return RedirectResponse("/setup", status_code=303)
    return render(request, "login.html", next=_safe_next(next), error="", msg=msg, username="")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          next: str = Form("/"), remember: str = Form("")):
    cfg = settings().auth
    throttle = CTX["throttle"]
    key = f"{username.strip().lower()}|{client_ip(request)}"
    wait = throttle.locked_for(key)
    if wait:
        return render(request, "login.html", status_code=429, next=_safe_next(next), username=username,
                      error=f"Sai mật khẩu quá nhiều lần. Thử lại sau {wait} phút.", msg="")
    with session_scope() as s:
        user = s.execute(select(User).where(User.name == username.strip())).scalar_one_or_none()
        if user is None or not user.active or not user.password_hash or not verify_password(
                password, user.password_hash):
            throttle.fail(key)
            log_audit(s, "LOGIN_FAILED", actor_type="USER", actor_name=username[:80], entity="user",
                      source=client_ip(request))
            return render(request, "login.html", status_code=401, next=_safe_next(next), username=username,
                          error="Sai tên đăng nhập hoặc mật khẩu", msg="")
        throttle.ok(key)
        hours = cfg.remember_days * 24 if remember else cfg.session_hours
        token, _ = create_session(s, user, hours=hours, ip=client_ip(request),
                                  user_agent=request.headers.get("user-agent", ""))
        log_audit(s, "LOGIN", actor_type="USER", actor_name=user.name, entity="user", source=client_ip(request))
        must_change = user.must_change_password
    resp = RedirectResponse("/account/password" if must_change else _safe_next(next), status_code=303)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=cfg.secure_cookie,
                    max_age=hours * 3600 if remember else None, path="/")
    return resp


@router.post("/logout")
def logout(request: Request):
    with session_scope() as s:
        destroy_session(s, request.cookies.get(COOKIE_NAME))
    resp = RedirectResponse("/login?msg=Đã đăng xuất", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ---------------------------------------------------------------------------
# Tao Admin dau tien (chi khi chua co Admin nao co mat khau)
# ---------------------------------------------------------------------------


def _setup_allowed(request: Request) -> bool:
    return is_local(request) or settings().auth.allow_remote_setup


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    with session_scope() as s:
        if not needs_setup(s):
            return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", error="", allowed=_setup_allowed(request))


@router.post("/setup")
def setup(request: Request, username: str = Form(...), display_name: str = Form(""),
          password: str = Form(...), password2: str = Form(...)):
    if not _setup_allowed(request):
        return render(request, "setup.html", status_code=403, allowed=False,
                      error="Chỉ tạo Admin đầu tiên được trên chính máy chủ.")
    if password != password2:
        return render(request, "setup.html", status_code=400, allowed=True, error="Hai mật khẩu không khớp")
    try:
        with session_scope() as s:
            if not needs_setup(s):
                return RedirectResponse("/login", status_code=303)
            u = upsert_user(s, username, role="admin", password=password, display_name=display_name,
                            min_length=settings().auth.min_password_length)
            log_audit(s, "USER_CREATED", actor_type="USER", actor_name=u.name, entity="user",
                      entity_id=u.name, new_value="admin", note="Admin dau tien (setup)")
    except AuthError as exc:
        return render(request, "setup.html", status_code=400, allowed=True, error=str(exc))
    return RedirectResponse("/login?msg=Đã tạo Admin, hãy đăng nhập", status_code=303)


# ---------------------------------------------------------------------------
# Tai khoan ca nhan
# ---------------------------------------------------------------------------


@router.get("/account", response_class=HTMLResponse)
@router.get("/account/password", response_class=HTMLResponse)
def account_page(request: Request, msg: str = ""):
    return render(request, "account.html", error="", msg=msg)


@router.post("/account/password")
def change_password(request: Request, old_password: str = Form(...), new_password: str = Form(...),
                    new_password2: str = Form(...)):
    p = current(request)
    if new_password != new_password2:
        return render(request, "account.html", status_code=400, error="Hai mật khẩu mới không khớp", msg="")
    with session_scope() as s:
        u = s.get(User, p.id)
        if not verify_password(old_password, u.password_hash):
            return render(request, "account.html", status_code=400, error="Mật khẩu hiện tại không đúng", msg="")
        try:
            set_password(u, new_password, settings().auth.min_password_length)
        except AuthError as exc:
            return render(request, "account.html", status_code=400, error=str(exc), msg="")
        destroy_user_sessions(s, u.id, keep_id=p.session_id)  # dang xuat cac may khac
        log_audit(s, "PASSWORD_CHANGED", actor_type="USER", actor_name=u.name, entity="user", entity_id=u.name)
    return RedirectResponse("/account?msg=Đã đổi mật khẩu", status_code=303)


@router.post("/account/profile")
def update_profile(request: Request, display_name: str = Form(""), email: str = Form("")):
    p = current(request)
    with session_scope() as s:
        u = s.get(User, p.id)
        u.display_name, u.email = display_name.strip()[:160], email.strip()[:160]
    return RedirectResponse("/account?msg=Đã lưu", status_code=303)
