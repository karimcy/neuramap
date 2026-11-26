"""Local-flex aggregator cut (issue 018 WS-C, C1/C2): net = gross x (1 - cut).

Precedence: a per-site ``ExportArrangement.aggregator_cut_pct`` wins; else the assumption
default ``local_flex_piclo.aggregator_cut_pct`` (gb_v1_5 = 0.20); else 0.0 (no cut). gb_v0_1 has
no key, so it applies no cut — existing goldens/traces are preserved.

**The base is derived, never written as a literal.** This file originally hard-coded
``GROSS = price x 100 MWh``, which silently encoded a 100% call rate: every in-window delivered
MWh treated as an instructed, paid DNO call. The F-19 parity fix routes both the proxy and the
engine through :func:`local_flex_call_rate`, so the payable volume is ``delivered x call_rate``
and every expectation here moved by 1/call_rate at once. Re-pinning those to new literals would
re-arm exactly the same trap the next time the sourced rate is re-versioned, so the base is now
resolved from the same helper the production path calls, and the assertions below are about the
**cut ratio**, which is what this file exists to test. ``test_f19_parity_...`` is the guard that
keeps that derivation honest: it fails if a 100% call rate ever comes back.
"""

from __future__ import annotations

from datetime import date

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions.loader import load_assumptions
from gridq.schemas import ExportArrangement
from gridq.schemas.enums import MarketStream
from gridq.value_stack.dispatch import DispatchSummary
from gridq.value_stack.gb_streams import local_flex_call_rate
from gridq.value_stack.interface import GateContext
from gridq.value_stack.registry import default_registry

REG = default_registry()
LIB5 = load_assumptions("gb_v1_5")
LIB0 = load_assumptions("gb_v0_1")
PICLO = LIB5.get("local_flex_piclo", "utilisation_price_gbp_per_mwh").value  # 150.0
MWH = 100.0
CUT_KEY = "local_flex_piclo.aggregator_cut_pct"


def _uncut_base(lib) -> float:  # type: ignore[no-untyped-def]
    """Revenue before any aggregator cut, derived from the release the run actually reads.

    Mirrors the production arithmetic: utilisation price x payable MWh, where payable is the
    delivered volume scaled by the sourced call rate. ``local_flex_utilisation_adjustment`` passes
    the volume straight through while ``contracted_mwh`` is None (no award, so no delivery ratio
    exists), and the availability leg is £0 until an award lands (D30), so neither adds a term.
    """
    price = lib.get("local_flex_piclo", "utilisation_price_gbp_per_mwh").value
    call_rate, _, _ = local_flex_call_rate(lib)
    return float(price) * MWH * call_rate


def _site(*, cut: float | None = None) -> s.SiteModel:
    site = s.SiteModel(
        site_id="site_cut",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
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
            import_rates={"red": 0.42, "amber": 0.21, "green": 0.14},
            duos_red_rate=0.18,
        ),
        connection=s.ConnectionDetails(mic_kw=300.0, dno_region="WPD_East_Midlands"),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )
    if cut is not None:
        site = site.model_copy(
            update={"export_arrangement": ExportArrangement(aggregator_cut_pct=cut)}
        )
    return site


def _price(site: s.SiteModel, lib):  # type: ignore[no-untyped-def]
    ctx = GateContext(local_flex_tender_open=True)
    dispatch = DispatchSummary(power_mw=2.0, duration_h=2.0, tender_window_discharge_mwh=MWH)
    return REG.get(MarketStream.LOCAL_FLEX_PICLO).value_function(dispatch, site, lib, None, ctx)


def test_assumption_default_cut_applied_on_gb_v1_5() -> None:
    v = _price(_site(), LIB5)
    assert v.annual_revenue_gbp == pytest.approx(_uncut_base(LIB5) * (1 - 0.20))
    assert CUT_KEY in v.assumptions_used


def test_no_cut_on_gb_v0_1_key_absent() -> None:
    v = _price(_site(), LIB0)
    assert v.annual_revenue_gbp == pytest.approx(_uncut_base(LIB0))  # no cut applied
    assert CUT_KEY not in v.assumptions_used


def test_site_override_beats_assumption_default() -> None:
    v = _price(_site(cut=0.5), LIB5)
    assert v.annual_revenue_gbp == pytest.approx(_uncut_base(LIB5) * 0.5)


def test_site_override_applies_even_when_release_has_no_key() -> None:
    v = _price(_site(cut=0.10), LIB0)
    assert v.annual_revenue_gbp == pytest.approx(_uncut_base(LIB0) * 0.90)


@pytest.mark.parametrize("cut", [0.0, 0.10, 0.5, 0.9])
def test_the_cut_scales_the_same_base_whatever_its_size(cut: float) -> None:
    """The subject of this file: net/base == 1 - cut, independent of what the base is.

    Deriving the base could make the four tests above pass against any arithmetic at all, so this
    asserts the relationship directly against a single measured reference — the uncut result from
    the release with no cut key — rather than against a recomputed expectation.
    """
    reference = _price(_site(), LIB0).annual_revenue_gbp
    assert reference > 0.0, "reference is £0; the ratio assertion below would be vacuous"
    assert _price(_site(cut=cut), LIB0).annual_revenue_gbp == pytest.approx(reference * (1.0 - cut))


def test_f19_parity_a_call_rate_is_applied_not_an_implicit_100_percent() -> None:
    """Guard on the derivation above: availability is NOT utilisation.

    Before F-19 the proxy path paid every in-window delivered MWh as an instructed DNO call while
    the engine applied the sourced rate, and the two disagreed by 20x on a CONTRACTED stream that
    feeds the priced floor, P50/P90 and the DSCR. Both clauses matter: without the second, a
    regression to a 100% rate would move the helper and the result together and go unnoticed.
    """
    for lib in (LIB5, LIB0):
        rate, _, _ = local_flex_call_rate(lib)
        assert 0.0 < rate < 1.0, f"call rate {rate} is not a rate; F-19 has regressed"

    naive_every_delivered_mwh_paid = PICLO * MWH
    assert _price(_site(), LIB0).annual_revenue_gbp < naive_every_delivered_mwh_paid
