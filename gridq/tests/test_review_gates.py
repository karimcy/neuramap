"""Tests for the local quality gate (``gridq.dev.review_gates``).

Pure file/text inspection — no pricing logic. The real repo must pass the gate; the
per-check failure modes are exercised against a minimal synthetic repo skeleton in
``tmp_path`` so each check can be broken in isolation.
"""

from __future__ import annotations

from pathlib import Path

from gridq.dev import review_gates as rg

REPO_ROOT = Path(__file__).resolve().parents[1]

SKILLS_BODY = (
    "# SKILLS.md\n\n## Project mission\n- x\n\n"
    f"{rg.SLOW_UPDATE_MARKERS[0]}\n## Foundational invariants\n- y\n{rg.SLOW_UPDATE_MARKERS[1]}\n"
)


def _make_passing_repo(root: Path) -> None:
    """Create the minimal file scaffolding the gate requires, so it passes green."""
    (root / "SKILLS.md").write_text(SKILLS_BODY, encoding="utf-8")

    skills = root / "skills"
    skills.mkdir()
    for name in ("edit_log.md", "rejected_edits.md", "skill_update_protocol.md"):
        (skills / name).write_text(f"# {name}\n", encoding="utf-8")

    shared = root / "docs" / "shared_work"
    shared.mkdir(parents=True)
    for name in rg.REQUIRED_SHARED_WORK:
        (shared / name).write_text(f"# {name}\n", encoding="utf-8")

    assumptions = root / "assumptions"
    assumptions.mkdir()
    (assumptions / "gb_v0_1.yaml").write_text("version: gb_v0_1\n", encoding="utf-8")

    examples = root / "examples"
    examples.mkdir()
    (examples / "site_t0.json").write_text("{}\n", encoding="utf-8")

    core = root / "src" / "gridq" / "engine"
    core.mkdir(parents=True)
    (core / "baseline.py").write_text('"""clean core module."""\n', encoding="utf-8")


# --- real repo -------------------------------------------------------------


def test_real_repo_passes_quality_gate() -> None:
    report = rg.check_quality_gate(repo_root=REPO_ROOT)
    assert report.passed, report.summary()


def test_report_summary_is_rendered() -> None:
    report = rg.check_quality_gate(repo_root=REPO_ROOT)
    summary = report.summary()
    assert "LOCAL QUALITY GATE" in summary
    assert "Overall: GREEN" in summary


# --- synthetic skeleton: passing + each failure mode -----------------------


def test_synthetic_skeleton_passes(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert report.passed, report.summary()


def _check(report: rg.QualityGateReport, name: str) -> rg.GateCheck:
    return next(c for c in report.checks if c.name == name)


def test_missing_skills_md_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    (tmp_path / "SKILLS.md").unlink()
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    assert not _check(report, "SKILLS.md exists").passed


def test_missing_protected_markers_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    # Rewrite SKILLS.md without the slow-update markers.
    (tmp_path / "SKILLS.md").write_text("# SKILLS.md\n## Project mission\n- x\n", encoding="utf-8")
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    assert not _check(report, "protected slow-update markers").passed


def test_missing_edit_log_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    (tmp_path / "skills" / "edit_log.md").unlink()
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    assert not _check(report, "skills/edit_log.md exists").passed


def test_missing_shared_work_file_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    (tmp_path / "docs" / "shared_work" / "HANDOFF_LOG.md").unlink()
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    shared_check = _check(report, "docs/shared_work files exist")
    assert not shared_check.passed
    assert "HANDOFF_LOG.md" in shared_check.detail


def test_missing_assumptions_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    (tmp_path / "assumptions" / "gb_v0_1.yaml").unlink()
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    assert not _check(report, "assumption files exist").passed


def test_missing_examples_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    (tmp_path / "examples" / "site_t0.json").unlink()
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    assert not _check(report, "example input files exist").passed


# --- banned-string scan ----------------------------------------------------


def test_banned_string_in_core_fails(tmp_path: Path) -> None:
    _make_passing_repo(tmp_path)
    bad = tmp_path / "src" / "gridq" / "finance" / "model.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("revenue = 1  # this is a fake bankable number\n", encoding="utf-8")
    report = rg.check_quality_gate(repo_root=tmp_path)
    assert not report.passed
    assert not _check(report, "no banned strings in core modules").passed


def test_banned_string_scan_is_case_insensitive(tmp_path: Path) -> None:
    bad = tmp_path / "src" / "gridq" / "outputs" / "memo.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("# TODO INVENT a tariff here\n", encoding="utf-8")
    hits = rg.scan_for_banned_strings(tmp_path)
    assert len(hits) == 1
    assert hits[0][2] == "todo invent"


def test_banned_string_scan_skips_dev_package(tmp_path: Path) -> None:
    # The dev/ package defines the banned-string patterns; it must not self-match.
    dev = tmp_path / "src" / "gridq" / "dev"
    dev.mkdir(parents=True)
    (dev / "review_gates.py").write_text('BANNED = ("fake bankable",)\n', encoding="utf-8")
    assert rg.scan_for_banned_strings(tmp_path) == []


def test_real_dev_modules_do_not_trip_the_scan() -> None:
    # The real review_gates/skills modules contain the literal patterns but live under
    # dev/, so the core scan stays clean.
    assert rg.scan_for_banned_strings(REPO_ROOT) == []
