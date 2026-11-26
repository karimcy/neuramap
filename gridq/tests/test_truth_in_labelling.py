"""WP-1 truth-in-labelling — every claim the pack makes is one the code computed.

These pin the WP-0 findings whose common shape is an OUTPUT THAT ASSERTS SOMETHING NOBODY
COMPUTED, or a declared control that cannot fire. No computed number moves in any of them; what
moves is what the artifacts are allowed to say.

- **N8**  — the Low/Central/High sensitivity table is three identical rows and the scenario
  builder's own "this case equals Central" warning is dropped one hop from the record.
- **N15** — the no-viable verdict quotes the whole proxy cloud as "dispatch solved and priced".
- **N16** — the persisted sizing grid says "no candidate selected" on runs whose result says
  SELECTED, with both persisted on the same `QuoteRecord`.
- **N17** — scenario warnings and `bankable_status` are computed then never read.
- **N18** — `cod_delay_months` is reported on the row and applied to no number.
- **N19** — the T0-underwritten guard is satisfied by any non-empty warnings list, so it never
  fires on any input the pipeline can produce.
- **N20** — a quote with `financial_case=None` can be walked to `underwritten`.
- **N21** — the Gate-2 confidence ceiling is prose and constrains nothing.
- **N22** — `--forecast` is documented as never priced into the floor; the engine prices it in.
- **N30** — the not-bankable disclaimer hard-codes a status value the record can contradict.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance import DebtTerms, DispatchEnergy
from gridq.ingestion.hh_mandate import HalfHourlyAdmission
from gridq.quotes import (
    InvalidTransitionError,
    assemble_quote_record,
    assess_confidence,
    generate_quote,
    transition_quote,
    underwriting_blockers,
)
from gridq.quotes.sensitivities import compute_scenario_sensitivities
from gridq.scenarios import Scenario, ScenarioName, ScenarioSet
from gridq.schemas import T0_UNDERWRITING_ACKNOWLEDGEMENT
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import CandidateEvaluation, SizingRun

_DEBT = DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10)
_DISPATCH = DispatchEnergy(annual_discharge_kwh=400_000.0)


def _sensitivities(rich_quote):
    return compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
    )


# --- N8 / N17: the sensitivity table may not assert a sensitivity it never ran ----------------


def test_n8_identical_rows_carry_the_equals_central_warning(rich_quote) -> None:
    """N8: three bit-identical rows must say WHY they are identical, on the row.

    The standard set defaults every unsupplied band to identity, so Low/Central/High are the base
    case three times. Presented bare, a lender reads that as "insensitive to price and cost" — the
    strongest possible statement about the asset, made by a table that ran no sensitivity.
    """
    rows = _sensitivities(rich_quote).rows
    low = next(r for r in rows if r.label == "Low")
    high = next(r for r in rows if r.label == "High")
    central = next(r for r in rows if r.label == "Central")

    # The defect's precondition: the rows really are identical figures.
    assert low.min_dscr == central.min_dscr == high.min_dscr
    assert low.irr_pct == central.irr_pct == high.irr_pct
    # ...and each identity row now says so, in the builder's own words.
    for row in (low, high):
        assert row.note is not None
        assert "equals Central" in row.note


def test_n17_set_level_warnings_reach_the_caller(rich_quote) -> None:
    """N17: `validation_warnings` / `bankable_status` were computed and discarded here."""
    out = _sensitivities(rich_quote)
    joined = " ".join(out.set_warnings)
    assert "NO SENSITIVITY WAS RUN" in joined  # the all-identity finding (N8's set-level half)
    assert "not bankable" in joined.lower()  # the placeholder-weights bankable_status


def test_n17_set_warnings_land_on_the_quote(make_site) -> None:
    """N17 end-to-end: the finding reaches `QuoteRecord.warnings`, not just the return value."""
    run = generate_quote(make_site(), load_assumptions("gb_v0_1"))
    if not run.quote.scenario_sensitivities:
        pytest.skip("this site produced no priced base case, so no sensitivity table was built")
    assert any("NO SENSITIVITY WAS RUN" in w for w in run.quote.warnings)


def test_n8_rows_still_carry_their_computed_figures(rich_quote) -> None:
    """WP-1 changes labels, never numbers: the figures are exactly what they were."""
    rows = _sensitivities(rich_quote).rows
    assert all(r.min_dscr is not None and r.irr_pct is not None for r in rows)
    assert _sensitivities(rich_quote) == _sensitivities(rich_quote)  # still deterministic


# --- N18: a COD delay that reaches no number may not be reported as if it did -----------------


def _cod_delayed_set() -> ScenarioSet:
    return ScenarioSet(
        scenario_set_id="cod",
        assumption_version="gb_v0_1",
        scenarios=[
            Scenario(
                scenario_id="delayed",
                name=ScenarioName.CENTRAL,
                probability_weight=1.0,
                cod_delay_months=12,
            )
        ],
    )


def test_n18_a_delayed_row_reports_its_own_applied_figures(rich_quote) -> None:
    """N18, closed by WP-UW-17 (D130): the delay now reaches the financial model.

    Before, `cod_delay_months` reached no line of `finance/` and was absent from `_hash_input`, so a
    delayed row would have reported the *undelayed* DSCR and IRR; it was withheld. It is now
    APPLIED: the run moves to the month-dated timeline (operations start 12 months late), so the
    delayed row carries its own figures, different from the on-time row's, and says what is and is
    not modelled. Re-add the withholding, or drop the delay from `build_cashflow_model` /
    `run_financial_model`, and this fails.
    """
    rows = _sensitivities_with(rich_quote, _cod_delayed_set())
    row = rows[0]
    on_time = _sensitivities(rich_quote).rows
    central = next(r for r in on_time if r.scenario_id == "central")
    assert row.cod_delay_months == 12  # still reported — the delay is real
    assert row.min_dscr is not None and row.irr_pct is not None  # and now applied
    assert row.irr_pct < central.irr_pct  # a year's delay lowers the return
    assert row.note is not None and "APPLIED" in row.note and "NOT modelled" in row.note


def test_n18_an_undelayed_row_is_untouched(rich_quote) -> None:
    """The withholding is scoped to non-zero delays: the standard set is unaffected."""
    rows = _sensitivities(rich_quote).rows
    assert all(r.cod_delay_months == 0 for r in rows)
    assert all(r.min_dscr is not None for r in rows)


def _sensitivities_with(rich_quote, scenario_set):
    return compute_scenario_sensitivities(
        rich_quote.site,
        rich_quote.result,
        load_assumptions("gb_v0_1"),
        debt_terms=_DEBT,
        dispatch_energy=_DISPATCH,
        scenario_set=scenario_set,
    ).rows


# --- N19: the T0-underwritten guard must be able to fire --------------------------------------


def _t0_record(**overrides) -> dict:
    base = {
        "quote_id": "quote_t0",
        "quote_mode": s.QuoteMode.UNDERWRITTEN,
        "site_id": "site_t0",
        "data_tier": s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_n19_a_generic_warning_no_longer_satisfies_the_t0_guard() -> None:
    """N19: the guard tested `not self.warnings`, and every real quote has warnings.

    The three routes that make the old guard vacuous — the not-bankable note, the
    zero-confirmed-assumptions note and the always-appended half-hourly basis note — are all
    represented here. Each used to pass the guard on its own.
    """
    for vacuous in (
        ["This quote is NOT bankable: bankable_status=not_bankable. ..."],
        ["0 of 12 assumptions used by this run are confirmed."],
        ["Half-hourly basis: MODELLED — reference profile x, scaled to y."],
    ):
        with pytest.raises(ValidationError):
            s.QuoteRecord(**_t0_record(warnings=vacuous))


def test_n19_the_specific_acknowledgement_does_satisfy_it() -> None:
    """The escape hatch exists, but a caller can only reach it on purpose."""
    quote = s.QuoteRecord(**_t0_record(warnings=[T0_UNDERWRITING_ACKNOWLEDGEMENT]))
    assert quote.quote_mode is s.QuoteMode.UNDERWRITTEN


def test_n19_the_guard_is_scoped_to_t0_and_to_underwritten() -> None:
    """It must not start refusing things it was never about."""
    s.QuoteRecord(**_t0_record(quote_mode=s.QuoteMode.INDICATIVE))  # T0 but not underwritten
    s.QuoteRecord(
        **_t0_record(data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY)
    )  # underwritten but not T0


# --- N20 / N21: `underwritten` must be a claim the record can support -------------------------


def _bare_indicative(**overrides) -> s.QuoteRecord:
    base = {
        "quote_id": "quote_bare",
        "quote_mode": s.QuoteMode.INDICATIVE,
        "site_id": "site_bare",
        "data_tier": s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        "status_history": [s.StatusHistoryEntry(quote_mode=s.QuoteMode.DRAFT)],
    }
    base.update(overrides)
    return s.QuoteRecord(**base)


def test_n20_a_quote_with_no_financial_case_cannot_be_underwritten() -> None:
    """N20: the `InsufficientInputsError` path produces exactly this record, and it still
    assembled — nothing then stopped it being walked to `underwritten`."""
    quote = _bare_indicative()
    with pytest.raises(InvalidTransitionError) as exc:
        transition_quote(quote, s.QuoteMode.UNDERWRITTEN)
    assert "financial_case is None" in str(exc.value)


def test_n20_blockers_name_every_missing_precondition() -> None:
    blockers = " ".join(underwriting_blockers(_bare_indicative()))
    assert "financial_case is None" in blockers
    assert "no engine run is attached" in blockers
    assert "no half-hourly basis is recorded" in blockers


def test_n20_the_covenant_outcome_is_deliberately_not_a_precondition(rich_quote) -> None:
    """`underwritten` says the case was TESTED; `bankable_status` says whether it passed.

    Blocking on a failed covenant would conflate the two, and would also refuse every unlevered
    case (no debt structure ⇒ ``passes=None``) — turning a financing-data gap into a project
    verdict, which `docs/validation_requirements.md` §3 explicitly forbids. The fixture's covenant
    genuinely fails (min P90 DSCR ~0.75 against a 1.30 covenant) and it is still underwritable:
    that is a correct, honest, underwritten quote that reports a failing covenant.
    """
    assert rich_quote.quote.financial_case.covenant_status.passes is False
    assert underwriting_blockers(rich_quote.quote) == []
    assert transition_quote(rich_quote.quote, s.QuoteMode.UNDERWRITTEN).quote_mode is (
        s.QuoteMode.UNDERWRITTEN
    )


def test_n20_a_baseline_engine_run_cannot_be_underwritten(rich_quote) -> None:
    baseline = rich_quote.result.model_copy(
        update={"implementation_level": s.ImplementationLevel.BASELINE}
    )
    quote = rich_quote.quote.model_copy(update={"optimisation_result": baseline})
    assert any("BaselineEngine" in b for b in underwriting_blockers(quote))


def test_n20_a_no_viable_verdict_cannot_be_underwritten(rich_quote) -> None:
    refused = rich_quote.result.model_copy(
        update={"selection_status": s.SelectionStatus.NO_VIABLE_CONFIGURATION}
    )
    quote = rich_quote.quote.model_copy(update={"optimisation_result": refused})
    assert any("no_viable_configuration" in b for b in underwriting_blockers(quote))


def test_n21_a_modelled_basis_cannot_advance_past_indicative(rich_quote) -> None:
    """N21: the ceiling was interpolated into a warning string and capped nothing — a
    modelled-basis run permitted every transition a metered one did."""
    modelled = rich_quote.quote.model_copy(
        update={
            "half_hourly_basis": s.HalfHourlyBasis.MODELLED,
            "confidence_ceiling": s.ConfidenceBand.MEDIUM,
        }
    )
    with pytest.raises(InvalidTransitionError) as exc:
        transition_quote(modelled, s.QuoteMode.UNDERWRITTEN)
    assert "MODELLED" in str(exc.value) and "basis_override_note" in str(exc.value)

    # ...and the override is a real, recorded escape hatch, not a dead end.
    overridden = modelled.model_copy(
        update={"basis_override_note": "Olly 2026-08-11: invoice-reconciled shape accepted."}
    )
    assert transition_quote(overridden, s.QuoteMode.UNDERWRITTEN).quote_mode is (
        s.QuoteMode.UNDERWRITTEN
    )


def test_n21_the_gate_applies_to_every_state_that_claims_underwriting(rich_quote) -> None:
    """CONTRACTED and OPERATING inherit the underwritten claim, so they inherit the gate."""
    underwritten = transition_quote(rich_quote.quote, s.QuoteMode.UNDERWRITTEN)
    stripped = underwritten.model_copy(update={"financial_case": None})
    with pytest.raises(InvalidTransitionError):
        transition_quote(stripped, s.QuoteMode.CONTRACTED)


def test_n21_lapse_is_never_gated() -> None:
    """A quote must always be able to die. The gate is about claims, not about housekeeping."""
    assert transition_quote(_bare_indicative(), s.QuoteMode.LAPSED).quote_mode is (
        s.QuoteMode.LAPSED
    )


# --- N21 (wiring): the basis and the band land on the record ----------------------------------


def test_the_gate_2_basis_and_confidence_band_are_persisted(make_site) -> None:
    """`assess_confidence` was built and tested but never connected to `generate_quote`.

    The site DECLARES its basis: since N9/D44 an undeclared basis is honestly persisted as ``None``
    (never defaulted to metered), so a fixture that states nothing about its series' provenance can
    no longer demonstrate that a *recorded* basis reaches the record.
    """
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "metered"
    run = generate_quote(site, load_assumptions("gb_v0_1"))
    quote = run.quote
    assert quote.half_hourly_basis is not None
    assert quote.confidence_ceiling is not None
    assert quote.confidence_band is not None
    assert quote.confidence_binding_factors  # what to fix to raise it, not just the letter grade
    assert run.confidence is not None and run.confidence.band is quote.confidence_band
    assert any("Confidence:" in w for w in quote.warnings)


def test_the_band_never_exceeds_the_gate_2_ceiling(make_site) -> None:
    """The whole point of a ceiling: no other evidence can lift the band above it."""
    run = generate_quote(make_site(), load_assumptions("gb_v0_1"))
    assert run.quote.confidence_band.rank <= run.quote.confidence_ceiling.rank


def test_confidence_reads_the_records_own_value_streams(rich_quote) -> None:
    """`_revenue_factor` read a top-level `value_streams` attribute no `QuoteRecord` has, so the
    floor-composition factor reported "no priced floor revenue to assess" — and pinned the band at
    LOW — on every real record. The fixture's floor is 15% contracted, which is a real, non-LOW
    reading of a real floor, not the absent-data reading."""
    assessment = assess_confidence(
        rich_quote.site,
        rich_quote.quote,
        admission=HalfHourlyAdmission(basis=s.HalfHourlyBasis.METERED, file_ref="real.csv"),
        uses_unconfirmed_assumptions=False,
    )
    floor = next(f for f in assessment.factors if f.name == "floor composition")
    assert "no priced floor revenue" not in floor.reason
    assert "% of the priced floor" in floor.reason


# --- N15: the no-viable verdict must count its own evidence -----------------------------------


def test_n15_the_verdict_counts_solved_and_priced_not_the_proxy_cloud() -> None:
    """N15: the string formatted `len(run.candidates_evaluated)` — the whole cloud, mostly
    typical-day proxy solves — into the claim "dispatch solved and priced", while the viability
    test three lines above filters on `not e.proxy`. A run confirming 10 reported ~280."""
    from gridq.optimisation import engine as engine_mod

    src = engine_mod.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    start = text.index("no viable configuration:")
    verdict = text[start : text.index("selection_status=Selection", start)]
    assert "len(solved_and_priced)" in verdict
    assert "len(run.candidates_evaluated)" not in verdict
    assert "PROXY" in verdict  # the proxy sweep is reported separately and labelled


# --- N16: the persisted grid must agree with the result about whether a size was chosen -------


def _evaluation(power_kw: float) -> CandidateEvaluation:
    """One priced, full-series candidate — enough for the grid projection, nothing invented."""
    candidate = SizingCandidate(power_kw=power_kw, duration_hours=2.0)
    return CandidateEvaluation(
        candidate=candidate,
        optimisation_result=s.OptimisationResult(
            result_id=f"res_{power_kw}",
            request_id="req_x",
            implementation_level=s.ImplementationLevel.IN_PROGRESS,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            selected_power_kw=power_kw,
            selected_energy_kwh=power_kw * 2.0,
            duration_hours=2.0,
            solver_status=s.SolverStatus.OPTIMAL,
        ),
        priceable=True,
        bankable=False,
        min_p90_dscr=None,
        covenant=1.3,
        passes_covenant=None,
        levered_return_pct=None,
        project_irr_pct=6.5,
        financial=None,
    )


def _run_with(selected, indicative, candidates) -> SizingRun:
    return SizingRun(
        search_space_size=len(candidates),
        candidates_evaluated=list(candidates),
        selected=selected,
        selection_reason="no bankable candidate: no size meets the P90 DSCR covenant",
        rejected_by_covenant=[],
        bankable=False,
        assumption_version="gb_v0_1",
        indicative_selection=indicative,
    )


@pytest.fixture
def sizing_run_with_candidates() -> tuple[SizingRun, CandidateEvaluation]:
    """A run where no candidate met the covenant — the DEFAULT path, and the N16 defect's home."""
    candidates = [_evaluation(100.0), _evaluation(250.0)]
    return _run_with(None, None, candidates), candidates[1]


def test_n16_the_grid_marks_the_indicatively_chosen_candidate(sizing_run_with_candidates) -> None:
    """N16: `sizing_grid()` copied `selected=None` + "no candidate selected" onto the same
    `QuoteRecord` whose result said `selection_status=SELECTED` and named a kW. Both were
    persisted, side by side, and one was false."""
    run, chosen = sizing_run_with_candidates
    assert run.sizing_grid().selected is None  # nothing was chosen yet — honest
    assert run.sizing_grid().selection_basis == "none"

    recorded = run.with_indicative_selection(chosen, "ranked on priced-floor revenue")
    grid = recorded.sizing_grid()
    assert grid.selected is not None
    assert grid.selected.power_kw == chosen.candidate.power_kw
    assert grid.selection_basis == "indicative_fallback"
    assert "priced-floor revenue" in grid.selection_reason


def test_n16_an_indicative_choice_never_overwrites_a_real_selection(
    sizing_run_with_candidates,
) -> None:
    """A covenant-feasible selection is the objective's optimum; a fallback must not shadow it."""
    run, chosen = sizing_run_with_candidates
    feasible = _run_with(chosen, None, run.candidates_evaluated)
    unchanged = feasible.with_indicative_selection(chosen, "irrelevant")
    assert unchanged.indicative_selection is None
    assert unchanged.sizing_grid().selection_basis == "covenant"


# --- N30: the disclaimer must not assert a status the record contradicts ----------------------


def test_n30_the_disclaimer_interpolates_the_actual_status(rich_quote) -> None:
    """N30: the note hard-coded `bankable_status=not_bankable` and is appended for EVERY status
    other than BANKABLE_CANDIDATE — including REVIEW_REQUIRED, which the MILP solver returns once
    every used assumption is confirmed. Latent only because today's library has none confirmed."""
    assert rich_quote.result.bankable_status is s.BankableStatus.REVIEW_REQUIRED
    note = next(w for w in rich_quote.quote.warnings if "bankable_status=" in w)
    assert "bankable_status=review_required" in note
    assert "bankable_status=not_bankable" not in note


def test_n30_a_not_bankable_run_still_says_not_bankable(rich_quote) -> None:
    """The interpolation must not soften the disclaimer on a genuinely not-bankable run."""
    result = rich_quote.result.model_copy(update={"bankable_status": s.BankableStatus.NOT_BANKABLE})
    quote = assemble_quote_record(
        rich_quote.site, result, rich_quote.case, load_assumptions("gb_v0_1")
    )
    assert any("bankable_status=not_bankable" in w for w in quote.warnings)


# --- N22: the CLI's contract text must match what the engine does -----------------------------


def test_n22_the_forecast_flag_no_longer_claims_it_is_never_priced_into_the_floor() -> None:
    """N22: `--forecast` said "gated upside; never priced into the floor" while
    `MilpEngine._final_result` prices the agreed export tariff into `value_streams` as CONTRACTED,
    which is inside `_PRICED_FIRMNESS` and so reaches P50/P90, the DSCR and both IRRs."""
    import inspect

    from gridq import cli

    # The real artifact a user sees: the option object typer renders into `gridq run --help`.
    help_text = inspect.signature(cli.run).parameters["forecast"].default.help
    assert "never priced into the floor" not in help_text
    assert "CONTRACTED" in help_text

    doc = cli._load_forecast_reference.__doc__ or ""
    assert "not purely gated upside" in doc
    assert "raise the priced floor" in doc


def test_n22_the_engine_exposes_a_priced_floor_note_hook() -> None:
    """The warning the quote carries when a forecast DID move the floor. ``None`` on the ordinary
    path (no forecast / no consent / no agreed tariff), which is byte-identical to before."""
    from gridq.optimisation import MilpEngine

    assert MilpEngine().last_forecast_priced_floor_note() is None
