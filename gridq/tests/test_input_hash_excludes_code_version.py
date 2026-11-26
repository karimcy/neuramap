"""`input_hash` pins the INPUTS — never the code version, directly or by derived identity.

``run_id`` derives from ``(input_hash, assumption_version, code_version, schema_version)`` precisely
so those four pins are **independent**. ``input_hash`` exists to answer exactly one question — *did
the inputs change?* — and an ``input_hash`` that already contains ``code_version`` cannot answer it:
"same inputs, new code" and "the inputs changed" become the same observation. Before this was fixed,
re-publishing six real sites moved ``input_hash`` on all six while a full recursive diff of the old
and new ``quote.json`` showed the records identical apart from identity fields.

Two independent routes carried the git commit into the hash, and **both** had to close:

1. ``result.solver_metadata.code_version`` — read from git HEAD on every call
   (`MilpEngine._metadata`).
2. ``financial_case.case_id`` — ``fc_`` + the finance `RunContext`'s ``run_id``, minted in
   `finance.financial_model.run_financial_model`, which binds ``code_version``. This is the route
   that bit in production, because every real run carries a `FinancialCase`. Closing route 1 alone
   have left ``input_hash`` moving on every commit exactly as before — a fix that passes its own
   test and changes nothing on the production path.

Asserted through `assemble_quote_record`, the production entry point (called by
`quotes.pipeline.generate_quote`, reached from `cli.py`), not through the private dump helpers — so
deleting the wiring fails the test rather than leaving it green (validation_requirements.md §3,
clause 2).

The negative controls matter as much as the positive one. ``seed`` and ``model_version`` are genuine
run inputs and **must** still split the hash; they pin the field-level exclusion against a future
"simplification" that drops `solver_metadata` wholesale and silently makes two different solves
share an identity.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import gridq.provenance.run_context as run_context_module
import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.finance.financial_model import run_financial_model
from gridq.provenance.hashing import hash_json
from gridq.quotes import assemble_quote_record
from gridq.quotes.assembly import _canonical_result_dump, _canonical_run_input

ASSUMPTION_VERSION = "gb_v0_1"

_CODE_A = "git:aaaaaaaaaaaa"
_CODE_B = "git:bbbbbbbbbbbb"


@pytest.fixture
def assumptions() -> Any:
    return load_assumptions(ASSUMPTION_VERSION)


@pytest.fixture
def site(make_site: Callable[..., s.SiteModel]) -> s.SiteModel:
    return make_site()


@pytest.fixture
def result(site: s.SiteModel, assumptions: Any) -> s.OptimisationResult:
    engine = BaselineEngine()
    return engine.solve(engine.build_optimisation_request(site, assumptions))


def _with_metadata(result: s.OptimisationResult, **fields: Any) -> s.OptimisationResult:
    """A copy of ``result`` with `SolverMetadata` fields overridden."""
    assert result.solver_metadata is not None, "fixture must stamp solver metadata"
    return result.model_copy(
        update={"solver_metadata": result.solver_metadata.model_copy(update=fields)}
    )


def _quote_at_code_version(
    monkeypatch: pytest.MonkeyPatch,
    code_version: str,
    site: s.SiteModel,
    result: s.OptimisationResult,
    assumptions: Any,
) -> s.QuoteRecord:
    """Assemble a full quote as if the repo were sitting at ``code_version``.

    One monkeypatch seam covers both routes: `build_run_context` reads
    ``run_context.get_code_version`` for the quote's own stamp *and* the finance model calls the
    same function when it mints ``case_id``. The engine-side ``solver_metadata.code_version`` is set
    by copy rather than by patching the engine module, so this touches no engine internals.
    """
    monkeypatch.setattr(run_context_module, "get_code_version", lambda: code_version)
    case = run_financial_model(site, result, assumptions).case
    return assemble_quote_record(
        site, _with_metadata(result, code_version=code_version), case, assumptions
    )


def test_identical_inputs_at_different_code_versions_share_an_input_hash(
    monkeypatch: pytest.MonkeyPatch,
    site: s.SiteModel,
    result: s.OptimisationResult,
    assumptions: Any,
) -> None:
    """THE acceptance property: only the code version moved, so only code-keyed fields may move."""
    quote_a = _quote_at_code_version(monkeypatch, _CODE_A, site, result, assumptions)
    quote_b = _quote_at_code_version(monkeypatch, _CODE_B, site, result, assumptions)

    assert quote_a.code_version == _CODE_A
    assert quote_b.code_version == _CODE_B
    assert quote_a.input_hash == quote_b.input_hash, (
        "input_hash moved on a code-version change alone. It pins the INPUTS; code identity has "
        "its own pin in run_id. Check that quotes.assembly still excludes "
        "solver_metadata.code_version AND financial_case.case_id from the canonical run input."
    )
    # Not over-stripped: the identity that is SUPPOSED to bind code_version still does, so the two
    # runs stay distinguishable. An input_hash that matched *and* a quote_id that matched would mean
    # the fix had erased the code pin instead of relocating it.
    assert quote_a.quote_id != quote_b.quote_id


def test_case_id_route_alone_would_move_the_hash(
    monkeypatch: pytest.MonkeyPatch,
    site: s.SiteModel,
    result: s.OptimisationResult,
    assumptions: Any,
) -> None:
    """Route 2 in isolation: the `FinancialCase` really does carry the commit, and is excluded.

    Holds ``solver_metadata.code_version`` fixed so the ONLY difference between the two canonical
    inputs is the finance-minted ``case_id``. This fails if `_canonical_financial_case_dump` stops
    excluding ``case_id`` — which is the half of the fix that a solver_metadata-only reading of the
    defect would have missed.
    """
    monkeypatch.setattr(run_context_module, "get_code_version", lambda: _CODE_A)
    case_a = run_financial_model(site, result, assumptions).case
    monkeypatch.setattr(run_context_module, "get_code_version", lambda: _CODE_B)
    case_b = run_financial_model(site, result, assumptions).case

    assert case_a.case_id != case_b.case_id, "precondition: case_id must bind code_version"
    assert case_a.model_dump(mode="json", exclude={"case_id"}) == case_b.model_dump(
        mode="json", exclude={"case_id"}
    ), "precondition: nothing the case COMPUTED may differ — only its derived identity"

    hash_a = hash_json(_canonical_run_input(site, result, case_a, assumptions, None))
    hash_b = hash_json(_canonical_run_input(site, result, case_b, assumptions, None))
    assert hash_a == hash_b


def test_solve_seconds_and_reproduction_env_never_reach_the_hash(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """Wall-clock timing and the host environment are measurements of a run, not inputs to one."""
    bare = hash_json(_canonical_run_input(site, result, None, assumptions, None))
    timed = _with_metadata(result, solve_seconds=1.23, reproduction_env="py3.12-linux-x86_64")
    assert hash_json(_canonical_run_input(site, timed, None, assumptions, None)) == bare


@pytest.mark.parametrize(
    ("field", "value_a", "value_b"),
    [("seed", 0, 1), ("model_version", "milp_v1", "milp_v2"), ("assumption_version", "x", "y")],
)
def test_genuine_solver_inputs_still_split_the_hash(
    field: str,
    value_a: object,
    value_b: object,
    site: s.SiteModel,
    result: s.OptimisationResult,
    assumptions: Any,
) -> None:
    """Negative control: the exclusion is FIELD-LEVEL, never the whole `solver_metadata` block.

    A wholesale exclusion would let two runs on different seeds — or on two different MILP
    formulations — share one identity, which is a worse defect than the one being fixed: a fixed
    identity over a moved number (cf. N25/MD-F-14).
    """
    hash_a = hash_json(
        _canonical_run_input(
            site, _with_metadata(result, **{field: value_a}), None, assumptions, None
        )
    )
    hash_b = hash_json(
        _canonical_run_input(
            site, _with_metadata(result, **{field: value_b}), None, assumptions, None
        )
    )
    assert hash_a != hash_b


def test_canonical_result_dump_keeps_inputs_and_drops_metadata(
    result: s.OptimisationResult,
) -> None:
    """The shape, stated once explicitly so the field-level split is readable, not inferable."""
    meta = _canonical_result_dump(result)["solver_metadata"]
    assert isinstance(meta, dict)
    assert set(meta) == {"schema_version", "seed", "assumption_version", "model_version"}


def test_dump_helpers_tolerate_an_absent_solver_metadata(result: s.OptimisationResult) -> None:
    """A nested exclude must not blow up on the `solver_metadata=None` results the schema allows."""
    assert (
        _canonical_result_dump(result.model_copy(update={"solver_metadata": None}))[
            "solver_metadata"
        ]
        is None
    )
