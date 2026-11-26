"""N-1 — Capacity Market prequalification is decided from EVIDENCE, never from a sizing output.

`SI 2014/2043` reg 15(4) puts the 1 MW minimum on **"the connection capacity of the CMU"** (limb
(a), generating/interconnector) or **"the DSR capacity of the CMU"** (limb (b)).
`docs/bess_constants_register.md` row "Minimum capacity threshold for prequalification" carries the
same basis and the caveat "Applies to connection/DSR capacity, not de-rated capacity."

The defect these tests pin closed: because the binding eligibility test read the **sizing output**
(`schedule.power_mw`), a site recording **no MIC and no MEC** — so no evidenced connection at all
and no right to export a single kW, therefore incapable of being a generating CMU — was judged
*"eligible STANDALONE: the unit's 2.000 MW clears the 1.000 MW minimum"* and booked £11,924/yr with
firmness CONTRACTED, gate OPEN, inside `value_streams`: in the priced floor the DSCR covenant is
measured on. Measured on both pricing paths before the fix, £0 on both after it.

That is the exact inverse of D-92's own principle — consent, deliverability or absence can never
manufacture eligibility — read in the direction that ADDS revenue, which D45's no-null amendment
names as the case that must never be silent: "a null on the pricing path is a defect to fix, not a
state to report".

What must NOT change, and is pinned here too: the evidenced-but-short screen (a proven shortfall
stays CONDITIONAL with its DSR-CMU unlock route, never removed), and the customer's consent, which
stays RECORDED whether or not eligibility holds — conflating consent and eligibility in *either*
direction is a defect.
"""

from __future__ import annotations

import pathlib
from datetime import date

import numpy as np
import pytest
from tests._dpa_support import campaign_site_records, dpa_released_site

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import ConfidenceBand, Firmness, MarketStream, RateUnit
from gridq.value_stack import DispatchSummary, GateContext, default_registry, gates
from gridq.value_stack.gb_streams import (
    CM_UNEVIDENCED_ROUTE,
    capacity_market_cmu_capacity,
    capacity_market_entry_verdict,
    eligibility_threshold_mw,
)

LIB = load_assumptions("gb_v1_8")
REG = default_registry()
CM = MarketStream.CAPACITY_MARKET
#: Deliverability confirmed is production-reachable (`sizing/deliverability.py` builds it from a
#: site's declared CM commitment record), and it is what opened the gate in the measured defect.
CONFIRMED = GateContext(deliverability_confirmed=True)


def _connection(
    *, mic_kw: float = 0.0, mec_kw: float | None = None, asc_kw: float | None = None
) -> s.ConnectionDetails:
    """A connection record. ``mic_kw=0.0`` is the schema's "not declared" (the field is
    non-nullable, so zero is how an undeclared MIC is carried)."""
    return s.ConnectionDetails(mic_kw=mic_kw, mec_kw=mec_kw, asc_kw=asc_kw)


def _site(
    *,
    connection: s.ConnectionDetails | None = None,
    consents: tuple[s.RevenueStreamConsent, ...] = (),
    derived: list[s.DerivedAssumption] | None = None,
) -> s.SiteModel:
    return s.SiteModel(
        site_id="site_n1",
        site_name="N-1 capacity-evidence site",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_file_ref="examples/demo_site/hh_2025.csv",
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"day": 0.24, "night": 0.12},
        ),
        connection=connection if connection is not None else _connection(),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(footprint_available=True, access_ok=True),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
        stream_consents=list(consents),
        derived_assumptions=derived,
    )


def _cm_consent(rate_gbp_per_kw_yr: float = 20.0) -> s.RevenueStreamConsent:
    return s.RevenueStreamConsent(
        stream=CM,
        consented=True,
        agreed_rate_gbp=rate_gbp_per_kw_yr,
        agreed_rate_unit=RateUnit.GBP_PER_KW_YEAR,
    )


def _schedule(
    *, power_kw: float, deliverable_mw: float | None = 0.44
) -> HalfHourlyDispatchSchedule:
    n = 48
    zeros = np.zeros(n, dtype=float)
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=zeros,
        discharge_kw=zeros,
        soe_kwh=zeros,
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.full(n, 0.1),
        red_band_mask=np.zeros(n, dtype=bool),
        deliverable_capacity_mw=deliverable_mw,
    )


def _engine_cm(site: s.SiteModel, *, power_kw: float, deliverable_mw: float | None = 0.44):
    value_streams, gated, _ = build_value_streams(
        _schedule(power_kw=power_kw, deliverable_mw=deliverable_mw), site, LIB, context=CONFIRMED
    )
    entries = [v for v in (*value_streams, *gated) if v.stream == CM]
    assert len(entries) == 1, "the Capacity Market must be reported exactly once"
    return entries[0]


def _proxy_cm(site: s.SiteModel, *, power_mw: float, deliverable_mw: float | None = 0.44):
    return REG.get(CM).value_function(
        DispatchSummary(power_mw=power_mw, duration_h=2.0, deliverable_capacity_mw=deliverable_mw),
        site,
        LIB,
        context=CONFIRMED,
    )


def _declared(key: str, value: float, unit: str = "kW") -> s.DerivedAssumption:
    """A D45 `derived_input` record — a value derived offline, declared with its basis."""
    return s.DerivedAssumption(
        kind="derived_input",
        key=key,
        missing_input=f"the site's {key.split('.')[-1]}",
        value=value,
        unit=unit,
        basis="declared offline from the DNO connection offer, pending the signed agreement.",
        sources=["DNO connection offer (reference on file)"],
        derivation_method="sourced_default",
        derived_by="tests.test_cm_prequalification_capacity_basis",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="confirm the capacity on the signed connection agreement.",
    )


# --- the defect: absence must never resolve to eligible ----------------------


def test_a_site_with_no_connection_or_export_capacity_cannot_be_a_cmu() -> None:
    """No MIC and no MEC ⇒ the reg 15(4) question is unanswerable, so the gate REFUSES.

    UNKNOWN, not CONDITIONAL: UNKNOWN is the gate layer's own "could not be determined; treated
    conservatively (not priced)", and it is the only status that keeps the sized power away from
    the pricing functions entirely. CONDITIONAL would still be priced as visible upside.
    """
    gate = REG.get(CM).check_gates(_site(), LIB, CONFIRMED)
    assert gate.gate_status.value == "unknown"
    assert gate.can_be_priced_now is False
    assert gate.firmness == Firmness.EXCLUDED
    assert gate.firmness != Firmness.CONTRACTED
    blob = " ".join(gate.warnings)
    assert "REFUSED" in blob
    assert "SI 2014/2043" in blob and "reg 15(4)" in blob
    assert "ABSENT input must never resolve to ELIGIBLE" in blob
    # A refusal owes the reader the way out of it, not just the refusal.
    assert gate.missing_requirements and any("mec_kw" in m for m in gate.missing_requirements)
    assert gate.unlock_condition and "D45" in gate.unlock_condition


def test_the_unevidenced_case_earns_zero_on_both_pricing_paths() -> None:
    """The measured defect: £11,924/yr CONTRACTED in `value_streams` on both paths, now £0.

    The figure came from the clearing price × the engine-reserved capability, so it survived every
    de-rating check — only the eligibility basis was wrong.
    """
    site = _site()
    engine = _engine_cm(site, power_kw=2_000.0)
    assert engine.annual_revenue_gbp == 0.0
    assert engine.firmness == Firmness.EXCLUDED
    assert engine.gate_status == "unknown"
    proxy = _proxy_cm(site, power_mw=2.0)
    assert proxy.annual_revenue_gbp == 0.0
    assert proxy.firmness == Firmness.EXCLUDED
    # The false claim itself must be gone, not merely outweighed.
    for result in (engine, proxy):
        assert not any("eligible STANDALONE" in w for w in result.warnings)


@pytest.mark.parametrize("power_kw", [1_000.0, 2_000.0, 5_000.0, 50_000.0])
def test_a_sizing_outcome_can_never_supply_the_missing_capacity_evidence(power_kw: float) -> None:
    """However large the sized unit, an unevidenced connection stays ineligible.

    The sibling change that re-anchored the sizing box on the connection rather than observed load
    makes a ≥1 MW outcome materially more likely, which is precisely why the basis — not the
    threshold — had to be fixed.
    """
    verdict = capacity_market_entry_verdict(_site(), unit_mw=power_kw / 1000.0, portfolio_mw=None)
    assert verdict.eligible is False
    assert verdict.route == CM_UNEVIDENCED_ROUTE
    assert verdict.sellable_mw is None
    assert _engine_cm(_site(), power_kw=power_kw).annual_revenue_gbp == 0.0


def test_aggregation_cannot_rescue_an_unevidenced_site() -> None:
    """A DSR CMU aggregates EVIDENCED capacity; an unevidenced member contributes an unknown."""
    verdict = capacity_market_entry_verdict(_site(), unit_mw=5.0, portfolio_mw=50.0)
    assert verdict.eligible is False
    assert verdict.route == CM_UNEVIDENCED_ROUTE


def test_a_pending_permission_record_is_never_capacity_evidence() -> None:
    """D45's two classes must not leak into one another: a permission is not a fact about a wire."""
    permission = s.DerivedAssumption(
        kind="pending_permission",
        key="consents.g99",
        missing_input="G99 export permission",
        basis="not applied for; a permission is never derived from data.",
        derivation_method="not_derivable",
        derived_by="tests.test_cm_prequalification_capacity_basis",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="apply for G99 and record the outcome.",
    )
    site = _site(derived=[permission])
    assert capacity_market_cmu_capacity(site).mw is None
    assert REG.get(CM).check_gates(site, LIB, CONFIRMED).gate_status.value == "unknown"
    assert _engine_cm(site, power_kw=2_000.0).annual_revenue_gbp == 0.0


# --- consent and eligibility stay orthogonal, in both directions -------------


def test_consent_is_still_recorded_when_the_capacity_is_unevidenced() -> None:
    """Eligibility failing must not erase the authorisation the customer actually gave."""
    site = _site(consents=(_cm_consent(),))
    gate = REG.get(CM).check_gates(site, LIB, CONFIRMED)
    assert gate.gate_status.value == "unknown"
    assert gate.consent_status == "given"
    assert any("consent is RECORDED" in w for w in gate.warnings)
    result = _engine_cm(site, power_kw=2_000.0)
    assert result.consent_status == "given"
    # And the consented agreed rate buys nothing: consent authorises pursuit, not participation.
    assert result.annual_revenue_gbp == 0.0


# --- the two statutory routes ------------------------------------------------


@pytest.mark.parametrize(
    ("mec_kw", "eligible"),
    [(500.0, False), (999.0, False), (1_000.0, True), (4_000.0, True)],
)
def test_the_generating_route_is_bounded_by_the_export_capacity(
    mec_kw: float, eligible: bool
) -> None:
    """A generating CMU exports, so its connection capacity cannot exceed the site's MEC.

    A 5 MW battery behind a 0.5 MW export connection has 0.5 MW of connection capacity, not 5 —
    so the sized power cannot carry it over the 1 MW threshold.
    """
    site = _site(connection=_connection(mec_kw=mec_kw))
    capacity = capacity_market_cmu_capacity(site, unit_mw=5.0)
    assert capacity.route == "generating"
    assert capacity.mw == pytest.approx(min(5.0, mec_kw / 1000.0))
    verdict = capacity_market_entry_verdict(site, unit_mw=5.0, portfolio_mw=None)
    assert verdict.eligible is eligible
    priced = _proxy_cm(site, power_mw=5.0, deliverable_mw=1.0)
    assert (priced.annual_revenue_gbp > 0.0) is eligible


def test_the_dsr_route_reads_import_capacity_and_accepts_asc_as_well_as_mic() -> None:
    """DSR capacity is demand-side, so the import record evidences the route — ASC included.

    An ASC-only site was invisible to the old screen (it read only MIC and MEC), so a site with
    800 kW of ASC and nothing else reached OPEN ⇒ CONTRACTED on a ≥1 MW sizing outcome.
    """
    short = _site(connection=_connection(asc_kw=800.0))
    assert capacity_market_cmu_capacity(short).mw == pytest.approx(0.8)
    assert capacity_market_cmu_capacity(short).route == "dsr"
    assert REG.get(CM).check_gates(short, LIB, CONFIRMED).gate_status.value == "conditional"
    ample = _site(connection=_connection(asc_kw=2_000.0))
    assert REG.get(CM).check_gates(ample, LIB, CONFIRMED).gate_status.value == "open"


def test_the_dsr_route_says_when_its_figure_is_an_upper_bound() -> None:
    """No DSR capacity is recorded anywhere, so a unit above the import capacity is a bound, not a
    confirmation — and it must say so rather than read as evidence."""
    site = _site(connection=_connection(mic_kw=300.0))
    notes = " ".join(capacity_market_cmu_capacity(site, unit_mw=5.0).notes)
    assert "UPPER BOUND on the DSR route" in notes
    assert "Proven DSR Capacity derivation" in notes
    assert "0.300 MW" in notes


def test_a_d45_declared_capacity_is_accepted_and_labelled_as_declared() -> None:
    """D45: derive and declare, never invent silently — a declared capacity is usable but caps the
    band at LOW and is reported as "assumed, confirmation required", never as evidence."""
    site = _site(derived=[_declared("connection.mec_kw", 2_000.0)])
    capacity = capacity_market_cmu_capacity(site, unit_mw=2.0)
    assert capacity.mw == pytest.approx(2.0)
    assert capacity.route == "generating"
    notes = " ".join(capacity.notes)
    assert "DECLARED" in notes
    assert "confirmation required" in notes
    assert "LOW" in notes
    gate = REG.get(CM).check_gates(site, LIB, CONFIRMED)
    assert gate.gate_status.value == "open"


def test_a_declared_capacity_in_the_wrong_unit_is_refused_not_converted() -> None:
    """The key names a kW field; a record contradicting that is refused rather than guessed at."""
    site = _site(derived=[_declared("connection.mec_kw", 2.0, unit="MW")])
    assert capacity_market_cmu_capacity(site).mw is None


# --- what must not regress ---------------------------------------------------


def test_an_evidenced_but_short_connection_keeps_its_conditional_unlock_route() -> None:
    """The pre-existing screen is preserved: a PROVEN shortfall is CONDITIONAL, not refused.

    Something was disproven and an aggregated DSR CMU is a real route out, so the stream stays
    visible with its unlock condition — a materially different report line from "we cannot tell".
    """
    site = _site(connection=_connection(mic_kw=300.0))
    gate = REG.get(CM).check_gates(site, LIB, CONFIRMED)
    assert gate.gate_status.value == "conditional"
    blob = " ".join(gate.warnings)
    assert "NOT ELIGIBLE" in blob
    assert "PREQUALIFICATION bar" in blob
    assert "SI 2014/2043" in blob
    assert any("DSR CMU" in m for m in gate.missing_requirements)


def test_an_evidenced_eligible_cmu_still_prices_unchanged() -> None:
    """The fix removes only what is unevidenced: a 4 MW export connection prices as before."""
    site = _site(connection=_connection(mic_kw=300.0, mec_kw=4_000.0))
    result = _engine_cm(site, power_kw=2_000.0, deliverable_mw=0.44)
    clearing = LIB.get("capacity_market", "clearing_price_gbp_per_kw_yr").value
    assert result.annual_revenue_gbp == pytest.approx(clearing * 0.44 * 1000.0, abs=0.01)
    assert result.firmness == Firmness.CONTRACTED


def test_the_threshold_itself_is_unchanged_and_is_not_a_de_rated_quantity() -> None:
    """1 MW, on connection/DSR capacity. The register row bars testing it on de-rated capacity."""
    assert eligibility_threshold_mw(CM) == 1.0
    site = _site(connection=_connection(mec_kw=4_000.0))
    # A 2 MW / 2 h unit de-rates to ~0.44 MW; testing that against 1 MW would bar an eligible CMU.
    assert capacity_market_entry_verdict(site, unit_mw=2.0, portfolio_mw=None).eligible is True


# --- traceability: the reason must name the quantity it tested ---------------


def test_the_cm_verdict_reports_the_quantity_it_actually_tested() -> None:
    """A reader must not be told a UNIT figure under a CONNECTION-capacity rule.

    The generic predicate's default wording describes the sized unit, which is right for the
    markets that test the unit and wrong for the Capacity Market, whose threshold is a property of
    the CMU's evidenced connection or DSR capacity.
    """
    site = _site(connection=_connection(mec_kw=4_000.0))
    reasons = " ".join(capacity_market_entry_verdict(site, unit_mw=2.0, portfolio_mw=None).reasons)
    assert "the CMU's evidenced connection/DSR capacity of 2.000 MW clears" in reasons
    assert "the unit's 2.000 MW clears" not in reasons
    # The default is unchanged for the markets whose threshold IS on the unit.
    generic = gates.market_entry_eligibility(unit_mw=2.0, portfolio_mw=None, threshold_mw=1.0)
    assert (
        "eligible STANDALONE: the unit's 2.000 MW clears the 1.000 MW minimum." in generic.reasons
    )


def test_the_short_verdict_also_names_the_tested_quantity() -> None:
    site = _site(connection=_connection(mec_kw=500.0))
    reasons = " ".join(capacity_market_entry_verdict(site, unit_mw=5.0, portfolio_mw=None).reasons)
    assert "INELIGIBLE: the CMU's evidenced connection/DSR capacity of 0.500 MW is below" in reasons


# --- the live campaign: does any real site's verdict move? -------------------


def test_the_campaign_set_is_present() -> None:
    assert campaign_site_records(), "the real-site evidence set must be listed in the manifest"


@pytest.mark.parametrize("path", campaign_site_records(), ids=lambda p: p.stem)
def test_the_real_sites_verdicts_follow_their_own_recorded_capacity(path: pathlib.Path) -> None:
    """Every committed real site is judged on its own connection record, not on a sizing outcome.

    Asserted as the RULE rather than as a snapshot, so it stays true when a site's recorded
    capacity is updated: no evidence ⇒ refused; evidence below 1 MW ⇒ conditional with the unlock
    route; evidence at or above 1 MW ⇒ the prequalification screen does not block it.

    Each record is quarantined customer data (WP-GRC-05): it is read through the guard, and skips,
    naming why, until its customer's DPA is registered.
    """
    site = dpa_released_site(path, run_cross_checks=False)
    capacity = capacity_market_cmu_capacity(site)
    gate = REG.get(CM).check_gates(site, LIB, CONFIRMED)
    if capacity.mw is None:
        assert gate.gate_status.value == "unknown", path.stem
    elif capacity.mw < 1.0:
        assert gate.gate_status.value == "conditional", path.stem
        assert gate.unlock_condition, path.stem
    else:
        assert gate.gate_status.value in {"open", "conditional"}, path.stem
    # Whatever the verdict, it can never be CONTRACTED on unevidenced capacity.
    if capacity.mw is None:
        assert gate.firmness != Firmness.CONTRACTED, path.stem
