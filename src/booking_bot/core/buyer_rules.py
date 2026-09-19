"""Buyer Configuration (SRS muc 14/65/67, DMS muc 13-14).

Chuan hoa ten buyer tho ("ABC CO.,LTD") -> buyer code chuan ("ABC")
va tra ve bo rule ap dung: CO, container, grouping, site...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .normalize import norm_key


@dataclass(slots=True)
class BuyerRule:
    code: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    co_required: bool = False
    co_form: str = ""
    container_rule: str = "standard"
    shipment_group: str = "by_no_exfty"
    invoice_rule: str = "multi"
    declaration_rule: str = "user_review"
    destination_default: str = ""
    site: str = ""
    lcl_threshold_cbm: float | None = None
    known: bool = True

    @property
    def forced_type(self) -> str:
        if self.container_rule.startswith("forced:"):
            return self.container_rule.split(":", 1)[1].strip()
        return ""

    @property
    def needs_user_review(self) -> bool:
        return self.container_rule == "user_review"


class BuyerRegistry:
    def __init__(self, buyer_cfg: dict[str, Any] | None = None):
        cfg = buyer_cfg or {}
        self.default_cfg: dict[str, Any] = cfg.get("default") or {}
        self.hold_reasons: list[dict[str, Any]] = cfg.get("hold_reasons") or []
        self._rules: dict[str, BuyerRule] = {}
        self._alias_index: dict[str, str] = {}

        for entry in cfg.get("buyers") or []:
            rule = self._build(entry)
            self._rules[rule.code] = rule
            for alias in [rule.code, rule.canonical_name, *rule.aliases]:
                k = norm_key(alias)
                if k:
                    self._alias_index.setdefault(k, rule.code)

    def _build(self, entry: dict[str, Any]) -> BuyerRule:
        merged = {**self.default_cfg, **(entry or {})}
        return BuyerRule(
            code=str(merged.get("code") or merged.get("canonical_name") or "UNKNOWN").strip(),
            canonical_name=str(merged.get("canonical_name") or merged.get("code") or "").strip(),
            aliases=list(merged.get("aliases") or []),
            co_required=bool(merged.get("co_required", False)),
            co_form=str(merged.get("co_form") or ""),
            container_rule=str(merged.get("container_rule") or "standard"),
            shipment_group=str(merged.get("shipment_group") or "by_no_exfty"),
            invoice_rule=str(merged.get("invoice_rule") or "multi"),
            declaration_rule=str(merged.get("declaration_rule") or "user_review"),
            destination_default=str(merged.get("destination_default") or ""),
            site=str(merged.get("site") or ""),
            lcl_threshold_cbm=merged.get("lcl_threshold_cbm"),
        )

    # -----------------------------------------------------------------

    def all_rules(self) -> list[BuyerRule]:
        return list(self._rules.values())

    def resolve(self, buyer_raw: str, fuzzy_threshold: float = 0.86) -> BuyerRule:
        """Tra ve rule cua buyer. Khong tim thay -> rule mac dinh voi known=False
        (Bot se dua vao Exception Center EX002/EX008, khong tu doan)."""
        key = norm_key(buyer_raw)
        if not key:
            return self._unknown("")

        if key in self._alias_index:
            return self._rules[self._alias_index[key]]

        best_code, best_score = None, 0.0
        for alias_key, code in self._alias_index.items():
            score = SequenceMatcher(None, key, alias_key).ratio()
            if alias_key and (alias_key in key or key in alias_key) and min(len(alias_key), len(key)) >= 3:
                score = max(score, 0.9)
            if score > best_score:
                best_code, best_score = code, score

        if best_code and best_score >= fuzzy_threshold:
            return self._rules[best_code]
        return self._unknown(buyer_raw)

    def _unknown(self, buyer_raw: str) -> BuyerRule:
        rule = self._build({"code": (buyer_raw or "UNKNOWN").strip().upper(), "canonical_name": buyer_raw})
        rule.known = False
        return rule

    def hold_reason_label(self, code: str) -> str:
        for hr in self.hold_reasons:
            if str(hr.get("code", "")).upper() == str(code).upper():
                return str(hr.get("label") or code)
        return code

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "code": r.code,
                "canonical_name": r.canonical_name,
                "aliases": r.aliases,
                "co_required": r.co_required,
                "co_form": r.co_form,
                "container_rule": r.container_rule,
                "shipment_group": r.shipment_group,
                "invoice_rule": r.invoice_rule,
                "declaration_rule": r.declaration_rule,
                "destination_default": r.destination_default,
                "site": r.site,
                "lcl_threshold_cbm": r.lcl_threshold_cbm,
            }
            for r in self._rules.values()
        ]
