"""D44 — the gates record and continue, and Gate 3 is finally on the real path.

`docs/decisions.md` **D44** (Olly, 2026-08-12). Two behaviours are pinned here:

**Gate 2** (`ingestion/hh_mandate.py`) never blocks a run for missing data. The run completes, the
band is capped, and the missing input is stated on the quote with its confirmation ask.

**Gate 3** (`ingestion/reconciliation.py`) is *wired*. It was built, tested, and called from nowhere
in `src/` (register contradiction C-6): `assess_confidence` was called without ``reconciles=``, so
the reconciliation factor permanently reported "was not reconciled" and a gate the validation
requirements call mandatory capped nothing on any production run. These tests fail if that wiring is
ever removed again.

The third property is the one that makes recording safe: **recording a divergence is not licence to
resolve it.** Gate 3 reports; it never prefers, averages or re-scales one side onto the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.ingestion.hh_data import load_half_hourly_csv, summarise_half_hourly_data
from gridq.ingestion.hh_mandate import HalfHourlyBasis, admit_half_hourly
from gridq.ingestion.hh_synthesis import (
    DEFAULT_PROFILE_ID,
    DEFAULT_SYNTHESIS_YEAR,
    SynthesisError,
    derive_half_hourly,
)
from gridq.ingestion.reconciliation import (
    reconcile_declared_against_measured,
    reconciliation_records,
)
from gridq.quotes import generate_quote

DEMO_HH = "examples/demo_site/hh_2025.csv"


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_6")


@pytest.fixture(scope="module")
def measured():
    frame = load_half_hourly_csv(DEMO_HH, actor="test")
    return summarise_half_hourly_data(frame, file_ref=DEMO_HH)


def _reconciling_site(make_site, measured) -> s.SiteModel:
    """A site whose DECLARED figures match the series that will actually be priced."""
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "metered"
    site.load_data.annual_kwh = measured.annualised_import_kwh or measured.total_import_kwh
    site.load_data.max_import_kw = measured.max_import_kw
    site.load_data.half_hourly_rows = measured.coverage.row_count
    site.annual_consumption_kwh = site.load_data.annual_kwh
    return s.SiteModel.model_validate(site.model_dump(mode="json"))


# --- Gate 3 is wired (C-6) ---------------------------------------------------------------------


def test_gate_3_runs_on_the_real_path_and_reports_a_reconciled_basis(
    make_site, assumptions, measured
) -> None:
    """The wiring test. Before this, this factor could only ever say "was not reconciled"."""
    run = generate_quote(_reconciling_site(make_site, measured), assumptions)

    assert run.reconciliation is not None, "Gate 3 must run on the production path"
    assert run.reconciliation.reconciles
    assert run.confidence is not None
    factor = next(f for f in run.confidence.factors if f.name == "load reconciliation")
    assert factor.ceiling is s.ConfidenceBand.HIGH
    assert "reconciles with the measured series" in factor.reason


def test_a_gate_3_breach_records_caps_the_band_and_lets_the_run_finish(
    make_site, assumptions
) -> None:
    """A declared annual the measured series cannot support — the Grundon four-month-sum shape."""
    site = _bad_declaration(make_site)

    run = generate_quote(site, assumptions)  # D44: completes, does not raise

    assert run.quote.quote_id
    assert run.reconciliation is not None and not run.reconciliation.reconciles
    factor = next(f for f in run.confidence.factors if f.name == "load reconciliation")
    assert factor.ceiling is s.ConfidenceBand.LOW
    assert run.quote.confidence_band is s.ConfidenceBand.LOW
    assert any("Load reconciliation (Gate 3)" in w for w in run.quote.warnings)
    assert any("Basis chosen: UNCHANGED" in w for w in run.quote.warnings)
    assert "load_data.annual_kwh" in {r.key for r in run.derived_assumptions}


def _bad_declaration(make_site) -> s.SiteModel:
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "metered"
    site.load_data.annual_kwh = 4_000_000.0  # the file cannot support this
    site.annual_consumption_kwh = 4_000_000.0
    return s.SiteModel.model_validate(site.model_dump(mode="json"))


def test_gate_3_records_the_discrepancy_without_resolving_it(make_site, assumptions) -> None:
    """Record-and-continue means the run proceeds on the SAME numbers, not a blended one.

    `validation_requirements.md` §3 forbids averaging or silently preferring one side. So no record
    may carry a value: a gate that chose a number on the customer's behalf would be doing exactly
    what the rule prohibits, while looking like documentation.
    """
    site = _bad_declaration(make_site)
    run = generate_quote(site, assumptions)

    assert run.site.load_data.annual_kwh == 4_000_000.0, "the declared figure is not re-based"
    records = [r for r in run.derived_assumptions if r.key.startswith("load_data.")]
    assert records
    assert all(r.value is None for r in records), "the gate derives nothing; it reports"
    assert all(r.derivation_method == "not_derivable" for r in records)


def test_a_warning_level_divergence_caps_at_medium_rather_than_passing(make_site, measured) -> None:
    """The module's own contract: "WARNINGs cap the confidence band, they do not block".

    A bare bool could not express that, so every warned run was reported as fully reconciled.
    """
    from gridq.quotes.confidence import _reconciliation_factor

    site = _reconciling_site(make_site, measured).model_copy(deep=True)
    declared = site.load_data.annual_kwh or 0.0
    site.load_data.annual_kwh = declared * 1.08  # inside ERROR (15%), beyond WARN (5%)
    result = reconcile_declared_against_measured(
        s.SiteModel.model_validate(site.model_dump(mode="json")), measured
    )

    assert result.reconciles, "a warning does not fail the gate"
    assert result.warnings
    assert _reconciliation_factor(None, result).ceiling is s.ConfidenceBand.MEDIUM
    assert reconciliation_records(result) == (), "a warning is not an open ask"


def test_gate_3_is_honestly_un_run_when_no_measured_series_can_be_read(
    make_site, assumptions
) -> None:
    """An unreadable file must never be the reason a run dies (and never a silent pass)."""
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_basis = "metered"
    site.load_data.half_hourly_file_ref = "does/not/exist.csv"
    site = s.SiteModel.model_validate(site.model_dump(mode="json"))

    run = generate_quote(site, assumptions)

    assert run.reconciliation is None
    factor = next(f for f in run.confidence.factors if f.name == "load reconciliation")
    assert factor.ceiling is s.ConfidenceBand.MEDIUM
    assert "not reconciled" in factor.reason


def test_a_supplied_summary_is_used_instead_of_re_reading_the_file(
    make_site, assumptions, measured
) -> None:
    """A caller that already summarised the series passes it in; the gate must honour it."""
    site = _reconciling_site(make_site, measured).model_copy(deep=True)
    site.load_data.half_hourly_file_ref = "does/not/exist.csv"
    site = s.SiteModel.model_validate(site.model_dump(mode="json"))

    run = generate_quote(site, assumptions, hh_summary=measured)
    assert run.reconciliation is not None and run.reconciliation.reconciles


# --- Gate 2: N9 route 2 — synthesis stamps its own basis ---------------------------------------


def _annual_only_site(make_site) -> s.SiteModel:
    site = make_site().model_copy(deep=True)
    site.load_data.half_hourly_file_ref = None
    site.load_data.half_hourly_rows = None
    site.load_data.start_date = None
    site.load_data.end_date = None
    site.load_data.annual_kwh = 908_590.0
    site.annual_consumption_kwh = 908_590.0
    site.data_tier = s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
    return s.SiteModel.model_validate(site.model_dump(mode="json"))


def test_derivation_stamps_modelled_onto_the_site(make_site, tmp_path: Path) -> None:
    """N9, route 2: synthesis never stamped a basis, so its own output read back as METERED.

    The stamp is the whole fix — without it, a generated CSV is indistinguishable from a meter
    export the moment it is referenced, and Gate 2 hands it the top confidence band.
    """
    derived = derive_half_hourly(_annual_only_site(make_site), out_dir=tmp_path, release="gb_v1_6")

    assert derived.site.load_data.half_hourly_basis == HalfHourlyBasis.MODELLED.value
    assert derived.site.load_data.half_hourly_file_ref == str(derived.synthesis.path)
    assert derived.site.load_data.half_hourly_rows == derived.synthesis.intervals
    assert DEFAULT_PROFILE_ID in (derived.site.load_data.half_hourly_reference_profile or "")

    admission = admit_half_hourly(derived.site)
    assert admission.basis is HalfHourlyBasis.MODELLED
    assert not admission.is_metered
    assert admission.confidence_ceiling == "medium", "a modelled shape cannot reach HIGH"


def test_the_default_archetype_for_an_unknown_business_type_is_deliberate_and_labelled(
    make_site, tmp_path: Path
) -> None:
    """`SiteModel` carries no business type by design, so the fallback must announce itself."""
    derived = derive_half_hourly(_annual_only_site(make_site), out_dir=tmp_path)

    assert derived.synthesis.profile.profile_id == DEFAULT_PROFILE_ID == "non_domestic_unrestricted"
    assert not derived.synthesis.profile.is_sourced, "an archetype must not read as a sourced curve"
    assert derived.record.value == DEFAULT_PROFILE_ID
    assert derived.record.sources == [], "an uncited derivation must stay visibly uncited"
    assert derived.record.confidence_ceiling is s.ConfidenceBand.MEDIUM
    assert str(DEFAULT_SYNTHESIS_YEAR) in derived.record.basis
    assert "not about this site" in derived.record.basis


def test_the_derived_record_travels_on_the_site_and_into_the_identity(
    make_site, tmp_path: Path, assumptions
) -> None:
    """A run on a derived shape is not the same run as one on no shape at all."""
    from gridq.quotes.assembly import _canonical_site_dump

    site = _annual_only_site(make_site)
    derived = derive_half_hourly(site, out_dir=tmp_path, release="gb_v1_6")

    assert derived.site.derived_assumptions == [derived.record]
    assert "derived_assumptions" not in _canonical_site_dump(site)
    assert "derived_assumptions" in _canonical_site_dump(derived.site)
    assert derived.record.derived_on_release == "gb_v1_6"


def test_derivation_refuses_to_invent_a_total_and_the_gate_continues_anyway(make_site) -> None:
    """The one thing synthesis will not do — and it still does not block the run (D44)."""
    site = _annual_only_site(make_site).model_copy(deep=True)
    site.load_data.annual_kwh = None
    site.annual_consumption_kwh = None
    site = s.SiteModel.model_validate(
        {**site.model_dump(mode="json"), "data_tier": "T1_PARTIAL_HALF_HOURLY"}
    )

    with pytest.raises(SynthesisError, match="may not be invented"):
        derive_half_hourly(site, out_dir=Path("unused"))

    admission = admit_half_hourly(site)  # the gate still continues
    assert admission.derived[0].derivation_method == "not_derivable"


def test_derivation_is_deterministic(make_site, tmp_path: Path) -> None:
    """Identical inputs ⇒ identical file, so a derived quote is as reproducible as a metered one."""
    site = _annual_only_site(make_site)
    a = derive_half_hourly(site, out_dir=tmp_path)
    b = derive_half_hourly(site, out_dir=tmp_path)
    assert a.synthesis.path == b.synthesis.path
    assert a.synthesis.path.read_text(encoding="utf-8") == b.synthesis.path.read_text(
        encoding="utf-8"
    )


# --- the opt-in pipeline derivation -----------------------------------------------------------


def test_generate_quote_can_derive_the_missing_series_on_request(
    make_site, assumptions, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D44's full behaviour, opt-in: the run gets a shape to dispatch against, honestly labelled."""
    from tests._dpa_support import register_fixture_dpas

    site = _annual_only_site(make_site)
    # Deriving writes a series to disk for the site, so it needs a permit (WP-GRC-05).
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", site.site_id)

    plain = generate_quote(site, assumptions)
    derived = generate_quote(site, assumptions, derived_hh_dir=tmp_path)

    assert plain.quote.half_hourly_basis is None
    assert derived.quote.half_hourly_basis is HalfHourlyBasis.MODELLED
    assert derived.site.load_data.half_hourly_file_ref
    assert derived.quote.confidence_ceiling is s.ConfidenceBand.MEDIUM
    assert derived.quote.confidence_band.rank <= s.ConfidenceBand.MEDIUM.rank
    # A derived shape is a different input, so it must be a different run.
    assert derived.quote.input_hash != plain.quote.input_hash
    assert any(
        r.key == "load_data.half_hourly_reference_profile" for r in derived.derived_assumptions
    )


def test_the_opt_in_derivation_never_displaces_a_metered_series(
    make_site, assumptions, tmp_path: Path
) -> None:
    """Metered data is never displaced, blended away or preferred against."""
    site = make_site()  # already carries examples/demo_site/hh_2025.csv
    run = generate_quote(site, assumptions, derived_hh_dir=tmp_path)
    assert run.site.load_data.half_hourly_file_ref == site.load_data.half_hourly_file_ref
    assert not list(tmp_path.iterdir()), "nothing was generated for a site that already has data"


def test_the_default_pipeline_writes_no_files(make_site, assumptions, tmp_path: Path) -> None:
    """Derivation is opt-in precisely so the default path stays offline and IO-free."""
    generate_quote(_annual_only_site(make_site), assumptions)
    assert not list(tmp_path.iterdir())
