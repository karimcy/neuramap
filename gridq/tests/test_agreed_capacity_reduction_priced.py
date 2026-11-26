"""The avoided agreed-capacity charge and TCR band drop are PRICED, and priced honestly (MICASC-3).

**The defect these tests stand on.** ``valuation.build_value_streams`` returned
``MIC_ASC_HEADROOM = £0`` unconditionally, reasoning that "the connection-headroom value is realised
through the connection-bounded arbitrage; pricing it separately would double-count". That is sound
for the ARBITRAGE reading of headroom and only for it. Two further amounts fall when the peak falls
and neither was modelled anywhere: the avoided AGREED-CAPACITY (ASC/MIC) charge and the avoided TCR
fixed residual band. They were structurally zero because both solvers computed the capacity line as
``capacity_charge × (asc_kw or mic_kw) × 365.25`` — a battery-invariant constant — so no dispatch
could reduce it.

**Why these tests are shaped the way they are — and the trap they fell into once already.** This
repo has shipped the same built-tested-never-called defect eleven times, every one with a passing
test. MICASC-3 made it twelve, during this very change: the overlay was wired into
``MilpDispatchSolver.solve``, all the direct-solve tests below went green, and a real
``gridq run`` on Bishops Cleeve MRF then reported **£0 on all 181 sizing-grid points, including
every one of the 31 confirmed candidates**. The sizing search and the confirm stage drive the
engine through ``PreloadedDispatchSolver``, which calls ``_solve_milp`` and ``_solved`` directly
and never touches ``solve``. Running the CLI caught it; the suite did not.

So the suite now covers BOTH entry points, and the ``..._a_real_run_actually_uses`` test is the
one that matters most — it is the only test here that would have failed on the first cut.
Each test names the production line it dies without:

* ``test_priced_through_the_solver_a_real_run_actually_uses`` dies if ``proxy_solver`` stops
  computing the overlay or stops forwarding it to ``_solved`` — i.e. if a real run stops pricing it.
* ``test_priced_on_the_production_solve_path`` dies if
  ``milp_solver.solve``'s ``self._last_capacity_reduction = self._capacity_reduction_overlay(...)``
  is deleted, if ``_solved`` stops passing ``capacity_reduction=``, if ``valuation``'s
  ``AGREED_CAPACITY_REDUCTION`` branch is deleted, or if the stream is dropped from
  ``gb_value_streams()``.
* ``test_overlay_objective_term_actually_shaves_the_peak`` dies if the
  ``peak_coefficient += capacity_price / annual_factor`` term is removed from the objective — the
  one line that turns the residual-peak epigraph from a tie-break into a real price.
* ``test_conditional_and_outside_the_priced_floor`` dies if the firmness is quietly promoted, which
  is the failure mode that would let conditional money into P50/P90/DSCR.

Fourteen mutations were run against this module, each breaking one production line; all fourteen
went RED and every file restored byte-identically (md5 checked).

**Direction of effect: UP, and deliberately fenced.** These tests exist to stop the number being
larger than it should be as much as to prove it is non-zero — hence the counterfactual assertion
(the renegotiation the customer could do with no battery is EXCLUDED) and the netting assertion
(firm revenue the overlay sacrifices is subtracted).

Cost: five real full-year solves plus one overlay each (the overlay roughly 2.4x's a single
full-series solve — measured 3.98 s → 9.65 s on Bishops Cleeve MRF). Comparable to
``test_real_sites_milp_runs.py``, so no marker and no skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.ingestion import load_site_model_json
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.proxy_solver import PreloadedDispatchSolver
from gridq.schemas import Firmness, MarketStream, SiteModel, SolverStatus
from gridq.schemas.market import is_priced_floor
from gridq.value_stack.gb_streams import (
    price_agreed_capacity_reduction,
    tcr_band_charge,
    tcr_band_for_kva,
)

REPO = Path(__file__).resolve().parents[1]
SITES = REPO / "examples" / "real" / "sites"
RELEASE = "gb_v1_11"

#: Knowl Hill on the invoice-anchored basis is the one real site where BOTH legs fire at a size the
#: sizing grid could plausibly reach: a 95.99 kW metered peak against a 325 kVA agreed capacity, so
#: a ~16 kW shave below the no-battery counterfactual crosses LV band 2 → band 1.
BAND_SITE = "knowl_hill_invoice_anchored.json"
#: Bishops Cleeve MRF: the site the MICASC audit worked its illustration on. Capacity leg only —
#: its band boundary is 68 kW below the counterfactual, far out of reach at any grid size.
AUDIT_SITE = "bishops_cleeve_mrf.json"


def _solve(stem: str, power_kw: float, duration_hours: float = 2.0) -> DispatchResult:
    """One real dispatch through the production entry point. No test-only code path."""
    site: SiteModel = load_site_model_json(SITES / stem)
    problem = DispatchProblem(
        site_model=site,
        assumptions=load_assumptions(RELEASE),
        power_kw=power_kw,
        energy_kwh=power_kw * duration_hours,
        duration_hours=duration_hours,
        requested_streams=[],
        constraints=[],
        scenario=None,
    )
    result = MilpDispatchSolver(seed=0).solve(problem)
    assert result.solver_status == SolverStatus.OPTIMAL, result.warnings
    return result


def _stream(result: DispatchResult, stream: MarketStream):
    """The one result for ``stream``, from wherever the engine filed it (floor or upside)."""
    found = [s for s in [*result.value_streams, *result.gated_upside] if s.stream == stream]
    assert len(found) == 1, f"expected exactly one {stream.value}, got {len(found)}"
    return found[0]


@pytest.fixture(scope="module")
def band_result() -> DispatchResult:
    return _solve(BAND_SITE, 25.0)


@pytest.fixture(scope="module")
def audit_result() -> DispatchResult:
    return _solve(AUDIT_SITE, 25.0)


def test_priced_on_the_production_solve_path(band_result: DispatchResult) -> None:
    """A real solve reports a POSITIVE agreed-capacity value. Was structurally £0 before MICASC-3.

    This is the reachability assertion. It goes through ``MilpDispatchSolver.solve`` and reads the
    stream off the ``DispatchResult`` the engine returns, so it fails if ANY link in the production
    chain is cut — the overlay call in ``solve``, the ``capacity_reduction=`` argument in
    ``_solved``, the ``valuation`` branch, or the registry entry.
    """
    stream = _stream(band_result, MarketStream.AGREED_CAPACITY_REDUCTION)
    assert stream.annual_revenue_gbp > 0.0, (
        "the avoided agreed-capacity charge is £0 on a site with 229 kVA of releasable capacity — "
        "the overlay is not reaching the value streams"
    )
    # Both legs, on this site: the capacity leg alone cannot reach £2,000 at 25 kW
    # (25 kW × £38.83/kVA/yr ≈ £971), so this also proves the TCR band step is being counted.
    assert stream.annual_revenue_gbp > 2_000.0, stream.warnings


def test_overlay_objective_term_actually_shaves_the_peak() -> None:
    """The priced objective term moves the dispatch — the floor tie-break alone does not.

    MEASURED BEFORE THIS CHANGE: the existing ``_EPS_PEAK`` tie-break shaves **0.000 kW** at
    7.4 kW and at 25 kW on Bishops Cleeve MRF, because shifting discharge onto the annual peak
    costs real red-band money and an epsilon cannot pay for it. So pricing the capacity leg off the
    floor schedule would have reported £0 at every size the sizing grid explores. This test pins
    the difference between the two objectives, and it is the only test that fails if the
    ``peak_coefficient += capacity_price / annual_factor`` line is deleted.
    """
    site: SiteModel = load_site_model_json(SITES / AUDIT_SITE)
    problem = DispatchProblem(
        site_model=site,
        assumptions=load_assumptions(RELEASE),
        power_kw=25.0,
        energy_kwh=50.0,
        duration_hours=2.0,
        requested_streams=[],
        constraints=[],
        scenario=None,
    )
    solver = MilpDispatchSolver(seed=0)
    solver.solve(problem)
    bill = solver.last_customer_bill()
    assert bill is not None
    floor_peak = bill.peak_import_after_kw
    assert floor_peak is not None
    # The floor dispatch is bill-optimised, not peak-optimised: it leaves the metered peak intact.
    assert floor_peak == pytest.approx(bill.peak_import_before_kw, abs=1e-6)

    value = solver._last_capacity_reduction
    assert value is not None
    # The overlay reached a strictly lower capacity than the no-battery counterfactual — only the
    # priced objective term can do that.
    assert value.proposed_kva < value.counterfactual_kva, value.notes


def test_priced_through_the_solver_a_real_run_actually_uses() -> None:
    """``PreloadedDispatchSolver`` is the path ``gridq run`` takes; it must price this too.

    **This test exists because the first version of MICASC-3 failed exactly here.** The overlay was
    wired into ``MilpDispatchSolver.solve``; every direct-solve test above passed; and a real
    ``gridq run`` on Bishops Cleeve MRF reported **£0 on all 181 sizing-grid points, including all
    31 confirmed candidates**. The sizing search and the confirm stage drive the engine through
    ``PreloadedDispatchSolver``, which calls ``_solve_milp`` and ``_solved`` directly and never
    touches ``MilpDispatchSolver.solve``. That is the twelfth instance of this repo's
    built-tested-never-called defect, and it was caught by running the CLI, not by the suite.

    So this test uses the wrapper, unweighted — the exact construction ``two_stage`` builds for its
    ``confirm_solver``. It fails if ``proxy_solver`` stops computing or stops forwarding the
    overlay.
    """
    site: SiteModel = load_site_model_json(SITES / BAND_SITE)
    assumptions = load_assumptions(RELEASE)
    base = MilpDispatchSolver(seed=0)
    load, timestamps, interval_min, _warn = base._load_series(
        site.load_data.half_hourly_file_ref or ""
    )
    confirm_solver = PreloadedDispatchSolver(
        base,
        load=load,
        timestamps=timestamps,
        interval_min=interval_min,
    )
    problem = DispatchProblem(
        site_model=site,
        assumptions=assumptions,
        power_kw=25.0,
        energy_kwh=50.0,
        duration_hours=2.0,
        requested_streams=[],
        constraints=[],
        scenario=None,
    )
    result = confirm_solver.solve(problem)
    assert result.solver_status == SolverStatus.OPTIMAL, result.warnings
    stream = _stream(result, MarketStream.AGREED_CAPACITY_REDUCTION)
    assert stream.annual_revenue_gbp > 2_000.0, (
        "the confirm-stage solver reports £0 for the agreed-capacity reduction — the overlay is "
        "wired into MilpDispatchSolver.solve only, which no real run calls"
    )
    # D57 item 6 (2026-08-18): CONTRACTED, not conditional - D53 held this stream out of the
    # priced floor pending an owner attestation of the DNO renegotiation and D57 supplies it.
    # The assertion this test is FOR is the one above it: the confirm-stage solver, which is
    # the path a real run takes, must actually PRICE the overlay.
    assert stream.firmness == Firmness.CONTRACTED


def test_typical_period_proxy_solver_refuses_rather_than_claiming() -> None:
    """The SEARCH solver is weighted, and a weighted subset cannot evidence an annual maximum.

    The refusal must be visible on the record, not an indistinguishable £0 — and it must be cheap:
    the helper declines before any second solve is paid for.
    """
    import numpy as np

    site: SiteModel = load_site_model_json(SITES / BAND_SITE)
    base = MilpDispatchSolver(seed=0)
    load, timestamps, interval_min, _warn = base._load_series(
        site.load_data.half_hourly_file_ref or ""
    )
    # A two-day slice standing for the year, exactly the shape `select_typical_days` produces.
    positions = slice(0, 96)
    search_solver = PreloadedDispatchSolver(
        base,
        load=load[positions],
        timestamps=timestamps[positions],
        interval_min=interval_min,
        interval_weights=np.full(96, 182.5, dtype=float),
    )
    problem = DispatchProblem(
        site_model=site,
        assumptions=load_assumptions(RELEASE),
        power_kw=25.0,
        energy_kwh=50.0,
        duration_hours=2.0,
        requested_streams=[],
        constraints=[],
        scenario=None,
    )
    result = search_solver.solve(problem)
    stream = _stream(result, MarketStream.AGREED_CAPACITY_REDUCTION)
    assert stream.annual_revenue_gbp == 0.0
    blob = " ".join(stream.warnings)
    assert "REFUSAL TO CLAIM" in blob or "NOT PRICED" in blob, blob


def test_contracted_and_inside_the_priced_floor_with_the_assumption_stated(
    band_result: DispatchResult,
) -> None:
    """D57 item 6: CONTRACTED and in the priced floor, with the renegotiation stated as ASSUMED.

    **This test replaces the assertion it inverts, deliberately, and the inversion is authorised.**
    It used to require CONDITIONAL on the reasoning that the charge falls when the connection
    agreement changes, which is an application by the customer and a decision by the DNO. That
    reasoning is intact and is still published on the stream — but D53 held the stream out of the
    floor *pending an owner attestation of that renegotiation*, and **D57 (Olly, 2026-08-18)
    supplies the attestation**. D57's status line supersedes every other instruction in this
    codebase on stream eligibility, so pricing this into the floor is execution, not a quiet
    promotion.

    What the test therefore pins is the pair that keeps it honest: the £ is in the floor, AND the
    record says the renegotiation is ASSUMED APPROVED rather than granted (D45). It dies if
    ``AgreedCapacityReductionStream.check_gates``'s ``GateStatus.OPEN`` is reverted to CONDITIONAL,
    and it dies if the D57 assumption sentence is dropped from the gate warnings.
    """
    stream = _stream(band_result, MarketStream.AGREED_CAPACITY_REDUCTION)
    assert stream.firmness == Firmness.CONTRACTED
    assert is_priced_floor(stream), (
        "D57 item 6 requires the avoided agreed capacity in the priced floor; a CONDITIONAL "
        "firmness here means the gate change was reverted and the stream moves no IRR again"
    )
    blob = " ".join(stream.warnings)
    # The assumption is STATED, and it is stated as an assumption.
    assert "ON A D57 ASSUMPTION" in blob
    assert "ASSUMED APPROVED" in blob
    assert "CONFIRMATION ASK" in blob
    # And it is still never written as a thing the DNO has done (D45).
    assert "no DNO is asserted to have granted anything" in blob


def test_free_renegotiation_is_excluded_from_the_battery_credit(
    band_result: DispatchResult,
) -> None:
    """The saving the customer could bank with NO battery is reported but NOT credited.

    Knowl Hill (invoice-anchored) is billed on 325 kVA against a 95.99 kW metered peak. Simply
    asking the DNO to reduce the agreed capacity is worth £18,632/yr — five times anything the
    battery adds. Crediting that to the battery is the largest single overstatement available on
    this stream, and it is the error the MICASC audit's framing invited.
    """
    stream = _stream(band_result, MarketStream.AGREED_CAPACITY_REDUCTION)
    blob = " ".join(stream.warnings)
    assert "COUNTERFACTUAL" in blob
    assert "NO BATTERY" in blob
    # The battery credit is far below the renegotiation-only figure it is incremental to.
    assert stream.annual_revenue_gbp < 10_000.0, (
        "the battery is being credited with renegotiation-only capacity"
    )


def test_sacrificed_firm_revenue_is_netted_off() -> None:
    """FIRM revenue the overlay gives up to buy its peak is SUBTRACTED — arithmetically.

    This is the disjointness proof against the connection-bounded arbitrage. The legs differ in
    kind (energy £/kWh vs capacity £/kVA/day), but that is a story; the netting is what makes it a
    proof, so this test checks the SUBTRACTION and not the sentence describing it. An earlier
    version of this test asserted only that the note text was present and stayed green when the
    netting was deleted — the exact hollow-test failure this repo has shipped eleven times.

    Bishops Cleeve MRF at 100 kW is used because the sacrifice is unambiguously non-zero there
    (~£78/yr against a ~£1,635/yr gross): a 100 kW battery on a 147 kW peak has to move real
    red-band discharge onto the peak half-hours to shave it, and that discharge was earning money
    where it was.
    """
    site: SiteModel = load_site_model_json(SITES / AUDIT_SITE)
    problem = DispatchProblem(
        site_model=site,
        assumptions=load_assumptions(RELEASE),
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        requested_streams=[],
        constraints=[],
        scenario=None,
    )
    solver = MilpDispatchSolver(seed=0)
    solver.solve(problem)
    value = solver.last_capacity_reduction()
    assert value is not None
    gross = value.capacity_leg_gbp + value.band_leg_gbp
    assert gross > 0.0
    # The reported £ is the gross saving MINUS the firm revenue the overlay gave up to earn it.
    assert value.annual_value_gbp < gross, (
        f"net £{value.annual_value_gbp} == gross £{gross}: firm revenue sacrificed by the overlay "
        "is not being netted off, so the floor's £ is being counted twice"
    )
    blob = " ".join(value.notes)
    assert "NETTED AGAINST THE PRICED FLOOR" in blob
    assert "DISJOINT FROM THE CONNECTION-BOUNDED ARBITRAGE" in blob


def test_floor_streams_are_untouched_by_the_overlay(band_result: DispatchResult) -> None:
    """The overlay must not move one penny of the priced floor — it is a second, separate solve.

    If the capacity price had gone into the FLOOR objective instead, the dispatch would sell firm
    bankable revenue to buy conditional unbankable revenue and the underwritten floor would fall.
    """
    site: SiteModel = load_site_model_json(SITES / BAND_SITE)
    problem = DispatchProblem(
        site_model=site,
        assumptions=load_assumptions(RELEASE),
        power_kw=25.0,
        energy_kwh=50.0,
        duration_hours=2.0,
        requested_streams=[],
        constraints=[],
        scenario=None,
    )
    solver = MilpDispatchSolver(seed=0)
    solver.solve(problem)
    schedule = solver.last_schedule()
    assert schedule is not None
    # The floor schedule's own £ — the numbers finance sums — must equal what the result reports.
    arbitrage = _stream(band_result, MarketStream.TARIFF_ARBITRAGE)
    duos = _stream(band_result, MarketStream.DUOS_RED_BAND_AVOIDANCE)
    assert arbitrage.annual_revenue_gbp - arbitrage.annual_cost_gbp == pytest.approx(
        schedule.arbitrage_value_gbp, abs=0.01
    )
    assert duos.annual_revenue_gbp - duos.annual_cost_gbp == pytest.approx(
        schedule.duos_avoidance_value_gbp, abs=0.01
    )


def test_mic_asc_headroom_still_reports_zero_and_says_why(band_result: DispatchResult) -> None:
    """The old stream keeps its £0 — headroom-as-arbitrage is unchanged — and points here.

    Leaving the superseded "neither of which any code path models" claim in place would have
    shipped a fresh contradiction the moment the value was priced.
    """
    headroom = _stream(band_result, MarketStream.MIC_ASC_HEADROOM)
    assert headroom.annual_revenue_gbp == 0.0
    assert "AGREED_CAPACITY_REDUCTION" in " ".join(headroom.warnings)


# --- the published arithmetic, pinned ---------------------------------------------------------


def test_band_step_counts_tnuos_as_well_as_duos() -> None:
    """The TCR band step is TNUoS residual + DUoS banded fixed. Counting DUoS alone understates ~7x.

    This is the audit's arithmetic error, pinned so it cannot come back. At NGED West Midlands LV,
    band 2 → band 1:
      * DUoS banded fixed  219.77 → 129.59 p/MPAN/day  = £329.42/yr   (what the audit costed)
      * TNUoS residual      11.511840 → 5.797398 £/day = £2,087.20/yr (what the audit missed)
    Both are banded on the SAME agreed capacity, so both fall together.
    """
    assumptions = load_assumptions(RELEASE)
    band_2 = tcr_band_charge(100.0, "NGED_WM", assumptions)
    band_1 = tcr_band_charge(50.0, "NGED_WM", assumptions)
    assert band_2 is not None and band_1 is not None
    assert (band_2.band, band_1.band) == ("band_2", "band_1")
    # DUoS leg, verbatim from the 2026/27 NGED West Midlands charging statement.
    assert band_2.duos_fixed_gbp_per_day == pytest.approx(2.1977)
    assert band_1.duos_fixed_gbp_per_day == pytest.approx(1.2959)
    # TNUoS leg, verbatim from NESO Jan-2026 Final TNUoS 2026/27 Table 10.
    assert band_2.tnuos_residual_gbp_per_day == pytest.approx(11.511840)
    assert band_1.tnuos_residual_gbp_per_day == pytest.approx(5.797398)
    step_gbp_per_year = (band_2.total_gbp_per_day - band_1.total_gbp_per_day) * 365.25
    assert step_gbp_per_year == pytest.approx(2_416.62, abs=1.0)
    duos_only = (band_2.duos_fixed_gbp_per_day - band_1.duos_fixed_gbp_per_day) * 365.25
    assert duos_only == pytest.approx(329.42, abs=1.0)
    assert step_gbp_per_year > 7 * duos_only, "the TNUoS leg is the larger of the two by ~6x"


def test_zero_duos_fixed_line_is_a_fact_not_a_gap() -> None:
    """SSEN_SEPD publishes 0.00 p/MPAN/day at every LV band — read as zero, TNUoS leg still applies.

    That area recovers its residual through band-VARYING unit rates instead. Treating the 0.00 as a
    missing input would either refuse a real site or invite a cross-area proxy, and gb_v1_11
    records cross-area proxying as the defect that release exists to have fixed.
    """
    assumptions = load_assumptions(RELEASE)
    sepd = tcr_band_charge(100.0, "SSEN_SEPD", assumptions)
    assert sepd is not None
    assert sepd.duos_fixed_gbp_per_day == 0.0
    assert sepd.tnuos_residual_gbp_per_day == pytest.approx(11.511840)


def test_band_boundary_rule_is_upper_inclusive() -> None:
    """``Lower < x <= Upper``, the charging statement's own rule, verbatim in the release.

    Getting this backwards moves a site sitting exactly on a threshold into the wrong band; on the
    LV4/LV3 step that is £8,692/yr.
    """
    assumptions = load_assumptions(RELEASE)
    assert tcr_band_for_kva(80.0, assumptions) == "band_1"
    assert tcr_band_for_kva(80.001, assumptions) == "band_2"
    assert tcr_band_for_kva(150.0, assumptions) == "band_2"
    assert tcr_band_for_kva(231.0, assumptions) == "band_3"
    assert tcr_band_for_kva(231.001, assumptions) == "band_4"


def test_part_year_coverage_refuses_to_claim() -> None:
    """A part-year meter cannot evidence an ANNUAL worst case, so the claim is REFUSED, not scaled.

    One half-hour above a reduced agreed capacity is a breach, and the half-hour most likely to
    cause one is in the season a part-year dataset does not cover.
    """
    site: SiteModel = load_site_model_json(SITES / BAND_SITE)
    assumptions = load_assumptions(RELEASE)
    value = price_agreed_capacity_reduction(
        site,
        assumptions,
        modelled_peak_kw=50.0,
        rated_power_kw=25.0,
        full_series=True,
        coverage_factor=2.0,  # six months of data
    )
    assert value is not None
    assert value.annual_value_gbp == 0.0
    assert "NOT PRICED" in " ".join(value.notes)
    assert "refusal, not a measurement" in " ".join(value.notes)


def test_typical_period_proxy_refuses_to_claim() -> None:
    """A weighted subset of periods cannot evidence the annual maximum either."""
    site: SiteModel = load_site_model_json(SITES / BAND_SITE)
    value = price_agreed_capacity_reduction(
        site,
        load_assumptions(RELEASE),
        modelled_peak_kw=50.0,
        rated_power_kw=25.0,
        full_series=False,
    )
    assert value is not None
    assert value.annual_value_gbp == 0.0
    assert "REFUSAL TO CLAIM" in " ".join(value.notes)


def test_security_margin_reduces_the_claim_and_is_labelled() -> None:
    """The margin is a labelled MODELLING CONVENTION, direction DOWN — never a found rate."""
    site: SiteModel = load_site_model_json(SITES / BAND_SITE)
    assumptions = load_assumptions(RELEASE)
    small = price_agreed_capacity_reduction(
        site, assumptions, modelled_peak_kw=70.0, rated_power_kw=10.0, full_series=True
    )
    large = price_agreed_capacity_reduction(
        site, assumptions, modelled_peak_kw=70.0, rated_power_kw=200.0, full_series=True
    )
    assert small is not None and large is not None
    # Same modelled peak, bigger battery ⇒ bigger margin ⇒ a SMALLER (more conservative) claim.
    assert large.security_margin_kw > small.security_margin_kw
    assert large.annual_value_gbp < small.annual_value_gbp
    assert "MODELLING CONVENTION" in " ".join(small.notes)
