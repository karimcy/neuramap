"""Confidential half-hourly data — NDA state, personal-data, access log (L03 · M0/M1).

The System Brief's non-functional floor: *half-hourly data held under recorded NDA state per
site; access logged; sole-trader sites treated as personal data*. These tests prove all three:

  * a site records an NDA state (`SiteModel.confidentiality.nda_status`);
  * sole-trader sites are personal data (with an explicit override either way);
  * every read of HH data via `load_half_hourly_csv` lands an entry in the access log, attributed
    and carrying the personal-data flag — and is mirrored to a JSONL file when configured.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.ingestion import (
    ACCESS_LOG_ENV,
    clear_access_log,
    get_access_log,
    load_half_hourly_csv,
)

START = datetime(2025, 1, 1, 0, 0)


@pytest.fixture(autouse=True)
def _clean_log() -> None:
    clear_access_log()


def _hh_csv(tmp_path: Path, *, n: int = 10) -> Path:
    path = tmp_path / "hh.csv"
    lines = ["timestamp,import_kw"]
    for i in range(n):
        lines.append(f"{(START + timedelta(minutes=30 * i)).isoformat()},{100 + i}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- NDA state (recorded per site) -----------------------------------------


def test_site_records_nda_state(make_site) -> None:
    site = make_site()
    # Default is an honest "unknown" bundle, present on every site.
    assert site.confidentiality.nda_status == s.NdaStatus.UNKNOWN
    signed = site.model_copy(
        update={
            "confidentiality": s.Confidentiality(
                nda_status=s.NdaStatus.SIGNED, nda_reference="NDA-2026-017"
            )
        }
    )
    assert signed.confidentiality.nda_status == s.NdaStatus.SIGNED
    assert signed.confidentiality.nda_reference == "NDA-2026-017"


# --- sole-trader → personal data -------------------------------------------


def test_sole_trader_site_is_personal_data(make_site) -> None:
    site = make_site()
    assert site.is_personal_data is False  # default UNKNOWN entity type
    sole = site.model_copy(
        update={
            "counterparty": site.counterparty.model_copy(
                update={"legal_entity_type": s.LegalEntityType.SOLE_TRADER}
            )
        }
    )
    assert sole.is_personal_data is True


def test_personal_data_override_wins(make_site) -> None:
    site = make_site()
    # A limited company explicitly flagged personal data (e.g. a named-individual contact).
    forced = site.model_copy(
        update={"confidentiality": s.Confidentiality(personal_data_override=True)}
    )
    assert forced.is_personal_data is True
    # And a sole trader explicitly recorded as not-personal-data.
    sole = site.model_copy(
        update={
            "counterparty": site.counterparty.model_copy(
                update={"legal_entity_type": s.LegalEntityType.SOLE_TRADER}
            ),
            "confidentiality": s.Confidentiality(personal_data_override=False),
        }
    )
    assert sole.is_personal_data is False


# --- access log (every HH read is logged) ----------------------------------


def test_hh_read_is_logged(tmp_path: Path) -> None:
    csv = _hh_csv(tmp_path, n=10)
    assert get_access_log() == []
    load_half_hourly_csv(csv, site_id="site_test", actor="Olly", is_personal_data=True)
    log = get_access_log()
    assert len(log) == 1
    event = log[0]
    assert event.file_ref == str(csv)
    assert event.action == "read"
    assert event.site_id == "site_test"
    assert event.actor == "Olly"
    assert event.row_count == 10
    assert event.is_personal_data is True


def test_each_read_appends_an_entry(tmp_path: Path) -> None:
    csv = _hh_csv(tmp_path)
    load_half_hourly_csv(csv)
    load_half_hourly_csv(csv)
    assert len(get_access_log()) == 2


def test_access_log_mirrored_to_jsonl_file(tmp_path: Path, monkeypatch) -> None:
    log_file = tmp_path / "access.jsonl"
    monkeypatch.setenv(ACCESS_LOG_ENV, str(log_file))
    csv = _hh_csv(tmp_path, n=5)
    load_half_hourly_csv(csv, site_id="site_test")
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["file_ref"] == str(csv)
    assert record["site_id"] == "site_test"
    assert record["row_count"] == 5


def test_access_log_creates_missing_parent_dir(tmp_path: Path, monkeypatch) -> None:
    # A benign missing parent dir must not break the read; the log file is created under it.
    log_file = tmp_path / "audit" / "nested" / "access.jsonl"
    monkeypatch.setenv(ACCESS_LOG_ENV, str(log_file))
    load_half_hourly_csv(_hh_csv(tmp_path))
    assert log_file.exists()
    assert len(log_file.read_text(encoding="utf-8").strip().splitlines()) == 1
