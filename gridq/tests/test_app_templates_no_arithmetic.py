"""Structural guard: the app's Jinja templates do no arithmetic on displayed figures (W5-1).

The deterministic-core invariant ("the UI never computes or authors a figure") is enforced for the
outputs HTML by an AST test; the Wave-5 templates need the same guarantee. Numbers reach templates
already turned into strings by `gridq.outputs.formatting` (exposed as `fmt_*` globals), or as raw
values inside a JSON island the client renderer only positions — never as template arithmetic.

This parses every app template with Jinja2's own parser and asserts the expression AST contains no
arithmetic operators (`+ - * / // % **`) and no aggregation/rounding filters or calls
(`sum`/`round`/`abs`/`min`/`max`/`pow`). It mirrors `tests/test_outputs_no_arithmetic_in_html.py`.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
from jinja2 import nodes

_TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "gridq" / "app" / "templates"

# Arithmetic binary-operator node types (subclasses of nodes.BinExpr).
_ARITH_NODES = tuple(
    getattr(nodes, name)
    for name in ("Add", "Sub", "Mul", "Div", "FloorDiv", "Mod", "Pow")
    if hasattr(nodes, name)
)
# Unary arithmetic (e.g. negation) — also forbidden on a displayed figure.
_UNARY_NODES = tuple(getattr(nodes, name) for name in ("Neg", "Pos") if hasattr(nodes, name))
_FORBIDDEN_CALLABLES = {"sum", "round", "abs", "min", "max", "pow", "divmod"}


def _templates() -> list[Path]:
    return sorted(_TEMPLATES.glob("*.html"))


def test_templates_exist() -> None:
    assert _templates(), "no app templates found to scan"


def test_no_arithmetic_in_templates() -> None:
    env = jinja2.Environment(autoescape=True)
    offences: list[str] = []
    for path in _templates():
        ast = env.parse(path.read_text(encoding="utf-8"))
        for _ in ast.find_all(_ARITH_NODES):
            offences.append(f"{path.name}: arithmetic operator (+ - * / // % **)")
        if _UNARY_NODES:
            for _ in ast.find_all(_UNARY_NODES):
                offences.append(f"{path.name}: unary arithmetic operator")
        for filt in ast.find_all(nodes.Filter):
            if filt.name in _FORBIDDEN_CALLABLES:
                offences.append(f"{path.name}: forbidden filter |{filt.name}")
        for call in ast.find_all(nodes.Call):
            target = call.node
            if isinstance(target, nodes.Name) and target.name in _FORBIDDEN_CALLABLES:
                offences.append(f"{path.name}: forbidden call {target.name}()")
    assert not offences, "arithmetic must not act on figures in templates:\n" + "\n".join(offences)
