"""Tests for the gb_v1_11 assumption release (the SOURCED COST-CORRECTION release over gb_v1_10).

gb_v1_11 answers decision D49. Two findings from the 2026-08-17 literature pass land TOGETHER,
because they pull in opposite directions and either alone would misrepresent the evidence:

*   `metering_idno.annual_gbp_per_site` 1200 -> **0** and `dno_costs.annual_gbp_per_site`
    1500 -> **0** as recurring lines. Both were `status: placeholder` with "No citable source
    identified", both were charged with **no `power_mw` multiplier** (so a 2 kW battery paid the
    same GBP 2,700/yr as a 750 kW one), and the DNO line is contradicted by the verbatim operative
    text of two DNO charging statements. Direction: DOWN, better, GBP 2,700/yr at every size.
*   `battery_capex.installed_cost` 500,000 -> **700,000 GBP/MW** (with the decomposition scaled
    uniformly so it still reconciles at the 2 h reference). The library sat 19% below the published
    GB grid-scale 2 h benchmark and 29% below the only attributable GB C&I figure. Direction: UP,
    worse.

**The anti-cherry-pick property is the load-bearing invariant in this module, and it is asserted as
a number rather than trusted to prose.** `test_gb_v1_11_net_effect_is_worse_at_the_reference_size`
measures both releases through the production cost functions and requires the net annual effect at
100 kW / 2 h to be **positive, i.e. WORSE**. If a later edit keeps the two GBP 0 lines and reverts
the capex correction, that test goes RED - which is exactly the failure mode the release exists to
prevent, because landing only the favourable half would manufacture a positive IRR out of half a
correction.

**Why the acceptance runs where it does.** `docs/validation_requirements.md` section 3: eleven
defects have shipped built, tested, merged and never called, and instance 4 is the warning against
trusting a call path - it *had* one and was still inert, because a call path proves reachability and
never that a value flows. So every figure here is read back through
`finance.cashflows.estimate_capex` and `finance.cashflows.build_cost_lines`, the functions
`build_cashflow_model` calls on every run, and asserted as a GBP amount:

- production consumers: `finance/cashflows.py` (`estimate_capex`, `build_cost_lines`), reached from
  `cli.py` -> `MilpEngine.optimise` -> `finance.cashflows.build_cashflow_model`;
- the release reaches production by `latest_version()` resolving from disk (MD-F-15, `cli.py`);
- editing any of the five changed values in `assumptions/gb_v1_11.yaml` turns these tests RED.

Nothing in this release is `confirmed`; every quote on it stays `not_bankable`, by design.
"""

from __future__ import annotations

import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import available_versions, latest_version, load_assumptions
from gridq.assumptions.models import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    Assumption,
    AssumptionLibrary,
    AssumptionStatus,
)
from gridq.finance.cashflows import build_cost_lines, estimate_capex
from gridq.finance.credit import build_debt_terms_from_credit
from gridq.schemas.enums import Confidence, CreditStatus

_ALL_SECTIONS = (*SECTION_NAMES, *OPTIONAL_SECTION_NAMES)

#: The release's own drift table: every value that moves from gb_v1_10, and nothing else may.
_VALUE_DRIFT: dict[tuple[str, str], tuple[object, object]] = {
    ("metering_idno", "annual_gbp_per_site"): (1200, 0),
    ("dno_costs", "annual_gbp_per_site"): (1500, 0),
    ("battery_capex", "installed_cost"): (500000, 700000),
    ("battery_capex", "installed_cost_gbp_per_kwh"): (175.0, 245.0),
    ("battery_capex", "installed_cost_gbp_per_mw_power_component"): (150000.0, 210000.0),
    ("financing", "debt_gearing_pct"): (60.0, 50.0),
}

#: Keys whose VALUE is deliberately unchanged and whose evidence/notes were rewritten instead.
_RESOURCED_ONLY: set[tuple[str, str]] = {
    ("business_rates", "annual_gbp_per_mw"),
    ("insurance", "annual_pct_of_capex"),
    ("battery_opex", "annual_fixed_opex"),
}

#: The one key added. REFERENCE ONLY: no code path reads it yet, and the release says so.
#: (Superseded 2026-08-19 by f3aedd8, FIX 3/7: `finance.cashflows._fixed_capex_gbp` now CONSUMES
#: this key - D49 alternative (g) named wiring it "the right thing to do". The YAML note on the key
#: still says "NOT CONSUMED"; the release is immutable, so that sentence is stale, not this test.)
_ADDED = ("battery_capex", "dno_assessment_and_design_one_off_gbp_per_site")

#: WP-CORE-01 triage (2026-11-16): the second key gb_v1_11 adds over gb_v1_10. D54
#: (docs/decisions.md) records it verbatim: "the zone clears are added to gb_v1_11 as
#: `local_flex_piclo.awarded_zone_clears` (context, never a default)". It landed in the same commit
#: as this test (12033d0) and the drift assertion below failed at that commit (measured on a clean
#: export of 12033d0), so the expectation predates D54 rather than the key drifting silently.
_ADDED_D54 = ("local_flex_piclo", "awarded_zone_clears")

#: Illustrative annualisation of a capex delta: the CEPA/DESNZ mid hurdle for lithium BESS over a
#: 10-year life.
_HURDLE, _LIFE_YEARS = 0.089, 10
_CRF = _HURDLE * (1 + _HURDLE) ** _LIFE_YEARS / ((1 + _HURDLE) ** _LIFE_YEARS - 1)

_FIXED_LINES = ("opex", "insurance", "metering_idno", "dno", "business_rates")


@pytest.fixture(scope="module")
def site() -> s.SiteModel:
    """A SYNTHETIC campaign-shaped site: a two-rate import tariff and an unassessed counterparty.

    The fixed lines under test (opex, insurance, metering/IDNO, DNO, business rates) read no site
    field, so the pinned figures are the same on any site; the counterparty is `unknown`, as on
    every campaign record. This used to read a campaign record that WP-GRC-05 quarantines until
    its DPA is registered; nothing here needs that customer's data.
    """
    return s.SiteModel(
        site_id="SYNTHETIC-gb-v1-11",
        site_name="SYNTHETIC campaign-shaped site (not a real site)",
        postcode="ZZ1 1AA",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        load_data=s.LoadDataSummary(annual_kwh=1_250_000.0),
        annual_consumption_kwh=1_250_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="SYNTHETIC Energy Supply Co",
            import_rates={"day": 0.2431, "night": 0.1187},
        ),
        connection=s.ConnectionDetails(mic_kw=500.0, dno_region="SSEN_SEPD"),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(
            legal_name="SYNTHETIC Operator A Ltd (not a real company)",
            credit_status=CreditStatus.UNKNOWN,
        ),
    )


def _fixed_stack(lib: AssumptionLibrary, site: s.SiteModel, kw: float) -> tuple[float, float]:
    """(installed capex GBP, total fixed annual opex GBP) at ``kw`` and the 2 h reference.

    Read through the PRODUCTION functions, never off the YAML: the point of the release is that
    these numbers move, so the test has to see the numbers a run would see.
    """
    warnings: list[str] = []
    used: list[str] = []
    capex = estimate_capex(kw / 1000.0, lib, warnings=warnings, used=used, energy_kwh=kw * 2.0)
    lines = build_cost_lines(
        site,
        kw / 1000.0,
        capex.total_gbp,
        lib,
        dispatch_energy=None,
        warnings=warnings,
        used=used,
    )
    fixed = sum(lines[k] or 0.0 for k in _FIXED_LINES)
    return capex.total_gbp, fixed


# --- load / resolve ----------------------------------------------------------


def test_gb_v1_11_loads_with_every_required_section_populated() -> None:
    lib = load_assumptions("gb_v1_11")
    assert lib.metadata.version == "gb_v1_11"
    assert lib.metadata.market == "GB"
    assert lib.metadata.currency == "GBP"
    assert lib.metadata.released.isoformat() == "2026-08-17"
    for section in SECTION_NAMES:
        assert getattr(lib, section), f"required section {section} is empty"
        for name, a in getattr(lib, section).items():
            assert isinstance(a, Assumption), f"{section}.{name} is not an Assumption"


def test_gb_v1_11_is_resolvable_and_the_default_carries_the_corrections() -> None:
    """The corrections must reach production, and must not be droppable by cutting a new release.

    Deliberately NOT `latest_version() == "gb_v1_11"`: that encodes a POSITION which stops being
    true the day gb_v1_12 is cut. The invariant is that the release the CLI resolves is at least
    this one AND still carries the sourced corrections, so a future release cannot quietly restore
    an unsourced GBP 2,700/yr or an optimistic capex while the packs keep reading "latest".
    """
    versions = available_versions()
    assert "gb_v1_11" in versions
    assert versions.index("gb_v1_11") > versions.index("gb_v1_10")
    resolved = latest_version()
    assert resolved != "gb_v0_1"
    assert resolved == versions[-1]
    assert versions.index(resolved) >= versions.index("gb_v1_11")

    default = load_assumptions(resolved)
    assert default.metering_idno["annual_gbp_per_site"].value == 0
    assert default.dno_costs["annual_gbp_per_site"].value == 0
    assert float(default.battery_capex["installed_cost"].value) >= 700000


def test_gb_v1_11_prior_releases_are_untouched() -> None:
    """Superseded, never rewritten: gb_v1_10 must still load with its own numbers."""
    prior = load_assumptions("gb_v1_10")
    assert prior.metadata.version == "gb_v1_10"
    assert prior.metering_idno["annual_gbp_per_site"].value == 1200
    assert prior.dno_costs["annual_gbp_per_site"].value == 1500
    assert prior.battery_capex["installed_cost"].value == 500000
    assert prior.financing["debt_gearing_pct"].value == 60.0


# --- the drift table, proved mechanically ------------------------------------


def test_gb_v1_11_priors_identical_to_gb_v1_10_except_the_enumerated_drift() -> None:
    """Silent value drift between releases is a defect. The release's own claim, enforced."""
    before, after = load_assumptions("gb_v1_10"), load_assumptions("gb_v1_11")
    observed: set[tuple[str, str]] = set()
    for section in _ALL_SECTIONS:
        prior = getattr(before, section) or {}
        current = getattr(after, section) or {}
        for name, was in prior.items():
            key = (section, name)
            assert name in current, f"gb_v1_11 DROPPED {section}.{name} - that breaks callers"
            now = current[name]
            if was.value != now.value:
                observed.add(key)
                assert key in _VALUE_DRIFT, f"{section}.{name} value drifted unannounced"
                expected_before, expected_after = _VALUE_DRIFT[key]
                assert was.value == expected_before
                assert now.value == expected_after
    assert observed == set(_VALUE_DRIFT), (
        f"the release's drift table and the file disagree: missing {set(_VALUE_DRIFT) - observed}"
    )

    added = {
        (section, name)
        for section in _ALL_SECTIONS
        for name in (getattr(after, section) or {})
        if name not in (getattr(before, section) or {})
    }
    assert added == {_ADDED, _ADDED_D54}, f"unexpected added keys: {added - {_ADDED, _ADDED_D54}}"


def test_gb_v1_11_confirms_nothing() -> None:
    """0 confirmed repo-wide: no quote on this release may present as bankable."""
    lib = load_assumptions("gb_v1_11")
    for section in _ALL_SECTIONS:
        for name, a in (getattr(lib, section) or {}).items():
            assert a.status != AssumptionStatus.CONFIRMED, f"{section}.{name} claims confirmed"


@pytest.mark.parametrize("key", sorted(_VALUE_DRIFT) + [_ADDED] + sorted(_RESOURCED_ONLY))
def test_gb_v1_11_every_touched_key_is_documented_dated_and_low_confidence(
    key: tuple[str, str],
) -> None:
    """Each touched key carries evidence, a 2026 review date, and no confidence promotion.

    A cost correction that RAISES confidence on its own authority is the failure this guards: the
    capex magnitude rests on one commercial blog and must say so.
    """
    section, name = key
    a = getattr(load_assumptions("gb_v1_11"), section)[name]
    assert a.unit, f"{section}.{name} has no unit"
    assert len(a.source) > 200, f"{section}.{name} source is too thin to audit"
    assert a.confidence == Confidence.LOW, f"{section}.{name} promoted its own confidence"
    assert a.last_reviewed.isoformat() >= "2026-08-12", f"{section}.{name} carries a stale vintage"
    assert "DIRECTION" in a.notes.upper(), (
        f"{section}.{name} does not state its direction of effect"
    )


def test_gb_v1_11_the_two_deleted_lines_state_that_they_flatter() -> None:
    """Both GBP 0 lines move every site in the FAVOURABLE direction, so each must say so."""
    lib = load_assumptions("gb_v1_11")
    for section in ("metering_idno", "dno_costs"):
        notes = lib_notes = getattr(lib, section)["annual_gbp_per_site"].notes.upper()
        assert "DOWN" in notes
        assert "BETTER" in lib_notes
        assert "CAPEX" in lib_notes, (
            f"{section} does not point at the offsetting capex correction; the two must be read "
            "together or the release reads as a free saving"
        )


def test_gb_v1_11_the_unconsumed_one_off_says_it_is_unconsumed() -> None:
    """A key nothing reads is not a fix, and the register entry must not imply that it is.

    `finance.cashflows.estimate_capex` prices capex per MW and per kWh only, so this GBP 1,557
    one-off does not enter capex today. That UNDERSTATES capex and therefore flatters every return,
    which is exactly the kind of thing that must be stated on the key rather than discovered later.
    """
    section, name = _ADDED
    a = getattr(load_assumptions("gb_v1_11"), section)[name]
    assert a.value == 1557
    assert "one-off" in a.unit
    blob = f"{a.source} {a.notes}".upper()
    assert "NOT CONSUMED" in blob
    assert "UNDERSTATED" in blob or "UNDERSTATES" in blob


# --- the numbers, read through the production cost functions -----------------


#: WP-CORE-01 triage (2026-11-16). Re-pinned for the FIXED capex term. D49 (docs/decisions.md)
#: landed gb_v1_11 with the GBP 1,557 one-off unconsumed and recorded, as alternative (g), that
#: wiring it into capex was "the right thing to do" and its omission understated capex. f3aedd8
#: (FIX 3/7, 2026-08-19; changelog 2026-08-19 item 3) did exactly that: `_fixed_capex_gbp` adds the
#: key to BOTH capex shapes. Recomputed by hand, 2 h reference:
#:   capex = 210,000 GBP/MW x MW + 245 GBP/kWh x kWh + 1,557
#:     25 kW:  5,250 + 12,250 + 1,557 =  19,057   (was 17,500)
#:    100 kW: 21,000 + 49,000 + 1,557 =  71,557   (was 70,000)
#:    750 kW: 157,500 + 367,500 + 1,557 = 526,557 (was 525,000)
#:   fixed = previous fixed + insurance 0.5% x 1,557 = +7.785, rounded to the penny by
#:   build_cost_lines: 595.28 / 2,357.79 / 17,632.78.
_FIXED_CAPEX_GBP = 1_557.0


@pytest.mark.parametrize(
    ("kw", "capex_gbp", "fixed_gbp"),
    [
        (25.0, 19_057.0, 595.28),
        (100.0, 71_557.0, 2_357.79),
        (750.0, 526_557.0, 17_632.78),
    ],
)
def test_gb_v1_11_capex_and_fixed_stack_reach_the_cost_model(
    site: s.SiteModel, kw: float, capex_gbp: float, fixed_gbp: float
) -> None:
    """The values FLOW, not merely exist: measured through the functions a run calls.

    The fixed stack is now opex + insurance + business_rates only, because metering_idno and dno
    are GBP 0 - and the insurance line is 40% higher than it was, at an unchanged 0.5%, because it
    is a percentage of the corrected capex.
    """
    lib = load_assumptions("gb_v1_11")
    capex, fixed = _fixed_stack(lib, site, kw)
    assert capex == pytest.approx(capex_gbp, abs=0.01)
    assert fixed == pytest.approx(fixed_gbp, abs=0.01)

    warnings: list[str] = []
    used: list[str] = []
    lines = build_cost_lines(
        site, kw / 1000.0, capex, lib, dispatch_energy=None, warnings=warnings, used=used
    )
    assert lines["metering_idno"] == 0.0
    assert lines["dno"] == 0.0


def test_gb_v1_11_net_effect_is_worse_at_the_reference_size(site: s.SiteModel) -> None:
    """**The anti-cherry-pick invariant.** Both corrections, netted, at 100 kW / 2 h.

    Removing GBP 2,700/yr of unsourced flat cost flips these sites toward viability on a simplified
    cost model. Correcting capex takes them back past where they started. Landing the first without
    the second would manufacture a positive IRR out of the favourable half of the evidence, so this
    test fails if a later edit keeps the zeros and reverts the capex.
    """
    v10, v11 = load_assumptions("gb_v1_10"), load_assumptions("gb_v1_11")
    capex_10, fixed_10 = _fixed_stack(v10, site, 100.0)
    capex_11, fixed_11 = _fixed_stack(v11, site, 100.0)

    # WP-CORE-01 triage (2026-11-16): re-pinned for f3aedd8's fixed capex term (see
    # _FIXED_CAPEX_GBP above; D49 alternative (g)). gb_v1_10 publishes no such key, so it adds 0
    # there and 1,557 here. Recomputed by hand:
    #   fixed: -2,600.00 + 0.5% x 1,557 = -2,592.215  (measured -2,592.21 after penny rounding)
    #   capex: 20,000 + 1,557 = 21,557
    #   capital charge: 21,557 x CRF(8.9%, 10 y) 0.1551349 = 3,344.24
    #   net: -2,592.21 + 3,344.24 = +752.03 (was +502.70). Still WORSE, so the invariant holds.
    assert fixed_11 - fixed_10 == pytest.approx(-2_600.00 + 0.005 * _FIXED_CAPEX_GBP, abs=0.01)
    assert capex_11 - capex_10 == pytest.approx(20_000.00 + _FIXED_CAPEX_GBP, abs=0.01)  # worse
    capital_charge = (capex_11 - capex_10) * _CRF
    assert capital_charge == pytest.approx(3_344.24, abs=1.0)

    net = (fixed_11 - fixed_10) + capital_charge
    assert net > 0.0, (
        "gb_v1_11 must be WORSE than gb_v1_10 at the 100 kW reference. A positive net is the "
        "evidence that both halves of the correction landed; a negative one means the capex "
        "correction was dropped and only the favourable half survives."
    )
    assert net == pytest.approx(752.03, abs=1.0)


def test_gb_v1_11_is_better_for_small_sites_and_worse_for_large_ones(site: s.SiteModel) -> None:
    """The size dependence is a fact about the release and must not be reported as one number.

    The two deleted lines are size-INVARIANT (no `power_mw` multiplier at all) while the capex
    correction scales, so the release helps a 25 kW site and hurts a 750 kW one. The crossover sits
    near 84 kW at 2 h, which is inside the campaign's own candidate range (25-300 kW).
    """
    v10, v11 = load_assumptions("gb_v1_10"), load_assumptions("gb_v1_11")

    def net(kw: float) -> float:
        c10, f10 = _fixed_stack(v10, site, kw)
        c11, f11 = _fixed_stack(v11, site, kw)
        return (f11 - f10) + (c11 - c10) * _CRF

    # WP-CORE-01 triage (2026-11-16): re-pinned for f3aedd8's fixed capex term (D49 alternative
    # (g)). Each net moves by (0.005 + CRF) x 1,557 = 0.1601349 x 1,557 = +249.33:
    #   25 kW:  -1,899.33 + 249.33 = -1,650.00   (measured -1,650.0004)
    #   750 kW: 21,320.24 + 249.33 = 21,569.57   (measured 21,569.56)
    # The crossover moves from ~84.3 kW to ~76.5 kW at 2 h; still inside the 25-300 kW range, so
    # the size dependence this test exists for is unchanged in sign at both ends.
    assert net(25.0) == pytest.approx(-1_650.00, abs=1.0)
    assert net(25.0) < 0.0
    assert net(750.0) == pytest.approx(21_569.56, abs=1.0)
    assert net(750.0) > 0.0
    assert net(25.0) < net(100.0) < net(750.0)


def test_gb_v1_11_capex_decomposition_still_reconciles_at_the_two_hour_reference() -> None:
    """210,000 + 2,000 kWh/MW x 245 = 700,000 GBP/MW, or the decomposition contradicts the all-in.

    The consumer prefers the decomposition whenever energy is supplied, so a decomposition that
    disagreed with `installed_cost` would make the priced capex depend on which caller ran.
    """
    capex = load_assumptions("gb_v1_11").battery_capex
    power = float(capex["installed_cost_gbp_per_mw_power_component"].value)
    per_kwh = float(capex["installed_cost_gbp_per_kwh"].value)
    all_in = float(capex["installed_cost"].value)
    assert power + 2_000.0 * per_kwh == pytest.approx(all_in, abs=0.01)

    band = capex["installed_cost_benchmark_band"].value
    assert band["low"] <= all_in <= band["high"], (
        "the corrected capex left the file's own plausibility band; one of them is wrong"
    )
    survey = capex["installed_cost_grid_scale_survey_benchmark"].value
    assert all_in > survey["survey_average_gbp_per_mw"], (
        "a sub-MW BTM system must not be priced below the GRID-SCALE survey average: fixed costs "
        "do not scale down, so cheaper-than-grid-scale is the one direction the evidence excludes"
    )


def test_gb_v1_11_business_rates_records_the_law_without_assuming_the_exemption() -> None:
    """SI 2022/405 is sourced on the key; Knowl Hill's biogas question stays a DATA ASK.

    Modelling the exemption without a surveyor's opinion would invent a GBP 0 rates line, which is
    the mirror image of the placeholder problem this release is fixing.
    """
    a = load_assumptions("gb_v1_11").business_rates["annual_gbp_per_mw"]
    assert a.value == 8000, "the rates VALUE must not move: no GBP/MW RV scheme is published"
    blob = f"{a.source} {a.notes}"
    assert "SI 2022 No. 405" in blob or "SI 2022/405" in blob
    assert "mainly or exclusively" in blob
    assert "31 March 2035" in blob
    assert "grid-charged" in blob.lower()
    assert "DATA ASK" in blob.upper()
    assert "Knowl Hill" in blob
    assert "Small Business Rate Relief" in blob or "SMALL BUSINESS RATE RELIEF" in blob.upper()


def test_gb_v1_11_gearing_change_is_sourced_and_claims_no_dscr(
    site: s.SiteModel,
) -> None:
    """50% is the CEPA/DESNZ notional; the key must not present it as enabling a levered case."""
    lib = load_assumptions("gb_v1_11")
    a = lib.financing["debt_gearing_pct"]
    assert a.value == 50.0
    assert "CEPA" in a.source
    blob = f"{a.source} {a.notes}".upper()
    assert "UNLEVERED" in blob
    assert "NO PRICED FIGURE MOVES" in blob or "NOT TO ENABLE A DSCR" in blob

    # The claim is measured, not asserted. Every campaign record is `unknown`, and an unassessed
    # counterparty assembles NO debt terms, so this key is not read on any of their runs...
    assert site.counterparty is not None
    assert site.counterparty.credit_status == CreditStatus.UNKNOWN
    for status in (CreditStatus.UNKNOWN, CreditStatus.NOT_ASSESSED):
        rate_tenor, terms, _notes = build_debt_terms_from_credit(status, lib)
        assert rate_tenor is None and terms is None, (
            "an unassessed counterparty must never default into a credit band: that is what makes "
            "the unlevered basis a refusal rather than an invention"
        )
    # ...and it IS reachable, so the corrected value flows the moment an assessment exists.
    _rt, assessed, _n = build_debt_terms_from_credit(CreditStatus.STRONG, lib)
    assert assessed is not None
    assert assessed.gearing_pct == 50.0
