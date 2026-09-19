"""Giao dien dong lenh: bookingbot <lenh>

    bookingbot init          Tao config.yaml + database lan dau
    bookingbot check         Kiem tra duong dan file, nhan dien sheet, mapping cot
    bookingbot scan          Quet 1 lan (them --dry-run de chi xem truoc)
    bookingbot run           Chay day du: scheduler + dashboard + tray  (che do hang ngay)
    bookingbot serve         Chi chay dashboard web
    bookingbot status        In nhanh tinh trang hien tai
    bookingbot backups       Liet ke / khoi phuc ban backup
    bookingbot outlook       Quet Outlook tim mail xac nhan booking
    bookingbot sample        Tao bo 5 file Excel demo de chay thu
"""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .db import init_engine, session_scope
from .engine import BookingBotEngine
from .logging_setup import setup_logging
from .settings import CONFIG_DIR, PROJECT_ROOT, reload_settings

app = typer.Typer(add_completion=False, help="Booking Deadline Bot - tu dong hoa deadline booking xuat khau")
console = Console()


def _boot(config: str | None = None, console_log: bool = True) -> BookingBotEngine:
    settings = reload_settings(config)
    setup_logging(settings.log_dir, settings.app.log_level, console=console_log)
    init_engine(settings.database_url(), settings.database.echo)
    engine = BookingBotEngine(settings)
    engine.bootstrap()
    return engine


# ---------------------------------------------------------------------------


@app.command()
def init(force: bool = typer.Option(False, "--force", help="Ghi de config.yaml neu da co")):
    """Tao config/config.yaml + config/buyer_rules.yaml + database."""
    pairs = [
        (CONFIG_DIR / "config.example.yaml", CONFIG_DIR / "config.yaml"),
        (CONFIG_DIR / "buyer_rules.example.yaml", CONFIG_DIR / "buyer_rules.yaml"),
    ]
    for src, dst in pairs:
        if dst.exists() and not force:
            console.print(f"[yellow]Da co[/] {dst.name} - bo qua (dung --force de ghi de)")
        else:
            shutil.copy2(src, dst)
            console.print(f"[green]Da tao[/] {dst}")

    engine = _boot()
    console.print(f"[green]Database san sang:[/] {engine.settings.database_url()}")
    console.print(f"[green]Thu muc du lieu:[/] {engine.settings.data_dir}")
    console.print("\nBuoc tiep theo: sua [cyan]config/config.yaml[/] -> muc [cyan]files:[/] "
                  "tro dung duong dan Excel cua ban, roi chay [cyan]bookingbot check[/]")


@app.command()
def check(config: str = typer.Option(None, "--config", "-c")):
    """Kiem tra cau hinh: file co ton tai khong, sheet nao duoc nhan la Follow Up, cot nao map duoc."""
    from openpyxl import load_workbook

    from .excel.detector import detect_workbook
    from .excel.locking import check_lock
    from .excel.mapping import HeaderMapper

    engine = _boot(config, console_log=False)
    s = engine.settings
    console.print(f"[bold]Config:[/] {s.config_path}\n")

    t = Table(title="File Registry")
    for c in ("ID", "Vai tro", "Duong dan", "Ton tai", "Khoa", "R/W"):
        t.add_column(c)
    for f in s.files:
        p = s.resolve_path(f.path)
        lock = check_lock(p) if p.exists() else None
        t.add_row(
            f.id, f.role, str(p),
            "[green]co[/]" if p.exists() else "[red]KHONG[/]",
            ("[red]dang mo[/]" if lock and lock.locked else "-") if p.exists() else "-",
            ("R" if f.read else "") + ("W" if f.write else ""),
        )
    console.print(t)

    mapper = HeaderMapper(engine.dictionary)
    for f in s.files_by_role("follow_up"):
        p = s.resolve_path(f.path)
        if not p.exists():
            continue
        console.print(f"\n[bold]{f.id}[/] - {p.name}")
        wb = load_workbook(p, data_only=True)
        try:
            t2 = Table()
            for c in ("Sheet", "Follow Up?", "Confidence", "Header row", "Truong khop", "Ghi chu"):
                t2.add_column(c)
            for det in detect_workbook(wb, engine.dictionary, mapper):
                mark = {"SCAN": "[green]CO[/]", "ASK_USER": "[yellow]HOI USER[/]",
                        "IGNORE": "[dim]bo qua[/]"}[det.decision]
                t2.add_row(det.sheet_name, mark, f"{det.confidence:.0%}",
                           str(det.header_row or "-"),
                           ", ".join(det.matched_fields[:6]), det.note[:60])
            console.print(t2)

            for det in detect_workbook(wb, engine.dictionary, mapper):
                if det.decision == "SCAN" and det.header_map and det.header_map.unmatched_headers:
                    console.print(f"  [yellow]Cot chua map ({det.sheet_name}):[/] "
                                  + ", ".join(det.header_map.unmatched_headers[:12]))
        finally:
            wb.close()

    console.print("\n[dim]Cot chua map khong lam Bot loi - chi la Bot khong dung den. "
                  "Neu do la cot quan trong, them alias vao config/field_mapping.yaml.[/]")


@app.command()
def scan(
    config: str = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Chi bao cao, khong ghi file"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
):
    """Quet Follow Up mot lan roi thoat."""
    engine = _boot(config, console_log=not quiet)
    result = engine.run_scan(dry_run=dry_run, deliver_notifications=not dry_run)
    console.print(result.render_text())
    raise typer.Exit(1 if result.errors else 0)


@app.command()
def run(
    config: str = typer.Option(None, "--config", "-c"),
    no_tray: bool = typer.Option(False, "--no-tray", help="Khong hien icon khay (chay console)"),
    no_web: bool = typer.Option(False, "--no-web", help="Khong bat dashboard"),
):
    """Chay ngam hang ngay: scheduler + dashboard + tray icon."""
    engine = _boot(config)
    from .scheduler import BotScheduler

    scheduler = BotScheduler(engine)
    url = ""

    if engine.settings.web.enabled and not no_web:
        url = _start_web(engine, scheduler)
        console.print(f"[green]Dashboard:[/] {url}")

    scheduler.start()
    console.print(f"[green]Bot dang chay[/] - quet moi {engine.settings.scheduler.scan_interval_hours} gio")

    if no_tray:
        console.print("Nhan Ctrl+C de dung.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            scheduler.shutdown()
        return

    try:
        from .tray import run_tray

        run_tray(scheduler, url)
    except ImportError:
        console.print("[yellow]Khong co pystray - chuyen sang che do console. Ctrl+C de dung.[/]")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    finally:
        scheduler.shutdown()


@app.command()
def serve(
    config: str = typer.Option(None, "--config", "-c"),
    port: int = typer.Option(0, "--port", "-p"),
    scheduler_on: bool = typer.Option(True, "--scheduler/--no-scheduler"),
):
    """Chi chay web dashboard (khong tray)."""
    import uvicorn

    engine = _boot(config)
    sched = None
    if scheduler_on:
        from .scheduler import BotScheduler

        sched = BotScheduler(engine)
        sched.start()

    from .web.app import create_app

    web_app = create_app(engine, sched)
    cfg = engine.settings.web
    console.print(f"[green]Dashboard:[/] http://{cfg.host}:{port or cfg.port}")
    try:
        uvicorn.run(web_app, host=cfg.host, port=port or cfg.port, log_level="warning")
    finally:
        if sched:
            sched.shutdown()


@app.command()
def status(config: str = typer.Option(None, "--config", "-c")):
    """In nhanh so lieu tong quan."""
    from sqlalchemy import func, select

    from .db.models import ExceptionRecord, ScanRun, Shipment

    _boot(config, console_log=False)
    with session_scope() as s:
        counts = dict(
            s.execute(
                select(Shipment.effective_status, func.count())
                .where(Shipment.is_active.is_(True)).group_by(Shipment.effective_status)
            ).all()
        )
        exc = s.execute(
            select(func.count()).select_from(ExceptionRecord).where(ExceptionRecord.status == "OPEN")
        ).scalar_one()
        last = s.execute(select(ScanRun).order_by(ScanRun.id.desc()).limit(1)).scalar_one_or_none()
        last_txt = (f"{last.started_at:%d/%m %H:%M} ({last.mode}, {last.shipments_found} lo)"
                    if last else "chua quet lan nao")

    t = Table(title="BOOKING CONTROL CENTER")
    t.add_column("Trang thai")
    t.add_column("So lo", justify="right")
    colors = {"WHITE": "white", "YELLOW": "yellow", "GREEN": "green", "RED": "red",
              "COMPLETED": "cyan", "PARTIAL": "magenta", "CANCELLED": "dim"}
    for k in ["WHITE", "YELLOW", "GREEN", "RED", "COMPLETED", "PARTIAL", "CANCELLED"]:
        t.add_row(f"[{colors[k]}]{k}[/]", str(counts.get(k, 0)))
    t.add_row("[bold]TONG[/]", f"[bold]{sum(counts.values())}[/]")
    console.print(t)
    console.print(f"Exception chua xu ly: [red]{exc}[/]")
    console.print(f"Lan quet gan nhat: {last_txt}")


@app.command()
def backups(
    config: str = typer.Option(None, "--config", "-c"),
    file_code: str = typer.Option("", "--file", "-f"),
    restore: str = typer.Option("", "--restore", help="Duong dan ban backup can khoi phuc"),
):
    """Liet ke hoac khoi phuc ban backup."""
    from .excel.backup import list_backups
    from .excel.backup import rollback as do_rollback

    engine = _boot(config, console_log=False)
    s = engine.settings

    if restore:
        if not file_code:
            console.print("[red]Can --file <ID> khi dung --restore[/]")
            raise typer.Exit(2)
        fcfg = s.file_by_code(file_code)
        if fcfg is None:
            console.print(f"[red]Khong co file ID {file_code}[/]")
            raise typer.Exit(2)
        target = s.resolve_path(fcfg.path)
        do_rollback(Path(restore), target, s.backup_dir, file_code)
        console.print(f"[green]Da khoi phuc[/] {target}")
        return

    codes = [file_code] if file_code else [f.id for f in s.files]
    for code in codes:
        rows = list_backups(s.backup_dir, code)
        if not rows:
            continue
        t = Table(title=f"Backup - {code}")
        for c in ("Ban backup", "Thoi diem", "KB", "Ghi chu"):
            t.add_column(c)
        for r in rows[:20]:
            t.add_row(r["name"], r["created"], f"{r['size'] / 1024:.0f}", r["note"][:50])
        console.print(t)


@app.command()
def outlook(
    config: str = typer.Option(None, "--config", "-c"),
    apply: bool = typer.Option(False, "--apply", help="Ap dung ngay thay vi chi tao Exception"),
):
    """Quet Outlook tim mail xac nhan / huy booking."""
    from sqlalchemy import select

    from .db.models import Shipment
    from .integrations import outlook as ol

    engine = _boot(config, console_log=False)
    if not ol.available():
        console.print("[red]Khong chay duoc: can Windows + Outlook desktop + pywin32[/]")
        raise typer.Exit(2)

    with session_scope() as s:
        jos = [j for (j,) in s.execute(select(Shipment.jo).distinct()).all() if j]
        hits = ol.scan_mailbox(engine.settings.outlook, jos)
        n = ol.apply_hits(s, hits, auto_apply=apply or engine.settings.outlook.auto_apply)

    t = Table(title=f"Outlook: {len(hits)} mail lien quan")
    for c in ("Nhan luc", "Loai", "JO", "Tieu de"):
        t.add_column(c)
    for h in hits[:40]:
        t.add_row(h.received.strftime("%d/%m %H:%M"), h.kind, ", ".join(h.matched_jos), h.subject[:60])
    console.print(t)
    console.print(f"Da {'ap dung' if apply else 'tao Exception cho'} {n} lo hang")


@app.command()
def sample(
    out: str = typer.Option("./sample_data", "--out", "-o"),
    seed: int = typer.Option(42, "--seed"),
):
    """Tao bo 5 file Excel demo (Follow Up x2 + 3 file dung chung) de chay thu."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from make_sample_data import build_all  # type: ignore

    paths = build_all(Path(out), seed=seed)
    for p in paths:
        console.print(f"[green]Da tao[/] {p}")
    console.print("\nChay thu: [cyan]bookingbot scan --dry-run[/]")


@app.command()
def version():
    """In phien ban."""
    from . import __version__

    console.print(f"Booking Deadline Bot {__version__}")


# ---------------------------------------------------------------------------


def _start_web(engine: BookingBotEngine, scheduler) -> str:
    import uvicorn

    from .web.app import create_app

    cfg = engine.settings.web
    web_app = create_app(engine, scheduler)
    server = uvicorn.Server(
        uvicorn.Config(web_app, host=cfg.host, port=cfg.port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://{cfg.host}:{cfg.port}"
    if cfg.open_browser_on_start:
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    return url


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
