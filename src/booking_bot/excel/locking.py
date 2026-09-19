"""Phat hien file Excel dang bi mo/lock + co che retry (SRS muc 40-41, 70).

Quy tac bat buoc: Bot KHONG BAO GIO force overwrite.
  Attempt 1 -> cho 30s -> Attempt 2 -> 60s -> Attempt 3 -> 120s -> Pending Queue.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from ..logging_setup import get_logger

log = get_logger("excel.locking")
T = TypeVar("T")


class FileLockedError(RuntimeError):
    """File dang bi mo boi Excel hoac tien trinh khac."""


@dataclass(slots=True)
class LockStatus:
    locked: bool
    reason: str = ""
    owner_hint: str = ""


def owner_lock_file(path: Path) -> Path:
    """Excel tao file an '~$Ten.xlsx' khi mo file."""
    return path.with_name("~$" + path.name)


def check_lock(path: Path) -> LockStatus:
    p = Path(path)
    if not p.exists():
        return LockStatus(False, "file chua ton tai")

    lock_marker = owner_lock_file(p)
    hint = ""
    if lock_marker.exists():
        try:
            raw = lock_marker.read_bytes()[:128]
            hint = "".join(ch for ch in raw.decode("utf-16-le", "ignore") if ch.isprintable()).strip()
        except OSError:
            hint = ""
        return LockStatus(True, "Excel dang mo file (~$ marker)", hint)

    # Thu mo che do ghi - tren Windows se bao PermissionError neu dang bi khoa
    try:
        with open(p, "r+b"):
            pass
    except PermissionError as exc:
        return LockStatus(True, f"khong mo duoc che do ghi: {exc}", hint)
    except OSError as exc:
        return LockStatus(True, f"loi truy cap file: {exc}", hint)

    return LockStatus(False)


def wait_until_free(path: Path, retry_seconds: list[int], sleeper: Callable[[float], None] = time.sleep) -> bool:
    """Thu lai theo lich 30/60/120s. True neu file da ranh."""
    status = check_lock(path)
    if not status.locked:
        return True
    for i, delay in enumerate(retry_seconds, start=1):
        log.warning("File dang bi khoa (%s). Thu lai lan %s sau %ss: %s", status.reason, i, delay, path)
        sleeper(delay)
        status = check_lock(path)
        if not status.locked:
            log.info("File da ranh sau %s lan thu: %s", i, path)
            return True
    return False


def atomic_replace(tmp_path: Path, target: Path) -> None:
    """Ghi de an toan: ghi ra file tam roi doi ten (giam rui ro hong file)."""
    os.replace(tmp_path, target)


def safe_write(
    target: Path,
    write_fn: Callable[[Path], T],
    retry_seconds: list[int],
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """Chay write_fn(tmp_path) roi thay the target. Raise FileLockedError neu khong the."""
    target = Path(target)
    if not wait_until_free(target, retry_seconds, sleeper):
        raise FileLockedError(f"File van bi khoa sau {len(retry_seconds)} lan thu: {target}")

    tmp = target.with_name(f".{target.stem}.bot-tmp{target.suffix}")
    try:
        result = write_fn(tmp)
        atomic_replace(tmp, target)
        return result
    except PermissionError as exc:
        raise FileLockedError(f"Khong ghi duoc {target}: {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover
                pass


def describe(status: LockStatus) -> dict[str, Any]:
    return {"locked": status.locked, "reason": status.reason, "owner": status.owner_hint}
