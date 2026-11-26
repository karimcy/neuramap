"""Installer capex sanity-checked against a benchmark band (L14 · M5 finance).

System Brief (M5): "Installer capex sanity-checked against a benchmark library." A deterministic
plausibility check compares the installed unit capex (£/MW) against the library's
``battery_capex.installed_cost_benchmark_band`` and flags an out-of-band figure for review. It
never blocks and never changes a number; the band bounds come from the library (no private
constant). The check also runs inside ``estimate_capex`` on every priced run.
"""

from __future__ import annotations

import pytest

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.finance import check_capex_plausibility, estimate_capex


@pytest.fixture
def lib() -> AssumptionLibrary:
    return load_assumptions("gb_v0_1")


def _band(lib: AssumptionLibrary) -> tuple[float, float]:
    value = lib.get("battery_capex", "installed_cost_benchmark_band").value
    return float(value["low"]), float(value["high"])


def test_within_band_is_clean(lib: AssumptionLibrary) -> None:
    low, high = _band(lib)
    midpoint = (low + high) / 2
    assert check_capex_plausibility(midpoint, lib) == []


def test_above_band_is_flagged(lib: AssumptionLibrary) -> None:
    _, high = _band(lib)
    warnings = check_capex_plausibility(high * 2, lib)
    assert warnings and "outside the benchmark band" in warnings[0]


def test_below_band_is_flagged(lib: AssumptionLibrary) -> None:
    low, _ = _band(lib)
    warnings = check_capex_plausibility(low / 2, lib)
    assert warnings and "outside the benchmark band" in warnings[0]


def test_band_boundaries_are_inclusive(lib: AssumptionLibrary) -> None:
    low, high = _band(lib)
    assert check_capex_plausibility(low, lib) == []
    assert check_capex_plausibility(high, lib) == []


def test_missing_band_skips_the_check(lib: AssumptionLibrary) -> None:
    # No band configured → the check is skipped, never guessed.
    stripped = lib.model_copy(deep=True)
    del stripped.battery_capex["installed_cost_benchmark_band"]
    assert check_capex_plausibility(1_000_000.0, stripped) == []


def test_modelled_capex_is_within_band_so_a_priced_run_is_clean(lib: AssumptionLibrary) -> None:
    # The gb_v0_1 installed_cost (£500k/MW) sits inside the band, so estimate_capex adds no
    # benchmark warning on a normal run (keeps the determinism golden stable).
    warnings: list[str] = []
    used: list[str] = []
    estimate_capex(2.0, lib, warnings=warnings, used=used)
    assert not any("benchmark band" in w for w in warnings)


def test_estimate_capex_flags_an_out_of_band_unit_cost(lib: AssumptionLibrary) -> None:
    # Force the unit cost above the band and confirm estimate_capex surfaces the flag (not block).
    tampered = lib.model_copy(deep=True)
    high = float(tampered.battery_capex["installed_cost_benchmark_band"].value["high"])
    tampered.battery_capex["installed_cost"].value = high * 3
    warnings: list[str] = []
    used: list[str] = []
    est = estimate_capex(1.0, tampered, warnings=warnings, used=used)
    assert est.total_gbp == round(high * 3, 2)  # the figure is unchanged — only flagged
    assert any("outside the benchmark band" in w for w in warnings)


# --------------------------------------------------------------- the FIXED capex term (2026-08-19)


def test_capex_carries_the_sourced_size_invariant_one_off_on_both_shapes() -> None:
    """Capex was purely linear in kW and kWh, and the library documented the omission verbatim.

    ``battery_capex.dno_assessment_and_design_one_off_gbp_per_site`` (GBP 1,557, NGED South West's
    published minimum Assessment and Design charge for LV generation over 50 kVA) read "REFERENCE
    ONLY - NOT CONSUMED BY ANY CODE PATH TODAY, AND THE OMISSION FLATTERS EVERY RETURN". With
    83.9 pct of the study-basis revenue stack invariant to battery size and a cost function with no
    fixed term, the sizing search walks to the smallest candidate that exists: the campaign's
    Harwell run selected 10.691 kW at 0.5 h and reported a 170.45 pct project IRR on GBP 3,554.76,
    i.e. GBP 332.50/kW installed against this library's own GBP 618/kW grid-scale 2-hour benchmark.

    It dies if ``_fixed_capex_gbp`` stops being called from either capex shape.
    """
    lib = load_assumptions("gb_v1_12")
    fixed = float(lib.get("battery_capex", "dno_assessment_and_design_one_off_gbp_per_site").value)
    assert fixed == 1557

    # The DECOMPOSED shape (the one every modern run takes).
    warnings: list[str] = []
    used: list[str] = []
    small = estimate_capex(0.010691, lib, warnings=warnings, used=used, energy_kwh=10.691 * 0.5)
    per_kwh = float(lib.get("battery_capex", "installed_cost_gbp_per_kwh").value)
    power_comp = float(lib.get("battery_capex", "installed_cost_gbp_per_mw_power_component").value)
    linear = power_comp * 0.010691 + per_kwh * 10.691 * 0.5
    assert small.fixed_gbp == fixed
    assert small.total_gbp == round(linear + fixed, 2)
    # The direction and magnitude, stated: DOWN on returns, +43.8 pct of capex at the small end.
    assert round((small.total_gbp / linear - 1.0) * 100, 1) == 43.8
    assert "battery_capex.dno_assessment_and_design_one_off_gbp_per_site" in used

    # A 30x larger candidate pays the SAME one-off, which is the whole point of a fixed
    # term: GBP/kW installed FALLS with size, which is the fixed-cost dilution the library cites
    # (DESNZ/Carbon Trust EINAS 2025, Reonic) and which a purely linear cost function denies.
    big = estimate_capex(0.320715, lib, warnings=[], used=[], energy_kwh=320.715 * 2.0)
    assert big.fixed_gbp == fixed
    assert round(big.total_gbp / 320.715, 2) == 704.85  # GBP/kW, up from 702.35 without the term

    # The LEGACY all-in shape charges it too: a fixed cost that lands on only one shape would make
    # the same site's capex depend on which release priced it.
    legacy = estimate_capex(0.010691, lib, warnings=[], used=[])
    all_in = float(lib.get("battery_capex", "installed_cost").value)
    assert legacy.fixed_gbp == fixed
    assert legacy.total_gbp == round(all_in * 0.010691 + fixed, 2)

    # A release that publishes no such key stays byte-identical: 0.0, not an invented default.
    old = load_assumptions("gb_v0_1")
    assert estimate_capex(0.010691, old, warnings=[], used=[]).fixed_gbp == 0.0
