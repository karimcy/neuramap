"""Tests for the deterministic market-gating logic (the heart of M2's honesty).

Covers the task's required scenarios:
- P415 without an RTM partner is conditional (never firm).
- Piclo without tender info is conditional (DNO known) or excluded (no DNO).
- Capacity Market without confirmed deliverability is not firm.
- Tariff arbitrage without usable tariff data fails its gate.
- Gated upside can never be firm.
- DUoS / MIC-ASC gates require their data.
- Missing data produces warnings and missing requirements — never an invented value.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import Assumption, AssumptionStatus, load_assumptions
from gridq.schemas import Confidence, ConsentStatus, Firmness, MarketStream
from gridq.value_stack import GateStatus, default_registry
from gridq.value_stack import gates as G

_UNIT_FOR = {
    s.MarketStream.CAPACITY_MARKET: s.RateUnit.GBP_PER_KW_YEAR,
    s.MarketStream.BALANCING_MECHANISM: s.RateUnit.GBP_PER_MW_YEAR,
    s.MarketStream.ANCILLARY_DC_DM_DR: s.RateUnit.GBP_PER_MW_YEAR,
    s.MarketStream.LOCAL_FLEX_PICLO: s.RateUnit.GBP_PER_MWH,
    s.MarketStream.WHOLESALE_P415: s.RateUnit.GBP_PER_KWH,
}

LIB = load_assumptions("gb_v0_1")
REG = default_registry()


# --- site builders ---------------------------------------------------------


def _t2_load(*, peak: bool = True) -> s.LoadDataSummary:
    return s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0 if peak else None,
    )


def _site(
    *,
    multi_rate: bool = True,
    duos: bool = True,
    peak: bool = True,
    dno: str | None = "WPD_East_Midlands",
    mec_kw: float | None = None,
    g99: ConsentStatus = ConsentStatus.UNKNOWN,
    g100: ConsentStatus = ConsentStatus.UNKNOWN,
    export_mpan: str | None = None,
    stream_consents: tuple[s.RevenueStreamConsent, ...] | None = None,
    export_arrangement: s.ExportArrangement | None = None,
) -> s.SiteModel:
    rates = {"red": 0.42, "amber": 0.21, "green": 0.14} if multi_rate else {"single": 0.24}
    return s.SiteModel(
        site_id="site_gate",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=_t2_load(peak=peak),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates=rates,
            duos_red_rate=0.18 if duos else None,
        ),
        connection=s.ConnectionDetails(mic_kw=300.0, mec_kw=mec_kw, dno_region=dno),
        consents=s.ConsentState(g99=g99, g100=g100, export_mpan=export_mpan),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
        stream_consents=stream_consents,
        export_arrangement=export_arrangement,
    )


def _consent(
    stream: MarketStream, *, agreed_rate_gbp: float | None = None
) -> s.RevenueStreamConsent:
    # F-17: a rate is inadmissible without its unit. The helper supplies the unit the pipeline
    # actually consumes for each stream, so these fixtures exercise the real contract.
    unit = None if agreed_rate_gbp is None else _UNIT_FOR.get(stream, s.RateUnit.GBP_PER_YEAR)
    return s.RevenueStreamConsent(
        stream=stream, consented=True, agreed_rate_gbp=agreed_rate_gbp, agreed_rate_unit=unit
    )


def _export_ok_kwargs() -> dict[str, object]:
    """Kwargs that make ``export_dispatch_enabled`` true (MEC>0 ∧ G99/G100 ∧ export MPAN)."""
    return {
        "mec_kw": 250.0,
        "g99": ConsentStatus.APPROVED,
        "g100": ConsentStatus.APPROVED,
        "export_mpan": "MPAN-EXPORT-001",
    }


def _t0_site() -> s.SiteModel:
    """A T0 site: annual kWh only, no peak, single flat tariff, no DUoS, no DNO."""
    return s.SiteModel(
        site_id="site_t0",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        load_data=s.LoadDataSummary(annual_kwh=100_000.0),
        annual_consumption_kwh=100_000.0,
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


def _gate(stream_id: MarketStream, site: s.SiteModel):  # type: ignore[no-untyped-def]
    return REG.get(stream_id).check_gates(site, LIB)


# --- P415 / BM / ancillary (gated upside) ----------------------------------


def test_p415_without_rtm_partner_is_conditional() -> None:
    g = _gate(MarketStream.WHOLESALE_P415, _site())
    assert g.gate_status == GateStatus.CONDITIONAL
    assert g.firmness == Firmness.CONDITIONAL
    assert g.can_be_priced_now is False
    assert g.unlock_condition and "route-to-market" in g.unlock_condition.lower()
    assert any("route-to-market" in m.lower() for m in g.missing_requirements)


def test_bm_without_rtm_or_portfolio_is_conditional() -> None:
    g = _gate(MarketStream.BALANCING_MECHANISM, _site())
    assert g.gate_status == GateStatus.CONDITIONAL
    assert g.firmness != Firmness.FIRM
    assert any("portfolio" in m.lower() for m in g.missing_requirements)


def test_ancillary_without_rtm_or_portfolio_is_conditional() -> None:
    g = _gate(MarketStream.ANCILLARY_DC_DM_DR, _site())
    assert g.gate_status == GateStatus.CONDITIONAL
    assert g.firmness != Firmness.FIRM


def test_gated_upside_can_never_be_firm() -> None:
    # On a fully-populated site (even with export capability + approved consents), the
    # gated streams are still conditional upside — never the priced floor.
    site = _site(mec_kw=250.0, g99=ConsentStatus.APPROVED, g100=ConsentStatus.APPROVED)
    for sid in s.GATED_UPSIDE_STREAMS:
        g = _gate(sid, site)
        assert g.firmness != Firmness.FIRM
        assert g.gate_status != GateStatus.OPEN
        assert g.can_be_priced_now is False
    # the result schema also forbids a gated stream being marked firm
    with pytest.raises(ValidationError, match="cannot be marked firm"):
        s.ValueStreamResult(stream=MarketStream.WHOLESALE_P415, firmness=Firmness.FIRM)


def test_gated_upside_value_function_prices_nothing() -> None:
    site = _site()
    for sid in s.GATED_UPSIDE_STREAMS:
        v = REG.get(sid).value_function(None, site, LIB, None)
        assert v.annual_revenue_gbp == 0.0
        assert v.firmness != Firmness.FIRM
        assert v.unlock_condition


# --- Capacity Market -------------------------------------------------------


def test_cm_without_deliverability_is_not_firm() -> None:
    # D-92: the site must first be able to PREQUALIFY (≥1 MW of connection capacity, SI 2014/2043
    # reg 15). The default `_site()` has a 300 kW connection, which is barred outright — this test
    # is about the deliverability gate, so give it a connection that reaches it.
    g = _gate(MarketStream.CAPACITY_MARKET, _site(mec_kw=1_500.0))
    assert g.firmness != Firmness.FIRM
    assert g.gate_status == GateStatus.CONDITIONAL
    assert g.can_be_priced_now is False
    assert any("deliverability" in m.lower() for m in g.missing_requirements)
    assert g.unlock_condition and "deliverability" in g.unlock_condition.lower()


# --- local flex (Piclo) ----------------------------------------------------


def test_piclo_without_tender_with_dno_is_conditional() -> None:
    g = _gate(MarketStream.LOCAL_FLEX_PICLO, _site(dno="WPD_East_Midlands"))
    assert g.gate_status == GateStatus.CONDITIONAL
    assert g.firmness == Firmness.CONDITIONAL
    assert g.unlock_condition and "tender" in g.unlock_condition.lower()


def test_piclo_without_dno_is_excluded() -> None:
    g = _gate(MarketStream.LOCAL_FLEX_PICLO, _site(dno=None))
    assert g.gate_status == GateStatus.CLOSED
    assert g.firmness == Firmness.EXCLUDED
    assert any("dno" in m.lower() for m in g.missing_requirements)


def test_piclo_is_conditional_or_excluded_never_firm() -> None:
    for dno in (None, "WPD_East_Midlands"):
        g = _gate(MarketStream.LOCAL_FLEX_PICLO, _site(dno=dno))
        assert g.gate_status in (GateStatus.CONDITIONAL, GateStatus.CLOSED)
        assert g.firmness in (Firmness.CONDITIONAL, Firmness.EXCLUDED)


# --- tariff arbitrage ------------------------------------------------------


def test_tariff_arbitrage_on_a_flat_tariff_opens_the_gate_to_carry_the_round_trip_cost() -> None:
    """A flat rate has no spread to arbitrage, but charge energy is still bought at it.

    Amended 2026-08-17 (TARIFF-1). This previously asserted CLOSED/EXCLUDED, and that was the
    defect: `tariff_calendar.energy_price_series` deliberately returns a CONSTANT price array for a
    single rate (the F-01 fix), so the MILP objective carries a NEGATIVE energy term equal to the
    round-trip loss cost — but a CLOSED gate is EXCLUDED firmness, and `finance/cashflows.py` sums
    `annual_cost_gbp` only over FIRM/CONTRACTED streams, so that cost was dropped and the reported
    floor sat ABOVE the objective.

    "No spread" is a £0-REVENUE fact, not an absent input, so the gate opens and the cost lands.
    An absent tariff is a different thing and still closes the gate — see the test below.
    """
    g = _gate(MarketStream.TARIFF_ARBITRAGE, _site(multi_rate=False))
    assert g.gate_status == GateStatus.OPEN
    assert g.firmness == Firmness.FIRM
    assert g.can_be_priced_now is True
    assert not g.missing_requirements, "a flat tariff is present data, not missing data"
    assert any("NO PRICE SPREAD TO ARBITRAGE" in w for w in g.warnings)


def test_tariff_arbitrage_with_no_import_rates_at_all_still_closes_the_gate() -> None:
    """Flat is not absent. Only ABSENT may close this gate (the other half of TARIFF-1).

    With no `import_rates` the site's energy price is genuinely unknown and
    `energy_price_series` returns None, so the MILP has no energy term either — the closed gate and
    the objective agree at £0. Opening this case would be the F-01 defect in reverse.
    """
    site = _site(multi_rate=False)
    bare = s.SiteModel.model_validate(
        {
            **site.model_dump(mode="python"),
            "tariff_stack": site.tariff_stack.model_copy(update={"import_rates": {}}).model_dump(
                mode="python"
            ),
        }
    )
    g = _gate(MarketStream.TARIFF_ARBITRAGE, bare)
    assert g.gate_status == GateStatus.CLOSED
    assert g.firmness == Firmness.EXCLUDED
    assert g.can_be_priced_now is False
    assert g.missing_requirements
    assert any("tariff" in m.lower() for m in g.missing_requirements)


def test_tariff_arbitrage_with_usable_tariff_is_firm_floor() -> None:
    g = _gate(MarketStream.TARIFF_ARBITRAGE, _site(multi_rate=True))
    assert g.gate_status == GateStatus.OPEN
    assert g.firmness == Firmness.FIRM
    assert g.can_be_priced_now is True


# --- DUoS ------------------------------------------------------------------


def test_duos_without_rate_data_fails_gate() -> None:
    g = _gate(MarketStream.DUOS_RED_BAND_AVOIDANCE, _site(duos=False))
    assert g.gate_status == GateStatus.CLOSED
    assert g.firmness == Firmness.EXCLUDED
    assert any("duos" in m.lower() for m in g.missing_requirements)


def test_duos_with_rate_data_is_firm_floor() -> None:
    g = _gate(MarketStream.DUOS_RED_BAND_AVOIDANCE, _site(duos=True))
    assert g.gate_status == GateStatus.OPEN
    assert g.firmness == Firmness.FIRM


# --- MIC / ASC headroom ----------------------------------------------------


def test_mic_asc_headroom_without_peak_fails_gate() -> None:
    g = _gate(MarketStream.MIC_ASC_HEADROOM, _t0_site())
    assert g.gate_status == GateStatus.CLOSED
    assert g.firmness == Firmness.EXCLUDED
    assert any("peak" in m.lower() for m in g.missing_requirements)


def test_mic_asc_headroom_with_connection_and_peak_is_firm() -> None:
    g = _gate(MarketStream.MIC_ASC_HEADROOM, _site(peak=True))
    assert g.gate_status == GateStatus.OPEN
    assert g.firmness == Firmness.FIRM
    # priced only where the connection actually binds — surfaced as a warning
    assert any("bind" in w.lower() for w in g.warnings)


# --- missing data → warnings + missing requirements (never invented) -------


def test_missing_data_produces_warnings_and_requirements() -> None:
    """Genuinely ABSENT data must still produce a missing requirement, never an invented value.

    TARIFF_ARBITRAGE left the list 2026-08-17 (TARIFF-1): the `_t0_site` fixture carries a FLAT
    import tariff, which is present data with no spread rather than missing data, and that gate now
    deliberately OPENS so the round-trip energy cost reaches the reported floor. Its absent-data
    behaviour is covered by the no-import-rates test above — removing it from here narrows the claim
    to what is still true, rather than weakening it.
    """
    site = _t0_site()  # flat tariff, no DUoS, no peak, no DNO
    closed = [
        MarketStream.DUOS_RED_BAND_AVOIDANCE,
        MarketStream.MIC_ASC_HEADROOM,
        MarketStream.LOCAL_FLEX_PICLO,
    ]
    for sid in closed:
        g = _gate(sid, site)
        assert g.missing_requirements, f"{sid} should report missing requirements"
        assert g.warnings, f"{sid} should warn"
        assert g.can_be_priced_now is False


def test_floor_streams_firm_on_rich_site() -> None:
    site = _site(multi_rate=True, duos=True, peak=True)
    for sid in (
        MarketStream.PPA_OR_BILL_SAVINGS,
        MarketStream.TARIFF_ARBITRAGE,
        MarketStream.DUOS_RED_BAND_AVOIDANCE,
        MarketStream.MIC_ASC_HEADROOM,
    ):
        g = _gate(sid, site)
        assert g.gate_status == GateStatus.OPEN
        assert g.firmness == Firmness.FIRM
        assert g.can_be_priced_now is True


# --- predicate units: route-to-market / export consent ---------------------


def test_route_to_market_available_requires_confirmed_positive() -> None:
    # placeholder library value is zero + needs_review → no RTM partner
    placeholder = LIB.get("wholesale_p415", "gated_upside_value_gbp_per_mw_yr")
    assert G.route_to_market_available(placeholder) is False
    assert G.route_to_market_available(None) is False

    confirmed = Assumption(
        value=10000,
        unit="GBP/MW/yr",
        source="rtm partner",
        status=AssumptionStatus.CONFIRMED,
        owner_or_reviewer="Olly",
        confidence=Confidence.HIGH,
        last_reviewed=date(2026, 6, 17),
        notes="confirmed RTM value",
    )
    assert G.route_to_market_available(confirmed) is True


def test_export_consent_requires_mec_and_approved_consents() -> None:
    no_export = _site(mec_kw=None)
    assert G.has_export_consent(no_export) is False
    export_no_consent = _site(mec_kw=250.0)
    assert G.has_export_consent(export_no_consent) is False
    export_ok = _site(mec_kw=250.0, g99=ConsentStatus.APPROVED, g100=ConsentStatus.APPROVED)
    assert G.has_export_consent(export_ok) is True


def test_portfolio_threshold_not_met_for_single_site() -> None:
    assert G.meets_portfolio_threshold(portfolio_mw=None, threshold_mw=10.0) is False
    assert G.meets_portfolio_threshold(portfolio_mw=12.0, threshold_mw=10.0) is True


# --- per-stream consent predicates (Revenue-Data Drive / D43) ---------------


def test_stream_consented_reads_site_stream_consents() -> None:
    # No stream_consents at all → every stream is unconsented.
    bare = _site()
    for sid in MarketStream:
        assert G.stream_consented(bare, sid) is False
    # A consented stream reads True; a non-listed one stays False; consented=False stays False.
    site = _site(
        stream_consents=(
            _consent(MarketStream.CAPACITY_MARKET),
            s.RevenueStreamConsent(stream=MarketStream.BALANCING_MECHANISM, consented=False),
        )
    )
    assert G.stream_consented(site, MarketStream.CAPACITY_MARKET) is True
    assert G.stream_consented(site, MarketStream.BALANCING_MECHANISM) is False
    assert G.stream_consented(site, MarketStream.WHOLESALE_P415) is False


def test_agreed_rate_precedence_and_absence() -> None:
    # Absent everywhere → None (never guessed).
    assert G.agreed_rate(_site(), MarketStream.CAPACITY_MARKET) is None
    # Generic carrier (stream_consents.agreed_rate_gbp) is used for a stream with no dedicated home.
    site = _site(stream_consents=(_consent(MarketStream.CAPACITY_MARKET, agreed_rate_gbp=42.0),))
    assert G.agreed_rate(site, MarketStream.CAPACITY_MARKET) == 42.0
    # Dedicated ExportArrangement homes: export tariff (£/kWh) for wholesale, local-flex rate.
    arr = s.ExportArrangement(
        export_tariff_gbp_per_kwh=0.15, local_flex_agreed_rate_gbp_per_mwh=120.0
    )
    site2 = _site(export_arrangement=arr)
    assert G.agreed_rate(site2, MarketStream.WHOLESALE_P415) == 0.15
    assert G.agreed_rate(site2, MarketStream.LOCAL_FLEX_PICLO) == 120.0
    # The generic carrier wins over the dedicated home when both are present.
    site3 = _site(
        export_arrangement=arr,
        stream_consents=(_consent(MarketStream.WHOLESALE_P415, agreed_rate_gbp=0.99),),
    )
    assert G.agreed_rate(site3, MarketStream.WHOLESALE_P415) == 0.99


# --- consent-aware gating: unconsented is behaviour-identical (bar the None field) ---


def test_unconsented_gates_are_identical_except_consent_status_none() -> None:
    """An UNCONSENTED site's gate is byte-identical to HEAD except the new consent_status=None."""
    site = _site(**_export_ok_kwargs())  # rich site, export cohering, but NO stream_consents
    for sid in MarketStream:
        g = REG.get(sid).check_gates(site, LIB)
        assert g.consent_status is None
        # Gated upside stays conditional even with export cohering, because consent is absent.
        if sid in s.GATED_UPSIDE_STREAMS:
            assert g.gate_status == GateStatus.CONDITIONAL
            assert g.firmness == Firmness.CONDITIONAL
            assert g.can_be_priced_now is False


# --- consent-aware gating: a consented gated stream opens to CONTRACTED ------


def test_consented_bm_and_ancillary_open_to_contracted_with_label() -> None:
    # D-92: the site must ALSO be size-eligible for the market. The default `_site()` has a
    # 300 kW connection, which is below the 1 MW BM Unit / response-unit minimum, so consent alone
    # no longer opens the gate (see test_market_entry_eligibility.py). A 1.5 MW export capacity
    # makes the connection capable of presenting an eligible unit, which is what this test is about.
    for sid in (MarketStream.BALANCING_MECHANISM, MarketStream.ANCILLARY_DC_DM_DR):
        site = _site(stream_consents=(_consent(sid),), mec_kw=1_500.0)
        g = REG.get(sid).check_gates(site, LIB)
        assert g.gate_status == GateStatus.OPEN
        assert g.firmness == Firmness.CONTRACTED  # never FIRM
        assert g.can_be_priced_now is True
        assert g.consent_status == "given"


def test_consented_wholesale_opens_only_when_export_dispatch_coheres() -> None:
    sid = MarketStream.WHOLESALE_P415
    # Consent alone is not enough: wholesale export also needs export dispatch to cohere.
    consent_only = _site(stream_consents=(_consent(sid),))
    g1 = REG.get(sid).check_gates(consent_only, LIB)
    assert g1.gate_status == GateStatus.CONDITIONAL
    assert g1.firmness == Firmness.CONDITIONAL
    assert g1.consent_status == "given"  # label still stamped, orthogonally
    # Consent + export dispatch cohering → OPEN ⇒ CONTRACTED.
    full = _site(stream_consents=(_consent(sid),), **_export_ok_kwargs())
    g2 = REG.get(sid).check_gates(full, LIB)
    assert g2.gate_status == GateStatus.OPEN
    assert g2.firmness == Firmness.CONTRACTED
    assert g2.can_be_priced_now is True
    assert g2.consent_status == "given"


def test_consent_cannot_bypass_cm_deliverability() -> None:
    # CM keeps its deliverability physics: consent stamps the label but never opens the gate.
    site = _site(stream_consents=(_consent(MarketStream.CAPACITY_MARKET),))
    g = REG.get(MarketStream.CAPACITY_MARKET).check_gates(site, LIB)
    assert g.gate_status == GateStatus.CONDITIONAL
    assert g.firmness == Firmness.CONDITIONAL
    assert g.consent_status == "given"


def test_consented_floor_stream_stamps_label_without_changing_firmness() -> None:
    site = _site(stream_consents=(_consent(MarketStream.TARIFF_ARBITRAGE),))
    g = REG.get(MarketStream.TARIFF_ARBITRAGE).check_gates(site, LIB)
    assert g.gate_status == GateStatus.OPEN
    assert g.firmness == Firmness.FIRM  # unchanged
    assert g.consent_status == "given"
