"""Validation Gate 2 — the half-hourly basis is RECORDED, never a reason to block.

`docs/validation_requirements.md` §1 Gate 2 and `docs/decisions.md` **D44** (Olly, 2026-08-12): no
missing input may block a validation run. Real metered data is used whenever it exists; where it
does not, a modelled shape scaled to the site's own totals is standard practice; where no shape has
been generated yet the run still completes — honestly, with the missing input recorded, the band
capped and the confirmation ask stated.

**These tests were inverted by D44.** They previously pinned the refusals (`HalfHourlyMandateError`
on a missing series). The refusal is withdrawn as policy, so they now pin the record-and-continue
behaviour that replaced it: the same conditions, the same evidence, no exception. The one refusal
that remains is a *corrupt* basis string — bad data, not absent data.
"""

from __future__ import annotations

import pytest

from gridq.assumptions import load_assumptions
from gridq.ingestion.hh_mandate import (
    HalfHourlyBasis,
    HalfHourlyMandateError,
    admit_half_hourly,
)


def _assumptions():
    return load_assumptions("gb_v1_6")


def _metered(make_site):
    """A site that DECLARES its series as metered — the only way to reach the top ceiling (N9)."""
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "metered"
    return site


def test_metered_series_is_admitted_as_metered(make_site) -> None:
    site = _metered(make_site)
    assert site.load_data.half_hourly_file_ref, "fixture must carry an HH ref"
    admission = admit_half_hourly(site)
    assert admission.basis is HalfHourlyBasis.METERED
    assert admission.is_metered
    assert admission.has_series
    assert admission.confidence_ceiling == "high"
    assert not admission.derived, "nothing was assumed, so nothing is recorded"


def test_an_undeclared_basis_is_never_read_as_metered(make_site) -> None:
    """N9 route 2: a generated series is a file on disk exactly like a metered one.

    Defaulting an absent basis to METERED handed every unlabelled series the top confidence band.
    The run still prices the data it has — that is D44 — but the provenance is an open question and
    is recorded as one.
    """
    site = make_site()
    assert site.load_data.half_hourly_basis is None, "fixture must not declare a basis"

    admission = admit_half_hourly(site)
    assert admission.basis is None
    assert not admission.is_metered
    assert admission.has_series, "the attached series is still priced — the run is not blocked"
    assert admission.confidence_ceiling == "medium", "the top band is withheld, not the run"

    (record,) = admission.derived
    assert record.kind == "derived_input"
    assert record.key == "load_data.half_hourly_basis"
    assert record.value is None, "the gate must not guess 'metered'"
    assert record.derivation_method == "not_derivable"
    assert "half_hourly_basis" in record.confirmation_ask


def test_no_hh_but_known_annual_records_and_continues(make_site) -> None:
    """The remedy for a missing meter export is to MODEL the shape, and the record must say so."""
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_file_ref = None
    site.load_data.annual_kwh = 908_590.0

    admission = admit_half_hourly(site)  # D44: no exception

    assert admission.basis is None
    assert not admission.has_series
    assert admission.confidence_ceiling == "low", "no series at all is structural, not shape-like"
    (record,) = admission.derived
    assert record.key == "load_data.half_hourly_file_ref"
    assert record.value == "non_domestic_unrestricted", (
        "the deliberate archetype for an unknown business type must be named, not implied"
    )
    assert record.derivation_method == "sourced_default"
    assert "908,590" in record.basis, "the record must name the total a shape would be scaled to"
    assert "hh_synthesis" in record.basis, "the record must name the remedy"
    assert "not a degradation" in record.basis, (
        "generating a shape is standard practice; the record must not frame it as a fallback"
    )
    assert record.confidence_ceiling == "low"


def test_no_hh_and_no_totals_records_not_derivable_and_continues(make_site) -> None:
    """With no totals there is nothing to scale to — and still no refusal (D44).

    The honest outcome is a completed run that priced nothing, carrying a `not_derivable` record.
    A shape may be modelled; the site's energy may not be invented.
    """
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_file_ref = None
    site.load_data.annual_kwh = None
    site.annual_consumption_kwh = None

    admission = admit_half_hourly(site)

    assert admission.basis is None
    assert not admission.has_series
    assert admission.confidence_ceiling == "low"
    (record,) = admission.derived
    assert record.derivation_method == "not_derivable"
    assert record.value is None, "no total may be invented to scale a shape to"
    assert "inventing" in record.basis


def test_the_release_in_force_is_stamped_on_a_gate_record(make_site) -> None:
    """Provenance: a record must say which assumption release was in force when it was made."""
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_file_ref = None

    (record,) = admit_half_hourly(site, release="gb_v1_6").derived
    assert record.derived_on_release == "gb_v1_6"
    assert record.derived_by == "gridq.ingestion.hh_mandate", (
        "a deterministic record names the module that made it, never a model"
    )
    # Absent is honest absence, never a placeholder string.
    assert admit_half_hourly(site).derived[0].derived_on_release is None


def test_modelled_basis_cannot_reach_high_confidence() -> None:
    """A modelled shape is evidence about a typical site of this type, not about this site."""
    from gridq.ingestion.hh_mandate import HalfHourlyAdmission

    admission = HalfHourlyAdmission(
        basis=HalfHourlyBasis.MODELLED,
        file_ref="data/sites/generated.csv",
        scaled_to="12 monthly invoice totals, 2025",
        reference_profile="Elexon profile class 3 (non-domestic unrestricted)",
    )
    assert admission.confidence_ceiling == "medium"
    assert not admission.is_metered


def test_generate_quote_never_blocks_on_a_missing_half_hourly_basis(make_site) -> None:
    """D44 at the choke point: the run COMPLETES and states what is missing.

    This is the inverted test. It previously asserted `HalfHourlyMandateError`; the whole point of
    D44 is that a missing input produces an auditable result instead of an exception. What replaces
    the refusal is not silence: the band is LOW, the record is on the quote, and no spec is priced.
    """
    from gridq.quotes import generate_quote

    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_file_ref = None
    site.load_data.annual_kwh = None
    site.annual_consumption_kwh = None

    run = generate_quote(site, _assumptions())

    assert run.quote.quote_id, "the run completed and produced a stamped quote"
    assert run.bankable is False
    assert run.quote.confidence_band == "low"
    assert run.quote.half_hourly_basis is None, "no basis was established, and none was invented"
    assert any("Half-hourly basis: NONE" in w for w in run.quote.warnings)
    assert any("ASSUMPTION REQUIRING CONFIRMATION" in w for w in run.quote.warnings)
    assert [r.key for r in run.derived_assumptions] == ["load_data.half_hourly_file_ref"]


def test_generate_quote_records_the_basis_on_the_quote(make_site) -> None:
    """A reader must never have to ask which sites were metered."""
    from gridq.quotes import generate_quote

    run = generate_quote(_metered(make_site), _assumptions())
    assert any("Half-hourly basis: METERED" in w for w in run.quote.warnings), (
        f"the HH basis must travel onto the quote; got {run.quote.warnings}"
    )


def test_a_modelled_series_is_not_reported_as_metered(make_site) -> None:
    """A generated series is a file on disk exactly like a metered one.

    Inferring "metered" from the presence of a path would silently promote every modelled site to
    the top confidence band. The basis is read from the record, never from the filesystem.
    """
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "modelled"
    site.load_data.half_hourly_reference_profile = "waste_mrf (archetype_pending_source)"
    site.load_data.half_hourly_scaled_to = "12 monthly invoice totals, 2025"

    admission = admit_half_hourly(site)
    assert admission.basis is HalfHourlyBasis.MODELLED
    assert not admission.is_metered
    assert admission.confidence_ceiling == "medium"
    assert admission.reference_profile == "waste_mrf (archetype_pending_source)"


def test_an_unrecognised_basis_is_refused(make_site) -> None:
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "vibes"
    with pytest.raises(HalfHourlyMandateError, match="not a recognised basis"):
        admit_half_hourly(site)


def test_generate_quote_reports_a_modelled_basis_on_the_quote(make_site) -> None:
    from gridq.quotes import generate_quote

    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "modelled"
    site.load_data.half_hourly_reference_profile = "waste_mrf (archetype_pending_source)"
    site.load_data.half_hourly_scaled_to = "12 monthly invoice totals, 2025"

    run = generate_quote(site, _assumptions())
    assert any("Half-hourly basis: MODELLED" in w for w in run.quote.warnings)
    assert any("capped at MEDIUM" in w for w in run.quote.warnings), (
        "a modelled basis must state its confidence ceiling on the quote"
    )


def test_the_hh_fold_does_not_promote_a_modelled_series_to_metered(make_site) -> None:
    """N9: a modelled series is a CSV like any other, so the fold cannot re-derive its basis.

    Dropping the recorded basis silently promoted every generated series to METERED and its
    confidence ceiling from `medium` to `high` — defeating Gate 2 on four of the five live sites.
    """
    from gridq.ingestion import load_half_hourly_csv, summarise_half_hourly_data
    from gridq.ingestion.assembly import assemble_site_model

    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "modelled"
    site.load_data.half_hourly_reference_profile = "waste_mrf (archetype_pending_source)"
    site.load_data.half_hourly_scaled_to = "12 monthly invoice totals, 2025"

    df = load_half_hourly_csv("examples/demo_site/hh_2025.csv", actor="test")
    summary = summarise_half_hourly_data(df, file_ref="examples/demo_site/hh_2025.csv")
    folded = assemble_site_model(site, hh_summary=summary)

    assert folded.load_data.half_hourly_basis == "modelled", (
        "the HH fold must not wipe the recorded basis"
    )
    assert admit_half_hourly(folded).basis is HalfHourlyBasis.MODELLED
    assert admit_half_hourly(folded).confidence_ceiling == "medium"
