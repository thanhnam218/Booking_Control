"""Doc va validate cau hinh YAML -> object Python co goi y kieu.

Uu tien tim config theo thu tu:
  1. duong dan truyen vao CLI (--config)
  2. bien moi truong BOOKINGCTL_CONFIG
  3. ./config/config.yaml
  4. ./config/config.example.yaml   (che do demo)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------


class AppCfg(BaseModel):
    name: str = "Booking Control"
    timezone: str = "Asia/Ho_Chi_Minh"
    language: str = "vi"
    data_dir: str = "./data"
    log_level: str = "INFO"


class DatabaseCfg(BaseModel):
    url: str = "sqlite:///./data/booking_control.db"
    echo: bool = False


class UserCfg(BaseModel):
    """Chi dung de TAO SAN tai khoan lan dau. Sau do quan ly user tren web (Admin > Nguoi dung)."""

    name: str
    display_name: str = ""
    email: str = ""
    role: str = "user"  # admin | manager | user | viewer
    team: str = ""


class FileCfg(BaseModel):
    id: str
    role: str
    display_name: str = ""
    path: str
    owner: str = "common"
    storage: str = "local"
    read: bool = True
    write: bool = False
    sheet: str | None = None
    header_row: int | None = None
    sheets_by_site: dict[str, str] = Field(default_factory=dict)
    # Cot KHONG to mau khi doi mau dong (vd cot NO la cong thuc, User to vang de danh dau)
    color_skip_columns: list[str] = Field(default_factory=list)

    @property
    def options(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "header_row": self.header_row,
            "sheets_by_site": self.sheets_by_site,
        }


class SiteRule(BaseModel):
    when: dict[str, Any]
    site: str


class SiteRulesCfg(BaseModel):
    default_site: str = "TGV"
    rules: list[SiteRule] = Field(default_factory=list)


class NotificationWindow(BaseModel):
    start: str = "08:00"
    end: str = "16:00"


class SchedulerCfg(BaseModel):
    scan_interval_hours: int = 3
    scan_anchor_hour: int = 9
    run_on_start: bool = True
    notification_window: NotificationWindow = Field(default_factory=NotificationWindow)
    active_weekdays: list[str] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat"]
    )
    queue_outside_window: bool = True

    @field_validator("scan_interval_hours")
    @classmethod
    def _range(cls, v: int) -> int:
        if not 1 <= v <= 4:
            raise ValueError("scan_interval_hours phai trong khoang 1-4 gio (SRS muc 42)")
        return v


class BookingWindow(BaseModel):
    start_days_before: int = 20
    end_days_before: int = 14


class MilestonesCfg(BaseModel):
    weekly_planning_days_before: int = 7
    custom_code_days_before: int = 7
    final_info_days_before: int = 3
    shipping_schedule_days_before: int = 1


class DeadlinesCfg(BaseModel):
    booking_window: BookingWindow = Field(default_factory=BookingWindow)
    hold_reminder_days: int = 3
    milestones: MilestonesCfg = Field(default_factory=MilestonesCfg)
    dedup_hours: int = 20
    overdue_grace_days: int = 3

    @field_validator("hold_reminder_days")
    @classmethod
    def _range(cls, v: int) -> int:
        if not 1 <= v <= 7:
            raise ValueError("hold_reminder_days phai trong khoang 1-7 ngay (SRS muc 45)")
        return v


class ContainerCfg(BaseModel):
    capacity: dict[str, float] = Field(
        default_factory=lambda: {"40HC": 63.0, "40DC": 53.0, "20ST": 23.0}
    )
    priority: list[str] = Field(default_factory=lambda: ["40HC", "40DC", "20ST"])
    strategy: str = "priority"
    lcl_threshold_cbm: float = 15.0
    safety_factor: float = 1.0
    air_type: str = "AC"
    lcl_type: str = "CFS"


class CustomStatus(BaseModel):
    name: str
    color: str
    label: str = ""
    notify: bool = False


class CompletedRule(BaseModel):
    """Khi nao coi la DA XUAT XONG (tra loi cau hoi goc trong yeu cau)."""

    enabled: bool = True
    require_container_seal: bool = True   # hang FCL phai co CONT + SEAL
    require_etd_passed: bool = True       # ngay tau chay (ETD) da qua
    group_partial: bool = True            # nhom cung NO: dong cont do dang -> PARTIAL


class ShipmentCfg(BaseModel):
    # NO trong file that la cong thuc tu INVOICE -> khong dua vao khoa (xem core/shipment.py)
    key_includes_no: bool = False
    merge_window_days: int = 7


class StatusCfg(BaseModel):
    colors: dict[str, str] = Field(
        default_factory=lambda: {
            "WHITE": "FFFFFFFF",
            "YELLOW": "FFFFFF00",
            "GREEN": "FF92D050",
            "RED": "FFFF0000",
            "COMPLETED": "FF00B0F0",
            "PARTIAL": "FFFFC000",
            "CANCELLED": "FFBFBFBF",
        }
    )
    custom: list[CustomStatus] = Field(default_factory=list)
    write_color_to_excel: bool = True
    # Doc mau User to tay trong Excel -> coi nhu User override (USER > AUTO)
    read_user_colors: bool = True
    completed_rule: CompletedRule = Field(default_factory=CompletedRule)

    def status_of_color(self, rgb: str) -> str:
        """Mau -> trang thai (dao nguoc bang mau). '' neu khong phai mau trang thai."""
        rgb = (rgb or "").upper()
        if len(rgb) == 6:
            rgb = "FF" + rgb
        for name, color in self.colors.items():
            if color.upper() == rgb and name != "WHITE":
                return name
        for c in self.custom:
            if c.color.upper() == rgb:
                return c.name
        return ""


class ExcelWriteCfg(BaseModel):
    dry_run_default: bool = False
    backup_before_write: bool = True
    backup_keep_versions: int = 20
    conflict_policy: str = "stop"
    lock_retry_seconds: list[int] = Field(default_factory=lambda: [30, 60, 120])
    pending_queue_max_age_hours: int = 72
    verify_after_write: bool = True


class EmailCfg(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 25
    use_tls: bool = False
    username: str = ""
    password_env: str = "BOOKINGCTL_SMTP_PASSWORD"  # mat khau doc tu bien moi truong
    sender: str = "booking-control@localhost"
    always_cc: list[str] = Field(default_factory=list)
    base_url: str = ""  # vd http://192.168.1.20:8765 - de chen link vao email


class NotificationCfg(BaseModel):
    channels: dict[str, bool] = Field(
        default_factory=lambda: {"web": True, "email": False, "console": True}
    )
    severity_to_channel: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "CRITICAL": ["web", "email"],
            "WARNING": ["web"],
            "INFO": ["web"],
        }
    )
    group_warnings: bool = True
    email: EmailCfg = Field(default_factory=EmailCfg)


class WebCfg(BaseModel):
    enabled: bool = True
    # 127.0.0.1 = chi may nay. "0.0.0.0" = cho ca mang LAN truy cap (may chu noi bo)
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser_on_start: bool = False
    grid_page_size: int = 2000  # so dong toi da tai len luoi Follow Up 1 lan


class AuthCfg(BaseModel):
    session_hours: int = 12                # het han phien dang nhap
    remember_days: int = 14                # "Ghi nho dang nhap"
    min_password_length: int = 8
    max_failed_logins: int = 5             # sai qua so lan -> khoa tam thoi
    lockout_minutes: int = 10
    allow_remote_setup: bool = False       # trang tao Admin dau tien chi mo tu chinh may chu
    secure_cookie: bool = False            # bat khi chay sau HTTPS reverse proxy


class AssignmentRule(BaseModel):
    """Tu dong giao lo hang moi. Cac dieu kien de trong = khong xet."""

    buyer: list[str] = Field(default_factory=list)
    forwarder: list[str] = Field(default_factory=list)
    mode: list[str] = Field(default_factory=list)
    site: list[str] = Field(default_factory=list)
    user: str
    backup: str = ""


class AssignmentCfg(BaseModel):
    auto_assign_new: bool = True
    # Lo hang doc tu file Follow Up cua ai -> mac dinh giao cho nguoi do (neu la user hop le)
    use_file_owner: bool = True
    rules: list[AssignmentRule] = Field(default_factory=list)


class JobsCfg(BaseModel):
    max_retries: int = 2               # thu lai toi da khi job loi (Outlook mat ket noi...)
    retry_backoff_seconds: int = 30
    keep_days: int = 60                # giu lich su job bao nhieu ngay
    outlook_interval_minutes: int = 30


class OutlookCfg(BaseModel):
    """Doc Outlook desktop tren may chu (can Outlook dang mo + pywin32)."""

    enabled: bool = False
    mailbox: str = ""                  # de trong = hop thu mac dinh; hoac ten hop thu dung chung
    folders: list[str] = Field(default_factory=lambda: ["Inbox", "Sent Items"])
    lookback_days: int = 14            # lan dau doc lui bao nhieu ngay; sau do chi doc email moi
    max_items: int = 500               # moi lan quet toi da bao nhieu email / thu muc
    attachment_exts: list[str] = Field(default_factory=lambda: [".pdf", ".xlsx", ".xls", ".docx"])
    max_attachment_mb: int = 15
    body_chars: int = 6000             # chi luu doan dau than email (khong luu ca hop thu)
    auto_apply: bool = True            # email du diem (>= thresholds.auto) va khong mau thuan -> tu ap dung


class DocumentsCfg(BaseModel):
    root: str = ""                     # de trong = <data_dir>/documents; co the la o NAS \\server\share
    max_upload_mb: int = 25
    allowed_exts: list[str] = Field(default_factory=lambda: [
        ".pdf", ".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".zip", ".msg", ".eml"])


class WeeklyPlanningCfg(BaseModel):
    """Weekly Planning phai xong truoc 12:00 thu 6 (SRSv5 muc 36)."""

    enabled: bool = True
    include_next_week_from: str = "thu"   # tu thu 5: ghi luon cac lo cua TUAN SAU vao Weekly Planning
    deadline: str = "fri 12:00"
    reminders: list[dict[str, str]] = Field(default_factory=lambda: [
        {"at": "thu 08:00", "level": "INFO"},
        {"at": "fri 08:00", "level": "WARNING"},
        {"at": "fri 11:00", "level": "CRITICAL"},
    ])


class Settings(BaseModel):
    app: AppCfg = Field(default_factory=AppCfg)
    database: DatabaseCfg = Field(default_factory=DatabaseCfg)
    users: list[UserCfg] = Field(default_factory=list)
    files: list[FileCfg] = Field(default_factory=list)
    site_rules: SiteRulesCfg = Field(default_factory=SiteRulesCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)
    deadlines: DeadlinesCfg = Field(default_factory=DeadlinesCfg)
    container: ContainerCfg = Field(default_factory=ContainerCfg)
    shipment: ShipmentCfg = Field(default_factory=ShipmentCfg)
    status: StatusCfg = Field(default_factory=StatusCfg)
    excel_write: ExcelWriteCfg = Field(default_factory=ExcelWriteCfg)
    notification: NotificationCfg = Field(default_factory=NotificationCfg)
    web: WebCfg = Field(default_factory=WebCfg)
    auth: AuthCfg = Field(default_factory=AuthCfg)
    assignment: AssignmentCfg = Field(default_factory=AssignmentCfg)
    jobs: JobsCfg = Field(default_factory=JobsCfg)
    outlook: OutlookCfg = Field(default_factory=OutlookCfg)
    documents: DocumentsCfg = Field(default_factory=DocumentsCfg)
    weekly_planning: WeeklyPlanningCfg = Field(default_factory=WeeklyPlanningCfg)

    # runtime
    config_path: str = ""
    field_mapping: dict[str, Any] = Field(default_factory=dict)
    buyer_rules: dict[str, Any] = Field(default_factory=dict)
    followup_template: dict[str, Any] = Field(default_factory=dict)
    email_profiles: dict[str, Any] = Field(default_factory=dict)
    workflow: dict[str, Any] = Field(default_factory=dict)

    # -- tien ich -----------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        p = Path(self.app.data_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def documents_dir(self) -> Path:
        p = Path(self.documents.root) if self.documents.root else self.data_dir / "documents"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def mail_dir(self) -> Path:
        p = self.data_dir / "mail"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def backup_dir(self) -> Path:
        p = self.data_dir / "backups"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir(self) -> Path:
        p = self.data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolve_path(self, raw: str) -> Path:
        p = Path(os.path.expandvars(raw)).expanduser()
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def database_url(self) -> str:
        url = self.database.url
        if url.startswith("sqlite:///./"):
            return "sqlite:///" + str(self.resolve_path(url.replace("sqlite:///", "", 1)))
        return url

    def files_by_role(self, role: str) -> list[FileCfg]:
        return [f for f in self.files if f.role == role]

    def file_by_code(self, code: str) -> FileCfg | None:
        return next((f for f in self.files if f.id == code), None)

    def user_by_name(self, name: str) -> UserCfg | None:
        return next((u for u in self.users if u.name == name), None)

    def status_color(self, status: str) -> str:
        if status in self.status.colors:
            return self.status.colors[status]
        for c in self.status.custom:
            if c.name == status:
                return c.color
        return self.status.colors.get("WHITE", "FFFFFFFF")


# ---------------------------------------------------------------------------


def _find_config(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("BOOKINGCTL_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates += [CONFIG_DIR / "config.yaml", CONFIG_DIR / "config.example.yaml"]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "Khong tim thay config. Hay copy config/config.example.yaml -> config/config.yaml"
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_path: str | None = None) -> Settings:
    cfg_file = _find_config(config_path)
    raw = _load_yaml(cfg_file)

    fm_path = CONFIG_DIR / "field_mapping.yaml"
    br_path = CONFIG_DIR / "buyer_rules.yaml"
    if not br_path.exists():
        br_path = CONFIG_DIR / "buyer_rules.example.yaml"

    raw["field_mapping"] = _load_yaml(fm_path)
    raw["buyer_rules"] = _load_yaml(br_path)
    raw["followup_template"] = _load_yaml(CONFIG_DIR / "followup_template.yaml")
    raw["email_profiles"] = _load_yaml(CONFIG_DIR / "email_profiles.yaml")
    raw["workflow"] = _load_yaml(CONFIG_DIR / "workflow.yaml")
    raw["config_path"] = str(cfg_file)
    return Settings(**raw)


_CACHED: dict[str, Settings] = {}


def get_settings(config_path: str | None = None) -> Settings:
    """Tra ve Settings dang dung (singleton)."""
    if "value" not in _CACHED:
        _CACHED["value"] = load_settings(config_path)
    return _CACHED["value"]


def reload_settings(config_path: str | None = None) -> Settings:
    """Doc lai YAML tu dia - dung sau khi User sua config trong Dashboard."""
    _CACHED["value"] = load_settings(config_path)
    return _CACHED["value"]


def set_settings(settings: Settings) -> None:
    """Chu yeu dung trong test."""
    _CACHED["value"] = settings
