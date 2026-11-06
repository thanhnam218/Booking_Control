"""Bo cong thuc kieu Excel dung chung cho ca ba noi.

    - Server (Python): tinh gia tri luu vao database
    - Luoi web (JS):   tinh ngay khi go, khong can cho may chu
    - File Excel:      xuat ra cong thuc that (=ROUND(W2*0.95,2))

Mot bieu thuc duoc phan tich MOT LAN thanh cay AST (JSON thuan). Python va JavaScript
cung doc cay do bang hai trinh thong dich giong het nhau -> ba noi luon cho cung ket qua.
Khong dung eval() / new Function() nen khong co rui ro chay ma la.

Cu phap (tap con Excel, du cho file Follow Up):
    so, "chuoi", TRUE / FALSE
    tham chieu:  [TenTruong]  hoac chu cot cung dong: W, AB, W5 (so dong phai la dong hien tai)
    vung trong CUNG MOT DONG: SUM(N:Q) hoac SUM(N5:Q5)
    phep tinh:   + - * / ^  &(noi chuoi)  = <> < <= > >=
    ham:         xem ALLOWED_FUNCS ben duoi

KHONG ho tro tham chieu sang dong khac (SUM(A1:A100)) - moi o chi tinh tu du lieu cua
chinh dong minh. Tong theo nhieu dong xem o thanh trang thai duoi luoi (COUNT/SUM/AVG).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


class FxError(ValueError):
    """Cong thuc sai cu phap hoac dung ham / tham chieu khong ho tro."""


# Ham  ->  (so tham so it nhat, so tham so nhieu nhat hoac None = khong gioi han)
ALLOWED_FUNCS: dict[str, tuple[int, int | None]] = {
    "IF": (2, 3), "IFERROR": (2, 2), "AND": (1, None), "OR": (1, None), "NOT": (1, 1),
    "ROUND": (2, 2), "ROUNDUP": (2, 2), "ROUNDDOWN": (2, 2), "INT": (1, 1), "ABS": (1, 1),
    "MIN": (1, None), "MAX": (1, None), "SUM": (1, None), "AVERAGE": (1, None),
    "COUNT": (1, None), "COUNTA": (1, None), "MOD": (2, 2), "POWER": (2, 2), "SQRT": (1, 1),
    "CEILING": (1, 2), "FLOOR": (1, 2),
    "LEN": (1, 1), "LEFT": (1, 2), "RIGHT": (1, 2), "MID": (3, 3), "TRIM": (1, 1),
    "UPPER": (1, 1), "LOWER": (1, 1), "CONCAT": (1, None), "CONCATENATE": (1, None),
    "VALUE": (1, 1), "ISBLANK": (1, 1), "ISNUMBER": (1, 1),
    "TODAY": (0, 0), "DATE": (3, 3), "YEAR": (1, 1), "MONTH": (1, 1), "DAY": (1, 1),
    "DAYS": (2, 2),
}

# Ham nhan CA MOT VUNG lam tham so (SUM(N:Q)); cac ham khac chi nhan gia tri don
RANGE_FUNCS = {"SUM", "MIN", "MAX", "AVERAGE", "COUNT", "COUNTA", "CONCAT", "CONCATENATE",
               "AND", "OR"}

CMP_OPS = ("<=", ">=", "<>", "=", "<", ">")


# ---------------------------------------------------------------------------
# Tach tu (tokenizer)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<num>\d+(\.\d+)?|\.\d+)
    | (?P<str>"(?:[^"]|"")*")
    | (?P<fieldref>\[[A-Za-z_][A-Za-z0-9_]*\])
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op><=|>=|<>|[-+*/^&=<>])
    | (?P<lp>\()
    | (?P<rp>\))
    | (?P<comma>[,;])
    | (?P<colon>:)
    """,
    re.VERBOSE,
)


@dataclass(slots=True)
class _Tok:
    kind: str
    text: str
    pos: int


def _tokenize(src: str) -> list[_Tok]:
    out: list[_Tok] = []
    i = 0
    while i < len(src):
        m = _TOKEN_RE.match(src, i)
        if not m:
            raise FxError(f"Ký tự không hiểu ở vị trí {i + 1}: '{src[i]}'")
        i = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            continue
        out.append(_Tok(kind, m.group(), m.start()))
    return out


# ---------------------------------------------------------------------------
# Phan tich cu phap -> cay AST (JSON thuan, gui thang xuong trinh duyet)
#
#   {"t":"num","v":1.5}  {"t":"str","v":"x"}  {"t":"bool","v":true}
#   {"t":"field","f":"Price"}  {"t":"cell","c":"W"}  {"t":"range","a":"N","b":"Q"}
#   {"t":"bin","o":"+","l":<>,"r":<>}  {"t":"un","o":"-","x":<>}
#   {"t":"call","n":"ROUND","a":[<>,...]}
# ---------------------------------------------------------------------------

CELL_RE = re.compile(r"^([A-Za-z]{1,3})(\d+)?$")


class _Parser:
    def __init__(self, tokens: list[_Tok], fields: set[str], letter_to_field: dict[str, str],
                 row_number: int | None):
        self.toks = tokens
        self.i = 0
        self.fields = fields
        # Excel khong phan biet hoa thuong; luoi web con tu viet hoa chuoi bat dau bang '='
        self.fields_ci = {f.lower(): f for f in fields}
        self.letter_to_field = letter_to_field
        self.row_number = row_number
        self.refs: set[str] = set()

    # -- tien ich ---------------------------------------------------------
    def peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> _Tok:
        if self.i >= len(self.toks):
            raise FxError("Công thức bị thiếu vế sau")
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, kind: str) -> _Tok | None:
        t = self.peek()
        if t and t.kind == kind:
            return self.take()
        return None

    def expect(self, kind: str, what: str) -> _Tok:
        t = self.accept(kind)
        if t is None:
            got = self.peek().text if self.peek() else "hết công thức"
            raise FxError(f"Thiếu {what} (gặp '{got}')")
        return t

    # -- cac muc uu tien --------------------------------------------------
    def parse(self) -> dict[str, Any]:
        node = self.compare()
        if self.peek():
            raise FxError(f"Thừa ký tự '{self.peek().text}' ở cuối công thức")
        return node

    def compare(self) -> dict[str, Any]:
        node = self.concat()
        while True:
            t = self.peek()
            if t and t.kind == "op" and t.text in CMP_OPS:
                self.take()
                node = {"t": "bin", "o": t.text, "l": node, "r": self.concat()}
            else:
                return node

    def concat(self) -> dict[str, Any]:
        node = self.addsub()
        while True:
            t = self.peek()
            if t and t.kind == "op" and t.text == "&":
                self.take()
                node = {"t": "bin", "o": "&", "l": node, "r": self.addsub()}
            else:
                return node

    def addsub(self) -> dict[str, Any]:
        node = self.muldiv()
        while True:
            t = self.peek()
            if t and t.kind == "op" and t.text in ("+", "-"):
                self.take()
                node = {"t": "bin", "o": t.text, "l": node, "r": self.muldiv()}
            else:
                return node

    def muldiv(self) -> dict[str, Any]:
        node = self.unary()
        while True:
            t = self.peek()
            if t and t.kind == "op" and t.text in ("*", "/"):
                self.take()
                node = {"t": "bin", "o": t.text, "l": node, "r": self.unary()}
            else:
                return node

    def unary(self) -> dict[str, Any]:
        t = self.peek()
        if t and t.kind == "op" and t.text in ("-", "+"):
            self.take()
            return {"t": "un", "o": t.text, "x": self.unary()}
        return self.power()

    def power(self) -> dict[str, Any]:
        node = self.primary()
        t = self.peek()
        if t and t.kind == "op" and t.text == "^":
            self.take()
            return {"t": "bin", "o": "^", "l": node, "r": self.unary()}
        return node

    def primary(self) -> dict[str, Any]:
        t = self.take()
        if t.kind == "num":
            return {"t": "num", "v": float(t.text)}
        if t.kind == "str":
            return {"t": "str", "v": t.text[1:-1].replace('""', '"')}
        if t.kind == "lp":
            node = self.compare()
            self.expect("rp", "dấu ) đóng ngoặc")
            return node
        if t.kind == "fieldref":
            name = self.fields_ci.get(t.text[1:-1].lower())
            if name is None:
                raise FxError(f"Không có cột tên [{t.text[1:-1]}]")
            self.refs.add(name)
            return {"t": "field", "f": name}
        if t.kind == "name":
            return self.name_token(t)
        raise FxError(f"Không hiểu '{t.text}'")

    def name_token(self, t: _Tok) -> dict[str, Any]:
        up = t.text.upper()
        nxt = self.peek()
        if nxt and nxt.kind == "lp":  # ten ham: NAME(
            if up not in ALLOWED_FUNCS:
                raise FxError(f"Chưa hỗ trợ hàm {up}()")
            self.take()
            args: list[dict[str, Any]] = []
            if not self.accept("rp"):
                while True:
                    args.append(self.arg(up))
                    if self.accept("comma"):
                        continue
                    self.expect("rp", f"dấu ) đóng hàm {up}")
                    break
            lo, hi = ALLOWED_FUNCS[up]
            if len(args) < lo or (hi is not None and len(args) > hi):
                rng = str(lo) if hi == lo else f"{lo}-{hi if hi is not None else 'n'}"
                raise FxError(f"Hàm {up}() cần {rng} tham số, đang có {len(args)}")
            return {"t": "call", "n": up, "a": args}
        if up == "TRUE":
            return {"t": "bool", "v": True}
        if up == "FALSE":
            return {"t": "bool", "v": False}
        return self.cell_ref(t)

    def arg(self, func: str) -> dict[str, Any]:
        """Tham so co the la mot vung A:C neu ham cho phep."""
        node = self.compare()
        nxt = self.peek()
        if nxt and nxt.kind == "colon":
            if node.get("t") != "cell":
                raise FxError("Vùng phải viết dạng cột:cột, ví dụ SUM(N:Q)")
            self.take()
            end = self.compare()
            if end.get("t") != "cell":
                raise FxError("Vùng phải viết dạng cột:cột, ví dụ SUM(N:Q)")
            if func not in RANGE_FUNCS:
                raise FxError(f"Hàm {func}() không nhận cả một vùng")
            for letter in _letters_between(node["c"], end["c"]):
                fld = self.letter_to_field.get(letter)
                if fld:
                    self.refs.add(fld)
            return {"t": "range", "a": node["c"], "b": end["c"]}
        return node

    def cell_ref(self, t: _Tok) -> dict[str, Any]:
        m = CELL_RE.match(t.text)
        if not m:
            raise FxError(f"Không hiểu '{t.text}'. Dùng [TênCột] hoặc chữ cột như W, AB")
        letter, rownum = m.group(1).upper(), m.group(2)
        if letter not in self.letter_to_field:
            raise FxError(f"Bảng không có cột {letter}")
        # Khong biet dong nao -> khong cho viet so dong (luoi con sap xep lai duoc)
        if rownum is not None and self.row_number is None:
            raise FxError(f"Viết '{letter}' thay cho '{t.text}': công thức luôn tính trên chính dòng "
                          "của ô đó, nên không cần số dòng (lưới còn sắp xếp lại được).")
        if rownum is not None and self.row_number is not None and int(rownum) != self.row_number:
            raise FxError(f"Chỉ tính được trong cùng một dòng: '{t.text}' trỏ sang dòng {rownum}, "
                          f"ô này ở dòng {self.row_number}")
        self.refs.add(self.letter_to_field[letter])
        return {"t": "cell", "c": letter}


def _letters_between(a: str, b: str) -> list[str]:
    ia, ib = letter_index(a), letter_index(b)
    if ia > ib:
        ia, ib = ib, ia
    return [index_letter(i) for i in range(ia, ib + 1)]


def letter_index(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def index_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------------------------------------------------------------------------
# Ket qua phan tich
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Program:
    source: str
    node: dict[str, Any]
    refs: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"src": self.source, "node": self.node, "refs": self.refs}


def compile_expr(expr: str, fields: set[str], letter_to_field: dict[str, str] | None = None,
                 row_number: int | None = None) -> Program:
    """Phan tich mot bieu thuc. Bo dau '=' dau neu nguoi dung go kieu Excel."""
    src = (expr or "").strip()
    if src.startswith("="):
        src = src[1:].strip()
    if not src:
        raise FxError("Công thức trống")
    if len(src) > 500:
        raise FxError("Công thức quá dài (tối đa 500 ký tự)")
    p = _Parser(_tokenize(src), set(fields), dict(letter_to_field or {}), row_number)
    node = p.parse()
    return Program(source=src, node=node, refs=sorted(p.refs))


# ---------------------------------------------------------------------------
# Tinh gia tri (Python) - ban song doi cua ham fxEval() trong followup.js
# ---------------------------------------------------------------------------

EPOCH = dt.date(1899, 12, 30)  # goc ngay thang cua Excel


def to_num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, dt.datetime):
        return float((v.date() - EPOCH).days)
    if isinstance(v, dt.date):
        return float((v - EPOCH).days)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return float((dt.date.fromisoformat(s[:10]) - EPOCH).days)
    except ValueError:
        return None


def to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return False
    n = to_num(v)
    return bool(n) if n is not None else bool(str(v).strip())


def as_date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    n = to_num(v)
    if n is None:
        return None
    try:
        return EPOCH + dt.timedelta(days=int(n))
    except (OverflowError, ValueError):
        return None


def excel_round(x: float, n: int) -> float:
    """ROUND cua Excel: 0.5 lam tron RA XA so 0 (Python round() lam tron ve so chan)."""
    q = Decimal(1).scaleb(-int(n))
    return float(Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP))


@dataclass(slots=True)
class Ctx:
    """Cach lay gia tri cua dong hien tai."""

    field_value: Callable[[str], Any]
    letter_value: Callable[[str], Any]
    today: dt.date | None = None


def evaluate(node: dict[str, Any], ctx: Ctx) -> Any:
    t = node.get("t")
    if t == "num":
        return node["v"]
    if t in ("str", "bool"):
        return node["v"]
    if t == "field":
        return ctx.field_value(node["f"])
    if t == "cell":
        return ctx.letter_value(node["c"])
    if t == "range":
        return [ctx.letter_value(x) for x in _letters_between(node["a"], node["b"])]
    if t == "un":
        v = to_num(evaluate(node["x"], ctx))
        if v is None:
            return None
        return -v if node["o"] == "-" else v
    if t == "bin":
        return _binary(node["o"], evaluate(node["l"], ctx), evaluate(node["r"], ctx))
    if t == "call":
        return _call(node["n"], node["a"], ctx)
    raise FxError("Cây công thức không hợp lệ")


def _binary(op: str, a: Any, b: Any) -> Any:
    if op == "&":
        return to_text(a) + to_text(b)
    if op in CMP_OPS:
        na, nb = to_num(a), to_num(b)
        if na is not None and nb is not None:
            left: Any = na
            right: Any = nb
        else:
            left, right = to_text(a).upper(), to_text(b).upper()
        return {
            "=": left == right, "<>": left != right, "<": left < right,
            "<=": left <= right, ">": left > right, ">=": left >= right,
        }[op]
    na, nb = to_num(a), to_num(b)
    if na is None or nb is None:
        return None
    if op == "+":
        out = na + nb
    elif op == "-":
        out = na - nb
    elif op == "*":
        out = na * nb
    elif op == "/":
        if nb == 0:
            return None
        out = na / nb
    elif op == "^":
        try:
            out = float(na ** nb)
        except (OverflowError, ValueError, ZeroDivisionError):
            return None
    else:
        raise FxError(f"Phép tính không hỗ trợ: {op}")
    # Ngay +/- so  ->  van la ngay (giong Excel)
    if op in ("+", "-") and _is_date(a) and not _is_date(b):
        return as_date(out)
    return out


def _is_date(v: Any) -> bool:
    return isinstance(v, (dt.date, dt.datetime))


def _flat(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for v in values:
        if isinstance(v, list):
            out.extend(v)
        else:
            out.append(v)
    return out


def _nums(values: list[Any]) -> list[float]:
    return [n for n in (to_num(v) for v in _flat(values)) if n is not None]


def _round_away(x: float, digits: int, up: bool) -> float:
    """ROUNDUP / ROUNDDOWN cua Excel: lam tron RA XA / VE PHIA so 0."""
    p = 10.0 ** int(digits)
    scaled = abs(x) * p
    r = math.ceil(scaled - 1e-9) if up else math.floor(scaled + 1e-9)
    return math.copysign(r / p, x)


def _call(name: str, arg_nodes: list[dict[str, Any]], ctx: Ctx) -> Any:  # noqa: C901 - bang tra ham
    # IF / IFERROR chi tinh nhanh nao thuc su can (giong Excel)
    if name == "IF":
        if to_bool(evaluate(arg_nodes[0], ctx)):
            return evaluate(arg_nodes[1], ctx)
        return evaluate(arg_nodes[2], ctx) if len(arg_nodes) > 2 else False
    if name == "IFERROR":
        try:
            v = evaluate(arg_nodes[0], ctx)
        except FxError:
            return evaluate(arg_nodes[1], ctx)
        return evaluate(arg_nodes[1], ctx) if v is None else v

    args = [evaluate(a, ctx) for a in arg_nodes]
    flat = _flat(args)

    if name == "AND":
        return all(to_bool(v) for v in flat)
    if name == "OR":
        return any(to_bool(v) for v in flat)
    if name == "NOT":
        return not to_bool(args[0])
    if name in ("SUM", "MIN", "MAX", "AVERAGE"):
        nums = _nums(args)
        if not nums:
            return 0.0 if name == "SUM" else None
        if name == "SUM":
            return sum(nums)
        if name == "MIN":
            return min(nums)
        if name == "MAX":
            return max(nums)
        return sum(nums) / len(nums)
    if name == "COUNT":
        return float(len(_nums(args)))
    if name == "COUNTA":
        return float(sum(1 for v in flat if v not in (None, "")))
    if name in ("CONCAT", "CONCATENATE"):
        return "".join(to_text(v) for v in flat)

    a0 = to_num(args[0]) if args else None
    if name == "ROUND":
        n = to_num(args[1])
        return None if a0 is None or n is None else excel_round(a0, int(n))
    if name in ("ROUNDUP", "ROUNDDOWN"):
        n = to_num(args[1])
        return None if a0 is None or n is None else _round_away(a0, int(n), up=name == "ROUNDUP")
    if name == "INT":
        return None if a0 is None else float(math.floor(a0))
    if name == "ABS":
        return None if a0 is None else abs(a0)
    if name == "SQRT":
        return None if a0 is None or a0 < 0 else math.sqrt(a0)
    if name == "MOD":
        b = to_num(args[1])
        return None if a0 is None or not b else a0 - b * math.floor(a0 / b)
    if name == "POWER":
        b = to_num(args[1])
        try:
            return None if a0 is None or b is None else float(a0 ** b)
        except (OverflowError, ValueError, ZeroDivisionError):
            return None
    if name in ("CEILING", "FLOOR"):
        step = to_num(args[1]) if len(args) > 1 else 1.0
        if a0 is None or not step:
            return None
        f = math.ceil if name == "CEILING" else math.floor
        return f(a0 / step - (1e-9 if name == "CEILING" else -1e-9)) * step

    if name == "LEN":
        return float(len(to_text(args[0])))
    if name == "TRIM":
        return " ".join(to_text(args[0]).split())
    if name == "UPPER":
        return to_text(args[0]).upper()
    if name == "LOWER":
        return to_text(args[0]).lower()
    if name in ("LEFT", "RIGHT"):
        n = int(to_num(args[1]) or 0) if len(args) > 1 else 1
        s = to_text(args[0])
        if n <= 0:
            return ""
        return s[:n] if name == "LEFT" else s[-n:]
    if name == "MID":
        s = to_text(args[0])
        start = int(to_num(args[1]) or 1)
        n = int(to_num(args[2]) or 0)
        begin = max(0, start - 1)
        return s[begin: begin + max(0, n)]
    if name == "VALUE":
        return to_num(args[0])
    if name == "ISBLANK":
        return args[0] in (None, "")
    if name == "ISNUMBER":
        return not _is_date(args[0]) and to_num(args[0]) is not None

    if name == "TODAY":
        return ctx.today or dt.date.today()
    if name == "DATE":
        y, m, d = (to_num(x) for x in args[:3])
        if y is None or m is None or d is None:
            return None
        try:
            return dt.date(int(y), int(m), int(d))
        except ValueError:
            return None
    if name in ("YEAR", "MONTH", "DAY"):
        d = as_date(args[0])
        return None if d is None else float(getattr(d, name.lower()))
    if name == "DAYS":
        d1, d2 = as_date(args[0]), as_date(args[1])
        return None if d1 is None or d2 is None else float((d1 - d2).days)
    raise FxError(f"Chưa hỗ trợ hàm {name}()")


# ---------------------------------------------------------------------------
# Xuat ra cong thuc Excel that
# ---------------------------------------------------------------------------


# Do uu tien de chi dat ngoac khi that su can - cong thuc xuat ra phai giong het
# file Excel that (=ROUND(W7*0.95,2) chu khong phai =ROUND((W7*0.95),2)).
_PREC = {"^": 5, "*": 4, "/": 4, "+": 3, "-": 3, "&": 2,
         "=": 1, "<>": 1, "<": 1, "<=": 1, ">": 1, ">=": 1}
_NON_ASSOC = ("-", "/", "^")  # a-(b-c) != (a-b)-c  ->  ve phai phai giu ngoac


def to_excel(node: dict[str, Any], letter_of: dict[str, str], row: int) -> str:
    """Tra ve chuoi cong thuc (chua co dau '='). Nem FxError neu thieu cot."""
    t = node.get("t")
    if t == "num":
        v = node["v"]
        return str(int(v)) if v == int(v) else repr(v)
    if t == "str":
        return '"' + str(node["v"]).replace('"', '""') + '"'
    if t == "bool":
        return "TRUE" if node["v"] else "FALSE"
    if t == "field":
        letter = letter_of.get(node["f"])
        if not letter:
            raise FxError(f"Cột [{node['f']}] không có trong file xuất ra")
        return f"{letter}{row}"
    if t == "cell":
        return f"{node['c']}{row}"
    if t == "range":
        return f"{node['a']}{row}:{node['b']}{row}"
    if t == "un":
        inner = to_excel(node["x"], letter_of, row)
        if node["x"].get("t") == "bin":
            inner = f"({inner})"
        return f"{node['o']}{inner}"
    if t == "bin":
        op = node["o"]
        mine = _PREC[op]
        left = _excel_side(node["l"], letter_of, row, mine, right_side=False, op=op)
        right = _excel_side(node["r"], letter_of, row, mine, right_side=True, op=op)
        return f"{left}{op}{right}"
    if t == "call":
        inner = ",".join(to_excel(a, letter_of, row) for a in node["a"])
        return f"{node['n']}({inner})"
    raise FxError("Cây công thức không hợp lệ")


def _excel_side(child: dict[str, Any], letter_of: dict[str, str], row: int, parent_prec: int,
                *, right_side: bool, op: str) -> str:
    text = to_excel(child, letter_of, row)
    if child.get("t") != "bin":
        return text
    child_prec = _PREC[child["o"]]
    if child_prec < parent_prec or (right_side and child_prec == parent_prec and op in _NON_ASSOC):
        return f"({text})"
    return text
