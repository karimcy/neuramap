"""Two physics/completeness fixes in the dispatch: one warranty budget, and a proof that covers DFS.

**FIX 6 - RULE 2 OF THE PUBLISHED EXCLUSIVITY MATRIX, WIRED.**
`ancillary_services.study_basis_stream_exclusivity_matrix` had ZERO callers in `src/` (its only
reader was a test), which is the built-tested-never-called pattern CLAUDE.md records as having
shipped a dozen times. Rule 2 says: "Total annual discharge across ALL streams is capped by
warranty_cycle_limits.max_cycles_per_year_by_duration_h. Streams compete for one throughput budget."
The response service's own measured throughput
(`study_basis_availability_gbp_per_kw_yr_by_product.by_product.DCL.throughput_kwh_per_kw_yr` =
[58.1, 69.0] kWh/kW/yr) was priced into its RATE, net of GBP 21.3/MWh degradation, and charged
against NO throughput budget - so the audited campaign runs consumed 119-125 pct of a cap the MILP
treats as binding. It is deducted from `cap_rhs` now, exactly as the load-service obligation already
is.

THIS IS THE ONLY RULE WIRED, deliberately. D58 limb 1 waives the REGULATORY exclusivity and
expressly keeps "one annual warranty throughput budget" as the half enforced in the dispatch. Rules
1, 3, 4 and 7 are regulatory or eligibility rules D58 settles as waived and NOTHING in `src/` reads
them; this file pins that too, so a later session cannot quietly enforce one.

NO STREAM IS GATED, ZEROED OR HELD CONDITIONAL by any of this. The ancillary revenue is unchanged.
What changes is how much warranty headroom the priced dispatch has left to compete for, which is
physics: the cells have a finite throughput and a response delivered is a cycle spent.

**FIX 7 - THE AGREED-CAPACITY NETTING PROOF NOW COVERS DFS.**
`_floor_value` omitted `dfs_utilisation_value_gbp`. DFS is a priced-floor, dispatch-measured stream
worth 6.4-6.8 pct of revenue on the audited runs, so an overlay that gave up DFS delivery to shave
the annual peak had that sacrifice booked in both DFS and the capacity credit. The proof its own
docstring calls "arithmetic rather than assertion" must cover every stream in the floor.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import gridq.schemas as s
from _milp_support import multi_rate_site, write_hh_csv
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import (
    MilpDispatchSolver,
    _ancillary_response_throughput_kwh,
    _floor_value,
)
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.schemas import MarketStream

REPO = Path(__file__).resolve().parents[1]
RELEASE = "gb_v1_12"
LIB = load_assumptions(RELEASE)


def _site() -> s.SiteModel:
    """A SYNTHETIC site paid for the response service at the campaign's study-basis rate.

    GBP 16,600/MW/yr is the library's DCL net GBP 20.75/kW/yr less the 20 pct cut, the rate every
    campaign record carries; it is not a customer's figure. This used to read a campaign record
    that WP-GRC-05 quarantines until its DPA is registered.
    """
    consent = s.RevenueStreamConsent(
        stream=MarketStream.ANCILLARY_DC_DM_DR,
        consented=True,
        agreed_rate_gbp=16_600.0,
        agreed_rate_unit=s.RateUnit.GBP_PER_MW_YEAR,
        notes="SYNTHETIC: DCL net 20.75 GBP/kW/yr less the 20 pct cut.",
    )
    return s.SiteModel(
        site_id="SYNTHETIC-one-warranty",
        site_name="SYNTHETIC response-service site (not a real site)",
        postcode="ZZ1 1AA",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        load_data=s.LoadDataSummary(annual_kwh=1_250_000.0),
        annual_consumption_kwh=1_250_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="SYNTHETIC",
            import_rates={"single": 0.24},
        ),
        connection=s.ConnectionDetails(mic_kw=500.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="SYNTHETIC Operator A Ltd (not a real company)"),
        stream_consents=[consent],
    )


# ------------------------------------------------------------------- fix 6: ONE warranty budget


def test_the_response_services_own_throughput_is_charged_against_the_warranty_budget() -> None:
    """DCL's published throughput, at the D56 favourable (low) end, on the candidate's own kW.

    It dies if `_ancillary_response_throughput_kwh` stops being subtracted from ``cap_rhs``, and it
    dies if the product or the end of the range changes without the note changing with it.
    """
    site = _site()
    products = LIB.get(
        "ancillary_services", "study_basis_availability_gbp_per_kw_yr_by_product"
    ).value["by_product"]
    low, high = (float(v) for v in products["DCL"]["throughput_kwh_per_kw_yr"])
    assert (low, high) == (58.1, 69.0)
    # The product is the one the RATE was built from: DCL net 20.75 x (1 - 0.20) = 16.60 GBP/kW/yr,
    # which is the GBP 16,600/MW/yr the site record carries. Pinned so the two cannot drift apart.
    assert float(products["DCL"]["net_gbp_per_kw_yr"][0]) * 0.8 * 1000.0 == pytest.approx(
        float(site.agreed_rate_for(MarketStream.ANCILLARY_DC_DM_DR) or 0.0), abs=1.0
    )

    power_kw = 46.32
    charged, notes = _ancillary_response_throughput_kwh(
        site, LIB, power_kw=power_kw, annual_factor=1.0
    )
    assert charged == pytest.approx(low * power_kw, rel=1e-9)
    assert charged < high * power_kw, "the FAVOURABLE (low) end is charged, per D56"
    text = " ".join(notes)
    assert "ONE WARRANTY BUDGET ACROSS ALL STREAMS" in text
    assert "58.1-69" in text
    assert "THE ANCILLARY REVENUE IS UNCHANGED" in text
    # The never-called library key gets its first `src/` reader, and its rule travels with the note.
    assert "THE PUBLISHED RULE, VERBATIM" in text
    assert "Streams compete for one throughput budget" in text

    # It is a share of a REAL budget, not a token: 58.1 x 46.32 = 2,691 kWh against a 0.5 h cap of
    # 548 cycles x 23.16 kWh = 12,692 kWh, i.e. 21 pct of the cap the dispatch may now not spend.
    cap_cycles = float(
        LIB.get("warranty_cycle_limits", "max_cycles_per_year_by_duration_h").value["1"]
    )
    cap_kwh = cap_cycles * power_kw * 0.5
    assert 0.15 < charged / cap_kwh < 0.30

    # A proxy solve represents a whole year in a few weighted periods, so the ANNUAL figure is
    # divided by the annual factor exactly as the cap itself is - never charged at full year on a
    # horizon that is not one.
    scaled, _ = _ancillary_response_throughput_kwh(site, LIB, power_kw=power_kw, annual_factor=8.0)
    assert scaled == pytest.approx(charged / 8.0, rel=1e-9)


def test_a_site_not_paid_for_the_response_service_is_charged_nothing() -> None:
    """Charged only where the stream is PAID, mirroring the load-service obligation exactly.

    This is not an eligibility test on a revenue stream - it never reduces a revenue figure and can
    never zero one. It is the wear of a service, charged where the service is rendered.
    """
    site = _site()
    without = site.model_copy(
        update={
            "stream_consents": [
                c
                for c in (site.stream_consents or ())
                if c.stream != MarketStream.ANCILLARY_DC_DM_DR
            ]
        }
    )
    charged, notes = _ancillary_response_throughput_kwh(
        without, LIB, power_kw=46.32, annual_factor=1.0
    )
    assert charged == 0.0
    assert notes == []


def test_only_rule_2_of_the_exclusivity_matrix_is_wired() -> None:
    """Rules 1, 3, 4 and 7 are settled as waived by D58 limb 1 and must stay unread in `src/`.

    It dies the moment a later session enforces one of them, which is exactly the re-imposition of
    eligibility D57 forbids.
    """
    matrix = LIB.get("ancillary_services", "study_basis_stream_exclusivity_matrix").value
    assert set(matrix) >= {
        "rule_1_one_mw_sold_once",
        "rule_2_one_warranty_budget",
        "rule_3_dfs_exclusivity",
        "rule_4_response_mutual_exclusivity",
        "rule_7_response_aggregation_is_location_tied",
    }
    sources = "\n".join(path.read_text() for path in (REPO / "src" / "gridq").rglob("*.py"))
    assert "rule_2_one_warranty_budget" in sources, "rule 2 must have a production reader"
    for waived in (
        "rule_1_one_mw_sold_once",
        "rule_3_dfs_exclusivity",
        "rule_4_response_mutual_exclusivity",
        "rule_5_capacity_market_stacks_with_everything",
        "rule_6_dno_flex_has_no_exclusivity",
        "rule_7_response_aggregation_is_location_tied",
    ):
        assert waived not in sources, (
            f"{waived} is settled as WAIVED by D58 limb 1 and must not be enforced in src/"
        )


# ------------------------------------------------------- fix 7: the netting proof now covers DFS


def _schedule(*, dfs_value_kwh_rate: float | None) -> HalfHourlyDispatchSchedule:
    """A solved schedule with a measurable DFS leg, or none at all."""
    n = 48
    discharge = np.zeros(n)
    discharge[36:40] = 100.0
    price = None if dfs_value_kwh_rate is None else np.full(n, dfs_value_kwh_rate)
    return HalfHourlyDispatchSchedule(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=np.zeros(n),
        discharge_kw=discharge,
        soe_kwh=np.zeros(n),
        site_load_kw=np.full(n, 200.0),
        duos_rate_gbp_per_kwh=np.zeros(n),
        red_band_mask=np.zeros(n, dtype=bool),
        dfs_price_gbp_per_kwh=price,
    )


def test_the_netting_proof_counts_the_dfs_the_overlay_gives_up() -> None:
    """A schedule that earns DFS must value HIGHER in the floor than one that gives it up.

    That difference is exactly what the agreed-capacity overlay subtracts as ``sacrificed``. With
    DFS omitted the two schedules valued identically, so DFS given up to shave a peak was booked in
    both DFS and the capacity credit.

    It dies if `dfs_utilisation_value_gbp` is removed from `_floor_value`.
    """
    delivering = _schedule(dfs_value_kwh_rate=0.15971)
    gave_it_up = _schedule(dfs_value_kwh_rate=None)
    assert delivering.dfs_utilisation_value_gbp > 0.0
    assert gave_it_up.dfs_utilisation_value_gbp == 0.0
    sacrificed = _floor_value(delivering) - _floor_value(gave_it_up)
    assert sacrificed == pytest.approx(delivering.dfs_utilisation_value_gbp, rel=1e-9)
    assert sacrificed > 0.0, "the DFS leg must be visible to the netting proof"
    # Every dispatch-measured floor stream is in the proof, so it cannot go stale again silently.
    assert _floor_value(delivering) == pytest.approx(
        delivering.arbitrage_value_gbp
        + delivering.duos_avoidance_value_gbp
        + delivering.windowed_levy_avoidance_gbp
        + delivering.dfs_utilisation_value_gbp,
        rel=1e-12,
    )


# ---------------------------------------------- fix 6, THROUGH THE PRODUCTION SOLVE, not the helper


def test_the_warranty_cap_the_milp_enforces_falls_by_the_response_services_wear(tmp_path) -> None:
    """THE REACHABILITY TEST. The same problem, solved twice, differing only in the consent.

    A 4-hour battery wants roughly one cycle a day and the warranty caps it at 250 cycles/yr, so
    ``warranty_throughput_cap`` BINDS and the solved annual discharge sits on the cap. Adding the
    response consent must move that cap down by the service's own wear and nothing else.

    It dies if ``cap_rhs -= anc_throughput`` is deleted from `_solve_milp`: the two solves would
    then return identical throughput, which is the defect (119-125 pct of the budget spent).
    """
    path = tmp_path / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    base = multi_rate_site(path, rows=rows)
    power_kw, duration_h = 100.0, 4.0

    consent = s.RevenueStreamConsent(
        stream=MarketStream.ANCILLARY_DC_DM_DR,
        consented=True,
        agreed_rate_gbp=16_600.0,
        agreed_rate_unit=s.RateUnit.GBP_PER_MW_YEAR,
        notes="DCL net 20.75 GBP/kW/yr less the 20 pct cut, as on every campaign record.",
    )
    with_consent = base.model_copy(update={"stream_consents": [consent]})

    def _solve(site: s.SiteModel):
        return MilpDispatchSolver().solve(
            DispatchProblem(
                site_model=site,
                assumptions=LIB,
                power_kw=power_kw,
                energy_kwh=power_kw * duration_h,
                duration_hours=duration_h,
            )
        )

    without, charged_run = _solve(base), _solve(with_consent)
    assert "warranty_throughput_cap" in without.constraints_binding
    charged, _notes = _ancillary_response_throughput_kwh(
        with_consent, LIB, power_kw=power_kw, annual_factor=1.0
    )
    assert charged > 0.0
    lost = (without.annual_discharge_kwh or 0.0) - (charged_run.annual_discharge_kwh or 0.0)
    assert lost == pytest.approx(charged, rel=0.02), (
        "the consented run must give up exactly the response service's own throughput"
    )
    # And the reader is told, on the record the reader reads.
    assert any("ONE WARRANTY BUDGET ACROSS ALL STREAMS" in w for w in (charged_run.warnings or ()))
    assert not any("ONE WARRANTY BUDGET ACROSS ALL STREAMS" in w for w in (without.warnings or ()))
