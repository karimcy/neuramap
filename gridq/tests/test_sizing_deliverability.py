"""Tests for the closed-form end-of-tenor deliverability check (W7-3).

The M4 acceptance criterion the review found dormant now fires for real: with a tenored
commitment on the seam, at least one sizing candidate is REJECTED for late-life capability
shortfall — computed (power × derate × retention vs committed kW), never asserted. Verdicts are
three-state honest: None without commitments or usable fade (data-ask), False on any known
failure, True only when every tenored commitment passes.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import time

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem, DispatchResult
from gridq.finance.debt import DebtTerms
from gridq.sizing import SearchSpace, SizingObjective, enumerate_sizing
from gridq.sizing.deliverability import assess_end_of_life_deliverability
from gridq.value_stack.interface import DispatchConstraintDescriptor

FADE_PCT = 2.5  # gb_v0_1 degradation.annual_capacity_fade_pct (placeholder — arithmetic input)
DERATE_2H = 0.65  # gb_v0_1 capacity_market.de_rating_factor_by_duration_h['2']
RETENTION_15Y = (1 - FADE_PCT / 100.0) ** 15  # ≈ 0.68396


def _commitment(
    *,
    stream: s.MarketStream = s.MarketStream.CAPACITY_MARKET,
    committed_kw: float,
    held_kwh: float | None = None,
    tenor: float | None = 15.0,
    reference: str | None = "CM-TEST/AGR-1",
) -> s.AvailabilityCommitment:
    return s.AvailabilityCommitment(
        commitment_id="eol_test",
        stream=stream,
        committed_power_kw=committed_kw,
        held_energy_kwh=held_kwh,
        windows=[
            s.AvailabilityWindow(
                days_of_week=[0, 1, 2, 3, 4], start_time=time(16), end_time=time(19)
            )
        ],
        tenor_years=tenor,
        reference=reference,
    )


def _seam(commitment: s.AvailabilityCommitment) -> list[DispatchConstraintDescriptor]:
    return [
        DispatchConstraintDescriptor(
            constraint_id="eol_test_reserve",
            description="test commitment",
            kind="availability_window",
            parameters=commitment,
        )
    ]


def _assess(commitment: s.AvailabilityCommitment | None, *, lib=None):
    return assess_end_of_life_deliverability(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        constraints=_seam(commitment) if commitment is not None else [],
        assumptions=lib if lib is not None else load_assumptions("gb_v0_1"),
    )


def test_no_tenored_commitment_stays_a_data_ask() -> None:
    verdict, notes = _assess(None)
    assert verdict is None and notes == []
    verdict, _ = _assess(_commitment(committed_kw=50.0, tenor=None))
    assert verdict is None  # a commitment without a tenor cannot be assessed — never assumed


def test_cm_commitment_fails_at_end_of_tenor_when_degraded_capability_falls_short() -> None:
    # capability = 200 × 0.65 × 0.975^15 ≈ 88.9 kW < 150 kW committed → NOT deliverable.
    verdict, notes = _assess(_commitment(committed_kw=150.0))
    assert verdict is False
    assert any("NOT deliverable" in n for n in notes)
    expected = 200.0 * DERATE_2H * RETENTION_15Y
    assert any(f"{expected:.1f} kW" in n for n in notes)  # the working is shown, not asserted


def test_cm_commitment_passes_when_degraded_capability_suffices() -> None:
    verdict, notes = _assess(_commitment(committed_kw=50.0))  # 88.9 ≥ 50
    assert verdict is True
    assert any("status 'placeholder'" in n for n in notes)  # arithmetic labelled, not evidence
    assert any("W7-4" in n for n in notes)


def test_held_energy_shortfall_fails_even_when_power_passes() -> None:
    # energy at end-of-tenor = 400 × 0.68396 ≈ 273.6 kWh < 300 kWh held → NOT deliverable.
    verdict, _ = _assess(_commitment(committed_kw=50.0, held_kwh=300.0))
    assert verdict is False


def test_non_cm_commitment_uses_no_derating() -> None:
    # Flex commitments are plain kW: 200 × 0.68396 ≈ 136.8 ≥ 120 → deliverable.
    verdict, _ = _assess(_commitment(stream=s.MarketStream.LOCAL_FLEX_PICLO, committed_kw=120.0))
    assert verdict is True


def test_unusable_fade_is_a_data_ask_never_a_pass() -> None:
    lib = copy.deepcopy(load_assumptions("gb_v0_1"))
    lib.degradation["annual_capacity_fade_pct"].value = None
    verdict, notes = _assess(_commitment(committed_kw=50.0), lib=lib)
    assert verdict is None
    assert any("never a fabricated pass" in n for n in notes)


def _produce(commitment: s.AvailabilityCommitment | None, *, lib=None):
    from gridq.sizing.deliverability import produce_gate_context

    return produce_gate_context(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        constraints=_seam(commitment) if commitment is not None else [],
        assumptions=lib if lib is not None else load_assumptions("gb_v0_1"),
    )


#: WP-CORE-01 triage (2026-11-16). D57 item 1 (7405d4b, 2026-08-18; D57 limb 2 in
#: docs/decisions.md: "aggregation ... ASSUMED APPROVED") made `produce_gate_context` ALWAYS
#: return a context carrying the assumed portfolio aggregate, and ALWAYS emit the one D57
#: statement of that assumption. The three expectations below that read `None` / `[]` predate
#: it. The aggregate is the largest sourced minimum unit size (`d57_portfolio_aggregate_mw`,
#: 1.000 MW), and `deliverability_confirmed` / `local_flex_tender_open` are untouched on the
#: no-evidence branches, which is what "nothing assertable" still means for those facts.
_D57_NOTE_PREFIX = (
    "D57 ASSUMED APPROVED: this site is assumed aggregated into a portfolio of 1.000 MW"
)


def _only_the_d57_note(warns: list[str]) -> bool:
    return len(warns) == 1 and warns[0].startswith(_D57_NOTE_PREFIX)


def test_producer_asserts_deliverability_only_with_reference_and_evidence() -> None:
    # Referenced + passing → confirmed True, with the full evidence chain attached.
    ctx, warns = _produce(_commitment(committed_kw=50.0))
    # Re-pinned (see _D57_NOTE_PREFIX): was `warns == []`; the only warning is now the D57 note.
    assert ctx is not None and ctx.deliverability_confirmed is True and _only_the_d57_note(warns)
    ev = ctx.evidence[0]
    assert ev.verdict is True
    assert ev.confirmed_by.startswith("engine:")  # a computed verdict says so — never dressed up
    assert ev.reference == "CM-TEST/AGR-1"
    assert ev.final_contract_year == 15.0
    assert ev.verified_capacity_kw is not None
    assert abs(ev.verified_capacity_kw - 200.0 * DERATE_2H * RETENTION_15Y) < 0.01
    assert "status 'placeholder'" in ev.basis  # arithmetic labelled with its input's status


def test_producer_records_a_known_failure_as_false() -> None:
    ctx, _ = _produce(_commitment(committed_kw=150.0))  # capability ≈ 88.9 < 150
    assert ctx is not None and ctx.deliverability_confirmed is False
    assert ctx.evidence[0].verdict is False  # the failing check is evidence too


def test_producer_never_asserts_from_an_unreferenced_commitment() -> None:
    ctx, warns = _produce(_commitment(committed_kw=50.0, reference=None))
    # Re-pinned (see _D57_NOTE_PREFIX): was `ctx is None`. Nothing about DELIVERABILITY is
    # assertable from an unreferenced commitment, and that is what is still pinned; the context now
    # exists only to carry the D57 portfolio aggregate.
    assert ctx is not None
    assert ctx.deliverability_confirmed is None and ctx.local_flex_tender_open is None
    assert ctx.evidence == []
    assert ctx.portfolio_mw == 1.0 and ctx.portfolio_threshold_mw == 1.0
    assert any("not evidence" in w for w in warns)


def test_producer_flags_contracted_flex_from_its_reference() -> None:
    ctx, _ = _produce(
        _commitment(
            stream=s.MarketStream.LOCAL_FLEX_PICLO, committed_kw=50.0, reference="PICLO/T-42"
        )
    )
    assert ctx is not None and ctx.local_flex_tender_open is True
    assert ctx.deliverability_confirmed is None  # no CM fact asserted
    assert ctx.evidence[0].confirmed_by == "contract-record"
    assert ctx.evidence[0].reference == "PICLO/T-42"


def test_producer_with_no_commitments_is_a_no_op() -> None:
    # Re-pinned (see _D57_NOTE_PREFIX): was `(None, [])`. With no commitments the producer asserts
    # no deliverability fact and no evidence; it carries only the D57 aggregate and its note.
    ctx, warns = _produce(None)
    assert ctx is not None
    assert ctx.deliverability_confirmed is None and ctx.local_flex_tender_open is None
    assert ctx.evidence == []
    assert ctx.portfolio_mw == 1.0 and ctx.portfolio_threshold_mw == 1.0
    assert _only_the_d57_note(warns)


class _FixedRevenueSolver:
    """Constant FIRM revenue (test fixture only) — covenant passes everywhere with big revenue,
    so deliverability is the ONLY axis splitting the grid (isolates the M4 rejection)."""

    def __init__(self, annual_revenue_gbp: float) -> None:
        self._rev = annual_revenue_gbp

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        return DispatchResult(
            power_kw=problem.power_kw,
            energy_kwh=problem.energy_kwh,
            duration_hours=problem.duration_hours,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.BASELINE,
            value_streams=[
                s.ValueStreamResult(
                    stream=s.MarketStream.TARIFF_ARBITRAGE,
                    annual_revenue_gbp=round(self._rev, 2),
                    firmness=s.Firmness.FIRM,
                )
            ],
            annual_charge_kwh=problem.energy_kwh * 300.0,
            annual_discharge_kwh=problem.energy_kwh * 260.0,
        )


def test_sizing_rejects_a_candidate_for_late_life_shortfall(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # The dormant M4 acceptance criterion fires: 40 kW committed for 15 years — the 50 kW
    # candidate's degraded capability (50 × 0.65 × 0.684 ≈ 22.2 kW) fails; the 100 kW candidate
    # (≈ 44.5 kW) passes. Fixed high revenue + injected debt ⇒ the covenant passes on both, so
    # deliverability alone decides — and the smaller (otherwise tie-preferred) candidate is
    # rejected for end-of-tenor shortfall.
    run = enumerate_sizing(
        make_site(),
        load_assumptions("gb_v0_1"),
        SearchSpace(power_kw_candidates=[50.0, 100.0], duration_hour_candidates=[2.0]),
        dispatch_solver=_FixedRevenueSolver(200_000.0),
        objective=SizingObjective(min_p90_dscr_covenant=1.3),
        debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
        extra_constraints=_seam(_commitment(committed_kw=40.0)),
    )
    by_power = {e.candidate.power_kw: e for e in run.candidates_evaluated}
    assert by_power[50.0].end_of_life_deliverable is False
    assert by_power[50.0].passes_covenant is True  # the covenant was NOT the rejection axis
    assert by_power[100.0].end_of_life_deliverable is True
    assert run.selected is not None
    assert run.selected.candidate.power_kw == 100.0, (
        "the 50 kW candidate must be REJECTED for late-life shortfall despite its covenant pass "
        "and the smaller-system tie-break"
    )
