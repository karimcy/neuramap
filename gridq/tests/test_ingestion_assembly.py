"""Tests for `gridq.ingestion.assembly` — the missing_data wire-back + HH fold.

Covers: `populate_missing_data` writes the deterministic ranked asks into `SiteModel.missing_data`
without mutating the input; the HH fold re-grades the data tier from measured coverage, preserves
a prior `annual_kwh` / `max_import_kw` the summary does not carry; and an inconsistent fold (a
too-sparse file that would grade to T0 with no annual figure) RAISES — the proof that the fold
round-trips through `model_validate` (a `model_copy` would silently mint an invalid site).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.ingestion import (
    assemble_site_model,
    load_half_hourly_csv,
    populate_missing_data,
    summarise_half_hourly_data,
)
from gridq.schemas.enums import DataTier

START = datetime(2025, 1, 1, 0, 0)


def _hh_summary(tmp_path: Path, *, n: int, blank_import: bool = False) -> s.SiteModel:
    path = tmp_path / "hh.csv"
    lines = ["timestamp,import_kw,export_kw"]
    for i in range(n):
        ts = (START + timedelta(minutes=30 * i)).isoformat()
        val = "" if blank_import else f"{100 + i}"
        lines.append(f"{ts},{val},0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    df = load_half_hourly_csv(path)
    return summarise_half_hourly_data(df, file_ref=str(path))


def _t1_site_without_annual() -> s.SiteModel:
    return s.SiteModel(
        site_id="t1_no_annual",
        postcode="LE17 4XN",
        data_tier=DataTier.T1_PARTIAL_HALF_HOURLY,
        load_data=s.LoadDataSummary(max_import_kw=100.0),
        annual_consumption_kwh=None,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
        ),
        connection=s.ConnectionDetails(mic_kw=300.0),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def test_populate_missing_data_deterministic_and_pure(make_site: Callable[..., s.SiteModel]):
    site = make_site()
    out1 = populate_missing_data(site)
    out2 = populate_missing_data(site)
    assert out1.missing_data == out2.missing_data
    assert out1.missing_data  # non-empty
    assert site.missing_data == []  # input untouched


def test_no_hh_summary_just_populates(make_site: Callable[..., s.SiteModel]):
    site = make_site()
    out = assemble_site_model(site)
    assert out.data_tier == site.data_tier
    assert out.missing_data


def test_hh_fold_regrades_to_t1(make_site: Callable[..., s.SiteModel], tmp_path: Path):
    site = make_site()  # T2 with annual_kwh
    summary = _hh_summary(tmp_path, n=200)  # partial → T1
    out = assemble_site_model(site, hh_summary=summary)

    assert out.data_tier == DataTier.T1_PARTIAL_HALF_HOURLY
    assert out.load_data.half_hourly_rows == 200
    assert out.load_data.annual_kwh == site.load_data.annual_kwh  # preserved
    assert out.load_data.max_import_kw is not None
    assert out.missing_data


def test_hh_fold_preserves_max_import_when_summary_lacks_it(
    make_site: Callable[..., s.SiteModel], tmp_path: Path
):
    site = make_site()  # max_import_kw = 420
    summary = _hh_summary(tmp_path, n=200, blank_import=True)  # all-null import → max None
    out = assemble_site_model(site, hh_summary=summary)
    assert out.load_data.max_import_kw == site.load_data.max_import_kw  # not wiped


def test_inconsistent_fold_raises(tmp_path: Path):
    site = _t1_site_without_annual()
    sparse = _hh_summary(tmp_path, n=1)  # below the T1 floor → grades to T0
    with pytest.raises(ValidationError):
        assemble_site_model(site, hh_summary=sparse)
