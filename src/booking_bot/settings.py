"""Doc va validate cau hinh YAML -> object Python co goi y kieu.

Uu tien tim config theo thu tu:
  1. duong dan truyen vao CLI (--config)
  2. bien moi truong BOOKINGBOT_CONFIG
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
    name: str = "Booking Deadline Bot"
    timezone: str = "Asia/Ho_Chi_Minh"
    language: str = "vi"
    data_dir: str = "./data"
    log_level: str = "INFO"


class DatabaseCfg(BaseModel):
    url: str = "sqlite:///./data/bookingbot.db"
    echo: bool = False


class UserCfg(BaseModel):
    name: str
    display_name: str = ""
    email: str = ""
    role: str = "user"


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


class ExcelWriteCfg(BaseModel):
    dry_run_default: bool = False
    backup_before_write: bool = True
    backup_keep_versions: int = 20
    conflict_policy: str = "stop"
    lock_retry_seconds: list[int] = Field(default_factory=lambda: [30, 60, 120])
    pending_queue_max_age_hours: int = 72
    verify_after_write: bool = True


class NotificationCfg(BaseModel):
    channels: dict[str, bool] = Field(
        default_factory=lambda: {
            "toast": True,
            "popup": True,
            "tray_balloon": True,
            "console": True,
        }
    )
    severity_to_channel: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "CRITICAL": ["popup", "toast"],
            "WARNING": ["toast"],
            "INFO": ["tray_balloon"],
        }
    )
    group_warnings: bool = True
    max_popups_per_scan: int = 5


class WebCfg(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser_on_start: bool = False
    basic_auth_user: str = ""
    basic_auth_password: str = ""


class OutlookCfg(BaseModel):
    enabled: bool = False
    folders: list[str] = Field(default_factory=lambda: ["Inbox"])
    lookback_days: int = 30
    booking_confirm_keywords: list[str] = Field(default_factory=list)
    cancel_keywords: list[str] = Field(default_factory=list)
    auto_apply: bool = False


class Settings(BaseModel):
    app: AppCfg = Field(default_factory=AppCfg)
    database: DatabaseCfg = Field(default_factory=DatabaseCfg)
    users: list[UserCfg] = Field(default_factory=list)
    files: list[FileCfg] = Field(default_factory=list)
    site_rules: SiteRulesCfg = Field(default_factory=SiteRulesCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)
    deadlines: DeadlinesCfg = Field(default_factory=DeadlinesCfg)
    container: ContainerCfg = Field(default_factory=ContainerCfg)
    status: StatusCfg = Field(default_factory=StatusCfg)
    excel_write: ExcelWriteCfg = Field(default_factory=ExcelWriteCfg)
    notification: NotificationCfg = Field(default_factory=NotificationCfg)
    web: WebCfg = Field(default_factory=WebCfg)
    outlook: OutlookCfg = Field(default_factory=OutlookCfg)

    # runtime
    config_path: str = ""
    field_mapping: dict[str, Any] = Field(default_factory=dict)
    buyer_rules: dict[str, Any] = Field(default_factory=dict)

    # -- tien ich -----------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        p = Path(self.app.data_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
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
    env = os.environ.get("BOOKINGBOT_CONFIG")
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
