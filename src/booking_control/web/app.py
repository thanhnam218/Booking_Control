"""Web noi bo - Booking Control (SRS v2: Web + Workflow + Automation).

Chay tren 1 may Windows lam may chu noi bo; dong nghiep mo bang trinh duyet qua mang LAN.
Moi trang deu can dang nhap; quyen kiem tra o backend.

    /login /setup /account     Dang nhap, tao Admin dau tien, doi mat khau
    /                          Dashboard (Lo hang cua toi / Team Workload)
    /followup                  Luoi Follow Up 47 cot giong Excel (sua, dan, keo, loc, xuat Excel)
    /shipments, /shipments/ID  Danh sach + chi tiet lo hang (Why?, container, lich su)
    /bookings, /bookings/ID    Booking tach rieng: split / gop / hold / huy / rebook / xuat mot phan (v3)
    /emails, /emails/ID        Hang cho duyet email Outlook: ghep lo, chon gia tri email / PDF (v3)
    /tasks                     Viec cua toi: task B1-B15.1, chung tu con thieu (v3)
    /weekly                    Weekly Planning tuan sau - han 12:00 thu 6 (v3)
    /assignments               Phan cong lo hang (Quan ly)
    /exceptions                Exception Center
    /notifications             Thong bao cua toi
    /import                    Nhap file Follow Up (xem truoc -> nhap)
    /automation                Cac job tu dong + lich su chay
    /admin/users               Nguoi dung & vai tro (Admin);  /admin/permissions  ma tran quyen
    /mapping /buyers /settings /backups /audit
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..logging_setup import get_logger
from .auth import LoginThrottle
from .deps import (
    CTX,
    LoginRequired,
    PasswordChangeRequired,
    PermissionDenied,
    auth_guard,
    is_api,
    render,
)

log = get_logger("web")
STATIC_DIR = Path(__file__).parent / "static"


def create_app(engine=None, scheduler=None) -> FastAPI:
    CTX["engine"] = engine
    CTX["scheduler"] = scheduler
    if engine is not None:
        a = engine.settings.auth
        CTX["throttle"] = LoginThrottle(a.max_failed_logins, a.lockout_minutes)

    app = FastAPI(title="Booking Control", docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(auth_guard)])
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if is_api(request):
            return JSONResponse({"error": "Chưa đăng nhập hoặc phiên đã hết hạn"}, status_code=401)
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        from urllib.parse import quote

        return RedirectResponse(f"/login?next={quote(nxt)}", status_code=303)

    @app.exception_handler(PasswordChangeRequired)
    async def _pw_required(request: Request, exc: PasswordChangeRequired):
        if is_api(request):
            return JSONResponse({"error": "Cần đổi mật khẩu trước"}, status_code=403)
        return RedirectResponse("/account/password", status_code=303)

    @app.exception_handler(PermissionDenied)
    async def _denied(request: Request, exc: PermissionDenied):
        if is_api(request) or request.headers.get("x-csrf-token"):
            return JSONResponse({"error": exc.message}, status_code=403)
        return render(request, "error.html", status_code=403, message=exc.message)

    from ..services.shipment_service import NotFound, ServiceError

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        code = 404 if isinstance(exc, NotFound) else 400
        if is_api(request) or request.headers.get("x-csrf-token"):
            return JSONResponse({"error": str(exc)}, status_code=code)
        return render(request, "error.html", status_code=code, message=str(exc))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    from . import (
        routes_admin,
        routes_auth,
        routes_booking,
        routes_email,
        routes_grid,
        routes_main,
        routes_work,
    )

    app.include_router(routes_auth.router)
    app.include_router(routes_booking.router)  # /bookings/new truoc /bookings/{id}
    app.include_router(routes_main.router)
    app.include_router(routes_grid.router)
    app.include_router(routes_email.router)
    app.include_router(routes_work.router)
    app.include_router(routes_admin.router)
    return app
