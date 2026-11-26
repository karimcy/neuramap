"""N26 — parasitic auxiliary energy must be priced at the DELIVERED import cost, levies included.

Two call sites priced imported grid energy two different ways: the price counterfactual in
`financial_model` added the site's £/kWh levy lines, the parasitic-load cost line in `cashflows`
did not — behind a comment claiming the two shared a convention. Auxiliary draw is imported grid
energy: it attracts levies exactly as any other import does, which is the same reasoning the
levies-on-charge line rests on, and the MILP already treats it as continuous grid import consuming
connection headroom. Nothing else in the tree charged levies on it, so the cost was simply dropped.

These tests pin the ONE shared helper, both call sites agreeing, and the size and direction of the
correction (cost UP, therefore EBITDA/DSCR/IRR DOWN).
"""

from __future__ import annotations

from datetime import date

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq import schemas as s
from gridq.assumptions import load_assumptions
from gridq.finance.cashflows import (
    PARASITIC_LOAD_KEY,
    blended_import_rate_gbp_per_kwh,
    build_cost_lines,
)
from gridq.finance.financial_model import _blended_import_rate

_IMPORT_RATES = {"day": 0.2431, "night": 0.1187}
#: A representative GB C&I £/kWh levy stack (CCL + RO) — TEST data, not an assumption.
_LEVIES = {"cclevy": 0.00775, "ro": 0.0121}


def _site(
    *, import_rates: dict[str, float] | None = None, levies: dict[str, float] | None = None
) -> s.SiteModel:
    load = s.LoadDataSummary(
        annual_kwh=1_800_000.0,
        half_hourly_rows=17520,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        max_import_kw=420.0,
    )
    rates = _IMPORT_RATES if import_rates is None else import_rates
    return s.SiteModel(
        site_id="site_n26",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=load,
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates=rates,
            levies=levies or {},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def _parasitic(site: s.SiteModel, version: str = "gb_v1_6", power_mw: float = 1.0) -> float | None:
    lines = build_cost_lines(
        site,
        power_mw,
        500_000.0,
        load_assumptions(version),
        dispatch_energy=None,
        warnings=[],
        used=[],
    )
    return lines["parasitic_load"]


# --- the shared helper -----------------------------------------------------


def test_the_blended_rate_is_the_mean_import_rate_plus_every_levy_line() -> None:
    expected = round(sum(_IMPORT_RATES.values()) / 2 + sum(_LEVIES.values()), 6)
    assert blended_import_rate_gbp_per_kwh(_site(levies=_LEVIES)) == expected
    # 0.18090 mean + 0.01985 levies
    assert blended_import_rate_gbp_per_kwh(_site(levies=_LEVIES)) == pytest.approx(0.20075)


def test_no_import_rates_gives_no_rate_rather_than_a_guess() -> None:
    assert blended_import_rate_gbp_per_kwh(_site(import_rates={}, levies=_LEVIES)) is None


def test_both_call_sites_now_read_the_same_number() -> None:
    """The false-parity claim (`financial_model.py`) is now true by construction, not assertion."""
    for levies in ({}, _LEVIES, {"cclevy": 0.05}):
        site = _site(levies=levies)
        assert _blended_import_rate(site) == blended_import_rate_gbp_per_kwh(site)
    assert _blended_import_rate(_site(import_rates={})) is None


# --- the parasitic cost line ----------------------------------------------


def test_the_parasitic_line_is_priced_at_the_delivered_rate_including_levies() -> None:
    site = _site(levies=_LEVIES)
    lib = load_assumptions("gb_v1_6")
    fraction = float(lib.get(*PARASITIC_LOAD_KEY).value)
    aux_kwh = fraction * 1_000.0 * 8760.0
    expected = round(0.20075 * aux_kwh, 2)
    assert _parasitic(site) == pytest.approx(expected, abs=0.01)


def test_the_correction_is_the_levy_stack_times_the_auxiliary_volume() -> None:
    """The size and the DIRECTION of the N26 move: cost up, so every return metric down.

    1 MW, 1% parasitic fraction → 87,600 kWh/yr of levied grid import that was priced levy-free.
    """
    lib = load_assumptions("gb_v1_6")
    fraction = float(lib.get(*PARASITIC_LOAD_KEY).value)
    aux_kwh = fraction * 1_000.0 * 8760.0
    assert aux_kwh == pytest.approx(87_600.0)

    without = _parasitic(_site(levies={}))
    with_levies = _parasitic(_site(levies=_LEVIES))
    assert without is not None and with_levies is not None
    assert with_levies > without
    assert with_levies - without == pytest.approx(sum(_LEVIES.values()) * aux_kwh, abs=0.02)
    # ~£1,739/yr against a year-1 P50 EBITDA of order £22.8k on the review's worked 1 MW site.
    assert with_levies - without == pytest.approx(1_738.86, abs=0.01)


def test_a_site_with_no_levy_lines_is_left_exactly_where_it_was() -> None:
    """No gratuitous movement: with no levies the line is the mean import rate, as before."""
    mean_only = sum(_IMPORT_RATES.values()) / len(_IMPORT_RATES)
    lib = load_assumptions("gb_v1_6")
    aux_kwh = float(lib.get(*PARASITIC_LOAD_KEY).value) * 1_000.0 * 8760.0
    assert _parasitic(_site(levies={})) == round(mean_only * aux_kwh, 2)


def test_the_disclosure_names_the_levies_it_now_includes() -> None:
    """The old warning said "the MEAN of the site's import rates" — that sentence was the defect's
    own evidence, so the new one has to say what actually happened."""
    warnings: list[str] = []
    build_cost_lines(
        _site(levies=_LEVIES),
        1.0,
        500_000.0,
        load_assumptions("gb_v1_6"),
        dispatch_energy=None,
        warnings=warnings,
        used=[],
    )
    w = next(x for x in warnings if "'parasitic_load' prices" in x)
    assert "blended DELIVERED import cost" in w
    assert "levy" in w and "N26" in w
    assert "87600.0 kWh/yr" in w


def test_no_import_rates_leaves_the_line_unavailable_not_zero() -> None:
    warnings: list[str] = []
    lines = build_cost_lines(
        _site(import_rates={}, levies=_LEVIES),
        1.0,
        500_000.0,
        load_assumptions("gb_v1_6"),
        dispatch_energy=None,
        warnings=warnings,
        used=[],
    )
    assert lines["parasitic_load"] is None
    assert any("'parasitic_load' is unavailable" in w for w in warnings)


def test_releases_without_the_parasitic_key_stay_unchanged() -> None:
    """gb_v0_1/gb_v1_0 omit the battery's own draw entirely — N26 must not activate it."""
    for version in ("gb_v0_1", "gb_v1_0"):
        assert _parasitic(_site(levies=_LEVIES), version) is None
