"""Bang v3: Booking tach rieng (N:N voi lo hang), xuat tung phan, email Outlook,
chung tu co version, task workflow B1-B15.1, dong thoi gian su kien (SRSv5).

Nguyen tac (SRSv5 BR-008..BR-012, BR-018):
    - Khong xoa booking khi huy; rebook tao booking MOI tro ve booking cu.
    - 1 lo -> nhieu booking (split), nhieu lo -> 1 booking (gop): bang booking_shipments.
    - Xuat mot phan theo so luong thuc te: bang shipment_executions.
    - Moi thay doi quan trong -> shipment_events (timeline) + audit_logs.
    - File chung tu nam ngoai database; DB chi luu metadata + duong dan + checksum.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow

# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

BOOKING_STATUSES = (
    "DRAFT", "REQUESTED", "PENDING", "BOOKED", "CONFIRMED", "AMENDED", "ON_HOLD", "DELAYED",
    "CANCEL_REQUESTED", "CANCELLED", "REBOOK_REQUESTED", "REBOOKED", "PARTIALLY_SHIPPED",
    "DEPARTED", "COMPLETED",
)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_booking_no", "booking_no"),
        Index("ix_booking_conv", "conversation_id"),
        Index("ix_booking_etd", "etd"),
        Index("ix_booking_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_no: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    forwarder: Mapped[str] = mapped_column(String(120), default="")
    mode: Mapped[str] = mapped_column(String(20), default="")
    container_plan: Mapped[str] = mapped_column(String(120), default="")  # vd 2x40HC
    vessel: Mapped[str] = mapped_column(String(160), default="")
    voyage: Mapped[str] = mapped_column(String(80), default="")
    etd: Mapped[dt.date | None] = mapped_column(Date)
    eta: Mapped[dt.date | None] = mapped_column(Date)
    booking_date: Mapped[dt.date | None] = mapped_column(Date)
    cy_cutoff: Mapped[str] = mapped_column(String(60), default="")
    si_vgm_cutoff: Mapped[str] = mapped_column(String(60), default="")
    closing_time: Mapped[str] = mapped_column(String(60), default="")
    port: Mapped[str] = mapped_column(String(120), default="")
    hold_reason: Mapped[str] = mapped_column(Text, default="")
    hold_review_date: Mapped[dt.date | None] = mapped_column(Date)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    # Rebook: booking moi tro ve booking cu; booking cu tro sang booking thay the
    original_booking_id: Mapped[int | None] = mapped_column(Integer)
    replacement_booking_id: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[str] = mapped_column(String(200), default="")  # email yeu cau booking
    source: Mapped[str] = mapped_column(String(20), default="USER")  # USER | EMAIL | EXCEL
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)

    @property
    def active(self) -> bool:
        return self.status not in ("CANCELLED", "REBOOKED", "DRAFT")

    @property
    def label(self) -> str:
        return self.booking_no or f"BK#{self.id}"


class BookingShipment(Base):
    """Phan bo lo hang vao booking (SRSv5 muc 7-9: split / combined booking)."""

    __tablename__ = "booking_shipments"
    __table_args__ = (
        UniqueConstraint("booking_id", "shipment_code", name="uq_booking_shipment"),
        Index("ix_bs_shipment", "shipment_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(Integer)
    shipment_code: Mapped[str] = mapped_column(String(40))
    allocated_qty: Mapped[float | None] = mapped_column(Float)  # None = ca lo
    allocated_cbm: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ShipmentExecution(Base):
    """Mot lan xuat hang thuc te (SRSv5 muc 10, BR-012). Tong so luong -> PARTIAL / FULLY_SHIPPED."""

    __tablename__ = "shipment_executions"
    __table_args__ = (Index("ix_exec_shipment", "shipment_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_code: Mapped[str] = mapped_column(String(40))
    booking_id: Mapped[int | None] = mapped_column(Integer)
    ship_date: Mapped[dt.date] = mapped_column(Date)
    shipped_qty: Mapped[float] = mapped_column(Float, default=0)
    shipped_ctns: Mapped[float | None] = mapped_column(Float)
    shipped_cbm: Mapped[float | None] = mapped_column(Float)
    container_no: Mapped[str] = mapped_column(String(80), default="")
    seal_no: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ShipmentEvent(Base):
    """Dong thoi gian nghiep vu (SRSv5 muc 73-74): BOOKING_CONFIRMED, CANCELLED, ETD_CHANGED..."""

    __tablename__ = "shipment_events"
    __table_args__ = (
        Index("ix_event_shipment", "shipment_code"),
        Index("ix_event_booking", "booking_id"),
        Index("ix_event_ts", "ts"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    event_type: Mapped[str] = mapped_column(String(40))
    shipment_code: Mapped[str] = mapped_column(String(40), default="")
    booking_id: Mapped[int | None] = mapped_column(Integer)
    email_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="USER")  # USER | EMAIL | EXCEL | SYSTEM
    actor: Mapped[str] = mapped_column(String(80), default="")
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Email (Outlook desktop)
# ---------------------------------------------------------------------------


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_email_message_id"),  # idempotent (NFR-03)
        Index("ix_email_conv", "conversation_id"),
        Index("ix_email_status", "match_status"),
        Index("ix_email_received", "received_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(300))
    conversation_id: Mapped[str] = mapped_column(String(200), default="")
    in_reply_to: Mapped[str] = mapped_column(String(300), default="")
    direction: Mapped[str] = mapped_column(String(4), default="IN")  # IN | OUT (Sent Items)
    folder: Mapped[str] = mapped_column(String(120), default="")
    sender: Mapped[str] = mapped_column(String(200), default="")
    recipients: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    body_excerpt: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30), default="OTHER")
    forwarder: Mapped[str] = mapped_column(String(120), default="")
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # tu than email
    attachment_extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # tu PDF
    conflicts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # truong email != PDF
    # AUTO_MATCHED | REVIEW | UNMATCHED | CONFLICT | APPLIED | IGNORED
    match_status: Mapped[str] = mapped_column(String(20), default="UNMATCHED")
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    shipment_codes: Mapped[list[Any]] = mapped_column(JSON, default=list)  # lo da ghep
    booking_id: Mapped[int | None] = mapped_column(Integer)
    processed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    applied_by: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class EmailAttachment(Base):
    __tablename__ = "email_attachments"
    __table_args__ = (Index("ix_att_email", "email_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(260))
    stored_path: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parse_status: Mapped[str] = mapped_column(String(20), default="")  # OK | NO_TEXT | FAILED | SKIPPED
    parse_error: Mapped[str] = mapped_column(Text, default="")


class ConversationLink(Base):
    """Conversation da biet thuoc lo nao - reply sau tu dong ghep (SRSv5 muc 20-21)."""

    __tablename__ = "conversation_links"
    __table_args__ = (UniqueConstraint("conversation_id", "shipment_code", name="uq_conv_ship"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(200), index=True)
    shipment_code: Mapped[str] = mapped_column(String(40))
    booking_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="AUTO")  # AUTO | USER
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Chung tu + workflow
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("shipment_code", "doc_type", name="uq_doc_shipment_type"),
        Index("ix_doc_shipment", "shipment_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_code: Mapped[str] = mapped_column(String(40))
    doc_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="RECEIVED")  # RECEIVED | APPROVED | REJECTED
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    updated_by: Mapped[str] = mapped_column(String(80), default="")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (Index("ix_docver_doc", "document_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(260))
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="UPLOAD")  # UPLOAD | EMAIL
    email_id: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(String(80), default="")
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")


TASK_STATUSES = ("PENDING", "IN_PROGRESS", "WAITING", "COMPLETED", "CANCELLED", "SKIPPED")


class WorkflowTask(Base):
    """Buoc B1..B15.1 cua tung lo (SRSv5 muc 30-31)."""

    __tablename__ = "workflow_tasks"
    __table_args__ = (
        UniqueConstraint("shipment_code", "step_code", name="uq_task_step"),
        Index("ix_task_due", "due_date"),
        Index("ix_task_owner", "owner"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_code: Mapped[str] = mapped_column(String(40))
    step_code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    owner: Mapped[str] = mapped_column(String(80), default="")
    depends_on: Mapped[list[Any]] = mapped_column(JSON, default=list)
    required_docs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    completed_by: Mapped[str] = mapped_column(String(80), default="")
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(Text, default="")
    manual_due: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_status: Mapped[bool] = mapped_column(Boolean, default=False)  # nguoi dung da doi tay -> khong tu dong


class ImportProfile(Base):
    """Bo map cot cho file Excel NGOAI (vd "GARMENT SHIPMENT SCHEDULE" 61 cot).

    File ngoai co hang tram cot, phan lon khong lien quan; nguoi dung chi chon vai cot
    can dung roi luu lai thanh "bo map" de lan sau nhap mot phat, khong phai chon lai.
    Cot duoc ghi theo VI TRI (chi so cot) chu khong theo ten tieu de, vi nhieu file co
    tieu de lech so voi du lieu ben duoi.
    """

    __tablename__ = "import_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_import_profile_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    sheet_name: Mapped[str] = mapped_column(String(160), default="")
    header_row: Mapped[int] = mapped_column(Integer, default=1)
    # {"6": "JO", "25": "ExFtyDate", ...}  chi so cot (1-based) -> truong chuan
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Tieu de da thay luc tao bo map, de canh bao khi file doi cau truc
    headers_seen: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class JoGoLink(Base):
    """Quan he JO - GO (SRSv5.1 muc 5): mot GO lien quan nhieu JO.

    SRS: "Khong nen chi luu GO duoi dang text trong Shipment". Bang nay cho phep tra
    cuu hai chieu va ghi ro quan he den TU DAU (file Excel, suy ra tu ma goc, hay
    nguoi dung tu noi) - de nguoi dung biet cho nao chac chan, cho nao chi la de xuat.
    """

    __tablename__ = "jo_go_links"
    __table_args__ = (
        UniqueConstraint("jo", "go", name="uq_jo_go"),
        Index("ix_jogo_jo", "jo"),
        Index("ix_jogo_go", "go"),
        Index("ix_jogo_core", "core"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    jo: Mapped[str] = mapped_column(String(120))
    go: Mapped[str] = mapped_column(String(120))
    core: Mapped[str] = mapped_column(String(120), default="")   # phan ma dung chung
    source: Mapped[str] = mapped_column(String(16), default="IMPORT")  # IMPORT|SYSTEM_RULE|USER
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    confirmed_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ExportShipmentGroup(Base):
    """Export Shipment Group (SRSv5.1 muc 2-4): nhieu lo cung ngay xuat gom lai.

    Dieu kien gom: BUYER + EX-FACT DATE + DEST + MODE.

    LUU Y: nhom KHONG phai booking. Mot nhom co the ung voi 1 hoac nhieu booking, va
    he thong khong duoc tu ket luan ca nhom chac chan di chung mot booking
    (SRSv5.1 muc 3). Bang nay chi tong hop va de xuat.
    """

    __tablename__ = "export_shipment_groups"
    __table_args__ = (
        UniqueConstraint("group_key", name="uq_export_group_key"),
        Index("ix_export_group_exfty", "exfty_date"),
        Index("ix_export_group_buyer", "buyer_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_key: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(200), default="")

    # Bon truong dinh nghia nhom
    buyer_code: Mapped[str] = mapped_column(String(80), default="")
    exfty_date: Mapped[dt.date | None] = mapped_column(Date)
    destination: Mapped[str] = mapped_column(String(120), default="")
    ship_mode: Mapped[str] = mapped_column(String(20), default="")

    # Tong hop - nhan vien khong phai cong tay (SRSv5.5 muc 4)
    shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    jo_count: Mapped[int] = mapped_column(Integer, default=0)
    go_count: Mapped[int] = mapped_column(Integer, default=0)
    total_pcs: Mapped[float] = mapped_column(Float, default=0.0)
    total_ctns: Mapped[float] = mapped_column(Float, default=0.0)
    total_gross_weight: Mapped[float] = mapped_column(Float, default=0.0)
    total_cbm: Mapped[float] = mapped_column(Float, default=0.0)
    missing_cbm: Mapped[int] = mapped_column(Integer, default=0)

    # Container du kien cho ca nhom (tinh tu tong CBM)
    container_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    booking_numbers: Mapped[dict[str, Any]] = mapped_column(JSON, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # danh sach JO / GO

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
