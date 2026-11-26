"""Tests for the gb_v1_7 assumption release (Tier A remediation constants).

gb_v1_7 is the REGISTER-BACKED successor to gb_v1_6: it adds the constants the open Tier A findings
need — a marginal degradation cost for the dispatch objective (DEG-01), the SoC operating window
(D-169), published fleet availability distinct from degradation (H-17), per-licence-area CDCM DUoS
band windows (LIT-F-05), Capacity Market de-rating and clearing prices keyed by auction type AND
delivery year (D-25), ENA Open Networks local-flex settlement mechanics (D-116), DC/DM/DR settlement
K/F and EFA-block prices (D-158), DFS registration and settlement (E-06), and the 1 MW eligibility
thresholds plus the aggregation rule (D-92).

The load-bearing invariants these tests lock:

*   Exactly ONE value drifts from gb_v1_6 (the degradation cost, 5.0 -> 21.3) and NO key is dropped.
    `test_gb_v1_7_priors_identical_to_gb_v1_6_except_the_enumerated_leaves` is the mechanical proof
    of the release's drift table — it fails if any other number moves, in either release.
*   Capex and opex are untouched, value AND status, so nothing was tuned toward viability.
*   Nothing is `confirmed` (0 confirmed repo-wide, by design).
*   Unsourced constants are null-valued homes carrying a data ask, never invented numbers.
*   The CM de-rating table is NOT duration-proportional in general — a property a killed finding
    relied on, which held only for the single slice gb_v1_6 carried.
"""

from __future__ import annotations

from datetime import date

import pytest

from gridq.assumptions import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    Assumption,
    AssumptionStatus,
    load_assumptions,
)
from gridq.assumptions.loader import available_versions, latest_version

_ALL_SECTIONS = tuple(SECTION_NAMES) + tuple(OPTIONAL_SECTION_NAMES)

# The ONE value that drifts from gb_v1_6, and what it lands at (DEG-01).
_VALUE_DRIFT: dict[tuple[str, str], object] = {
    ("battery_opex", "degradation_marginal_cost_gbp_per_mwh"): 21.3,
}

# Entries whose value is carried forward byte-identical but whose `notes` gained a cross-reference
# to a new gb_v1_7 key. Status, unit, source, owner and confidence must NOT move on any of them.
_NOTES_ONLY: frozenset[tuple[str, str]] = frozenset(
    {
        ("ancillary_services", "dc_dm_dr_value_gbp_per_mw_yr"),
        ("battery_capex", "installed_cost"),
        ("battery_opex", "annual_fixed_opex"),
        ("business_rates", "annual_gbp_per_mw"),
        ("capacity_market", "clearing_price_gbp_per_kw_yr"),
        ("capacity_market", "de_rating_factor_by_duration_h"),
        ("duos", "red_band_avoidance_gbp_per_mw_yr"),
        ("insurance", "annual_pct_of_capex"),
        ("local_flex_piclo", "aggregator_cut_pct"),
        ("penalties", "cm_annual_cap_pct_of_revenue"),
        ("penalties", "cm_monthly_cap_pct_of_revenue"),
        ("penalties", "cm_penalty_rate_fraction_of_clearing"),
        ("round_trip_efficiency", "ac_round_trip"),
    }
)

# Locked entry mix (across required + optional sections) — drift is caught here.
_TOTAL_ENTRIES = 71
_NEEDS_REVIEW = 42
_PLACEHOLDER = 29
_CONFIRMED = 0

# The 14 GB distribution licence areas the DUoS band table must key on.
_LICENCE_AREAS = (
    "NGED_EM",
    "NGED_WM",
    "NGED_SW",
    "NGED_SWALES",
    "UKPN_EPN",
    "UKPN_LPN",
    "UKPN_SPN",
    "SSEN_SEPD",
    "SSEN_SHEPD",
    "SPEN_SPD",
    "SPEN_SPM",
    "NPG_NORTHEAST",
    "NPG_YORKSHIRE",
    "ENWL",
)


def _entries(lib) -> list[tuple[str, str, Assumption]]:  # type: ignore[no-untyped-def]
    return [(sec, name, a) for sec in _ALL_SECTIONS for name, a in getattr(lib, sec).items()]


# --- Load / validate / sections ---------------------------------------------


def test_gb_v1_7_loads_with_all_sections() -> None:
    lib = load_assumptions("gb_v1_7")
    assert lib.metadata.version == "gb_v1_7"
    assert lib.metadata.market == "GB"
    assert lib.metadata.released == date(2026, 8, 12)
    assert lib.metadata.currency == "GBP"
    for section in SECTION_NAMES:
        assert getattr(lib, section), f"required section {section} is empty"


def test_gb_v1_7_is_resolvable_and_never_the_superseded_default() -> None:
    """MD-F-15: the CLI resolves ``latest_version()`` rather than pinning ``gb_v0_1``.

    This deliberately does NOT assert ``latest_version() == "gb_v1_7"``. That form was written when
    gb_v1_7 was newest and it broke the moment gb_v1_8 was cut, which is a fragile test rather than
    a real invariant: a release-specific file must not fail simply because a later release exists.
    What matters, and what MD-F-15 fixed, is that the resolved default is the NEWEST release on
    disk and never the superseded gb_v0_1, whose CM de-rating is overstated ~2.3-4x.

    Ordering comes from ``available_versions()``, which sorts on the NUMERIC parts of the name.
    This assertion used to be a plain string compare (``resolved >= "gb_v1_7"``), which ranks
    ``gb_v1_10`` BELOW ``gb_v1_7`` because "1" sorts before "7". It was a time bomb: it went red
    the first time a two-digit minor was cut (gb_v1_10, 2026-08-16) even though nothing had
    regressed. `available_versions` carries a docstring warning about exactly this, and the
    sibling gb_v1_8 test already uses the index form; this file had not been brought across.
    """
    versions = available_versions()
    resolved = latest_version()
    assert resolved != "gb_v0_1"
    assert resolved == versions[-1]
    assert versions.index(resolved) >= versions.index("gb_v1_7"), (
        f"the resolved default {resolved!r} is older than gb_v1_7; MD-F-15 has regressed"
    )
    # This release must stay loadable for provenance after being superseded.
    assert load_assumptions("gb_v1_7").metadata.version == "gb_v1_7"


def test_gb_v1_7_entry_and_status_counts_locked() -> None:
    entries = _entries(load_assumptions("gb_v1_7"))
    assert len(entries) == _TOTAL_ENTRIES
    mix = [a.status for _, _, a in entries]
    assert mix.count(AssumptionStatus.NEEDS_REVIEW) == _NEEDS_REVIEW
    assert mix.count(AssumptionStatus.PLACEHOLDER) == _PLACEHOLDER
    assert mix.count(AssumptionStatus.CONFIRMED) == _CONFIRMED


def test_gb_v1_7_confirms_nothing() -> None:
    # 0 confirmed repo-wide, by design: no quote on this release may present as bankable.
    for section, name, a in _entries(load_assumptions("gb_v1_7")):
        assert a.status != AssumptionStatus.CONFIRMED, f"{section}.{name} must not be confirmed"


def test_gb_v1_7_every_entry_fully_documented() -> None:
    for section, name, a in _entries(load_assumptions("gb_v1_7")):
        where = f"{section}.{name}"
        assert a.unit.strip(), f"{where} has no unit"
        assert a.source.strip(), f"{where} has no source"
        assert a.owner_or_reviewer.strip(), f"{where} has no owner"
        assert a.notes.strip(), f"{where} has no notes"
        assert a.last_reviewed <= date(2026, 8, 12), f"{where} reviewed in the future"


# --- The drift table, proved mechanically ------------------------------------


def test_gb_v1_7_priors_identical_to_gb_v1_6_except_the_enumerated_leaves() -> None:
    """The release's own drift claim, enforced: one value moves, nothing else, nothing dropped."""
    v6, v7 = load_assumptions("gb_v1_6"), load_assumptions("gb_v1_7")
    observed_drift: dict[tuple[str, str], object] = {}
    for section in _ALL_SECTIONS:
        prior, current = getattr(v6, section), getattr(v7, section)
        for name, before in prior.items():
            key = (section, name)
            assert name in current, f"gb_v1_7 DROPPED {section}.{name} — that breaks callers"
            after = current[name]
            if before.value != after.value:
                observed_drift[key] = after.value
                continue
            # Value carried forward: only `notes` may differ, and only where enumerated.
            for field in ("unit", "source", "status", "owner_or_reviewer", "confidence"):
                assert getattr(before, field) == getattr(after, field), (
                    f"{section}.{name}.{field} drifted with no value change"
                )
            if before.notes != after.notes:
                assert key in _NOTES_ONLY, f"{section}.{name} notes changed unannounced"
    assert observed_drift == _VALUE_DRIFT


def test_gb_v1_7_capex_and_opex_untouched() -> None:
    """Item 11: nothing was tuned toward viability. Value AND status frozen on capex/opex."""
    v6, v7 = load_assumptions("gb_v1_6"), load_assumptions("gb_v1_7")
    for section in ("battery_capex", "battery_opex", "insurance", "business_rates", "dno_costs"):
        for name, before in getattr(v6, section).items():
            if (section, name) in _VALUE_DRIFT:
                continue  # the degradation cost is a wear price, not a capex/opex re-basing
            after = getattr(v7, section)[name]
            assert after.value == before.value, f"{section}.{name} value moved"
            assert after.status == before.status, f"{section}.{name} status moved"
    # The grid-scale benchmarks are landed as SEPARATE reference keys, never as the inputs.
    v7_capex = load_assumptions("gb_v1_7").battery_capex
    assert "installed_cost_grid_scale_survey_benchmark" in v7_capex
    assert v7_capex["installed_cost"].value == 500000


def test_gb_v1_7_prior_releases_untouched() -> None:
    # gb_v0_1..gb_v1_6 stay frozen for provenance; this release is a new file, not an edit.
    assert load_assumptions("gb_v1_6").metadata.version == "gb_v1_6"
    assert (
        load_assumptions("gb_v1_6").battery_opex["degradation_marginal_cost_gbp_per_mwh"].value
        == 5.0
    )
    assert load_assumptions("gb_v0_1").metadata.version == "gb_v0_1"


# --- DEG-01: the degradation cost -------------------------------------------


def test_gb_v1_7_degradation_cost_is_per_mwh_discharged_and_reconciles_to_its_basis() -> None:
    lib = load_assumptions("gb_v1_7")
    cost = lib.get("battery_opex", "degradation_marginal_cost_gbp_per_mwh")
    assert cost.value == 21.3
    assert "MWh discharged" in cost.unit  # throughput, NOT per cycle — the wiring depends on this
    assert cost.status == AssumptionStatus.NEEDS_REVIEW
    # The flat number must be reproducible from its own recorded basis (C-34).
    basis = lib.get("degradation", "degradation_cost_basis").value
    derived = basis["c_repl_gbp_per_mwh_rated"] / (basis["n_efc_to_eol"] * basis["eta_discharge"])
    assert derived == pytest.approx(cost.value, abs=0.05)
    assert basis["c_deg_gbp_per_mwh_discharged"] == cost.value
    # And the break-even check must be the RTE term plus that cost (C-36), not the RTE term alone.
    spread = lib.get("battery_opex", "arbitrage_breakeven_spread_check_gbp_per_mwh").value
    assert spread == pytest.approx(70.0 * (1 / 0.86 - 1) + cost.value, abs=0.05)


def test_gb_v1_7_degradation_cost_notes_warn_about_the_existing_finance_line() -> None:
    # Finance already deducts this key post-hoc while ObjectiveWeights default to 0.0. Wiring it
    # into the MILP objective must charge wear once, and the release has to say so.
    notes = (
        load_assumptions("gb_v1_7")
        .get("battery_opex", "degradation_marginal_cost_gbp_per_mwh")
        .notes
    )
    assert "ONCE" in notes
    assert "objective" in notes.lower()


def test_gb_v1_7_depth_aware_coefficients_are_convex() -> None:
    # C-35: a flat GBP/MWh adder cannot represent depth dependence; k5 > 1 makes Phi convex.
    coeffs = load_assumptions("gb_v1_7").get("degradation", "cycle_depth_stress_coefficients").value
    assert coeffs["k4"] == pytest.approx(5.24e-4)
    assert coeffs["k5"] > 1.0
    assert (0.8 / 0.1) ** (coeffs["k5"] - 1) == pytest.approx(
        coeffs["relative_marginal_cost_delta_0_8_vs_0_1"], rel=0.01
    )


# --- D-169: the SoC operating window ----------------------------------------


def test_gb_v1_7_soc_window_is_strictly_inside_zero_to_one() -> None:
    lib = load_assumptions("gb_v1_7")
    lo = lib.get("warranty_cycle_limits", "soc_min_fraction")
    hi = lib.get("warranty_cycle_limits", "soc_max_fraction")
    assert 0.0 < lo.value < hi.value < 1.0
    assert lo.status == AssumptionStatus.NEEDS_REVIEW
    assert hi.status == AssumptionStatus.NEEDS_REVIEW
    # Not an OEM warranty term — the release must not claim it is.
    assert "NOT AN OEM WARRANTY TERM" in lo.notes


# --- H-17: fleet availability, distinct from degradation ---------------------


def test_gb_v1_7_fleet_availability_is_distinct_from_degradation() -> None:
    lib = load_assumptions("gb_v1_7")
    avail = lib.get("availability", "fleet_availability_pct")
    fade = lib.get("degradation", "annual_capacity_fade_pct")
    assert 90.0 <= avail.value <= 100.0
    # It must not be the complement of the fade rate — that would BE degradation, not availability.
    assert avail.value != pytest.approx(100.0 - fade.value)
    # The site-specific firm-commitment factor stays an empty home (D30) — two different things.
    assert lib.get("availability", "expected_availability_pct").value is None


def test_gb_v1_7_annual_availability_scalar_is_not_offered_for_frequency_response() -> None:
    # D-158: F zeroes a whole settlement period after 1.8 s of unavailability and K_e takes the
    # minimum across the block, so an annual scalar is invalid there. The release must say so.
    lib = load_assumptions("gb_v1_7")
    f = lib.get("ancillary_services", "availability_factor_f_threshold_pct").value
    assert f["threshold_pct"] == 99.9
    assert f["equivalent_unavailability_seconds_per_settlement_period"] == 1.8
    assert "MUST NOT" in lib.get("availability", "fleet_availability_pct").notes


# --- LIT-F-05: per-licence-area DUoS bands ----------------------------------


def test_gb_v1_7_duos_bands_cover_all_fourteen_licence_areas() -> None:
    bands = load_assumptions("gb_v1_7").get("duos", "band_windows_by_dno").value
    assert tuple(bands) == _LICENCE_AREAS
    sourced = {k for k, v in bands.items() if v is not None}
    # Honest partial: exactly the areas the register holds, the rest explicit nulls (not defaults).
    assert sourced == {"NGED_EM"}


def test_gb_v1_7_duos_nged_em_band_is_transcribed_faithfully() -> None:
    em = load_assumptions("gb_v1_7").get("duos", "band_windows_by_dno").value["NGED_EM"]
    assert em["weekday_windows"]["red"] == [{"start": "16:00", "end": "19:00"}]
    # Bank holidays are RED for LV/HV in this licence area — the register calls treating them as
    # green 'a common modelling error'. Per-DNO, never assumed.
    assert em["bank_holiday_band"] == "red"
    assert em["timezone"] == "Europe/London"  # UK clock time, not UTC
    # The EHV/EDCM Super Red band is Nov-Feb only, unlike the all-year LV/HV red band.
    assert em["edcm_super_red"]["months"] == [11, 12, 1, 2]
    assert em["red_months"] == "all"


def test_gb_v1_7_duos_rates_and_windows_are_keyed_the_same_way() -> None:
    # The GBP of red-band avoidance is the window TIMES the rate: a sourced window with no sourced
    # rate is half a constant, so the two tables must key identically.
    lib = load_assumptions("gb_v1_7")
    bands = lib.get("duos", "band_windows_by_dno").value
    rates = lib.get("duos", "unit_rates_p_per_kwh_by_dno").value
    assert tuple(bands) == tuple(rates)
    em = rates["NGED_EM"]
    assert em["lv_site_specific"]["red"] > em["lv_site_specific"]["green"]
    # The near-equality of the LV red import charge and the LV red export credit is a MILP hazard.
    assert em["generation_export_credits"]["lv_site_specific"]["red"] < 0


# --- D-25: CM de-rating by auction type and delivery year -------------------


def test_gb_v1_7_legacy_de_rating_key_unchanged_for_existing_callers() -> None:
    old = load_assumptions("gb_v1_7").get("capacity_market", "de_rating_factor_by_duration_h").value
    assert old == {"1": 0.1099, "2": 0.2198, "4": 0.4396}


def test_gb_v1_7_de_rating_is_keyed_by_auction_then_delivery_year_then_duration() -> None:
    table = (
        load_assumptions("gb_v1_7")
        .get("capacity_market", "de_rating_by_auction_and_delivery_year")
        .value
    )
    hist = table["history_1h_2h_4h"]
    assert set(hist) == {"T-1", "T-3", "T-4"}
    # The new table's latest T-4 slice must agree with the legacy duration-only key.
    assert hist["T-4"]["2029-30"] == {"1": 0.1099, "2": 0.2198, "4": 0.4396}
    # The register claims T-1 is 'systematically higher' than T-4 for the same delivery year. Its
    # own history contradicts that: T-1 is higher in 1 of the 6 shared years and LOWER in 5. The
    # difference is unsigned, so neither auction's factor may substitute for the other's.
    shared = sorted(set(hist["T-1"]) & set(hist["T-4"]))
    higher = [dy for dy in shared if hist["T-1"][dy]["1"] > hist["T-4"][dy]["1"]]
    assert len(shared) == 6
    assert higher == ["2026-27"]
    # The conservative floor-lookup convention is carried explicitly, not left implicit.
    assert "floor" in table["lookup_convention"]
    # 5h/5.5h were not published in the fetched curves: absent, never interpolated.
    for curve in table["full_curves"].values():
        for bins in curve.values():
            assert "5" not in bins and "5.5" not in bins


def test_gb_v1_7_de_rating_table_is_not_duration_proportional_in_general() -> None:
    """A killed finding assumed exact duration-proportionality. It holds for only 2 of 18 slices."""
    hist = (
        load_assumptions("gb_v1_7")
        .get("capacity_market", "de_rating_by_auction_and_delivery_year")
        .value["history_1h_2h_4h"]
    )
    proportional = set()
    for auction, years in hist.items():
        for dy, bins in years.items():
            one = bins["1"]
            if all(
                bins[str(d)] == pytest.approx(d * one, abs=1e-9) for d in (2, 4) if str(d) in bins
            ):
                proportional.add((auction, dy))
    assert proportional == {("T-4", "2029-30"), ("T-1", "2026-27")}
    # The worst break is far too large for treating the table as proportional to be rounding.
    t1_2019 = hist["T-1"]["2019-20"]
    assert 4 * t1_2019["1"] - t1_2019["4"] > 0.4


def test_gb_v1_7_cm_clearing_prices_pair_with_the_de_rating_table() -> None:
    lib = load_assumptions("gb_v1_7")
    prices = lib.get("capacity_market", "clearing_price_by_auction_and_delivery_year").value
    # Must agree with the scalar the release also carries for the latest T-4.
    assert (
        prices["T-4"]["2029-30"] == lib.get("capacity_market", "clearing_price_gbp_per_kw_yr").value
    )
    assert prices["T-1"]["2026-27"] == 5.00
    # T-4 agreements are CPI-indexed, so they depend on the unresolved CPI decision.
    assert prices["indexation"]["T-1"] == "not indexed"
    assert "CPI" in prices["indexation"]["T-4"]
    # Nothing clears above the published price cap.
    cap = prices["auction_parameters_2026_auctions"]["price_cap_gbp_per_kw_yr"]
    for auction, years in prices.items():
        if auction in {"indexation", "auction_parameters_2026_auctions"}:
            continue
        for dy, p in years.items():
            assert p <= cap, f"{auction} {dy} clears above the price cap"


def test_gb_v1_7_cm_price_gap_is_left_empty_not_filled_from_a_neighbour() -> None:
    """The published series has no 2022-23 T-4 price (that year ran a T-3). Do not invent one."""
    lib = load_assumptions("gb_v1_7")
    prices = lib.get("capacity_market", "clearing_price_by_auction_and_delivery_year").value
    derating = lib.get("capacity_market", "de_rating_by_auction_and_delivery_year").value[
        "history_1h_2h_4h"
    ]
    assert "2022-23" not in prices["T-4"]
    assert prices["T-3"]["2022-23"] == 6.44
    # The de-rating table DOES publish that slice: the asymmetry is real and must stay visible, so
    # a consumer pairing the tables refuses rather than substituting an adjacent year.
    assert "2022-23" in derating["T-4"]
    unpriced = {
        (a, dy) for a, years in derating.items() for dy in years if dy not in prices.get(a, {})
    }
    assert unpriced == {("T-4", "2022-23")}


# --- D-92: eligibility thresholds -------------------------------------------


@pytest.mark.parametrize(
    ("section", "name"),
    [
        ("capacity_market", "min_connection_capacity_mw"),
        ("balancing_mechanism", "min_unit_capacity_mw"),
    ],
)
def test_gb_v1_7_one_mw_eligibility_gates_are_present(section: str, name: str) -> None:
    a = load_assumptions("gb_v1_7").get(section, name)
    value = a.value if isinstance(a.value, float) else a.value["minimum"]
    assert value == 1.0
    assert a.status == AssumptionStatus.NEEDS_REVIEW


def test_gb_v1_7_response_gate_is_one_mw_and_dfs_is_the_route_below_it() -> None:
    lib = load_assumptions("gb_v1_7")
    resp = lib.get("ancillary_services", "min_response_unit_capacity_mw").value
    dfs = lib.get("ancillary_services", "dfs_registered_unit_capacity_mw").value
    assert resp["minimum"] == 1.0 and resp["maximum"] == 100.0
    assert dfs["minimum"] == 0.1 and dfs["maximum"] == 100.0
    assert dfs["zone_count"] == 12
    assert "GSP Group" in resp["aggregation"]
    # No sourced aggregator revenue share exists anywhere in the register: it stays an empty home.
    assert (
        lib.get("balancing_mechanism", "aggregation_rule").value["aggregator_revenue_share_pct"]
        is None
    )


# --- D-158 / D-116 / E-06: settlement mechanics -----------------------------


def test_gb_v1_7_dc_dm_dr_k_thresholds_cover_all_six_products_at_efa_granularity() -> None:
    lib = load_assumptions("gb_v1_7")
    k = lib.get("ancillary_services", "performance_factor_k_parameters").value
    assert set(k["error_thresholds"]) >= {"DC", "DM", "DR"}
    for product, ab in k["error_thresholds"].items():
        assert 0 < ab["A"] < ab["B"] <= 1.0, product
    assert "min" in k["block_factor"]  # K_e is the MINIMUM k across the block, not the mean
    # Realised K is not published anywhere in the register: no expected value is invented.
    assert k["assumed_realised_k"] is None
    prices = lib.get("ancillary_services", "clearing_price_gbp_per_mw_h_by_product").value
    assert set(prices["fy26_27_mean"]) == {"DCL", "DCH", "DML", "DMH", "DRL", "DRH"}
    assert set(prices["fy26_27_mean_by_efa_block"]) == set(prices["fy26_27_mean"])
    assert set(prices["efa_blocks"]) == {f"EFA{i}" for i in range(1, 7)}
    # Two products clear NEGATIVE on average: clamping ancillary revenue at zero misprices them.
    assert prices["fy26_27_mean"]["DRH"] < 0 and prices["fy26_27_mean"]["DMH"] < 0


def test_gb_v1_7_local_flex_settlement_carries_grace_multiplier_and_pod() -> None:
    mech = (
        load_assumptions("gb_v1_7").get("local_flex_piclo", "settlement_mechanics_by_product").value
    )
    for product, terms in mech["by_product"].items():
        assert terms["performance_multiplier"] >= 2, product
        assert terms["payable_over_delivery_pct"] == 0, product  # over-delivery is never paid
    assert "MPF" in mech["monthly_utilisation_performance_factor"]
    taper = mech["payment_taper_at_grace_5pct_multiplier_3"]
    assert taper["95"] == 100 and taper["80"] == 50 and taper["63"] == 0


def test_gb_v1_7_local_flex_availability_prices_are_kept_unaveraged() -> None:
    a = load_assumptions("gb_v1_7").get(
        "local_flex_piclo", "availability_price_gbp_per_mw_h_by_source"
    )
    # Sources disagree 20-30x for the same product. The register's rule: keep both, average neither.
    assert "DISAGREE" in a.value["note"]
    assert a.value["nged_saou_da_ceiling_2025_26"]["median"] < 10
    assert a.value["ukpn_saou_hv_demand_indicative_2026_tender"]["lower_range"]["median"] > 50
    # And no default availability price is created: that home stays empty per D30.
    assert (
        load_assumptions("gb_v1_7").get("availability", "availability_price_gbp_per_mw_h").value
        is None
    )


def test_gb_v1_7_dfs_k_penalty_branch_is_not_clipped_at_zero() -> None:
    dfs = (
        load_assumptions("gb_v1_7")
        .get("ancillary_services", "dfs_performance_factor_k_piecewise")
        .value
    )
    ks = dfs["k_schedule"]
    assert ks[0]["k"] == 1.2  # 20% over-delivery bonus cap
    assert ks[-1]["k"] == -1.0  # the provider PAYS below -100% realised volume
    assert any(row["k"] == 0.0 for row in ks)  # the 0-25% dead zone earns exactly zero


# --- H-15 / N13: the CPI home is a home, not a number -----------------------


def test_gb_v1_7_cpi_key_exists_but_asserts_no_rate() -> None:
    cpi = load_assumptions("gb_v1_7").get("financing", "cpi_pct_per_year")
    assert cpi.value is None, "the index, the rate and which lines are indexed are Tier B decisions"
    assert cpi.status == AssumptionStatus.NEEDS_REVIEW
    # The candidate figures and the reason they are not interchangeable must be recorded.
    assert "1.95" in cpi.source and "CPIH" in cpi.source


# --- Honest-missing discipline ----------------------------------------------


def test_gb_v1_7_null_valued_entries_state_what_would_settle_them() -> None:
    """A null value is only useful if the entry says what is missing. Enforce it."""
    for section, name, a in _entries(load_assumptions("gb_v1_7")):
        if a.value is not None:
            continue
        blob = f"{a.source} {a.notes}".upper()
        assert any(
            marker in blob
            for marker in ("NOT SOURCED", "NOT AGREED", "DATA ASK", "REQUIRED", "EMPTY HOME")
        ), f"{section}.{name} is empty but names no data ask"


def test_gb_v1_7_new_keys_cite_a_source_and_a_vintage() -> None:
    v6, v7 = load_assumptions("gb_v1_6"), load_assumptions("gb_v1_7")
    new = [(sec, name, a) for sec, name, a in _entries(v7) if name not in getattr(v6, sec)]
    assert len(new) == 31
    for section, name, a in new:
        where = f"{section}.{name}"
        # A vintage: either a cited year/date in the source, or an explicit not-sourced statement.
        assert any(str(y) in a.source for y in range(2014, 2027)) or "NOT " in a.source.upper(), (
            f"{where} cites no vintage"
        )
        assert a.status != AssumptionStatus.CONFIRMED, where
