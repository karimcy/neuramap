"""Local quality gates for the GridQ repo (NOT part of the pricing path).

`check_quality_gate()` is a fast, dependency-light *pre-flight* check a session (or a
human) runs before a handoff/merge. It does **not** replace the full quality bar
(`pytest` / `ruff` / `mypy` / `ruff format` — see `CLAUDE.md` §6 and `/run-quality`); it
checks the *structural* invariants those tools don't: that the governance scaffolding is
in place (`SKILLS.md` + its protected slow-update markers, the skills edit/rejected logs,
the shared-work coordination files), that the data fixtures the pipeline reads still
exist, and that no obvious "invented number / fake bankable output" smell strings have
crept into the core modules, and — since 2026-08-15 — that no **cost line moved silently**.

This module contains **no pricing or optimisation logic** and reads no numbers a lender
relies on. It only inspects the repo's file structure and text.

Run it::

    python -m gridq.dev.review_gates                    # diff-scoped vs HEAD
    python -m gridq.dev.review_gates --strict-cost-lines   # undeclared cost movement fails the gate
    python -m gridq.dev.review_gates --baseline main --repo-root /path/to/tree

(exits non-zero if a hard check fails).

The cost-line guard, and why it exists
--------------------------------------
The banned-string scan above is a **vocabulary** screen: three literal phrases, two of them
revenue/bankability flavoured (``"fake bankable"``, ``"hallucinated revenue"``). It has no
notion of a number *moving*, on either side of the P&L. That asymmetry had a cost. A change
that quietly reduces a cost improves returns exactly as effectively as one that inflates a
revenue, and nothing here could see it. Three such changes landed in the 2026-08 campaign — a
marginal degradation cost entering the dispatch objective (it decides whether the battery
cycles at all), a flat levy's treatment of round-trip losses, and a per-stream P90 haircut
moving — and two of the three were caught only by a human reading a diff.

:func:`scan_cost_line_movement` closes that hole. It is **diff-scoped** (default baseline
``HEAD``) for the same reason ``call_path_audit`` is: a cost constant that has sat unchanged
for six months is a design choice, one that moved on this branch is a thing to declare.
Whole-tree mode does not exist here, because every cost constant in the repo would report.

**What it can and cannot do.** It cannot know whether a cost *should* have moved, and it never
guesses a direction of effect. Its only claim is "this diff moved a cost line; say which way".
The author clears a finding by declaring it in the source, next to the change::

    # COST-LINE: WORSENS — c_deg charges every discharged kWh, so marginal cycling gets dearer.
    objective = objective - c_deg * discharge_kwh

The marker needs one of :data:`COST_DIRECTIONS` (``IMPROVES`` / ``WORSENS`` / ``NEUTRAL``, read
as "effect on returns") and must sit on the flagged line or within
:data:`COST_MARKER_WINDOW` lines of it. The guard checks that the claim *was made*; it cannot
check that it is true. That is a real limit, not a footnote: the value delivered is that a cost
change can no longer land with nobody having said anything about it.

**There is deliberately no off-switch.** ``--strict-cost-lines`` promotes findings from warn to
error, but no flag suppresses the scan. A global mute on a bias guard is the failure mode the
guard exists to prevent; the declaration marker is the only escape hatch.

Two detectors, both **file-level set differences** between the baseline blob and the working
blob (so a pure move, a reindent or a reformat reports nothing):

=====  ==============================================================================
C1     A ``(cost context, numeric literal)`` pair present on exactly one side. Catches
       a rate edited, a default introduced, a haircut zeroed, an ``or 0.0`` deleted.
C2     A ``(cost name, arithmetic operator, neighbour)`` term present on exactly one
       side. Catches a cost term entering or leaving an objective/aggregation with no
       literal anywhere in sight, which is exactly how the degradation cost landed.
=====  ==============================================================================

Both read only Python **tokens**, never raw text, so prose can never trip them: a docstring
line is one ``STRING`` token and an f-string format spec (``f"{x:,.2f}"``) is ``FSTRING_MIDDLE``
— neither yields a ``NAME`` or a ``NUMBER``. Assumption releases (``assumptions/*.yaml``) are
scanned line-wise for ``cost-ish key: number`` on the same set-difference basis.

A file that is wholly new (or wholly deleted) collapses to **one** finding naming the count,
not one per constant: a new assumption release is a single thing to review, and reporting 300
findings for it is how a guard gets switched off.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import io
import re
import subprocess
import sys
import token as _token
import tokenize
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

# --- locations -------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Severities. Only ``error`` checks can fail the gate; ``warn``/``info`` are advisory
# (e.g. a missing dev tool is noted but never fails the run — matching /run-quality).
ERROR = "error"
WARN = "warn"
INFO = "info"

# Smell strings that should never appear in the *core* modules — they are the textual
# signature of the failure modes SKILLS.md warns about (invented numbers, spoofed
# bankable output, placeholder "make something up" TODOs). Case-insensitive.
BANNED_STRINGS: tuple[str, ...] = (
    "fake bankable",
    "hallucinated revenue",
    "todo invent",
)

# Files that must exist for the governance + data scaffolding to be intact. Paths are
# relative to the repo root.
REQUIRED_FILES: tuple[str, ...] = (
    "SKILLS.md",
    "skills/edit_log.md",
    "skills/rejected_edits.md",
    "skills/skill_update_protocol.md",
)

REQUIRED_SHARED_WORK: tuple[str, ...] = (
    "TEAM_SYNC.md",
    "ACTIVE_PLAN.md",
    "HANDOFF_LOG.md",
    "OPEN_COMMENTS.md",
    "BLOCKERS.md",
)

SLOW_UPDATE_MARKERS = ("<!-- SLOW_UPDATE_START -->", "<!-- SLOW_UPDATE_END -->")

# Glob patterns that locate the versioned assumption library and the example inputs.
ASSUMPTION_GLOBS = ("assumptions/*.yaml", "assumptions/*.yml", "assumptions/*.json")
EXAMPLE_GLOBS = ("examples/*.json",)

# Packages under src/gridq/ that are *core* (pricing/domain) and therefore scanned for
# banned strings. ``dev`` is excluded: it is tooling (and this very module defines the
# banned-string patterns, which would otherwise self-match).
_NON_CORE_PACKAGES = {"dev"}


# --- cost-line vocabulary --------------------------------------------------
#
# Identifier fragments that mark a name as naming a COST the model charges. Matched
# case-insensitively as substrings of Python NAME tokens (and of YAML keys), so
# ``modelled_cost_gbp``, ``_DEG_COST_GBP_PER_MWH_DEFAULT`` and ``levies_on_charge`` all hit.
#
# The list is deliberately tight. Every entry earns its place by naming a cost *concept*, not
# a word that merely co-occurs with one. Four obvious candidates are DELIBERATELY ABSENT —
# ``rate``, ``price``, ``charge``, ``margin`` — because each appears on hundreds of lines that
# move no cost (``rates``, ``price_t``, ``charge_kw``, ``margin`` as a text column width), and
# a guard that reports those is a guard nobody runs. Leaving them out costs less recall than
# it looks: C2 sees a bare ``levy``/``cost``/``duos`` name entering an arithmetic term without
# needing the noisy words, and it is the term, not the word, that moves the money.
#
# The whole REVENUE side is absent too, and that is a gap rather than a decision: a revenue
# coefficient moving is the mirror image of this defect and is still unwatched. The
# ``side`` field on :class:`CostLineFinding` exists so that vocabulary can be added without
# reshaping the finding, the report or the tests.
COST_TOKENS: tuple[str, ...] = (
    # capital and operating cost
    "capex",
    "opex",
    "cost",
    "fee",
    # statutory / network cost lines on an imported kWh
    "levy",
    "levies",
    "duos",
    "tnuos",
    "bsuos",
    "standing_charge",
    "capacity_charge",
    "fixed_charge",
    # wear, losses and the efficiency penalty (a cost in kWh rather than £)
    "degrad",
    "c_deg",
    "round_trip",
    "efficien",
    "rte",
    "parasitic",
    "penalt",
    # the uncertainty discounts that behave exactly like a cost on the way to P90/DSCR
    "haircut",
    "derat",
    "de_rat",
    "shortfall",
    # the cost of money
    "wacc",
    "discount_rate",
    "hurdle",
    "interest_rate",
)

#: The declaration that clears a finding. Must carry one of :data:`COST_DIRECTIONS`.
COST_DECLARATION_MARKER = "COST-LINE:"

#: Direction of effect **on returns**, which is the question a reviewer is actually asking.
#: A cost that falls IMPROVES returns and needs more justification than one that WORSENS them.
COST_DIRECTIONS: tuple[str, ...] = ("IMPROVES", "WORSENS", "NEUTRAL")

#: How far above/below a flagged line the declaration may sit (noqa-style, not hunk-scoped:
#: with ``-U0`` a comment three lines up lands in a *different* hunk, and hunk-scoping would
#: then silently fail to find a declaration the author had plainly written).
COST_MARKER_WINDOW = 3

#: Arithmetic operators whose presence makes a cost name part of a computed term (C2).
_ARITH_OPS = frozenset({"+", "-", "*", "/", "//", "%", "**", "+=", "-=", "*=", "/="})

#: Assumption-release files scanned line-wise for ``cost-ish key: number``.
COST_YAML_GLOBS = ("assumptions/*.yaml", "assumptions/*.yml")

#: One ``key: 12.5`` line in an assumption release (trailing comment tolerated).
_YAML_NUM_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z_][\w.\-]*)\s*:\s*"
    r"(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?:#.*)?$"
)
_YAML_KEY_LINE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][\w.\-]*)\s*:\s*(?:#.*)?$")


# --- result types ----------------------------------------------------------


@dataclass(frozen=True)
class GateCheck:
    """One named check and its outcome."""

    name: str
    passed: bool
    detail: str
    severity: str = ERROR

    @property
    def is_blocking(self) -> bool:
        """True if a failure of this check should fail the whole gate."""
        return self.severity == ERROR and not self.passed


@dataclass(frozen=True)
class CostLineFinding:
    """One cost line this diff moved, and did not declare.

    A finding is a **suspicion, not a verdict**: it says "a cost the model charges changed
    here, and no direction of effect was stated". It never claims the change is wrong, and it
    never guesses which way returns moved — a static reader cannot know either.
    """

    path: str
    """Repo-relative path of the file."""

    line: int
    """Line number. In the *working* file for an addition, in the *baseline* file for a
    removal (where the line no longer exists to point at)."""

    detector: str
    """``C1`` (a numeric literal on a cost line) or ``C2`` (a cost term in an expression)."""

    side: str = "cost"
    """Which side of the P&L moved. Only ``cost`` is detected today; the field exists so the
    revenue-side vocabulary can be added without changing the finding schema or the report."""

    token: str = ""
    """The cost-vocabulary fragment that matched (``levy``, ``haircut``, ``c_deg`` …)."""

    change: str = ""
    """What moved, in the diff's own terms: ``added 21.3``, ``removed 0.0``, ``term added``."""

    text: str = ""
    """The source line, stripped. Truncated for display, never interpreted."""

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    def render(self) -> str:
        body = self.text if len(self.text) <= 88 else self.text[:85] + "..."
        return f"{self.location} [{self.detector}/{self.side}] {self.token}: {self.change} | {body}"

    @property
    def sort_key(self) -> tuple[str, int, str, str, str]:
        return (self.path, self.line, self.detector, self.token, self.change)


@dataclass
class QualityGateReport:
    """The collected outcome of ``check_quality_gate``."""

    checks: list[GateCheck] = field(default_factory=list)
    cost_findings: list[CostLineFinding] = field(default_factory=list)
    """Undeclared cost-line movements, for the detail block under the check table."""

    @property
    def passed(self) -> bool:
        """True only if no ``error``-severity check failed (warn/info never fail)."""
        return not any(c.is_blocking for c in self.checks)

    @property
    def failures(self) -> list[GateCheck]:
        return [c for c in self.checks if c.is_blocking]

    @property
    def warnings(self) -> list[GateCheck]:
        return [c for c in self.checks if c.severity == WARN and not c.passed]

    def summary(self) -> str:
        """A human-readable, deterministic pass/fail report (one line per check)."""
        lines = ["GRIDQ — LOCAL QUALITY GATE", "=" * 64]
        for c in self.checks:
            tag = "PASS" if c.passed else ("WARN" if c.severity != ERROR else "FAIL")
            lines.append(f"[{tag:4}] {c.name} — {c.detail}")
        lines.append("=" * 64)
        verdict = "GREEN" if self.passed else "RED"
        lines.append(
            f"Overall: {verdict} "
            f"({len(self.failures)} blocking failure(s), {len(self.warnings)} warning(s))"
        )
        lines.extend(self._cost_block())
        return "\n".join(lines)

    def _cost_block(self) -> list[str]:
        """The cost-line detail, printed in full under the table.

        The one-line table row is capped like every other row; this block is not, because a
        capped list is how a cost change stays invisible. It is the whole point of the check
        that every movement is named where a reader will see it.
        """
        if not self.cost_findings:
            return []
        out = [
            "",
            "UNDECLARED COST-LINE MOVEMENT",
            "-" * 64,
            "A cost the model charges moved in this diff and no direction of effect was",
            "stated. This is not a claim the change is wrong. Declare it in the source, on or",
            f"within {COST_MARKER_WINDOW} lines of the flagged line:",
            f"    # {COST_DECLARATION_MARKER} {'|'.join(COST_DIRECTIONS)} - why, and by how much.",
            "(direction = effect on RETURNS; a cost that falls improves them and needs the",
            "more justification of the two.)",
            "",
        ]
        out.extend(f"  {f.render()}" for f in self.cost_findings)
        out.append("-" * 64)
        return out


# --- low-level helpers -----------------------------------------------------


def _iter_core_py_files(repo_root: Path) -> list[Path]:
    """Return the ``*.py`` files in the *core* packages under ``src/gridq`` (excludes
    ``dev`` tooling and any ``__pycache__``)."""
    src = repo_root / "src" / "gridq"
    if not src.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src)
        if "__pycache__" in rel.parts:
            continue
        if rel.parts and rel.parts[0] in _NON_CORE_PACKAGES:
            continue
        out.append(p)
    return out


def scan_for_banned_strings(
    root: Path | str,
    *,
    patterns: tuple[str, ...] = BANNED_STRINGS,
) -> list[tuple[Path, int, str]]:
    """Scan the core ``*.py`` files under ``root`` for any banned smell string.

    Returns a list of ``(file, line_number, pattern)`` hits (empty == clean). The match
    is case-insensitive and substring-based — it is a deliberately blunt smell detector,
    not a parser.
    """
    root = Path(root)
    lowered = [p.lower() for p in patterns]
    hits: list[tuple[Path, int, str]] = []
    for path in _iter_core_py_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            low = line.lower()
            for original, needle in zip(patterns, lowered, strict=True):
                if needle in low:
                    hits.append((path, lineno, original))
    return hits


def _pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _exists(path: Path) -> bool:
    return path.exists()


def _globs_match(repo_root: Path, globs: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for g in globs:
        found.extend(sorted(repo_root.glob(g)))
    return found


# --- cost-line movement: git plumbing --------------------------------------


def _git(repo_root: Path, *args: str) -> str | None:
    """Run one git command. ``None`` on any failure — the caller must then report the scan
    as NOT CHECKED, never as clean (a silent fall-back would read as a fact about the code)."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _resolve_baseline(repo_root: Path, ref: str) -> str | None:
    if ref == "merge-base":
        out = _git(repo_root, "merge-base", "main", "HEAD")
        return out.strip() if out else None
    out = _git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return out.strip() if out else None


def _blob_at(repo_root: Path, sha: str, rel: str) -> str | None:
    """The file's text at ``sha``; ``None`` when it did not exist there."""
    return _git(repo_root, "show", f"{sha}:{rel}")


def _in_cost_scan_scope(rel: str) -> bool:
    """Core ``src/gridq`` Python (``dev/`` tooling excluded, as for the banned-string scan)
    plus the versioned assumption releases."""
    parts = rel.split("/")
    if rel.endswith(".py") and parts[:2] == ["src", "gridq"]:
        return len(parts) > 2 and parts[2] not in _NON_CORE_PACKAGES
    return parts[:1] == ["assumptions"] and rel.endswith((".yaml", ".yml"))


def _hunk_anchors(diff_text: str) -> dict[str, list[tuple[int, int, int]]]:
    """``{path: [(old_start, old_end, new_start), ...]}`` from a ``-U0`` unified diff.

    Used only to point a *removal* finding at somewhere in the working file, since the line
    it names no longer exists there and the declaration has to be looked for somewhere.
    """
    out: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    current = ""
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if line.startswith("+++ ") or not line.startswith("@@"):
            continue
        m = header.match(line)
        if not m or not current:
            continue
        o_start, o_count, n_start = int(m.group(1)), int(m.group(2) or 1), int(m.group(3))
        out[current].append((o_start, o_start + max(o_count, 1) - 1, n_start))
    return dict(out)


# --- cost-line movement: fact harvesting -----------------------------------


def _cost_tokens_in(name: str) -> tuple[str, ...]:
    """The cost tokens an identifier carries, matched on **word parts**, not raw substrings.

    Raw substring matching is what makes this class of tool cry wolf: ``rte`` is inside
    ``sorted``, ``fee`` is inside ``feed_in``, and a guard that reports ``sorted(...)`` as a
    round-trip-efficiency change gets switched off the same afternoon. So the identifier is
    split into ``_``-separated parts and:

    * a **short** token (<= 4 chars: ``cost``, ``duos``, ``levy``, ``fee``, ``rte``, ``wacc``)
      must equal a part exactly, optionally plus a trailing ``s``;
    * a **longer** token may match a part as a prefix (``degrad`` -> ``degradation``);
    * a token containing ``_`` (``round_trip``, ``standing_charge``, ``c_deg``) is matched
      against the whole lowered name, since the split would destroy it.
    """
    low = name.lower()
    parts = [p for p in low.split("_") if p]
    out: list[str] = []
    for tok in COST_TOKENS:
        if "_" in tok:
            if tok in low:
                out.append(tok)
            continue
        if len(tok) <= 4:
            if any(p == tok or p == f"{tok}s" for p in parts):
                out.append(tok)
        elif any(p.startswith(tok) for p in parts):
            out.append(tok)
    return tuple(out)


#: Integer literals that are structural rather than priced: indices, bounds, rounding
#: precision, unit conversions and calendar constants. A real cost quantity in this codebase
#: is a float (£/kWh, a percentage, a factor), so suppressing these costs no recall and
#: removes the largest measured false-positive family (``len(item) != 2``, ``if pct > 0``,
#: ``round(x, 6)``, ``item[0] < item[1] <= 24``).
_STRUCTURAL_INTS = frozenset(
    {"0", "1", "2", "3", "4", "6", "10", "12", "24", "48", "60", "100", "365", "1000", "3600"}
)

#: Unit conversions: percent, MWh<->kWh, days/year, hours/day, minutes, seconds, £m.
#: Suppressed ONLY when the literal sits immediately beside ``/`` or ``*``, i.e. when it is
#: being used to convert. That narrowness is what keeps the real catches: ``return 100.0 if
#: stream in _NO_P90_IN_TERM else pct`` (a per-stream haircut) has no such neighbour and
#: reports; ``capex = 1000.0`` reports. Nor can this hide a unit-scale bug — changing
#: ``/ 1000.0`` to ``/ 100.0`` shows up on the ADDED side as a literal that is no longer the
#: one the divisor used to be, and either way C2 reports the term.
_UNIT_LITERALS = frozenset(
    {
        "100",
        "100.0",
        "1000",
        "1000.0",
        "365",
        "365.0",
        "365.25",
        "24",
        "24.0",
        "60",
        "60.0",
        "1440",
        "1440.0",
        "3600",
        "3600.0",
        "1000000",
        "1000000.0",
    }
)
_UNIT_OPS = frozenset({"/", "*"})

#: ``0.0`` and ``1.0`` are the two most structural floats in the language, and in this codebase
#: they are overwhelmingly a clamp (``max(0.0, ...)``, ``min(1.0, ...)``) or a complement
#: (``1.0 - availability_factor``) rather than a price. They are kept only in the positions
#: where they ARE a price: after ``or`` (the ``rate or 0.0`` default, which is the exact
#: null-reads-as-zero pattern D45 names, and the single highest-value catch this guard makes),
#: after ``=`` (a declared default) and after ``return`` (a cost function that yields zero).
_CLAMP_FLOATS = frozenset({"0.0", "1.0"})
_CLAMP_KEEP_AFTER = frozenset({"or", "=", ":=", "return"})


def _norm_number(text: str) -> str:
    """Canonical form of a numeric literal, so ``0.50`` and ``0.5`` are the same number and a
    reformat is not reported as a movement."""
    raw = text.replace("_", "")
    try:
        if any(c in raw for c in ".eE") or raw.lower().endswith("j"):
            return repr(float(raw))
        return str(int(raw, 0))
    except ValueError:
        return raw


#: ``(cost token, normalised literal)`` -> the lines carrying it.
_C1Facts = dict[tuple[str, str], list[int]]
#: ``(cost name, operator, neighbour, side)`` -> the lines carrying it.
_C2Facts = dict[tuple[str, str, str, str], list[int]]

_SIGNIFICANT = frozenset({_token.NAME, _token.NUMBER, _token.OP})

#: Present from 3.12, absent on 3.11. See :func:`_significant_tokens`.
_FSTRING_START = getattr(_token, "FSTRING_START", None)
_FSTRING_END = getattr(_token, "FSTRING_END", None)


def _significant_tokens(tokens: list[tokenize.TokenInfo]) -> list[tokenize.TokenInfo]:
    """``NAME`` / ``NUMBER`` / ``OP`` tokens, with everything inside an f-string dropped.

    The f-string exclusion is about **determinism across Python versions**, not taste. On 3.11
    an f-string is one opaque ``STRING`` token, so its interpolations are invisible; from 3.12
    ``tokenize`` opens it up and yields real ``NAME``/``NUMBER`` tokens for the replacement
    fields. Without this, the same tree would report different cost movements on two
    interpreters — a finding that depends on the reader's Python is not a finding. 3.11's
    behaviour is the one adopted: an f-string is a message, and a message prices nothing.
    """
    out: list[tokenize.TokenInfo] = []
    depth = 0
    for t in tokens:
        if _FSTRING_START is not None and t.type == _FSTRING_START:
            depth += 1
            continue
        if _FSTRING_END is not None and t.type == _FSTRING_END:
            depth = max(depth - 1, 0)
            continue
        if depth == 0 and t.type in _SIGNIFICANT:
            out.append(t)
    return out


def _python_cost_facts(source: str) -> tuple[_C1Facts, _C2Facts] | None:
    """Harvest C1 and C2 facts from Python source. ``None`` if it will not parse.

    Everything is read from **tokens**, never from raw text, which is what makes prose
    incapable of tripping either detector: a docstring line is a single ``STRING`` token and
    an f-string's ``:,.2f`` format spec is ``FSTRING_MIDDLE`` (3.12) or part of the enclosing
    ``STRING`` (3.11). Neither yields a ``NAME`` or a ``NUMBER``.
    """
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, ValueError, tokenize.TokenError, UnicodeDecodeError):
        return None

    # A line inside `def build_cost_lines(...)` is a cost line even when the line itself names
    # no cost: `return 100.0 if stream in _NO_P90_IN_TERM else pct` is a haircut, and the only
    # thing that says so is the enclosing def's name.
    scope: dict[int, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        toks = _cost_tokens_in(node.name)
        if not toks:
            continue
        for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            scope[ln].update(toks)

    flat = _significant_tokens(tokens)
    by_line: dict[int, list[int]] = defaultdict(list)
    for idx, t in enumerate(flat):
        by_line[t.start[0]].append(idx)

    def neighbour_ops(idx: int) -> set[str]:
        ops: set[str] = set()
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(flat) and flat[j].type == _token.OP:
                ops.add(flat[j].string)
        return ops

    c1: _C1Facts = defaultdict(list)
    for ln, idxs in by_line.items():
        numbers = [i for i in idxs if flat[i].type == _token.NUMBER]
        if not numbers:
            continue
        on_line: set[str] = set()
        for i in idxs:
            if flat[i].type == _token.NAME:
                on_line.update(_cost_tokens_in(flat[i].string))
        context = on_line | set(scope.get(ln, ()))
        if not context:
            continue
        tok = sorted(context)[0]
        for i in numbers:
            norm = _norm_number(flat[i].string)
            is_int = "." not in norm and "e" not in norm.lower()
            # Four rules, each measured against this repo's own diffs and each killing a
            # distinct false-positive family. (1) An integer counts only when the line ITSELF
            # names a cost — an integer inside a cost-named function is an index or a bound far
            # more often than a price. (2) Structural integers never count. (3) A unit constant
            # beside ``/`` or ``*`` is a conversion. (4) ``0.0``/``1.0`` count only where they
            # are a value rather than a clamp or a complement.
            if is_int and (not on_line or norm in _STRUCTURAL_INTS):
                continue
            if norm in _UNIT_LITERALS and neighbour_ops(i) & _UNIT_OPS:
                continue
            prev = flat[i - 1].string if i > 0 else ""
            if norm in _CLAMP_FLOATS and prev not in _CLAMP_KEEP_AFTER:
                continue
            c1[(tok, norm)].append(ln)

    c2: _C2Facts = defaultdict(list)
    for i, t in enumerate(flat):
        if t.type != _token.NAME or not _cost_tokens_in(t.string):
            continue
        for j, side in ((i - 1, "L"), (i + 1, "R")):
            if not 0 <= j < len(flat):
                continue
            op = flat[j]
            if op.type != _token.OP or op.string not in _ARITH_OPS:
                continue
            k = j - 1 if side == "L" else j + 1
            other = ""
            if 0 <= k < len(flat) and flat[k].type in (_token.NAME, _token.NUMBER):
                other = flat[k].string.lower()
            if _norm_number(other) in _UNIT_LITERALS and op.string in _UNIT_OPS:
                continue  # ``cost * 100`` is a unit conversion, not a term entering a sum.
            c2[(t.string.lower(), op.string, other, side)].append(t.start[0])

    return dict(c1), dict(c2)


def _statement_starts(source: str) -> dict[int, int]:
    """``{line: first line of the statement it belongs to}``.

    A cost expression wrapped over five lines is **one** change, not five. Without this,
    ``p90_rev - penalty_year - dispatch_cost - operating_cost + avoided_variable_cost`` split
    one operand per line reports four times, which is the shape of noise that gets a guard
    ignored. Lines not inside any statement map to themselves.
    """
    out: dict[int, int] = {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return out
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.stmt)]
    # Widest first, so the innermost statement covering a line wins.
    nodes.sort(key=lambda n: (n.end_lineno or n.lineno) - n.lineno, reverse=True)
    for node in nodes:
        for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            out[ln] = node.lineno
    return out


def _yaml_cost_facts(source: str) -> _C1Facts:
    """``cost-ish key: number`` in an assumption release, keyed by the ancestor path too, so
    ``battery_capex: {installed_cost_gbp_per_kwh: 250}`` is a cost line at both levels."""
    facts: _C1Facts = defaultdict(list)
    stack: list[tuple[int, str]] = []
    for ln, raw in enumerate(source.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        num_match = _YAML_NUM_LINE.match(raw)
        if num_match:
            keys = [k for _, k in stack] + [num_match.group("key")]
            context: set[str] = set()
            for k in keys:
                context.update(_cost_tokens_in(k))
            if context:
                key = f"{sorted(context)[0]}/{num_match.group('key')}"
                facts[(key, _norm_number(num_match.group("num")))].append(ln)
            continue
        key_match = _YAML_KEY_LINE.match(raw)
        if key_match:
            stack.append((indent, key_match.group("key")))
    return dict(facts)


def _declared_lines(source: str) -> set[int]:
    """Lines carrying a well-formed ``COST-LINE:`` declaration (marker **and** a direction)."""
    out: set[int] = set()
    for ln, raw in enumerate(source.splitlines(), start=1):
        if COST_DECLARATION_MARKER not in raw:
            continue
        tail = raw.split(COST_DECLARATION_MARKER, 1)[1]
        if any(d in tail for d in COST_DIRECTIONS):
            out.add(ln)
    return out


def _is_declared(line: int, declared: set[int]) -> bool:
    return any(abs(line - d) <= COST_MARKER_WINDOW for d in declared)


# --- cost-line movement: the scan ------------------------------------------


@dataclass(frozen=True)
class CostScanResult:
    """Outcome of :func:`scan_cost_line_movement`.

    ``checked=False`` means the scan could not run (no git, not a work tree, an unresolvable
    baseline). It is **never** the same as "clean": an empty finding list from a scan that did
    not happen would read as evidence that no cost moved, which is precisely the confidently
    wrong number this repo's D45 rule is about.
    """

    findings: list[CostLineFinding]
    status: str
    checked: bool
    files_scanned: int = 0
    unscannable: tuple[str, ...] = ()


def scan_cost_line_movement(
    repo_root: Path | str,
    *,
    baseline: str = "HEAD",
) -> CostScanResult:
    """Report cost lines this diff moved without declaring a direction of effect.

    Diff-scoped against ``baseline`` (default ``HEAD``, i.e. the uncommitted working tree —
    what a session checks before a handoff). ``merge-base`` diffs the whole branch against
    ``main`` and reports proportionally more.
    """
    root = Path(repo_root)
    if not (root / ".git").exists():
        return CostScanResult([], f"NOT CHECKED - {root} is not a git work tree", False)
    base = _resolve_baseline(root, baseline)
    if base is None:
        return CostScanResult([], f"NOT CHECKED - cannot resolve baseline {baseline!r}", False)

    name_status = _git(root, "diff", "--name-status", "--no-renames", base, "--", ".")
    if name_status is None:
        return CostScanResult([], f"NOT CHECKED - git diff failed against {base[:8]}", False)
    diff_text = _git(root, "diff", "-U0", "--no-renames", base, "--", ".") or ""
    anchors = _hunk_anchors(diff_text)

    rows: list[tuple[str, str]] = []
    for row in name_status.splitlines():
        parts = row.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0].strip(), parts[-1].strip()))

    # Untracked files do not appear in ``git diff`` at all. Leaving them out would let a
    # brand-new pricing module land with every cost line in it unseen — the exact silent
    # landing this check exists to stop — so they are scanned as additions.
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", ".") or ""
    rows.extend(("A", rel.strip()) for rel in untracked.splitlines() if rel.strip())

    findings: list[CostLineFinding] = []
    unscannable: list[str] = []
    scanned = 0

    for status, rel in sorted(set(rows)):
        if not _in_cost_scan_scope(rel):
            continue
        old_text = None if status.startswith("A") else _blob_at(root, base, rel)
        new_text: str | None
        if status.startswith("D"):
            new_text = None
        else:
            try:
                new_text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                new_text = None
        scanned += 1
        file_findings = _findings_for_file(
            rel, old_text, new_text, anchors.get(rel, []), unscannable
        )
        findings.extend(file_findings)

    findings.sort(key=lambda f: f.sort_key)
    status_line = (
        f"diff-scoped vs {baseline} ({base[:8]}); {scanned} in-scope file(s), "
        f"{len(findings)} undeclared movement(s)"
    )
    if unscannable:
        status_line += f"; {len(unscannable)} file(s) NOT scanned: {', '.join(unscannable)}"
    return CostScanResult(findings, status_line, True, scanned, tuple(unscannable))


def _findings_for_file(
    rel: str,
    old_text: str | None,
    new_text: str | None,
    file_anchors: list[tuple[int, int, int]],
    unscannable: list[str],
) -> list[CostLineFinding]:
    is_yaml = rel.endswith((".yaml", ".yml"))

    def harvest(text: str | None) -> tuple[_C1Facts, _C2Facts] | None:
        if text is None:
            return {}, {}
        if is_yaml:
            return _yaml_cost_facts(text), {}
        return _python_cost_facts(text)

    old_facts = harvest(old_text)
    new_facts = harvest(new_text)
    if old_facts is None or new_facts is None:
        unscannable.append(rel)
        return [
            CostLineFinding(
                path=rel,
                line=1,
                detector="C0",
                token="",
                change="NOT SCANNED - the file would not parse; cost movement is UNKNOWN here",
                text="",
            )
        ]
    old_c1, old_c2 = old_facts
    new_c1, new_c2 = new_facts

    declared = _declared_lines(new_text or "")
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()

    # A wholly new (or wholly deleted) file collapses to ONE finding. A new assumption release
    # carries hundreds of cost constants and reporting each is how a guard gets switched off;
    # it is one thing to review, so it is one finding.
    if old_text is None or new_text is None:
        total = sum(len(v) for v in (new_c1 if old_text is None else old_c1).values())
        total += sum(len(v) for v in (new_c2 if old_text is None else old_c2).values())
        if not total:
            return []
        verb = "added" if old_text is None else "deleted"
        return [
            CostLineFinding(
                path=rel,
                line=1,
                detector="C1" if is_yaml else "C2",
                token="whole-file",
                change=(
                    f"file {verb} carrying {total} cost value(s)/term(s) - review as one release"
                ),
                text="",
            )
        ]

    def anchor_in_new(old_line: int) -> int:
        for o_start, o_end, n_start in file_anchors:
            if o_start <= old_line <= o_end:
                return max(n_start, 1)
        return old_line

    out: list[CostLineFinding] = []

    def emit(
        detector: str,
        token: str,
        change: str,
        lines: list[int],
        count: int,
        *,
        removed: bool,
    ) -> None:
        source_lines = old_lines if removed else new_lines
        for ln in sorted(lines)[-count:]:
            check_line = anchor_in_new(ln) if removed else ln
            if _is_declared(check_line, declared):
                continue
            text = source_lines[ln - 1].strip() if 0 < ln <= len(source_lines) else ""
            out.append(
                CostLineFinding(
                    path=rel, line=ln, detector=detector, token=token, change=change, text=text
                )
            )

    for key in sorted(set(old_c1) | set(new_c1)):
        tok, num = key
        delta = len(new_c1.get(key, [])) - len(old_c1.get(key, []))
        if delta > 0:
            emit("C1", tok, f"added literal {num}", new_c1[key], delta, removed=False)
        elif delta < 0:
            emit("C1", tok, f"removed literal {num}", old_c1[key], -delta, removed=True)

    for c2_key in sorted(set(old_c2) | set(new_c2)):
        name, op, other, side = c2_key
        term = f"{other} {op} {name}" if side == "L" else f"{name} {op} {other}"
        delta = len(new_c2.get(c2_key, [])) - len(old_c2.get(c2_key, []))
        if delta > 0:
            emit("C2", name, f"term added: {term.strip()}", new_c2[c2_key], delta, removed=False)
        elif delta < 0:
            emit("C2", name, f"term removed: {term.strip()}", old_c2[c2_key], -delta, removed=True)

    return _one_c2_per_statement(
        out,
        new_statements=_statement_starts(new_text) if not is_yaml else {},
        old_statements=_statement_starts(old_text) if not is_yaml else {},
    )


def _one_c2_per_statement(
    findings: list[CostLineFinding],
    *,
    new_statements: dict[int, int],
    old_statements: dict[int, int],
) -> list[CostLineFinding]:
    """Collapse C2 findings belonging to the same statement and direction to one.

    ``shortfall = invoiced_cost_gbp - modelled_cost_gbp`` has two cost-named operands and so
    matches C2 twice; ``a * b / c`` matches once per adjacent operator; an expression wrapped
    over five lines matches once per line. All of those are one thing to declare, and
    reporting each three or four times is how a reviewer learns to scroll past the block. C1 is
    left alone — two different literals changing in one statement really are two movements.
    """
    seen: set[tuple[str, int, bool]] = set()
    out: list[CostLineFinding] = []
    for f in findings:
        if f.detector != "C2":
            out.append(f)
            continue
        removed = "removed" in f.change
        statements = old_statements if removed else new_statements
        key = (f.path, statements.get(f.line, f.line), removed)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# --- the gate --------------------------------------------------------------


def check_quality_gate(
    *,
    repo_root: Path | str | None = None,
    baseline: str = "HEAD",
    strict_cost_lines: bool = False,
) -> QualityGateReport:
    """Run the local quality gate over ``repo_root`` (defaults to this repo).

    Each check appends a :class:`GateCheck`. ``error``-severity failures fail the gate;
    a missing dev tool (e.g. ``pytest`` not installed) is a ``warn`` and never fails the
    run — mirroring the project rule "skip a tool if uninstalled, don't fail the run".

    ``strict_cost_lines`` promotes undeclared cost-line movement from ``warn`` to ``error``.
    It defaults to ``warn`` for one honest reason and not a design preference: the tree this
    check landed on already carries undeclared movement, and defaulting to ``error`` would
    make the gate red for work that predates the rule. Flip the default once the standing
    movements are declared.
    """
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    checks: list[GateCheck] = []
    cost_findings: list[CostLineFinding] = []

    # 1. pytest available (advisory only).
    if _pytest_available():
        checks.append(GateCheck("pytest available", True, "pytest importable", INFO))
    else:
        checks.append(
            GateCheck(
                "pytest available",
                False,
                "pytest not installed — run the quality bar in an env that has it",
                WARN,
            )
        )

    # 2. SKILLS.md exists.
    skills_md = root / "SKILLS.md"
    checks.append(
        GateCheck(
            "SKILLS.md exists",
            _exists(skills_md),
            "found" if _exists(skills_md) else "missing SKILLS.md",
        )
    )

    # 3. protected slow-update markers present and ordered.
    start_m, end_m = SLOW_UPDATE_MARKERS
    if skills_md.exists():
        text = skills_md.read_text(encoding="utf-8")
        has_start = start_m in text
        has_end = end_m in text
        ordered = has_start and has_end and text.index(start_m) < text.index(end_m)
        if ordered:
            detail = "both slow-update markers present and ordered"
        elif has_start and has_end:
            detail = "slow-update markers present but out of order"
        else:
            missing = [m for m, ok in ((start_m, has_start), (end_m, has_end)) if not ok]
            detail = f"missing marker(s): {', '.join(missing)}"
        checks.append(GateCheck("protected slow-update markers", ordered, detail))
    else:
        checks.append(
            GateCheck("protected slow-update markers", False, "SKILLS.md missing — cannot check")
        )

    # 4 & 5 & part of governance: required governance files.
    for rel in REQUIRED_FILES:
        if rel == "SKILLS.md":
            continue  # already checked above
        p = root / rel
        checks.append(GateCheck(f"{rel} exists", _exists(p), "found" if _exists(p) else "missing"))

    # 6. shared-work coordination files.
    shared = root / "docs" / "shared_work"
    missing_shared = [f for f in REQUIRED_SHARED_WORK if not (shared / f).exists()]
    checks.append(
        GateCheck(
            "docs/shared_work files exist",
            not missing_shared,
            "all present" if not missing_shared else f"missing: {', '.join(missing_shared)}",
        )
    )

    # 7. assumption library files exist.
    assumption_files = _globs_match(root, ASSUMPTION_GLOBS)
    checks.append(
        GateCheck(
            "assumption files exist",
            bool(assumption_files),
            f"{len(assumption_files)} file(s): {', '.join(p.name for p in assumption_files)}"
            if assumption_files
            else "no assumption library file under assumptions/",
        )
    )

    # 8. example input files exist.
    example_files = _globs_match(root, EXAMPLE_GLOBS)
    checks.append(
        GateCheck(
            "example input files exist",
            bool(example_files),
            f"{len(example_files)} example input file(s)"
            if example_files
            else "no example inputs under examples/",
        )
    )

    # 9. no banned smell strings in core modules.
    hits = scan_for_banned_strings(root)
    if hits:
        detail = "; ".join(
            f"{p.relative_to(root)}:{ln} contains {pat!r}" for p, ln, pat in hits[:5]
        )
        if len(hits) > 5:
            detail += f" (+{len(hits) - 5} more)"
    else:
        detail = "core modules clean of invented-number / fake-bankable smell strings"
    checks.append(GateCheck("no banned strings in core modules", not hits, detail))

    # 10. no UNDECLARED cost-line movement. The banned-string scan above watches for an
    # invented revenue; this watches the other half of the same bias, because a cost quietly
    # removed improves returns exactly as effectively as a revenue invented. It reports
    # movement, never a verdict on it: the author declares the direction, the guard only
    # insists that somebody did.
    cost = scan_cost_line_movement(root, baseline=baseline)
    cost_findings = list(cost.findings)
    if not cost.checked:
        # Never "PASS". A scan that did not run is not a clean tree, and reading it as one is
        # how a zero becomes indistinguishable from a measurement.
        checks.append(GateCheck("cost-line movement declared", False, cost.status, WARN))
    elif cost.findings:
        shown = "; ".join(f.location for f in cost.findings[:5])
        detail = f"{len(cost.findings)} undeclared cost-line movement(s): {shown}"
        if len(cost.findings) > 5:
            detail += f" (+{len(cost.findings) - 5} more, listed below)"
        checks.append(
            GateCheck(
                "cost-line movement declared",
                False,
                detail,
                ERROR if strict_cost_lines else WARN,
            )
        )
    else:
        checks.append(GateCheck("cost-line movement declared", True, cost.status))

    return QualityGateReport(checks=checks, cost_findings=cost_findings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gridq.dev.review_gates",
        description="GridQ local quality gate (structural pre-flight; not the quality bar).",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="tree to check (default: this repo).",
    )
    parser.add_argument(
        "--baseline",
        default="HEAD",
        help=(
            "git ref the cost-line scan diffs against, or 'merge-base'. Default: HEAD (the "
            "uncommitted working tree, i.e. what this session is about to hand off)."
        ),
    )
    parser.add_argument(
        "--strict-cost-lines",
        action="store_true",
        help=(
            "promote undeclared cost-line movement from warn to error, so it fails the gate. "
            "There is no flag that switches the scan OFF: the declaration marker is the only "
            "escape hatch, because a mute button on a bias guard defeats the guard."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m gridq.dev.review_gates`` / ``gridq-review-gates``.

    Prints the report and returns 0 if the gate is green, 1 if any blocking check failed.
    """
    args = _build_parser().parse_args(argv)
    report = check_quality_gate(
        repo_root=args.repo_root,
        baseline=args.baseline,
        strict_cost_lines=args.strict_cost_lines,
    )
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
