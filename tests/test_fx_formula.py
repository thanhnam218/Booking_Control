"""Bo cong thuc kieu Excel (followup/fx.py).

tests/fx_cases.json la bo ca kiem dung CHUNG cho ca hai ban cai dat:
    - Python: test duoi day
    - JavaScript: web/static/fx.js doc cung cay AST trong file nay

Sua fx.py ma quen sua fx.js (hoac nguoc lai) -> luoi web va may chu se cho ket qua
khac nhau. Giu file nay lam moc chung de bat som.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from booking_control.followup import fx

CASES = json.loads((Path(__file__).parent / "fx_cases.json").read_text(encoding="utf-8"))


def _ctx():
    vals = {k: (dt.date.fromisoformat(v) if isinstance(v, str) and len(v) == 10 and v[4] == "-" else v)
            for k, v in CASES["vals"].items()}
    l2f = CASES["l2f"]
    return fx.Ctx(field_value=vals.get,
                  letter_value=lambda letter: vals.get(l2f.get(letter, "")),
                  today=dt.date.fromisoformat(CASES["today"]))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["e"])
def test_formula_matches_shared_vector(case):
    got = fx.evaluate(case["node"], _ctx())
    if isinstance(got, dt.date):
        got = got.isoformat()
    if isinstance(got, float):
        got = round(got, 6)
    assert got == case["py"], f"{case['e']}: {got!r} != {case['py']!r}"


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["e"])
def test_expression_still_parses_to_the_same_tree(case):
    """Cay AST phai on dinh - trinh duyet doc dung cay nay."""
    prog = fx.compile_expr(case["e"], set(CASES["vals"]), CASES["l2f"])
    assert prog.node == case["node"]


def test_round_matches_excel_not_python_banker_rounding():
    assert fx.excel_round(2.345, 2) == 2.35
    assert fx.excel_round(2.675, 2) == 2.68
    assert fx.excel_round(-0.125, 2) == -0.13
    assert round(2.675, 2) == 2.67  # Python lam tron kieu ngan hang


@pytest.mark.parametrize("expr,msg", [
    ("SUM(W1:W100)", "không cần số dòng"),
    ("VLOOKUP(1,2,3)", "Chưa hỗ trợ hàm"),
    ("[KhongCoCot]*2", "Không có cột tên"),
    ("1+", "thiếu vế sau"),
    ("ROUND(W)", "tham số"),
    ("__import__('os')", ""),
])
def test_unsafe_or_unsupported_formula_is_rejected(expr, msg):
    with pytest.raises(fx.FxError) as exc:
        fx.compile_expr(expr, set(CASES["vals"]), CASES["l2f"])
    if msg:
        assert msg in str(exc.value)
