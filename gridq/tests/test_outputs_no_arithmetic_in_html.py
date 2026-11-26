"""M8 outputs — the HTML generator does no arithmetic (it only formats existing values).

The hard rule for the HTML report is "no arithmetic in HTML generation except formatting existing
values". We enforce it structurally: the `html_report` module's AST must contain no arithmetic
`BinOp`/`AugAssign` and no aggregation call (``sum``/``round``/``min``/``max``/``abs``/…). Every
figure is computed upstream in the deterministic finance core and turned into a string only in
`formatting` (where the division-by-1000 for £k headlines legitimately lives) — so this test also
confirms that the arithmetic genuinely sits in `formatting`, not that it was banned everywhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

import gridq.outputs.formatting as formatting_mod
import gridq.outputs.html_report as html_mod

_ARITH_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.MatMult,
)
_FORBIDDEN_CALLS = {"sum", "round", "min", "max", "abs", "pow", "divmod"}


def _module_tree(module: object) -> ast.Module:
    path = Path(module.__file__)  # type: ignore[attr-defined]
    return ast.parse(path.read_text(encoding="utf-8"))


def test_html_report_contains_no_arithmetic() -> None:
    tree = _module_tree(html_mod)

    binops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITH_OPS)
    ]
    assert not binops, (
        f"arithmetic BinOp(s) found in html_report.py: {[ast.dump(n) for n in binops]}"
    )

    aug = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AugAssign) and isinstance(node.op, _ARITH_OPS)
    ]
    assert not aug, "augmented arithmetic assignment found in html_report.py"

    bad_calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FORBIDDEN_CALLS
    ]
    assert not bad_calls, f"aggregation call(s) found in html_report.py: {bad_calls}"


def test_formatting_is_where_value_scaling_lives() -> None:
    """Sanity: arithmetic isn't banned globally — the formatters legitimately scale values."""
    tree = _module_tree(formatting_mod)
    binops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITH_OPS)
    ]
    assert binops, (
        "expected the formatting module to do value-formatting arithmetic (e.g. £k scaling)"
    )
