"""Kenh thong bao: web (trong ung dung), email (SMTP), console.

Khong co tray icon / popup tren may chu: moi nguoi nhan thong bao cua minh
qua trinh duyet (chuong thong bao + Desktop Notification) hoac email.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger("notify.channels")
IS_WINDOWS = platform.system() == "Windows"


@dataclass(slots=True)
class NotificationPayload:
    title: str
    body: str
    severity: str = "WARNING"
    category: str = "GENERIC"
    context: dict[str, Any] = field(default_factory=dict)
    source_file: str = ""
    source_sheet: str = ""
    source_row: int | None = None
    shipment_code: str = ""

    def context_lines(self) -> list[str]:
        """Thong bao phai co Context (SRS muc 50)."""
        keys = ["jo", "buyer", "exfty", "days_left", "status", "hold_reason"]
        labels = {
            "jo": "JO", "buyer": "Buyer", "exfty": "ExFtyDate",
            "days_left": "Con lai (ngay)", "status": "Status", "hold_reason": "Ly do",
        }
        out = []
        for k in keys:
            if k in self.context and self.context[k] not in (None, ""):
                out.append(f"{labels[k]}: {self.context[k]}")
        if self.source_sheet:
            loc = f"{self.source_sheet}"
            if self.source_row:
                loc += f" / dong {self.source_row}"
            out.append(f"Nguon: {loc}")
        return out


# ---------------------------------------------------------------------------


class Channel:
    name = "base"

    def send(self, payload: NotificationPayload) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleChannel(Channel):
    name = "console"

    ICONS = {"CRITICAL": "[!!]", "WARNING": "[!]", "INFO": "[i]"}

    def send(self, payload: NotificationPayload) -> bool:
        icon = self.ICONS.get(payload.severity, "[*]")
        lines = [f"{icon} {payload.title}", f"    {payload.body}"]
        lines += [f"    {line}" for line in payload.context_lines()]
        print("\n".join(lines), file=sys.stderr)
        return True


class WebChannel(Channel):
    """Thong bao trong web (chuong + trang Thong bao cua tung nguoi phu trach).

    Ban ghi Notification da nam san trong database (co cot recipient), nen kenh nay
    chi can xac nhan "da phat". Trinh duyet cua nguoi dung se tu hien Desktop
    Notification khi tab web dang mo (xem static/app.js).
    """

    name = "web"

    def send(self, payload: NotificationPayload) -> bool:
        return True


class EmailChannel(Channel):
    """Gui email qua SMTP noi bo (tuy chon). Bat trong config: notification.email."""

    name = "email"

    def __init__(self, cfg: Any, resolve_email=None):
        self.cfg = cfg
        self.resolve_email = resolve_email or (lambda username: "")

    def send(self, payload: NotificationPayload) -> bool:
        import smtplib
        from email.message import EmailMessage

        cfg = self.cfg
        if not cfg or not cfg.enabled or not cfg.smtp_host:
            return False
        to = self.resolve_email(payload.context.get("recipient", "")) or ""
        recipients = [x for x in [to, *cfg.always_cc] if x]
        if not recipients:
            return False
        msg = EmailMessage()
        msg["Subject"] = f"[{payload.severity}] {payload.title}"
        msg["From"] = cfg.sender
        msg["To"] = ", ".join(recipients)
        body = [payload.body, "", *payload.context_lines()]
        if cfg.base_url and payload.shipment_code:
            body += ["", f"Mo lo hang: {cfg.base_url.rstrip('/')}/shipments/{payload.shipment_code}"]
        msg.set_content("\n".join(body))
        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
                if cfg.use_tls:
                    smtp.starttls()
                if cfg.username:
                    import os

                    smtp.login(cfg.username, os.environ.get(cfg.password_env, ""))
                smtp.send_message(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Gui email that bai: %s", exc)
            return False


class CallbackChannel(Channel):
    """Dung cho test / tich hop - goi mot ham bat ky."""

    name = "callback"

    def __init__(self, fn, name: str = "callback"):
        self.fn = fn
        self.name = name

    def send(self, payload: NotificationPayload) -> bool:
        try:
            self.fn(payload)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Callback channel loi: %s", exc)
            return False


# ---------------------------------------------------------------------------


def open_file(path: str, sheet: str = "", row: int | None = None) -> bool:
    """Mo file nguon. Tren Windows co the mo dung sheet/dong qua COM (SRS muc 51)."""
    if not path:
        return False
    p = Path(path)
    if not p.exists():
        log.warning("Khong tim thay file de mo: %s", path)
        return False

    if IS_WINDOWS and sheet:  # pragma: no cover - can Excel
        try:
            import win32com.client  # type: ignore

            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = True
            wb = excel.Workbooks.Open(str(p.resolve()))
            ws = wb.Worksheets(sheet)
            ws.Activate()
            if row:
                ws.Range(f"A{row}").Select()
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("Khong mo duoc dung o (%s), mo file thong thuong", exc)

    try:
        if IS_WINDOWS:  # pragma: no cover
            import os

            os.startfile(str(p))  # noqa: S606
        elif platform.system() == "Darwin":  # pragma: no cover
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
        return True
    except OSError as exc:  # pragma: no cover
        log.warning("Khong mo duoc file: %s", exc)
        return False
