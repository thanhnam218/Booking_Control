"""Giao dien dong lenh: bookingctl <lenh>

    bookingctl init          Tao config.yaml + database lan dau
    bookingctl check         Kiem tra duong dan file, nhan dien sheet, mapping cot
    bookingctl scan          Quet 1 lan (them --dry-run de chi xem truoc)
    bookingctl run           Chay may chu web + Automation Engine (che do hang ngay)
    bookingctl serve         Chi chay web (them --no-scheduler de tat automation)
    bookingctl user          Quan ly tai khoan: add / passwd / list / disable
    bookingctl status        In nhanh tinh trang hien tai
    bookingctl backups       Liet ke / khoi phuc ban backup
    bookingctl outlook       Quet Outlook tim mail xac nhan booking
    bookingctl sample        Tao bo 5 file Excel demo de chay thu
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
from .engine import AutomationEngine
from .logging_setup import setup_logging
from .settings import CONFIG_DIR, PROJECT_ROOT, reload_settings

app = typer.Typer(add_completion=False, help="Booking Control - tu dong hoa deadline booking xuat khau")
console = Console()


def _boot(config: str | None = None, console_log: bool = True) -> AutomationEngine:
    settings = reload_settings(config)
    setup_logging(settings.log_dir, settings.app.log_level, console=console_log)
    init_engine(settings.database_url(), settings.database.echo)
    engine = AutomationEngine(settings)
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
                  "tro dung duong dan Excel cua ban, roi chay [cyan]bookingctl check[/]")


@app.command()
def check(config: str = typer.Option(None, "--config", "-c")):
    """Kiem tra cau hinh: file co ton tai khong, sheet nao duoc nhan la Follow Up, cot nao map duoc."""
    from openpyxl import load_workbook

    from .excel.detector import detect_workbook
    from .excel.locking import check_lock
    from .excel.mapping import HeaderMapper
    from .excel.reader import read_workbook
    from .excel.xlsx_patch import inspect_package

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

    t3 = Table(title="Cau truc file (Automation ghi LOSSLESS - chi doi o can doi)")
    for c in ("ID", "Part", "External link", "Defined name", "Cong thuc", "Comment", "openpyxl se lam mat"):
        t3.add_column(c)
    for f in s.files:
        p = s.resolve_path(f.path)
        if not p.exists():
            continue
        rep = inspect_package(p)
        t3.add_row(f.id, str(rep.parts), str(rep.external_links), str(rep.defined_names),
                   str(rep.formulas), str(rep.comments), "; ".join(rep.openpyxl_would_lose) or "-")
    console.print(t3)

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

        res = read_workbook(p, f.id, engine.dictionary, owner_user=f.owner)
        for sr in res.sheets:
            for fc in sr.formula_columns:
                console.print(f"  [cyan]Cot cong thuc[/] {fc.column} ({fc.field}): {fc.pattern} "
                              f"- {fc.formula_rows} dong")
            if sr.merged_ranges:
                console.print(f"  [cyan]O merge[/]: {', '.join(sr.merged_ranges[:10])}")
            n_formula_issue = sum(1 for r in sr.rows for i in r.issues if i.get("code") == "EX013")
            if n_formula_issue:
                console.print(f"  [red]{n_formula_issue} van de cong thuc[/] (go de / thieu) - xem khi scan")

    console.print("\n[dim]Cot chua map khong lam Automation loi - chi la Automation khong dung den. "
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
    host: str = typer.Option("", "--host", help="Ghi de web.host, vd 0.0.0.0 de mo cho mang LAN"),
    port: int = typer.Option(0, "--port", "-p"),
    no_scheduler: bool = typer.Option(False, "--no-scheduler", help="Chi chay web, tat Automation"),
):
    """Chay he thong: web (dang nhap, luoi Follow Up, phan cong, xuat Excel) + Automation Engine."""
    import uvicorn

    engine = _boot(config)
    sched = None
    if not no_scheduler:
        from .scheduler import AutomationScheduler

        sched = AutomationScheduler(engine)
        sched.start()
        console.print(f"[green]Automation dang chay[/] - quet Follow Up moi "
                      f"{engine.settings.scheduler.scan_interval_hours} gio")

    from .web.app import create_app

    web_app = create_app(engine, sched)
    cfg = engine.settings.web
    bind_host, bind_port = host or cfg.host, port or cfg.port
    _print_urls(bind_host, bind_port)
    _warn_if_no_admin()
    if cfg.open_browser_on_start:
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{bind_port}")).start()
    try:
        uvicorn.run(web_app, host=bind_host, port=bind_port, log_level="warning")
    finally:
        if sched:
            sched.shutdown()


@app.command()
def serve(
    config: str = typer.Option(None, "--config", "-c"),
    host: str = typer.Option("", "--host"),
    port: int = typer.Option(0, "--port", "-p"),
    scheduler_on: bool = typer.Option(True, "--scheduler/--no-scheduler"),
):
    """Giong `run` (giu lai cho tuong thich)."""
    run(config=config, host=host, port=port, no_scheduler=not scheduler_on)


def _print_urls(host: str, port: int) -> None:
    import socket

    console.print(f"[green]Web:[/] http://127.0.0.1:{port}  (tren may nay)")
    if host in ("0.0.0.0", "::"):
        try:
            ip = socket.gethostbyname(socket.gethostname())
            console.print(f"[green]Mang LAN:[/] http://{ip}:{port}  (dong nghiep mo bang trinh duyet)")
        except OSError:
            pass
    console.print("Nhan Ctrl+C de dung.")


def _warn_if_no_admin() -> None:
    from sqlalchemy import select

    from .db.models import User

    with session_scope() as s:
        has = s.execute(select(User).where(User.role == "admin", User.password_hash != "")).first()
    if not has:
        console.print("[yellow]Chua co Admin co mat khau.[/] Mo web tren CHINH may nay de tao, hoac chay:\n"
                      "   bookingctl user add admin --role admin")


# ---------------------------------------------------------------------------
# Quan ly tai khoan
# ---------------------------------------------------------------------------

user_app = typer.Typer(help="Quan ly tai khoan dang nhap (admin / manager / user / viewer)")
app.add_typer(user_app, name="user")


@user_app.command("add")
def user_add(
    name: str = typer.Argument(..., help="Ten dang nhap, vd: nam.nguyen"),
    role: str = typer.Option("user", "--role", "-r", help="admin | manager | user | viewer"),
    display_name: str = typer.Option("", "--display-name", "-d"),
    email: str = typer.Option("", "--email", "-e"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Tao tai khoan (hoac dat lai mat khau + vai tro neu da co)."""
    from .web.auth import AuthError, upsert_user

    engine = _boot(config, console_log=False)
    try:
        with session_scope() as s:
            u = upsert_user(s, name, role=role, password=password, display_name=display_name, email=email,
                            min_length=engine.settings.auth.min_password_length)
            console.print(f"[green]OK[/] {u.name} ({u.role})")
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc


@user_app.command("passwd")
def user_passwd(
    name: str = typer.Argument(...),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Dat lai mat khau (khi nguoi dung quen)."""
    from sqlalchemy import select

    from .db.models import User
    from .web.auth import AuthError, set_password

    engine = _boot(config, console_log=False)
    with session_scope() as s:
        u = s.execute(select(User).where(User.name == name)).scalar_one_or_none()
        if u is None:
            console.print(f"[red]Khong co user {name}[/]")
            raise typer.Exit(2)
        try:
            set_password(u, password, engine.settings.auth.min_password_length)
        except AuthError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(2) from exc
    console.print("[green]Da doi mat khau[/]")


@user_app.command("list")
def user_list(config: str = typer.Option(None, "--config", "-c")):
    from sqlalchemy import select

    from .db.models import User

    _boot(config, console_log=False)
    t = Table(title="Tai khoan")
    for c in ("Ten dang nhap", "Ho ten", "Vai tro", "Nhom", "Trang thai", "Mat khau"):
        t.add_column(c)
    with session_scope() as s:
        for u in s.execute(select(User).order_by(User.role, User.name)).scalars():
            t.add_row(u.name, u.display_name, u.role, u.team, "hoat dong" if u.active else "KHOA",
                      "co" if u.password_hash else "[yellow]chua dat[/]")
    console.print(t)


@user_app.command("disable")
def user_disable(name: str = typer.Argument(...), config: str = typer.Option(None, "--config", "-c")):
    """Khoa tai khoan (khong xoa - giu lich su)."""
    from sqlalchemy import select

    from .db.models import User

    _boot(config, console_log=False)
    with session_scope() as s:
        u = s.execute(select(User).where(User.name == name)).scalar_one_or_none()
        if u is None:
            raise typer.Exit(2)
        u.active = False
    console.print(f"Da khoa {name}")


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
    console.print("\nChay thu: [cyan]bookingctl scan --dry-run[/]")


@app.command()
def version():
    """In phien ban."""
    from . import __version__

    console.print(f"Booking Control {__version__}")


# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
