"""Tests for the NESO CKAN dev fetcher (:mod:`gridq.dev.fetch_neso_ckan`).

Off the pricing path; these tests NEVER touch the network — the single HTTP seam
(:func:`gridq.dev._fetch_common.http_get_bytes`, imported into the fetcher) is monkeypatched to
return small captured CKAN payloads. Coverage: the pure per-resource transforms (latest-auction
selection, £/kW/yr derivation, DC/DM/DR mean × 8760), malformed rows skipped never fabricated, a
failing fetch writes no artifact, sidecar byte-determinism under a fixed ``fetched_utc``, and the
suggested-YAML block carries full provenance.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from gridq.dev import fetch_neso_ckan as ck
from gridq.dev._fetch_common import FetchError

_FIXTURES = Path(__file__).parent / "fixtures"
_FETCHED = datetime(2026, 7, 20, 12, 0, 0)


def _fixture_bytes(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _stub(raw: bytes):
    def _get(url: str, *, timeout: float = 0.0) -> bytes:
        return raw

    return _get


def _records(name: str):
    import json

    return ck.extract_records(json.loads(_fixture_bytes(name)))


# --- Pure transforms ---------------------------------------------------------


def test_cm_derating_picks_latest_t4_storage_durations() -> None:
    summary = ck.summarise_cm_derating(_records("neso_cm_derating_sample.json"))
    assert summary["auction_name"].startswith("2029-30 (T-4)")
    assert summary["factors"] == {"1": 0.1099, "2": 0.2198, "4": 0.4396}


def test_cm_derating_raises_when_no_storage_rows() -> None:
    # A T-1-only / non-storage payload has no 1h/2h/4h T-4 rows — raise, never fabricate.
    payload = [
        {
            "Auction Name": "2026-27 (T-1) One Year Ahead",
            "Generating Technology Class": "DSR",
            "De-Rating Factor": 0.8,
        }
    ]
    with pytest.raises(FetchError):
        ck.summarise_cm_derating(payload)


def test_cm_clearing_derives_price_and_reports_t1_context() -> None:
    summary = ck.summarise_cm_clearing(_records("neso_cm_clearing_sample.json"))
    # latest T-4 = 2029-30: 1086943276.8 / (40108.608 * 1000) = 27.1
    assert summary["latest_t4"]["auction_name"].startswith("2029-30 (T-4)")
    assert summary["latest_t4"]["clearing_price_gbp_per_kw_yr"] == 27.1
    # T-1 context = 2026-27: 5,000,000 / (1000 * 1000) = 5.0
    assert summary["latest_t1"]["clearing_price_gbp_per_kw_yr"] == 5.0


def test_eac_mean_excludes_non_dc_dm_dr_and_skips_null_price() -> None:
    summary = ck.summarise_eac_dc_dm_dr(_records("neso_eac_sample.json"))
    # DC/DM/DR prices [-8, 16, 2, 4, 0, 10] mean 4.0; NBR excluded, null skipped.
    assert summary["product_count"] == 6
    assert summary["mean_gbp_per_mw_h"] == 4.0
    assert summary["annualised_gbp_per_mw_yr"] == 4.0 * 8760
    assert summary["window_start"] == "2026-04-01T02:00:00"


def test_transforms_are_deterministic() -> None:
    recs = _records("neso_eac_sample.json")
    assert ck.summarise_eac_dc_dm_dr(recs) == ck.summarise_eac_dc_dm_dr(recs)


def test_parse_auction() -> None:
    assert ck.parse_auction("2029-30 (T-4) Four Year Ahead Capacity Auction") == (2029, "T-4")
    assert ck.parse_auction("2026-27 (T-1) One Year Ahead") == (2026, "T-1")
    assert ck.parse_auction("nonsense") == (None, None)


# --- Payload extraction ------------------------------------------------------


def test_extract_records_rejects_bad_shapes() -> None:
    with pytest.raises(FetchError):
        ck.extract_records({"success": False, "error": "boom"})
    with pytest.raises(FetchError):
        ck.extract_records({"result": {"no_records": 1}})
    with pytest.raises(FetchError):
        ck.extract_records("not an object")


# --- Orchestration + provenance ---------------------------------------------


def test_run_writes_artifact_and_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ck, "http_get_bytes", _stub(_fixture_bytes("neso_cm_clearing_sample.json")))
    out = tmp_path / "cm_clearing.json"
    result = ck.run(resource="cm-clearing", out_path=out, fetched_utc=_FETCHED)
    assert out.exists()
    assert result.sidecar_path.exists()
    assert result.record_count == 5
    assert "27.1" in result.suggested_yaml
    assert result.sha256[:12] in result.suggested_yaml
    assert str(result.sidecar_path) in result.suggested_yaml
    assert "needs_review" in result.suggested_yaml


def test_sidecar_is_byte_deterministic_under_fixed_fetched_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _fixture_bytes("neso_eac_sample.json")
    monkeypatch.setattr(ck, "http_get_bytes", _stub(raw))
    first = tmp_path / "a" / "eac.json"
    second = tmp_path / "b" / "eac.json"
    r1 = ck.run(resource="eac-results", out_path=first, fetched_utc=_FETCHED)
    r2 = ck.run(resource="eac-results", out_path=second, fetched_utc=_FETCHED)
    assert r1.sidecar_path.read_bytes() == r2.sidecar_path.read_bytes()
    # the sidecar pins exactly the fetched bytes
    import json

    side = json.loads(r1.sidecar_path.read_text())
    assert side["sha256"] == r1.sha256
    assert side["size_bytes"] == len(raw)
    assert side["fetched_utc"] == "2026-07-20T12:00:00"
    assert side["licence"] == ck.LICENCE


def test_run_failure_writes_no_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str, *, timeout: float = 0.0) -> bytes:
        raise FetchError("network down in test")

    monkeypatch.setattr(ck, "http_get_bytes", _boom)
    out = tmp_path / "should_not_exist.json"
    with pytest.raises(FetchError):
        ck.run(resource="cm-derating", out_path=out, fetched_utc=_FETCHED)
    assert not out.exists()


def test_main_stubbed_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ck, "http_get_bytes", _stub(_fixture_bytes("neso_cm_derating_sample.json")))
    out = tmp_path / "deep" / "cm_derating.json"
    rc = ck.main(
        ["--resource", "cm-derating", "--out", str(out), "--fetched-utc", "2026-07-20T12:00:00"]
    )
    assert rc == 0
    assert out.exists()


def test_main_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def _boom(url: str, *, timeout: float = 0.0) -> bytes:
        raise FetchError("network down in test")

    monkeypatch.setattr(ck, "http_get_bytes", _boom)
    out = tmp_path / "nope.json"
    rc = ck.main(["--resource", "eac-results", "--out", str(out)])
    assert rc == 1
    assert not out.exists()
    assert "network down in test" in capsys.readouterr().err


def test_build_search_url_is_keyless() -> None:
    url = ck.build_search_url(ck.RESOURCES["cm-derating"])
    assert "d94ff98d-39ba-40d6-9672-7433de0631ac" in url
    assert "datastore_search" in url
    assert "api_key" not in url.lower()
