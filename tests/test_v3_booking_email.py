"""v3: Booking tach rieng (split / gop / huy / rebook / hold / xuat mot phan) + email Outlook.

Email duoc gia lap bang MailMessage (khong can Outlook) -> EmailProcessor.ingest.
Chay tren bo du lieu demo (37 lo), ngay chay co dinh 18/09/2026.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest
from sqlalchemy import func, select

from booking_control.core import booking as bk
from booking_control.core import email_match as em
from booking_control.core import email_parse as ep
from booking_control.db import session_scope
from booking_control.db.models import ExceptionRecord, Notification, Shipment
from booking_control.db.models_v3 import (
    Booking,
    BookingShipment,
    ConversationLink,
    Document,
    Email,
    ShipmentEvent,
)
from booking_control.integrations.outlook import MailAttachment, MailMessage
from booking_control.services import booking_service as bs
from booking_control.services.email_service import EmailError, EmailProcessor

RECV = dt.datetime(2026, 9, 18, 9, 30)


def pdf_bytes(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(60, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


def mail(mid: str, subject: str, body: str, **kw) -> MailMessage:
    kw.setdefault("sender", "cs@fwd-ocean1.example.com")
    kw.setdefault("received_at", RECV)
    return MailMessage(message_id=mid, subject=subject, body=body, **kw)


def ship_by_jo(s, jo: str, **filters) -> Shipment:
    q = select(Shipment).where(Shipment.jo == jo)
    for k, v in filters.items():
        q = q.where(getattr(Shipment, k) == v)
    return s.execute(q).scalars().first()


@pytest.fixture
def scanned(engine, today):
    engine.run_scan(today=today)
    return engine


# ---------------------------------------------------------------------------
# Module thuan
# ---------------------------------------------------------------------------


def test_allocation_and_ship_status_rules():
    assert bk.allocation_status(1000, [600, 400]) == ("FULL", 1000.0, 0.0)
    assert bk.allocation_status(1000, [600, 200]) == ("PARTIAL", 800.0, 200.0)
    assert bk.allocation_status(1000, [None]) == ("FULL", 1000, 0.0)
    assert bk.allocation_status(1000, [])[0] == "NONE"
    assert bk.allocation_status(1000, [700, 400])[0] == "OVER"
    assert bk.ship_status(0, 1000) == "NOT_SHIPPED"
    assert bk.ship_status(500, 1000) == "PARTIALLY_SHIPPED"
    assert bk.ship_status(1000, 1000) == "FULLY_SHIPPED"


def test_transitions_block_invalid_moves():
    bk.check_transition("REQUESTED", "CONFIRMED")
    bk.check_transition("CONFIRMED", "ON_HOLD")
    with pytest.raises(bk.BookingRuleError):
        bk.check_transition("CANCELLED", "CONFIRMED")  # huy roi thi phai rebook
    with pytest.raises(bk.BookingRuleError):
        bk.check_transition("COMPLETED", "ON_HOLD")


def test_summary_prefers_active_booking_and_flags_hold():
    t0 = dt.datetime(2026, 9, 1)
    views = [bk.BookingView(1, "CANCELLED", "OLD1", created_at=t0),
             bk.BookingView(2, "ON_HOLD", "NEW2", created_at=t0 + dt.timedelta(days=1))]
    sm = bk.summarize(views, 1000)
    assert sm.booking_status == "ON_HOLD" and sm.hold and sm.booked and sm.current_booking_no == "NEW2"
    assert bk.status_values(sm, 0)["HoldFlag"] is True
    sm2 = bk.summarize([bk.BookingView(1, "CANCELLED", "OLD1", created_at=t0)], 1000)
    assert sm2.cancelled and sm2.booking_status == "CANCELLED" and bk.status_values(sm2, 0)["CancelFlag"]


def test_email_parse_fields_and_category():
    idx = ep.build_alias_index({"booking_no": ["booking no"], "etd": ["etd"], "vessel": ["vessel"],
                                "cy_cutoff": ["cy cutoff"], "containers": ["equipment"]}, {})
    text = "Booking No: OCN778899\nVessel: MSC ALPHA / 041E\nETD: 03 OCT\nCY Cutoff: 01/10 12:00\nEquipment: 2x40HC"
    f = ep.extract_fields(text, idx, dt.date(2026, 9, 18))
    assert f["booking_no"] == "OCN778899" and f["vessel"] == "MSC ALPHA" and f["voyage"] == "041E"
    assert f["etd"] == "2026-10-03"
    cats = {"CANCEL": ["booking cancel"], "BOOKING_CONFIRMATION": ["booking confirmation"],
            "BOOKING_REQUEST": ["booking request"]}
    assert ep.classify("RE: Booking request JO1 - Booking confirmation", "", cats)[0] == "BOOKING_CONFIRMATION"
    assert ep.classify("Fw: update", "we regret booking cancel due to space", cats)[0] == "CANCEL"
    assert ep.find_conflicts({"etd": "2026-10-03"}, {"etd": "2026-10-04"}) == {
        "etd": {"email": "2026-10-03", "pdf": "2026-10-04"}}


def test_match_scoring_rules():
    refs = [em.ShipmentRef("S1", jo="JO-A1", invoices=("INV-9",), booking_nos=("BKG1001",), buyer="ABC"),
            em.ShipmentRef("S2", jo="JO-A2", booking_nos=("BKG1001",)),
            em.ShipmentRef("S3", jo="JO-A3")]

    def run(text, conv=(), reply=(), fields=None):
        cands = em.score_candidates(refs, tokens=em.tokenize(text), text_norm=text.upper(), fields=fields or {},
                                    conversation_codes=set(conv), reply_codes=set(reply))
        return em.decide(cands)

    assert run("hello JO-A3").status == "UNMATCHED"            # chi JO -> chua du
    assert run("JO-A1 INV-9").status == "REVIEW"               # JO + invoice = 80
    d = run("booking BKG1001 ETD", conv=["S1", "S2"])              # booking gop 2 lo, cung conversation
    assert d.status == "AUTO_MATCHED" and d.codes == ["S1", "S2"]
    d = run("BKG1001 for both")                                    # 2 lo sat diem, khong conversation
    assert d.status == "REVIEW"
    d = run("confirmed", conv=["S3"], reply=["S3"])
    assert d.status == "AUTO_MATCHED" and d.codes == ["S3"]


# ---------------------------------------------------------------------------
# Booking service
# ---------------------------------------------------------------------------


def test_excel_booking_numbers_become_bookings(scanned):
    with session_scope() as s:
        sh = ship_by_jo(s, "JO-A1007")
        rows = bs.bookings_for(s, [sh.shipment_code])[sh.shipment_code]
        assert [b.booking_no for b, _ in rows] == ["BKG-77124"] and rows[0][0].source == "EXCEL"
        # KRB: nhieu lo cung 1 so booking -> 1 booking gop nhieu lo
        b = s.execute(select(Booking).where(Booking.booking_no == "VN0200001")).scalar_one()
        assert len(bs.shipment_codes_of(s, b)) == 3
        assert sh.booking_status == "BOOKED"
        free = ship_by_jo(s, "JO-A1002")
        assert free.booking_status == "NOT_BOOKED"


def test_split_booking_allocation_and_partial_shipment(scanned, today):
    eng = scanned
    with session_scope() as s:
        sh = ship_by_jo(s, "JO-A1001")  # 5000 pcs, chua booking
        code = sh.shipment_code
        b1 = bs.create_booking(s, shipments=[(code, 3000)], fields={"booking_no": "SPL-001", "etd": "2026-10-08"},
                               status="CONFIRMED", actor="user_a")
        s.flush()
        bs.allocation_warnings(s, [sh])
        assert s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX018",
                                                       ExceptionRecord.shipment_code == code)).first()
        bs.create_booking(s, shipments=[(code, 2000)], fields={"booking_no": "SPL-002", "etd": "2026-10-10"},
                          status="CONFIRMED", actor="user_a")
        s.flush()
        summary, _ = bs.summaries(s, [sh])[code]
        assert summary.allocation == "FULL" and summary.allocated_qty == 5000
        assert set(summary.booking_nos) == {"SPL-001", "SPL-002"}

        bs.record_execution(s, sh, qty=3000, ship_date=dt.date(2026, 10, 8), actor="user_a", booking_id=b1.id,
                            container="TEST1234567")
        bs.refresh(s, eng, [code], today)
        assert sh.ship_status == "PARTIALLY_SHIPPED" and sh.effective_status == "PARTIAL"
        bs.record_execution(s, sh, qty=2000, ship_date=dt.date(2026, 10, 10), actor="user_a")
        bs.refresh(s, eng, [code], today)
        assert sh.ship_status == "FULLY_SHIPPED"
        kinds = [e.event_type for e in s.execute(select(ShipmentEvent).where(ShipmentEvent.shipment_code == code)
                                                 .order_by(ShipmentEvent.id)).scalars()]
        assert kinds.count("BOOKING_CREATED") == 2 and "PARTIAL_SHIPMENT" in kinds and "SHIPMENT_COMPLETED" in kinds


def test_cancel_then_rebook_keeps_history(scanned, today):
    eng = scanned
    with session_scope() as s:
        sh = ship_by_jo(s, "JO-A1003")
        old = s.execute(select(Booking).where(Booking.booking_no == "BKG-77120")).scalar_one()
        with pytest.raises(bs.BookingError):
            bs.set_status(s, old, "REQUESTED", actor="user_a")  # khong di nguoc
        bs.set_status(s, old, "CANCELLED", actor="user_a", reason="Hang chua san sang")
        bs.refresh(s, eng, [sh.shipment_code], today)
        assert sh.booking_status == "CANCELLED" and sh.effective_status == "CANCELLED"
        assert s.execute(select(Notification).where(Notification.category == "BOOKING_CANCELLED",
                                                    Notification.recipient == "user_a")).first()
        new = bs.rebook(s, old, {"booking_no": "BKG-88001", "etd": "2026-10-04"}, actor="user_a")
        bs.push_to_shipments(s, eng, new, actor="user_a", source="BOOKING", today=today)
        bs.refresh(s, eng, [sh.shipment_code], today)
        assert old.status == "CANCELLED" and old.replacement_booking_id == new.id
        assert new.original_booking_id == old.id and new.status == "CONFIRMED"
        assert sh.booking_status == "CONFIRMED" and sh.booking_no == "BKG-88001"
        assert s.scalar(select(func.count(Booking.id))) >= 17  # khong xoa booking cu


def test_cancelled_booking_is_not_resurrected_by_next_scan(scanned, today):
    eng = scanned
    with session_scope() as s:
        b = s.execute(select(Booking).where(Booking.booking_no == "BKG-77123")).scalar_one()
        bs.set_status(s, b, "CANCELLED", actor="user_a")
    eng.run_scan(today=today)  # Excel van ghi BKG-77123
    with session_scope() as s:
        rows = s.execute(select(Booking).where(Booking.booking_no == "BKG-77123")).scalars().all()
        assert len(rows) == 1 and rows[0].status == "CANCELLED"
        assert ship_by_jo(s, "JO-A1006").booking_status == "CANCELLED"


def test_hold_and_release(scanned, today):
    eng = scanned
    with session_scope() as s:
        sh = ship_by_jo(s, "JO-A1007")
        b = s.execute(select(Booking).where(Booking.booking_no == "BKG-77124")).scalar_one()
        bs.set_status(s, b, "ON_HOLD", actor="manager", reason="Buyer hold thanh toan")
        bs.refresh(s, eng, [sh.shipment_code], today)
        assert sh.hold_status == "HOLD" and sh.effective_status == "RED" and b.hold_review_date
        bs.set_status(s, b, "CONFIRMED", actor="manager")
        bs.refresh(s, eng, [sh.shipment_code], today)
        assert sh.hold_status == "RELEASED" and sh.effective_status != "RED"
        kinds = {e.event_type for e in s.execute(select(ShipmentEvent).where(
            ShipmentEvent.booking_id == b.id)).scalars()}
        assert {"BOOKING_HOLD", "BOOKING_RELEASED"} <= kinds


def test_changed_booking_no_in_excel_raises_ex019(scanned):
    with session_scope() as s:
        sh = ship_by_jo(s, "JO-A1004")
        sh.booking_no = "BKG-NEW-555"
        bs.sync_from_row(s, sh)
        assert s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX019",
                                                       ExceptionRecord.shipment_code == sh.shipment_code)).first()
        # booking cu khong bi huy tu dong
        assert s.execute(select(Booking.status).where(Booking.booking_no == "BKG-77121")).scalar_one() == "BOOKED"


# ---------------------------------------------------------------------------
# Email Outlook
# ---------------------------------------------------------------------------


def test_request_then_confirmation_in_same_conversation(scanned, today):
    eng = scanned
    proc = EmailProcessor(eng)
    req = mail("<req-1@company.com>", "Booking request JO-A1002 / ABC",
               "Dear team,\nPls book 1x40HC for JO-A1002, qty 3200 pcs.\nThanks", sender="user.a@company.com",
               direction="OUT", folder="Sent Items", conversation_id="CONV-A1002")
    with session_scope() as s:
        out = proc.ingest(s, [req], today)
        e = s.execute(select(Email).where(Email.message_id == "<req-1@company.com>")).scalar_one()
        assert out["new"] == 1 and e.category == "BOOKING_REQUEST" and e.match_status in ("UNMATCHED", "REVIEW")
        code = ship_by_jo(s, "JO-A1002").shipment_code
        # Nguoi dung chon lo trong hang cho duyet -> he thong nho Conversation
        res = proc.apply(s, e, [code], actor="user_a")
        b = s.get(Booking, res["booking"])
        assert b.status == "REQUESTED" and b.conversation_id == "CONV-A1002"
        assert s.execute(select(ConversationLink).where(ConversationLink.conversation_id == "CONV-A1002")).first()

    confirm = mail("<cf-1@fwd>", "RE: Booking request JO-A1002 / ABC - Booking Confirmation",
                   "Booking No: OCN12345\nVessel: EVER BRAVE / 012E\nETD: 03 OCT\nETA: 30 OCT\n"
                   "CY Cutoff: 01 OCT 12:00\n\n> Pls book 1x40HC for JO-A1002",
                   conversation_id="CONV-A1002", in_reply_to="<req-1@company.com>")
    with session_scope() as s:
        out = EmailProcessor(eng).ingest(s, [confirm], today)
        assert out["applied"] == 1, out
        e = s.execute(select(Email).where(Email.message_id == "<cf-1@fwd>")).scalar_one()
        assert e.forwarder == "FWD-OCEAN-1" and e.match_score >= 85
        sh = ship_by_jo(s, "JO-A1002")
        b = s.get(Booking, e.booking_id)
        assert b.status == "CONFIRMED" and b.booking_no == "OCN12345" and b.etd == dt.date(2026, 10, 3)
        assert sh.booking_no == "OCN12345" and sh.etd == dt.date(2026, 10, 3)
        assert "EVER BRAVE" in (sh.vessel or "")
        assert sh.booking_status == "CONFIRMED" and sh.effective_status != "WHITE"
        assert (sh.field_src or {}).get("BookingNo", {}).get("src") == "OUTLOOK"
        # Chay lai cung email (Outlook doc lui 10 phut) -> khong xu ly lan 2
        out2 = EmailProcessor(eng).ingest(s, [req, confirm], today)
        assert out2["duplicate"] == 2 and out2["new"] == 0

    # Excel scan sau do khong ghi de gia tri tu email (OUTLOOK > Excel import)
    eng.run_scan(today=today)
    with session_scope() as s:
        assert ship_by_jo(s, "JO-A1002").booking_no == "OCN12345"


def test_email_pdf_conflict_requires_choice(scanned, today):
    eng = scanned
    pdf = pdf_bytes(["BOOKING CONFIRMATION", "Booking No: BKG-77121", "ETD: 27 SEP", "Vessel: SEA STAR / 101W"])
    m = mail("<amd-1@fwd>", "Booking amendment BKG-77121 JO-A1004",
             "Booking No: BKG-77121\nETD: 26 SEP\nPlease see attached.", conversation_id="CONV-AMD",
             attachments=[MailAttachment("BKG-77121.pdf", pdf)])
    with session_scope() as s:
        out = EmailProcessor(eng).ingest(s, [m], today)
        e = s.execute(select(Email).where(Email.message_id == "<amd-1@fwd>")).scalar_one()
        assert out["conflict"] == 1 and e.match_status == "CONFLICT"
        assert e.conflicts["etd"] == {"email": "2026-09-26", "pdf": "2026-09-27"}
        assert e.attachment_extracted.get("vessel") == "SEA STAR"
        proc = EmailProcessor(eng)
        with pytest.raises(EmailError):
            proc.apply(s, e, e.shipment_codes, actor="user_a")
        proc.apply(s, e, e.shipment_codes, actor="user_a", choices={"etd": "pdf"})
        sh = ship_by_jo(s, "JO-A1004")
        assert sh.etd == dt.date(2026, 9, 27)
        b = s.execute(select(Booking).where(Booking.booking_no == "BKG-77121")).scalar_one()
        assert b.status == "AMENDED" and b.voyage == "101W"
        # PDF duoc luu thanh chung tu BOOKING v1 cua lo
        doc = s.execute(select(Document).where(Document.shipment_code == sh.shipment_code,
                                               Document.doc_type == "BOOKING")).scalar_one()
        assert doc.current_version == 1


def test_cancel_email_notifies_owner(scanned, today):
    eng = scanned
    m = mail("<cx-1@fwd>", "Booking cancellation - BKG-77123", "Booking No: BKG-77123\nJO-A1006 cancelled by carrier")
    with session_scope() as s:
        out = EmailProcessor(eng).ingest(s, [m], today)
        assert out["applied"] == 1
        b = s.execute(select(Booking).where(Booking.booking_no == "BKG-77123")).scalar_one()
        assert b.status == "CANCELLED"
        n = s.execute(select(Notification).where(Notification.category == "BOOKING_CANCELLED")).scalars().all()
        assert {x.recipient for x in n} == {"user_a"} and n[0].severity == "CRITICAL"


def test_unmatched_and_user_value_kept(scanned, today):
    eng = scanned
    with session_scope() as s:
        out = EmailProcessor(eng).ingest(s, [mail("<x-1@fwd>", "Booking confirmation", "Booking No: ZZZ999\nETD: 05 OCT")],
                                         today)
        assert out["unmatched"] == 1
        assert s.execute(select(Notification).where(Notification.category == "EMAIL_REVIEW",
                                                    Notification.recipient == "")).first()
        # User da sua tay ETD cua lo -> email khong ghi de, bao EX020
        sh = ship_by_jo(s, "JO-A1005")
        src = dict(sh.field_src or {})
        src["ETD"] = {"src": "USER", "by": "user_a", "at": "2026-09-18T08:00:00"}
        sh.field_src = src
        row = dict(sh.row_data or {})
        row["ETD"] = "2026-09-29"
        sh.row_data = row
    m = mail("<amd-2@fwd>", "ETD change BKG-77122", "Booking No: BKG-77122\nJO-A1005\nETD: 01 OCT")
    with session_scope() as s:
        out = EmailProcessor(eng).ingest(s, [m], today)
        assert out["applied"] == 1
        sh = ship_by_jo(s, "JO-A1005")
        assert (sh.row_data or {}).get("ETD") == "2026-09-29"
        assert s.execute(select(ExceptionRecord).where(ExceptionRecord.code == "EX020",
                                                       ExceptionRecord.shipment_code == sh.shipment_code)).first()
        b = s.execute(select(Booking).where(Booking.booking_no == "BKG-77122")).scalar_one()
        assert b.etd == dt.date(2026, 10, 1)  # booking van nhan ETD moi


def test_combined_booking_confirmation_matches_all_shipments(scanned, today):
    eng = scanned
    m = mail("<kr-1@fwd>", "Booking confirmation VN0200001", "BKG NO: VN0200001\nVessel: KR STAR / 22S\nETD: 27 SEP",
             sender="booking.kr@fwd-kr1.example.com")
    with session_scope() as s:
        out = EmailProcessor(eng).ingest(s, [m], today)
        e = s.execute(select(Email).where(Email.message_id == "<kr-1@fwd>")).scalar_one()
        assert out["applied"] == 1 and len(e.shipment_codes) == 3 and e.forwarder == "FWD-KR-1"
        links = s.execute(select(BookingShipment).where(BookingShipment.booking_id == e.booking_id)).scalars().all()
        assert len(links) == 3
