"""The provenance claim must be narrower than the old one, and it must stay true.

The six 2026-08-13 packs footed with::

    Every figure on this page is copied from run record quote_run_a30b80c0a4d44c4b through the
    output view model and formatted for display. The generator performs no arithmetic...

which was false twice over: the cited record was never written anywhere, and the seam beneath the
renderer *does* compute — ``viewmodel._split`` sums the per-stream revenues into the priced-floor
total a reader sees (£7,072.62 + £4,505.80 = £11,578.42), and ``viewmodel._cashflows`` derives the
operating-year count.

The replacement is deliberately **narrower**, not vaguer: it names the file, publishes its hash, and
enumerates exhaustively what is recomputed rather than copied. That only works while the enumeration
is complete, so the load-bearing test here is `test_the_recomputed_list_is_still_exhaustive`: it
AST-scans the view model and fails if arithmetic appears in a function the claim does not name.
Widening the sentence to make that test pass would be the wrong repair — the enumeration is the
claim's whole value.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gridq.provenance import CLAIM_VERSION, RECOMPUTED_AT_RENDER, publication_claim
from gridq.provenance.claims import PublicationReceipt, RunArtifact

VIEWMODEL = Path(__file__).resolve().parents[1] / "src" / "gridq" / "outputs" / "viewmodel.py"

#: Functions in `gridq.outputs.viewmodel` allowed to contain arithmetic, and what the claim says
#: about each. Anything else that starts computing makes the claim false; the fix is to add it to
#: `RECOMPUTED_AT_RENDER` (and to this map), never to relax the check.
ARITHMETIC_IS_DISCLOSED_FOR = {
    "_split": "the priced-floor and merchant-upside totals",
    "_cashflows": "the operating-year count",
    # `value[:57] + "…"` — string truncation for display. Not a figure, so not in the claim.
    "_assumption_sources": None,
    "_best_candidate": "the best evaluated candidate shown when no system is recommended",
}


def _receipt(*, with_site: bool = True, recomputed: tuple[str, ...] = RECOMPUTED_AT_RENDER):
    artifacts = [
        RunArtifact(filename="quote.json", sha256="sha256:" + "a" * 64, size_bytes=1234),
    ]
    if with_site:
        artifacts.append(
            RunArtifact(filename="site.json", sha256="sha256:" + "b" * 64, size_bytes=567)
        )
    return PublicationReceipt(
        quote_id="quote_run_a30b80c0a4d44c4b",
        input_hash="sha256:" + "c" * 64,
        assumption_version="gb_v1_6",
        code_version="abc1234",
        record_schema_version="0.1.0",
        record_created_at="2026-08-13T09:00:00",
        artifacts=artifacts,
        recomputed_at_render=list(recomputed),
    )


def test_the_claim_names_the_file_and_publishes_its_hash() -> None:
    """ "Copied from run record X" is checkable only if the reader is told where X is."""
    claim = publication_claim(_receipt())
    assert "quote_run_a30b80c0a4d44c4b" in claim
    assert "runs/quote_run_a30b80c0a4d44c4b/quote.json" in claim
    assert "sha256:" + "a" * 64 in claim
    assert "runs/quote_run_a30b80c0a4d44c4b/site.json" in claim
    assert "sha256:" + "b" * 64 in claim
    assert "provenance.json" in claim


def test_the_claim_states_the_ordering_that_makes_it_true() -> None:
    """The files existed before the render, and the render read them. That is the guarantee."""
    claim = publication_claim(_receipt())
    assert "written before this page was rendered" in claim
    assert "re-reading those files" in claim
    assert "re-read at its source by anyone holding this pack" in claim


def test_the_claim_enumerates_what_is_recomputed_and_counts_it() -> None:
    """An exhaustive list with its own length: a reader can check nothing was left off."""
    claim = publication_claim(_receipt())
    assert f"Exactly {len(RECOMPUTED_AT_RENDER)} value(s)" in claim
    assert "this is the complete list" in claim
    for item in RECOMPUTED_AT_RENDER:
        assert item in claim
    # The old sentence's two false absolutes must not reappear.
    assert "performs no arithmetic" not in claim
    assert "Every figure on this page is copied from run record" not in claim


def test_the_claim_does_not_overreach_to_a_site_model_that_was_not_persisted() -> None:
    """No site.json, no claim about site-derived figures — the sentence adapts to the evidence."""
    claim = publication_claim(_receipt(with_site=False))
    assert "site.json" not in claim
    assert "No site model was persisted with this run" in claim


def test_the_claim_names_the_assumption_release_the_recomputation_is_pinned_to() -> None:
    assert "gb_v1_6" in publication_claim(_receipt())
    unstamped = _receipt()
    unstamped.assumption_version = None
    assert "unstamped" in publication_claim(unstamped)


def test_the_claim_is_deterministic() -> None:
    receipt = _receipt()
    assert publication_claim(receipt) == publication_claim(receipt)


def test_run_dir_label_never_leaks_an_absolute_path() -> None:
    """The reader gets a store-relative label; the producing machine's layout is not the pack's."""
    claim = publication_claim(_receipt(), run_dir_label="runs/quote_run_a30b80c0a4d44c4b")
    assert "/Users/" not in claim
    assert "/private/tmp" not in claim


def _arithmetic_functions(path: Path) -> dict[str, list[str]]:
    """Every function in ``path`` containing arithmetic, with what it does."""
    arith_ops = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow, ast.Mod)
    numeric_calls = {"sum", "round", "min", "max", "abs"}
    found: dict[str, list[str]] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        hits: list[str] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.BinOp) and isinstance(node.op, arith_ops):
                hits.append(type(node.op).__name__)
            elif isinstance(node, ast.AugAssign):
                hits.append("AugAssign")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in numeric_calls
            ):
                hits.append(node.func.id)
        if hits:
            found[fn.name] = sorted(set(hits))
    return found


def test_the_recomputed_list_is_still_exhaustive() -> None:
    """The claim's load-bearing guard: no undisclosed arithmetic in the seam that feeds the packs.

    `gridq.outputs.viewmodel` is the one place that touches the record on the way to a page, so
    it is where a new render-time computation appears. If this fails, a value on the page is derived
    and the claim says nothing about it — which makes the claim false again. Add the value to
    `RECOMPUTED_AT_RENDER` (naming the function and the operation) rather than loosening the
    sentence.
    """
    computing = set(_arithmetic_functions(VIEWMODEL))
    undisclosed = computing - set(ARITHMETIC_IS_DISCLOSED_FOR)
    assert not undisclosed, (
        f"gridq.outputs.viewmodel now computes in {sorted(undisclosed)}, which the provenance "
        "claim does not disclose. Add each to gridq.provenance.claims.RECOMPUTED_AT_RENDER."
    )
    # ...and every function the map says is disclosed must actually be named in the claim.
    for function, phrase in ARITHMETIC_IS_DISCLOSED_FOR.items():
        if phrase is None:
            continue
        assert function in computing, f"{function} no longer computes; the claim is now overstated"
        assert any(phrase in item for item in RECOMPUTED_AT_RENDER), phrase
        assert any(function in item for item in RECOMPUTED_AT_RENDER), function


def test_the_recomputed_list_names_the_two_non_viewmodel_recomputations() -> None:
    """The ranked asks and the sensitivity rows are computed in `outputs.generate`, not the seam."""
    joined = " ".join(RECOMPUTED_AT_RENDER)
    assert "build_ranked_data_asks" in joined
    assert "gridq.outputs.sensitivities" in joined
    assert "only when the run record carries none of its own" in joined


def test_claim_version_is_pinned_so_a_pack_traces_to_its_wording() -> None:
    assert CLAIM_VERSION == "persisted-run-record-v1"
    assert _receipt().claim_version == CLAIM_VERSION


def test_an_empty_recomputed_list_states_that_nothing_is_derived() -> None:
    """If a future pack copies everything, the claim says so plainly rather than listing nothing."""
    claim = publication_claim(_receipt(recomputed=()))
    assert "No value on this page is recomputed; every one is a copy." in claim


def test_a_receipt_with_no_record_cannot_produce_a_claim() -> None:
    receipt = _receipt()
    receipt.artifacts = [a for a in receipt.artifacts if a.filename != "quote.json"]
    with pytest.raises(ValueError, match="pins no quote.json"):
        publication_claim(receipt)
