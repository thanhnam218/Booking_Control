"""SRSv4 muc 68: nam bang "xuong song" + kiem tra cheo giua chung.

SRS canh bao: "Neu chi dua vao file Follow Up Excel de code thi he thong se nhanh chong
tro nen rat kho bao tri khi Buyer hoac Forwarder thay doi quy trinh."

Cac test o day bao ve dung dieu do: cau hinh phai day du va khop nhau.
"""

from __future__ import annotations

import pytest

from booking_control.core import parser_profile as pp
from booking_control.core.forwarder import ForwarderRule
from booking_control.services import matrices as mx

# ---------------------------------------------------------------------------
# Bang 1 - Buyer x Forwarder (SRSv4 muc 14)
# ---------------------------------------------------------------------------


def test_forwarder_rule_has_all_srs_columns():
    """SRS: Buyer + Mode + Destination + Effective Date + Forwarder + Service
    + Booking Rule + Email Parser."""
    r = ForwarderRule.from_dict({
        "forwarder": "FWD-KR-1", "buyer": ["KRB"], "mode": ["SEA"], "dest": ["KR"],
        "service": "FCL", "booking_rule": "Gui truoc 12:00 T-14", "parser": "FWD-KR-1",
        "exfty_from": "2026-01-01", "exfty_to": "2026-12-31",
    })
    assert r.forwarder == "FWD-KR-1"
    assert r.service == "FCL"
    assert r.booking_rule == "Gui truoc 12:00 T-14"
    assert r.parser == "FWD-KR-1"
    assert r.exfty_from is not None and r.exfty_to is not None


def test_one_buyer_can_have_many_forwarders(buyer_cfg):
    """SRS: "Khong duoc thiet ke Buyer -> mot Forwarder co dinh"."""
    from booking_control.core import forwarder as fw

    rules = fw.load_rules(buyer_cfg.get("forwarder_rules"))
    by_buyer: dict[str, set[str]] = {}
    for r in rules:
        for b in r.buyer:
            by_buyer.setdefault(b, set()).add(r.forwarder)
    assert any(len(v) > 1 for v in by_buyer.values()), "Cau hinh mau nen co it nhat 1 buyer nhieu FWD"


# ---------------------------------------------------------------------------
# Bang 2 - Email Pattern / Parser Profile (SRSv4 muc 15, BR-020)
# ---------------------------------------------------------------------------


@pytest.fixture
def profiles():
    return pp.load([{
        "name": "FWD-A",
        "domains": ["fwd-a.example.com"],
        "field_aliases": {"cy_cutoff": ["cy closing"]},
        "patterns": {
            "booking_no": r"BKG\s*[:#]?\s*(?P<v>[A-Z0-9-]{6,})",
            "vessel": r"VSL\s*[:/]\s*(?P<v>[A-Z0-9 .-]+?)\s*(?:V\.|$)",
        },
        "contacts": [{
            "name": "Mary", "senders": ["mary@fwd-a.example.com"],
            "patterns": {"booking_no": r"Our\s+ref\.?\s*(?P<v>[A-Z0-9-]{6,})"},
        }],
    }])


def test_forwarder_pattern_beats_generic_alias(profiles):
    r = pp.resolve(profiles, "ops@fwd-a.example.com")
    assert r.forwarder == "FWD-A"
    assert r.extract("BKG: VN0200777\nVSL/ HAIAN ROSE V.2609N") == {
        "booking_no": "VN0200777", "vessel": "HAIAN ROSE"}


def test_contact_pattern_beats_forwarder_pattern(profiles):
    """SRSv5 muc 25: mot Forwarder nhieu nhan vien, moi nguoi mot format."""
    r = pp.resolve(profiles, "mary@fwd-a.example.com")
    assert r.contact_name == "Mary"
    got = r.extract("Our ref. KR-556677\nVSL/ SKY PEARL V.01")
    assert got["booking_no"] == "KR-556677"      # mau cua Mary thang
    assert got["vessel"] == "SKY PEARL"          # van thua huong mau cua Forwarder


def test_contact_inherits_forwarder_aliases(profiles):
    assert pp.resolve(profiles, "mary@fwd-a.example.com").aliases()["cy_cutoff"] == ["cy closing"]


def test_unknown_sender_gets_no_profile(profiles):
    r = pp.resolve(profiles, "someone@other.com")
    assert r.forwarder == "" and r.extract("BKG: X123456") == {}


def test_subject_gate(profiles):
    gated = pp.load([{"name": "G", "domains": ["g.com"], "subject_must": ["booking"],
                      "patterns": {"jo": r"JO\s*(?P<v>\w+)"}}])
    assert pp.resolve(gated, "a@g.com", "Booking confirmation").forwarder == "G"
    assert pp.resolve(gated, "a@g.com", "Invoice copy").forwarder == ""


def test_bad_pattern_is_rejected_at_load_not_at_runtime():
    with pytest.raises(pp.ProfileError, match="khong ho tro|Khong ho tro"):
        pp.load([{"name": "X", "patterns": {"khong_co_truong_nay": "(a)"}}])
    with pytest.raises(pp.ProfileError, match="nhom bat gia tri"):
        pp.load([{"name": "X", "patterns": {"jo": "khong-co-nhom"}}])
    with pytest.raises(pp.ProfileError, match="sai cu phap"):
        pp.load([{"name": "X", "patterns": {"jo": "(chua dong ngoac"}}])


def test_attachment_pattern_does_not_need_capture_group():
    prof = pp.load([{"name": "X", "domains": ["x.com"], "attachment_pattern": r"(?i)booking.*\.pdf$"}])[0]
    assert prof.wants_attachment("Booking_Confirm.pdf") is True
    assert prof.wants_attachment("invoice.xlsx") is False


# ---------------------------------------------------------------------------
# Kiem tra cheo giua nam bang
# ---------------------------------------------------------------------------


@pytest.fixture
def built(workspace):
    from booking_control.followup import FollowUpTemplate

    tmpl = FollowUpTemplate(workspace.followup_template or {"columns": []},
                            workspace.field_mapping.get("derived_fields"))
    return mx.build(workspace, tmpl)


def test_shipped_config_has_no_errors(built):
    """Cau hinh mau di kem phan mem phai sach - day la luoi an toan cho nguoi cai dat."""
    errors = [i for i in built.issues if i["level"] == mx.ERROR]
    assert not errors, "Cau hinh mau co loi: " + "; ".join(i["message"] for i in errors)


def test_all_five_tables_are_populated(built):
    assert built.buyer_forwarder, "thieu bang Buyer x Forwarder"
    assert built.email_patterns, "thieu bang Email Pattern"
    assert built.deadlines, "thieu bang Deadline"
    assert built.documents, "thieu bang Chung tu"
    assert len(built.field_ownership) == 47, "Field Ownership phai du 47 cot"


def test_every_document_has_owner_and_deadline(built):
    """SRSv4 bang 4: Mode · Buyer · Forwarder · Required Document · Deadline · OWNER."""
    for rule in built.documents:
        for d in rule["required"]:
            assert d["known"], f"{d['code']} khong co trong document_types"
            assert d["owner"], f"{d['code']} chua ghi bo phan phu trach"
            assert d["deadline"], f"{d['code']} chua co han nop"


def test_every_field_states_who_can_edit(built):
    """SRSv4 bang 5: moi cot phai tra loi duoc 'ai sua duoc, Automation co ghi de khong'."""
    for c in built.field_ownership:
        assert c["source"], f"cot {c['letter']} chua ghi nguon"
        assert c["user"] in ("edit", "review", "view")
        assert c["manager"] in ("edit", "view")
        assert c["override"], f"cot {c['letter']} chua co quy tac ghi de"


def test_parser_reference_must_exist(workspace):
    """Rule tro toi mot parser khong ton tai -> bao LOI, khong de im."""
    from booking_control.followup import FollowUpTemplate

    broken = dict(workspace.buyer_rules or {})
    broken["forwarder_rules"] = [{"forwarder": "FWD-X", "buyer": ["ABC"], "parser": "KHONG-CO"}]
    workspace.buyer_rules = broken
    tmpl = FollowUpTemplate(workspace.followup_template or {"columns": []},
                            workspace.field_mapping.get("derived_fields"))
    m = mx.build(workspace, tmpl)
    assert any(i["level"] == mx.ERROR and "KHONG-CO" in i["message"] for i in m.issues)


def test_broken_booking_window_is_reported(workspace):
    from booking_control.followup import FollowUpTemplate

    workspace.deadlines.booking_window.start_days_before = 10
    workspace.deadlines.booking_window.end_days_before = 14   # nguoc doi
    tmpl = FollowUpTemplate(workspace.followup_template or {"columns": []},
                            workspace.field_mapping.get("derived_fields"))
    m = mx.build(workspace, tmpl)
    assert any(i["level"] == mx.ERROR and "Cửa sổ booking" in i["message"] for i in m.issues)


# ---------------------------------------------------------------------------
# Kiem tra cheo: nhung cho HAI bang khong khop nhau
#
# Day moi la loai loi nguy hiem - no khong bao gi ca, chi am tham lam automation
# bo sot, va chi lo ra khi da mat du lieu that.
# ---------------------------------------------------------------------------


def _of(built, table: str) -> list[dict]:
    return [i for i in built.issues if i["table"].startswith(table)]


def test_forwarder_recognised_by_domain_not_just_exact_sender(profiles):
    """Forwarder doi dia chi nhan vien van phai nhan ra duoc qua domain."""
    r = pp.resolve(profiles, "booking.desk@fwd-a.example.com")
    assert r.forwarder == "FWD-A"
    assert r.contact_name == ""


def test_forwarder_pattern_reads_its_own_format(profiles):
    r = pp.resolve(profiles, "booking@fwd-a.example.com")
    assert r.extract("Dear team\nBKG: VN0200777\nVSL/ HAIAN ROSE V.2609N") == {
        "booking_no": "VN0200777", "vessel": "HAIAN ROSE"}


def test_unknown_sender_extracts_nothing(profiles):
    """Khong nhan ra Forwarder thi khong doan bua theo mau cua Forwarder khac."""
    assert pp.resolve(profiles, "ai@khong-quen.com").extract("BKG: VN0200777") == {}


def test_profile_without_domain_or_sender_is_an_error(workspace):
    """Profile khong co cach nhan dien = khong bao gio khop email nao."""
    from booking_control.followup import FollowUpTemplate

    cfg = workspace.model_copy(deep=True)
    cfg.email_profiles["forwarders"] = [{"name": "FWD-MO-COI"}]
    m = mx.build(cfg, FollowUpTemplate(cfg.followup_template,
                                       cfg.field_mapping.get("derived_fields")))
    assert any(i["level"] == mx.ERROR for i in _of(m, "Email"))


def test_two_rules_matching_one_buyer_are_flagged(built):
    """BR-007: nhieu ung vien ngang nhau -> he thong phai HOI, khong tu chon.

    Cau hinh mau co IKEA khop hai Forwarder - co y de thu duong nay.
    """
    assert any("IKEA" in i["message"] for i in _of(built, "Buyer"))


def test_required_document_without_owner_is_flagged(workspace):
    """Chung tu bat buoc ma khong ghi ai lam -> den han khong biet doi ai."""
    from booking_control.followup import FollowUpTemplate

    cfg = workspace.model_copy(deep=True)
    cfg.workflow["document_types"] = [
        {"code": d["code"], "label": d.get("label", d["code"])}       # bo owner di
        for d in cfg.workflow.get("document_types") or []
    ]
    m = mx.build(cfg, FollowUpTemplate(cfg.followup_template,
                                       cfg.field_mapping.get("derived_fields")))
    assert _of(m, "Document"), "phai bao khi chung tu bat buoc chua co owner"
