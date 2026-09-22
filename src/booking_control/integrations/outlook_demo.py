"""Email mau de thu hang cho duyet ma khong can Outlook (bookingctl outlook --demo).

Lay lo hang that trong database demo roi dung 5 email:
    1. Minh gui yeu cau booking (Sent Items)           -> cho duyet: chon lo, he thong nho Conversation
    2. Forwarder tra loi xac nhan cung Conversation     -> cho duyet (lan sau tu ghep)
    3. Amendment: than email ETD khac PDF dinh kem       -> CONFLICT, nguoi dung chon gia tri
    4. Forwarder bao HOLD                               -> tu ap dung (du diem)
    5. Email khong khop lo nao                          -> chua ghep
Chay lai nhieu lan khong tao trung (message_id co dinh).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Shipment
from ..db.models_v3 import Booking, BookingShipment
from .outlook import MailAttachment, MailMessage


def mini_pdf(lines: list[str]) -> bytes:
    """PDF 1 trang co lop chu (Helvetica) - du de pdfplumber doc, khong can thu vien ngoai."""
    esc = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
    stream = "BT /F1 11 Tf 60 780 Td 16 TL " + " ".join(f"({x}) Tj T*" for x in esc) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> "
        "/Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def demo_messages(s: Session, now: dt.datetime | None = None) -> list[MailMessage]:
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    ships = list(s.execute(select(Shipment).where(Shipment.archived.is_(False), Shipment.jo != "",
                                                  Shipment.effective_status != "CANCELLED")
                           .order_by(Shipment.exfty_date)).scalars())
    free = [sh for sh in ships if sh.booking_status == "NOT_BOOKED"]
    free.sort(key=lambda sh: not (sh.row_data or {}).get("Invoice"))  # uu tien lo co INVOICE (JO + INV = cho duyet)
    booked = [(b, sh) for sh in ships for b in s.execute(
        select(Booking).join(BookingShipment, BookingShipment.booking_id == Booking.id)
        .where(BookingShipment.shipment_code == sh.shipment_code, Booking.booking_no != "",
               Booking.status.in_(("BOOKED", "CONFIRMED")))).scalars()]
    msgs: list[MailMessage] = []
    if free:
        sh = free[0]
        ref = " / ".join(x for x in (sh.jo, str((sh.row_data or {}).get("Invoice") or "")) if x)
        conv = "DEMO-CONV-REQUEST-1"
        msgs.append(MailMessage(
            message_id="<demo-1@booking-control.local>", subject=f"Booking request {ref}",
            body=f"Dear team,\nPls book space for {ref}\nQty: {sh.order_qty or ''} pcs\n"
                 f"Ex-factory: {sh.exfty_date or ''}\nThanks",
            sender="planner@company.com", recipients="cs@fwd-ocean1.example.com", direction="OUT",
            folder="Sent Items", conversation_id=conv, received_at=now - dt.timedelta(hours=5)))
        etd = (sh.exfty_date or now.date()) + dt.timedelta(days=3)
        msgs.append(MailMessage(
            message_id="<demo-2@fwd-ocean1.example.com>", subject=f"RE: Booking request {ref} - Booking Confirmation",
            body=f"Booking No: DMO{sh.id:05d}\nVessel: DEMO STAR / 101E\nETD: {etd:%d %b}\nCY Cutoff: "
                 f"{etd - dt.timedelta(days=2):%d/%m} 12:00\n\n> Pls book space for {ref}",
            sender="cs@fwd-ocean1.example.com", conversation_id=conv, in_reply_to="<demo-1@booking-control.local>",
            received_at=now - dt.timedelta(hours=2)))
    if booked:
        b, sh = booked[0]
        etd = b.etd or sh.etd or ((sh.exfty_date or now.date()) + dt.timedelta(days=4))
        pdf = mini_pdf(["BOOKING AMENDMENT", f"Booking No: {b.booking_no}", f"ETD: {etd + dt.timedelta(days=1):%d %b %Y}",
                        "Vessel: DEMO WAVE / 22W"])
        msgs.append(MailMessage(
            message_id="<demo-3@fwd-ocean1.example.com>", subject=f"Booking amendment {b.booking_no} {sh.jo}",
            body=f"Booking No: {b.booking_no}\nETD: {etd:%d %b}\nPlease see attached revised booking.",
            sender="cs@fwd-ocean1.example.com", conversation_id="DEMO-CONV-AMD-1",
            attachments=[MailAttachment(f"{b.booking_no}_amendment.pdf", pdf)], received_at=now - dt.timedelta(hours=1)))
    if len(booked) > 1:
        b, sh = booked[1]
        msgs.append(MailMessage(
            message_id="<demo-4@fwd-ocean1.example.com>", subject=f"Please hold shipment - {b.booking_no}",
            body=f"Booking No: {b.booking_no}\nJO: {sh.jo}\nBuyer requests to hold this shipment until further notice.",
            sender="cs@fwd-ocean1.example.com", conversation_id="DEMO-CONV-HOLD-1",
            received_at=now - dt.timedelta(minutes=40)))
    msgs.append(MailMessage(
        message_id="<demo-5@fwd-air1.example.com>", subject="Booking confirmation - AWB 999-12345675",
        body="Booking No: AIR77881\nFlight: XX123\nETD: " + f"{now + dt.timedelta(days=6):%d %b}",
        sender="ops@fwd-air1.example.com", conversation_id="DEMO-CONV-X", received_at=now - dt.timedelta(minutes=10)))
    return msgs
