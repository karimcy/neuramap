# Skill Update Protocol (SkillOpt — repo-local adaptation)

How `SKILLS.md` evolves safely. This is the GridQ-local adaptation of a SkillOpt-style loop: learn from real work, propose *small bounded* edits, validate, and keep only what helps. It is deliberately conservative — `SKILLS.md` must stay compact and procedural.

> No external services and no automation are required to run this; it is a checklist a Claude session (or a human) follows. The optional helper functions live in `src/gridq/dev/`.

## 1. Collect evidence
Gather signals from **completed work** since the last update:
- Finished tasks and what made them go smoothly or badly (`docs/shared_work/HANDOFF_LOG.md`, `ACTIVE_PLAN.md`).
- **Failed tests** and the root cause (what a rule could have prevented).
- **Code-review notes** (from the reviewer agents) and recurring findings.
- **Handoff comments** and `docs/shared_work/OPEN_COMMENTS.md`.
- **User corrections** — explicit "do it this way" / "don't do that" feedback.

## 2. Separate successes and failures
Split the evidence into:
- **Successes** — procedures that worked and are worth codifying so they repeat.
- **Failures** — mistakes that recurred or cost time, that a rule could prevent.
Discard one-off noise (a typo, a flaky run, a local-env glitch) — those are not skills.

## 3. Propose bounded edits
- **Maximum 3 edits per update.** Fewer is better.
- Allowed operations only: **`append`**, **`insert_after`** (anchored to an existing line/heading), **`replace`** (exact-match a span), **`delete`** (an existing span).
- Each candidate edit names: the operation, the target section/anchor, the new/old text, and the **evidence** that motivates it (which success or failure).
- **Never edit between** `<!-- SLOW_UPDATE_START -->` and `<!-- SLOW_UPDATE_END -->` here — that block changes only during an explicit *slow-update* (a separate, deliberate review, not this loop).

## 4. Validate the candidate `SKILLS.md`
Before accepting, run the candidate against [`validation_cases.md`](validation_cases.md). It must:
- still contain both slow-update markers, with the protected block unchanged (unless this is a slow-update);
- keep all required sections present;
- stay compact (procedural bullets, not prose; no changelog/history content);
- contain no secrets, no executable code, and no contradiction with `CLAUDE.md` or the foundational invariants.

## 5. Accept / reject
**Accept an edit only if** it does at least one of:
- improves clarity of an existing instruction, or
- reduces a *repeated* mistake (evidence shows it recurred), or
- improves a repo-specific procedure (more accurate to how GridQ actually works).

**Reject if** it is:
- speculative (no evidence it will help), or
- bloat (restates something already covered, or adds length without procedural value), or
- a fact/event better suited to `docs/changelog.md` or `docs/decisions.md`, or
- a change to the protected block outside a slow-update.

## 6. Record
- **Accepted** edits → append to [`edit_log.md`](edit_log.md): date, operation, target, one-line summary, and the evidence.
- **Rejected** edits → append to [`rejected_edits.md`](rejected_edits.md): date, the proposed edit, and the reason for rejection.
- If any required-section or marker check fails during validation, **do not write** the candidate — fix or drop the edit first.

## 7. Slow-update (rare, explicit)
Only on a deliberate decision (e.g. a foundational rule genuinely changed) may the protected block be edited. A slow-update is logged in `edit_log.md` marked `SLOW_UPDATE`, and should usually also be a `docs/decisions.md` entry.

---
**Cadence:** run this loop at natural checkpoints (after a milestone, after a cluster of related changes, or when a mistake repeats) — not on every commit. See [`meta_skill.md`](meta_skill.md).
