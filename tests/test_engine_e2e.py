"""End-to-end: quet bo du lieu demo va kiem tra toan bo tieu chi nghiem thu (SRS muc 73)."""

from __future__ import annotations

import datetime as dt

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from booking_control.db import session_scope
from booking_control.db.models import (
    AuditLog,
    ContainerAllocation,
    ExceptionRecord,
    Notification,
    Shipment,
    UserOverride,
)

TODAY = dt.date(2026, 9, 18)


@pytest.fixture
def scanned(engine):
    result = engine.run_scan(today=TODAY, deliver_notifications=False)
    return engine, result


def target_rows(path, sheet):
    wb = load_workbook(path)
    try:
        ws = wb[sheet]
        header = [c.value for c in ws[1]]
        out = []
        for r in ws.iter_rows(min_row=2, values_only=True):
            if any(r):
                out.append(dict(zip(header, r, strict=False)))
        return out
    finally:
        wb.close()


# ---------------------------------------------------------------------------


def test_scan_reads_all_follow_up_sheets(scanned):
    _, res = scanned
    assert res.files_scanned == 3
    assert res.sheets_scanned == 6      # ABC, XYZ, IKEA, SAMSUNG, DEF + sheet cau truc that
    assert res.shipments_total >= 20
    assert not res.errors


def test_statuses_are_computed(scanned):
    _, res = scanned
    c = res.status_counts
    assert c["WHITE"] and c["YELLOW"] and c["GREEN"] and c["RED"]
    assert c["COMPLETED"] >= 1 and c["PARTIAL"] >= 1 and c["CANCELLED"] >= 1


def test_red_wins_over_green(scanned):
    """JO-A1005 co du CDF + BL nhung dang HOLD -> phai la RED."""
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-A1005")).scalar_one()
        assert sh.cdf and sh.bl
        assert sh.effective_status == "RED"


def test_160_cbm_allocation(scanned):
    """Tieu chi nghiem thu: 160 CBM -> 2x40HC + 1x40DC."""
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-A1007")).scalar_one()
        a = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one()
    assert (a.qty_40hc, a.qty_40dc, a.qty_20st) == (2, 1, 0)
    assert a.estimated_capacity == 179
    assert a.excess_capacity == 19


def test_buyer_grouping_merges_same_day_lots(scanned):
    """IKEA dung shipment_group=by_exfty -> 2 JO cung ngay gop lai 1 container."""
    with session_scope() as s:
        a = s.execute(select(Shipment).where(Shipment.jo == "JO-I3001")).scalar_one()
        b = s.execute(select(Shipment).where(Shipment.jo == "JO-I3002")).scalar_one()
        assert a.group_key == b.group_key
        alloc = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == a.group_key)
        ).scalar_one()
    assert alloc.total_cbm == pytest.approx(60.5)
    assert alloc.qty_40hc == 1


def test_user_review_buyer_is_not_auto_decided(scanned):
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-D5001")).scalar_one()
        alloc = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one()
    assert alloc.allocation_status == "NEED_REVIEW"


def test_air_shipment_type_is_ac(scanned):
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-S4002")).scalar_one()
        alloc = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one()
    assert alloc.container_type == "AC"


def test_split_jo_creates_two_shipments(scanned):
    with session_scope() as s:
        rows = list(s.execute(select(Shipment).where(Shipment.jo == "JO-X2003")).scalars())
    assert len(rows) == 2
    assert len({r.exfty_date for r in rows}) == 2


def test_duplicate_and_invalid_data_go_to_exception_center(scanned):
    with session_scope() as s:
        codes = {e.code for e in s.execute(select(ExceptionRecord)).scalars()}
    assert "EX003" in codes   # duplicate
    assert "EX001" in codes   # ngay xuat sai
    assert "EX004" in codes   # CBM "25?"
    assert "EX009" in codes   # container can User duyet


def test_weekly_planning_written_at_t7(scanned):
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F02").path)
    rows = target_rows(path, "2026")
    assert rows, "Weekly Planning phai co du lieu"
    jos = {r["JO"] for r in rows}
    assert "JO-A1007" in jos                       # T-1, chac chan qua T-7
    assert "JO-A1001" not in jos                   # T-18, chua den T-7
    row = next(r for r in rows if r["JO"] == "JO-A1007")
    assert row["TYPE"] == "2x40HC + 1x40DC"
    assert row["CO"] == "Yes" and row["CO FORM"] == "Form D"


def test_custom_code_written(scanned):
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F03").path)
    rows = target_rows(path, "HS CODE")
    assert any(r["JO"] == "JO-A1007" for r in rows)


def test_shipping_schedule_written_at_t1_only(scanned):
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F05").path)
    rows = target_rows(path, "TGV") + target_rows(path, "TDV")
    jos = {r["JO"] for r in rows}
    assert "JO-A1007" in jos      # T-1
    assert "JO-A1004" not in jos  # T-6, chua den T-1


def test_rerun_is_idempotent(scanned):
    """Automation chay lai khong duoc tao dong trung o file dich."""
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F02").path)
    before = len(target_rows(path, "2026"))

    res2 = eng.run_scan(today=TODAY, deliver_notifications=False)
    after = len(target_rows(path, "2026"))
    assert after == before
    assert res2.shipments_new == 0
    assert all(w.inserted == 0 for w in res2.write_reports if w.file_code == "F02")


def test_follow_up_rows_get_status_color(scanned):
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F01-A").path)
    wb = load_workbook(path)
    try:
        ws = wb["ABC"]
        colors = {}
        for r in range(2, 10):
            jo = ws.cell(row=r, column=5).value
            fill = ws.cell(row=r, column=1).fill
            colors[jo] = str(fill.fgColor.rgb) if fill and fill.fgColor else ""
    finally:
        wb.close()
    assert colors["JO-A1005"].upper().endswith("FF0000")   # RED
    assert colors["JO-A1004"].upper().endswith("92D050")   # GREEN
    assert colors["JO-A1003"].upper().endswith("FFFF00")   # YELLOW


def test_user_override_survives_next_scan(scanned):
    """SRS muc 34-35 + 73: User thang Automation, ke ca sau khi Automation quet lai."""
    eng, _ = scanned
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-A1004")).scalar_one()
        assert sh.effective_status == "GREEN"
        s.add(UserOverride(shipment_code=sh.shipment_code, field="status",
                           user_value="YELLOW", user_name="tester", note="cho kiem tra lai"))

    eng.run_scan(today=TODAY, deliver_notifications=False)
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-A1004")).scalar_one()
    assert sh.auto_status == "GREEN"          # Automation van tinh dung
    assert sh.effective_status == "YELLOW"   # nhung hien thi theo User


def test_user_container_choice_survives(scanned):
    eng, _ = scanned
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-A1007")).scalar_one()
        alloc = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one()
        alloc.qty_40hc, alloc.qty_40dc = 3, 0
        alloc.container_type = "3x40HC"
        alloc.allocation_source = "USER"
        alloc.allocation_status = "USER_CONFIRMED"

    eng.run_scan(today=TODAY, deliver_notifications=False)
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "JO-A1007")).scalar_one()
        alloc = s.execute(
            select(ContainerAllocation).where(ContainerAllocation.group_key == sh.group_key)
        ).scalar_one()
    assert alloc.container_type == "3x40HC"
    assert alloc.allocation_source == "USER"


def test_exfty_change_is_audited_and_recalculated(scanned):
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F01-A").path)
    wb = load_workbook(path)
    try:
        ws = wb["ABC"]
        ws.cell(row=8, column=2).value = dt.datetime(2026, 10, 5)  # JO-A1007 doi ngay
        wb.save(path)
    finally:
        wb.close()

    eng.run_scan(today=TODAY, deliver_notifications=False)
    with session_scope() as s:
        actions = [a.action for a in s.execute(select(AuditLog)).scalars()]
    assert "EXFTY_CHANGED" in actions


def test_notifications_cover_all_deadline_types(scanned):
    with session_scope() as s:
        cats = {n.category for n in s.execute(select(Notification)).scalars()}
    assert {"BOOKING_DUE", "BOOKING_OVERDUE", "HOLD_REMINDER", "T7", "T1"} <= cats


def test_notification_dedup(scanned):
    eng, _ = scanned
    with session_scope() as s:
        before = len(list(s.execute(select(Notification)).scalars()))
    eng.run_scan(today=TODAY, deliver_notifications=False)
    with session_scope() as s:
        after = len(list(s.execute(select(Notification)).scalars()))
    assert after == before, "Quet lai trong cung ngay khong duoc ban lai thong bao trung"


def test_audit_log_records_bot_writes(scanned):
    with session_scope() as s:
        actions = {a.action for a in s.execute(select(AuditLog)).scalars()}
    assert "SHIPMENT_CREATED" in actions
    assert any(a.startswith("WRITE_") for a in actions)


def test_dry_run_does_not_touch_files(engine):
    path = engine.settings.resolve_path(engine.settings.file_by_code("F02").path)
    before = path.read_bytes()
    res = engine.run_scan(dry_run=True, today=TODAY, deliver_notifications=False)
    assert res.dry_run
    assert path.read_bytes() == before
    assert any(w.inserted > 0 for w in res.write_reports)   # van bao cao se ghi gi
    assert "DRY RUN" in res.render_text()


def test_stale_planning_row_is_reported_after_date_change(scanned):
    """Doi ngay xuat sau khi da ghi Planning -> Automation bao dong cu con sot, khong tu xoa."""
    eng, _ = scanned
    path = eng.settings.resolve_path(eng.settings.file_by_code("F01-A").path)
    wb = load_workbook(path)
    try:
        wb["ABC"].cell(row=8, column=2).value = dt.datetime(2026, 10, 5)  # JO-A1007
        wb.save(path)
    finally:
        wb.close()

    eng.run_scan(today=TODAY, deliver_notifications=False)
    with session_scope() as s:
        reasons = [e.reason for e in s.execute(select(ExceptionRecord)).scalars()]
    assert any("dong cu van con" in r for r in reasons)


def test_shipment_codes_unique_when_clock_day_differs_from_scan_day(engine):
    """Lo chua co EX-FACT DATE lay ngay quet de danh so.

    Truoc day bo dem thu tu dung `today` truyen vao con ma lo hang lai tu goi
    dt.date.today(). Qua nua dem (hoac quet lai du lieu cu) hai ngay lech nhau ->
    sinh trung shipment_code va ca lan quet do hong.
    """
    from freezegun import freeze_time

    # Dong ho that = mot ngay CO trong du lieu mau, khac han ngay quet
    with freeze_time("2026-09-23 08:00:00"):
        engine.run_scan(today=TODAY, deliver_notifications=False)

    with session_scope() as s:
        codes = list(s.execute(select(Shipment.shipment_code)).scalars())
    assert len(codes) == len(set(codes)), "shipment_code bi trung"
