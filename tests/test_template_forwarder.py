"""Khung mau 47 cot (cong thuc giong file that) + Buyer-Forwarder Rule."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml
from make_sample_data import REAL_HEADERS  # type: ignore

from booking_control.core import forwarder as fw
from booking_control.followup import FollowUpTemplate, TemplateError, excel_round

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tmpl(field_mapping_cfg) -> FollowUpTemplate:
    data = yaml.safe_load((ROOT / "config" / "followup_template.yaml").read_text(encoding="utf-8"))
    return FollowUpTemplate(data, field_mapping_cfg["derived_fields"])


def test_template_has_47_columns_in_real_order(tmpl):
    assert len(tmpl.columns) == 47
    assert [c.header for c in tmpl.columns] == REAL_HEADERS
    assert tmpl.columns[0].letter == "A" and tmpl.columns[46].letter == "AU"


def test_excel_formulas_match_real_file(tmpl):
    letters = {c.field: c.letter for c in tmpl.columns}
    assert tmpl.formulas["ShipmentNo"].excel(letters, 2) == '=IF(LEFT(M2,4)="TEGV",RIGHT(M2,5)-2108,"")'
    assert tmpl.formulas["DiscountPrice"].excel(letters, 7) == "=ROUND(W7*0.95,2)"
    assert tmpl.formulas["CdfAmount"].excel(letters, 7) == "=X7*N7"
    assert tmpl.formulas["TsnAmount"].excel(letters, 7) == "=W7*N7"


def test_python_formulas_follow_dependency_order(tmpl):
    out = tmpl.compute_formulas({"Invoice": "TEGV03001", "Price": 11.2, "FinalExportQty": 4200})
    assert tmpl.formula_order.index("DiscountPrice") < tmpl.formula_order.index("CdfAmount")
    assert out == {"ShipmentNo": 893, "DiscountPrice": 10.64, "CdfAmount": 44688, "TsnAmount": 47040}
    # Thieu dau vao -> de trong (khong bia so)
    assert tmpl.compute_formulas({"Invoice": "ABC123"})["ShipmentNo"] is None


def test_excel_round_is_half_up_not_bankers():
    assert excel_round(2.675, 2) == 2.68  # Excel: ROUND(2.675,2) = 2.68
    assert excel_round(0.125, 2) == 0.13
    assert excel_round(-0.125, 2) == -0.13
    assert round(0.125, 2) == 0.12  # Python lam tron kieu ngan hang - vi vay moi can excel_round


def test_formula_translated_to_safe_js(tmpl):
    js = tmpl.formulas["DiscountPrice"].js()
    assert js == {"kind": "expr", "code": 'R(M(F("Price"),0.95),2.0)', "refs": ["Price"]}
    assert tmpl.formulas["ShipmentNo"].js()["kind"] == "invoice_suffix"


def test_unsafe_formula_is_rejected(field_mapping_cfg):
    data = {"columns": [{"header": "A", "field": "Price", "type": "number"},
                        {"header": "B", "field": "X", "type": "number"}],
            "formulas": {"X": {"expr": "__import__('os').system('x')"}}}
    with pytest.raises(TemplateError):
        FollowUpTemplate(data, field_mapping_cfg["derived_fields"])


def test_parse_values_from_grid(tmpl):
    assert tmpl.parse("ExFtyDate", "25/09/2026") == (dt.date(2026, 9, 25), "")
    assert tmpl.parse("ExFtyDate", "2026-09-25 00:00:00") == (dt.date(2026, 9, 25), "")
    assert tmpl.parse("FinalCBM", "1,234.5") == (1234.5, "")
    assert tmpl.parse("ShipmentNo", "12.5")[1]
    assert tmpl.parse("FinalCBM", "abc")[1]
    assert tmpl.parse("Remark", "  HOLD  ") == ("HOLD", "")


def test_live_formulas_keep_typed_values(tmpl):
    # NO go tay 20005 (file khong dung cong thuc) -> khong duoc tu tinh lai de mat so
    live = tmpl.live_formulas({"ShipmentNo": 20005, "Invoice": "INV-1", "Price": 10, "DiscountPrice": 9.5,
                               "FinalExportQty": 100, "CdfAmount": 950, "TsnAmount": 1000})
    assert "ShipmentNo" not in live
    assert {"DiscountPrice", "CdfAmount", "TsnAmount"} <= live
    assert tmpl.live_formulas({"ShipmentNo": 999}, always=True) == set(tmpl.formula_order)


def test_field_ownership(tmpl):
    no, buyer, cbm, fob95 = (tmpl.by_field[f] for f in ("ShipmentNo", "Buyer", "FinalCBM", "DiscountPrice"))
    assert not no.can_edit("user") and no.can_edit("manager")
    assert buyer.can_edit("user") and buyer.needs_review("user") and not buyer.needs_review("manager")
    assert cbm.can_edit("user") and not cbm.can_edit("viewer")
    assert not fob95.can_edit("admin")


# ---------------------------------------------------------------------------
# Forwarder (SRS v2 test case 62)
# ---------------------------------------------------------------------------

RULES = fw.load_rules([
    {"buyer": ["BUYER_A"], "mode": ["SEA"], "dest": ["USA"], "forwarder": "FWD1"},
    {"buyer": ["BUYER_B"], "forwarder": "FWD1"},
    {"buyer": ["BUYER_B"], "forwarder": "FWD2"},
    {"buyer": ["BUYER_B"], "mode": ["AIR"], "forwarder": "FWD3"},
])


def test_forwarder_case_a_auto_assigned():
    m = fw.resolve_forwarder(RULES, buyer="BUYER_A", mode="S", dest="USA")
    assert (m.status, m.forwarder) == (fw.AUTO_ASSIGNED, "FWD1")


def test_forwarder_case_b_need_confirm():
    m = fw.resolve_forwarder(RULES, buyer="BUYER_B", mode="A")
    assert m.status == fw.NEED_CONFIRM and m.forwarder == "" and len(m.candidates) == 3


def test_forwarder_case_c_not_assigned():
    assert fw.resolve_forwarder(RULES, buyer="BUYER_Z").status == fw.NOT_ASSIGNED
    assert fw.resolve_forwarder(RULES, buyer="BUYER_A", mode="AIR", dest="USA").status == fw.NOT_ASSIGNED


def test_forwarder_case_d_conflict():
    assert fw.check_conflict("DHL", "DSV") == fw.CONFLICT
    assert fw.check_conflict("DHL", "dhl") == ""
