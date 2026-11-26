"""The engine and proxy local-flex paths must price the SAME £ — F-19 parity, pinned.

The engine path (`optimisation.valuation._local_flex_value`) applied the sourced 5% DNO call rate;
the proxy path (`value_stack.gb_streams.LocalFlexPicloStream._price`) applied none. The two
therefore differed by **20x** on a CONTRACTED stream that feeds the priced floor, the P50/P90
series and the DSCR — while `_local_flex_value`'s docstring claimed "parity with the proxy".

That parity claim was entirely unpinned: nothing in the suite compared the two paths, and nothing
anywhere referenced `utilisation_call_rate` or a call rate at all, so the F-19 fix landed with zero
coverage. These tests pin both halves:

1. the call-rate resolver itself (the sourced default, the library override, and rejection of
   out-of-range values), and
2. **path parity** — same site, same library, same delivered MWh ⇒ identical £, for the
   unconsented case, the consented agreed-rate case, and with an aggregator cut.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus, load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import (
    _DEFAULT_LOCAL_FLEX_CALL_RATE,
    _local_flex_call_rate,
    build_value_streams,
)
from gridq.schemas import RateUnit
from gridq.value_stack import DispatchSummary, GateContext, default_registry
from gridq.value_stack.gb_streams import local_flex_call_rate

TENDER_MWH = 40.0
POWER_KW = 400.0


def _schedule() -> HalfHourlyDispatchSchedule:
    n = 48
    arr = lambda v: np.asarray(v, dtype=float)  # noqa: E731
    return HalfHourlyDispatchSchedule(
        power_kw=POWER_KW,
        energy_kwh=POWER_KW * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=arr([0.0] * n),
        discharge_kw=arr([0.0] * n),
        soe_kwh=arr([0.0] * n),
        site_load_kw=arr([100.0] * n),
        duos_rate_gbp_per_kwh=arr([0.1] * n),
        red_band_mask=np.zeros(n, dtype=bool),
        tender_window_discharge_mwh=TENDER_MWH,
    )


def _dispatch() -> DispatchSummary:
    """The proxy read-model of the very same dispatched quantities."""
    return DispatchSummary(
        power_mw=POWER_KW / 1000.0,
        duration_h=2.0,
        tender_window_discharge_mwh=TENDER_MWH,
    )


def _site(
    make_site: Callable[..., s.SiteModel],
    *,
    consents: tuple[s.RevenueStreamConsent, ...] = (),
    export_arrangement: s.ExportArrangement | None = None,
) -> s.SiteModel:
    base = make_site()
    update: dict[str, object] = {
        "stream_consents": consents,
        "connection": base.connection.model_copy(update={"dno_region": "east_midlands"}),
    }
    if export_arrangement is not None:
        update["export_arrangement"] = export_arrangement
    return s.SiteModel.model_validate({**base.model_dump(mode="python"), **update})


def _set_call_rate(lib: AssumptionLibrary, value: float) -> None:
    """Inject a `local_flex_piclo.utilisation_call_rate` entry into a loaded library."""
    lib.local_flex_piclo["utilisation_call_rate"] = Assumption(
        value=value,
        unit="fraction",
        source="test injection",
        status=AssumptionStatus.NEEDS_REVIEW,
        owner_or_reviewer="Olly",
        confidence=s.Confidence.LOW,
        last_reviewed=date(2026, 8, 12),
        notes="test-only override of the call rate.",
    )


def _engine_flex(site: s.SiteModel, lib: AssumptionLibrary) -> float:
    value_streams, gated, _ = build_value_streams(_schedule(), site, lib)
    entry = next(v for v in (*value_streams, *gated) if v.stream == s.MarketStream.LOCAL_FLEX_PICLO)
    return entry.annual_revenue_gbp


def _proxy_flex(site: s.SiteModel, lib: AssumptionLibrary) -> float:
    stream = default_registry().get(s.MarketStream.LOCAL_FLEX_PICLO)
    return stream.value_function(_dispatch(), site, lib, None, None).annual_revenue_gbp


# --- the call-rate resolver (F-19 had ZERO coverage before this) --------------


def test_call_rate_defaults_to_the_sourced_dno_figure() -> None:
    lib = load_assumptions("gb_v0_1")
    rate, used, note = local_flex_call_rate(lib)
    assert rate == _DEFAULT_LOCAL_FLEX_CALL_RATE == 0.05
    assert used == []  # nothing claimed as a library key when the default is used
    assert "CALL RATE" in note
    assert "utilisation_call_rate" in note


def test_valuation_reexports_the_same_resolver() -> None:
    # One definition, two module surfaces: the engine path must not fork its own copy again.
    assert _local_flex_call_rate is local_flex_call_rate


@pytest.mark.parametrize("value", [0.0, 0.22, 1.0])
def test_a_library_call_rate_overrides_the_default(value: float) -> None:
    lib = load_assumptions("gb_v0_1")
    _set_call_rate(lib, value)
    rate, used, _ = local_flex_call_rate(lib)
    assert rate == value
    assert used == ["local_flex_piclo.utilisation_call_rate"]


@pytest.mark.parametrize("bad", [-0.1, 1.5, 20.0])
def test_an_out_of_range_call_rate_is_refused_not_applied(bad: float) -> None:
    # A call rate of 20 (i.e. 2000%) must never multiply revenue — it falls back to the default.
    lib = load_assumptions("gb_v0_1")
    _set_call_rate(lib, bad)
    rate, used, _ = local_flex_call_rate(lib)
    assert rate == _DEFAULT_LOCAL_FLEX_CALL_RATE
    assert used == []


# --- the 20x divergence: both paths, one number ------------------------------


def test_engine_and_proxy_price_local_flex_identically(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _site(make_site)
    engine, proxy = _engine_flex(site, lib), _proxy_flex(site, lib)
    assert engine == pytest.approx(proxy, abs=0.01), (
        "the engine and proxy local-flex paths must price the same £ — they differed by 20x "
        "because only the engine applied the sourced DNO call rate (F-19)"
    )


def test_both_paths_apply_the_call_rate_not_full_in_window_energy(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _site(make_site)
    util = lib.get("local_flex_piclo", "utilisation_price_gbp_per_mwh").value
    call_rate, _, _ = local_flex_call_rate(lib)
    expected = util * TENDER_MWH * call_rate
    assert _engine_flex(site, lib) == pytest.approx(expected, abs=0.01)
    assert _proxy_flex(site, lib) == pytest.approx(expected, abs=0.01)
    # And the un-rated figure — every in-window MWh paid as an instructed call — is 20x bigger.
    assert util * TENDER_MWH == pytest.approx(expected / call_rate, abs=0.01)


def test_parity_holds_for_the_consented_agreed_rate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # The proxy previously read ONLY the assumption key, so a site whose agreed rate lived on
    # ExportArrangement was priced off the library here and off the agreed rate there.
    lib = load_assumptions("gb_v0_1")
    site = _site(
        make_site,
        consents=(
            s.RevenueStreamConsent(
                stream=s.MarketStream.LOCAL_FLEX_PICLO,
                consented=True,
                agreed_rate_gbp=222.0,
                agreed_rate_unit=RateUnit.GBP_PER_MWH,
            ),
        ),
    )
    engine, proxy = _engine_flex(site, lib), _proxy_flex(site, lib)
    call_rate, _, _ = local_flex_call_rate(lib)
    assert engine == pytest.approx(222.0 * TENDER_MWH * call_rate, abs=0.01)
    assert engine == pytest.approx(proxy, abs=0.01)


def test_parity_holds_with_an_aggregator_cut(make_site: Callable[..., s.SiteModel]) -> None:
    lib = load_assumptions("gb_v0_1")
    site = _site(make_site, export_arrangement=s.ExportArrangement(aggregator_cut_pct=0.25))
    engine, proxy = _engine_flex(site, lib), _proxy_flex(site, lib)
    util = lib.get("local_flex_piclo", "utilisation_price_gbp_per_mwh").value
    call_rate, _, _ = local_flex_call_rate(lib)
    assert engine == pytest.approx(util * TENDER_MWH * call_rate * 0.75, abs=0.01)
    assert engine == pytest.approx(proxy, abs=0.01)


def test_parity_holds_when_a_library_call_rate_lands(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # Guard the resolver seam itself: if a release ever carries the key, BOTH paths must move.
    lib = load_assumptions("gb_v0_1")
    _set_call_rate(lib, 0.22)  # the register's UKPN median utilisation-to-availability ratio
    site = _site(make_site)
    util = lib.get("local_flex_piclo", "utilisation_price_gbp_per_mwh").value
    assert _engine_flex(site, lib) == pytest.approx(util * TENDER_MWH * 0.22, abs=0.01)
    assert _engine_flex(site, lib) == pytest.approx(_proxy_flex(site, lib), abs=0.01)


def test_tender_context_does_not_break_parity(make_site: Callable[..., s.SiteModel]) -> None:
    # An awarded tender makes the stream CONTRACTED (it enters the priced floor) — the £ must still
    # match the proxy, because that is precisely when the divergence was most expensive.
    lib = load_assumptions("gb_v0_1")
    site = _site(make_site)
    ctx = GateContext(local_flex_tender_open=True)
    value_streams, _, _ = build_value_streams(_schedule(), site, lib, context=ctx)
    engine_entry = next(v for v in value_streams if v.stream == s.MarketStream.LOCAL_FLEX_PICLO)
    assert engine_entry.firmness == s.Firmness.CONTRACTED
    proxy_entry = (
        default_registry()
        .get(s.MarketStream.LOCAL_FLEX_PICLO)
        .value_function(_dispatch(), site, lib, None, ctx)
    )
    assert proxy_entry.firmness == s.Firmness.CONTRACTED
    assert engine_entry.annual_revenue_gbp == pytest.approx(
        proxy_entry.annual_revenue_gbp, abs=0.01
    )
