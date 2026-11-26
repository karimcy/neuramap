"""Tests for the assumption-schema evolution mechanism (D30, north_star Wave 8).

The invariants: frozen releases (gb_v0_1, gb_v1_0) load UNCHANGED with the new optional
sections empty; a release carrying an optional section validates and resolves through the same
`get()` door; a mistyped section is still a hard error (extra='forbid' intact); and the
required-section list is guarded at exactly its 23 names — shrinking or renaming it is a
breaking event this test refuses.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from gridq.assumptions import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    Assumption,
    AssumptionLibrary,
    AssumptionStatus,
    load_assumptions,
)


def test_required_sections_are_exactly_the_frozen_23() -> None:
    assert len(SECTION_NAMES) == 23
    assert OPTIONAL_SECTION_NAMES == ("availability", "penalties", "tnuos", "bill_products")
    assert set(SECTION_NAMES).isdisjoint(OPTIONAL_SECTION_NAMES)


@pytest.mark.parametrize("version", ["gb_v0_1", "gb_v1_0"])
def test_frozen_releases_load_unchanged_with_empty_optional_sections(version: str) -> None:
    lib = load_assumptions(version)
    for section in OPTIONAL_SECTION_NAMES:
        assert getattr(lib, section) == {}, (
            f"{version} predates '{section}' — it must load as empty, never invented"
        )


def test_a_release_carrying_an_optional_section_resolves_through_get() -> None:
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["penalties"] = {
        "cm_penalty_rate_fraction_of_clearing": {
            "value": None,
            "unit": "fraction",
            "source": "Capacity Market Rules (to be cited on population)",
            "status": "placeholder",
            "owner_or_reviewer": "Olly",
            "confidence": "low",
            "last_reviewed": str(date(2026, 7, 2)),
            "notes": "Placeholder home only — populated in gb_v1_1 with the sourced regime.",
        }
    }
    lib = AssumptionLibrary.model_validate(base)
    a = lib.get("penalties", "cm_penalty_rate_fraction_of_clearing")
    assert isinstance(a, Assumption)
    assert a.status == AssumptionStatus.PLACEHOLDER
    assert a.value is None  # a home is not a number — nothing invented


def test_a_mistyped_section_is_still_a_hard_error() -> None:
    base = load_assumptions("gb_v0_1").model_dump(mode="json")
    base["penaltys"] = {}  # typo — must never become a silent no-op
    with pytest.raises(ValidationError):
        AssumptionLibrary.model_validate(base)


def test_gb_v1_1_folds_the_credit_policy_and_populates_the_growth_sections() -> None:
    # The W8 release: the D19/D18 divergence closes and the D30 homes carry labelled
    # placeholders — nothing confirmed, every entry under the full 8-field discipline.
    lib = load_assumptions("gb_v1_1")
    assert lib.metadata.version == "gb_v1_1"
    # (a) the fold: the v1 line can now resolve the M9 credit mapping, identically to v0_1.
    assert (
        lib.financing["credit_rate_tenor_policy"].value
        == load_assumptions("gb_v0_1").financing["credit_rate_tenor_policy"].value
    )
    # (b) the growth sections are populated, all placeholder, never confirmed.
    assert set(lib.availability) == {"availability_price_gbp_per_mw_h", "expected_availability_pct"}
    assert set(lib.penalties) == {
        "cm_penalty_rate_fraction_of_clearing",
        "cm_monthly_cap_pct_of_revenue",
        "cm_annual_cap_pct_of_revenue",
    }
    assert set(lib.tnuos) == {"demand_residual_gbp_per_year"}
    for section in OPTIONAL_SECTION_NAMES:
        for name, a in getattr(lib, section).items():
            assert a.status == AssumptionStatus.PLACEHOLDER, f"{section}.{name} must be placeholder"
    # Unsourced homes stay empty-valued; the research-grade caps name their unverified source.
    assert lib.availability["availability_price_gbp_per_mw_h"].value is None
    assert lib.tnuos["demand_residual_gbp_per_year"].value is None
    assert "UNVERIFIED" in lib.penalties["cm_annual_cap_pct_of_revenue"].source
    # Prior releases stay frozen: no fold, no growth content.
    prev = load_assumptions("gb_v1_0")
    assert "credit_rate_tenor_policy" not in prev.financing
    for section in OPTIONAL_SECTION_NAMES:
        assert getattr(prev, section) == {}


def test_gb_v1_1_penalty_cap_activates_the_w8_penalty_line() -> None:
    # The release content exercises the W8 machinery end-to-end: contracted CM revenue on
    # gb_v1_1 produces the bounded worst-case P90 penalty line — still not bankable.
    from tests.conftest import _make_site

    import gridq.schemas as s
    from gridq.finance.cashflows import build_cashflow_model

    result = s.OptimisationResult(
        result_id="res_v11",
        request_id="req_v11",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(
                stream=s.MarketStream.CAPACITY_MARKET,
                annual_revenue_gbp=9_000.0,
                firmness=s.Firmness.CONTRACTED,
            )
        ],
        solver_status=s.SolverStatus.BASELINE,
    )
    model = build_cashflow_model(_make_site(), result, load_assumptions("gb_v1_1"))
    # REBASELINED for N2 (2026-08-10 review): was the FULL 100% annual cap (£9,000), which was
    # then subtracted on top of the P90 haircut already taken on the same £9,000 of CM revenue —
    # two deductions for one event, so holding the agreement made P90 EBITDA worse than not
    # holding it. The line is now the cap scaled by the SAME 15% delivery shortfall the contracted
    # haircut applies: 100% × £9,000 × 0.15 = £1,350.
    assert model.p90_penalty_exposure_gbp == pytest.approx(1_350.0)
    assert model.bankable is False
