"""Cross-cutting platform invariants from the goal spec (docs/goal/spec/) with no prior test.

- INV market-agnostic core: no module under ``src/gridq`` compares anything against a ``"GB"``
  string literal — GB specifics are content behind the generic market interface; "if the core
  ever branches on ``market == 'GB'``, the abstraction has failed" (CLAUDE.md architecture rule).
- INV CLI render byte-identity: ``gridq render`` over the same saved QuoteRecord produces
  byte-identical documents on every invocation (manual ch17; the double-render invariant at the
  CLI seam, previously only tested at the library layer).
- INV tariff schema: negative rates are rejected at validation, on the banded maps and on the
  DUoS fields alike (a negative price is a data error, never silently priced).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.cli import app
from gridq.schemas.tariff import TariffStack

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "gridq"
EXAMPLES = REPO / "examples"

runner = CliRunner()


def test_core_never_compares_against_a_gb_literal():
    """AST-scan every core module: no comparison against a "GB" string constant anywhere."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Constant) and operand.value == "GB":
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, (
        "core modules must never branch on a 'GB' literal (market specifics are content "
        f"behind the generic interface); found: {offenders}"
    )


def test_cli_render_is_byte_identical_across_invocations(tmp_path: Path, monkeypatch):
    """The same saved QuoteRecord renders to byte-identical documents, every time."""
    from tests._dpa_support import register_fixture_dpas

    # Rendering writes the site's documents, so it needs a DPA permit (WP-GRC-05): a TEST
    # FIXTURE one for the fictitious example site, kept outside both output directories.
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_000123")
    quote = EXAMPLES / "quote_record_example.json"
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    for out in (out_a, out_b):
        res = runner.invoke(app, ["render", str(quote), "--out", str(out)])
        assert res.exit_code == 0, res.output
    rendered_a = sorted(p.name for p in out_a.iterdir())
    assert rendered_a, "render produced no documents"
    assert rendered_a == sorted(p.name for p in out_b.iterdir())
    for name in rendered_a:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes(), (
            f"{name} differs between two renders of the identical quote"
        )


def test_negative_tariff_rates_are_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="T",
            import_rates={"day": -0.10},
        )
    with pytest.raises(ValueError):
        TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="T",
            duos_red_rate=-0.05,
        )
    with pytest.raises(ValueError, match="non-negative"):
        TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="T",
            levies={"ro": -0.01},
        )
