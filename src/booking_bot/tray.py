"""Tray Application - icon khay he thong Windows (SRS muc 3).

    Booking Bot
    Last Scan: 09:00
    Next Scan: 12:00

Menu: Quet ngay / Dry Run / Mo Dashboard / Tam dung / Thoat.
Mau icon doi theo tinh trang: xanh = binh thuong, cam = co canh bao, do = co loi.
"""

from __future__ import annotations

import threading
import webbrowser

from .logging_setup import get_logger
from .scheduler import BotScheduler

log = get_logger("tray")


def _make_icon(color: tuple[int, int, int], badge: int = 0):
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=color + (255,))
    # hinh thung container don gian
    d.rectangle((18, 24, 46, 44), fill=(255, 255, 255, 235))
    for x in range(22, 46, 6):
        d.line((x, 24, x, 44), fill=color + (255,), width=2)
    if badge:
        d.ellipse((size - 26, 0, size, 26), fill=(229, 57, 53, 255))
    return img


class TrayApp:
    """Chay pystray o thread chinh; scheduler chay nen."""

    def __init__(self, scheduler: BotScheduler, dashboard_url: str = ""):
        self.scheduler = scheduler
        self.dashboard_url = dashboard_url
        self.icon = None

    # ------------------------------------------------------------------

    def _color(self) -> tuple[int, int, int]:
        state = self.scheduler.state
        if state.last_error:
            return (229, 57, 53)
        if state.paused:
            return (251, 140, 0)
        counts = (state.last_result or {}).get("status_counts", {})
        if counts.get("RED") or counts.get("WHITE"):
            return (249, 168, 37)
        return (67, 160, 71)

    def _title(self) -> str:
        return self.scheduler.state.tray_text()

    def refresh(self) -> None:
        if self.icon is None:
            return
        counts = (self.scheduler.state.last_result or {}).get("status_counts", {})
        badge = int(counts.get("RED", 0)) + int(counts.get("WHITE", 0))
        try:
            self.icon.icon = _make_icon(self._color(), badge)
            self.icon.title = self._title()
        except Exception:  # noqa: BLE001  pragma: no cover
            pass

    # ------------------------------------------------------------------

    def _menu(self):
        import pystray

        def scan(_icon=None, _item=None):
            threading.Thread(target=self._scan, daemon=True).start()

        def dryrun(_icon=None, _item=None):
            threading.Thread(target=self._dryrun, daemon=True).start()

        def dashboard(_icon=None, _item=None):
            if self.dashboard_url:
                webbrowser.open(self.dashboard_url)

        def toggle(_icon=None, _item=None):
            st = self.scheduler.state
            self.scheduler.resume() if st.paused else self.scheduler.pause()
            self.refresh()

        def quit_app(icon, _item=None):
            self.scheduler.shutdown()
            icon.stop()

        return pystray.Menu(
            pystray.MenuItem(lambda _: self._title(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quet ngay", scan, default=True),
            pystray.MenuItem("Dry Run (xem truoc)", dryrun),
            pystray.MenuItem("Mo Dashboard", dashboard, enabled=bool(self.dashboard_url)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: "Chay tiep" if self.scheduler.state.paused else "Tam dung", toggle
            ),
            pystray.MenuItem("Thoat", quit_app),
        )

    def _scan(self) -> None:
        self.scheduler.scan_now(dry_run=False)
        self.refresh()
        self._notify_summary()

    def _dryrun(self) -> None:
        result = self.scheduler.scan_now(dry_run=True)
        self._balloon("Dry Run xong", f"{result.shipments_total} lo hang, "
                                      f"{result.events} su kien deadline. Xem chi tiet trong Dashboard.")

    def _notify_summary(self) -> None:
        r = self.scheduler.state.last_result or {}
        counts = r.get("status_counts", {})
        self._balloon(
            "Quet xong",
            f"{r.get('shipments_total', 0)} lo - "
            f"Trang: {counts.get('WHITE', 0)} | Vang: {counts.get('YELLOW', 0)} | "
            f"Xanh: {counts.get('GREEN', 0)} | Do: {counts.get('RED', 0)}",
        )

    def _balloon(self, title: str, message: str) -> None:
        if self.icon is None:
            return
        try:  # pragma: no cover - chi chay tren Windows
            self.icon.notify(message, title)
        except Exception:  # noqa: BLE001
            log.info("%s: %s", title, message)

    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            log.error("Chua cai pystray/Pillow. Chay: pip install pystray Pillow")
            raise

        import pystray

        self.icon = pystray.Icon(
            "bookingbot", _make_icon(self._color()), self._title(), self._menu()
        )
        log.info("Tray dang chay. Nhan chuot phai vao icon de xem menu.")
        self.icon.run()


def run_tray(scheduler: BotScheduler, dashboard_url: str = "") -> None:
    app = TrayApp(scheduler, dashboard_url)
    scheduler.on_scan_done = lambda *_: app.refresh()
    app.run()
