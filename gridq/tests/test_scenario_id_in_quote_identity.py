"""The scenario a quote was priced under is an INPUT, and pins the quote's identity directly.

Commit 9c07c5b took ``financial_case.case_id`` out of the quote's canonical hash input, correctly:
``case_id`` is ``fc_`` + the finance ``run_id``, which binds ``code_version``, so hashing it moved
``input_hash`` on every commit. But ``case_id`` was also the ONLY route by which the scenario
reached the quote hash, and the commit accepted the resulting collapse as a known residual: two
runs whose scenarios differ in **name alone** now produce one ``input_hash`` and one ``quote_id``.

That collapse is not hypothetical. `quotes.sensitivities` documents that the shipped
Low/Central/High set defaults every unsupplied band to an identity multiplier of 1.0, so the three
standard scenarios differ in ``scenario_id`` and in nothing that touches a cashflow — which is
exactly the case that collapses.

**Why threading the id directly is the same rule, not an exception to it.** `_canonical_run_input`
states the principle: a derived identity must never feed a computed identity. ``case_id`` is
*minted by* a run and re-imports every pin its parent `RunContext` carried; ``scenario_id`` is
*declared by the caller before* the run, like ``assumption_version``. One is an output wearing an
id's clothes, the other is an input.

**Read the wiring caveat below before treating any of this as enforced.** These tests assert at the
`assemble_quote_record` layer — the layer a reader of a ``quote_id`` reads — but that function's one
production caller does not yet pass a scenario, and cannot: `generate_quote` has no ``scenario``
parameter. `test_the_scenario_seam_stays_honest_about_being_unwired` is the tripwire that fails the
moment someone adds one without completing the thread.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.finance.financial_model import _hash_input, run_financial_model
from gridq.provenance import build_run_context
from gridq.provenance.hashing import hash_json
from gridq.quotes import assemble_quote_record, generate_quote
from gridq.quotes.assembly import (
    _canonical_financial_case_dump,
    _canonical_result_dump,
    _canonical_run_input,
    _canonical_site_dump,
)
from gridq.scenarios import Scenario, ScenarioName
from gridq.schemas import SCHEMA_VERSION

ASSUMPTION_VERSION = "gb_v0_1"


def _scenario(scenario_id: str) -> Scenario:
    """A scenario that changes NO number: every multiplier is the shipped identity default.

    This is the whole point. A scenario that moves a multiplier moves the cashflows, which moves
    `FinancialCase`, which already moves the hash — there was never a defect there. The collapse
    lives precisely where the scenario is a *label*, and `scenarios.models.Scenario` defaults
    ``price_multiplier``/``cost_multiplier`` to 1.0 and ``cod_delay_months`` to 0, so the shipped
    Low/Central/High set is three labels over one arithmetic unless the library populates bands.
    """
    return Scenario(scenario_id=scenario_id, name=ScenarioName.CUSTOM, probability_weight=1.0)


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


@pytest.fixture
def two_named_runs(site: s.SiteModel, result: s.OptimisationResult, assumptions: Any) -> tuple:
    """The same priced run under two scenarios that differ only in ``scenario_id``."""
    low = run_financial_model(site, result, assumptions, scenario=_scenario("low"))
    high = run_financial_model(site, result, assumptions, scenario=_scenario("high"))
    return low, high


# --- the defect is real, and it is real at the layer a reader reads ----------------------------


def test_two_scenarios_differing_only_in_name_compute_an_identical_financial_case(
    two_named_runs: tuple,
) -> None:
    """Precondition, proven rather than asserted: nothing the case COMPUTED separates the two.

    Mirrors the same precondition in `test_input_hash_excludes_code_version` for the ``case_id``
    route. If this ever fails, the two scenarios are no longer identity-multiplier cases and every
    test below is testing something weaker than it claims.
    """
    low, high = two_named_runs
    assert low.case.case_id != high.case.case_id, (
        "precondition: the finance hash already pins scenario_id (financial_model._hash_input), "
        "so the two cases must carry different case_ids"
    )
    assert low.case.model_dump(mode="json", exclude={"case_id"}) == high.case.model_dump(
        mode="json", exclude={"case_id"}
    ), "precondition: identity multipliers must move no number — only the derived identity"


def test_the_financial_case_alone_cannot_tell_a_reader_which_scenario_ran(
    two_named_runs: tuple,
) -> None:
    """Why the seam into `finance/` had to widen: the id is unrecoverable downstream.

    `assemble_quote_record` receives a `FinancialCase`, not the scenario. The canonical dump of
    that case — with ``case_id`` correctly excluded — is byte-identical for the two runs, so no
    amount of cleverness inside `quotes/` can recover the scenario. That is the entire reason
    `FinancialModelResult` now carries ``scenario_id``: it is the only honest route.
    """
    low, high = two_named_runs
    assert _canonical_financial_case_dump(low.case) == _canonical_financial_case_dump(high.case)


def test_an_unthreaded_quote_collapses_the_two_scenarios_onto_one_id(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any, two_named_runs: tuple
) -> None:
    """The accepted residual of 9c07c5b, reproduced at the reader's layer.

    Not a test of the fix — a test that the thing being fixed exists. Assemble both quotes the way
    a caller that knows nothing about scenarios assembles them, and the two named runs come back
    wearing one ``quote_id``.
    """
    low, high = two_named_runs
    quote_low = assemble_quote_record(site, result, low.case, assumptions)
    quote_high = assemble_quote_record(site, result, high.case, assumptions)
    assert quote_low.input_hash == quote_high.input_hash
    assert quote_low.quote_id == quote_high.quote_id


def test_threading_the_scenario_id_splits_the_quote_identity(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any, two_named_runs: tuple
) -> None:
    """THE acceptance property, asserted on `QuoteRecord` fields rather than on a private dump.

    A reader of a quote reads ``quote_id`` and ``input_hash``; asserting on `_canonical_run_input`
    alone would pass just as happily with `assemble_quote_record` never forwarding the argument.
    """
    low, high = two_named_runs
    quote_low = assemble_quote_record(
        site, result, low.case, assumptions, scenario_id=low.scenario_id
    )
    quote_high = assemble_quote_record(
        site, result, high.case, assumptions, scenario_id=high.scenario_id
    )
    assert (low.scenario_id, high.scenario_id) == ("low", "high")
    assert quote_low.input_hash != quote_high.input_hash, (
        "two named scenarios shared an input_hash. assemble_quote_record must forward scenario_id "
        "into _canonical_run_input; see the docstring there for why it enters directly rather than "
        "through the finance-minted case_id."
    )
    assert quote_low.quote_id != quote_high.quote_id


def test_the_split_survives_a_financial_case_being_absent(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """A degraded run (finance refused, ``financial_case=None``) still keeps its scenarios apart.

    The old route went through the case, so it vanished entirely on the very runs whose identity is
    least legible. The direct thread does not depend on finance having produced anything.
    """
    a = assemble_quote_record(site, result, None, assumptions, scenario_id="low")
    b = assemble_quote_record(site, result, None, assumptions, scenario_id="high")
    assert a.quote_id != b.quote_id


# --- and every run WITHOUT a scenario keeps the identity it already had -------------------------


def _pre_change_canonical(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> dict[str, object]:
    """The canonical run input EXACTLY as it was shaped before ``scenario_id`` existed.

    Reconstructed LITERALLY, not by calling the implementation with a default argument: a
    compare-against-implementation test passes even when the implementation has silently changed
    shape, which is the failure mode this technique exists to rule out. Copied in spirit from
    `tests/test_study_basis_hash_identity._pre_d46_canonical`, which pins the same property for
    the D46 study basis. Update it BY HAND, and only when the canonical shape is deliberately
    re-minted.
    """
    return {
        "site": _canonical_site_dump(site),
        "result": _canonical_result_dump(result),
        "financial_case": None,
        "assumption_version": assumptions.metadata.version,
        "forecast_content_hash": None,
    }


def test_a_run_with_no_scenario_is_byte_identical_to_the_pre_change_shape(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """Proof, not assertion: no existing ``input_hash``/``quote_id``/``run_id`` moves."""
    canonical = _canonical_run_input(site, result, None, assumptions, None)
    assert canonical == _pre_change_canonical(site, result, assumptions)
    assert hash_json(canonical) == hash_json(_pre_change_canonical(site, result, assumptions))
    before = build_run_context(
        _pre_change_canonical(site, result, assumptions),
        assumptions.metadata.version,
        SCHEMA_VERSION,
    )
    after = build_run_context(canonical, assumptions.metadata.version, SCHEMA_VERSION)
    assert before.input_hash == after.input_hash
    assert before.run_id == after.run_id


def test_the_absent_key_is_popped_and_never_present_as_null(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """C033/D42 pop-if-None, stated explicitly because ``"scenario_id": None`` also hashes.

    A present-and-null key is not a smaller version of the same thing: it is a different canonical
    document and would re-mint every historical identity for no change in what was priced.
    """
    canonical = _canonical_run_input(site, result, None, assumptions, None)
    assert "scenario_id" not in canonical


def test_passing_none_explicitly_is_identical_to_omitting_it(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    omitted = _canonical_run_input(site, result, None, assumptions, None)
    explicit = _canonical_run_input(site, result, None, assumptions, None, None, None)
    assert omitted == explicit


def test_assemble_quote_record_without_a_scenario_is_the_unchanged_identity(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """The same byte-stability claim at the reader's layer, since that is where it is relied on."""
    quote = assemble_quote_record(site, result, None, assumptions)
    expected = build_run_context(
        _pre_change_canonical(site, result, assumptions),
        assumptions.metadata.version,
        SCHEMA_VERSION,
    )
    assert quote.input_hash == expected.input_hash
    assert quote.quote_id == f"quote_{expected.run_id}"


# --- the two ids the seam exposes are one value ------------------------------------------------


def test_the_finance_result_reports_the_same_id_its_own_hash_was_pinned_by(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """`FinancialModelResult.scenario_id` and `_hash_input`'s ``scenario_id`` read one helper.

    If they could diverge, the finance identity and the quote identity would pin different
    scenarios while both claiming to pin "the" scenario — an identity that lies quietly.
    """
    scenario = _scenario("central")
    model = run_financial_model(site, result, assumptions, scenario=scenario)
    payload = _hash_input(site, result, assumptions, None, scenario, None)
    assert model.scenario_id == payload["scenario_id"] == "central"


def test_a_run_with_no_scenario_reports_no_id(
    site: s.SiteModel, result: s.OptimisationResult, assumptions: Any
) -> None:
    """Absent stays absent — never an invented default, never an empty string."""
    assert run_financial_model(site, result, assumptions).scenario_id is None


# --- the honest caveat, enforced -----------------------------------------------------------------


def test_the_scenario_seam_stays_honest_about_being_unwired() -> None:
    """A tripwire, because this repo has shipped built-tested-never-called twelve times.

    Today `generate_quote` — the sole production caller of `assemble_quote_record` — takes no
    ``scenario``, so nothing above is enforced on a real run and this file must not be read as
    saying otherwise. The guard is the JOINT condition: the day someone gives `generate_quote` a
    scenario to price, this fails unless they also forward the id, which is the exact step that
    was missed eleven of the twelve previous times.
    """
    takes_a_scenario = "scenario" in inspect.signature(generate_quote).parameters
    forwards_the_id = "scenario_id=" in inspect.getsource(generate_quote)
    assert takes_a_scenario == forwards_the_id, (
        "generate_quote now prices a scenario but does not forward its id to "
        "assemble_quote_record (or vice versa). Pass scenario_id=model.scenario_id from the "
        "run_financial_model result, then update this test's docstring: the seam is wired."
    )
