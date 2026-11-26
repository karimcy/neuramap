"""Goal invariants for the remaining M0 rows — the spec-extract gap half-claims.

Executable form of the docs/goal/spec/manual_ch01-08.md M0 rows whose coverage notes said
"half not asserted" / "none found": INV-M0-07 (offenders NAMED), INV-M0-09 (derived-from-energy
LABELLED), INV-M0-13 (annual never inferred from a partial trace), INV-M0-19 (wholesale-forecast
ask only when requested), INV-M0-23 (access log never perturbs a deterministic identity),
INV-M0-24 (persistent sink is fail-closed), INV-M0-25 (ingestion-level honest degradation:
a bad meter file is a precise error and the run continues without it).

Rows already covered elsewhere are cited, not re-tested: the fold/regrade/preserve behaviour is
`test_ingestion_assembly.py`; ask ranking determinism is `test_ingestion_ranked_asks.py`;
solver-level unreadable-file degrade is `test_optimisation_milp_solver.py::test_degrade_
unreadable_file`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gridq.ingestion import (
    HalfHourlyDataError,
    load_half_hourly_csv,
    summarise_half_hourly_data,
)
from gridq.ingestion.access_log import record_hh_access
from gridq.ingestion.assembly import assemble_site_model
from gridq.ingestion.hh_data import to_load_data_summary
from gridq.ingestion.missing_data import detect_missing_data
from gridq.provenance import hash_json
from gridq.quotes.assembly import _canonical_run_input
from gridq.schemas import QuoteRecord

runner = CliRunner()

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _write_csv(path: Path, rows: list[str], header: str = "timestamp,import_kw") -> Path:
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# --- INV-M0-07: the unparseable-timestamp error NAMES the first offenders ------------


def test_unparseable_timestamp_error_names_the_offending_value(tmp_path) -> None:
    path = _write_csv(
        tmp_path / "bad.csv",
        ["2025-01-01T00:00,10.0", "NOT-A-TIME,11.0", "2025-01-01T01:00,12.0"],
    )
    with pytest.raises(HalfHourlyDataError) as excinfo:
        load_half_hourly_csv(path)
    message = str(excinfo.value)
    assert "NOT-A-TIME" in message  # the offender is named, not a generic "invalid file"
    assert "never guessed" in message.lower() or "unparseable" in message.lower()


# --- INV-M0-09: energy-derived power is LABELLED derived, never passed off as measured


def test_energy_derived_series_is_labelled_with_its_basis(tmp_path) -> None:
    energy = _write_csv(
        tmp_path / "kwh.csv",
        ["2025-01-01T00:00,5.0", "2025-01-01T00:30,6.0", "2025-01-01T01:00,4.0"],
        header="timestamp,import_kwh",
    )
    power = _write_csv(
        tmp_path / "kw.csv",
        ["2025-01-01T00:00,10.0", "2025-01-01T00:30,12.0", "2025-01-01T01:00,8.0"],
    )
    energy_summary = summarise_half_hourly_data(load_half_hourly_csv(energy))
    power_summary = summarise_half_hourly_data(load_half_hourly_csv(power))
    assert energy_summary.import_basis == "energy"  # derived kW carries its provenance label
    assert power_summary.import_basis == "power"
    # The deterministic transform itself: kWh x 2 for 30-minute periods.
    assert energy_summary.max_import_kw == pytest.approx(12.0)


# --- INV-M0-13: annual energy is never inferred from a partial trace -----------------


def test_partial_trace_never_yields_an_annual_figure(tmp_path, make_site) -> None:
    # A week of energy readings sums to a real kWh total — the bridge must still leave
    # annual_kwh unset (a partial trace is not a year; nothing is scaled up).
    rows = [f"2025-01-{day:02d}T{hour:02d}:00,5.0" for day in range(1, 8) for hour in range(24)]
    path = _write_csv(tmp_path / "week.csv", rows, header="timestamp,import_kwh")
    summary = summarise_half_hourly_data(load_half_hourly_csv(path), file_ref=str(path))
    load = to_load_data_summary(summary)
    assert load.annual_kwh is None
    # And through the fold: a stated prior annual figure is PRESERVED, never recomputed —
    # the folded value must equal what the site already stated, not any function of the trace.
    site = make_site(with_max_import=True)
    stated = site.load_data.annual_kwh
    folded = assemble_site_model(site, hh_summary=summary)
    assert folded.load_data.annual_kwh == stated


# --- INV-M0-19: the wholesale-forecast ask appears only when wholesale is requested ---


def test_wholesale_forecast_ask_only_when_requested(make_site) -> None:
    site = make_site(with_max_import=True)
    without = detect_missing_data(site)
    with_flag = detect_missing_data(site, wholesale_requested=True)
    assert "wholesale_forecast_file" not in {ask.ask_id for ask in without}
    assert "wholesale_forecast_file" in {ask.ask_id for ask in with_flag}
    # The conditional ask is gated upside — it must say so, never join the priced floor.
    ask = next(a for a in with_flag if a.ask_id == "wholesale_forecast_file")
    assert "gated" in (ask.detail or "").lower() or "gated" in ask.why.lower()


# --- INV-M0-23: the access log can never perturb a deterministic identity ------------


def test_access_log_entries_never_perturb_the_canonical_run_input(rich_quote) -> None:
    from gridq.assumptions import load_assumptions

    assumptions = load_assumptions("gb_v0_1")
    before = hash_json(
        _canonical_run_input(rich_quote.site, rich_quote.result, rich_quote.case, assumptions, None)
    )
    for i in range(3):  # extra confidential reads land in the log, not in any identity
        record_hh_access(f"extra_read_{i}.csv", site_id=rich_quote.site.site_id, row_count=1)
    after = hash_json(
        _canonical_run_input(rich_quote.site, rich_quote.result, rich_quote.case, assumptions, None)
    )
    assert before == after
    # And no access-log shaped field exists on the quote contract to smuggle it in.
    assert not any("access" in name for name in QuoteRecord.model_fields)


# --- INV-M0-24: a configured persistent sink is fail-closed --------------------------


def test_access_log_persistent_sink_is_fail_closed(tmp_path, monkeypatch) -> None:
    # Point the sink at a path that cannot be a file (it is a directory): the write must
    # raise — a confidential read never proceeds unaudited when auditing was asked for.
    sink_dir = tmp_path / "sink.jsonl"
    sink_dir.mkdir()
    monkeypatch.setenv("GRIDQ_ACCESS_LOG", str(sink_dir))
    with pytest.raises(OSError):
        record_hh_access("some_file.csv", site_id="site_x")


# --- INV-M0-25: honest degradation, ingestion level -----------------------------------


def test_unreadable_meter_ref_degrades_the_run_honestly(tmp_path, monkeypatch) -> None:
    """A T2-shaped site whose HH ref does not exist still runs end to end: precise
    ingestion error surfaced as degradation, quote produced, nothing invented."""
    from tests._dpa_support import register_fixture_dpas

    from gridq.cli import app

    # The fictitious example site needs a TEST FIXTURE DPA to be run at all (WP-GRC-05).
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_000123")

    site_json = json.loads((EXAMPLES / "site_t2.json").read_text(encoding="utf-8"))
    site_json["load_data"]["half_hourly_file_ref"] = str(tmp_path / "no_such_file.csv")
    site_path = tmp_path / "site.json"
    site_path.write_text(json.dumps(site_json), encoding="utf-8")

    out = tmp_path / "out"
    res = runner.invoke(app, ["run", str(site_path), "--out", str(out)])
    assert res.exit_code == 0, res.output  # the platform never simply fails

    quote_files = sorted(out.rglob("quote.json"))
    assert quote_files, f"no quote.json under {out}"
    quote = json.loads(quote_files[0].read_text(encoding="utf-8"))
    result = quote["optimisation_result"]
    assert result["selected_power_kw"] is None  # no system invented from a missing series
    assert result["solver_status"] in ("insufficient_data", "infeasible")
    assert result["bankable_status"] == "not_bankable"
