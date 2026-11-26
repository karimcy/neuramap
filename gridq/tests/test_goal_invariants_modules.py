"""Goal invariants for the remaining WS1 module rows — M1 / M2 / M6 / X.1 / X.2 / X.3 / M8 / CLI.

Executable form of the docs/goal/spec worklist rows whose coverage said "none found"
(manual_ch01-08.md, manual_ch09-17.md, docs_md.md). One section per module. Rows already
covered elsewhere are cited in the section comments rather than re-tested:

- M2 no-market-branch guard: ``test_goal_invariants_platform.py::test_core_never_compares_
  against_a_gb_literal``; no private constants: ``test_finance_cashflows.py`` (capex/opex/
  haircut not hidden constants) + ``test_finance_credit.py::test_library_driven_no_private_
  constants``.
- X.2 golden repro (same pins -> identical hash): ``test_determinism_golden.py`` (all three).
- M8/CLI double-render byte-identity: ``test_outputs_numbers_match_record.py::
  test_regeneration_is_byte_identical`` + ``test_goal_invariants_platform.py::
  test_cli_render_is_byte_identical_across_invocations``.
- M1 negative tariff rates/levies rejected (INV-M1-09): ``test_goal_invariants_platform.py::
  test_negative_tariff_rates_are_rejected``.
- M6 scenario determinism: ``test_scenario_engine.py::test_weighted_summary_is_deterministic``,
  ``test_scenario_forecast_prices.py::test_forecast_backed_set_is_deterministic``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import AssumptionStatus, assess_reprice, load_assumptions
from gridq.assumptions.models import SECTION_NAMES
from gridq.cli import app
from gridq.finance import run_financial_model
from gridq.finance.cashflows import build_cashflow_model, revenue_by_stream
from gridq.finance.financial_model import capital_recovery_account
from gridq.outputs import build_view_model
from gridq.outputs.formatting import pass_fail
from gridq.provenance import hash_json, stable_json_dumps
from gridq.quotes.telemetry import QUOTE_CREATED_EVENT_TYPE, emit_quote_telemetry
from gridq.scenarios import build_forecast_backed_scenario_set, build_standard_scenario_set
from gridq.scenarios.engine import run_scenario_set
from gridq.schemas.dispatch_profile import DispatchProfilePoint
from gridq.telemetry import ManualBenchmarkRecord, ManualQuoteType
from gridq.telemetry.analytics import summarise_telemetry
from gridq.telemetry.manual_benchmark import MANUAL_BENCHMARK_EVENT_TYPE

REPO = Path(__file__).resolve().parents[1]
ASSUMPTION_DIR = REPO / "assumptions"
EXAMPLES = REPO / "examples"
FORECAST_FIXTURE = Path(__file__).parent / "fixtures" / "modo_sample_forecast.csv"

runner = CliRunner()

_BANKABLE = {
    "bankable_status": s.BankableStatus.BANKABLE_CANDIDATE,
    "implementation_level": s.ImplementationLevel.PRODUCTION,
}


def _site() -> s.SiteModel:
    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    return s.SiteModel(
        site_id="site_goal_mod",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _result_with_stream_mix() -> s.OptimisationResult:
    """A synthetic TEST result: firm + contracted + conditional + excluded + gated streams."""
    return s.OptimisationResult(
        result_id="res_goal_mod",
        request_id="req_goal_mod",
        implementation_level=s.ImplementationLevel.IN_PROGRESS,
        bankable_status=s.BankableStatus.REVIEW_REQUIRED,
        selected_power_kw=500.0,
        selected_energy_kwh=1000.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.TARIFF_ARBITRAGE,
                annual_revenue_gbp=38000.0,
                firmness=s.Firmness.FIRM,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=9000.0,
                firmness=s.Firmness.CONTRACTED,
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.LOCAL_FLEX_PICLO,
                annual_revenue_gbp=5000.0,
                firmness=s.Firmness.CONDITIONAL,
                unlock_condition="Piclo tender award",
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.MIC_ASC_HEADROOM,
                annual_revenue_gbp=99999.0,  # excluded => must never be counted anywhere
                firmness=s.Firmness.EXCLUDED,
            ),
        ],
        gated_upside=[
            s.ValueStreamResult(
                stream=s.MarketStream.WHOLESALE_P415,
                annual_revenue_gbp=0.0,
                firmness=s.Firmness.CONDITIONAL,
                unlock_condition="RTM partner + export consents",
            ),
        ],
        solver_status=s.SolverStatus.MOCK,
    )


# =============================================================================
# M1 — SiteModel / schema and data contracts (manual ch03)
# =============================================================================


def test_t0_site_without_any_annual_figure_is_rejected() -> None:
    """INV-M1-07: a T0-graded site must carry an annual consumption figure or fail validation."""
    with pytest.raises(ValidationError, match="annual"):
        s.SiteModel(
            site_id="site_t0_bare",
            postcode="LE17 4XN",
            data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
            load_data=s.LoadDataSummary(),  # no annual_kwh
            annual_consumption_kwh=None,
            tariff_stack=s.TariffStack(
                basis=SYNTHETIC_BASIS,
                provenance=SYNTHETIC_PROVENANCE,
                supplier="X",
                import_rates={"single": 0.24},
            ),
            connection=s.ConnectionDetails(mic_kw=100.0),
            consents=s.ConsentState(),
            physical_gates=s.PhysicalGateStatus(),
            counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
        )


def test_the_two_documented_negative_exceptions_and_only_those() -> None:
    """INV-M1-04: headroom fields and FinancialCase cashflows may be negative; power may not."""
    # Exception 1: connection headroom is a signed margin (negative = over-subscribed).
    conn = s.ConnectionDetails(mic_kw=300.0, import_headroom_kw=-25.0, export_headroom_kw=-10.0)
    assert conn.import_headroom_kw == -25.0
    assert conn.export_headroom_kw == -10.0
    # Exception 2: FinancialCase cashflows (year 0 is the capex outflow).
    case = s.FinancialCase(
        case_id="case_neg",
        p50_cashflows=[-500000.0, 120000.0],
        p90_cashflows=[-500000.0, 100000.0],
        dscr_summary=s.DscrSummary(),
        capital_recovery=capital_recovery_account([-500000.0, 120000.0]),
        debt_assumptions=s.DebtAssumptions(),
        price_recommendation=s.PriceRecommendation(),
        covenant_status=s.CovenantStatus(),
    )
    assert case.p50_cashflows[0] < 0
    # The rule itself still holds everywhere else: a negative capacity is rejected.
    with pytest.raises(ValidationError):
        s.ConnectionDetails(mic_kw=-1.0)


def test_an_archetype_field_is_a_validation_error_never_a_silent_pass() -> None:
    """INV-M1-08: no archetype/site-type field exists; smuggling one in is rejected."""
    assert "archetype" not in s.SiteModel.model_fields
    valid = _site().model_dump()
    valid["archetype"] = "office"
    with pytest.raises(ValidationError, match="archetype"):
        s.SiteModel.model_validate(valid)


def test_dispatch_profile_stores_two_non_negative_series_not_a_signed_one() -> None:
    """INV-M1-14 (as ratified — see FINDINGS F-009): the persisted dispatch convention is two
    non-negative series, ``charge_kw`` and ``discharge_kw``, per half hour; a negative value in
    either is rejected, and no signed per-point power field exists on the contract."""
    fields = set(DispatchProfilePoint.model_fields)
    assert {"charge_kw", "discharge_kw"} <= fields
    assert "power_kw" not in fields  # no signed single series on the point contract
    point = dict(
        period=0,
        charge_kw=50.0,
        discharge_kw=0.0,
        soe_kwh=100.0,
        site_load_kw=120.0,
        duos_rate_gbp_per_kwh=0.05,
        red_band=False,
    )
    DispatchProfilePoint(**point)  # the good case validates
    for field in ("charge_kw", "discharge_kw"):
        with pytest.raises(ValidationError):
            DispatchProfilePoint(**{**point, field: -1.0})


# =============================================================================
# M2 — value stack: value accounting sums exactly to the total (outputs/finance side;
# the dispatch-side partition is pinned in test_goal_invariants_dispatch.py)
# =============================================================================


def test_finance_revenue_total_is_exactly_the_sum_of_the_by_stream_map() -> None:
    """INV-M1-15 / ch09 caution: per-stream values sum exactly to the reported total — the
    total is derived from the by-stream map (never stored independently), with no slack line."""
    site, result = _site(), _result_with_stream_mix()
    warnings: list[str] = []
    by_stream = revenue_by_stream(result, warnings=warnings)
    # Only the priced (firm + contracted) streams appear; nothing else leaks in.
    assert by_stream == {
        s.MarketStream.TARIFF_ARBITRAGE.value: 38000.0,
        s.MarketStream.CAPACITY_MARKET.value: 9000.0,
    }
    model = build_cashflow_model(site, result, load_assumptions("gb_v0_1"), horizon_years=15)
    assert model.annual_revenue_by_stream_gbp == by_stream
    assert model.total_annual_revenue_gbp == round(sum(by_stream.values()), 2) == 47000.0


def test_outputs_floor_and_upside_totals_partition_the_record_streams_exactly(
    rich_quote,
) -> None:
    """ch03 value accounting at the outputs seam: the artifact totals are exact sums of the
    record's own stream revenues, every stream lands in exactly one bucket, and an excluded
    stream is counted in neither total. Gated entries join the upside only on the firm
    (underwritten+) path — the indicative pack never cites them (INV-M8-10)."""
    result = rich_quote.result
    firm_contracted = [
        v.annual_revenue_gbp
        for v in result.value_streams
        if v.firmness in (s.Firmness.FIRM, s.Firmness.CONTRACTED)
    ]
    conditional = [
        v.annual_revenue_gbp for v in result.value_streams if v.firmness == s.Firmness.CONDITIONAL
    ]
    gated = [v.annual_revenue_gbp for v in result.gated_upside]

    # Indicative mode: value_streams partition exactly; gated entries are deliberately absent.
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    assert vm.split.floor_total_gbp == round(sum(firm_contracted), 2) == 59000.0
    assert vm.split.upside_total_gbp == round(sum(conditional), 2)
    n_rows = len(vm.split.floor) + len(vm.split.upside) + len(vm.split.excluded)
    assert n_rows == len(result.value_streams)
    excluded_streams = {r.stream for r in vm.split.excluded}
    assert s.MarketStream.MIC_ASC_HEADROOM.value in excluded_streams

    # Underwritten mode: the gated register joins the upside and the partition is total.
    underwritten = rich_quote.quote.model_copy(update={"quote_mode": s.QuoteMode.UNDERWRITTEN})
    vm_uw = build_view_model(underwritten, rich_quote.site)
    assert vm_uw.split.floor_total_gbp == round(sum(firm_contracted), 2)
    assert vm_uw.split.upside_total_gbp == round(sum(conditional) + sum(gated), 2)
    n_rows_uw = len(vm_uw.split.floor) + len(vm_uw.split.upside) + len(vm_uw.split.excluded)
    assert n_rows_uw == len(result.value_streams) + len(result.gated_upside)


# =============================================================================
# M6 — scenario engine (manual ch11)
# =============================================================================


@dataclass
class _BaseCase:
    p50_cashflows: list[float]
    p90_cashflows: list[float]


def test_scenario_p90_never_exceeds_p50_in_every_case_and_the_weighted_summary() -> None:
    """P90 <= P50 ordering survives the scenario engine: element-wise in every evaluated case
    and in the probability-weighted summary (ch12 rebuild invariant applied through ch11)."""
    base = _BaseCase(
        p50_cashflows=[-500000.0] + [120000.0 - 1500.0 * y for y in range(10)],
        p90_cashflows=[-500000.0] + [0.85 * (120000.0 - 1500.0 * y) for y in range(10)],
    )
    scenario_set = build_standard_scenario_set(
        load_assumptions("gb_v0_1"),
        low_price_multiplier=0.9,
        high_price_multiplier=1.15,
        low_cost_multiplier=1.05,
        high_cost_multiplier=0.98,
    )
    run = run_scenario_set(base, scenario_set)
    for result in run.results:
        for p90, p50 in zip(result.p90_cashflows, result.p50_cashflows, strict=True):
            assert p90 <= p50
        assert result.total_p90_gbp <= result.total_p50_gbp
    summary = run.weighted_summary
    for p90, p50 in zip(
        summary.weighted_p90_cashflows, summary.weighted_p50_cashflows, strict=True
    ):
        assert p90 <= p50
    assert summary.weighted_total_p90_gbp < summary.weighted_total_p50_gbp


def test_scenario_edge_cases_warn_and_never_fake(rich_quote) -> None:
    """INV-M6-08: a non-negative year 0 and an operating-year-free base each warn loudly;
    neither invents a figure and neither raises."""
    scenario_set = build_standard_scenario_set(
        load_assumptions("gb_v0_1"), low_price_multiplier=0.9, high_price_multiplier=1.1
    )
    # Non-negative year 0: sign-convention warning on every evaluated case.
    run = run_scenario_set(_BaseCase([100.0, 50.0], [100.0, 40.0]), scenario_set)
    assert all(any("year-0 cashflow is non-negative" in w for w in r.warnings) for r in run.results)
    # A base with no operating years: the price multiplier is a warned no-op, never a fake.
    run2 = run_scenario_set(_BaseCase([-100.0], [-100.0]), scenario_set)
    low = next(r for r in run2.results if r.scenario_id == "low")
    assert any("no operating-year cashflows" in w for w in low.warnings)
    assert low.p50_cashflows == [-100.0]  # cost multiplier defaulted; nothing invented


def test_forecast_reference_never_sets_a_multiplier() -> None:
    """INV-M6-11: a pinned forecast is provenance for gated upside only — Low/High price and
    cost multipliers stay explicit caller input and default to identity, never derived from
    the file."""
    scenario_set = build_forecast_backed_scenario_set(
        load_assumptions("gb_v0_1"),
        forecast_file=FORECAST_FIXTURE,
        source="Modo Energy",
        forecast_version="modo-2026-q2-sample",
    )
    assert all(scn.forecast_ref is not None for scn in scenario_set.scenarios)
    assert all(scn.price_multiplier == 1.0 for scn in scenario_set.scenarios)
    assert all(scn.cost_multiplier == 1.0 for scn in scenario_set.scenarios)


# =============================================================================
# X.1 — assumption library (manual ch05, Appendix B)
# =============================================================================


def test_every_shipped_release_is_unconfirmed_and_its_quotes_stay_not_bankable() -> None:
    """INV-X1-13 (placeholder never bankable), swept across every shipped release file: each
    release still carries non-confirmed entries, and a financial case built on it is not
    bankable with a warning naming a non-confirmed assumption."""
    site, result = _site(), _result_with_stream_mix()
    release_files = sorted(ASSUMPTION_DIR.glob("*.yaml"))
    assert release_files, "no assumption releases found"
    for path in release_files:
        lib = load_assumptions(path.stem)
        statuses = [
            entry.status for section in SECTION_NAMES for entry in getattr(lib, section).values()
        ]
        assert any(status != AssumptionStatus.CONFIRMED for status in statuses), path.stem
        case = run_financial_model(site, result, lib).case
        assert case is not None
        assert not any("bankable_candidate" in w for w in case.warnings)
        model = build_cashflow_model(site, result, lib, horizon_years=15)
        assert model.bankable is False, f"{path.stem} produced a bankable case on placeholders"
        assert model.uses_unconfirmed_assumptions is True
        assert any("not bankable" in w for w in model.warnings), path.stem


def test_gb_v0_1_values_match_appendix_b_exactly() -> None:
    """INV-X1-15: the frozen gb_v0_1 release pins the Appendix B numbers byte-for-byte —
    a released file is never edited in place, so these can never drift."""
    lib = load_assumptions("gb_v0_1")
    pins = {
        ("battery_capex", "installed_cost"): 500000,
        ("battery_capex", "installed_cost_benchmark_band"): {"low": 300000, "high": 900000},
        ("battery_opex", "annual_fixed_opex"): 12000,
        ("degradation", "annual_capacity_fade_pct"): 2.5,
        ("round_trip_efficiency", "ac_round_trip"): 0.88,
        ("parasitic_load", "standby_kw_per_mw"): 5.0,
        ("warranty_cycle_limits", "max_cycles_per_year_by_duration_h"): {
            "1": 548,
            "2": 365,
            "4": 250,
        },
        ("financing", "target_equity_irr_pct"): 12.0,
        ("dscr_covenants", "min_p90_dscr"): 1.30,
        ("p50_p90_haircuts", "p90_revenue_haircut_pct"): 15.0,
        ("p50_p90_haircuts", "perfect_foresight_haircut_pct"): 20.0,
        ("capacity_market", "clearing_price_gbp_per_kw_yr"): 30.0,
        ("capacity_market", "de_rating_factor_by_duration_h"): {"1": 0.40, "2": 0.65, "4": 0.95},
        ("local_flex_piclo", "utilisation_price_gbp_per_mwh"): 150.0,
        ("quote_validity", "indicative_validity_days"): 30,
        ("quote_validity", "reprice_tolerance_pct"): 5.0,
    }
    for (section, name), expected in pins.items():
        assert lib.get(section, name).value == expected, f"{section}.{name}"


def test_a_move_away_from_exactly_zero_is_always_material() -> None:
    """INV-X1-08: a leaf moving off exactly zero has no meaningful relative move, so it is
    always a breach (infinite change), however small the new value and whatever the tolerance."""
    priced = load_assumptions("gb_v0_1")
    current = priced.model_copy(deep=True)
    entry = current.wholesale_p415["gated_upside_value_gbp_per_mw_yr"]
    assert entry.value == 0  # the gated placeholder really is zero on this release
    entry.value = 0.0001
    check = assess_reprice(priced, current)
    assert check.needs_reprice is True
    breach = next(m for m in check.breaches if "wholesale_p415" in m.key)
    assert breach.change_pct == float("inf")
    assert "wholesale_p415" in check.summary()


# =============================================================================
# X.2 — provenance / canonical hashing / quote identity (manual ch06, ch13)
# =============================================================================


def test_canonical_hashing_raises_on_an_unhandled_type_never_skips() -> None:
    """INV-X2-04: an unhandled type is a hard error — a silent skip would make two different
    objects hash identically. Sets render sorted so equal-by-value objects hash equal."""
    with pytest.raises(TypeError, match="cannot serialise"):
        stable_json_dumps(object())
    with pytest.raises(TypeError):
        hash_json({"x": complex(1, 2)})
    with pytest.raises(TypeError):
        hash_json({"payload": b"raw-bytes"})
    assert stable_json_dumps({"s": {3, 1, 2}}) == '{"s":[1,2,3]}'


def test_quote_without_an_assumption_version_is_never_presented_bankable(rich_quote) -> None:
    """INV-X1-07 (ch05): 'a quote with no version can never be bankable' — the outputs layer
    downgrades a claimed-bankable record with no assumption_version stamp, with a warning,
    exactly as it does for a missing P50/P90/DSCR deliverable (fixed here; see FINDINGS F-010)."""
    bankable_result = rich_quote.result.model_copy(update=_BANKABLE)
    stamped = rich_quote.quote.model_copy(update={"optimisation_result": bankable_result})
    unstamped = stamped.model_copy(update={"assumption_version": None})

    vm_stamped = build_view_model(stamped, rich_quote.site)
    assert vm_stamped.bankable is True  # positive control: trio + version present

    vm = build_view_model(unstamped, rich_quote.site)
    assert vm.bankable is False
    assert any("assumption" in w.lower() and "version" in w.lower() for w in vm.warnings)


# =============================================================================
# X.3 — telemetry (manual ch14)
# =============================================================================


def test_no_priced_number_ever_enters_a_telemetry_event(rich_quote) -> None:
    """INV-X3-02 / ch14 keyidea: the event records how, never what — no field can carry a
    priced figure, none of the record's pounds appear in the emitted event, and a smuggled
    revenue field is a validation error."""
    banned_tokens = ("gbp", "price", "revenue", "cost", "capex", "dscr", "irr", "cashflow")
    for name in s.TelemetryEvent.model_fields:
        assert not any(token in name for token in banned_tokens), name

    event = emit_quote_telemetry(
        rich_quote.quote, rich_quote.site, contributor="Olly", human_minutes=12.0
    )
    dump = event.model_dump_json()
    priced_figures = [
        v.annual_revenue_gbp
        for v in (*rich_quote.result.value_streams, *rich_quote.result.gated_upside)
        if v.annual_revenue_gbp
    ] + [rich_quote.floor_total_gbp]
    for figure in priced_figures:
        assert str(int(figure)) not in dump, f"priced figure {figure} leaked into telemetry"

    with pytest.raises(ValidationError):
        s.TelemetryEvent(
            event_id="e1",
            quote_id="q1",
            event_type=QUOTE_CREATED_EVENT_TYPE,
            annual_revenue_gbp=38000.0,  # type: ignore[call-arg]
        )


def test_telemetry_aggregation_is_order_independent_and_repeatable() -> None:
    """INV-X3-08 + the ordering guarantee: the summary is pure aggregation — the same events
    produce the identical summary on a re-run and under any event ordering (hit-rate keys
    iterate in sorted order, so no dict-order leak)."""

    def _event(i: int, etype: str, minutes: float, outcome: s.QuoteOutcome, tier: s.DataTier):
        return s.TelemetryEvent(
            event_id=f"e{i}",
            quote_id=f"q{i}",
            event_type=etype,
            data_tier=tier,
            human_minutes=minutes,
            outcome=outcome,
        )

    t0 = s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
    t2 = s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    events = [
        _event(1, QUOTE_CREATED_EVENT_TYPE, 10.0, s.QuoteOutcome.WON, t2),
        _event(2, QUOTE_CREATED_EVENT_TYPE, 20.0, s.QuoteOutcome.LOST, t2),
        _event(3, QUOTE_CREATED_EVENT_TYPE, 30.0, s.QuoteOutcome.WON, t0),
        _event(4, MANUAL_BENCHMARK_EVENT_TYPE, 120.0, s.QuoteOutcome.PENDING, t0),
        _event(5, MANUAL_BENCHMARK_EVENT_TYPE, 240.0, s.QuoteOutcome.PENDING, t2),
    ]
    baseline = summarise_telemetry(events)
    assert summarise_telemetry(events) == baseline  # repeatable
    assert summarise_telemetry(list(reversed(events))) == baseline  # order-independent
    assert summarise_telemetry(events[2:] + events[:2]) == baseline
    assert baseline.time_compression_ratio == 9.0  # 180 benchmark mean / 20 system mean
    assert list(baseline.hit_rate_by_data_tier) == sorted(baseline.hit_rate_by_data_tier)


def test_lifecycle_state_and_benchmark_artifact_label_are_distinct_vocabularies() -> None:
    """INV-X3-09: ManualQuoteType (the artifact a hand-built quote produced) and QuoteMode
    (the lifecycle state) are different enums on different objects and are never conflated."""
    assert ManualQuoteType is not s.QuoteMode
    manual_values = {m.value for m in ManualQuoteType}
    mode_values = {m.value for m in s.QuoteMode}
    assert manual_values != mode_values
    assert "proposal" in manual_values and "proposal" not in mode_values
    assert "draft" in mode_values and "draft" not in manual_values
    # Neither telemetry object carries a lifecycle-state field.
    assert "quote_mode" not in s.TelemetryEvent.model_fields
    assert "quote_mode" not in ManualBenchmarkRecord.model_fields
    assert ManualBenchmarkRecord.model_fields["quote_type"].annotation is ManualQuoteType


# =============================================================================
# M8 — outputs (manual ch15; byte-identity + no-arithmetic + bankable guard are cited
# in the module docstring — verified green alongside this file)
# =============================================================================


def test_unevaluated_covenant_renders_not_evaluated_never_a_pass() -> None:
    """INV-M8-07 (verdict half): a covenant verdict of None renders 'not evaluated' —
    never silently a pass, never a guess."""
    assert "not evaluated" in pass_fail(None)
    assert pass_fail(True) == "PASS"
    assert pass_fail(False) == "FAIL"


# =============================================================================
# CLI (manual ch17)
# =============================================================================


def test_unknown_assumption_version_is_a_fatal_input_error(tmp_path, monkeypatch) -> None:
    """INV-CLI-01: an unknown assumption version exits 2 (a fatal input error) — the CLI
    never silently substitutes another release."""
    from tests._dpa_support import register_fixture_dpas

    # A DPA refusal (exit 3) would otherwise come first (WP-GRC-05); this pins the input error.
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_000900")
    result = runner.invoke(
        app, ["run", str(EXAMPLES / "site_t0.json"), "--assumptions", "gb_v9_9_missing"]
    )
    assert result.exit_code == 2
    assert "gb_v9_9_missing" in result.output
