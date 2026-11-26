"""The customer-data scan and its ratchet (WP-GRC-05, acceptance criterion 3 for files in git).

The fast-tier ratchet fails on: a tracked file under a customer-data root that the manifest does
not list, a listed file whose bytes changed, a listed file's bytes tracked at another path, a
half-hourly-shaped CSV outside the roots that nobody declared, a manifest longer than the pinned
count, and a waiver list longer than its pinned count. Both lists may only shrink. The scan reports
paths only; nothing here prints or reads a customer figure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests._dpa_support import register_fixture_dpas

from gridq.governance import customer_data, dpa_scan
from gridq.governance.customer_data import (
    DatasetEntry,
    Manifest,
    classify_path,
    load_manifest,
    scan,
)
from gridq.governance.dpa_guard import repo_root, sha256_file

#: Customer datasets tracked in git on 2026-11-26 without DPA evidence. May only go DOWN: a
#: removal (owner's decision) or a release by registered DPAs never needs this changed; a new
#: customer file in git fails here first.
PINNED_DATASET_COUNT = 78
#: Test modules on the read-only legacy validation waiver. May only go DOWN.
PINNED_WAIVER_COUNT = 12


def test_the_manifest_is_complete_and_no_longer_than_pinned() -> None:
    result = scan()
    assert [(f.kind, f.path) for f in result.findings] == []
    manifest = load_manifest()
    assert len(manifest.datasets) <= PINNED_DATASET_COUNT
    assert manifest.raw["dataset_count"] == len(manifest.datasets)
    assert len(result.unevidenced) + len(result.evidenced) == len(manifest.datasets)


def test_every_listed_dataset_is_quarantined_and_attributed() -> None:
    known_sites = {s for c in customer_data.CUSTOMERS.values() for s in c["site_ids"]}
    for entry in load_manifest().datasets:
        assert entry.status == "quarantined"
        assert entry.customer in customer_data.CUSTOMERS
        assert entry.site_ids and set(entry.site_ids) <= known_sites
        assert entry.sha256.startswith("sha256:")


def test_the_waiver_list_only_shrinks_and_names_real_modules() -> None:
    modules = load_manifest().waiver_modules
    assert len(modules) <= PINNED_WAIVER_COUNT
    assert len(set(modules)) == len(modules)
    for module in modules:
        assert (repo_root() / module).is_file(), module
        assert module.startswith("tests/") and module.endswith(".py")


def test_the_demo_is_declared_non_customer_and_is_no_customer_file() -> None:
    from gridq.governance.dpa_guard import DEMO_ATTESTATION

    manifest = load_manifest()
    listed = {e.path for e in manifest.datasets}
    hashes = {e.sha256 for e in manifest.datasets}
    for rel, digest in DEMO_ATTESTATION.items():
        assert rel not in listed and digest not in hashes
    assert "examples/demo_site/hh_2025.csv" in manifest.not_customer_data


def test_the_scan_cli_prints_paths_only_and_exits_one_while_data_is_unevidenced(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = dpa_scan.main([])
    out = capsys.readouterr().out
    assert code == 1
    assert "criterion 3 for files in git: NOT MET" in out
    listed = {e.path for e in load_manifest().datasets}
    indented = [line.strip() for line in out.splitlines() if line.startswith("  ")]
    allowed = listed | set(load_manifest().review_prose)
    assert indented and set(indented) <= allowed  # every detail line is a tracked path


def test_the_scan_cli_json_is_paths_and_labels(capsys: pytest.CaptureFixture[str]) -> None:
    assert dpa_scan.main(["--json"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["criterion_3_met"] is False
    assert body["unevidenced_count"] == len(load_manifest().datasets)
    assert set(body["unevidenced"][0]) == {"path", "customer", "kind", "status"}


def test_a_broken_registry_stops_the_scan_rather_than_reading_as_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert dpa_scan.main(["--evidence-dir", str(tmp_path)]) == 2


def test_the_run_store_listing_names_entries_without_a_dpa_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "runs"
    (store / "uploads").mkdir(parents=True)
    (store / "uploads" / "hh_legacy.csv").write_text("x", encoding="utf-8")
    (store / "quote_legacy").mkdir()
    (store / "quote_legacy" / "quote.json").write_text("{}", encoding="utf-8")
    (store / "quote_ok").mkdir()
    (store / "quote_ok" / "quote.json").write_text("{}", encoding="utf-8")
    (store / "quote_ok" / "dpa_evidence.json").write_text("{}", encoding="utf-8")
    missing = dpa_scan._run_store_without_record(store)
    assert missing == [str(store / "quote_legacy"), str(store / "uploads" / "hh_legacy.csv")]


# ---------------------------------------------------------------------------------------------
# The scan's findings, on a fictitious repository
# ---------------------------------------------------------------------------------------------
def _fixture_manifest(root: Path) -> Manifest:
    listed = root / "customers" / "listed.csv"
    listed.parent.mkdir(parents=True)
    listed.write_text("timestamp,import_kw\n1,2\n", encoding="utf-8")
    gone = DatasetEntry("customers/gone.csv", "sha256:" + "0" * 64, "fixture", ("FIXTURE-A",), "x")
    entry = DatasetEntry(
        "customers/listed.csv", sha256_file(listed), "fixture", ("FIXTURE-A",), "hh_series"
    )
    return Manifest(
        customer_roots=("customers",),
        not_customer_data=("market/declared.csv",),
        datasets=(entry, gone),
        review_prose=(),
        waiver_modules=(),
    )


def test_each_kind_of_drift_is_found(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    (tmp_path / "customers" / "unlisted.csv").write_text("a", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "copy.csv").write_bytes(
        (tmp_path / "customers" / "listed.csv").read_bytes()
    )
    header = "timestamp,import_kwh\n"
    (tmp_path / "elsewhere" / "series.csv").write_text(header + "t,1\n" * 400, encoding="utf-8")
    (tmp_path / "market").mkdir()
    (tmp_path / "market" / "declared.csv").write_text(header + "t,1\n" * 400, encoding="utf-8")
    tracked = [
        "customers/listed.csv",
        "customers/unlisted.csv",
        "elsewhere/copy.csv",
        "elsewhere/series.csv",
        "market/declared.csv",
    ]
    result = scan(tmp_path, manifest=manifest, registry=[], tracked=tracked)
    found = {(f.kind, f.path) for f in result.findings}
    assert found == {
        ("unlisted", "customers/unlisted.csv"),
        ("copy", "elsewhere/copy.csv"),
        ("unlisted_hh_shaped", "elsewhere/series.csv"),
        ("stale", "customers/gone.csv"),
    }
    (tmp_path / "customers" / "listed.csv").write_text("changed", encoding="utf-8")
    result = scan(tmp_path, manifest=manifest, registry=[], tracked=["customers/listed.csv"])
    assert ("changed", "customers/listed.csv") in {(f.kind, f.path) for f in result.findings}
    assert not result.criterion_3_met


def test_a_dataset_leaves_the_unevidenced_list_when_every_site_has_a_dpa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _fixture_manifest(tmp_path)
    tracked = ["customers/listed.csv"]
    assert [e.path for e in scan(tmp_path, manifest=manifest, tracked=tracked).unevidenced] == [
        "customers/listed.csv"
    ]
    register_fixture_dpas(monkeypatch, tmp_path / "ev", "FIXTURE-A")
    result = scan(tmp_path, manifest=manifest, tracked=tracked)
    assert [e.path for e in result.evidenced] == ["customers/listed.csv"]
    assert result.unevidenced == ()


def test_classification_fails_closed() -> None:
    assert classify_path("examples/real/Grundon/Ewelme MRF.csv") == (
        "grundon",
        ("grundon_ewelme",),
    )
    customer, sites = classify_path("deliverables/harwell_memo.md", content="see grundon") or (
        "",
        (),
    )
    assert customer == "harwell" and "grundon_tanhouse" in sites  # names another customer: both
    assert classify_path("deliverables/unknown.html") is None
    assert classify_path("deliverables/unknown.html", content="Harwell campus") == (
        "harwell",
        ("harwell_campus_k16w304527",),
    )


def test_the_pre_commit_hook_ships_and_runs_the_scan() -> None:
    import os

    hook = repo_root() / "scripts" / "hooks" / "pre-commit-dpa-scan"
    assert hook.is_file() and os.access(hook, os.X_OK)
    assert "gridq.governance.dpa_scan" in hook.read_text(encoding="utf-8")


def test_the_scan_module_runs_as_a_script() -> None:
    # This checkout's source, not whichever checkout the interpreter's editable install names.
    proc = subprocess.run(
        [sys.executable, "-m", "gridq.governance.dpa_scan", "--json"],
        capture_output=True,
        text=True,
        cwd=repo_root(),
        env={**os.environ, "PYTHONPATH": str(repo_root() / "src")},
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["criterion_3_met"] is False
