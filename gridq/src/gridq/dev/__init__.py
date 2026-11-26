"""Developer utilities — collaboration/coordination + local quality tooling.

This package is **not part of the pricing path**: it touches no numbers a lender relies
on. It holds the cross-session plumbing that keeps parallel work coherent:

- ``team_sync`` — the shared-work Markdown files (handoffs, comments, plan, blockers) and
  a printable dashboard.
- ``review_gates`` — :func:`check_quality_gate`, a fast structural pre-flight check
  (governance scaffolding present, fixtures intact, no invented-number smell strings).
- ``skills`` — the non-LLM helpers for the SKILLS.md self-evolution loop (validate a
  candidate, guard the protected block, apply a bounded patch, log accept/reject).

Public functions are re-exported lazily (PEP 562) so that, e.g.,
``python -m gridq.dev.team_sync`` does not eagerly import the others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # for type-checkers only — no runtime import
    from gridq.dev.review_gates import check_quality_gate, scan_for_banned_strings
    from gridq.dev.skills import (
        append_rejected_edit,
        append_skill_edit_log,
        apply_patch_if_valid,
        check_protected_region,
        load_skill,
        validate_skill_structure,
    )
    from gridq.dev.team_sync import (
        add_handoff_entry,
        add_open_comment,
        build_team_sync_summary,
        resolve_open_comment,
        update_active_plan,
    )

# Map each lazily-exported name to the submodule that defines it.
_EXPORT_SOURCES: dict[str, str] = {
    # team_sync
    "add_handoff_entry": "team_sync",
    "add_open_comment": "team_sync",
    "resolve_open_comment": "team_sync",
    "update_active_plan": "team_sync",
    "build_team_sync_summary": "team_sync",
    # review_gates
    "check_quality_gate": "review_gates",
    "scan_for_banned_strings": "review_gates",
    # skills
    "load_skill": "skills",
    "validate_skill_structure": "skills",
    "check_protected_region": "skills",
    "apply_patch_if_valid": "skills",
    "append_skill_edit_log": "skills",
    "append_rejected_edit": "skills",
}

__all__ = [
    # team_sync
    "add_handoff_entry",
    "add_open_comment",
    "resolve_open_comment",
    "update_active_plan",
    "build_team_sync_summary",
    # review_gates
    "check_quality_gate",
    "scan_for_banned_strings",
    # skills
    "load_skill",
    "validate_skill_structure",
    "check_protected_region",
    "apply_patch_if_valid",
    "append_skill_edit_log",
    "append_rejected_edit",
]


def __getattr__(name: str) -> object:
    module_name = _EXPORT_SOURCES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"gridq.dev.{module_name}")
    return getattr(module, name)
