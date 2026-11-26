"""M8 outputs: the RENDERED deliverable carries no em dash.

The em dash is the tell that copy was written by a language model, and these artifacts go to a
customer and to a lender. The app row already pins the property on its own sources
(`test_app_design_system.py::test_ui_copy_carries_no_em_dashes`, which sweeps templates and static
assets); nothing pinned it for the documents `gridq.outputs` renders, which is where the sweep
matters most because that is the artifact a client actually reads.

Two complementary checks, in the order they catch things:

1. **Rendered output** (the artifact, not the source): the underwriting pack, the customer proposal,
   the HTML report and the consulting report are generated from real records and swept. This is the
   check that matters, because only rendering resolves f-strings, prose-drafter copy and every
   `unavailable(...)` label into the text a reader sees.
2. **Every non-docstring string literal** in the package, over each module's AST. Rendering one
   record cannot reach every branch (the caveat ladder, the mock-solver banner, the
   no-viable-configuration verdict, the empty-table placeholders), so the literal sweep covers the
   copy the fixtures do not exercise. Docstrings and comments are deliberately excluded: they are
   repo prose, never rendered, and the app-row test excludes them for the same reason.

**Scoping, stated so a failure is never ambiguous.** The renderers reproduce record-authored text
verbatim by design (`quote.warnings` above all: "quote.warnings, verbatim and unfiltered"). Those
strings are authored in `gridq.finance` / `gridq.assumptions` and are NOT this package's copy, so
the populated fixtures clear `warnings` before rendering rather than the renderer scrubbing them at
render time. Scrubbing would break the verbatim-warning invariant, which matters far more than
punctuation. Any em dash these tests catch is therefore renderer-authored copy.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import gridq.outputs as outputs_pkg
import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.ingestion.improvement_asks import build_ranked_data_asks
from gridq.outputs import generate_artifacts, render_report_for_quote
from gridq.outputs.consulting_report import ConfidenceView
from gridq.quotes.confidence import assess_confidence

EM_DASH = "—"
_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "quote_record_example.json"


def _offences(name: str, doc: str) -> list[str]:
    """Every em dash in ``doc``, with surrounding text so a failure names the sentence."""
    out: list[str] = []
    for idx, char in enumerate(doc):
        if char == EM_DASH:
            out.append(f"{name}: ...{doc[max(0, idx - 70) : idx + 70]}...")
    return out


def _assert_clean(**docs: str) -> None:
    offences = [o for name, doc in docs.items() for o in _offences(name, doc)]
    assert not offences, "em dash in rendered output:\n" + "\n".join(offences)


# --- the check that matters: the rendered artifact ------------------------------------------


def test_degraded_record_renders_all_three_artifacts_without_an_em_dash() -> None:
    """The committed example record (null result, null finance) exercises the *unavailable* copy.

    Every figure degrades to a labelled `formatting.unavailable(...)`, so this render is almost
    entirely renderer-authored prose: the strictest sweep available.
    """
    quote = s.QuoteRecord.model_validate(json.loads(_EXAMPLE.read_text(encoding="utf-8")))
    arts = generate_artifacts(quote)
    _assert_clean(
        memo=arts.memo_markdown,
        proposal=arts.proposal_markdown,
        report_html=arts.report_html,
    )


def test_populated_record_renders_all_three_artifacts_without_an_em_dash(rich_quote) -> None:
    """A fully-populated record: priced streams, gated upside, cashflows, DSCR, sensitivities.

    ``warnings`` are cleared because the pack reproduces them verbatim from the record and they are
    authored elsewhere (see the module docstring). Everything else here is this package's copy.
    """
    quote = rich_quote.quote.model_copy(
        update={"quote_mode": s.QuoteMode.UNDERWRITTEN, "warnings": []}
    )
    arts = generate_artifacts(quote, rich_quote.site, assumptions=load_assumptions("gb_v0_1"))
    _assert_clean(
        memo=arts.memo_markdown,
        proposal=arts.proposal_markdown,
        report_html=arts.report_html,
    )


def test_consulting_report_renders_without_an_em_dash(rich_quote) -> None:
    """The consulting deliverable, with the confidence band and ranked data asks populated.

    Those two paths carry their own copy (the band note, the binding-factor lines, the ask table),
    and neither renders unless it is supplied.

    The band is built here rather than adapted from a live `assess_confidence` run for the same
    reason `warnings` are cleared: a real assessment's `factor.reason` strings are authored in
    `gridq.quotes.confidence` and reproduced verbatim, so they belong to that module's copy, not
    this package's. `from_assessment` is still exercised below for the joiner it owns.
    """
    quote = rich_quote.quote.model_copy(update={"warnings": []})
    band = ConfidenceView(
        band="low",
        ceiling="medium",
        binding_factors=("load basis: the reconciliation is unverified",),
    )
    html = render_report_for_quote(
        quote,
        rich_quote.site,
        assumptions=load_assumptions("gb_v0_1"),
        confidence=band,
        data_asks=build_ranked_data_asks(rich_quote.site),
    )
    assert html.startswith("<!doctype html>")  # fixture sanity: something really rendered
    assert "LOW" in html and "load basis" in html  # the band paths really rendered
    _assert_clean(consulting_report=html)


def test_confidence_view_joins_a_live_assessment_without_adding_an_em_dash(rich_quote) -> None:
    """`from_assessment` owns the ``name``/``reason`` joiner: it adds no punctuation of its own.

    The factor reasons themselves are `gridq.quotes.confidence` copy, reproduced verbatim, so this
    asserts on what the adapter contributes rather than on the text it carries.
    """
    assessment = assess_confidence(rich_quote.site, rich_quote.quote)
    view = ConfidenceView.from_assessment(assessment, ceiling="high")
    assert view.binding_factors, "fixture sanity: the assessment must record binding factors"
    for factor, joined in zip(assessment.binding_factors, view.binding_factors, strict=True):
        assert joined == f"{factor.name}: {factor.reason}"


def test_no_viable_configuration_verdict_renders_without_an_em_dash(rich_quote) -> None:
    """The refusal verdict and the unsolved-dispatch banner: copy no priced record reaches."""
    result = rich_quote.result.model_copy(
        update={
            "selection_status": s.SelectionStatus.NO_VIABLE_CONFIGURATION,
            "solver_status": s.SolverStatus.INSUFFICIENT_DATA,
        }
    )
    quote = rich_quote.quote.model_copy(update={"optimisation_result": result, "warnings": []})
    html = render_report_for_quote(quote, rich_quote.site)
    arts = generate_artifacts(quote, rich_quote.site)
    _assert_clean(
        consulting_report=html,
        memo=arts.memo_markdown,
        proposal=arts.proposal_markdown,
        report_html=arts.report_html,
    )


# --- the branches no single render reaches: sweep the literals ------------------------------


def _rendered_literals(path: Path) -> list[tuple[int, str]]:
    """Every string literal in ``path`` that is not a docstring, with its line number.

    Docstrings are module/class/function prose: never rendered, and excluded on purpose (the
    app-row sweep excludes Python docstrings for the same reason). Comments never reach a
    literal, so the AST walk excludes them for free.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_output_module_carries_an_em_dash_in_rendered_copy() -> None:
    """Covers the copy the fixtures above cannot reach (every conditional branch and label)."""
    package = Path(outputs_pkg.__file__).parent
    modules = sorted(package.glob("*.py"))
    assert len(modules) > 5, f"expected the outputs package to be swept, found {modules}"

    offenders = [
        f"{path.name}:{line}: {text.strip()[:110]}"
        for path in modules
        for line, text in _rendered_literals(path)
        if EM_DASH in text
    ]
    assert not offenders, "em dash in a rendered string literal:\n" + "\n".join(offenders)
