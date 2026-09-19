"""Version token, content hash, backup va rollback (SRS muc 38, 54).

version_token = "<mtime_ns>-<size>"  : re, du de phat hien file da doi.
content_hash  = sha256 toan bo file : chac chan, dung khi can so sanh ky.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("excel.backup")

TS_FMT = "%Y%m%d-%H%M%S"


@dataclass(slots=True)
class FileSnapshot:
    path: str
    version_token: str
    content_hash: str
    size: int
    mtime: dt.datetime

    def differs_from(self, other: FileSnapshot | None) -> bool:
        if other is None:
            return True
        return self.version_token != other.version_token or self.content_hash != other.content_hash


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def snapshot(path: Path, with_hash: bool = True) -> FileSnapshot:
    p = Path(path)
    st = p.stat()
    return FileSnapshot(
        path=str(p),
        version_token=f"{st.st_mtime_ns}-{st.st_size}",
        content_hash=file_hash(p) if with_hash else "",
        size=st.st_size,
        mtime=dt.datetime.fromtimestamp(st.st_mtime),
    )


def row_hash(values: dict) -> str:
    """Hash mot dong du lieu da chuan hoa -> phat hien User sua dong do."""
    parts = []
    for k in sorted(values):
        v = values[k]
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        parts.append(f"{k}={v if v is not None else ''}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def backup_dir_for(base_backup_dir: Path, file_code: str) -> Path:
    d = Path(base_backup_dir) / file_code
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_backup(path: Path, base_backup_dir: Path, file_code: str, keep: int = 20, note: str = "") -> Path:
    """Copy file truoc khi ghi. Tra ve duong dan ban backup."""
    src = Path(path)
    dest_dir = backup_dir_for(base_backup_dir, file_code)
    ts = dt.datetime.now().strftime(TS_FMT)
    dest = dest_dir / f"{src.stem}__{ts}{src.suffix}"
    # Hai lan backup trong cung mot giay khong duoc ghi de len nhau
    seq = 1
    while dest.exists():
        dest = dest_dir / f"{src.stem}__{ts}-{seq:02d}{src.suffix}"
        seq += 1
    shutil.copy2(src, dest)
    if note:
        dest.with_suffix(dest.suffix + ".txt").write_text(note, encoding="utf-8")
    log.info("Backup %s -> %s", src.name, dest.name)
    prune_backups(dest_dir, keep)
    return dest


def prune_backups(dest_dir: Path, keep: int) -> None:
    files = sorted(
        (p for p in dest_dir.glob("*.xlsx") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
            sidecar = old.with_suffix(old.suffix + ".txt")
            if sidecar.exists():
                sidecar.unlink()
        except OSError:  # pragma: no cover
            pass


def list_backups(base_backup_dir: Path, file_code: str) -> list[dict]:
    d = backup_dir_for(base_backup_dir, file_code)
    out = []
    for p in sorted(d.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True):
        note_file = p.with_suffix(p.suffix + ".txt")
        out.append(
            {
                "name": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "created": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "note": note_file.read_text(encoding="utf-8") if note_file.exists() else "",
            }
        )
    return out


def rollback(backup_path: Path, target_path: Path, base_backup_dir: Path, file_code: str) -> Path:
    """Khoi phuc 1 ban backup. Truoc khi ghi de van backup ban hien tai (co the undo lan nua)."""
    target = Path(target_path)
    if target.exists():
        make_backup(target, base_backup_dir, file_code, note="auto-backup truoc khi rollback")
    shutil.copy2(backup_path, target)
    log.warning("ROLLBACK %s <- %s", target, backup_path)
    return target
