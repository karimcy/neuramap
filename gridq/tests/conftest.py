"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.governance.dpa_guard import iter_waiver_modules, legacy_validation_read

# ---------------------------------------------------------------------------------------------
# WP-GRC-05: the read-only legacy validation waiver (Fable 5.2 ruling Q3).
#
# The customer files already in git are quarantined: the DPA guard refuses to load them. The test
# modules listed in src/gridq/governance/customer_data_manifest.json (legacy_validation_waiver)
# validate the engine against those files, so for exactly those modules, and only while one of
# their tests (or their import) runs, this conftest opens `legacy_validation_read`. Every read
# through it is logged as ``quarantined_legacy_unevidenced``; it never permits a store; only this
# file can open it; no environment variable or flag can. The list may only shrink
# (tests/governance/test_customer_data_scan.py pins its length).
# ---------------------------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
_WAIVED = frozenset(iter_waiver_modules())


def _waiver_module(path: Any) -> str | None:
    try:
        rel = Path(str(path)).resolve().relative_to(_REPO).as_posix()
    except ValueError:
        return None
    return rel if rel in _WAIVED else None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: Any) -> Generator[None, None, None]:
    module = _waiver_module(item.path)
    if module is None:
        yield
        return
    with legacy_validation_read(module):
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Generator[None, None, None]:
    module = _waiver_module(collector.path) if isinstance(collector, pytest.Module) else None
    if module is None:
        yield
        return
    with legacy_validation_read(module):
        yield


def _make_site(*, with_max_import: bool = True) -> s.SiteModel:
    """Build a minimal valid SiteModel for engine/pipeline tests.

    ``with_max_import=True`` → a T2 site whose load_data carries max_import_kw;
    ``False`` → a T0 site with no max_import_kw (to exercise the insufficient-data path).
    """
    if with_max_import:
        load = s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            # A T2 site must actually HAVE the series it claims. This fixture previously declared
            # 17,520 rows with no file behind them — the exact fiction hostile-review finding F-10
            # names (declared load metadata never reconciled with the file the solver prices) and a
            # site that could never satisfy validation Gate 2. It now points at the committed demo
            # series, whose shape matches the declared metadata.
            half_hourly_file_ref="examples/demo_site/hh_2025.csv",
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        )
        tier = s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    else:
        load = s.LoadDataSummary(annual_kwh=100_000.0)  # no max_import_kw
        tier = s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE

    return s.SiteModel(
        site_id="site_test",
        postcode="LE17 4XN",
        data_tier=tier,
        load_data=load,
        annual_consumption_kwh=1_800_000.0 if with_max_import else 100_000.0,
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


@pytest.fixture
def make_site() -> Callable[..., s.SiteModel]:
    """Return the SiteModel factory (call with ``with_max_import=True|False``)."""
    return _make_site


@dataclass(frozen=True)
class RichQuoteFixture:
    """A clearly-SYNTHETIC, illustrative run for exercising output rendering end-to-end.

    NOT a real quote and never flows into a real pipeline: it pairs a hand-built result
    (a firm/contracted/conditional value-stream mix + a gated-upside entry) with injected
    `DebtTerms`/`DispatchEnergy` so DSCR and the price-to-target populate. It stays **not bankable**
    because `gb_v0_1` is all placeholders — exactly the honest v1 state, with the figures present so
    the artifacts can be tested with real (record-derived) numbers.
    """

    quote: s.QuoteRecord
    site: s.SiteModel
    case: s.FinancialCase
    result: s.OptimisationResult
    floor_total_gbp: float


def _build_rich_quote() -> RichQuoteFixture:
    from datetime import date

    from gridq.assumptions import load_assumptions
    from gridq.finance import DebtTerms, DispatchEnergy, run_financial_model
    from gridq.quotes import assemble_quote_record

    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    site = s.SiteModel(
        site_id="site_synth",
        site_name="Synthetic Demo Site (illustrative)",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
            levies={"cclevy": 0.01},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(footprint_available=True, access_ok=True),
        counterparty=s.Counterparty(
            legal_name="Synthetic Demo Ltd",
            company_number="09999999",
            sector="manufacturing",
            credit_status=s.CreditStatus.ADEQUATE,
            expected_tenure_years=10.0,
        ),
    )
    result = s.OptimisationResult(
        result_id="res_synth",
        request_id="req_synth",
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
                gate_status="met",
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.DUOS_RED_BAND_AVOIDANCE,
                annual_revenue_gbp=12000.0,
                firmness=s.Firmness.FIRM,
                gate_status="met",
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=9000.0,
                firmness=s.Firmness.CONTRACTED,
                gate_status="deliverable",
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.LOCAL_FLEX_PICLO,
                annual_revenue_gbp=5000.0,
                firmness=s.Firmness.CONDITIONAL,
                gate_status="tender_open",
                unlock_condition="Piclo tender award",
            ),
            s.ValueStreamResult(
                stream=s.MarketStream.MIC_ASC_HEADROOM,
                annual_revenue_gbp=0.0,
                firmness=s.Firmness.EXCLUDED,
                gate_status="not_priced",
            ),
        ],
        gated_upside=[
            s.ValueStreamResult(
                stream=s.MarketStream.WHOLESALE_P415,
                annual_revenue_gbp=0.0,
                firmness=s.Firmness.CONDITIONAL,
                gate_status="gated",
                unlock_condition="RTM partner + export consents",
            ),
        ],
        solver_status=s.SolverStatus.MOCK,
    )
    assumptions = load_assumptions("gb_v0_1")
    case = run_financial_model(
        site,
        result,
        assumptions,
        debt_terms=DebtTerms(gearing_pct=70.0, annual_rate_pct=7.0, tenor_years=10),
        dispatch_energy=DispatchEnergy(annual_discharge_kwh=400_000.0),
    ).case
    quote = assemble_quote_record(
        site,
        result,
        case,
        assumptions,
        quote_mode=s.QuoteMode.INDICATIVE,
        # The Gate-2 verdict a real run of this site would carry (N21): 17,520 rows over a full
        # calendar year at T2 is the metered basis. Stamped explicitly because this fixture
        # bypasses `generate_quote`, and the lifecycle gate now READS the basis — an unstamped
        # record is honestly "basis unknown" and cannot be advanced to underwritten. Both fields
        # are excluded from the canonical hash input, so the fixture's quote_id is unmoved.
        half_hourly_basis=s.HalfHourlyBasis.METERED,
        confidence_ceiling=s.ConfidenceBand.HIGH,
    )
    # 38000 (firm) + 12000 (firm) + 9000 (contracted); conditional/gated held out of the floor.
    return RichQuoteFixture(
        quote=quote, site=site, case=case, result=result, floor_total_gbp=59000.0
    )


@pytest.fixture
def rich_quote() -> RichQuoteFixture:
    """A synthetic, illustrative quote with a fully-populated `FinancialCase`."""
    return _build_rich_quote()
