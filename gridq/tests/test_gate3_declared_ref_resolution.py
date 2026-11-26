"""Gate 3 must never be able to no-op in silence (the cwd residual of the C-6 defect).

`docs/validation_requirements.md` Gate 3 is mandatory: the DECLARED load basis has to reconcile
against the series actually being priced, or the sizing side and the revenue side of a quote can
describe different loads. C-6 was that the reconciliation module existed, was tested, and was called
from nowhere in `src/`. This is the same shape one layer out, and it survived that fix:

* the CLI held an `hh_summary` and did not pass it to `generate_quote`, so a `--hh` run made
  the gate re-read the file it had just been handed; and
* with no `--hh`, the pipeline resolved the site's own **relative** `half_hourly_file_ref`
  (`data/sites/hh/....csv`) against the **process cwd**. Run from anywhere but the repo root that
  read failed, `reconciliation` came back `None`, and the gate was reported as "not reconciled
  against the measured series" at band MEDIUM: indistinguishable from a corrupt file, and quieter
  than a failure.

An un-run gate that looks like a passed gate is worse than a loud failure, so two things are pinned:
the ref resolves relative to the site file as well as the cwd, and when it cannot be resolved the
quote says so in terms a reader cannot mistake for a pass.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tests._dpa_support import dpa_released_sites, register_fixture_dpas
from typer.testing import CliRunner

from gridq.cli import _resolve_declared_hh_ref, app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fixture_dpa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`gridq run` needs a DPA for the site (WP-GRC-05): a TEST FIXTURE one for ``site_test``."""
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_test")


# --- the resolver itself ------------------------------------------------------------------------


def test_a_repo_relative_ref_resolves_from_the_site_file_not_only_the_cwd(tmp_path) -> None:
    """The real shape: the site JSON sits several directories down from the ref's own base."""
    root = tmp_path / "repo"
    series = root / "data" / "sites" / "hh" / "series.csv"
    series.parent.mkdir(parents=True)
    series.write_text("x", encoding="utf-8")
    site_json = root / "examples" / "real" / "sites" / "site.json"
    site_json.parent.mkdir(parents=True)
    site_json.write_text("{}", encoding="utf-8")

    resolved = _resolve_declared_hh_ref("data/sites/hh/series.csv", site_json)
    assert resolved is not None
    assert resolved.resolve() == series.resolve()


def test_the_cwd_is_still_tried_first_so_a_repo_root_run_is_unchanged(
    tmp_path, monkeypatch
) -> None:
    """Behaviour from the repo root must be byte-for-byte what it was, or this is not a safe fix."""
    cwd = tmp_path / "cwd"
    (cwd / "data").mkdir(parents=True)
    from_cwd = cwd / "data" / "series.csv"
    from_cwd.write_text("cwd copy", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "data").mkdir(parents=True)
    (elsewhere / "data" / "series.csv").write_text("site copy", encoding="utf-8")
    site_json = elsewhere / "site.json"
    site_json.write_text("{}", encoding="utf-8")

    monkeypatch.chdir(cwd)
    resolved = _resolve_declared_hh_ref("data/series.csv", site_json)
    assert resolved is not None
    assert resolved.read_text(encoding="utf-8") == "cwd copy"


def test_a_ref_that_is_nowhere_returns_none_rather_than_a_path_that_does_not_exist(
    tmp_path,
) -> None:
    site_json = tmp_path / "site.json"
    site_json.write_text("{}", encoding="utf-8")
    assert _resolve_declared_hh_ref("data/nope.csv", site_json) is None


def test_an_absolute_ref_is_used_as_given(tmp_path) -> None:
    series = tmp_path / "abs.csv"
    series.write_text("x", encoding="utf-8")
    site_json = tmp_path / "site.json"
    site_json.write_text("{}", encoding="utf-8")
    assert _resolve_declared_hh_ref(str(series), site_json) == series
    assert _resolve_declared_hh_ref(str(tmp_path / "missing.csv"), site_json) is None


def test_the_real_site_files_resolve_from_anywhere(tmp_path, monkeypatch) -> None:
    """The regression case: the real sites, run from a directory that is not the repo root.

    The site files are DISCOVERED, not listed. A hard-coded list silenced this test completely once:
    it named `bishops_cleeve` first, the metered rebuild split that file into two supplies and
    deleted it, and the per-name `pytest.skip` then fired on the first iteration — reporting
    "examples/real not installed on this host" on a host where it was installed, and checking none
    of the remaining sites. The skip now fires only on the condition it names, and the test asserts
    that it actually resolved something so it cannot pass on an empty loop.
    """
    repo = Path(__file__).resolve().parents[1]
    sites = repo / "examples" / "real" / "sites"
    site_files = sorted(sites.glob("*.json"))
    if not site_files:  # examples/real is gitignored private evidence
        pytest.skip("examples/real not installed on this host")
    # Quarantined customer data (WP-GRC-05), read only through the guard: the whole set is
    # released before any record is read, or the test skips as a whole, naming every blocker.
    records = dpa_released_sites(site_files, run_cross_checks=False)

    monkeypatch.chdir(tmp_path)
    checked = 0
    for site_json, site in zip(site_files, records, strict=True):
        ref = site.load_data.half_hourly_file_ref
        assert ref is not None, f"{site_json.name} declares no half_hourly_file_ref"
        if not (repo / ref).exists():
            continue
        resolved = _resolve_declared_hh_ref(ref, site_json)
        assert resolved is not None, f"{site_json.name}: {ref!r} did not resolve from {tmp_path}"
        assert resolved.resolve() == (repo / ref).resolve()
        checked += 1
    assert checked == len(site_files), (
        f"only {checked} of {len(site_files)} real site series resolved; a site whose declared "
        "half_hourly_file_ref does not exist on disk is a broken reference, not a skip condition"
    )


# --- the CLI path: the gate runs, or the quote says it could not ---------------------------------


def _minimal_site(path: Path, hh_ref: str | None, make_site) -> None:
    """The shared site factory, declaring ``hh_ref`` as its series: enough to reach the gate."""
    site = make_site()
    site = site.model_copy(
        update={
            "load_data": site.load_data.model_copy(
                update={
                    "half_hourly_file_ref": hh_ref,
                    "half_hourly_rows": 48,
                    "half_hourly_basis": "metered",
                    "annual_kwh": 500_000.0,
                }
            )
        }
    )
    path.write_text(site.model_dump_json(), encoding="utf-8")


def _write_series(path: Path, rows: int = 48, kwh: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(2025, 1, 1)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "kwh"])
        for i in range(rows):
            writer.writerow([(start + timedelta(minutes=30 * i)).isoformat(), kwh])


def _run(site_json: Path, out: Path) -> str:
    result = runner.invoke(
        app,
        ["run", str(site_json), "--assumptions", "gb_v0_1", "--out", str(out), "--json"],
    )
    assert result.exit_code == 0, result.output
    import json

    return json.dumps(json.loads(result.stdout)["warnings"])


GATE3_UNRUN = "LOAD RECONCILIATION (Gate 3) COULD NOT RUN"


def test_an_unresolvable_declared_ref_makes_the_quote_say_the_gate_did_not_run(
    tmp_path, monkeypatch, make_site
) -> None:
    """The silent case, now loud. This is the whole point of the change."""
    monkeypatch.chdir(tmp_path)
    site_json = tmp_path / "site.json"
    _minimal_site(site_json, "data/sites/hh/not_here.csv", make_site)
    warnings = _run(site_json, tmp_path / "out")
    assert GATE3_UNRUN in warnings
    assert "has therefore NOT been checked" in warnings
    assert "This is a missing check, not a passed one" in warnings


def test_a_resolvable_declared_ref_runs_the_gate_and_says_nothing_about_not_running(
    tmp_path, monkeypatch, make_site
) -> None:
    """Run from a directory the repo-relative path does not resolve against, and it still runs."""
    repo_like = tmp_path / "repo"
    site_json = repo_like / "examples" / "sites" / "site.json"
    site_json.parent.mkdir(parents=True)
    _write_series(repo_like / "data" / "sites" / "hh" / "series.csv")
    _minimal_site(site_json, "data/sites/hh/series.csv", make_site)

    elsewhere = tmp_path / "somewhere_else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    warnings = _run(site_json, tmp_path / "out")
    assert GATE3_UNRUN not in warnings
    # Gate 3 actually ran, and on this site it has something to say: the declared 500,000 kWh does
    # not reconcile with a 48-interval series. A LOUD divergence is the correct outcome, and it is
    # the outcome the old cwd-relative read could never reach from here.
    assert "Load reconciliation (Gate 3)" in warnings


def test_a_corrupt_declared_ref_is_reported_as_unreadable_not_as_absent(
    tmp_path, monkeypatch, make_site
) -> None:
    """ "Found but unreadable" and "not found" are different facts and must read differently."""
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "data" / "bad.csv"
    bad.parent.mkdir(parents=True)
    bad.write_text("this is not a half-hourly csv\n", encoding="utf-8")
    site_json = tmp_path / "site.json"
    _minimal_site(site_json, "data/bad.csv", make_site)
    warnings = _run(site_json, tmp_path / "out")
    assert GATE3_UNRUN in warnings
    assert "could not be read" in warnings


def test_a_site_with_no_declared_series_is_not_accused_of_a_missing_file(
    tmp_path, monkeypatch, make_site
) -> None:
    """Gate 2 already reports "no series at all" at band LOW; Gate 3 must not double-report it.

    The defect being closed is an un-run gate that LOOKS run. A site that declares no series has
    nothing to reconcile against and is already loudly capped, so adding a second notice here would
    be noise on every legitimately empty site.
    """
    monkeypatch.chdir(tmp_path)
    site_json = tmp_path / "site.json"
    _minimal_site(site_json, None, make_site)
    warnings = _run(site_json, tmp_path / "out")
    assert GATE3_UNRUN not in warnings
