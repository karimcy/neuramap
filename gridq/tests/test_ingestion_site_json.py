"""Tests for the SiteModel JSON loader (M0 ingestion).

Covers: a valid SiteModel JSON loads and validates; schema failures raise a specific,
actionable `SiteModelLoadError`; a schema-valid-but-incoherent site is rejected distinctly;
and the loader never invents a missing field. Fixtures are generated *from the models* (per
the SKILLS.md rule) and from the committed example JSONs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.ingestion import (
    SiteModelLoadError,
    load_site_model_json,
    load_site_model_json_with_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"


def _dump(site: s.SiteModel, path: Path) -> str:
    path.write_text(json.dumps(site.model_dump(mode="json")), encoding="utf-8")
    return str(path)


# --- valid loads -----------------------------------------------------------


@pytest.mark.parametrize("filename", ["site_t0.json", "site_t2.json"])
def test_committed_examples_load(filename: str) -> None:
    site = load_site_model_json(str(EXAMPLES / filename))
    assert isinstance(site, s.SiteModel)
    assert site.schema_version == s.SCHEMA_VERSION


def test_generated_site_round_trips(make_site, tmp_path: Path) -> None:
    site = make_site(with_max_import=True)  # a T2 site
    path = _dump(site, tmp_path / "site.json")
    loaded = load_site_model_json(path)
    assert loaded.site_id == site.site_id
    assert loaded.data_tier == site.data_tier


def test_load_returns_report_with_warnings(make_site, tmp_path: Path) -> None:
    # A T2 site with no file reference triggers a coherence WARNING (not an error).
    # The condition is constructed explicitly rather than inherited from the fixture: the shared
    # factory used to declare T2 with no series behind it (hostile-review F-10, now fixed), so
    # this test passed by accident on a site that could never have been underwritten.
    site = make_site(with_max_import=True).model_copy(deep=True)
    site.load_data.half_hourly_file_ref = None
    path = _dump(site, tmp_path / "site.json")
    loaded, report = load_site_model_json_with_report(path)
    assert loaded.site_id == site.site_id
    assert report.ok  # warnings present, but no ERROR-severity issue
    assert any(i.code == "site.t2_no_file_ref" for i in report.warnings)


# --- clean failures --------------------------------------------------------


def test_missing_file_raises_clean_error() -> None:
    with pytest.raises(SiteModelLoadError, match="not found"):
        load_site_model_json(str(EXAMPLES / "does_not_exist.json"))


def test_invalid_json_raises_clean_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is : not valid json", encoding="utf-8")
    with pytest.raises(SiteModelLoadError, match="not valid JSON"):
        load_site_model_json(str(bad))


def test_non_object_top_level_raises(tmp_path: Path) -> None:
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SiteModelLoadError, match="JSON object at the top level"):
        load_site_model_json(str(arr))


def test_missing_required_field_names_the_field(make_site, tmp_path: Path) -> None:
    data = make_site().model_dump(mode="json")
    del data["counterparty"]
    path = tmp_path / "no_counterparty.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SiteModelLoadError, match="counterparty"):
        load_site_model_json(str(path))


def test_extra_field_is_rejected(make_site, tmp_path: Path) -> None:
    data = make_site().model_dump(mode="json")
    data["totally_made_up_field"] = 123
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SiteModelLoadError):
        load_site_model_json(str(path))


def test_t2_without_coverage_metadata_is_rejected(make_site, tmp_path: Path) -> None:
    # Strip the HH coverage metadata from a T2 site → schema-level rejection.
    data = make_site(with_max_import=True).model_dump(mode="json")
    data["load_data"]["half_hourly_rows"] = None
    data["load_data"]["start_date"] = None
    data["load_data"]["end_date"] = None
    path = tmp_path / "t2_no_coverage.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SiteModelLoadError, match="half-hourly coverage"):
        load_site_model_json(str(path))


# --- coherence (second gate, beyond the schema) ----------------------------


def _exporting_incoherent_site() -> s.SiteModel:
    """A schema-valid site that exports but declares export consent 'not_required'."""
    return s.SiteModel(
        site_id="site_incoherent",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        load_data=s.LoadDataSummary(annual_kwh=100_000.0),
        annual_consumption_kwh=100_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0, mec_kw=100.0),  # exports
        consents=s.ConsentState(g99=s.ConsentStatus.NOT_REQUIRED),  # incoherent with export
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def test_incoherent_site_loads_without_raising_now_a_warning(tmp_path: Path) -> None:
    # Owner steer (issue 018 section 9): consent/export incoherence is a declared-state
    # WARNING, never a hard block - the plain loader returns the site, it is not rejected.
    path = _dump(_exporting_incoherent_site(), tmp_path / "incoherent.json")
    site = load_site_model_json(path)
    assert site.site_id == "site_incoherent"


def test_incoherent_site_surfaces_a_coherence_warning(tmp_path: Path) -> None:
    path = _dump(_exporting_incoherent_site(), tmp_path / "incoherent.json")
    site, report = load_site_model_json_with_report(path)
    assert site.site_id == "site_incoherent"
    assert report.ok  # a WARNING now, not an ERROR (issue 018 section 9) - never blocks the run
    assert any("incoherent_with_export" in i.code for i in report.warnings)


# --- no invented data ------------------------------------------------------


def test_loader_does_not_invent_hh_for_a_t0_site() -> None:
    site = load_site_model_json(str(EXAMPLES / "site_t0.json"))
    # The file carries an annual figure and NO half-hourly data; the loader must not
    # fabricate coverage to "upgrade" it.
    assert site.load_data.annual_kwh is not None
    assert site.load_data.half_hourly_rows is None
    assert site.load_data.start_date is None
    assert site.data_tier == s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
