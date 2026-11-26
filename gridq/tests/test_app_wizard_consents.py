"""D8 (issue 018) — consents & grid-arrangements as a frontend-declared input step.

Locks in: the wizard has the consents pane (6 steps), the real form carries the same fields,
`_site_from_form` maps them onto ConsentState / SupplyBoundary / ExportArrangement, a blank form
leaves `export_arrangement` None (hash-stable), and the UI percent aggregator cut is stored as a
0-1 fraction. Consent state is a declared input, never a backend block (issue 018 section 9).

Revenue-Data Drive / Slice 3 (D43) extends this: BOTH forms carry a per-stream consent control
(`consent_<stream>` + `consent_rate_<stream>` for the streams with no dedicated agreed-rate home),
the wizard gains the real-form parity fields (supplier_consent, asc_kw, export_counterparty,
local_flex_agreed_rate), `_site_from_form` maps the ticked consents onto
`SiteModel.stream_consents`, a submission with no consents ticked keeps `stream_consents=None`
(hash-stable), and a consented submission carries "consent given" all the way into pricing.
"""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

import gridq.schemas as s
from gridq.app.app import _site_from_form, create_app
from gridq.quotes.assembly import _canonical_site_dump


def _client() -> TestClient:
    return TestClient(create_app())


def test_wizard_has_consents_pane_and_six_steps() -> None:
    r = _client().get("/new-quote")
    assert r.status_code == 200
    assert 'data-step="6"' in r.text  # Review renumbered to step 6
    assert 'data-pane="5"' in r.text  # the new consents pane
    for field in ('name="g98"', 'name="export_route"', 'name="import_mpan"'):
        assert field in r.text


def test_real_form_carries_consents_and_export_fields() -> None:
    r = _client().get("/real/haywood-oaks")
    assert r.status_code == 200
    for field in ('name="g98"', 'name="export_route"', 'name="aggregator_cut_pct"'):
        assert field in r.text


def test_site_from_form_maps_consents_and_export_arrangement() -> None:
    site = _site_from_form(
        {
            "site_name": "T",
            "postcode": "NG21 0PE",
            "annual_consumption_kwh": "215000",
            "max_import_kw": "421",
            "mic_kw": "500",
            "mec_kw": "500",
            "g98": "approved",
            "g99": "approved",
            "g100": "approved",
            "export_mpan": "1900000000001",
            "import_mpan": "1900000000000",
            "export_route": "flexibility_aggregator",
            "aggregator_cut_pct": "20",
        },
        None,
    )
    assert site.consents.g98.value == "approved"
    assert site.consents.export_mpan == "1900000000001"
    assert site.supply_boundary.import_mpan == "1900000000000"
    assert site.export_arrangement.route.value == "flexibility_aggregator"
    assert site.export_arrangement.aggregator_cut_pct == 0.20  # UI percent 20 -> 0.20 fraction


def test_blank_form_leaves_export_arrangement_none_hash_stable() -> None:
    site = _site_from_form(
        {
            "site_name": "T",
            "postcode": "NG1 1AA",
            "annual_consumption_kwh": "100000",
            "max_import_kw": "50",
            "mic_kw": "100",
        },
        None,
    )
    assert site.export_arrangement is None


# --- Slice 3 (D43): per-stream consent intake -------------------------------


def test_both_forms_carry_per_stream_consent_controls() -> None:
    """Wizard + real form render the per-stream consent checkboxes, the agreed-rate inputs for the
    three streams with no dedicated rate home, and the "not a guarantee" helper text."""
    for url in ("/new-quote", "/real/haywood-oaks"):
        text = _client().get(url).text
        # a floor stream (checkbox only) and the three rate-bearing gated/CM streams
        for field in (
            'name="consent_tariff_arbitrage"',
            'name="consent_capacity_market"',
            'name="consent_rate_capacity_market"',
            'name="consent_rate_balancing_mechanism"',
            'name="consent_rate_ancillary_dc_dm_dr"',
        ):
            assert field in text, f"{field} missing from {url}"
        # the streams whose rate lives on ExportArrangement get a checkbox but NO generic rate input
        assert 'name="consent_local_flex_piclo"' in text
        assert 'name="consent_rate_local_flex_piclo"' not in text
        assert 'name="consent_rate_wholesale_p415"' not in text
        assert "it is not a guarantee" in text


def test_wizard_gains_real_form_parity_fields() -> None:
    """The wizard can now produce the same SiteModel as the real form: supplier_consent, asc_kw,
    export_counterparty and the local-flex agreed rate are all present on the wizard."""
    text = _client().get("/new-quote").text
    for field in (
        'name="supplier_consent"',
        'name="asc_kw"',
        'name="export_counterparty"',
        'name="local_flex_agreed_rate_gbp_per_mwh"',
    ):
        assert field in text, f"{field} missing from the wizard"


def test_site_from_form_maps_per_stream_consents_and_agreed_rates() -> None:
    site = _site_from_form(
        {
            "site_name": "T",
            "postcode": "NG21 0PE",
            "annual_consumption_kwh": "215000",
            "max_import_kw": "421",
            "mic_kw": "500",
            "mec_kw": "500",
            "asc_kw": "480",
            "supplier_consent": "approved",
            "export_route": "flexibility_aggregator",
            "export_counterparty": "Acme Flex Ltd",
            "aggregator_cut_pct": "20",
            "local_flex_agreed_rate_gbp_per_mwh": "150",
            # per-stream consents: a floor stream (label only), CM + BM with agreed rates
            "consent_tariff_arbitrage": "on",
            "consent_capacity_market": "on",
            "consent_rate_capacity_market": "50",
            "consent_balancing_mechanism": "on",
            "consent_rate_balancing_mechanism": "10000",
            "consent_local_flex_piclo": "on",
        },
        None,
    )
    # parity fields land where 2a/2b expect them
    assert site.consents.supplier_consent_state.value == "approved"
    assert site.connection.asc_kw == 480.0
    assert site.export_arrangement.counterparty == "Acme Flex Ltd"
    assert site.export_arrangement.local_flex_agreed_rate_gbp_per_mwh == 150.0
    # per-stream consents (canonically sorted by stream value)
    assert site.consented_streams == frozenset(
        {
            s.MarketStream.TARIFF_ARBITRAGE,
            s.MarketStream.CAPACITY_MARKET,
            s.MarketStream.BALANCING_MECHANISM,
            s.MarketStream.LOCAL_FLEX_PICLO,
        }
    )
    # agreed rates carried only where an input was supplied; the floor/local-flex ones stay None
    assert site.agreed_rate_for(s.MarketStream.CAPACITY_MARKET) == 50.0
    assert site.agreed_rate_for(s.MarketStream.BALANCING_MECHANISM) == 10_000.0
    assert site.agreed_rate_for(s.MarketStream.TARIFF_ARBITRAGE) is None
    assert site.agreed_rate_for(s.MarketStream.LOCAL_FLEX_PICLO) is None


def test_no_consents_ticked_leaves_stream_consents_none_hash_stable() -> None:
    """A submission with no per-stream consents keeps `stream_consents=None`; the canonical run
    input POPS the field so the input hash is byte-stable vs a pre-drive site (mirror of the
    blank-export-arrangement guarantee)."""
    site = _site_from_form(
        {
            "site_name": "T",
            "postcode": "NG1 1AA",
            "annual_consumption_kwh": "100000",
            "max_import_kw": "50",
            "mic_kw": "100",
        },
        None,
    )
    assert site.stream_consents is None
    assert "stream_consents" not in _canonical_site_dump(site)


def _schedule() -> object:
    """A minimal synthetic schedule with a de-rated deliverable capacity, mirroring the 2b tests."""
    from gridq.optimisation.schedule import HalfHourlyDispatchSchedule

    n = 48
    arr = lambda v: np.asarray(v, dtype=float)  # noqa: E731
    return HalfHourlyDispatchSchedule(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=arr([0.0] * n),
        discharge_kw=arr([0.0] * n),
        soe_kwh=arr([0.0] * n),
        site_load_kw=arr([100.0] * n),
        duos_rate_gbp_per_kwh=arr([0.1] * n),
        red_band_mask=np.zeros(n, dtype=bool),
        energy_price_gbp_per_kwh=None,
        deliverable_capacity_mw=0.2,
        tender_window_discharge_mwh=None,
    )


def test_consent_from_intake_flows_into_pricing_end_to_end() -> None:
    """A fully-consented intake submission -> a SiteModel whose consented CM stream prices at the
    customer-agreed rate and carries consent_status="given" through the value stack (the intake
    end of the gate->price->floor chain wired in 2a/2b)."""
    from gridq.assumptions import load_assumptions
    from gridq.optimisation.valuation import build_value_streams

    site = _site_from_form(
        {
            "site_name": "T",
            "postcode": "NG21 0PE",
            "annual_consumption_kwh": "215000",
            "max_import_kw": "421",
            "mic_kw": "500",
            "consent_capacity_market": "on",
            "consent_rate_capacity_market": "50",
        },
        None,
    )
    value_streams, _, _ = build_value_streams(_schedule(), site, load_assumptions("gb_v0_1"))
    cm = next(v for v in value_streams if v.stream == s.MarketStream.CAPACITY_MARKET)
    # D-92 (2026-08-12): this previously asserted £10,000/yr (agreed £50/kW/yr × 0.2 MW × 1000) and
    # was WRONG. A 0.2 MW unit on a 500 kW connection cannot prequalify for the Capacity Market at
    # all, so that £10,000 was ineligible revenue entering the priced floor, the P50/P90 series and
    # the DSCR. The stream now prices £0 and says why.
    #
    # What this test is actually FOR is the consent chain (intake -> gate -> price -> floor), and
    # that half still holds: consent is recorded even though it buys nothing. Consent and
    # eligibility are orthogonal, and conflating them is exactly what put the ineligible £ in the
    # floor. Consent authorises pursuit; it never manufactures a right to participate.
    assert cm.annual_revenue_gbp == 0.0
    assert cm.consent_status == "given"
    assert cm.gate_status == "conditional"
    # A £0 must never be a bare zero: an ungrounded zero reads exactly like a measured result.
    assert any("ELIGIBILITY" in w for w in cm.warnings), (
        "an ineligible stream priced at £0 must state the eligibility reason"
    )
