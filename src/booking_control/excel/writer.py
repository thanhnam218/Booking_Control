"""Ghi du lieu ra cac file Excel - AN TOAN TUYET DOI (SRS muc 23, 37-41, 54).

Nguyen tac:
  1. Backup truoc khi ghi.
  2. Kiem tra version token; file da doi tu luc doc -> STOP, khong ghi de.
  3. File dang bi mo/lock -> retry 30/60/120s -> Pending Queue.
  4. UPSERT theo khoa nghiep vu, khong bao gio INSERT trung.
  5. Khong xoa gia tri User da nhap bang o trong; KHONG ghi de o co cong thuc.
  6. Ghi LOSSLESS qua xlsx_patch: chi sua dung o can sua, moi thu khac
     (cong thuc + gia tri tinh san, external link, defined name, comment,
     thiet lap in...) giu nguyen tung byte. Xem excel/xlsx_patch.py.

openpyxl CHI duoc dung de DOC (lap ke hoach), khong bao gio de luu.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from ..core.normalize import clean_text, norm_key
from ..logging_setup import get_logger
from .backup import make_backup, row_hash, snapshot
from .locking import FileLockedError, safe_write
from .xlsx_patch import XlsxPackage, XlsxPatchError, col_to_idx

log = get_logger("excel.writer")


class ConflictError(RuntimeError):
    """File dich da bi thay doi tu luc Automation doc -> khong duoc ghi de (SRS muc 38)."""

    def __init__(self, message: str, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


@dataclass(slots=True)
class WriteOp:
    kind: str  # INSERT | UPDATE | SKIP | COLOR
    sheet: str
    row: int | None
    key: str
    values: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    shipment_code: str = ""

    def describe(self) -> str:
        label = {"INSERT": "+", "UPDATE": "~", "SKIP": "=", "COLOR": "#"}.get(self.kind, "?")
        return f"{label} {self.sheet}!{self.row or 'new'} {self.key} {self.reason}".strip()


@dataclass(slots=True)
class WriteReport:
    file_code: str
    file_path: str
    dry_run: bool
    ops: list[WriteOp] = field(default_factory=list)
    applied: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    colored: int = 0
    formula_protected: int = 0
    formulas_copied: int = 0
    changed_parts: list[str] = field(default_factory=list)
    backup_path: str = ""
    error: str = ""
    conflict: dict[str, Any] | None = None

    def counts(self) -> dict[str, int]:
        return {
            "insert": self.inserted,
            "update": self.updated,
            "skip": self.skipped,
            "color": self.colored,
        }


# ---------------------------------------------------------------------------
# Tien ich tim header trong file dich
# ---------------------------------------------------------------------------


def find_target_header(
    ws: Worksheet, expected: list[str], max_rows: int = 15
) -> tuple[int, dict[str, int]]:
    """Tim dong header cua file dich dua tren danh sach ten cot mong doi.

    So sanh bang norm_key nen 'EXFTY DATE' == 'ExFtyDate' == 'exfty_date'.
    """
    want = {norm_key(e): e for e in expected}
    best_row, best_map, best_hits = 0, {}, 0
    max_col = min(ws.max_column or 1, 100)

    for r in range(1, min(ws.max_row or 1, max_rows) + 1):
        found: dict[str, int] = {}
        for c in range(1, max_col + 1):
            key = norm_key(ws.cell(row=r, column=c).value)
            if key and key in want and want[key] not in found:
                found[want[key]] = c
        if len(found) > best_hits:
            best_row, best_map, best_hits = r, found, len(found)

    return best_row, best_map


def first_empty_row(ws: Worksheet, header_row: int, key_col: int) -> int:
    """Dong trong dau tien sau du lieu - de APPEND."""
    last_filled = header_row
    for r in range(header_row + 1, (ws.max_row or header_row) + 1):
        if clean_text(ws.cell(row=r, column=key_col).value):
            last_filled = r
    return last_filled + 1


def key_part(value: Any) -> str:
    """Chuan hoa mot thanh phan khoa nghiep vu.

    Ngay phai ra cung mot chuoi du la date, datetime hay text trong Excel,
    neu khong Automation se INSERT trung o lan chay sau.
    """
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value.strftime("%Y%m%d")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return norm_key(value)


# ---------------------------------------------------------------------------
# Writer chinh
# ---------------------------------------------------------------------------


class ExcelTargetWriter:
    """UPSERT cac ban ghi vao 1 sheet cua file dich."""

    def __init__(
        self,
        path: str | Path,
        file_code: str,
        sheet: str | None,
        expected_columns: list[str],
        key_columns: list[str],
        *,
        backup_dir: Path,
        header_row: int | None = None,
        lock_retry: list[int] | None = None,
        backup: bool = True,
        backup_keep: int = 20,
        protect_user_edits: bool = True,
        verify_after_write: bool = True,
        extend_formulas: bool = True,
    ):
        self.path = Path(path)
        self.file_code = file_code
        self.sheet = sheet
        self.expected_columns = expected_columns
        self.key_columns = key_columns
        self.backup_dir = Path(backup_dir)
        self.header_row_hint = header_row
        self.lock_retry = lock_retry or [30, 60, 120]
        self.backup = backup
        self.backup_keep = backup_keep
        self.protect_user_edits = protect_user_edits
        self.verify_after_write = verify_after_write
        self.extend_formulas = extend_formulas

    # -- ho tro -----------------------------------------------------------

    def _pick_sheet(self, wb) -> Worksheet:
        if self.sheet and self.sheet in wb.sheetnames:
            return wb[self.sheet]
        if self.sheet:
            log.warning("Khong thay sheet '%s' trong %s, dung sheet khop nhat", self.sheet, self.path.name)
        best, best_hits = wb[wb.sheetnames[0]], -1
        for name in wb.sheetnames:
            _, cmap = find_target_header(wb[name], self.expected_columns)
            if len(cmap) > best_hits:
                best, best_hits = wb[name], len(cmap)
        return best

    def _row_key(self, ws: Worksheet, row: int, cmap: dict[str, int]) -> str:
        parts = []
        for k in self.key_columns:
            col = cmap.get(k)
            parts.append(key_part(ws.cell(row=row, column=col).value) if col else "")
        return "|".join(parts)

    @staticmethod
    def record_key(record: dict[str, Any], key_columns: list[str]) -> str:
        return "|".join(key_part(record.get(k)) for k in key_columns)

    def _header(self, ws: Worksheet) -> tuple[int, dict[str, int]]:
        header_row, cmap = find_target_header(ws, self.expected_columns)
        if self.header_row_hint:
            hr2, cmap2 = self.header_row_hint, {}
            for c in range(1, (ws.max_column or 1) + 1):
                key = norm_key(ws.cell(row=hr2, column=c).value)
                for e in self.expected_columns:
                    if norm_key(e) == key and e not in cmap2:
                        cmap2[e] = c
            if len(cmap2) >= len(cmap):
                header_row, cmap = hr2, cmap2
        return header_row, cmap

    # -- lap ke hoach -----------------------------------------------------

    def plan(self, records: list[dict[str, Any]]) -> tuple[WriteReport, dict[str, Any]]:
        """Tinh truoc se INSERT/UPDATE/SKIP nhung gi - phuc vu Dry Run (SRS muc 53)."""
        report = WriteReport(self.file_code, str(self.path), dry_run=True)
        if not self.path.exists():
            report.error = f"Khong tim thay file dich: {self.path}"
            return report, {}

        # Doc 2 lan: gia tri (de so khoa) + cong thuc (de biet o nao KHONG duoc dung)
        wb_v = load_workbook(self.path, data_only=True)
        wb_f = load_workbook(self.path, data_only=False)
        try:
            ws = self._pick_sheet(wb_v)
            ws_f = wb_f[ws.title]
            header_row, cmap = self._header(ws)
            if not cmap:
                report.error = (
                    f"Khong nhan dien duoc header trong {self.path.name}/{ws.title}. "
                    f"Mong doi cac cot: {', '.join(self.expected_columns)}"
                )
                return report, {}

            missing_cols = [c for c in self.expected_columns if c not in cmap]
            existing: dict[str, int] = {}
            key_col = cmap.get(self.key_columns[0]) or min(cmap.values())
            last_data_row = header_row
            for r in range(header_row + 1, (ws.max_row or header_row) + 1):
                k = self._row_key(ws, r, cmap)
                if k and k.strip("|"):
                    existing.setdefault(k, r)
                    last_data_row = r

            append_at = first_empty_row(ws, header_row, key_col)
            for rec in records:
                key = self.record_key(rec, self.key_columns)
                if not key.strip("|"):
                    report.ops.append(WriteOp("SKIP", ws.title, None, key, rec, "thieu khoa nghiep vu"))
                    report.skipped += 1
                    continue
                if key in existing:
                    row = existing[key]
                    changed = self._changed_fields(ws, ws_f, row, cmap, rec)
                    if changed:
                        report.ops.append(
                            WriteOp("UPDATE", ws.title, row, key, changed,
                                    f"cap nhat {len(changed)} o", rec.get("_shipment_code", ""))
                        )
                        report.updated += 1
                    else:
                        report.ops.append(WriteOp("SKIP", ws.title, row, key, {}, "khong doi"))
                        report.skipped += 1
                else:
                    report.ops.append(
                        WriteOp("INSERT", ws.title, append_at, key, rec, "them dong moi",
                                rec.get("_shipment_code", ""))
                    )
                    report.inserted += 1
                    existing[key] = append_at
                    append_at += 1

            meta = {
                "sheet": ws.title,
                "header_row": header_row,
                "column_map": cmap,
                "missing_columns": missing_cols,
                "last_data_row": last_data_row,
                "last_col": ws.max_column or max(cmap.values()),
                "version_token": snapshot(self.path, with_hash=False).version_token,
            }
            return report, meta
        finally:
            wb_v.close()
            wb_f.close()

    def _changed_fields(
        self, ws: Worksheet, ws_f: Worksheet, row: int, cmap: dict[str, int], rec: dict[str, Any]
    ) -> dict[str, Any]:
        changed: dict[str, Any] = {}
        for col_name, new_val in rec.items():
            if col_name.startswith("_") or col_name not in cmap:
                continue
            col = cmap[col_name]
            raw = ws_f.cell(row=row, column=col).value
            if isinstance(raw, str) and raw.startswith("="):
                continue  # o cong thuc: Automation khong bao gio dung vao
            cur = ws.cell(row=row, column=col).value
            if _same_value(cur, new_val):
                continue
            if self.protect_user_edits and clean_text(cur) and new_val in (None, ""):
                continue  # khong xoa du lieu User da nhap bang gia tri rong
            changed[col_name] = new_val
        return changed

    # -- thuc thi ---------------------------------------------------------

    def apply(
        self,
        records: list[dict[str, Any]],
        *,
        expected_version: str | None = None,
        dry_run: bool = False,
        note: str = "",
    ) -> WriteReport:
        report, meta = self.plan(records)
        report.dry_run = dry_run
        if report.error or dry_run:
            return report

        # --- Conflict check (SRS muc 38) ---------------------------------
        current_version = meta.get("version_token", "")
        if expected_version and current_version != expected_version:
            report.conflict = {
                "file": str(self.path),
                "expected_version": expected_version,
                "current_version": current_version,
                "message": "File da bi thay doi tu luc Automation doc. Automation KHONG ghi de.",
            }
            report.error = report.conflict["message"]
            log.error("CONFLICT %s: %s != %s", self.path.name, expected_version, current_version)
            return report

        todo = [op for op in report.ops if op.kind in ("INSERT", "UPDATE")]
        if not todo:
            return report

        if self.backup:
            try:
                report.backup_path = str(
                    make_backup(self.path, self.backup_dir, self.file_code, self.backup_keep, note)
                )
            except OSError as exc:  # pragma: no cover
                report.error = f"Khong backup duoc: {exc}"
                return report

        cmap: dict[str, int] = meta["column_map"]
        header_row: int = meta["header_row"]
        all_cols = list(range(1, int(meta["last_col"]) + 1))

        def _write(tmp_path: Path) -> None:
            pkg = XlsxPackage(self.path)
            sh = pkg.sheet(meta["sheet"])
            template = meta["last_data_row"] if meta["last_data_row"] > header_row else None
            for op in todo:
                written_cols: list[int] = []
                formula_cols: set[int] = set()
                if op.kind == "INSERT" and template:
                    sh.row_el(op.row, template=template)
                    sh.copy_row_styles(template, op.row, all_cols)
                    # Cot ma dong mau dang dung cong thuc -> dong moi cung dung cong thuc,
                    # Automation khong ghi gia tri cua minh vao (logic cua file la cua User)
                    formula_cols = {c for c in all_cols
                                    if sh.is_formula(sh.cell_el(template, c, create=False))}
                for col_name, value in op.values.items():
                    if col_name.startswith("_") or col_name not in cmap:
                        continue
                    col = cmap[col_name]
                    if col in formula_cols:
                        report.formula_protected += 1
                        continue
                    if sh.set_value(op.row, col, _excel_safe(value)):
                        written_cols.append(col)
                    else:
                        report.formula_protected += 1
                if op.kind == "INSERT" and template and self.extend_formulas:
                    rest = [c for c in all_cols if c not in written_cols]
                    report.formulas_copied += sh.copy_formulas(template, op.row, rest)
                if op.kind == "INSERT":
                    template = op.row
            report.changed_parts = pkg.save(tmp_path)

        try:
            safe_write(self.path, _write, self.lock_retry)
        except FileLockedError as exc:
            report.error = str(exc)
            log.warning("Dua vao Pending Queue: %s", exc)
            return report
        except (OSError, XlsxPatchError) as exc:
            report.error = f"Loi ghi file: {exc}"
            return report

        report.applied = len(todo)
        log.info("Ghi %s: +%s ~%s (=%s) | giu %s o cong thuc | keo %s cong thuc",
                 self.path.name, report.inserted, report.updated, report.skipped,
                 report.formula_protected, report.formulas_copied)

        if self.verify_after_write:
            ok, msg = self._verify(records)
            if not ok:
                report.error = f"Verify sau khi ghi that bai: {msg}"
        return report

    def _verify(self, records: list[dict[str, Any]]) -> tuple[bool, str]:
        wb = load_workbook(self.path, data_only=True)
        try:
            ws = self._pick_sheet(wb)
            _, cmap = self._header(ws)
            keys = {self._row_key(ws, r, cmap) for r in range(1, (ws.max_row or 1) + 1)}
            for rec in records:
                k = self.record_key(rec, self.key_columns)
                if k.strip("|") and k not in keys:
                    return False, f"khong tim thay ban ghi {k} sau khi ghi"
            return True, ""
        finally:
            wb.close()


# ---------------------------------------------------------------------------
# To mau dong trong file Follow Up (yeu cau goc: doi Themes Color theo dong)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ColorOp:
    sheet: str
    row: int
    color: str
    status: str
    shipment_code: str = ""
    previous: str = ""
    last_col: int = 0


class FollowUpColorWriter:
    """To mau dong theo trang thai - LOSSLESS, khong dung toi gia tri/cong thuc."""

    def __init__(
        self,
        path: str | Path,
        file_code: str,
        *,
        backup_dir: Path,
        lock_retry: list[int] | None = None,
        backup: bool = True,
        backup_keep: int = 20,
        skip_columns: list[str] | None = None,
        max_column: int | None = None,
    ):
        self.path = Path(path)
        self.file_code = file_code
        self.backup_dir = Path(backup_dir)
        self.lock_retry = lock_retry or [30, 60, 120]
        self.backup = backup
        self.backup_keep = backup_keep
        self.skip = {col_to_idx(c.strip().upper()) for c in (skip_columns or []) if c.strip()}
        self.max_column = max_column

    def apply(self, ops: list[ColorOp], dry_run: bool = False, note: str = "") -> WriteReport:
        report = WriteReport(self.file_code, str(self.path), dry_run=dry_run)
        report.ops = [color_op_as_write_op(o) for o in ops]
        report.colored = len(ops)
        if dry_run or not ops:
            return report
        if not self.path.exists():
            report.error = f"Khong thay file: {self.path}"
            return report

        # Lap ke hoach tren ban sao trong bo nho: neu khong o nao doi -> khong dung toi file
        try:
            pkg = XlsxPackage(self.path)
            for op in ops:
                sh = pkg.sheet(op.sheet)
                last = self.max_column or op.last_col or 1
                cols = [c for c in range(1, last + 1) if c not in self.skip]
                report.applied += 1 if sh.fill_row(op.row, op.color, cols) else 0
        except XlsxPatchError as exc:
            report.error = f"Khong doc duoc cau truc file: {exc}"
            return report

        if not pkg.changed:
            report.colored = 0
            return report

        if self.backup:
            report.backup_path = str(
                make_backup(self.path, self.backup_dir, self.file_code, self.backup_keep, note)
            )

        def _write(tmp_path: Path) -> None:
            report.changed_parts = pkg.save(tmp_path)

        try:
            safe_write(self.path, _write, self.lock_retry)
        except FileLockedError as exc:
            report.error = str(exc)
            report.applied = 0
        return report


def color_op_as_write_op(op: ColorOp) -> WriteOp:
    return WriteOp(
        kind="COLOR",
        sheet=op.sheet,
        row=op.row,
        key=op.shipment_code,
        values={"color": op.color, "status": op.status},
        reason=f"{op.previous or '?'} -> {op.status}",
        shipment_code=op.shipment_code,
    )


# ---------------------------------------------------------------------------


def _same_value(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, dt.datetime) and isinstance(b, dt.date):
        return a.date() == (b.date() if isinstance(b, dt.datetime) else b)
    if isinstance(b, dt.datetime) and isinstance(a, dt.date):
        return b.date() == a
    return clean_text(a) == clean_text(b)


def _excel_safe(value: Any) -> Any:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value


def payload_fingerprint(record: dict[str, Any]) -> str:
    return row_hash({k: v for k, v in record.items() if not k.startswith("_")})
