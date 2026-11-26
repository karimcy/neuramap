"""Tests for the shared-work coordination helpers and SKILLS.md invariants.

Pure file/markdown behaviour — no pricing logic involved. Timestamps are injected so
the formatting test is deterministic.
"""

from datetime import datetime
from pathlib import Path

from gridq.dev import team_sync as ts

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED = datetime(2026, 1, 2, 9, 30)


# --- handoff formatting ----------------------------------------------------


def test_handoff_entry_formatting_is_deterministic() -> None:
    entry = ts.format_handoff_entry(
        "Oli",
        "Implemented the SiteModel schema",
        files_changed=["src/gridq/schemas/site_model.py", "tests/test_site_model.py"],
        tests_run="pytest (12 passed)",
        remaining=["wire OptimisationResult"],
        warnings=None,
        timestamp=FIXED,
    )
    assert entry.startswith("### 2026-01-02 09:30 — Oli\n")
    assert "- **Summary:** Implemented the SiteModel schema" in entry
    assert "src/gridq/schemas/site_model.py, tests/test_site_model.py" in entry
    assert "- **Tests run:** pytest (12 passed)" in entry
    assert "- **Warnings for next person:** —" in entry  # None -> placeholder


def test_add_handoff_entry_appends_to_file(tmp_path: Path) -> None:
    ts.add_handoff_entry("Olly", "first", base_dir=tmp_path, timestamp=FIXED)
    ts.add_handoff_entry("Oli", "second", base_dir=tmp_path, timestamp=FIXED)
    text = (tmp_path / "HANDOFF_LOG.md").read_text()
    assert text.index("first") < text.index("second")  # append-only order
    assert text.count("### 2026-01-02 09:30 —") == 2


# --- open comments ---------------------------------------------------------


def test_add_open_comment_creates_and_increments_ids(tmp_path: Path) -> None:
    cid1 = ts.add_open_comment(
        "Olly", "src/gridq/schemas/", "Please confirm the ASC field type", base_dir=tmp_path
    )
    cid2 = ts.add_open_comment("Oli", "src/gridq/finance/", "DSCR basis ok?", base_dir=tmp_path)
    assert cid1 == "C001"
    assert cid2 == "C002"
    text = (tmp_path / "OPEN_COMMENTS.md").read_text()
    assert "### C001 · status: open" in text
    assert "- **Author:** Olly" in text
    assert "Please confirm the ASC field type" in text


def test_resolve_open_comment_sets_status_and_response(tmp_path: Path) -> None:
    cid = ts.add_open_comment(
        "Olly", "src/gridq/schemas/", "Confirm sign convention", base_dir=tmp_path
    )
    found = ts.resolve_open_comment(cid, "Confirmed: + discharge / - charge", base_dir=tmp_path)
    assert found is True
    text = (tmp_path / "OPEN_COMMENTS.md").read_text()
    assert f"### {cid} · status: resolved" in text
    assert "- **Response:** Confirmed: + discharge / - charge" in text
    assert ts.resolve_open_comment("C999", "n/a", base_dir=tmp_path) is False  # missing id


# --- active plan -----------------------------------------------------------


def test_update_active_plan_updates_only_provided_fields(tmp_path: Path) -> None:
    ts.update_active_plan(milestone="Milestone 1", task="SiteModel schema", base_dir=tmp_path)
    ts.update_active_plan(
        task="OptimisationResult schema", base_dir=tmp_path
    )  # milestone unchanged
    inner = ts._between((tmp_path / "ACTIVE_PLAN.md").read_text(), *ts.ACTIVE_PLAN_MARKERS)
    assert "- **Current milestone:** Milestone 1" in inner
    assert "- **Current task:** OptimisationResult schema" in inner


# --- team sync summary -----------------------------------------------------


def _scaffold(tmp_path: Path) -> None:
    ts.update_active_plan(milestone="Milestone 1", task="schemas", base_dir=tmp_path)
    ts.add_handoff_entry("Oli", "set up schemas package", base_dir=tmp_path, timestamp=FIXED)
    ts.add_open_comment(
        "Olly", "src/gridq/schemas/", "review the stream registry", base_dir=tmp_path
    )
    (tmp_path / "BLOCKERS.md").write_text(
        "# BLOCKERS\n\n<!-- BLOCKERS:BEGIN -->\n\n"
        "### B001 · RTM partner unknown\n- **Why it matters:** gates wholesale modelling.\n"
        "\n<!-- BLOCKERS:END -->\n"
    )
    (tmp_path / "TEAM_SYNC.md").write_text(
        "# TEAM_SYNC\n\n## Next recommended actions\n- Freeze the SiteModel schema\n"
    )


def test_build_team_sync_summary_includes_all_sections(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    summary = ts.build_team_sync_summary(base_dir=tmp_path)
    for header in ["Active plan", "Latest handoffs", "Open comments", "Blockers", "Next tasks"]:
        assert header in summary
    assert "Milestone 1" in summary
    assert "set up schemas package" in summary
    assert "review the stream registry" in summary
    assert "RTM partner unknown" in summary
    assert "Freeze the SiteModel schema" in summary


def test_build_team_sync_summary_handles_empty_dir(tmp_path: Path) -> None:
    # Robustness: a dir with no shared-work files must not crash.
    summary = ts.build_team_sync_summary(base_dir=tmp_path)
    assert "TEAM SYNC DASHBOARD" in summary
    assert "(no active plan recorded)" in summary


# --- SKILLS.md invariant ---------------------------------------------------


def test_skills_md_has_protected_slow_update_markers() -> None:
    text = (REPO_ROOT / "SKILLS.md").read_text()
    start = text.find("<!-- SLOW_UPDATE_START -->")
    end = text.find("<!-- SLOW_UPDATE_END -->")
    assert start != -1, "SLOW_UPDATE_START marker missing"
    assert end != -1, "SLOW_UPDATE_END marker missing"
    assert start < end, "slow-update markers are out of order"
