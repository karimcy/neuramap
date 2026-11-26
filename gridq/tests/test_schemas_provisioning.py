"""Tests for the ProvisioningRequirement schema (issue 017 stage 1, C033 seam module).

The declared part is minimal and auditable: a requirement must demand something (declared
addition and/or the metered shortfall), and the only £ field — the customer's declared
counterfactual — cannot exist without its verbatim basis (a figure with no basis is an
invented number). SiteModel is untouched: this module is additive, the wiring is C033.
"""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError

from gridq.schemas import AvailabilityWindow, ProvisioningRequirement


def _window() -> AvailabilityWindow:
    return AvailabilityWindow(start_time=time(18, 0), end_time=time(19, 0))


def test_minimal_declared_addition_is_valid() -> None:
    req = ProvisioningRequirement(
        requirement_id="prov-1", windows=[_window()], additional_load_kw=120.0
    )
    assert req.additional_load_kw == 120.0
    assert req.serve_metered_shortfall is False
    assert req.import_cap_kw is None  # resolved by the consumer, never defaulted here


def test_metered_shortfall_alone_is_valid() -> None:
    req = ProvisioningRequirement(
        requirement_id="prov-2", windows=[_window()], serve_metered_shortfall=True
    )
    assert req.additional_load_kw is None


def test_a_requirement_that_demands_nothing_is_rejected() -> None:
    with pytest.raises(ValidationError, match="demands nothing"):
        ProvisioningRequirement(requirement_id="prov-3", windows=[_window()])


def test_windows_are_required() -> None:
    with pytest.raises(ValidationError):
        ProvisioningRequirement(requirement_id="prov-4", windows=[], additional_load_kw=50.0)


def test_counterfactual_requires_its_basis() -> None:
    with pytest.raises(ValidationError, match="invented number"):
        ProvisioningRequirement(
            requirement_id="prov-5",
            windows=[_window()],
            additional_load_kw=50.0,
            declared_counterfactual_gbp_per_year=45_000.0,
        )
    # whitespace is not a basis
    with pytest.raises(ValidationError, match="invented number"):
        ProvisioningRequirement(
            requirement_id="prov-5b",
            windows=[_window()],
            additional_load_kw=50.0,
            declared_counterfactual_gbp_per_year=45_000.0,
            counterfactual_basis="   ",
        )


def test_basis_without_a_figure_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not there"):
        ProvisioningRequirement(
            requirement_id="prov-6",
            windows=[_window()],
            additional_load_kw=50.0,
            counterfactual_basis="NGED reinforcement quote ref 123",
        )


def test_counterfactual_with_basis_is_valid() -> None:
    req = ProvisioningRequirement(
        requirement_id="prov-7",
        windows=[_window()],
        additional_load_kw=50.0,
        tenor_years=15.0,
        counterparty="Robert Thomas Farms",
        reference="contract-RTF-2026-07",
        declared_counterfactual_gbp_per_year=45_000.0,
        counterfactual_basis="NGED reinforcement quote ref 123, annualised over 15y",
    )
    assert req.declared_counterfactual_gbp_per_year == 45_000.0


@pytest.mark.parametrize(
    "field, value",
    [
        ("additional_load_kw", 0.0),
        ("additional_load_kw", -5.0),
        ("import_cap_kw", 0.0),
        ("tenor_years", 0.0),
        ("declared_counterfactual_gbp_per_year", 0.0),
    ],
)
def test_non_positive_numerics_are_rejected(field: str, value: float) -> None:
    kwargs: dict = {
        "requirement_id": "prov-8",
        "windows": [_window()],
        "serve_metered_shortfall": True,
        field: value,
    }
    if field == "declared_counterfactual_gbp_per_year":
        kwargs["counterfactual_basis"] = "a basis"
    with pytest.raises(ValidationError):
        ProvisioningRequirement(**kwargs)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ProvisioningRequirement(
            requirement_id="prov-9",
            windows=[_window()],
            additional_load_kw=50.0,
            unmet_load_kw=99.0,  # the derived series is never a schema input
        )


# ------------------------------------------------- manual daily curve (D42)


def _hgv_profile() -> list[float]:
    # an easy HGV-depot shape: two 350 kW chargers busy 18:00-21:00, tapering
    p = [0.0] * 48
    for slot in range(36, 42):  # 18:00-21:00
        p[slot] = 700.0 if slot < 40 else 350.0
    return p


def test_daily_curve_is_a_valid_addition_shape() -> None:
    req = ProvisioningRequirement(
        requirement_id="prov-10",
        windows=[AvailabilityWindow(start_time=time(18, 0), end_time=time(21, 0))],
        additional_load_profile_daily_kw=_hgv_profile(),
    )
    assert req.additional_load_kw is None
    assert req.additional_load_profile_daily_kw is not None
    assert len(req.additional_load_profile_daily_kw) == 48


def test_daily_curve_must_have_48_slots() -> None:
    with pytest.raises(ValidationError, match="48"):
        ProvisioningRequirement(
            requirement_id="prov-11",
            windows=[_window()],
            additional_load_profile_daily_kw=[100.0] * 24,
        )


def test_daily_curve_rejects_negative_and_all_zero() -> None:
    bad = [0.0] * 48
    bad[10] = -5.0
    with pytest.raises(ValidationError, match=">= 0"):
        ProvisioningRequirement(
            requirement_id="prov-12", windows=[_window()], additional_load_profile_daily_kw=bad
        )
    with pytest.raises(ValidationError, match="all-zero"):
        ProvisioningRequirement(
            requirement_id="prov-13",
            windows=[_window()],
            additional_load_profile_daily_kw=[0.0] * 48,
        )


def test_flat_kw_and_daily_curve_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="not both"):
        ProvisioningRequirement(
            requirement_id="prov-14",
            windows=[_window()],
            additional_load_kw=100.0,
            additional_load_profile_daily_kw=_hgv_profile(),
        )


def test_a_daily_curve_alone_demands_something() -> None:
    req = ProvisioningRequirement(
        requirement_id="prov-15",
        windows=[_window()],
        additional_load_profile_daily_kw=_hgv_profile(),
    )
    assert not req.serve_metered_shortfall


# ------------------------------------- SiteModel wiring + hash stability (D42)


def test_sitemodel_accepts_requirements_and_defaults_none(make_site) -> None:
    site = make_site()
    assert site.provisioning_requirements is None  # additive default: nothing declared
    site2 = site.model_copy(
        update={
            "provisioning_requirements": [
                ProvisioningRequirement(
                    requirement_id="prov-16", windows=[_window()], additional_load_kw=100.0
                )
            ]
        }
    )
    assert site2.provisioning_requirements is not None


def test_canonical_dump_is_hash_stable_when_no_requirement_declared(make_site) -> None:
    """D42 hash policy: absent requirements are POPPED from the canonical dump, so every
    pre-existing site's input_hash / run_id is byte-stable across the schema growth; a set
    requirement enters the identity."""
    from gridq.quotes.assembly import _canonical_site_dump

    site = make_site()
    dump = _canonical_site_dump(site)
    assert "provisioning_requirements" not in dump  # identical bytes to the pre-D42 dump
    site2 = site.model_copy(
        update={
            "provisioning_requirements": [
                ProvisioningRequirement(
                    requirement_id="prov-17", windows=[_window()], additional_load_kw=100.0
                )
            ]
        }
    )
    dump2 = _canonical_site_dump(site2)
    assert "provisioning_requirements" in dump2  # a real input must enter the identity
