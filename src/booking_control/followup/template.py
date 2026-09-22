"""Khung mau Follow Up 47 cot + Field Ownership Matrix.

Mot nguon dinh nghia duy nhat (config/followup_template.yaml) cho:
    - luoi Follow Up tren web (cot, kieu, quyen sua, cong thuc tinh phia trinh duyet)
    - xuat Excel (thu tu cot, do rong, dinh dang ngay, cong thuc Excel that)
    - kiem tra quyen sua o backend

Cong thuc duoc viet bang bieu thuc rat nho: so, + - * /, ( ), ROUND(x, n), [TenTruong].
Bieu thuc duoc kiem tra bang `ast` luc nap cau hinh, roi dich ra:
    - Python (tinh o server)
    - JavaScript (tinh ngay tren luoi khi go)
    - cong thuc Excel (=ROUND(W2*0.95,2)) khi xuat file
nen ba noi luon cho cung mot ket qua.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.normalize import apply_derived_rule, clean_text, parse_date, parse_decimal

ROLES = ("admin", "manager", "user", "viewer", "auditor")
MANAGER_ROLES = ("admin", "manager")
FIELD_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


class TemplateError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Bieu thuc cong thuc
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Formula:
    target: str
    kind: str  # expr | invoice_suffix
    expr: str = ""
    tree: ast.AST | None = None
    refs: list[str] = field(default_factory=list)
    rule: dict[str, Any] = field(default_factory=dict)

    # -- Python ------------------------------------------------------------
    def evaluate(self, values: dict[str, Any]) -> Any:
        if self.kind == "invoice_suffix":
            res = apply_derived_rule(self.rule, values.get(self.rule.get("source", "Invoice")))
            if res.value in (None, ""):
                return None
            try:
                return int(res.value)
            except (TypeError, ValueError):
                return None
        return _eval(self.tree, values)

    # -- Excel ---------------------------------------------------------------
    def excel(self, letter_of: dict[str, str], row: int) -> str | None:
        if self.kind == "invoice_suffix":
            src = letter_of.get(self.rule.get("source", "Invoice"))
            if not src:
                return None
            prefix = str(self.rule.get("prefix", ""))
            n = int(self.rule.get("last_digits", 5))
            off = int(self.rule.get("offset", 0))
            off_txt = f"{off:+d}" if off else ""
            return f'=IF(LEFT({src}{row},{len(prefix)})="{prefix}",RIGHT({src}{row},{n}){off_txt},"")'
        missing = [r for r in self.refs if r not in letter_of]
        if missing:
            return None
        return "=" + FIELD_RE.sub(lambda m: f"{letter_of[m.group(1)]}{row}", self.expr).replace(" ", "")

    # -- JavaScript ------------------------------------------------------------
    def js(self) -> dict[str, Any]:
        if self.kind == "invoice_suffix":
            return {"kind": "invoice_suffix", "source": self.rule.get("source", "Invoice"),
                    "prefix": str(self.rule.get("prefix", "")),
                    "last_digits": int(self.rule.get("last_digits", 5)),
                    "offset": int(self.rule.get("offset", 0))}
        return {"kind": "expr", "code": _to_js(self.tree), "refs": self.refs}


_ALLOWED_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _compile_expr(target: str, expr: str, fields: set[str]) -> Formula:
    refs = FIELD_RE.findall(expr)
    unknown = [r for r in refs if r not in fields]
    if unknown:
        raise TemplateError(f"Cong thuc {target}: truong khong ton tai {unknown}")
    py = FIELD_RE.sub(lambda m: f"f_{m.group(1)}", expr)
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as exc:
        raise TemplateError(f"Cong thuc {target} sai cu phap: {expr}") from exc
    _validate(tree.body, target)
    return Formula(target=target, kind="expr", expr=expr, tree=tree.body, refs=refs)


def _validate(node: ast.AST, target: str) -> None:
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BIN:
        _validate(node.left, target)
        _validate(node.right, target)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        _validate(node.operand, target)
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        pass
    elif isinstance(node, ast.Name) and node.id.startswith("f_"):
        pass
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ROUND"
          and len(node.args) == 2 and not node.keywords):
        for a in node.args:
            _validate(a, target)
    else:
        raise TemplateError(f"Cong thuc {target}: khong ho tro '{ast.dump(node)[:60]}'")


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    res = parse_decimal(v)
    return float(res.value) if res.value is not None else None


def _eval(node: ast.AST | None, values: dict[str, Any]) -> float | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return _num(values.get(node.id[2:]))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, values)
        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, values), _eval(node.right, values)
        if a is None or b is None:
            return None
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        return None if b == 0 else a / b
    if isinstance(node, ast.Call):
        a, n = _eval(node.args[0], values), _eval(node.args[1], values)
        if a is None or n is None:
            return None
        return excel_round(a, int(n))
    return None


def excel_round(x: float, n: int) -> float:
    """ROUND cua Excel: lam tron 0.5 ra xa so 0 (Python round() la banker's rounding)."""
    from decimal import ROUND_HALF_UP, Decimal

    q = Decimal(1).scaleb(-n)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def _to_js(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant):
        return repr(float(node.value))
    if isinstance(node, ast.Name):
        return f'F("{node.id[2:]}")'
    if isinstance(node, ast.UnaryOp):
        inner = _to_js(node.operand)
        return f"M(-1,{inner})" if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp):
        fn = {ast.Add: "A", ast.Sub: "S", ast.Mult: "M", ast.Div: "D"}[type(node.op)]
        return f"{fn}({_to_js(node.left)},{_to_js(node.right)})"
    if isinstance(node, ast.Call):
        return f"R({_to_js(node.args[0])},{_to_js(node.args[1])})"
    raise TemplateError("bieu thuc khong hop le")


# ---------------------------------------------------------------------------
# Cot
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Column:
    index: int
    header: str
    field: str
    type: str = "text"  # text | number | int | date
    width: int = 10
    user: str = "edit"  # edit | review | view
    manager: str = "edit"  # edit | view
    auto: bool = False
    source: str = "EXCEL"
    excel_format: str = ""
    required: bool = False
    choices: list[str] = field(default_factory=list)
    merge_group: bool = False
    formula: Formula | None = None

    @property
    def label(self) -> str:
        return re.sub(r"\s+", " ", self.header).strip()

    @property
    def letter(self) -> str:
        return column_letter(self.index + 1)

    def can_edit(self, role: str) -> bool:
        if role in MANAGER_ROLES:
            return self.manager == "edit"
        if role == "user":
            return self.user in ("edit", "review")
        return False

    def needs_review(self, role: str) -> bool:
        return role == "user" and self.user == "review"


def column_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


@dataclass
class SystemColumn:
    header: str
    key: str
    width: int = 14


class FollowUpTemplate:
    def __init__(self, data: dict[str, Any], derived_fields: dict[str, Any] | None = None):
        self.raw = data
        self.freeze_columns = int(data.get("freeze_columns", 10))
        self.header_fill = str(data.get("header_fill", "FFD9D9D9"))
        self.no_column_fill = str(data.get("no_column_fill", "") or "")
        self.columns: list[Column] = []
        for i, c in enumerate(data.get("columns", [])):
            col = Column(
                index=i, header=str(c["header"]), field=str(c["field"]), type=c.get("type", "text"),
                width=int(c.get("width", 10)), user=c.get("user", "edit"), manager=c.get("manager", "edit"),
                auto=bool(c.get("auto", False)), source=c.get("source", "EXCEL"),
                excel_format=c.get("excel_format", ""), required=bool(c.get("required", False)),
                choices=list(c.get("choices", []) or []), merge_group=bool(c.get("merge_group", False)),
            )
            if col.type not in ("text", "number", "int", "date"):
                raise TemplateError(f"Cot {col.label}: kieu '{col.type}' khong ho tro")
            if col.user not in ("edit", "review", "view") or col.manager not in ("edit", "view"):
                raise TemplateError(f"Cot {col.label}: quyen user/manager khong hop le")
            self.columns.append(col)
        self.by_field: dict[str, Column] = {c.field: c for c in self.columns}
        if len(self.by_field) != len(self.columns):
            raise TemplateError("Template co 2 cot trung field")

        fields = set(self.by_field)
        derived_fields = derived_fields or {}
        self.formulas: dict[str, Formula] = {}
        for target, spec in (data.get("formulas") or {}).items():
            if target not in self.by_field:
                raise TemplateError(f"Cong thuc cho cot khong ton tai: {target}")
            if spec.get("kind") == "invoice_suffix":
                rule = dict(derived_fields.get(target) or {})
                if rule.get("rule") != "invoice_suffix":
                    raise TemplateError(
                        f"{target}: can derived_fields.{target} (rule: invoice_suffix) trong field_mapping.yaml")
                rule.setdefault("source", "Invoice")
                f = Formula(target=target, kind="invoice_suffix", rule=rule, refs=[rule["source"]])
            else:
                f = _compile_expr(target, str(spec.get("expr", "")), fields)
            self.formulas[target] = f
            self.by_field[target].formula = f
        self.formula_order = self._topo_order()

        self.system_columns = [SystemColumn(**x) for x in data.get("system_columns", [])]

    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, derived_fields: dict[str, Any] | None = None) -> FollowUpTemplate:
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {}, derived_fields)

    @property
    def fields(self) -> list[str]:
        return [c.field for c in self.columns]

    def _topo_order(self) -> list[str]:
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(name: str) -> None:
            if state.get(name) == 2:
                return
            if state.get(name) == 1:
                raise TemplateError(f"Cong thuc vong lap tai {name}")
            state[name] = 1
            for r in self.formulas[name].refs:
                if r in self.formulas:
                    visit(r)
            state[name] = 2
            order.append(name)

        for name in self.formulas:
            visit(name)
        return order

    # -- gia tri -----------------------------------------------------------

    def parse(self, fld: str, raw: Any) -> tuple[Any, str]:
        """Chuoi go tren luoi -> gia tri co kieu. Tra (value, loi)."""
        col = self.by_field.get(fld)
        kind = col.type if col else "text"
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None, ""
        if kind == "date":
            if isinstance(raw, dt.datetime):
                return raw.date(), ""
            if isinstance(raw, dt.date):
                return raw, ""
            text = str(raw).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?", text):
                text = text[:10]
                try:
                    return dt.date.fromisoformat(text), ""
                except ValueError:
                    return None, f"Ngày không hợp lệ: {text}"
            res = parse_date(text)
            if res.value is None or not res.ok:
                return None, f"Ngày không hợp lệ: {text} (vd 25/09/2026)"
            return res.value, ""
        if kind in ("number", "int"):
            res = parse_decimal(raw)
            if res.value is None or not res.ok:
                return None, f"Cần nhập số: {raw}"
            num = float(res.value)
            if kind == "int":
                if num != int(num):
                    return None, f"Cần nhập số nguyên: {raw}"
                return int(num), ""
            return (int(num) if num == int(num) and abs(num) < 1e15 else num), ""
        return clean_text(raw), ""

    def to_json(self, fld: str, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dt.datetime):
            return value.date().isoformat()
        if isinstance(value, dt.date):
            return value.isoformat()
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        try:
            from decimal import Decimal

            if isinstance(value, Decimal):
                f = float(value)
                return int(f) if f == int(f) else f
        except Exception:  # noqa: BLE001
            pass
        return str(value)

    def from_json(self, fld: str, value: Any) -> Any:
        col = self.by_field.get(fld)
        if value is None or col is None:
            return value
        if col.type == "date" and isinstance(value, str):
            try:
                return dt.date.fromisoformat(value[:10])
            except ValueError:
                return None
        return value

    def row_to_json(self, values: dict[str, Any]) -> dict[str, Any]:
        return {k: self.to_json(k, v) for k, v in values.items() if not k.startswith("_")}

    def row_from_json(self, data: dict[str, Any]) -> dict[str, Any]:
        return {k: self.from_json(k, v) for k, v in (data or {}).items()}

    # -- cong thuc ---------------------------------------------------------

    def compute_formulas(self, values: dict[str, Any], skip: set[str] | None = None,
                         only_missing: bool = False) -> dict[str, Any]:
        """Tinh lai cac cot cong thuc theo thu tu phu thuoc. Tra ve dict cac gia tri moi."""
        skip = skip or set()
        out: dict[str, Any] = {}
        work = dict(values)
        for name in self.formula_order:
            if name in skip:
                continue
            if only_missing and work.get(name) not in (None, ""):
                continue
            v = self.formulas[name].evaluate(work)
            if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
                v = int(v)
            elif isinstance(v, float):
                v = round(v, 6)
            work[name] = v
            out[name] = v
        return out

    def live_formulas(self, values: dict[str, Any], *, always: bool = False) -> set[str]:
        """Cot cong thuc nao cua dong nay dang 'song' (gia tri = ket qua cong thuc hoac dang trong).

        Dong tu file Excel co the go tay gia tri o cot NO (vd file khong dung cong thuc) -> khong
        duoc tu tinh lai de mat so da go. Dong tao tren web (always=True) luon theo cong thuc.
        """
        if always:
            return set(self.formula_order)
        computed = self.compute_formulas(values)
        live: set[str] = set()
        for name in self.formula_order:
            cur = values.get(name)
            if cur in (None, "") or _same_value(cur, computed.get(name)):
                live.add(name)
        return live

    # -- cau hinh gui cho luoi (JS) ----------------------------------------

    def client_config(self, role: str) -> dict[str, Any]:
        return {
            "freeze": self.freeze_columns,
            "columns": [
                {
                    "field": c.field, "title": c.label, "header": c.header, "type": c.type,
                    "width": max(60, c.width * 9), "editable": c.can_edit(role),
                    "review": c.needs_review(role), "formula": c.formula.js() if c.formula else None,
                    "source": c.source, "required": c.required, "choices": c.choices,
                    "merge_group": c.merge_group,
                }
                for c in self.columns
            ],
            "formula_order": self.formula_order,
        }


def _same_value(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) < 1e-6
    return str(a if a is not None else "").strip() == str(b if b is not None else "").strip()


_TEMPLATE: FollowUpTemplate | None = None


def load_template(path: str | Path, derived_fields: dict[str, Any] | None = None) -> FollowUpTemplate:
    global _TEMPLATE
    _TEMPLATE = FollowUpTemplate.load(path, derived_fields)
    return _TEMPLATE
