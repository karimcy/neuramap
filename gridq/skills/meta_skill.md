# meta_skill — maintaining the skills

The skill about keeping `SKILLS.md` useful. Read this before running an update.

## When to run the update loop
- After completing a milestone or a cluster of related changes.
- When the **same mistake repeats** (the strongest signal — a rule should now prevent it).
- When a user correction establishes a durable "do it this way" for this repo.
- **Not** on every commit, and not to record events (that's the changelog).

## Principles
- **Compact beats complete.** `SKILLS.md` is procedural memory, not a manual. If an addition doesn't change behaviour, leave it out.
- **Evidence over intuition.** Every accepted edit points to a real success or failure. No speculative rules.
- **Bounded.** ≤3 edits per update; prefer 1. Small, anchored, reversible.
- **Procedure, not history.** "How to act" stays here; "what happened" goes to `docs/changelog.md`; "why we chose X" goes to `docs/decisions.md`.
- **Protect the core.** Never touch the slow-update block in the normal loop.
- **Reject loudly.** Log rejected edits with the reason so the same bloat isn't re-proposed.

## Net effect
The loop should make `SKILLS.md` *more accurate and no longer* over time — fewer repeated mistakes, clearer procedure, same compact size. If it's growing without getting more useful, that's a smell: prune.

See [`skill_update_protocol.md`](skill_update_protocol.md) for the exact steps and [`validation_cases.md`](validation_cases.md) for the acceptance checks.
