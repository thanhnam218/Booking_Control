"""Nam bang "xuong song" cua he thong (SRSv4 muc 68).

SRS xac dinh 5 bang phai chot truoc khi code, va canh bao: "Neu chi dua vao file Follow Up
Excel de code thi he thong se nhanh chong tro nen rat kho bao tri khi Buyer hoac Forwarder
thay doi quy trinh."

    1. Buyer x Forwarder Matrix   config/buyer_rules.yaml      > forwarder_rules
    2. Email Pattern Matrix       config/email_profiles.yaml   > forwarders
    3. Deadline Matrix            config/config.yaml           > deadlines
                                  config/workflow.yaml         > document_deadlines
    4. Document Matrix            config/workflow.yaml         > document_rules / document_types
    5. Field Ownership Matrix     config/followup_template.yaml > columns

Module nay KHONG sua cau hinh. No doc 5 bang ra mot cho de nhin, va quan trong hon la
KIEM TRA CHEO giua cac bang - vi cac loi that hay nam o cho noi giua hai bang:
Forwarder co trong rule nhung khong co bo doc email, chung tu bat buoc ma khong co han,
cot khong ai duoc sua...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core import forwarder as fw
from ..core import parser_profile as pp

OK = "OK"
WARN = "WARNING"
ERROR = "ERROR"


@dataclass(slots=True)
class Issue:
    level: str
    table: str
    message: str
    fix: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "table": self.table, "message": self.message, "fix": self.fix}


@dataclass(slots=True)
class Matrices:
    buyer_forwarder: list[dict[str, Any]] = field(default_factory=list)
    email_patterns: list[dict[str, Any]] = field(default_factory=list)
    deadlines: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    field_ownership: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i["level"] == ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i["level"] == WARN)


def build(settings, template) -> Matrices:
    m = Matrices()
    issues: list[Issue] = []

    rules = fw.load_rules((settings.buyer_rules or {}).get("forwarder_rules"))
    profiles = pp.load((settings.email_profiles or {}).get("forwarders"))
    wf_cfg = settings.workflow or {}

    _buyer_forwarder(m, rules, profiles, issues)
    _email_patterns(m, profiles, rules, issues)
    _deadlines(m, settings, wf_cfg, issues)
    _documents(m, wf_cfg, rules, issues)
    _field_ownership(m, template, issues)

    m.issues = [i.as_dict() for i in issues]
    return m


# ---------------------------------------------------------------------------
# 1. Buyer x Forwarder
# ---------------------------------------------------------------------------


def _buyer_forwarder(m: Matrices, rules, profiles, issues: list[Issue]) -> None:
    names = {p.name for p in profiles}
    seen: dict[tuple, list[str]] = {}
    for r in rules:
        key = (tuple(sorted(x.upper() for x in r.buyer)), tuple(sorted(x.upper() for x in r.mode)),
               tuple(sorted(x.upper() for x in r.dest)))
        seen.setdefault(key, []).append(r.forwarder)
        m.buyer_forwarder.append({
            "buyer": r.buyer or ["*"], "mode": r.mode or ["*"], "dest": r.dest or ["*"],
            "port": r.port or [], "forwarder": r.forwarder, "service": r.service,
            "booking_rule": r.booking_rule, "parser": r.parser,
            "effective": _range(r.exfty_from, r.exfty_to), "note": r.note,
            "parser_ok": (not r.parser) or r.parser in names,
        })
        if r.parser and r.parser not in names:
            issues.append(Issue(ERROR, "Buyer × Forwarder",
                                f"Rule của {r.forwarder} trỏ tới parser '{r.parser}' nhưng "
                                f"email_profiles.yaml không có profile tên đó.",
                                "Sửa tên parser, hoặc thêm profile vào email_profiles.yaml > forwarders"))
        elif not r.parser:
            issues.append(Issue(WARN, "Buyer × Forwarder",
                                f"Rule {', '.join(r.buyer) or '*'} → {r.forwarder} chưa gán Parser "
                                f"Profile — email của họ chỉ được đọc bằng alias chung.",
                                "Thêm parser: \"<tên profile>\" vào dòng rule"))
        if not r.service:
            issues.append(Issue(WARN, "Buyer × Forwarder",
                                f"{r.forwarder} ({', '.join(r.buyer) or '*'}) chưa ghi dịch vụ "
                                f"(FCL / LCL / AIR).", "Thêm service: vào dòng rule"))
    for key, fwds in seen.items():
        if len(set(fwds)) > 1:
            buyer = ", ".join(key[0]) or "*"
            issues.append(Issue(WARN, "Buyer × Forwarder",
                                f"{buyer} khớp nhiều Forwarder ({', '.join(sorted(set(fwds)))}) — "
                                f"hệ thống sẽ hỏi lại chứ không tự chọn.",
                                "Đúng thiết kế nếu cố ý; nếu không, thu hẹp mode/dest cho từng rule"))


def _range(a, b) -> str:
    if not a and not b:
        return "luôn luôn"
    return f"{a or '…'} → {b or '…'}"


# ---------------------------------------------------------------------------
# 2. Email Pattern
# ---------------------------------------------------------------------------


def _email_patterns(m: Matrices, profiles, rules, issues: list[Issue]) -> None:
    used = {r.forwarder for r in rules}
    for p in profiles:
        row = p.as_dict()
        row["pattern_count"] = len(p.patterns)
        row["contact_count"] = len(p.contacts)
        row["in_use"] = p.name in used
        m.email_patterns.append(row)
        if not p.domains and not p.senders:
            issues.append(Issue(ERROR, "Email Pattern",
                                f"Profile '{p.name}' không có domain lẫn sender — không bao giờ "
                                f"khớp email nào.", "Thêm domains: hoặc senders:"))
        if not p.patterns and not p.field_aliases:
            issues.append(Issue(WARN, "Email Pattern",
                                f"Profile '{p.name}' chưa có pattern lẫn alias riêng — "
                                f"khai ra cũng như không.",
                                "Thêm patterns: cho booking_no / etd / vessel..."))
        if not row["in_use"]:
            issues.append(Issue(WARN, "Email Pattern",
                                f"Profile '{p.name}' không Forwarder nào trong Buyer × Forwarder "
                                f"dùng tới.", "Kiểm tra lại tên, hoặc xóa nếu không còn dùng"))


# ---------------------------------------------------------------------------
# 3. Deadline
# ---------------------------------------------------------------------------


def _deadlines(m: Matrices, settings, wf_cfg: dict[str, Any], issues: list[Issue]) -> None:
    d = settings.deadlines
    bw, ms = d.booking_window, d.milestones
    m.deadlines = [
        {"moc": "D-20", "y_nghia": "Bắt đầu theo dõi booking",
         "gia_tri": f"{bw.start_days_before} ngày trước EX-FACT", "o_dau": "config.yaml › deadlines.booking_window"},
        {"moc": "D-14", "y_nghia": "Hạn chót booking",
         "gia_tri": f"{bw.end_days_before} ngày trước EX-FACT", "o_dau": "config.yaml › deadlines.booking_window"},
        {"moc": "D-7", "y_nghia": "Weekly Planning + Custom Code",
         "gia_tri": f"{ms.weekly_planning_days_before} / {ms.custom_code_days_before} ngày trước",
         "o_dau": "config.yaml › deadlines.milestones"},
        {"moc": "D-3", "y_nghia": "Kiểm tra dữ liệu cuối (Inv, Qty, CTNS, GW, CBM, Price)",
         "gia_tri": f"{ms.final_info_days_before} ngày trước", "o_dau": "config.yaml › deadlines.milestones"},
        {"moc": "D-1", "y_nghia": "Lịch xuất TDV / TGV",
         "gia_tri": f"{ms.shipping_schedule_days_before} ngày trước", "o_dau": "config.yaml › deadlines.milestones"},
        {"moc": "Hold", "y_nghia": "Nhắc lại khi đang hold",
         "gia_tri": f"mỗi {d.hold_reminder_days} ngày", "o_dau": "config.yaml › deadlines.hold_reminder_days"},
    ]
    for code, spec in (wf_cfg.get("document_deadlines") or {}).items():
        anchor, offset = spec.get("anchor", "EXFTY"), int(spec.get("offset", 0))
        if offset < 0:
            continue                      # cac moc truoc ngay xuat da liet ke o tren
        m.deadlines.append({
            "moc": f"{anchor}{offset:+d}", "y_nghia": f"Chứng từ {code}",
            "gia_tri": f"{offset} ngày sau {anchor}", "o_dau": "workflow.yaml › document_deadlines"})

    if bw.end_days_before >= bw.start_days_before:
        issues.append(Issue(ERROR, "Deadline",
                            f"Cửa sổ booking sai: bắt đầu D-{bw.start_days_before} nhưng hạn chót "
                            f"D-{bw.end_days_before}.", "start_days_before phải lớn hơn end_days_before"))
    if not 1 <= d.hold_reminder_days <= 7:
        issues.append(Issue(WARN, "Deadline",
                            f"Nhắc hold mỗi {d.hold_reminder_days} ngày — SRS quy định 1–7 ngày.",
                            "Sửa deadlines.hold_reminder_days"))


# ---------------------------------------------------------------------------
# 4. Document
# ---------------------------------------------------------------------------


def _documents(m: Matrices, wf_cfg: dict[str, Any], rules, issues: list[Issue]) -> None:
    types = {d["code"]: d for d in wf_cfg.get("document_types") or []}
    deadlines = wf_cfg.get("document_deadlines") or {}
    known_fw = {r.forwarder for r in rules}

    for rule in wf_cfg.get("document_rules") or []:
        required = rule.get("required") or []
        m.documents.append({
            "mode": rule.get("mode") or "*",
            "buyer": rule.get("buyer") or ["*"],
            "forwarder": rule.get("forwarder") or ["*"],
            "required": [{
                "code": c,
                "label": (types.get(c) or {}).get("label", c),
                "owner": (types.get(c) or {}).get("owner", ""),
                "deadline": _due_text(deadlines.get(c)),
                "known": c in types,
            } for c in required],
        })
        for c in required:
            if c not in types:
                issues.append(Issue(ERROR, "Document",
                                    f"Rule yêu cầu chứng từ '{c}' nhưng không có trong "
                                    f"document_types.", "Khai thêm vào workflow.yaml › document_types"))
        for f in rule.get("forwarder") or []:
            if f not in known_fw:
                issues.append(Issue(WARN, "Document",
                                    f"Rule chứng từ nhắc Forwarder '{f}' không có trong "
                                    f"Buyer × Forwarder.", "Kiểm tra lại tên Forwarder"))
    for code, spec in types.items():
        if not spec.get("owner"):
            issues.append(Issue(WARN, "Document", f"Chứng từ {code} chưa ghi bộ phận phụ trách.",
                                "Thêm owner: vào document_types"))
        if code not in deadlines:
            issues.append(Issue(WARN, "Document", f"Chứng từ {code} chưa có hạn nộp.",
                                "Thêm vào document_deadlines"))


def _due_text(spec: dict[str, Any] | None) -> str:
    if not spec:
        return ""
    anchor, offset = spec.get("anchor", "EXFTY"), int(spec.get("offset", 0))
    label = "EX-FACT" if anchor == "EXFTY" else anchor
    return f"{label}{offset:+d}"


# ---------------------------------------------------------------------------
# 5. Field Ownership
# ---------------------------------------------------------------------------


def _field_ownership(m: Matrices, template, issues: list[Issue]) -> None:
    for c in template.columns:
        override = ("Công thức — gõ đè thì giá trị tay thắng" if c.formula
                    else "Sửa được, tạo mục Quản lý xem lại" if c.user == "review"
                    else "Người dùng sửa được" if c.user == "edit"
                    else "Chỉ Quản lý sửa được" if c.manager == "edit"
                    else "Không ai sửa trên lưới")
        m.field_ownership.append({
            "letter": c.letter, "field": c.field, "label": c.label, "type": c.type,
            "source": c.source, "user": c.user, "manager": c.manager,
            "auto": c.auto, "formula": bool(c.formula), "required": c.required,
            "override": override,
        })
        if c.user == "view" and c.manager == "view" and c.formula is None:
            issues.append(Issue(WARN, "Field Ownership",
                                f"Cột {c.letter} ({c.label}) không ai sửa được và cũng không phải "
                                f"cột công thức.", "Xem lại quyền user/manager trong followup_template.yaml"))
        if c.auto and c.user == "edit" and c.source == "USER":
            issues.append(Issue(WARN, "Field Ownership",
                                f"Cột {c.letter} ({c.label}) vừa để Automation ghi vừa ghi nguồn là "
                                f"USER — dễ hiểu nhầm ai thắng.",
                                "Đặt source đúng nguồn chính (OUTLOOK / RULE) hoặc tắt auto"))
