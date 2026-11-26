"""Shared-work coordination helpers for the GridQ repo (contributor: Olly).

Maintains the Markdown files under ``docs/shared_work/`` (handoff log, open comments,
active plan, blockers) and builds a printable team-sync dashboard. This is collaboration
tooling only — it contains **no pricing or optimisation logic** and touches no numbers a
lender relies on. Timestamps are injectable so behaviour is testable/deterministic.

Run the dashboard:  ``python -m gridq.dev.team_sync``  (or the ``gridq-team-sync`` script).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

# --- locations -------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SHARED_WORK = _REPO_ROOT / "docs" / "shared_work"

HANDOFF_FILE = "HANDOFF_LOG.md"
COMMENTS_FILE = "OPEN_COMMENTS.md"
ACTIVE_PLAN_FILE = "ACTIVE_PLAN.md"
BLOCKERS_FILE = "BLOCKERS.md"
TEAM_SYNC_FILE = "TEAM_SYNC.md"

HANDOFF_MARKERS = ("<!-- HANDOFF_ENTRIES:BEGIN -->", "<!-- HANDOFF_ENTRIES:END -->")
COMMENTS_MARKERS = ("<!-- OPEN_COMMENTS:BEGIN -->", "<!-- OPEN_COMMENTS:END -->")
ACTIVE_PLAN_MARKERS = ("<!-- ACTIVE_PLAN:BEGIN -->", "<!-- ACTIVE_PLAN:END -->")
BLOCKERS_MARKERS = ("<!-- BLOCKERS:BEGIN -->", "<!-- BLOCKERS:END -->")

# Ordered (field-key -> label) for the ACTIVE_PLAN managed block.
ACTIVE_PLAN_FIELDS: dict[str, str] = {
    "milestone": "Current milestone",
    "task": "Current task",
    "files": "Files expected to change",
    "acceptance": "Acceptance criteria",
    "risks": "Risks",
    "last_touched": "Last touched by",
    "notes": "Notes for the next person",
}

VALID_COMMENT_STATUSES = ("open", "resolved", "deferred")


# --- contributors / attribution -------------------------------------------

#: The contributor(s) who work in this repo. Every change is attributed to one of them so the
#: handoff log always answers "who made this change, and when".
CONTRIBUTORS: tuple[str, ...] = ("Olly",)

# Case-insensitive aliases → canonical contributor name, so attribution stays clean whether a
# session sets "olly", "oli", an email prefix, etc.
_CONTRIBUTOR_ALIASES: dict[str, str] = {
    "olly": "Olly",
    "oli": "Olly",
    "ollie": "Olly",
    "o": "Olly",
    "op": "Olly",
}


def resolve_contributor(name: str | None) -> str | None:
    """Normalise a contributor name to its canonical form (``Olly``).

    Unknown names pass through unchanged (so an automated/agent author is never lost); an
    empty/``None`` name returns ``None``.
    """
    if not name or not name.strip():
        return None
    return _CONTRIBUTOR_ALIASES.get(name.strip().lower(), name.strip())


def current_contributor() -> str | None:
    """The contributor for this session, read from the ``GRIDQ_CONTRIBUTOR`` env var.

    Set it **per machine** in ``.claude/settings.local.json`` (``env.GRIDQ_CONTRIBUTOR``) to
    ``Olly`` so every change is attributed automatically. Returns ``None`` if unset.
    """
    return resolve_contributor(os.environ.get("GRIDQ_CONTRIBUTOR"))


# --- small file/markdown helpers ------------------------------------------


def _shared_dir(base_dir: str | Path | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    env = os.environ.get("GRIDQ_SHARED_WORK_DIR")
    return Path(env) if env else DEFAULT_SHARED_WORK


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ensure(path: Path, title: str, markers: tuple[str, str]) -> None:
    """Create a marker-bearing file if it doesn't exist yet."""
    if not path.exists():
        begin, end = markers
        _write(path, f"# {title}\n\n{begin}\n{end}\n")


def _join(value: str | Sequence[str] | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value.strip() or "—"
    items = [str(v).strip() for v in value if str(v).strip()]
    return ", ".join(items) if items else "—"


def _insert_before(text: str, end_marker: str, block: str) -> str:
    block = block.rstrip() + "\n"
    if end_marker in text:
        return text.replace(end_marker, block + "\n" + end_marker, 1)
    return text.rstrip() + "\n\n" + block


def _between(text: str, begin: str, end: str) -> str:
    if begin in text and end in text:
        return text.split(begin, 1)[1].split(end, 1)[0].strip()
    return text.strip()


def _replace_between(text: str, begin: str, end: str, new_inner: str) -> str:
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    return pattern.sub(begin + "\n" + new_inner + "\n" + end, text, count=1)


def _blocks(section: str) -> list[str]:
    """Split a section into '### ...' blocks."""
    parts = re.split(r"(?m)^(?=### )", section)
    return [p.strip() for p in parts if p.strip().startswith("### ")]


def _section(md: str, heading: str) -> str:
    """Return the body under a '## heading' until the next '## ' (or EOF)."""
    m = re.search(rf"(?m)^#{{2,}}\s+{re.escape(heading)}\s*$", md)
    if not m:
        return ""
    rest = md[m.end() :]
    nxt = re.search(r"(?m)^##\s", rest)
    return (rest[: nxt.start()] if nxt else rest).strip()


# --- handoff log -----------------------------------------------------------


def format_handoff_entry(
    contributor: str,
    summary: str,
    files_changed: str | Sequence[str] | None = None,
    tests_run: str | Sequence[str] | None = None,
    remaining: str | Sequence[str] | None = None,
    warnings: str | Sequence[str] | None = None,
    *,
    timestamp: datetime | None = None,
) -> str:
    """Format one handoff entry as a Markdown block (deterministic given ``timestamp``)."""
    ts = (timestamp or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return (
        f"### {ts} — {contributor}\n"
        f"- **Summary:** {summary.strip()}\n"
        f"- **Files changed:** {_join(files_changed)}\n"
        f"- **Tests run:** {_join(tests_run)}\n"
        f"- **Remaining tasks:** {_join(remaining)}\n"
        f"- **Warnings for next person:** {_join(warnings)}\n"
    )


def add_handoff_entry(
    contributor: str,
    summary: str,
    files_changed: str | Sequence[str] | None = None,
    tests_run: str | Sequence[str] | None = None,
    remaining: str | Sequence[str] | None = None,
    warnings: str | Sequence[str] | None = None,
    *,
    timestamp: datetime | None = None,
    base_dir: str | Path | None = None,
) -> str:
    """Append a handoff entry to HANDOFF_LOG.md (append-only). Returns the entry text."""
    path = _shared_dir(base_dir) / HANDOFF_FILE
    _ensure(path, "HANDOFF_LOG", HANDOFF_MARKERS)
    entry = format_handoff_entry(
        contributor, summary, files_changed, tests_run, remaining, warnings, timestamp=timestamp
    )
    _write(path, _insert_before(_read(path), HANDOFF_MARKERS[1], entry))
    return entry


# --- open comments ---------------------------------------------------------


def format_open_comment(
    comment_id: str,
    author: str,
    target: str,
    comment: str,
    status: str = "open",
    response: str = "—",
) -> str:
    """Format one async comment block."""
    return (
        f"### {comment_id} · status: {status}\n"
        f"- **Author:** {author}\n"
        f"- **Module/file:** {target}\n"
        f"- **Comment:** {comment.strip()}\n"
        f"- **Response:** {response.strip() or '—'}\n"
    )


def _next_comment_id(text: str) -> str:
    nums = [int(n) for n in re.findall(r"### C(\d+) ", text)]
    return f"C{(max(nums) + 1) if nums else 1:03d}"


def add_open_comment(
    author: str,
    target: str,
    comment: str,
    *,
    comment_id: str | None = None,
    status: str = "open",
    response: str = "—",
    base_dir: str | Path | None = None,
) -> str:
    """Append an async comment to OPEN_COMMENTS.md. Returns the comment id."""
    if status not in VALID_COMMENT_STATUSES:
        raise ValueError(f"status must be one of {VALID_COMMENT_STATUSES}, got {status!r}")
    path = _shared_dir(base_dir) / COMMENTS_FILE
    _ensure(path, "OPEN_COMMENTS", COMMENTS_MARKERS)
    text = _read(path)
    cid = comment_id or _next_comment_id(text)
    block = format_open_comment(cid, author, target, comment, status, response)
    _write(path, _insert_before(text, COMMENTS_MARKERS[1], block))
    return cid


def resolve_open_comment(
    comment_id: str,
    response: str,
    *,
    status: str = "resolved",
    base_dir: str | Path | None = None,
) -> bool:
    """Set a comment's status and response. Returns True if the comment was found."""
    if status not in VALID_COMMENT_STATUSES:
        raise ValueError(f"status must be one of {VALID_COMMENT_STATUSES}, got {status!r}")
    path = _shared_dir(base_dir) / COMMENTS_FILE
    text = _read(path)
    header_re = re.compile(rf"(?m)^### {re.escape(comment_id)} · status: \w+\s*$")
    m = header_re.search(text)
    if not m:
        return False
    rest = text[m.end() :]
    nxt = re.search(r"(?m)^### |<!-- OPEN_COMMENTS:END -->", rest)
    end = m.end() + (nxt.start() if nxt else len(rest))
    block = text[m.start() : end]
    block = header_re.sub(f"### {comment_id} · status: {status}", block, count=1)
    if re.search(r"(?m)^- \*\*Response:\*\*.*$", block):
        block = re.sub(
            r"(?m)^- \*\*Response:\*\*.*$", f"- **Response:** {response.strip()}", block, count=1
        )
    else:
        block = block.rstrip() + f"\n- **Response:** {response.strip()}\n"
    _write(path, text[: m.start()] + block + text[end:])
    return True


# --- active plan -----------------------------------------------------------


def _parse_active_plan(inner: str) -> dict[str, str]:
    current: dict[str, str] = {}
    for label in ACTIVE_PLAN_FIELDS.values():
        m = re.search(rf"(?m)^- \*\*{re.escape(label)}:\*\*\s*(.*)$", inner)
        if m:
            current[label] = m.group(1).strip()
    return current


def update_active_plan(
    *,
    milestone: str | None = None,
    task: str | None = None,
    files: str | Sequence[str] | None = None,
    acceptance: str | None = None,
    risks: str | None = None,
    last_touched: str | None = None,
    notes: str | None = None,
    base_dir: str | Path | None = None,
) -> None:
    """Update the managed block in ACTIVE_PLAN.md. Only provided fields change; others stay."""
    path = _shared_dir(base_dir) / ACTIVE_PLAN_FILE
    _ensure(path, "ACTIVE_PLAN", ACTIVE_PLAN_MARKERS)
    text = _read(path)
    current = _parse_active_plan(_between(text, *ACTIVE_PLAN_MARKERS))
    provided: dict[str, str | Sequence[str] | None] = {
        "milestone": milestone,
        "task": task,
        "files": files,
        "acceptance": acceptance,
        "risks": risks,
        "last_touched": last_touched,
        "notes": notes,
    }
    lines: list[str] = []
    for key, label in ACTIVE_PLAN_FIELDS.items():
        if provided[key] is not None:
            value = _join(provided[key]) if key == "files" else str(provided[key]).strip()
        else:
            value = current.get(label, "—")
        lines.append(f"- **{label}:** {value}")
    _write(path, _replace_between(text, *ACTIVE_PLAN_MARKERS, "\n".join(lines)))


# --- dashboard -------------------------------------------------------------


def build_team_sync_summary(*, base_dir: str | Path | None = None, max_handoffs: int = 3) -> str:
    """Compose the printable team-sync dashboard as plain text. Robust to missing files."""
    d = _shared_dir(base_dir)

    plan = (
        _between(_read(d / ACTIVE_PLAN_FILE), *ACTIVE_PLAN_MARKERS) or "(no active plan recorded)"
    )

    handoffs = _blocks(_between(_read(d / HANDOFF_FILE), *HANDOFF_MARKERS))
    latest = handoffs[-max_handoffs:] if handoffs else []
    handoff_text = "\n\n".join(latest) if latest else "(no handoff entries)"

    comments = _blocks(_between(_read(d / COMMENTS_FILE), *COMMENTS_MARKERS))
    open_comments = [b for b in comments if "status: open" in b.splitlines()[0]]
    comments_text = "\n\n".join(open_comments) if open_comments else "(no open comments)"

    blockers = _blocks(_between(_read(d / BLOCKERS_FILE), *BLOCKERS_MARKERS))
    blockers_text = "\n\n".join(blockers) if blockers else "(no blockers)"

    next_tasks = _section(_read(d / TEAM_SYNC_FILE), "Next recommended actions") or "(none listed)"

    sep = "=" * 64
    return "\n".join(
        [
            "GRIDQ — TEAM SYNC DASHBOARD",
            sep,
            "\n## Active plan",
            plan,
            f"\n## Latest handoffs (most recent {len(latest)})",
            handoff_text,
            "\n## Open comments",
            comments_text,
            "\n## Blockers",
            blockers_text,
            "\n## Next tasks",
            next_tasks,
            sep,
        ]
    )


# --- session briefing (printed at every session start) ---------------------


def _handoff_digest(base_dir: Path, *, limit: int) -> list[str]:
    """One attributed line per recent handoff: ``• <ts> — <who>: <summary>`` (newest last)."""
    blocks = _blocks(_between(_read(base_dir / HANDOFF_FILE), *HANDOFF_MARKERS))
    lines: list[str] = []
    for block in blocks[-limit:]:
        head = block.splitlines()[0].lstrip("# ").strip()  # "<ts> — <contributor>"
        m = re.search(r"(?m)^- \*\*Summary:\*\*\s*(.*)$", block)
        summary = m.group(1).strip() if m else "(no summary)"
        if len(summary) > 240:
            summary = summary[:237].rstrip() + "…"
        lines.append(f"• {head}: {summary}")
    return lines


def _open_headers(base_dir: Path, filename: str, markers: tuple[str, str]) -> list[str]:
    """Header lines of the ``### ...`` blocks in a marker-bounded file (open comments only)."""
    blocks = _blocks(_between(_read(base_dir / filename), *markers))
    if filename == COMMENTS_FILE:
        blocks = [b for b in blocks if "status: open" in b.splitlines()[0]]
    return [b.splitlines()[0].lstrip("# ").strip() for b in blocks]


def build_session_briefing(*, base_dir: str | Path | None = None, max_handoffs: int = 5) -> str:
    """The mandatory session-start briefing: who you are · what's been done (by whom) · what's next.

    Printed by the SessionStart hook so every session — whatever the prompt — opens by
    reconciling against the shared plan. Attribution comes from ``GRIDQ_CONTRIBUTOR``.
    """
    d = _shared_dir(base_dir)
    identity = current_contributor() or (
        "UNKNOWN — set GRIDQ_CONTRIBUTOR=Olly in .claude/settings.local.json"
    )

    done = _handoff_digest(d, limit=max_handoffs) or ["(no handoff entries yet)"]

    plan = _parse_active_plan(_between(_read(d / ACTIVE_PLAN_FILE), *ACTIVE_PLAN_MARKERS))
    current_task = plan.get(ACTIVE_PLAN_FIELDS["task"], "—")
    next_notes = plan.get(ACTIVE_PLAN_FIELDS["notes"], "—")
    next_actions = (
        _section(_read(d / TEAM_SYNC_FILE), "Next recommended actions") or "(none listed)"
    )
    open_comments = _open_headers(d, COMMENTS_FILE, COMMENTS_MARKERS) or ["(none open)"]
    blockers = _open_headers(d, BLOCKERS_FILE, BLOCKERS_MARKERS) or ["(none)"]

    sep = "=" * 70
    lines = [
        sep,
        "GRIDQ — SESSION BRIEFING  (team-sync · auto-injected at session start)",
        sep,
        f"Active contributor: {identity}",
        "",
        "DO THIS FIRST (whatever the prompt asks):",
        "  1. Show this briefing to the user.",
        "  2. Reconcile the request against the Active plan + Open items below; flag any clash.",
        "  3. Attribute every change you make to the active contributor above.",
        "  4. Before ending, log a HANDOFF_LOG entry (who/what/next) — see "
        "docs/shared_work/README.md.",
        "",
        f"WHAT'S BEEN DONE (most recent {len(done)}, by whom):",
        *(f"  {ln}" for ln in done),
        "",
        "WHAT NEEDS DOING NEXT:",
        f"  • Current task: {current_task}",
        f"  • Notes for the next person: {next_notes}",
        "  • Next recommended actions:",
        *(f"      {ln}" for ln in next_actions.splitlines()),
        "",
        "OPEN ITEMS TO RECONCILE:",
        "  Open comments:",
        *(f"    - {ln}" for ln in open_comments),
        "  Blockers:",
        *(f"    - {ln}" for ln in blockers),
        sep,
        "Full dashboard:  python -m gridq.dev.team_sync\n"
        'Log your work:   python -m gridq.dev.team_sync add-handoff --summary "..."',
        sep,
    ]
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    """CLI: ``dashboard`` (default) · ``briefing`` · ``whoami`` · ``add-handoff``.

    ``python -m gridq.dev.team_sync`` / ``gridq-team-sync`` print the dashboard;
    ``... briefing`` prints the session-start briefing (used by the SessionStart hook).
    """
    parser = argparse.ArgumentParser(prog="gridq-team-sync", description="GridQ team-sync.")
    sub = parser.add_subparsers(dest="mode")
    sub.add_parser("dashboard", help="print the full team-sync dashboard (default)")
    sub.add_parser("briefing", help="print the session-start briefing (who / done / next)")
    sub.add_parser("whoami", help="print the current contributor (from GRIDQ_CONTRIBUTOR)")
    ah = sub.add_parser("add-handoff", help="append a HANDOFF_LOG entry attributed to you")
    ah.add_argument("--summary", required=True, help="what you did this session")
    ah.add_argument("--contributor", default=None, help="defaults to GRIDQ_CONTRIBUTOR")
    ah.add_argument("--files", default=None, help="files changed (comma-separated)")
    ah.add_argument("--tests", default=None, help="tests run + result")
    ah.add_argument("--remaining", default=None, help="remaining tasks")
    ah.add_argument("--warnings", default=None, help="warnings for the next person")
    args = parser.parse_args(argv)

    mode = args.mode or "dashboard"
    if mode == "briefing":
        print(build_session_briefing())
    elif mode == "whoami":
        print(current_contributor() or "(GRIDQ_CONTRIBUTOR not set — set it to Olly)")
    elif mode == "add-handoff":
        who = resolve_contributor(args.contributor) or current_contributor()
        if not who:
            print(
                "refusing to log: pass --contributor or set GRIDQ_CONTRIBUTOR (Olly).",
                file=sys.stderr,
            )
            raise SystemExit(2)
        add_handoff_entry(
            who,
            args.summary,
            files_changed=args.files,
            tests_run=args.tests,
            remaining=args.remaining,
            warnings=args.warnings,
        )
        print(f"logged HANDOFF_LOG entry for {who}.")
    else:
        print(build_team_sync_summary())


if __name__ == "__main__":
    main()
