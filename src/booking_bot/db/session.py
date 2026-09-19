"""Khoi tao engine + session factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

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
            cur.close()

    Base.metadata.create_all(_engine)
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
