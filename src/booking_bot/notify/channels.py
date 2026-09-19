"""Kenh thong bao: console, Windows toast, popup co Context, tray balloon.

Tat ca deu "an toan khi khong co Windows": tren Linux/macOS hoac khi thieu thu vien,
kenh se tu dong xuong console thay vi lam sap Bot.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import threading
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
        """Popup phai co Context (SRS muc 50)."""
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


class ToastChannel(Channel):
    """Windows toast qua winotify. Ngoai Windows -> ghi log."""

    name = "toast"

    def __init__(self, app_id: str = "Booking Deadline Bot", icon: str = ""):
        self.app_id = app_id
        self.icon = icon
        self._impl = None
        if IS_WINDOWS:
            try:
                from winotify import Notification  # type: ignore

                self._impl = Notification
            except ImportError:  # pragma: no cover
                log.warning("Chua cai winotify - toast se chuyen sang console")

    def send(self, payload: NotificationPayload) -> bool:
        if self._impl is None:
            return False
        try:  # pragma: no cover - chi chay tren Windows
            body = payload.body
            ctx = payload.context_lines()
            if ctx:
                body += "\n" + " | ".join(ctx[:3])
            toast = self._impl(
                app_id=self.app_id,
                title=payload.title,
                msg=body[:600],
                icon=self.icon or "",
                duration="long" if payload.severity == "CRITICAL" else "short",
            )
            if payload.source_file:
                toast.add_actions(label="Mo file nguon", launch=payload.source_file)
            toast.show()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Toast that bai: %s", exc)
            return False


class PopupChannel(Channel):
    """Popup Tkinter co Context + nut [Mo file nguon] / [Da kiem tra] (SRS muc 50-51).

    Chay o thread rieng de khong chan vong scan.
    """

    name = "popup"

    def __init__(self, on_ack=None):
        self.on_ack = on_ack

    def send(self, payload: NotificationPayload) -> bool:
        try:
            import tkinter  # noqa: F401
        except ImportError:  # pragma: no cover
            log.info("Khong co tkinter - bo qua popup")
            return False
        t = threading.Thread(target=self._show, args=(payload,), daemon=True)
        t.start()
        return True

    def _show(self, payload: NotificationPayload) -> None:  # pragma: no cover - UI
        import tkinter as tk
        from tkinter import ttk

        colors = {"CRITICAL": "#c62828", "WARNING": "#ef6c00", "INFO": "#1565c0"}
        accent = colors.get(payload.severity, "#37474f")

        root = tk.Tk()
        root.title("Booking Deadline Bot")
        root.attributes("-topmost", True)
        root.geometry("460x300")
        root.configure(bg="white")

        header = tk.Frame(root, bg=accent, height=44)
        header.pack(fill="x")
        tk.Label(
            header, text=payload.title, bg=accent, fg="white",
            font=("Segoe UI", 11, "bold"), anchor="w", padx=12,
        ).pack(fill="both", expand=True)

        body = tk.Frame(root, bg="white", padx=14, pady=10)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text=payload.body, bg="white", justify="left", wraplength=420,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 8))

        for line in payload.context_lines():
            tk.Label(body, text=line, bg="white", anchor="w", font=("Consolas", 9)).pack(anchor="w")

        footer = tk.Frame(root, bg="white", pady=10)
        footer.pack(fill="x")

        def open_source():
            open_file(payload.source_file)

        def ack():
            if self.on_ack:
                try:
                    self.on_ack(payload)
                except Exception:  # noqa: BLE001
                    pass
            root.destroy()

        if payload.source_file:
            ttk.Button(footer, text="Mo file nguon", command=open_source).pack(side="left", padx=12)
        ttk.Button(footer, text="Da kiem tra", command=ack).pack(side="right", padx=6)
        ttk.Button(footer, text="Dong", command=root.destroy).pack(side="right", padx=6)

        root.after(180_000, root.destroy)  # tu dong dong sau 3 phut
        root.mainloop()


class CallbackChannel(Channel):
    """Dung cho tray balloon / test - goi mot ham bat ky."""

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
