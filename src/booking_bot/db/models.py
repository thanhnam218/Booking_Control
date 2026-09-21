"""SQLAlchemy models - lop Database trung gian giua Excel va cac Engine.

Nguyen tac kien truc (SRS muc 4 & 74): Bot KHONG doc Excel roi ghi thang Excel.
Moi du lieu di qua Database -> co lich su, co audit, co conflict detection.
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
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------------------
# 1. Nguoi dung / File / Sheet / Mapping
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin | user
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SourceFile(Base):
    """DMS muc 2 - File Registry."""

    __tablename__ = "source_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)  # F01-A, F02...
    role: Mapped[str] = mapped_column(String(40))  # follow_up, weekly_planning...
    display_name: Mapped[str] = mapped_column(String(200), default="")
    path: Mapped[str] = mapped_column(Text)
    storage: Mapped[str] = mapped_column(String(20), default="local")
    owner: Mapped[str] = mapped_column(String(80), default="common")
    can_read: Mapped[bool] = mapped_column(Boolean, default=True)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    last_version_token: Mapped[str | None] = mapped_column(String(120))
    last_scanned_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    last_written_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    sheets: Mapped[list[SheetMap]] = relationship(back_populates="file", cascade="all,delete-orphan")


class SheetMap(Base):
    """DMS muc 3 - Sheet Mapping. Bot nhan dien, User xac nhan lan dau."""

    __tablename__ = "sheet_maps"
    __table_args__ = (UniqueConstraint("file_id", "sheet_name", name="uq_sheet"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"))
    sheet_name: Mapped[str] = mapped_column(String(160))
    buyer_raw: Mapped[str] = mapped_column(String(160), default="")
    is_follow_up: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    header_row: Mapped[int] = mapped_column(Integer, default=1)
    detection_note: Mapped[str] = mapped_column(Text, default="")
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    header_signature: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    file: Mapped[SourceFile] = relationship(back_populates="sheets")


class ColumnMapping(Base):
    """DMS muc 4 - Column Mapping. Sheet = '*' nghia la ap dung cho moi sheet cua file."""

    __tablename__ = "column_mappings"
    __table_args__ = (
        UniqueConstraint("file_code", "sheet_name", "source_header", name="uq_colmap"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    file_code: Mapped[str] = mapped_column(String(40))
    sheet_name: Mapped[str] = mapped_column(String(160), default="*")
    source_header: Mapped[str] = mapped_column(String(200))
    source_column: Mapped[str] = mapped_column(String(10), default="")  # A, B, C...
    canonical_field: Mapped[str] = mapped_column(String(60))
    data_type: Mapped[str] = mapped_column(String(20), default="string")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    transformation: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# 2. Buyer
# ---------------------------------------------------------------------------


class Buyer(Base):
    __tablename__ = "buyers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    canonical_name: Mapped[str] = mapped_column(String(160))
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# 3. Shipment / Invoice / Customs / Container
# ---------------------------------------------------------------------------


class Shipment(Base):
    """SRS muc 10 - ShipmentID la khoa, KHONG dung JO."""

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("shipment_key", name="uq_shipment_key"),
        Index("ix_shipment_exfty", "exfty_date"),
        Index("ix_shipment_status", "effective_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_code: Mapped[str] = mapped_column(String(40), unique=True)  # SHP-20260912-0001
    shipment_key: Mapped[str] = mapped_column(String(64))  # hash(user|no|jo|exfty)
    group_key: Mapped[str] = mapped_column(String(64), default="")  # gom lo de tinh container

    # Nguon goc (metadata - SRS muc 9)
    source_file_code: Mapped[str] = mapped_column(String(40))
    source_sheet: Mapped[str] = mapped_column(String(160))
    source_row: Mapped[int] = mapped_column(Integer)
    owner_user: Mapped[str] = mapped_column(String(80), default="")

    # Canonical fields
    shipment_no: Mapped[str] = mapped_column(String(80), default="")
    jo: Mapped[str] = mapped_column(String(120), default="")
    exfty_date: Mapped[dt.date | None] = mapped_column(Date)
    buyer_raw: Mapped[str] = mapped_column(String(160), default="")
    buyer_code: Mapped[str] = mapped_column(String(80), default="")
    ship_mode: Mapped[str] = mapped_column(String(20), default="")
    booking_mode: Mapped[str] = mapped_column(String(20), default="")
    destination: Mapped[str] = mapped_column(String(120), default="")
    customs_code: Mapped[str] = mapped_column(String(120), default="")
    order_qty: Mapped[float | None] = mapped_column(Float)
    final_export_qty: Mapped[float | None] = mapped_column(Float)
    final_carton_qty: Mapped[float | None] = mapped_column(Float)
    gross_weight: Mapped[float | None] = mapped_column(Float)
    final_cbm: Mapped[float | None] = mapped_column(Float)
    cdf: Mapped[str] = mapped_column(String(200), default="")
    bl: Mapped[str] = mapped_column(String(200), default="")
    price: Mapped[float | None] = mapped_column(Float)
    discount_price: Mapped[float | None] = mapped_column(Float)
    pic: Mapped[str] = mapped_column(String(120), default="")
    site: Mapped[str] = mapped_column(String(20), default="")  # TDV | TGV
    remark: Mapped[str] = mapped_column(Text, default="")

    # Cot bo sung tu file Follow Up that (47 cot)
    go: Mapped[str] = mapped_column(String(80), default="")
    po: Mapped[str] = mapped_column(String(80), default="")
    style: Mapped[str] = mapped_column(String(80), default="")
    booking_no: Mapped[str] = mapped_column(String(80), default="")
    booking_date: Mapped[dt.date | None] = mapped_column(Date)
    etd: Mapped[dt.date | None] = mapped_column(Date)
    eta: Mapped[dt.date | None] = mapped_column(Date)
    vessel: Mapped[str] = mapped_column(String(120), default="")
    port: Mapped[str] = mapped_column(String(80), default="")
    closing_time: Mapped[str] = mapped_column(String(80), default="")
    container_type_user: Mapped[str] = mapped_column(String(80), default="")  # CONT TYPE User go
    container_no: Mapped[str] = mapped_column(String(80), default="")
    seal_no: Mapped[str] = mapped_column(String(80), default="")
    co_no: Mapped[str] = mapped_column(String(120), default="")
    term: Mapped[str] = mapped_column(String(20), default="")
    currency: Mapped[str] = mapped_column(String(10), default="")
    cdf_amount: Mapped[float | None] = mapped_column(Float)
    tsn_amount: Mapped[float | None] = mapped_column(Float)
    derived_notes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Mau dong: Bot to lan cuoi / User to tay (USER > BOT)
    last_bot_color: Mapped[str] = mapped_column(String(12), default="")
    user_color: Mapped[str] = mapped_column(String(12), default="")

    # Co trang thai
    booking_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    hold_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    hold_reason: Mapped[str] = mapped_column(String(80), default="")
    hold_reason_detail: Mapped[str] = mapped_column(Text, default="")
    hold_since: Mapped[dt.date | None] = mapped_column(Date)
    cancel_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    shipped_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    shipped_qty: Mapped[float | None] = mapped_column(Float)
    co_required: Mapped[bool] = mapped_column(Boolean, default=False)
    co_form: Mapped[str] = mapped_column(String(40), default="")

    # Ket qua Status Engine
    bot_status: Mapped[str] = mapped_column(String(20), default="WHITE")
    effective_status: Mapped[str] = mapped_column(String(20), default="WHITE")
    status_reason: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # explain trace
    data_confidence: Mapped[str] = mapped_column(String(20), default="VALID")  # SRS muc 56

    # Conflict detection (SRS muc 38)
    data_hash: Mapped[str] = mapped_column(String(64), default="")
    source_version: Mapped[str] = mapped_column(String(120), default="")

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    exfty_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    previous_exfty: Mapped[dt.date | None] = mapped_column(Date)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    invoices: Mapped[list[Invoice]] = relationship(back_populates="shipment", cascade="all,delete-orphan")
    declarations: Mapped[list[CustomsDeclaration]] = relationship(
        back_populates="shipment", cascade="all,delete-orphan"
    )

    @property
    def raw_dict(self) -> dict[str, Any]:
        return {
            "ShipmentNo": self.shipment_no,
            "JO": self.jo,
            "ExFtyDate": self.exfty_date,
            "Buyer": self.buyer_code or self.buyer_raw,
            "FinalCBM": self.final_cbm,
            "ShipMode": self.ship_mode,
            "CDF": self.cdf,
            "BL": self.bl,
            "PIC": self.pic,
        }


class Invoice(Base):
    """SRS muc 11-12: 1 shipment co the co NHIEU invoice."""

    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    invoice_no: Mapped[str] = mapped_column(String(120))
    declaration_group: Mapped[str] = mapped_column(String(80), default="")
    shipment: Mapped[Shipment] = relationship(back_populates="invoices")


class CustomsDeclaration(Base):
    __tablename__ = "customs_declarations"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    declaration_no: Mapped[str] = mapped_column(String(120), default="")
    customs_code: Mapped[str] = mapped_column(String(120), default="")
    group_key: Mapped[str] = mapped_column(String(80), default="")
    invoice_nos: Mapped[list[Any]] = mapped_column(JSON, default=list)
    shipment: Mapped[Shipment] = relationship(back_populates="declarations")


class ContainerAllocation(Base):
    """SRS muc 18."""

    __tablename__ = "container_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_key: Mapped[str] = mapped_column(String(64), index=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"))
    total_cbm: Mapped[float] = mapped_column(Float, default=0.0)
    qty_40hc: Mapped[int] = mapped_column(Integer, default=0)
    qty_40dc: Mapped[int] = mapped_column(Integer, default=0)
    qty_20st: Mapped[int] = mapped_column(Integer, default=0)
    estimated_capacity: Mapped[float] = mapped_column(Float, default=0.0)
    excess_capacity: Mapped[float] = mapped_column(Float, default=0.0)
    booking_mode: Mapped[str] = mapped_column(String(20), default="")  # FCL | LCL | AIR
    container_type: Mapped[str] = mapped_column(String(40), default="")  # text hien thi
    allocation_status: Mapped[str] = mapped_column(String(20), default="AUTO")
    allocation_source: Mapped[str] = mapped_column(String(20), default="BOT")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# 4. Override / Audit / Exception / Notification
# ---------------------------------------------------------------------------


class UserOverride(Base):
    """SRS muc 34-35: USER > BOT."""

    __tablename__ = "user_overrides"
    __table_args__ = (Index("ix_override_shipment", "shipment_code", "field"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_code: Mapped[str] = mapped_column(String(40))
    field: Mapped[str] = mapped_column(String(60))
    user_value: Mapped[str] = mapped_column(Text)
    user_name: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """SRS muc 36."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_ts", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    actor_type: Mapped[str] = mapped_column(String(10), default="BOT")  # BOT | USER | SYSTEM
    actor_name: Mapped[str] = mapped_column(String(80), default="bot")
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    field: Mapped[str] = mapped_column(String(60), default="")
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class ExceptionRecord(Base):
    """SRS muc 55 - Exception Center."""

    __tablename__ = "exceptions"
    __table_args__ = (Index("ix_exc_status", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))  # EX001..EX009
    severity: Mapped[str] = mapped_column(String(20), default="WARNING")
    source_file: Mapped[str] = mapped_column(String(200), default="")
    source_sheet: Mapped[str] = mapped_column(String(160), default="")
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_column: Mapped[str] = mapped_column(String(80), default="")
    value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")
    shipment_code: Mapped[str] = mapped_column(String(40), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN|RESOLVED|IGNORED
    dedup_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    resolved_by: Mapped[str] = mapped_column(String(80), default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_created", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(50))  # BOOKING_DUE, HOLD, T3_MISSING...
    severity: Mapped[str] = mapped_column(String(20), default="WARNING")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    shipment_code: Mapped[str] = mapped_column(String(40), default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedup_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    next_repeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------------------
# 5. Output / Pending queue / Version / Settings
# ---------------------------------------------------------------------------


class PlanningRecord(Base):
    """Theo dau dong Bot da ghi ra file dich -> khong bao gio INSERT trung (SRS muc 23)."""

    __tablename__ = "planning_records"
    __table_args__ = (
        UniqueConstraint("target_role", "external_key", name="uq_planning_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    target_role: Mapped[str] = mapped_column(String(40))
    target_file_code: Mapped[str] = mapped_column(String(40), default="")
    target_sheet: Mapped[str] = mapped_column(String(160), default="")
    target_row: Mapped[int | None] = mapped_column(Integer)
    external_key: Mapped[str] = mapped_column(String(160))
    shipment_code: Mapped[str] = mapped_column(String(40), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(20), default="WRITTEN")  # WRITTEN|PENDING|FAILED
    written_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PendingWrite(Base):
    """SRS muc 41 - Pending Queue khi file bi lock."""

    __tablename__ = "pending_writes"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_file_code: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class FileVersion(Base):
    """SRS muc 38 + 54 - version token & backup path."""

    __tablename__ = "file_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_code: Mapped[str] = mapped_column(String(40), index=True)
    version_token: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    backup_path: Mapped[str] = mapped_column(Text, default="")
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str] = mapped_column(String(200), default="")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ScanRun(Base):
    """Lich su moi lan scan - de tray hien Last Scan / Next Scan."""

    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    mode: Mapped[str] = mapped_column(String(20), default="SCAN")  # SCAN | DRY_RUN
    files_scanned: Mapped[int] = mapped_column(Integer, default=0)
    sheets_scanned: Mapped[int] = mapped_column(Integer, default=0)
    shipments_found: Mapped[int] = mapped_column(Integer, default=0)
    shipments_new: Mapped[int] = mapped_column(Integer, default=0)
    shipments_changed: Mapped[int] = mapped_column(Integer, default=0)
    notifications: Mapped[int] = mapped_column(Integer, default=0)
    exceptions: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
