"""v2: dang nhap + phan quyen + phan cong + luoi Follow Up + xuat/nhap Excel + USER > AUTOMATION.

Chay tren bo du lieu demo (3 file Follow Up, 37 lo) qua FastAPI TestClient.
"""

from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from booking_control.db import session_scope
from booking_control.db.models import (
    AssignmentHistory,
    AuditLog,
    ExceptionRecord,
    JobRun,
    Notification,
    Shipment,
)
from booking_control.web.auth import upsert_user

PW = "Passw0rd!"
ROLES = [("admin", "admin"), ("manager", "manager"), ("user_a", "user"), ("user_b", "user"),
         ("user_c", "user"), ("viewer1", "viewer")]


@pytest.fixture
def app(engine, today):
    engine.run_scan(today=today)
    with session_scope() as s:
        for name, role in ROLES:
            upsert_user(s, name, role=role, password=PW)
    from booking_control.web.app import create_app

    return create_app(engine, None)


def login(app, user: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": user, "password": PW}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    page = c.get("/account")
    c.headers["X-CSRF-Token"] = re.search(r'name="csrf" content="([^"]+)"', page.text).group(1)
    return c


def col(engine, field: str) -> int:
    return engine.template.fields.index(field)


# ---------------------------------------------------------------------------
# Dang nhap / CSRF / setup
# ---------------------------------------------------------------------------


def test_pages_require_login(app):
    c = TestClient(app)
    r = c.get("/followup", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert c.get("/api/followup/rows").status_code == 401
    assert c.get("/export/followup.xlsx", headers={"accept": "application/json"}).status_code == 401


def test_wrong_password_then_lockout(app, engine):
    c = TestClient(app)
    for _ in range(engine.settings.auth.max_failed_logins):
        assert c.post("/login", data={"username": "user_a", "password": "sai-mat-khau1"}).status_code == 401
    r = c.post("/login", data={"username": "user_a", "password": PW})
    assert r.status_code == 429  # khoa tam du nhap dung
    with session_scope() as s:
        assert s.execute(select(AuditLog).where(AuditLog.action == "LOGIN_FAILED")).first()


def test_write_without_csrf_is_rejected(app):
    c = login(app, "user_a")
    token = c.headers.pop("X-CSRF-Token")
    assert c.post("/api/followup/save", json={"changes": []}).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.post("/api/followup/save", json={"changes": []}).status_code == 200


def test_first_admin_setup_only_when_no_admin(engine):
    from booking_control.web.app import create_app

    c = TestClient(create_app(engine, None))
    assert c.get("/login", follow_redirects=False).headers["location"] == "/setup"
    r = c.post("/setup", data={"username": "boss", "display_name": "Sep", "password": "Abcdef12",
                               "password2": "Abcdef12"}, follow_redirects=False)
    assert r.status_code == 303
    # Da co admin -> trang setup dong lai
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_temp_password_forces_change(app):
    admin = login(app, "admin")
    r = admin.post("/admin/users", data={"name": "new.user", "display_name": "Moi", "role": "user"})
    temp = re.search(r"<code[^>]*>([^<]+)</code>", r.text).group(1)
    c = TestClient(app)
    r = c.post("/login", data={"username": "new.user", "password": temp}, follow_redirects=False)
    assert r.headers["location"] == "/account/password"
    assert c.get("/followup", follow_redirects=False).headers["location"] == "/account/password"


# ---------------------------------------------------------------------------
# Phan quyen: pham vi du lieu
# ---------------------------------------------------------------------------


def test_user_sees_only_assigned_rows_manager_sees_all(app):
    a = login(app, "user_a").get("/api/followup/rows").json()["rows"]
    m = login(app, "manager").get("/api/followup/rows").json()["rows"]
    assert len(m) == 37
    assert 0 < len(a) < len(m)
    assert all("user_a" in (r["assigned_to"], r["backup_owner"]) for r in a)


def test_user_cannot_open_or_edit_foreign_shipment(app):
    m = login(app, "manager").get("/api/followup/rows").json()["rows"]
    foreign = next(r for r in m if "user_a" not in (r["assigned_to"], r["backup_owner"]))
    a = login(app, "user_a")
    assert a.get(f"/shipments/{foreign['code']}").status_code == 404
    res = a.post("/api/followup/save", json={"changes": [{"code": foreign["code"], "values": {"Remark": "x"}}]})
    assert foreign["code"] in res.json()["errors"]


def test_admin_pages_forbidden_for_manager_and_user(app):
    m, u = login(app, "manager"), login(app, "user_a")
    for path in ("/admin/users", "/mapping", "/settings", "/backups", "/buyers"):
        assert m.get(path).status_code == 403
    for path in ("/assignments", "/automation", "/import", "/audit"):
        assert u.get(path).status_code == 403
        assert m.get(path).status_code == 200


def test_viewer_is_read_only(app):
    v = login(app, "viewer1")
    rows = v.get("/api/followup/rows").json()["rows"]
    assert len(rows) == 37
    assert v.post("/api/followup/save", json={"changes": [{"code": rows[0]["code"], "values": {"Remark": "x"}}]}
                  ).status_code == 403


# ---------------------------------------------------------------------------
# Phan cong
# ---------------------------------------------------------------------------


def test_scan_auto_assigns_by_rule_then_file_owner(app):
    with session_scope() as s:
        ships = list(s.execute(select(Shipment)).scalars())
        assert all(sh.assigned_to for sh in ships)  # moi lo deu co nguoi phu trach
        samsung = [sh for sh in ships if sh.buyer_code == "SAMSUNG"]
        assert samsung and all(sh.assigned_to == "user_b" and sh.backup_owner == "user_a" for sh in samsung)
        own = [sh for sh in ships if sh.buyer_code != "SAMSUNG"]
        assert all(sh.assigned_to == sh.owner_user for sh in own)
        assert s.execute(select(AssignmentHistory).where(AssignmentHistory.method == "FILE_OWNER")).first()


def test_manager_bulk_assign_and_rescan_never_overrides(app, engine, today):
    m = login(app, "manager")
    rows = m.get("/api/followup/rows?buyer=XYZ").json()["rows"]
    res = m.post("/api/followup/assign", json={"codes": [r["code"] for r in rows], "to_user": "user_c"}).json()
    assert res["changed"] == len(rows) > 0
    engine.run_scan(today=today)  # Automation chay lai
    with session_scope() as s:
        xyz = list(s.execute(select(Shipment).where(Shipment.buyer_code == "XYZ")).scalars())
        assert all(sh.assigned_to == "user_c" for sh in xyz)
        note = s.execute(select(Notification).where(Notification.recipient == "user_c",
                                                    Notification.category == "ASSIGNED")
                         .order_by(Notification.id.desc())).scalars().first()
        assert note and f"{len(rows)} lô" in note.title  # 1 thong bao gom nhom
    assert len(login(app, "user_c").get("/api/followup/rows?buyer=XYZ").json()["rows"]) == len(rows)


def test_bulk_assign_by_filter(app):
    m = login(app, "manager")
    res = m.post("/api/followup/assign", json={"by_filter": {"buyer": "ABC", "mode": "AIR"}, "to_user": "user_b",
                                               "kind": "BACKUP"}).json()
    with session_scope() as s:
        n = len([x for x in s.execute(select(Shipment).where(Shipment.buyer_code == "ABC")).scalars()
                 if x.ship_mode == "AIR"])
    assert res["changed"] + res["unchanged"] == n


def test_user_cannot_assign(app):
    u = login(app, "user_a")
    assert u.post("/api/followup/assign", json={"codes": [], "to_user": "user_a"}).status_code == 403


# ---------------------------------------------------------------------------
# Luoi Follow Up: sua, cong thuc, optimistic lock, quyen tung cot
# ---------------------------------------------------------------------------


def _mine_real(c, engine):
    rows = c.get("/api/followup/rows").json()["rows"]
    return next(r for r in rows if r["cells"][col(engine, "Invoice")] == "TEGV03001")


def test_edit_recalculates_formulas_and_status(app, engine):
    c = login(app, "user_c")
    r = _mine_real(c, engine)
    res = c.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"], "values": {
        "Price": "20", "FinalExportQty": "1000"}}]}).json()
    new = res["rows"][0]
    assert not res["errors"]
    assert new["v"] == r["v"] + 1
    assert new["cells"][col(engine, "DiscountPrice")] == 19        # ROUND(20*0.95,2)
    assert new["cells"][col(engine, "CdfAmount")] == 19000         # 95% FOB x Final Qty
    assert new["cells"][col(engine, "TsnAmount")] == 20000
    assert new["cells"][col(engine, "ShipmentNo")] == 893          # NO van dung cong thuc
    assert set(new["manual"]) == {"Price", "FinalExportQty"}
    with session_scope() as s:
        audit = s.execute(select(AuditLog).where(AuditLog.action == "FIELD_CHANGED",
                                                 AuditLog.entity_id == r["code"])).scalars().all()
        assert {a.field for a in audit} == {"Price", "FinalExportQty"} and audit[0].actor_name == "user_c"


def test_remark_hold_turns_row_red(app, engine):
    c = login(app, "user_c")
    r = _mine_real(c, engine)
    res = c.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"],
                                                          "values": {"Remark": "HOLD cho khach"}}]}).json()
    assert res["rows"][0]["status"] == "RED"


def test_stale_version_is_rejected(app, engine):
    c = login(app, "user_c")
    r = _mine_real(c, engine)
    c.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"], "values": {"Remark": "a"}}]})
    res = c.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"],
                                                          "values": {"Remark": "b"}}]}).json()
    assert res["conflicts"] == [r["code"]]
    assert res["rows"][0]["cells"][col(engine, "Remark")] == "a"  # khong ghi de


def test_column_permissions_and_review(app, engine):
    c = login(app, "user_c")
    r = _mine_real(c, engine)
    res = c.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"], "values": {
        "DiscountPrice": "1", "ShipmentNo": "5", "Buyer": "KRX", "FinalCBM": "abc"}}]}).json()
    errs = res["errors"][r["code"]]
    assert set(errs) == {"DiscountPrice", "ShipmentNo", "FinalCBM"}   # cong thuc / chi Quan ly / sai kieu
    assert res["rows"][0]["cells"][col(engine, "Buyer")] == "KRX"     # Buyer: sua duoc nhung can duyet
    with session_scope() as s:
        assert s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX016")).first()


def test_manager_can_override_formula_and_clear_it(app, engine):
    m = login(app, "manager")
    r = _mine_real(m, engine)
    res = m.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"],
                                                          "values": {"ShipmentNo": "777"}}]}).json()
    row = res["rows"][0]
    assert row["cells"][0] == 777 and "ShipmentNo" in row["manual"]
    res = m.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": row["v"],
                                                          "values": {"ShipmentNo": ""}}]}).json()
    row = res["rows"][0]
    assert row["cells"][0] == 893 and "ShipmentNo" not in row["manual"]  # xoa trang -> cong thuc chay lai


# ---------------------------------------------------------------------------
# USER > AUTOMATION khi Excel doi
# ---------------------------------------------------------------------------


def test_web_edit_survives_excel_rescan_and_conflict_goes_to_exception(app, engine, today, workspace):
    c = login(app, "user_c")
    r = _mine_real(c, engine)
    c.post("/api/followup/save", json={"changes": [{"code": r["code"], "v": r["v"], "values": {"Vessel": "WEB VESSEL"}}]})
    engine.run_scan(today=today)  # Excel khong doi -> giu gia tri web, khong conflict
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == r["code"])).scalar_one()
        assert sh.vessel == "WEB VESSEL"
        assert not s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX015")).first()
        row_idx, sheet = sh.source_row, sh.source_sheet

    # Ai do sua VESSEL trong file Excel -> van giu web, bao EX015 cho Quan ly
    from openpyxl import load_workbook as lw

    path = next(f.path for f in workspace.files if f.id == "F01-C")
    wb = lw(path)
    wb[sheet].cell(row_idx, 31).value = "EXCEL VESSEL"
    wb.save(path)
    engine.run_scan(today=today)
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == r["code"])).scalar_one()
        assert sh.vessel == "WEB VESSEL"
        exc = s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX015")).scalar_one()
        exc_id = exc.id

    m = login(app, "manager")
    m.post(f"/exceptions/{exc_id}/resolve", data={"decision": "excel", "status": "RESOLVED"})
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == r["code"])).scalar_one()
        assert sh.vessel == "EXCEL VESSEL" and "Vessel" not in (sh.field_src or {})


# ---------------------------------------------------------------------------
# Tao lo tren web
# ---------------------------------------------------------------------------


def test_create_web_shipment_and_rescan_keeps_it(app, engine, today):
    u = login(app, "user_a")
    res = u.post("/api/followup/create", json={"values": {
        "JO": "WEB-001", "ExFtyDate": "2026-10-05", "Buyer": "ABC", "FinalCBM": "33", "ShipMode": "S",
        "Invoice": "TEGV03050", "Price": "10", "FinalExportQty": "100"}}).json()
    row = res["row"]
    assert row["assigned_to"] == "user_a" and row["origin"] == "WEB" and row["status"] == "WHITE"
    assert row["cells"][0] == 942 and row["cells"][col(engine, "TsnAmount")] == 1000
    dup = u.post("/api/followup/create", json={"values": {"JO": "WEB-001", "ExFtyDate": "05/10/2026",
                                                          "Buyer": "ABC"}})
    assert dup.status_code == 400
    assert u.post("/api/followup/create", json={"values": {"JO": "X"}}).status_code == 400
    engine.run_scan(today=today)
    rows = u.get("/api/followup/rows?q=WEB-001").json()["rows"]
    assert len(rows) == 1 and rows[0]["status"] == "WHITE"
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.jo == "WEB-001")).scalar_one()
        assert sh.forwarder == "FWD-OCEAN-1" and sh.forwarder_status == "AUTO_ASSIGNED"


def test_archive_hides_row(app):
    m = login(app, "manager")
    code = m.get("/api/followup/rows").json()["rows"][0]["code"]
    assert m.post("/api/followup/archive", json={"codes": [code]}).json()["changed"] == 1
    assert code not in {r["code"] for r in m.get("/api/followup/rows").json()["rows"]}
    assert login(app, "user_a").post("/api/followup/archive", json={"codes": [code]}).status_code == 403


# ---------------------------------------------------------------------------
# Xuat Excel
# ---------------------------------------------------------------------------


def test_export_my_shipments_one_sheet_per_buyer(app, engine):
    from make_sample_data import REAL_HEADERS  # type: ignore

    u = login(app, "user_c")
    mine = u.get("/api/followup/rows").json()["rows"]
    r = u.get("/export/followup.xlsx?scope=all")  # user xin "all" -> backend van chi tra lo cua minh
    assert r.status_code == 200 and "user_c_Shipment_" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    buyers = sorted({row["cells"][col(engine, "Buyer")] for row in mine})
    assert wb.sheetnames == buyers
    ws = wb[wb.sheetnames[0]]
    assert [ws.cell(1, i).value for i in range(1, 48)] == REAL_HEADERS
    assert ws.cell(1, 48).value == "ASSIGNED USER"
    assert ws.freeze_panes == "K2"
    total = sum(wb[n].max_row - 1 for n in wb.sheetnames)
    assert total == len(mine)
    formulas = [ws.cell(i, 1).value for i in range(2, ws.max_row + 1)]
    assert any(str(f).startswith('=IF(LEFT(M') for f in formulas)
    assert str(ws["X2"].value).startswith("=ROUND(W2*0.95")
    assert any(rng.min_col == 4 for rng in ws.merged_cells.ranges)  # CONT TYPE merge theo nhom


def test_manager_export_by_filter_and_sheet_names():
    from booking_control.services.excel_export import sheet_name

    used: set[str] = set()
    assert sheet_name("ABC INTERNATIONAL TRADING COMPANY", used) == "ABC INTERNATIONAL TRADING"
    assert sheet_name("ABC INTERNATIONAL TRADING COMPANY LTD", used) == "ABC INTERNATIONAL TRADING_2"
    assert sheet_name("A/B:C*?", used) == "A B C"
    assert all(len(x) <= 31 for x in used)


def test_manager_filtered_export(app):
    m = login(app, "manager")
    r = m.get("/export/followup.xlsx?scope=filter&buyer=ABC")
    assert load_workbook(io.BytesIO(r.content)).sheetnames == ["ABC"]


# ---------------------------------------------------------------------------
# Nhap Excel qua web
# ---------------------------------------------------------------------------


def test_import_preview_then_apply_is_idempotent(app, sample_dir):
    m = login(app, "manager")
    data = (sample_dir / "FollowUp_UserC_CauTrucThat.xlsx").read_bytes()
    files = {"file": ("FollowUp_Moi.xlsx", data,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = m.post("/import/preview", files=files, data={"owner": "user_b"})
    assert r.status_code == 200 and "THEM MOI" in r.text
    token = re.search(r'name="token" value="([^"]+)"', r.text).group(1)
    with session_scope() as s:
        before = s.execute(select(Shipment)).scalars().all().__len__()
    r = m.post("/import/apply", data={"token": token, "owner": "user_b"})
    assert "Đã nhập" in r.text
    with session_scope() as s:
        after = list(s.execute(select(Shipment)).scalars())
        added = [x for x in after if x.source_file_code.startswith("UPLOAD:")]
        assert len(after) > before and added and all(x.assigned_to == "user_b" for x in added)
    # nhap lai cung file -> cap nhat, khong tao trung
    r = m.post("/import/preview", files=files, data={"owner": "user_b"})
    assert "THEM MOI" not in r.text.split("<tbody>")[-1]


# ---------------------------------------------------------------------------
# Automation job
# ---------------------------------------------------------------------------


def test_job_run_records_retries(engine):
    from booking_control.scheduler import AutomationScheduler

    sched = AutomationScheduler(engine, sleep=lambda _s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("Outlook mat ket noi")
        return {"mails": 3}

    run = sched.run_job("outlook_scan", flaky, retries=2)
    assert run.status == "SUCCESS" and run.retry_count == 1 and run.summary == {"mails": 3}
    run = sched.run_job("outlook_scan", lambda: 1 / 0, retries=1)
    assert run.status == "FAILED" and run.retry_count == 1 and "ZeroDivisionError" in run.error
    result = sched.scan_now(triggered_by="tester")
    assert result.shipments_total == 37
    with session_scope() as s:
        jobs = [j.job_name for j in s.execute(select(JobRun)).scalars()]
        assert jobs.count("followup_scan") == 1 and jobs.count("outlook_scan") == 2
