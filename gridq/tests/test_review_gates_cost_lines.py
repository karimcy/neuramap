"""The cost-line half of the local bias guard (``gridq.dev.review_gates``).

The guard's older half watches for an invented *revenue* — three literal smell strings, two
of them revenue/bankability flavoured. It could not see a **cost** move, and a cost quietly
reduced improves returns exactly as effectively as a revenue invented. These tests pin the
half that closes that gap.

**Every assertion here is made at the layer a reader reads**: the text
``python -m gridq.dev.review_gates`` prints, driven as a real subprocess against a real git
work tree. That is deliberate and it is the whole point of the file. This repo has eleven times
shipped a check that was built, tested and called from nowhere, and ten of the eleven were
tested at their own layer and passed while being unreachable. A test that called
``scan_cost_line_movement`` directly would keep passing with the call site in
``check_quality_gate`` deleted; every test below goes red the moment that call site goes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gridq.dev import review_gates as rg

REPO_ROOT = Path(__file__).resolve().parents[1]

SKILLS_BODY = (
    "# SKILLS.md\n\n## Project mission\n- x\n\n"
    f"{rg.SLOW_UPDATE_MARKERS[0]}\n## Foundational invariants\n- y\n{rg.SLOW_UPDATE_MARKERS[1]}\n"
)

# A core module with one cost constant and one cost term. Kept minimal so a diff against it is
# unambiguous: whatever the guard reports, this file is the only thing that changed.
BASE_MODULE = '''"""Wear costs charged against dispatch."""

DEGRADATION_COST_GBP_PER_MWH = 21.3


def marginal_cycle_cost_gbp(mwh_discharged: float) -> float:
    """The wear the objective charges per MWh discharged."""
    return mwh_discharged * DEGRADATION_COST_GBP_PER_MWH


def dispatch_objective(revenue_gbp: float, deg_cost: float, discharge_mwh: float) -> float:
    """Bill reduction, before wear."""
    return revenue_gbp
'''

MODULE_REL = "src/gridq/finance/wear.py"


# --- synthetic repo --------------------------------------------------------


def _make_passing_repo(root: Path) -> None:
    """The minimal scaffolding every other gate check requires, so that a RED verdict in
    these tests is attributable to the cost-line check and nothing else."""
    (root / "SKILLS.md").write_text(SKILLS_BODY, encoding="utf-8")

    skills = root / "skills"
    skills.mkdir(exist_ok=True)
    for name in ("edit_log.md", "rejected_edits.md", "skill_update_protocol.md"):
        (skills / name).write_text(f"# {name}\n", encoding="utf-8")

    shared = root / "docs" / "shared_work"
    shared.mkdir(parents=True, exist_ok=True)
    for name in rg.REQUIRED_SHARED_WORK:
        (shared / name).write_text(f"# {name}\n", encoding="utf-8")

    assumptions = root / "assumptions"
    assumptions.mkdir(exist_ok=True)
    (assumptions / "gb_v0_1.yaml").write_text("version: gb_v0_1\n", encoding="utf-8")

    examples = root / "examples"
    examples.mkdir(exist_ok=True)
    (examples / "site_t0.json").write_text("{}\n", encoding="utf-8")

    module = root / MODULE_REL
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(BASE_MODULE, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _committed_repo(tmp_path: Path) -> Path:
    """A git work tree whose HEAD holds the passing skeleton. The uncommitted delta a test
    then makes IS the diff the guard is scoped to."""
    _make_passing_repo(tmp_path)
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "--quiet", "--no-verify", "-m", "baseline")
    return tmp_path


def _run_gate(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Drive the real entry point. Not an import, not a helper — the command a human runs."""
    return subprocess.run(
        [sys.executable, "-m", "gridq.dev.review_gates", "--repo-root", str(root), *extra],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )


def _rewrite(root: Path, new_body: str) -> None:
    (root / MODULE_REL).write_text(new_body, encoding="utf-8")


# --- the reader's layer: a moved cost constant is printed ------------------


def test_cli_prints_an_undeclared_cost_constant_movement(tmp_path: Path) -> None:
    """A cost rate edited in the working tree is named, with its file, line and both values,
    in the text the command prints. Delete the ``scan_cost_line_movement`` call site in
    ``check_quality_gate`` and this fails: the row and the block both vanish."""
    repo = _committed_repo(tmp_path)
    _rewrite(repo, BASE_MODULE.replace("21.3", "12.0"))

    result = _run_gate(repo)

    assert "cost-line movement declared" in result.stdout, result.stdout
    assert "UNDECLARED COST-LINE MOVEMENT" in result.stdout, result.stdout
    assert f"{MODULE_REL}:3" in result.stdout, result.stdout
    assert "added literal 12.0" in result.stdout, result.stdout
    assert "removed literal 21.3" in result.stdout, result.stdout
    # Advisory by default: it is loud, it does not block.
    assert "Overall: GREEN" in result.stdout
    assert result.returncode == 0


def test_cli_strict_mode_makes_the_movement_fail_the_gate(tmp_path: Path) -> None:
    """``--strict-cost-lines`` is the gate mode: the same movement, now blocking. The assert
    names the row so a RED verdict caused by some *other* check cannot pass this test."""
    repo = _committed_repo(tmp_path)
    _rewrite(repo, BASE_MODULE.replace("21.3", "12.0"))

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout
    assert "Overall: RED" in result.stdout, result.stdout
    assert result.returncode == 1


def test_a_clean_diff_prints_no_cost_block(tmp_path: Path) -> None:
    repo = _committed_repo(tmp_path)
    (repo / "docs" / "shared_work" / "BLOCKERS.md").write_text("# changed\n", encoding="utf-8")

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[PASS] cost-line movement declared" in result.stdout, result.stdout
    assert "UNDECLARED COST-LINE MOVEMENT" not in result.stdout
    assert result.returncode == 0


# --- declaring the direction of effect clears it ---------------------------


def test_declaring_the_direction_of_effect_clears_the_finding(tmp_path: Path) -> None:
    """The escape hatch, and the only one: say which way returns moved, in the source, next
    to the change. The guard cannot check the claim is TRUE — it checks it was made."""
    repo = _committed_repo(tmp_path)
    _rewrite(
        repo,
        BASE_MODULE.replace(
            "DEGRADATION_COST_GBP_PER_MWH = 21.3",
            "# COST-LINE: IMPROVES - re-derived from a current pack replacement cost;\n"
            "# cheaper wear means the objective cycles more, so returns rise.\n"
            "DEGRADATION_COST_GBP_PER_MWH = 12.0",
        ),
    )

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[PASS] cost-line movement declared" in result.stdout, result.stdout
    assert "UNDECLARED COST-LINE MOVEMENT" not in result.stdout
    assert result.returncode == 0


def test_a_marker_without_a_direction_does_not_clear_the_finding(tmp_path: Path) -> None:
    """A bare "COST-LINE: tweaked the rate" is not a declaration. One of IMPROVES / WORSENS /
    NEUTRAL is the content; without it the marker is a comment and the finding stands."""
    repo = _committed_repo(tmp_path)
    _rewrite(
        repo,
        BASE_MODULE.replace(
            "DEGRADATION_COST_GBP_PER_MWH = 21.3",
            "# COST-LINE: tweaked the rate\nDEGRADATION_COST_GBP_PER_MWH = 12.0",
        ),
    )

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout
    assert result.returncode == 1


def test_a_declaration_too_far_from_the_change_does_not_clear_it(tmp_path: Path) -> None:
    body = BASE_MODULE.replace("21.3", "12.0").replace(
        '"""Wear costs charged against dispatch."""',
        '"""Wear costs charged against dispatch.\n\n'
        "COST-LINE: WORSENS - a module-level note, far from the line it claims to cover.\n"
        '"""\n\n\n\n\n\n',
    )
    repo = _committed_repo(tmp_path)
    _rewrite(repo, body)

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout


# --- C2: a cost term with no numeric literal anywhere ----------------------


def test_a_cost_term_entering_an_objective_is_reported_with_no_literal(tmp_path: Path) -> None:
    """The motivating case, and the one a numeric-literal scan cannot see: a marginal
    degradation cost entering the dispatch objective. Not one digit changes."""
    repo = _committed_repo(tmp_path)
    _rewrite(
        repo,
        BASE_MODULE.replace(
            '    """Bill reduction, before wear."""\n    return revenue_gbp',
            '    """Bill reduction, net of wear."""\n'
            "    return revenue_gbp - deg_cost * discharge_mwh",
        ),
    )

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout
    assert "term added" in result.stdout, result.stdout
    assert "deg_cost" in result.stdout, result.stdout
    assert result.returncode == 1


def test_a_cost_term_leaving_an_expression_is_reported(tmp_path: Path) -> None:
    """The direction that improves returns, and therefore the one that most needs seeing: a
    wear term deleted from the objective. The line no longer exists in the working file, so
    the guard has to point at the baseline's line number and still find it."""
    with_wear = BASE_MODULE.replace(
        "    return revenue_gbp\n",
        "    return revenue_gbp - deg_cost * discharge_mwh\n",
    )
    repo = _committed_repo(tmp_path)
    _rewrite(repo, with_wear)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "--no-verify", "-m", "wear in the objective")
    _rewrite(repo, BASE_MODULE)

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout
    assert "term removed" in result.stdout, result.stdout
    assert "deg_cost" in result.stdout, result.stdout


# --- the cry-wolf budget ---------------------------------------------------


def test_moving_a_cost_constant_unchanged_reports_nothing(tmp_path: Path) -> None:
    """Detection is a file-level set difference, not a line diff, so a pure move reports
    nothing. A guard that fires on every reindent is a guard that gets switched off."""
    repo = _committed_repo(tmp_path)
    _rewrite(repo, BASE_MODULE.replace('"""Wear costs charged against dispatch."""', "# wear\n"))

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[PASS] cost-line movement declared" in result.stdout, result.stdout


def test_prose_about_costs_never_reports(tmp_path: Path) -> None:
    """Docstrings, comments and f-string messages are read as tokens, so a paragraph full of
    the words cost and levy and a dozen numbers moves nothing."""
    repo = _committed_repo(tmp_path)
    _rewrite(
        repo,
        BASE_MODULE.replace(
            '"""The wear the objective charges per MWh discharged."""',
            '"""The wear the objective charges per MWh discharged.\n\n'
            "    Derived at 21.3 GBP/MWh from a 120000 GBP/MWh pack replacement cost over 6000\n"
            "    cycles at 85 percent depth, with the levy stack and the 0.86 round-trip\n"
            "    efficiency handled elsewhere. Costs 12.5 percent more than the 2024 basis.\n"
            '    """\n'
            '    note = f"wear {DEGRADATION_COST_GBP_PER_MWH:,.2f} GBP/MWh, 100.0% charged"\n'
            "    assert note",
        ),
    )

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[PASS] cost-line movement declared" in result.stdout, result.stdout


def test_unit_conversions_and_clamps_do_not_report(tmp_path: Path) -> None:
    """The two measured false-positive families: ``/ 1000.0`` unit conversion and the
    ``max(0.0, min(1.0, ...))`` clamp/complement idiom."""
    repo = _committed_repo(tmp_path)
    _rewrite(
        repo,
        BASE_MODULE + "\n\ndef cost_share(mwh: float) -> float:\n"
        "    per_kwh = DEGRADATION_COST_GBP_PER_MWH / 1000.0\n"
        "    clamped = max(0.0, min(1.0, mwh))\n"
        "    return per_kwh * clamped * 365.25\n",
    )

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[PASS] cost-line movement declared" in result.stdout, result.stdout


def test_a_null_default_on_a_cost_is_still_reported(tmp_path: Path) -> None:
    """The counterweight to the clamp rule. ``rate or 0.0`` is the exact pattern D45 names —
    a missing tariff line that prices a stream at zero indistinguishably from a measurement —
    so ``0.0`` after ``or`` survives the suppression that kills ``max(0.0, ...)``."""
    repo = _committed_repo(tmp_path)
    _rewrite(
        repo,
        BASE_MODULE.replace(
            "    return mwh_discharged * DEGRADATION_COST_GBP_PER_MWH",
            "    duos_red_rate = None\n    return mwh_discharged * (duos_red_rate or 0.0)",
        ),
    )

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout
    assert "0.0" in result.stdout


def test_a_wholly_new_file_collapses_to_one_finding(tmp_path: Path) -> None:
    """A new assumption release carries hundreds of cost values. It is one thing to review,
    so it is one finding; reporting each is how a guard gets switched off."""
    repo = _committed_repo(tmp_path)
    lines = "\n".join(f"    installed_cost_gbp_per_kwh_{i}: {100 + i}.5" for i in range(40))
    (repo / "assumptions" / "gb_v9_9.yaml").write_text(
        f"version: gb_v9_9\nbattery_capex:\n{lines}\n", encoding="utf-8"
    )

    result = _run_gate(repo, "--strict-cost-lines")

    body = result.stdout.split("UNDECLARED COST-LINE MOVEMENT", 1)[-1]
    reported = [ln for ln in body.splitlines() if "assumptions/gb_v9_9.yaml" in ln]
    assert len(reported) == 1, result.stdout
    assert "review as one release" in reported[0]


def test_an_edited_assumption_value_is_reported_with_both_figures(tmp_path: Path) -> None:
    """The case the YAML detector exists for, and the one no real diff has yet exercised: a
    capex rate edited inside an EXISTING release. Every release on this branch was a brand-new
    file, so the whole-file collapse above was the only YAML path any data had ever reached —
    which is this repo's signature failure shape (a limb tested in aggregate only) one layer
    down from the call site."""
    repo = _committed_repo(tmp_path)
    release = repo / "assumptions" / "gb_v9_9.yaml"
    before = (
        "version: gb_v9_9\n"
        "battery_capex:\n"
        "  installed_cost_gbp_per_kwh: 250.0\n"
        "  installed_cost_gbp_per_mw_power_component: 180000.0\n"
    )
    release.write_text(before, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "--no-verify", "-m", "release")

    release.write_text(before.replace("250.0", "200.0"), encoding="utf-8")

    result = _run_gate(repo, "--strict-cost-lines")

    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout
    assert "assumptions/gb_v9_9.yaml:3" in result.stdout, result.stdout
    assert "added literal 200.0" in result.stdout, result.stdout
    assert "removed literal 250.0" in result.stdout, result.stdout
    assert "installed_cost_gbp_per_kwh" in result.stdout, result.stdout
    # The untouched sibling key must not report, or every release edit drags its whole file in.
    assert "180000.0" not in result.stdout, result.stdout


# --- the degrade path: never silently clean --------------------------------


def test_a_non_git_tree_reports_not_checked_never_clean(tmp_path: Path) -> None:
    """A scan that could not run is not a clean tree. Reporting PASS here would make "no cost
    moved" indistinguishable from "nobody looked", which is the same confidently-wrong-zero
    failure the null rule exists to stop. It stays advisory, so a non-git tree (every
    ``tmp_path`` fixture in the suite) never turns the gate red for a tooling reason."""
    _make_passing_repo(tmp_path)

    result = _run_gate(tmp_path, "--strict-cost-lines")

    assert "[WARN] cost-line movement declared" in result.stdout, result.stdout
    assert "NOT CHECKED" in result.stdout, result.stdout
    assert "[PASS] cost-line movement declared" not in result.stdout
    assert "Overall: GREEN" in result.stdout
    assert result.returncode == 0


def test_an_unparseable_core_module_is_reported_not_skipped(tmp_path: Path) -> None:
    """A file the scanner cannot read is a hole in the scan, and a hole that reports nothing
    is worse than one that reports itself."""
    repo = _committed_repo(tmp_path)
    _rewrite(repo, BASE_MODULE + "\ndef broken(:\n")

    result = _run_gate(repo, "--strict-cost-lines")

    assert "NOT SCANNED" in result.stdout, result.stdout
    assert "[FAIL] cost-line movement declared" in result.stdout, result.stdout


# --- there is no off-switch ------------------------------------------------


def test_the_scan_has_no_mute_flag(tmp_path: Path) -> None:
    """A global off-switch on a bias guard is the failure mode the guard exists to prevent,
    so the CLI must not grow one. The declaration marker is the only escape hatch."""
    repo = _committed_repo(tmp_path)
    _rewrite(repo, BASE_MODULE.replace("21.3", "12.0"))

    for flag in ("--no-cost-lines", "--skip-cost-lines", "--disable-cost-lines"):
        result = _run_gate(repo, flag)
        assert result.returncode == 2, f"{flag} was accepted: {result.stdout}{result.stderr}"
