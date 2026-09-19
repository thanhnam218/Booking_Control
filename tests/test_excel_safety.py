"""An toan du lieu Excel - SRS muc 23, 37-41, 54, 70, 73.

Day la nhom test quan trong nhat: Bot khong duoc lam mat du lieu cua User.
"""

from __future__ import annotations

import datetime as dt

import pytest
from openpyxl import Workbook, load_workbook

from booking_bot.excel.backup import list_backups, make_backup, rollback, row_hash, snapshot
from booking_bot.excel.locking import FileLockedError, check_lock, owner_lock_file, safe_write
from booking_bot.excel.writer import ExcelTargetWriter, find_target_header, key_part

COLUMNS = ["NO", "EXFTY", "BUYER", "JO", "CBM", "TYPE"]
KEYS = ["JO", "EXFTY"]


@pytest.fixture
def target(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "PLAN"
    for i, h in enumerate(COLUMNS, start=1):
        ws.cell(row=1, column=i, value=h)
    p = tmp_path / "plan.xlsx"
    wb.save(p)
    return p


def writer(target, tmp_path, **kw):
    return ExcelTargetWriter(target, "F02", "PLAN", COLUMNS, KEYS,
                             backup_dir=tmp_path / "backups", lock_retry=[0], **kw)


def rec(jo, exfty, cbm=20.0, typ="1x20ST", no="1", buyer="ABC"):
    return {"NO": no, "EXFTY": exfty, "BUYER": buyer, "JO": jo, "CBM": cbm, "TYPE": typ}


def read_rows(path, sheet="PLAN"):
    wb = load_workbook(path)
    try:
        ws = wb[sheet]
        return [list(r) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]
    finally:
        wb.close()


# ---------------------------------------------------------------------------


def test_key_part_normalizes_dates():
    """Khoa phai giong nhau du Excel luu date, datetime hay chuoi."""
    d = dt.date(2026, 9, 20)
    assert key_part(d) == key_part(dt.datetime(2026, 9, 20, 0, 0)) == "20260920"
    assert key_part("JO-1") == key_part(" jo 1 ") == "JO1"
    assert key_part(10.0) == "10"


def test_find_target_header_is_case_and_accent_insensitive(tmp_path):
    wb = Workbook()
    ws = wb.active
    for i, h in enumerate(["no", "Exfty", "buyer", "Jo", "cbm", "type"], start=1):
        ws.cell(row=1, column=i, value=h)
    p = tmp_path / "x.xlsx"
    wb.save(p)
    wb2 = load_workbook(p)
    row, cmap = find_target_header(wb2["Sheet"], COLUMNS)
    wb2.close()
    assert row == 1
    assert set(cmap) == set(COLUMNS)


def test_insert_then_no_duplicate_on_rerun(target, tmp_path):
    """SRS muc 23 + 73: Bot chay nhieu lan khong duoc tao dong trung."""
    records = [rec("JO-1", dt.date(2026, 9, 20)), rec("JO-2", dt.date(2026, 9, 21))]
    w = writer(target, tmp_path)
    r1 = w.apply(records)
    assert (r1.inserted, r1.updated) == (2, 0)
    assert len(read_rows(target)) == 2

    r2 = writer(target, tmp_path).apply(records)
    assert (r2.inserted, r2.updated, r2.skipped) == (0, 0, 2)
    assert len(read_rows(target)) == 2


def test_same_jo_two_dates_are_two_rows(target, tmp_path):
    """JO tach 2 ngay xuat -> 2 dong, vi khoa gom ca EXFTY."""
    records = [rec("JO-1", dt.date(2026, 9, 20)), rec("JO-1", dt.date(2026, 9, 25))]
    writer(target, tmp_path).apply(records)
    assert len(read_rows(target)) == 2


def test_update_changes_only_changed_cells(target, tmp_path):
    writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20), cbm=20.0)])
    r = writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20), cbm=48.5)])
    assert r.updated == 1
    rows = read_rows(target)
    assert rows[0][4] == 48.5


def test_bot_never_erases_user_value_with_blank(target, tmp_path):
    """SRS muc 37: Bot khong duoc xoa du lieu User da nhap."""
    writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20), typ="1x40HC")])
    r = writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20), typ=None)])
    assert r.updated == 0
    assert read_rows(target)[0][5] == "1x40HC"


def test_conflict_detection_stops_write(target, tmp_path):
    """SRS muc 38-39: file doi tu luc Bot doc -> STOP, khong ghi de."""
    w = writer(target, tmp_path)
    stale = snapshot(target, with_hash=False).version_token

    # User sua file trong luc Bot dang xu ly
    wb = load_workbook(target)
    wb["PLAN"]["A5"] = "USER DA SUA"
    wb.save(target)
    wb.close()

    report = w.apply([rec("JO-9", dt.date(2026, 9, 20))], expected_version=stale)
    assert report.conflict is not None
    assert report.applied == 0
    assert "USER DA SUA" in str(read_rows(target))


def test_dry_run_writes_nothing(target, tmp_path):
    before = target.read_bytes()
    report = writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20))], dry_run=True)
    assert report.inserted == 1
    assert target.read_bytes() == before


def test_backup_created_before_write(target, tmp_path):
    writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20))])
    backups = list_backups(tmp_path / "backups", "F02")
    assert len(backups) == 1


def test_rollback_restores_previous_content(target, tmp_path):
    bdir = tmp_path / "backups"
    backup = make_backup(target, bdir, "F02", note="truoc khi ghi")
    writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20))])
    assert len(read_rows(target)) == 1

    rollback(backup, target, bdir, "F02")
    assert len(read_rows(target)) == 0
    assert len(list_backups(bdir, "F02")) >= 2  # ban truoc rollback cung duoc giu


def test_locked_file_is_detected_and_not_forced(target, tmp_path):
    """SRS muc 40: file dang mo -> retry roi vao Pending Queue, khong force."""
    marker = owner_lock_file(target)
    marker.write_bytes(b"\x00" * 16)
    try:
        assert check_lock(target).locked
        with pytest.raises(FileLockedError):
            safe_write(target, lambda p: p.write_bytes(b"x"), retry_seconds=[0, 0],
                       sleeper=lambda _: None)
        report = writer(target, tmp_path).apply([rec("JO-1", dt.date(2026, 9, 20))])
        assert report.error
        assert report.applied == 0
    finally:
        marker.unlink()


def test_safe_write_is_atomic(target, tmp_path):
    """Ghi loi giua chung khong duoc lam hong file goc."""
    original = target.read_bytes()

    def boom(_tmp):
        raise RuntimeError("loi giua chung")

    with pytest.raises(RuntimeError):
        safe_write(target, boom, retry_seconds=[0], sleeper=lambda _: None)
    assert target.read_bytes() == original


def test_row_hash_detects_user_edit():
    a = {"JO": "J1", "CBM": 20.0}
    assert row_hash(a) == row_hash({"CBM": 20, "JO": "J1"})   # thu tu khong quan trong
    assert row_hash(a) != row_hash({"JO": "J1", "CBM": 21.0})


def test_snapshot_changes_after_edit(target):
    s1 = snapshot(target)
    wb = load_workbook(target)
    wb["PLAN"]["A9"] = "x"
    wb.save(target)
    wb.close()
    assert snapshot(target).differs_from(s1)
