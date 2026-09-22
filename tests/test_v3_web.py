"""v3 tren web: phan quyen (Auditor), booking split / hold / rebook, hang cho duyet email,
chung tu + version, task B1-B15.1, Weekly Planning (han 12:00 thu 6).

Du lieu demo: user_a phu trach ABC + XYZ, user_b IKEA / SAMSUNG / DEF, user_c KRB.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from booking_control.core import weekly as wk
from booking_control.core import workflow as wf
from booking_control.db import session_scope
from booking_control.db.models import ExceptionRecord, Notification, Shipment
from booking_control.db.models_v3 import Booking, Document, DocumentVersion, Email, WorkflowTask
from booking_control.integrations.outlook_demo import demo_messages, mini_pdf
from booking_control.integrations.pdf_text import extract_text
from booking_control.services import weekly_service as ws
from booking_control.services.email_service import EmailProcessor
from booking_control.web.auth import upsert_user

PW = "Passw0rd!"
ROLES = [("admin", "admin"), ("manager", "manager"), ("user_a", "user"), ("user_b", "user"), ("user_c", "user"),
         ("viewer1", "viewer"), ("auditor1", "auditor")]


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


def code_of(jo: str, exfty: str | None = None) -> str:
    with session_scope() as s:
        q = select(Shipment.shipment_code).where(Shipment.jo == jo)
        if exfty:
            q = q.where(Shipment.exfty_date == dt.date.fromisoformat(exfty))
        return s.execute(q).scalars().first()


def post(c: TestClient, url: str, data=None, **kw):
    """POST form, khong theo redirect -> tra (status, location da giai ma)."""
    r = c.post(url, data=data or {}, follow_redirects=False, **kw)
    return r.status_code, unquote(r.headers.get("location", ""))


# ---------------------------------------------------------------------------
# Phan quyen
# ---------------------------------------------------------------------------


def test_new_pages_render_for_every_role(app):
    for user, _ in ROLES:
        c = login(app, user)
        for path in ("/", "/bookings", "/emails", "/tasks", "/weekly", f"/shipments/{code_of('JO-A1007')}"):
            if user in ("user_b", "user_c") and path.startswith("/shipments/"):
                continue
            r = c.get(path)
            assert r.status_code == 200, (user, path, r.text[:300])
        assert "Email chờ duyệt" in c.get("/").text


def test_auditor_reads_everything_but_cannot_change(app):
    c = login(app, "auditor1")
    code = code_of("JO-A1007")
    assert c.get("/admin/permissions").status_code == 200
    assert "Kiểm soát (Auditor)" in c.get("/admin/permissions").text
    assert c.get("/audit").status_code == 200
    assert "JO-A1007" in c.get("/shipments").text and "JO-S4002" in c.get("/shipments").text
    assert c.get("/bookings/new").status_code == 403
    assert post(c, "/bookings/new", {"code": code, "qty": "", "status": "REQUESTED"})[0] == 403
    assert post(c, f"/shipments/{code}/documents", {"doc_type": "INVOICE"},
                files={"file": ("inv.pdf", b"%PDF-1.4 x", "application/pdf")})[0] == 403
    assert post(c, "/weekly/confirm")[0] == 403
    with session_scope() as s:
        bid = s.execute(select(Booking.id).where(Booking.booking_no == "BKG-77124")).scalar_one()
    assert post(c, f"/bookings/{bid}/status", {"new_status": "CONFIRMED"})[0] == 403
    assert login(app, "user_a").get("/admin/permissions").status_code == 403


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def test_split_booking_partial_shipment_via_web(app):
    c = login(app, "user_a")
    code = code_of("JO-A1001")  # 5000 pcs, chua booking
    st, loc = post(c, "/bookings/new", {"code": [code], "qty": ["3000"], "status": "CONFIRMED",
                                        "booking_no": "SPL-100", "etd": "2026-10-08", "vessel": "SPLIT ONE"})
    assert st == 303 and loc.startswith("/bookings/") and "msg=" in loc, loc
    b1 = int(loc.split("/bookings/")[1].split("?")[0])
    with session_scope() as s:
        assert s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX018",
                                                       ExceptionRecord.shipment_code == code)).first()
    page = c.get(f"/bookings/{b1}").text
    assert "PARTIAL" in page and "thiếu 2,000" in page
    st, loc = post(c, "/bookings/new", {"code": [code], "qty": ["2000"], "status": "CONFIRMED",
                                        "booking_no": "SPL-101", "etd": "2026-10-10"})
    assert "msg=" in loc
    assert "FULL" in c.get(f"/bookings/{b1}").text
    # trung so booking dang hieu luc -> bao loi, khong tao
    st, loc = post(c, "/bookings/new", {"code": [code], "qty": [""], "booking_no": "SPL-100"})
    assert "err=" in loc and "da ton tai" in loc

    st, loc = post(c, f"/shipments/{code}/executions", {"qty": "3000", "ship_date": "2026-10-08",
                                                         "booking_id": str(b1), "container": "TCLU1234567"})
    assert "msg=" in loc
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one()
        assert sh.ship_status == "PARTIALLY_SHIPPED" and sh.effective_status == "PARTIAL"
        assert sh.booking_no in ("SPL-100", "SPL-101")
    # nguoi khac khong thay booking cua lo khong duoc giao
    assert login(app, "user_b").get(f"/bookings/{b1}").status_code == 404
    assert login(app, "viewer1").get(f"/bookings/{b1}").status_code == 200


def test_hold_cancel_rebook_via_web(app):
    c = login(app, "user_a")
    code = code_of("JO-A1007")
    with session_scope() as s:
        bid = s.execute(select(Booking.id).where(Booking.booking_no == "BKG-77124")).scalar_one()
    assert "err=" in post(c, f"/bookings/{bid}/status", {"new_status": "ON_HOLD"})[1]  # thieu ly do
    assert "msg=" in post(c, f"/bookings/{bid}/status", {"new_status": "ON_HOLD", "reason": "Buyer hold"})[1]
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one()
        assert sh.hold_status == "HOLD" and sh.effective_status == "RED"
    assert "Đang HOLD" in c.get(f"/bookings/{bid}").text
    assert login(app, "viewer1").post(f"/bookings/{bid}/status", data={"new_status": "CONFIRMED"}).status_code == 403

    st, loc = post(c, f"/bookings/{bid}/rebook", {"booking_no": "BKG-99001", "etd": "2026-09-24",
                                                  "vessel": "NEW HOPE", "reason": "Rot cho"})
    new_id = int(loc.split("/bookings/")[1].split("?")[0])
    assert new_id != bid
    with session_scope() as s:
        old, new = s.get(Booking, bid), s.get(Booking, new_id)
        assert old.status == "CANCELLED" and old.replacement_booking_id == new_id
        assert new.original_booking_id == bid and new.status == "CONFIRMED"
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one()
        assert sh.booking_no == "BKG-99001" and sh.etd == dt.date(2026, 9, 24)
        assert sh.booking_status == "CONFIRMED" and sh.effective_status != "RED"
        new_version = new.version
    page = c.get(f"/shipments/{code}").text
    assert "BKG-99001" in page and "REBOOKED" in page
    # sua ETD booking tren web -> ghi sang luoi Follow Up
    st, loc = post(c, f"/bookings/{new_id}/update", {"etd": "2026-09-25", "version": str(new_version)})
    assert "msg=" in loc
    with session_scope() as s:
        assert s.execute(select(Shipment.etd).where(Shipment.shipment_code == code)).scalar_one() == \
            dt.date(2026, 9, 25)


# ---------------------------------------------------------------------------
# Email Outlook - hang cho duyet
# ---------------------------------------------------------------------------


def test_email_review_queue_flow(app, engine, today):
    with session_scope() as s:
        out = EmailProcessor(engine).ingest(s, demo_messages(s, dt.datetime(2026, 9, 18, 15, 0)), today)
    assert out["new"] == 5 and out["review"] == 2 and out["conflict"] == 1 and out["unmatched"] == 1
    with session_scope() as s:
        req = s.execute(select(Email).where(Email.message_id == "<demo-1@booking-control.local>")).scalar_one()
        conf = s.execute(select(Email).where(Email.message_id == "<demo-2@fwd-ocean1.example.com>")).scalar_one()
        amd = s.execute(select(Email).where(Email.message_id == "<demo-3@fwd-ocean1.example.com>")).scalar_one()
        req_id, conf_id, amd_id, code = req.id, conf.id, amd.id, req.shipment_codes[0]
        hold = s.execute(select(Email).where(Email.message_id == "<demo-4@fwd-ocean1.example.com>")).scalar_one()
        assert hold.match_status == "APPLIED"

    a = login(app, "user_a")
    listing = a.get("/emails").text
    assert "Booking request" in listing
    detail = a.get(f"/emails/{req_id}").text
    assert "Lô hàng gợi ý" in detail and "JO" in detail
    assert login(app, "user_b").get(f"/emails/{req_id}").status_code == 404

    st, loc = post(a, f"/emails/{req_id}/apply", {"code": [code], "category": "BOOKING_REQUEST"})
    assert "msg=" in loc, loc
    st, loc = post(a, f"/emails/{conf_id}/apply", {"code": [code], "category": "BOOKING_CONFIRMATION"})
    assert "msg=" in loc, loc
    with session_scope() as s:
        sh = s.execute(select(Shipment).where(Shipment.shipment_code == code)).scalar_one()
        assert sh.booking_no.startswith("DMO") and sh.booking_status == "CONFIRMED"
    assert "err=" in post(a, f"/emails/{conf_id}/apply", {"code": [code]})[1]  # da xu ly roi

    m = login(app, "manager")
    assert "Email ≠ PDF" in m.get("/emails").text
    with session_scope() as s:
        amd = s.get(Email, amd_id)
        codes = amd.shipment_codes
    assert "err=" in post(m, f"/emails/{amd_id}/apply", {"code": codes})[1]       # chua chon ETD
    assert "msg=" in post(m, f"/emails/{amd_id}/apply", {"code": codes, "choice_etd": "pdf"})[1]
    with session_scope() as s:
        amd = s.get(Email, amd_id)
        assert amd.match_status == "APPLIED"
        doc = s.execute(select(Document).where(Document.shipment_code == codes[0],
                                               Document.doc_type == "BOOKING")).scalar_one()
        assert doc.current_version == 1
        att_id = s.execute(select(DocumentVersion.id).where(DocumentVersion.document_id == doc.id)).scalar_one()
    r = m.get(f"/documents/versions/{att_id}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    unmatched = login(app, "user_b").get("/emails?tab=UNMATCHED").text
    assert "AIR77881" in unmatched or "AWB" in unmatched  # email chua ghep: ai cung thay de nhan ve


def test_mini_pdf_is_readable():
    text, status, _ = extract_text(mini_pdf(["Booking No: ABC123", "ETD: 03 OCT 2026"]))
    assert status == "OK" and "ABC123" in text


# ---------------------------------------------------------------------------
# Chung tu + task
# ---------------------------------------------------------------------------


def _tasks(code: str) -> dict[str, WorkflowTask]:
    with session_scope() as s:
        out = {t.step_code: t for t in s.execute(select(WorkflowTask).where(
            WorkflowTask.shipment_code == code)).scalars()}
        for t in out.values():
            s.expunge(t)
        return out


def test_documents_versions_and_task_rules(app, engine, today):
    a = login(app, "user_a")
    code = code_of("JO-A1002")
    tasks = _tasks(code)
    assert {"B1", "B2", "B15.1", "B10"} <= set(tasks)            # ABC can CO -> co B10
    assert "B3.1" in tasks                                         # SEA -> keo container
    assert tasks["B1"].status == "COMPLETED" and tasks["B1"].completed_by == "automation"  # lo da co -> tu xong
    assert tasks["B2"].status == "PENDING"                          # chua co booking
    ids = {k: v.id for k, v in tasks.items()}
    assert "err=" in post(a, f"/tasks/{ids['B3']}/status", {"status": "COMPLETED"})[1]    # B2 chua xong
    assert "msg=" in post(a, f"/tasks/{ids['B2']}/status", {"status": "COMPLETED"})[1]
    st, loc = post(a, f"/tasks/{ids['B3']}/status", {"status": "COMPLETED"})
    assert "err=" in loc and "INVOICE" in loc and "PKL" in loc                            # thieu chung tu

    files = {"file": ("invoice.pdf", b"%PDF-1.4 invoice v1", "application/pdf")}
    assert "INVOICE v1" in post(a, f"/shipments/{code}/documents", {"doc_type": "INVOICE"}, files=files)[1]
    assert "giống hệt" in post(a, f"/shipments/{code}/documents", {"doc_type": "INVOICE"}, files=files)[1]
    files2 = {"file": ("invoice-rev.pdf", b"%PDF-1.4 invoice v2", "application/pdf")}
    assert "INVOICE v2" in post(a, f"/shipments/{code}/documents", {"doc_type": "INVOICE"}, files=files2)[1]
    bad = {"file": ("virus.exe", b"MZ", "application/octet-stream")}
    assert "err=" in post(a, f"/shipments/{code}/documents", {"doc_type": "PKL"}, files=bad)[1]
    pkl = {"file": ("pkl.xlsx", b"PK pkl", "application/octet-stream")}
    assert "PKL v1" in post(a, f"/shipments/{code}/documents", {"doc_type": "PKL"}, files=pkl)[1]
    engine.run_scan(today=today)                                   # du chung tu -> B3 tu hoan thanh
    assert _tasks(code)["B3"].status == "COMPLETED" and _tasks(code)["B3"].completed_by == "automation"

    # Nguoi dung mo lai task -> Automation khong tu dong lai
    assert "msg=" in post(a, f"/tasks/{ids['B3']}/status", {"status": "PENDING"})[1]
    engine.run_scan(today=today)
    assert _tasks(code)["B3"].status == "PENDING"

    with session_scope() as s:
        doc = s.execute(select(Document).where(Document.shipment_code == code, Document.doc_type == "INVOICE")) \
            .scalar_one()
        vers = s.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                         .order_by(DocumentVersion.version)).scalars().all()
        assert [v.version for v in vers] == [1, 2] and all(v.stored_path for v in vers)
        v1, doc_id = vers[0].id, doc.id
    assert a.get(f"/documents/versions/{v1}").content == b"%PDF-1.4 invoice v1"     # ban cu van con
    assert login(app, "user_b").get(f"/documents/versions/{v1}").status_code == 404
    assert post(login(app, "user_b"), f"/shipments/{code}/documents", {"doc_type": "INVOICE"},
                files=files)[0] == 404
    assert post(a, f"/documents/{doc_id}/status", {"status": "APPROVED"})[0] == 403
    assert "msg=" in post(login(app, "manager"), f"/documents/{doc_id}/status", {"status": "APPROVED"})[1]

    page = a.get(f"/shipments/{code}").text
    assert "Document Matrix" in page and "APPROVED" in page and "B15.1" in page
    tasks_page = a.get("/tasks?show=all&days=90").text
    assert "JO-A1002" in tasks_page and "JO-S4002" in tasks_page     # SAMSUNG: user_a la du phong
    assert "JO-I3003" not in tasks_page                                # IKEA cua user_b
    # lo da co booking tu Excel -> B2 tu xong
    assert _tasks(code_of("JO-A1007"))["B2"].status == "COMPLETED"


def test_workflow_rules_pure():
    cfg = {"steps": [{"code": "B1", "anchor": "EXFTY", "offset": -20},
                     {"code": "B2", "anchor": "EXFTY", "offset": -14, "depends_on": ["B1"]},
                     {"code": "B9", "anchor": "ETD", "offset": 1, "depends_on": ["B2"]},
                     {"code": "B10", "anchor": "ETD", "offset": 2, "only_if": "co_required"}]}
    steps = {s.code: s for s in wf.load_steps(cfg)}
    anchors = {"EXFTY": dt.date(2026, 10, 1), "ETD": None}
    assert wf.due_date("ETD", 1, anchors) == dt.date(2026, 10, 2)          # chua co ETD -> tam theo EX-FACT
    assert not wf.applicable(steps["B10"], mode="SEA", co_required=False)
    assert wf.display_status("PENDING", deps_done=False, due=None, today=dt.date(2026, 9, 1)) == "BLOCKED"
    assert wf.display_status("PENDING", deps_done=True, due=dt.date(2026, 9, 1), today=dt.date(2026, 9, 2)) == \
        "OVERDUE"
    rules = [{"mode": "SEA", "required": ["BOOKING", "SI"]}, {"buyer": ["KRB"], "required": ["CO"]}]
    assert wf.required_documents(rules, mode="SEA", buyer="KRB", forwarder="") == ["BOOKING", "SI", "CO"]
    assert wf.required_documents(rules, mode="AIR", buyer="ABC", forwarder="") == []
    with pytest.raises(ValueError):
        wf.load_steps({"steps": [{"code": "X", "depends_on": ["NOPE"]}]})
    with pytest.raises(ValueError):
        wf.load_steps({"steps": [{"code": "X", "auto_done": ["magic"]}]})
    auto = wf.load_steps({"steps": [{"code": "B3", "required_docs": ["INVOICE", "PKL"], "auto_done": "documents"},
                                    {"code": "B13", "auto_done": ["departed"]}]})
    assert not wf.auto_done(auto[0], {"docs": {"INVOICE"}})
    assert wf.auto_done(auto[0], {"docs": {"INVOICE", "PKL"}})
    assert wf.auto_done(auto[1], {"ship_status": "FULLY_SHIPPED"}) and not wf.auto_done(auto[1], {})


# ---------------------------------------------------------------------------
# Weekly Planning
# ---------------------------------------------------------------------------


def test_weekly_planning_deadline_and_reminders(app, engine):
    cfg = engine.settings.weekly_planning
    fri = dt.datetime(2026, 9, 18, 11, 30)
    assert wk.planning_week(fri.date()) == (dt.date(2026, 9, 21), dt.date(2026, 9, 27))
    assert wk.deadline_at(fri.date(), cfg) == dt.datetime(2026, 9, 18, 12, 0)
    assert wk.horizon(dt.date(2026, 9, 16), cfg) is None                  # thu 4: chua lay tuan sau
    assert wk.horizon(dt.date(2026, 9, 17), cfg) == dt.date(2026, 9, 27)  # thu 5
    with session_scope() as s:
        st = ws.status(s, engine.settings, fri)
        assert st["label"] == "2026-W39" and st["total"] >= 5 and not st["late"]
        assert {r["jo"] for r in st["rows"]} >= {"JO-A1003", "JO-A1004"}
        out = ws.check_reminders(s, engine.settings, fri)
        assert out["stage"] == "R2" and out["sent"] >= 2                   # 11:00 thu 6 -> CRITICAL
        assert ws.check_reminders(s, engine.settings, fri)["sent"] == 0    # moi moc 1 lan
        crit = s.execute(select(Notification).where(Notification.category == "WEEKLY_PLANNING",
                                                    Notification.recipient == "manager")).scalars().all()
        assert crit and crit[0].severity == "CRITICAL"
        late = ws.check_reminders(s, engine.settings, dt.datetime(2026, 9, 18, 13, 0))
        assert late["stage"] == "LATE"

    assert post(login(app, "user_a"), "/weekly/confirm")[0] == 403
    m = login(app, "manager")
    assert "msg=" in post(m, "/weekly/confirm", {"note": "ok"})[1]
    assert "Đã chốt bởi manager" in m.get("/weekly").text
    with session_scope() as s:  # da chot (theo gio that cua may) -> khong nhac nua
        assert ws.check_reminders(s, engine.settings, dt.datetime.now()).get("confirmed") is True


def test_weekly_file_includes_next_week_from_thursday(engine, today):
    from openpyxl import load_workbook

    engine.run_scan(today=today)  # 18/09/2026 la thu 6
    path = engine.settings.resolve_path(engine.settings.file_by_code("F02").path)
    wb = load_workbook(path)
    ws_ = wb["2026"]
    header = [c.value for c in ws_[1]]
    jos = {dict(zip(header, r, strict=False)).get("JO") for r in ws_.iter_rows(min_row=2, values_only=True)}
    wb.close()
    assert "JO-A1003" in jos        # EX-FACT 27/09 (CN tuan sau, T-9) - van vao Weekly Planning
    assert "JO-A1001" not in jos    # 06/10 - tuan sau nua


# ---------------------------------------------------------------------------
# Luoi Follow Up: BOOKING NO -> booking; phan trang phia may chu
# ---------------------------------------------------------------------------


def test_grid_booking_no_creates_booking_and_paging(app, engine):
    a = login(app, "user_a")
    code = code_of("JO-A1002")
    rows = a.get("/api/followup/rows").json()["rows"]
    r = next(x for x in rows if x["code"] == code)
    res = a.post("/api/followup/save", json={"changes": [{"code": code, "v": r["v"],
                                                          "values": {"BookingNo": "grid-777"}}]}).json()
    assert not res["errors"], res
    with session_scope() as s:
        b = s.execute(select(Booking).where(Booking.booking_no == "GRID-777")).scalar_one()
        assert b.source == "USER" and b.created_by == "user_a" and b.status == "BOOKED"
        assert s.execute(select(Shipment.booking_status).where(Shipment.shipment_code == code)).scalar_one() == \
            "BOOKED"

    engine.settings.web.grid_page_size = 10
    try:
        m = login(app, "manager")
        p1 = m.get("/api/followup/rows").json()
        p4 = m.get("/api/followup/rows?page=4").json()
        assert p1["pages"] == 4 and len(p1["rows"]) == 10 and p1["truncated"]
        assert p4["page"] == 4 and len(p4["rows"]) == p1["total"] - 30 and not p4["truncated"]
        assert not {x["code"] for x in p1["rows"]} & {x["code"] for x in p4["rows"]}
    finally:
        engine.settings.web.grid_page_size = 2000
