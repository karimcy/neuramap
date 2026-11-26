"""End-to-end validation of power provisioning on an HGV-depot scenario (issue 017, P-E).

The scenario Karim asked to verify as *achievable and realistic*: a haulage depot (500 kVA
connection, evening base peak 320 kW) installs a managed 350 kW HGV charging bay used
weekday evenings 18:00-21:00 — demand the connection cannot deliver on top of the metered
load. The declared curve enters as the D42 manual 48-slot daily profile. SYNTHETIC,
ILLUSTRATIVE inputs (the demo discipline): every figure is hand-derivable from the fixture.

Hand-derived physics (fixture: load 60 overnight / 220 day / 320 weekday 16-19, cap 500):
- 18:00-19:00 unmet = 320 + 350 - 500 = 170 kW  (2 half-hours)
- 19:00-21:00 unmet = 220 + 350 - 500 =  70 kW  (4 half-hours)
- worst occurrence energy = (2x170 + 4x70) x 0.5 = 310 kWh; peak 170 kW
- floors at 10y end-of-tenor fade rise above day-one (fade from the release, never invented)

The whole pipeline must: size a battery that clears the floors, force-serve the window,
price the LOAD_SERVICE stream from the declared counterfactual only, measure the obligation's
opportunity cost with one extra solve, keep the bill metered-basis honest, and stay
deterministic end to end.
"""

from __future__ import annotations

from datetime import time

import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.optimisation.engine import MilpEngine
from gridq.quotes import generate_quote
from gridq.schemas import AvailabilityWindow, Firmness, MarketStream, ProvisioningRequirement

UNMET_PEAK_KW = 170.0
OCCURRENCE_KWH = 310.0


def _hgv_requirement() -> ProvisioningRequirement:
    profile = [0.0] * 48
    for slot in range(36, 42):  # 18:00-21:00 local: one managed 350 kW charger bay
        profile[slot] = 350.0
    return ProvisioningRequirement(
        requirement_id="hgv-depot-bay-1",
        windows=[
            AvailabilityWindow(
                label="weekday evening HGV charging",
                days_of_week=[0, 1, 2, 3, 4],
                start_time=time(18, 0),
                end_time=time(21, 0),
            )
        ],
        additional_load_profile_daily_kw=profile,
        tenor_years=10.0,
        counterparty="HGV Depot Ltd (SYNTHETIC illustrative scenario)",
        reference="contract-HGV-2026-EXAMPLE",
        declared_counterfactual_gbp_per_year=38_000.0,
        counterfactual_basis="SYNTHETIC illustrative figure standing in for a DNO "
        "reinforcement quote, annualised — replace with the site's real quote.",
    )


@pytest.fixture(scope="module")
def hgv_quote(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("hgv_e2e") / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, site_id="site_hgv_depot").model_copy(
        update={"provisioning_requirements": [_hgv_requirement()]}
    )
    lib = load_assumptions("gb_v0_1")
    engine = MilpEngine()
    run = generate_quote(site, lib, engine=engine)
    return site, lib, engine, run


def test_a_realistic_battery_is_selected_and_clears_the_floors(hgv_quote) -> None:
    _, _, _, run = hgv_quote
    power = run.result.selected_power_kw
    energy = run.result.selected_energy_kwh
    assert power is not None and energy is not None, "the HGV scenario must be achievable"
    # floors: >= day-one requirement (end-of-tenor uplift only raises them)
    assert power >= UNMET_PEAK_KW
    assert energy >= OCCURRENCE_KWH
    # realism: a depot-scale containerised unit, not a grid-scale monster
    assert power <= 500.0
    assert energy <= 4000.0


def test_the_obligation_is_served_and_the_stream_is_contracted(hgv_quote) -> None:
    _, _, engine, run = hgv_quote
    schedule = engine._dispatch_solver.last_schedule()
    assert schedule is not None and schedule.load_service_kw is not None
    assert float(schedule.load_service_kw.max()) == pytest.approx(UNMET_PEAK_KW)
    assert schedule.load_service_energy_mwh is not None and schedule.load_service_energy_mwh > 0
    ls = next(v for v in run.result.value_streams if v.stream == MarketStream.LOAD_SERVICE)
    assert ls.firmness == Firmness.CONTRACTED  # referenced customer contract
    assert ls.annual_revenue_gbp == pytest.approx(38_000.0)


def test_opportunity_cost_is_measured_and_persisted(hgv_quote) -> None:
    _, _, engine, run = hgv_quote
    cost = engine.last_provisioning_opportunity_cost()
    assert cost is not None and cost >= 0.0
    assert run.quote.provisioning_opportunity_cost_gbp == cost
    # the obligation binds real economics on this site: serving the window forgoes value
    assert cost > 0.0


def test_bill_stays_metered_basis_honest(hgv_quote) -> None:
    _, _, _, run = hgv_quote
    bill = run.quote.customer_bill
    assert bill is not None
    # the declared HGV load is NOT in the bill: before/after stay on the metered series
    assert "metered" in bill.basis


def test_artifacts_render_the_provisioning_story(hgv_quote) -> None:
    from gridq.outputs import generate_artifacts

    site, lib, _, run = hgv_quote
    artifacts = generate_artifacts(run.quote, site, assumptions=lib)
    # the memo's stream tables render title-cased ids; the declared value sits in the
    # CONTRACTED (priced-floor) table with the counterfactual basis in the notes
    assert "Load Service" in artifacts.memo_markdown
    assert "Load Service" in artifacts.proposal_markdown


def test_end_to_end_determinism(hgv_quote, tmp_path_factory: pytest.TempPathFactory) -> None:
    site, lib, _, run = hgv_quote
    rerun = generate_quote(site, lib, engine=MilpEngine())
    assert rerun.quote.quote_id == run.quote.quote_id
    assert rerun.quote.input_hash == run.quote.input_hash
    assert rerun.quote.provisioning_opportunity_cost_gbp == (
        run.quote.provisioning_opportunity_cost_gbp
    )
