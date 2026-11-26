"""Non-LLM helpers for the SKILLS.md self-evolution loop (skill update protocol).

These are the deterministic, mechanical parts of ``skills/skill_update_protocol.md`` —
the bits a human or a Claude session can run without any model in the loop: load the
file, validate a *candidate* against the structural rules, check the protected
slow-update region was not touched, apply a small bounded patch, and append to the
accepted / rejected logs.

They contain **no LLM call and no pricing logic**. The *judgement* of the protocol
(is an edit worth making? what is the evidence?) stays with the human/agent running the
loop; this module only enforces the safety rails:

- at most 3 edits per patch;
- the protected ``<!-- SLOW_UPDATE_START/END -->`` block is off-limits unless the caller
  explicitly passes ``slow_update=True``;
- the candidate must keep every required heading and both markers;
- the candidate must stay compact unless ``allow_long=True`` overrides it.

See ``skills/skill_update_protocol.md``, ``skills/validation_cases.md`` and
``docs/parallel_development_plan.md`` §4 (skills governance).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

# --- locations -------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_PATH = _REPO_ROOT / "SKILLS.md"
EDIT_LOG_PATH = _REPO_ROOT / "skills" / "edit_log.md"
REJECTED_PATH = _REPO_ROOT / "skills" / "rejected_edits.md"

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"

# Headings every candidate SKILLS.md must keep (matched as a prefix of a `##`/`###`
# heading line, so "Common failure modes (watch for these)" still matches). Mirrors
# skills/validation_cases.md.
REQUIRED_HEADINGS: tuple[str, ...] = (
    "Project mission",
    "Non-negotiable rules",
    "Build workflow",
    "Collaboration workflow",
    "Deterministic pricing rules",
    "Assumption / provenance rules",
    "Testing and validation rules",
    "Documentation update rules",
    "Common failure modes",
)

# Compactness ceiling. SKILLS.md is procedural memory, not a manual — a candidate over
# this size needs an explicit ``allow_long`` override. The live file is ~6.5 KB; the cap
# leaves generous headroom for bounded edits while still catching a runaway addition.
MAX_SKILL_CHARS = 12_000

VALID_OPS = ("append", "insert_after", "replace", "delete")
MAX_EDITS_PER_PATCH = 3


# --- result types ----------------------------------------------------------


@dataclass
class SkillValidation:
    """Outcome of :func:`validate_skill_structure`."""

    ok: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class PatchResult:
    """Outcome of :func:`apply_patch_if_valid`."""

    accepted: bool
    reason: str = ""  # empty when accepted; the rejection reason otherwise
    candidate: str | None = None  # the new SKILLS.md text when accepted
    edits_applied: int = 0


class PatchError(ValueError):
    """An edit could not be applied mechanically (bad anchor / span / op)."""


# --- load / validate -------------------------------------------------------


def load_skill(path: str | Path | None = None) -> str:
    """Read the SKILLS.md text (defaults to the repo's ``SKILLS.md``)."""
    return Path(path or SKILLS_PATH).read_text(encoding="utf-8")


def _protected_region(text: str) -> str | None:
    """Return the text *between* the slow-update markers, or ``None`` if a marker is
    missing or the markers are out of order."""
    if SLOW_UPDATE_START not in text or SLOW_UPDATE_END not in text:
        return None
    start = text.index(SLOW_UPDATE_START)
    end = text.index(SLOW_UPDATE_END)
    if start >= end:
        return None
    return text[start + len(SLOW_UPDATE_START) : end]


def check_protected_region(original: str, candidate: str, *, slow_update: bool = False) -> bool:
    """True if the candidate respects the protected slow-update block.

    With ``slow_update=False`` (the normal fast loop) the region between the markers must
    be **byte-identical** to the original and both markers must survive. With
    ``slow_update=True`` the region may change (an explicit, deliberate slow-update), but
    the markers must still be present and ordered in the candidate.
    """
    cand_region = _protected_region(candidate)
    if cand_region is None:
        return False  # markers must always survive
    if slow_update:
        return True
    orig_region = _protected_region(original)
    if orig_region is None:
        # No protected block to protect in the original; nothing to violate.
        return True
    return orig_region == cand_region


def validate_skill_structure(
    text: str,
    *,
    slow_update: bool = False,
    max_chars: int = MAX_SKILL_CHARS,
    allow_long: bool = False,
) -> SkillValidation:
    """Validate a *candidate* SKILLS.md against the structural rules.

    Checks (all MUST unless noted): both slow-update markers present and ordered; every
    required heading present; no fenced code block; compactness (SHOULD — overridable
    with ``allow_long``). It does **not** compare against the original protected region —
    that is :func:`check_protected_region`. ``slow_update`` is accepted for symmetry but
    does not relax the structural checks (the markers are still required).
    """
    problems: list[str] = []

    has_start = SLOW_UPDATE_START in text
    has_end = SLOW_UPDATE_END in text
    if not has_start:
        problems.append(f"missing {SLOW_UPDATE_START} marker")
    if not has_end:
        problems.append(f"missing {SLOW_UPDATE_END} marker")
    if has_start and has_end and text.index(SLOW_UPDATE_START) >= text.index(SLOW_UPDATE_END):
        problems.append("slow-update markers are out of order")

    for heading in REQUIRED_HEADINGS:
        if not re.search(rf"(?m)^#{{2,}}\s+{re.escape(heading)}", text):
            problems.append(f"missing required heading: {heading}")

    if "```" in text:
        problems.append(
            "contains a fenced code block (SKILLS.md is procedural prose, not a script)"
        )

    if not allow_long and len(text) > max_chars:
        problems.append(
            f"not compact: {len(text)} chars > {max_chars} cap (pass allow_long to override)"
        )

    return SkillValidation(ok=not problems, problems=problems)


# --- log appenders ---------------------------------------------------------

_EDIT_LOG_HEADER = (
    "# SKILLS.md edit log\n\n"
    "Append-only. One row per **accepted** edit to `SKILLS.md`. Newest first. "
    "Mark foundational (protected-block) changes `SLOW_UPDATE`.\n\n"
    "| date | op | target section/anchor | summary | evidence | type |\n"
    "|------|----|-----------------------|---------|----------|------|\n"
)

_REJECTED_HEADER = (
    "# Rejected SKILLS.md edits\n\n"
    "Append-only. One row per **rejected** candidate edit and why. Newest first.\n\n"
    "| date | proposed edit (op · target · gist) | reason for rejection |\n"
    "|------|------------------------------------|----------------------|\n"
)

_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|\s*$")


def _today() -> str:
    return _date.today().isoformat()


def _ensure_table(path: Path, header: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")


def _insert_after_separator(path: Path, header: str, row: str) -> str:
    """Insert ``row`` directly below the table's separator line (newest-first ordering).

    The file is initialised from ``header`` if it does not exist yet. Returns the row
    that was written.
    """
    _ensure_table(path, header)
    lines = path.read_text(encoding="utf-8").splitlines()
    sep_idx = next((i for i, ln in enumerate(lines) if _TABLE_SEP_RE.match(ln.strip())), None)
    if sep_idx is None:
        # No recognisable table; append the row at the end as a fallback.
        lines.append(row)
    else:
        lines.insert(sep_idx + 1, row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return row


def append_skill_edit_log(
    op: str,
    target: str,
    summary: str,
    evidence: str,
    *,
    type_: str = "normal",
    date: str | None = None,
    path: str | Path | None = None,
) -> str:
    """Append one **accepted** edit row to ``skills/edit_log.md``. Returns the row text."""
    row = (
        f"| {date or _today()} | {op} | {target} | "
        f"{summary.strip()} | {evidence.strip()} | {type_} |"
    )
    return _insert_after_separator(Path(path or EDIT_LOG_PATH), _EDIT_LOG_HEADER, row)


def append_rejected_edit(
    proposed: str,
    reason: str,
    *,
    date: str | None = None,
    path: str | Path | None = None,
) -> str:
    """Append one **rejected** edit row to ``skills/rejected_edits.md``. Returns the row."""
    row = f"| {date or _today()} | {proposed.strip()} | {reason.strip()} |"
    return _insert_after_separator(Path(path or REJECTED_PATH), _REJECTED_HEADER, row)


# --- patch application -----------------------------------------------------


def _normalise_edits(
    patch: Mapping[str, object] | Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], bool, bool, str]:
    """Pull ``edits`` plus the patch-level flags out of a patch mapping (or a bare list
    of edits). Returns ``(edits, slow_update, allow_long, evidence)``."""
    if isinstance(patch, Mapping):
        raw: object = patch.get("edits", [])
        slow_update = bool(patch.get("slow_update", False))
        allow_long = bool(patch.get("allow_long", False))
        evidence = str(patch.get("evidence", "")).strip()
    else:
        raw = patch
        slow_update = allow_long = False
        evidence = ""
    # A well-formed patch carries a list/sequence of edit mappings; anything else is
    # treated as "no edits" and rejected downstream as an empty patch.
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        edits = [dict(e) for e in raw]
    else:
        edits = []
    return edits, slow_update, allow_long, evidence


def _apply_one_edit(text: str, edit: Mapping[str, object]) -> str:
    """Apply a single edit operation to ``text`` and return the new text.

    Raises :class:`PatchError` for an unknown op, a missing anchor/span, or a non-unique
    span (so a patch never edits the wrong place silently).
    """
    op = str(edit.get("op", "")).strip()
    if op not in VALID_OPS:
        raise PatchError(f"unknown op {op!r}; allowed: {', '.join(VALID_OPS)}")

    if op == "append":
        addition = str(edit.get("text", "")).rstrip("\n")
        if not addition:
            raise PatchError("append needs non-empty 'text'")
        return text.rstrip("\n") + "\n\n" + addition + "\n"

    if op == "insert_after":
        anchor = str(edit.get("anchor", ""))
        addition = str(edit.get("text", "")).rstrip("\n")
        if not anchor:
            raise PatchError("insert_after needs an 'anchor'")
        if not addition:
            raise PatchError("insert_after needs non-empty 'text'")
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if anchor in line:
                glue = "" if line.endswith("\n") else "\n"
                lines.insert(i + 1, glue + addition + "\n")
                return "".join(lines)
        raise PatchError(f"insert_after anchor not found: {anchor!r}")

    # replace / delete both operate on an exact 'old' span.
    old = str(edit.get("old", ""))
    if not old:
        raise PatchError(f"{op} needs an 'old' span")
    count = text.count(old)
    if count == 0:
        raise PatchError(f"{op} span not found: {old!r}")
    if count > 1:
        raise PatchError(f"{op} span is not unique ({count} matches): {old!r}")
    new = "" if op == "delete" else str(edit.get("new", ""))
    return text.replace(old, new, 1)


def _apply_edits(text: str, edits: Sequence[Mapping[str, object]]) -> str:
    for edit in edits:
        text = _apply_one_edit(text, edit)
    return text


def _edit_gist(edits: Sequence[Mapping[str, object]]) -> str:
    parts = []
    for e in edits:
        op = str(e.get("op", "?"))
        target = str(e.get("anchor") or e.get("target") or e.get("old") or "—")
        parts.append(f"{op}·{target[:40]}")
    return f"{len(edits)} edit(s): " + "; ".join(parts)


def apply_patch_if_valid(
    patch: Mapping[str, object] | Sequence[Mapping[str, object]],
    *,
    skills_path: str | Path | None = None,
    edit_log_path: str | Path | None = None,
    rejected_path: str | Path | None = None,
    original: str | None = None,
    date: str | None = None,
) -> PatchResult:
    """Validate a patch against the safety rails and apply it iff it passes.

    A *patch* is a mapping ``{"edits": [...], "slow_update": bool, "allow_long": bool,
    "evidence": str}`` (or a bare list of edits). Each edit is ``{"op": ..., ...}`` for
    one of ``append`` / ``insert_after`` / ``replace`` / ``delete``.

    On **accept**: writes the new SKILLS.md and appends one row per edit to the edit log;
    returns ``PatchResult(accepted=True, candidate=<new text>)``.

    On **reject**: writes nothing to SKILLS.md, appends one row to ``rejected_edits.md``
    with the reason, and returns ``PatchResult(accepted=False, reason=...)``. Rejection
    happens (in order) when: there are no edits, more than 3 edits, an edit cannot be
    applied mechanically, the protected block would change without ``slow_update=True``,
    or the candidate fails structural validation.
    """
    skills_path = Path(skills_path or SKILLS_PATH)
    edits, slow_update, allow_long, evidence = _normalise_edits(patch)
    src_text = original if original is not None else load_skill(skills_path)

    def _reject(reason: str) -> PatchResult:
        append_rejected_edit(_edit_gist(edits), reason, date=date, path=rejected_path)
        return PatchResult(accepted=False, reason=reason, edits_applied=0)

    # Rail 1: bounded edit count.
    if not edits:
        return _reject("empty patch: no edits to apply")
    if len(edits) > MAX_EDITS_PER_PATCH:
        return _reject(f"too many edits: {len(edits)} > max {MAX_EDITS_PER_PATCH} per patch")

    # Apply mechanically (a bad anchor/span is a rejection, not a crash).
    try:
        candidate = _apply_edits(src_text, edits)
    except PatchError as exc:
        return _reject(f"edit could not be applied: {exc}")

    # Rail 2: protected slow-update block.
    if not check_protected_region(src_text, candidate, slow_update=slow_update):
        return _reject(
            "edit touches the protected slow-update block (pass slow_update=True for a "
            "deliberate slow-update) or drops a slow-update marker"
        )

    # Rail 3 & 4: structure + compactness.
    validation = validate_skill_structure(candidate, slow_update=slow_update, allow_long=allow_long)
    if not validation.ok:
        return _reject("candidate failed validation: " + "; ".join(validation.problems))

    # Accept: write the candidate and log each edit.
    skills_path.write_text(candidate, encoding="utf-8")
    edit_type = "SLOW_UPDATE" if slow_update else "normal"
    for edit in edits:
        op = str(edit.get("op", "?"))
        target = str(edit.get("anchor") or edit.get("target") or edit.get("old") or "(append)")
        summary = str(edit.get("summary") or f"{op} edit via skill update protocol")
        ev = str(edit.get("evidence") or evidence or "(see proposal)")
        append_skill_edit_log(
            op, target[:60], summary, ev, type_=edit_type, date=date, path=edit_log_path
        )

    return PatchResult(accepted=True, reason="", candidate=candidate, edits_applied=len(edits))
