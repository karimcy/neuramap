"""Conformance suite for the `OptimisationResult` contract (issue 014).

This is the executable bar that proves Olly's real MILP can drop in behind the same seam as the
integration-only `BaselineEngine` with **no silent drift**. It exercises the reusable harness in
`gridq.engine.conformance` against:

1. the **placeholder** engine (`BaselineEngine`) — which must **pass** the whole suite;
2. deliberately **non-conforming stubs** — each constructs a *schema-valid* `OptimisationResult`
   that nonetheless breaks one contract invariant Pydantic does not enforce, proving the suite has
   teeth; and
3. the real downstream **seam** (`quotes.pipeline.generate_quote`) — proving any conforming engine's
   output flows through finance → quote with no code change and stays honestly not-bankable on the
   placeholder assumption library.

When Olly lands `gridq.optimisation.MilpEngine`, he appends it to `ENGINES` (and/or calls
`assert_conformant(MilpEngine(), assumptions=...)` from his own lane) — the same checks run against
it unchanged. M8 (outputs) is not yet built, so the outputs half of the M5/M8 seam joins here when
that lane lands; it is intentionally noted, not silently skipped.
"""

from __future__ import annotations

import pytest

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.engine import BaselineEngine, OptimisationEngine
from gridq.engine.conformance import (
    ConformanceError,
    assert_conformant,
    canonical_conformance_sites,
    check_engine_conformance,
    check_result_conformance,
)
from gridq.optimisation import MilpEngine
from gridq.quotes.pipeline import generate_quote
from gridq.schemas import (
    BankableStatus,
    Firmness,
    ImplementationLevel,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    SiteModel,
    SolverMetadata,
    SolverStatus,
    ValueStreamResult,
)

# Engines under test: the integration-only placeholder and the real production MILP engine (W4-1).
# Both must satisfy the same OptimisationResult contract with no silent drift.
ENGINES = [
    pytest.param(BaselineEngine(), id="baseline"),
    pytest.param(MilpEngine(), id="milp"),
]


@pytest.fixture(scope="module")
def assumptions() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


# --------------------------------------------------------------------------------------------------
# 1. The placeholder engine passes the whole conformance suite.
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_engine_conforms(engine: OptimisationEngine, assumptions: AssumptionLibrary) -> None:
    """Every registered engine satisfies the OptimisationResult contract + stream registry."""
    report = check_engine_conformance(engine, assumptions=assumptions)
    assert report.ok, report.summary()
    # assert_conformant is the one-liner Olly runs in his own lane; it must not raise here.
    assert_conformant(engine, assumptions=assumptions)


def test_canonical_sites_are_valid_and_cover_both_branches(assumptions: AssumptionLibrary) -> None:
    """The shared fixtures are valid SiteModels and exercise the spec / no-spec paths."""
    sites = canonical_conformance_sites()
    assert [label for label, _ in sites] == ["t2_full", "t1_partial", "t0_no_import"]
    engine = BaselineEngine()
    statuses = {
        label: engine.solve(engine.build_optimisation_request(site, assumptions)).solver_status
        for label, site in sites
    }
    # A site with max_import_kw sizes a baseline spec; the annual-only site degrades honestly.
    assert statuses["t2_full"] == SolverStatus.BASELINE
    assert statuses["t0_no_import"] == SolverStatus.INSUFFICIENT_DATA


# --------------------------------------------------------------------------------------------------
# 2. Deliberately non-conforming stubs FAIL the suite (teeth Pydantic does not provide).
# --------------------------------------------------------------------------------------------------


def _request(site: SiteModel, assumptions: AssumptionLibrary) -> OptimisationRequest:
    return OptimisationRequest(
        request_id=f"req_{site.site_id}",
        site_model=site,
        assumption_version=assumptions.metadata.version,
    )


def _metadata() -> SolverMetadata:
    return SolverMetadata(model_version="stub", assumption_version="gb_v0_1", code_version="test")


class _NonDeterministicStub:
    """Production-level engine whose sizing changes every solve — must fail `determinism`."""

    def __init__(self) -> None:
        self._n = 0

    def build_optimisation_request(
        self, site_model: SiteModel, assumptions: AssumptionLibrary, scenario_set: str | None = None
    ) -> OptimisationRequest:
        return _request(site_model, assumptions)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        self._n += 1
        power = 100.0 + self._n  # different every call
        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            selected_power_kw=power,
            selected_energy_kwh=power * 2,
            duration_hours=2.0,
            solver_status=SolverStatus.OPTIMAL,
            solver_metadata=_metadata(),
        )


class _GatedInFloorStub:
    """Prices a gated-upside stream as `contracted` in the floor — schema-valid, but must fail
    `firmness.gated_excluded_from_priced_floor` (Pydantic only bars `firm` for gated streams)."""

    def build_optimisation_request(
        self, site_model: SiteModel, assumptions: AssumptionLibrary, scenario_set: str | None = None
    ) -> OptimisationRequest:
        return _request(site_model, assumptions)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.REVIEW_REQUIRED,
            selected_power_kw=42.0,
            selected_energy_kwh=84.0,
            duration_hours=2.0,
            value_streams=[
                ValueStreamResult(
                    stream=MarketStream.WHOLESALE_P415,  # a gated-upside stream...
                    annual_revenue_gbp=50_000.0,
                    firmness=Firmness.CONTRACTED,  # ...sneaked into the priced floor.
                )
            ],
            solver_status=SolverStatus.OPTIMAL,
            solver_metadata=_metadata(),
        )


class _MissingUnlockStub:
    """A gated_upside entry with no unlock_condition — schema-valid, but must fail
    `firmness.gated_upside_states_unlock` (a gated stream you cannot price must say what unlocks
    it)."""

    def build_optimisation_request(
        self, site_model: SiteModel, assumptions: AssumptionLibrary, scenario_set: str | None = None
    ) -> OptimisationRequest:
        return _request(site_model, assumptions)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            selected_power_kw=42.0,
            selected_energy_kwh=84.0,
            duration_hours=2.0,
            gated_upside=[
                ValueStreamResult(
                    stream=MarketStream.BALANCING_MECHANISM,
                    firmness=Firmness.CONDITIONAL,
                    unlock_condition=None,  # no stated unlock
                )
            ],
            solver_status=SolverStatus.OPTIMAL,
            solver_metadata=_metadata(),
        )


class _BaselineClaimsReviewStub:
    """A baseline-level result claiming `review_required` — schema-valid (Pydantic bars only
    baseline+`bankable_candidate`), but must fail `bankability.baseline_not_bankable`."""

    def build_optimisation_request(
        self, site_model: SiteModel, assumptions: AssumptionLibrary, scenario_set: str | None = None
    ) -> OptimisationRequest:
        return _request(site_model, assumptions)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        return OptimisationResult(
            result_id=f"res_{request.request_id}",
            request_id=request.request_id,
            implementation_level=ImplementationLevel.BASELINE,
            bankable_status=BankableStatus.REVIEW_REQUIRED,  # baseline must stay not_bankable
            selected_power_kw=42.0,
            selected_energy_kwh=84.0,
            duration_hours=2.0,
            solver_status=SolverStatus.BASELINE,
            solver_metadata=_metadata(),
        )


class _WrongEchoStub:
    """A result whose request_id does not echo the request — must fail `result.echoes_request`
    (finance/quote correlate result->request by id)."""

    def build_optimisation_request(
        self, site_model: SiteModel, assumptions: AssumptionLibrary, scenario_set: str | None = None
    ) -> OptimisationRequest:
        return _request(site_model, assumptions)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        return OptimisationResult(
            result_id="res_detached",
            request_id="req_not_the_one_we_were_given",  # does not echo request.request_id
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            selected_power_kw=42.0,
            selected_energy_kwh=84.0,
            duration_hours=2.0,
            solver_status=SolverStatus.OPTIMAL,
            solver_metadata=_metadata(),
        )


@pytest.mark.parametrize(
    ("stub", "expected_failure"),
    [
        pytest.param(_NonDeterministicStub(), "determinism", id="non_deterministic"),
        pytest.param(
            _GatedInFloorStub(),
            "firmness.gated_excluded_from_priced_floor",
            id="gated_in_priced_floor",
        ),
        pytest.param(
            _MissingUnlockStub(),
            "firmness.gated_upside_states_unlock",
            id="gated_without_unlock",
        ),
        pytest.param(
            _BaselineClaimsReviewStub(),
            "bankability.baseline_not_bankable",
            id="baseline_claims_bankable",
        ),
        pytest.param(_WrongEchoStub(), "result.echoes_request", id="wrong_request_echo"),
    ],
)
def test_nonconforming_stub_is_rejected(
    stub: OptimisationEngine, expected_failure: str, assumptions: AssumptionLibrary
) -> None:
    """Each stub passes Pydantic construction but trips exactly the conformance check it breaks."""
    report = check_engine_conformance(stub, assumptions=assumptions)
    assert not report.ok, "a non-conforming stub must fail the suite"
    failed_checks = {r.check for r in report.failures}
    # The check name appears (suffixed with the canonical-site context, e.g. "[t2_full] ...").
    assert any(c.endswith(expected_failure) for c in failed_checks), (
        f"expected a failure ending in {expected_failure!r}; got {sorted(failed_checks)}"
    )
    # And the assert wrapper raises, surfacing the failure detail.
    with pytest.raises(ConformanceError):
        assert_conformant(stub, assumptions=assumptions)


def test_conforming_engine_has_no_failures_a_stub_would_share(
    assumptions: AssumptionLibrary,
) -> None:
    """Guard against a vacuous suite: the baseline must NOT trip the teeth checks the stubs do."""
    report = check_engine_conformance(BaselineEngine(), assumptions=assumptions)
    failed = {r.check for r in report.failures}
    assert failed == set()


def test_consented_gated_stream_may_sit_in_the_priced_floor() -> None:
    """Revenue-Data Drive 2b / D43 placement: a CONSENTED CONTRACTED gated stream in value_streams
    is conformant (it carries a ``consent_status`` basis), and the SAME stream may also carry its
    separate indicative CONDITIONAL entry in gated_upside (wholesale's dual contracted/MID mode)."""
    result = OptimisationResult(
        result_id="res_consent",
        request_id="req_consent",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            ValueStreamResult(
                stream=MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=40_000.0,
                firmness=Firmness.FIRM,
            ),
            ValueStreamResult(
                stream=MarketStream.WHOLESALE_P415,  # a gated stream...
                annual_revenue_gbp=12_000.0,
                firmness=Firmness.CONTRACTED,  # ...priced into the floor as CONTRACTED...
                consent_status="given",  # ...on an explicit consent basis.
            ),
        ],
        gated_upside=[
            ValueStreamResult(
                stream=MarketStream.WHOLESALE_P415,
                annual_revenue_gbp=3_000.0,
                firmness=Firmness.CONDITIONAL,
                consent_status="given",
                unlock_condition="indicative Elexon-MID perfect-foresight delta (never contracted)",
            ),
        ],
        solver_status=SolverStatus.OPTIMAL,
        solver_metadata=_metadata(),
    )
    failures = [r for r in check_result_conformance(result) if not r.passed]
    assert not failures, [r.check for r in failures]


def test_unconsented_gated_stream_in_floor_is_still_rejected() -> None:
    """The teeth stay: an UNCONSENTED gated stream in the priced floor still fails (no consent
    basis) — only an explicit consent relaxes the D43 placement bar."""
    result = OptimisationResult(
        result_id="res_leak",
        request_id="req_leak",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            ValueStreamResult(
                stream=MarketStream.WHOLESALE_P415,
                annual_revenue_gbp=12_000.0,
                firmness=Firmness.CONTRACTED,  # no consent_status → still a leak
            ),
        ],
        solver_status=SolverStatus.OPTIMAL,
        solver_metadata=_metadata(),
    )
    failures = {r.check for r in check_result_conformance(result) if not r.passed}
    assert "firmness.gated_excluded_from_priced_floor" in failures


# --------------------------------------------------------------------------------------------------
# 3. The downstream seam consumes any conforming engine with no code change (M5; M8 to follow).
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_pipeline_consumes_any_conforming_engine(
    engine: OptimisationEngine, assumptions: AssumptionLibrary
) -> None:
    """`generate_quote` (site -> engine -> finance -> quote) runs for any conforming engine.

    Proves the issue-014 M5 half: finance/quote consume the engine output with no code change. On
    the all-placeholder `gb_v0_1` library the quote stays honestly **not bankable**, and the
    engine's embedded `OptimisationResult` still satisfies the contract after the pipeline.
    """
    for _label, site in canonical_conformance_sites():
        run = generate_quote(site, assumptions, engine=engine)
        assert run.bankable is False  # placeholder assumptions never underwrite as bankable
        # The embedded result is the engine's own output and must still conform.
        result_failures = [r for r in check_result_conformance(run.result) if not r.passed]
        assert not result_failures, [r.check for r in result_failures]
