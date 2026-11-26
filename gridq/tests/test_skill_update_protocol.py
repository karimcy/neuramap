"""Tests for the non-LLM skill-update helpers (``gridq.dev.skills``).

These exercise the mechanical safety rails of ``skills/skill_update_protocol.md``:
structural validation, the protected slow-update guard, the ≤3-edit cap, and the
accept/reject logging. All writes go to copies in ``tmp_path`` — the real SKILLS.md and
logs are never touched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gridq.dev import skills as sk

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_DATE = "2026-01-02"


@pytest.fixture
def skills_files(tmp_path: Path) -> dict[str, Path]:
    """Copy the live SKILLS.md + logs into tmp_path so patches don't touch the repo."""
    paths = {
        "skills": tmp_path / "SKILLS.md",
        "edit_log": tmp_path / "edit_log.md",
        "rejected": tmp_path / "rejected_edits.md",
    }
    shutil.copy(REPO_ROOT / "SKILLS.md", paths["skills"])
    shutil.copy(REPO_ROOT / "skills" / "edit_log.md", paths["edit_log"])
    shutil.copy(REPO_ROOT / "skills" / "rejected_edits.md", paths["rejected"])
    return paths


def _apply(patch: object, files: dict[str, Path]) -> sk.PatchResult:
    return sk.apply_patch_if_valid(
        patch,  # type: ignore[arg-type]
        skills_path=files["skills"],
        edit_log_path=files["edit_log"],
        rejected_path=files["rejected"],
        date=FIXED_DATE,
    )


# --- structural validation -------------------------------------------------


def test_valid_skill_passes() -> None:
    result = sk.validate_skill_structure(sk.load_skill(REPO_ROOT / "SKILLS.md"))
    assert result.ok, result.problems


def test_protected_marker_absence_fails() -> None:
    text = sk.load_skill(REPO_ROOT / "SKILLS.md").replace(sk.SLOW_UPDATE_START, "")
    result = sk.validate_skill_structure(text)
    assert not result.ok
    assert any("SLOW_UPDATE_START" in p for p in result.problems)


def test_missing_required_heading_fails() -> None:
    text = sk.load_skill(REPO_ROOT / "SKILLS.md").replace("## Build workflow", "## Build steps")
    result = sk.validate_skill_structure(text)
    assert not result.ok
    assert any("Build workflow" in p for p in result.problems)


def test_compactness_fails_without_override_passes_with_it() -> None:
    text = sk.load_skill(REPO_ROOT / "SKILLS.md")
    bloated = text + ("\n- filler bullet that adds no behaviour" * 2000)
    assert not sk.validate_skill_structure(bloated).ok
    assert sk.validate_skill_structure(bloated, allow_long=True).ok


# --- protected region guard ------------------------------------------------


def test_check_protected_region_detects_change() -> None:
    original = sk.load_skill(REPO_ROOT / "SKILLS.md")
    inside = "- The pricing path is deterministic and contains no LLM/stochastic AI."
    changed = original.replace(inside, inside + " (edited)")
    assert sk.check_protected_region(original, original) is True
    assert sk.check_protected_region(original, changed) is False
    # ...but an explicit slow-update permits it.
    assert sk.check_protected_region(original, changed, slow_update=True) is True


# --- patch application: accept ---------------------------------------------


def test_accepted_patch_logs_edit(skills_files: dict[str, Path]) -> None:
    before_rows = skills_files["edit_log"].read_text().count("\n|")
    result = _apply(
        {
            "edits": [
                {
                    "op": "insert_after",
                    "anchor": "## Common failure modes",
                    "text": "- A new, evidence-backed procedural bullet.",
                    "summary": "codify a recurring lesson",
                    "evidence": "HANDOFF 2026-01-02",
                }
            ]
        },
        skills_files,
    )
    assert result.accepted, result.reason
    assert result.edits_applied == 1
    # SKILLS.md changed and still valid.
    new_text = skills_files["skills"].read_text()
    assert "A new, evidence-backed procedural bullet." in new_text
    assert sk.validate_skill_structure(new_text).ok
    # edit_log gained exactly one row, dated and carrying the evidence.
    log = skills_files["edit_log"].read_text()
    assert log.count("\n|") == before_rows + 1
    assert FIXED_DATE in log
    assert "HANDOFF 2026-01-02" in log


def test_multiple_valid_edits_each_logged(skills_files: dict[str, Path]) -> None:
    before = skills_files["edit_log"].read_text().count("\n|")
    result = _apply(
        {
            "edits": [
                {"op": "append", "text": "- bullet one", "summary": "one"},
                {"op": "append", "text": "- bullet two", "summary": "two"},
            ],
            "evidence": "shared evidence",
        },
        skills_files,
    )
    assert result.accepted, result.reason
    assert skills_files["edit_log"].read_text().count("\n|") == before + 2


# --- patch application: reject ---------------------------------------------


def test_patch_with_more_than_three_edits_fails(skills_files: dict[str, Path]) -> None:
    original = skills_files["skills"].read_text()
    before_rejected = skills_files["rejected"].read_text().count("\n|")
    result = _apply(
        {"edits": [{"op": "append", "text": f"- bullet {i}"} for i in range(4)]},
        skills_files,
    )
    assert not result.accepted
    assert "max 3" in result.reason
    assert skills_files["skills"].read_text() == original  # untouched
    assert skills_files["rejected"].read_text().count("\n|") == before_rejected + 1


def test_protected_section_edit_fails(skills_files: dict[str, Path]) -> None:
    original = skills_files["skills"].read_text()
    before_rejected = skills_files["rejected"].read_text().count("\n|")
    result = _apply(
        {
            "edits": [
                {
                    "op": "replace",
                    "old": "- The pricing path is deterministic and contains no LLM/stochastic AI.",
                    "new": "- The pricing path may use an LLM.",
                }
            ]
        },
        skills_files,
    )
    assert not result.accepted
    assert "protected" in result.reason
    assert skills_files["skills"].read_text() == original  # untouched
    assert skills_files["rejected"].read_text().count("\n|") == before_rejected + 1


def test_protected_section_edit_allowed_under_slow_update(skills_files: dict[str, Path]) -> None:
    old = "- The pricing path is deterministic and contains no LLM/stochastic AI."
    result = _apply(
        {
            "slow_update": True,
            "edits": [{"op": "replace", "old": old, "new": old + " (clarified wording)"}],
        },
        skills_files,
    )
    assert result.accepted, result.reason
    assert "(clarified wording)" in skills_files["skills"].read_text()
    # The accepted slow-update is logged with the SLOW_UPDATE type.
    assert "SLOW_UPDATE" in skills_files["edit_log"].read_text()


def test_bad_anchor_is_rejected_not_crash(skills_files: dict[str, Path]) -> None:
    result = _apply(
        {"edits": [{"op": "insert_after", "anchor": "NO SUCH ANCHOR ZZZ", "text": "x"}]},
        skills_files,
    )
    assert not result.accepted
    assert "anchor not found" in result.reason


def test_rejected_patch_logs_reason(skills_files: dict[str, Path]) -> None:
    result = _apply({"edits": []}, skills_files)
    assert not result.accepted
    rejected = skills_files["rejected"].read_text()
    assert FIXED_DATE in rejected
    assert result.reason in rejected or "empty patch" in rejected


# --- log appenders create files when missing -------------------------------


def test_append_edit_log_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "edit_log.md"
    sk.append_skill_edit_log("append", "—", "summary", "evidence", date=FIXED_DATE, path=path)
    text = path.read_text()
    assert "# SKILLS.md edit log" in text
    assert f"| {FIXED_DATE} | append |" in text


def test_append_rejected_edit_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "rejected_edits.md"
    sk.append_rejected_edit("append · X · gist", "speculative", date=FIXED_DATE, path=path)
    text = path.read_text()
    assert "# Rejected SKILLS.md edits" in text
    assert "speculative" in text
