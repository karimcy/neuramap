"""Tests for the Piclo Data Hub dev fetcher (:mod:`gridq.dev.fetch_piclo`).

Off the pricing path; these tests NEVER touch the network — the single HTTP seam
(:func:`gridq.dev._fetch_common.http_get_bytes`, imported into the fetcher) is monkeypatched to
return small captured XLSX/CSV bytes. Coverage: the competitions median transform (GB-wide,
gaps→None never fabricated), row-count-only for bids/lcm, a failing fetch writes no artifact,
sidecar byte-determinism under a fixed ``fetched_utc``, and the suggested-YAML provenance.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from gridq.dev import fetch_piclo as pf
from gridq.dev._fetch_common import FetchError

_FIXTURES = Path(__file__).parent / "fixtures"
_FETCHED = datetime(2026, 7, 20, 12, 0, 0)


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _stub(raw: bytes):
    def _get(url: str, *, timeout: float = 0.0) -> bytes:
        return raw

    return _get


# --- Pure transforms ---------------------------------------------------------


def test_competitions_median_is_gb_wide_and_gaps_are_not_fabricated() -> None:
    summary = pf.summarise_competitions(_fixture_bytes("piclo_competitions_sample.xlsx"))
    # utilisation values 50/100/100/150/200 (one null) -> median 100.0, 5 priced rows
    assert summary["utilisation_median_gbp_per_mwh"] == 100.0
    assert summary["utilisation_price_rows"] == 5
    assert summary["row_count"] == 6
    # availability column is all-null in the sample -> reported None, never a guessed number
    assert summary["availability_median_gbp_per_mw_h"] is None


def test_competitions_raises_when_price_column_absent() -> None:
    import io

    import pandas as pd

    raw = io.BytesIO()
    pd.DataFrame({"System Operator Name": ["SSEN"]}).to_excel(raw, index=False)
    with pytest.raises(FetchError):
        pf.summarise_competitions(raw.getvalue())


def test_csv_row_count_excludes_header() -> None:
    assert pf.count_csv_rows(_fixture_bytes("piclo_lcm_sample.csv")) == 3


def test_xlsx_row_count() -> None:
    assert pf.count_xlsx_rows(_fixture_bytes("piclo_bids_sample.xlsx")) == 4


def test_transform_is_deterministic() -> None:
    raw = _fixture_bytes("piclo_competitions_sample.xlsx")
    assert pf.summarise_competitions(raw) == pf.summarise_competitions(raw)


# --- Orchestration + provenance ---------------------------------------------


def test_run_competitions_writes_artifact_sidecar_and_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pf, "http_get_bytes", _stub(_fixture_bytes("piclo_competitions_sample.xlsx"))
    )
    out = tmp_path / "competitions.json"
    result = pf.run(dataset="competitions", out_path=out, fetched_utc=_FETCHED)
    assert out.exists()
    assert result.sidecar_path.exists()
    assert result.suggested_yaml is not None
    assert "100.0" in result.suggested_yaml
    assert result.sha256[:12] in result.suggested_yaml
    assert "needs_review" in result.suggested_yaml
    assert "utilisation_price_gbp_per_mwh" in result.suggested_yaml


def test_run_bids_is_rowcount_only_no_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pf, "http_get_bytes", _stub(_fixture_bytes("piclo_bids_sample.xlsx")))
    out = tmp_path / "bids.json"
    result = pf.run(dataset="bids", out_path=out, fetched_utc=_FETCHED)
    assert result.record_count == 4
    assert result.suggested_yaml is None
    assert result.sidecar_path.exists()


def test_sidecar_byte_deterministic_under_fixed_fetched_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _fixture_bytes("piclo_lcm_sample.csv")
    monkeypatch.setattr(pf, "http_get_bytes", _stub(raw))
    r1 = pf.run(dataset="lcm", out_path=tmp_path / "a" / "lcm.json", fetched_utc=_FETCHED)
    r2 = pf.run(dataset="lcm", out_path=tmp_path / "b" / "lcm.json", fetched_utc=_FETCHED)
    assert r1.sidecar_path.read_bytes() == r2.sidecar_path.read_bytes()
    import json

    side = json.loads(r1.sidecar_path.read_text())
    assert side["sha256"] == r1.sha256
    assert side["size_bytes"] == len(raw)
    assert side["fetched_utc"] == "2026-07-20T12:00:00"
    assert side["fetcher"] == "gridq.dev.fetch_piclo"


def test_run_failure_writes_no_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str, *, timeout: float = 0.0) -> bytes:
        raise FetchError("network down in test")

    monkeypatch.setattr(pf, "http_get_bytes", _boom)
    out = tmp_path / "should_not_exist.json"
    with pytest.raises(FetchError):
        pf.run(dataset="competitions", out_path=out, fetched_utc=_FETCHED)
    assert not out.exists()


def test_main_stubbed_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pf, "http_get_bytes", _stub(_fixture_bytes("piclo_competitions_sample.xlsx"))
    )
    out = tmp_path / "deep" / "competitions.json"
    rc = pf.main(
        ["--dataset", "competitions", "--out", str(out), "--fetched-utc", "2026-07-20T12:00:00"]
    )
    assert rc == 0
    assert out.exists()


def test_dataset_url_is_public_s3() -> None:
    url = pf.dataset_url(pf.DATASETS["competitions"])
    assert url.endswith("piclo_flex_uk_competitions.xlsx")
    assert "piclo-open-data.s3" in url
