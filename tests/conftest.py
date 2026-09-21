from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from booking_bot.core.buyer_rules import BuyerRegistry  # noqa: E402
from booking_bot.excel.mapping import FieldDictionary  # noqa: E402
from booking_bot.settings import Settings  # noqa: E402


def _yaml(name: str) -> dict:
    with (ROOT / "config" / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture(scope="session")
def field_mapping_cfg() -> dict:
    return _yaml("field_mapping.yaml")


@pytest.fixture(scope="session")
def buyer_cfg() -> dict:
    return _yaml("buyer_rules.example.yaml")


@pytest.fixture(scope="session")
def dictionary(field_mapping_cfg) -> FieldDictionary:
    return FieldDictionary(field_mapping_cfg)


@pytest.fixture(scope="session")
def buyers(buyer_cfg) -> BuyerRegistry:
    return BuyerRegistry(buyer_cfg)


@pytest.fixture
def today() -> dt.date:
    return dt.date(2026, 9, 18)


@pytest.fixture
def workspace(tmp_path, field_mapping_cfg, buyer_cfg, today) -> Settings:
    """Mot moi truong chay doc lap: sample data + config tro vao tmp_path."""
    from make_sample_data import build_all  # type: ignore

    sample_dir = tmp_path / "sample_data"
    build_all(sample_dir, today=today)

    raw = yaml.safe_load((ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    raw["app"]["data_dir"] = str(tmp_path / "data")
    raw["database"]["url"] = f"sqlite:///{tmp_path / 'data' / 'test.db'}"
    name_map = {
        "F01-A": "FollowUp_UserA.xlsx",
        "F01-B": "FollowUp_UserB.xlsx",
        "F01-C": "FollowUp_UserC_CauTrucThat.xlsx",
        "F02": "WEEKLY_PLANNING_2026.xlsx",
        "F03": "CUSTOM_CODE.xlsx",
        "F04": "MM_DAILY_PLANNING_SEP_2026_GV.xlsx",
        "F05": "LICH_XUAT_HANG_IP_DV.xlsx",
    }
    for f in raw["files"]:
        f["path"] = str(sample_dir / name_map[f["id"]])
    raw["web"]["enabled"] = False
    raw["notification"]["channels"] = {"toast": False, "popup": False,
                                       "tray_balloon": False, "console": False}
    raw["field_mapping"] = field_mapping_cfg
    raw["buyer_rules"] = buyer_cfg
    raw["config_path"] = "test"
    return Settings(**raw)


@pytest.fixture
def engine(workspace):
    """Engine da khoi tao DB rieng cho tung test."""
    from booking_bot.db import init_engine
    from booking_bot.engine import BookingBotEngine
    from booking_bot.notify.engine import NotificationEngine

    init_engine(workspace.database_url())
    eng = BookingBotEngine(workspace, NotificationEngine(workspace, channels={}))
    eng.bootstrap()
    return eng


@pytest.fixture
def sample_dir(workspace) -> Path:
    return Path(workspace.files[0].path).parent


def copy_tree(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst
