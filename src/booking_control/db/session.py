"""Khoi tao engine + session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import models_v3  # noqa: F401 - dang ky bang v3 vao Base.metadata
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def init_engine(url: str, echo: bool = False) -> Engine:
    global _engine, _SessionFactory
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, echo=echo, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):  # pragma: no cover - driver hook
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            # Hang chuc nhan vien ghi cung luc: SQLite chi cho 1 nguoi ghi tai mot thoi diem.
            # busy_timeout = cho toi da 15s thay vi bao ngay "database is locked".
            cur.execute("PRAGMA busy_timeout=15000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    _rename_legacy_columns(_engine)
    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    _ensure_indexes(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database chua duoc khoi tao. Goi init_engine() truoc.")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionFactory is None:
        raise RuntimeError("Database chua duoc khoi tao. Goi init_engine() truoc.")
    s = _SessionFactory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def new_session() -> Session:
    if _SessionFactory is None:
        raise RuntimeError("Database chua duoc khoi tao. Goi init_engine() truoc.")
    return _SessionFactory()


def _add_missing_columns(engine: Engine) -> list[str]:
    """Nang cap DB cu: them cot moi (khong xoa, khong doi cot cu).

    Dung khi cap nhat phan mem ma van giu database dang chay. Chi ADD COLUMN,
    thay doi phuc tap hon can dung Alembic (xem docs/DEVELOPER_GUIDE.md).
    """
    added: list[str] = []
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}'))
                # Dong cu phai co gia tri mac dinh (vd archived=False, assigned_to=""),
                # neu de NULL thi cac bo loc "archived = false" se bo sot du lieu cu.
                value = _python_default(col)
                if value is not None:
                    if isinstance(value, (dict, list)):
                        import json

                        value = json.dumps(value)
                    conn.execute(text(f"UPDATE {table.name} SET {col.name} = :v WHERE {col.name} IS NULL"),
                                 {"v": value})
                added.append(f"{table.name}.{col.name}")
    return added


def _python_default(col) -> object:
    d = col.default
    if d is None:
        return None
    if getattr(d, "is_callable", False):
        try:
            return d.arg(None)
        except TypeError:
            return d.arg()
    return getattr(d, "arg", None)


# Cot doi ten tu v1 (Booking Deadline Bot) -> v2 (khong con khai niem "Bot")
LEGACY_RENAMES = {"shipments": {"bot_status": "auto_status", "last_bot_color": "last_auto_color"}}


def _rename_legacy_columns(engine: Engine) -> list[str]:
    if engine.dialect.name != "sqlite":  # SQL Server / PostgreSQL: doi ten bang script rieng
        return []
    done: list[str] = []
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, pairs in LEGACY_RENAMES.items():
            if table not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for old, new in pairs.items():
                if old in have and new not in have:
                    conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"))
                    done.append(f"{table}.{old}->{new}")
    return done


def _ensure_indexes(engine: Engine) -> None:
    """Tao index moi cho bang da ton tai tu phien ban cu (create_all chi tao cho bang moi)."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        have = {ix["name"] for ix in insp.get_indexes(table.name)}
        for ix in table.indexes:
            if ix.name and ix.name not in have:
                ix.create(bind=engine, checkfirst=True)
