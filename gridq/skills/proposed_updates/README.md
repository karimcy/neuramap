# skills/proposed_updates/ — the SKILLS.md proposal inbox

Under parallel development (see [`docs/parallel_development_plan.md`](../../docs/parallel_development_plan.md)), **only the designated skills-maintainer edits [`SKILLS.md`](../../SKILLS.md) directly.** Every other lane proposes changes here instead. This keeps the most-read file in the repo coherent and free of merge races across eight branches.

## How to propose a SKILLS.md edit

1. Add **one file per proposal** to this directory: `YYYY-MM-DD-<short-slug>.md` (e.g. `2026-06-18-finance-determinism-note.md`).
2. Use the format below. Ground every proposal in **evidence from real work** — a finished task, a failed test, a recurring review finding, a handoff comment, or an explicit user correction. Speculative or "nice to have" edits are rejected (per the protocol).
3. Leave it. The **skills-maintainer** picks it up, runs the [skill update protocol](../skill_update_protocol.md) (validate → accept/reject), and records the outcome in [`edit_log.md`](../edit_log.md) (accepted) or [`rejected_edits.md`](../rejected_edits.md) (rejected). Applied/closed proposals are removed from this inbox.

## Proposal template

```markdown
---
proposed_by: <branch / agent, e.g. agent/finance>
date: 2026-06-18
status: proposed   # proposed | accepted | rejected
---

## Operation
append | insert_after | replace | delete   (one only; max 3 edits per update)

## Target
Section / anchor in SKILLS.md (e.g. "## Testing and validation rules", or the exact line to anchor after).

## Proposed text
<the new text to append/insert, or the exact span to replace/delete>

## Evidence
Which real success or failure motivates this (link the HANDOFF_LOG entry, failed test,
OPEN_COMMENTS id, review finding, or user correction). No evidence → rejected.

## Notes
Why this belongs in SKILLS.md (procedural "how to act") and not in docs/changelog.md
or docs/decisions.md (facts/events).
```

## Rules

- **Never propose edits to the protected block** (`<!-- SLOW_UPDATE_START/END -->`). Those invariants change only via an explicit slow-update, not through this inbox.
- Keep proposals **bounded and procedural** — `SKILLS.md` stays compact. A fact about *what happened* goes to `docs/changelog.md`; a decision goes to `docs/decisions.md`; only *how to act* belongs in `SKILLS.md`.
- Maximum **3 edits accepted per update cycle** — propose the highest-value change, not everything.
