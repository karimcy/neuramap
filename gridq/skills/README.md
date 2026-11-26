# skills/ — self-evolving procedural memory

This directory governs how the root [`SKILLS.md`](../SKILLS.md) — the project's active procedural memory — is maintained and improved over time, without letting it bloat or drift.

## The idea

`SKILLS.md` tells future Claude sessions *how to work in this repo*. It should get **better** as we learn, but stay **compact** and **trustworthy**. So changes to it are governed: bounded, evidence-based, validated, and logged.

## Files

| File | Role |
|------|------|
| [`skill_update_protocol.md`](skill_update_protocol.md) | The protocol (SkillOpt, repo-local): collect evidence → split successes/failures → propose ≤3 bounded edits → validate → accept/reject → record. |
| [`meta_skill.md`](meta_skill.md) | The skill *about* maintaining skills — when to run the loop, principles, how to avoid bloat. |
| [`validation_cases.md`](validation_cases.md) | The checks a candidate `SKILLS.md` must pass before acceptance (markers present, sections present, compact, no secrets/code). |
| [`edit_log.md`](edit_log.md) | Append-only log of **accepted** edits to `SKILLS.md` (date, op, target, summary, evidence). |
| [`rejected_edits.md`](rejected_edits.md) | Append-only log of **rejected** candidate edits and why. |

## How to use it

When you finish a chunk of work (or a mistake repeats), run the protocol in `skill_update_protocol.md`. Keep edits tiny. Record what you accept and what you reject — the rejection log is as valuable as the edit log, because it stops the same speculative change being re-proposed.

The protected block in `SKILLS.md` (between `<!-- SLOW_UPDATE_START -->` and `<!-- SLOW_UPDATE_END -->`) is **off-limits** to the normal loop and changes only in an explicit slow-update.

> This is governance, not automation. No external services. Optional helpers for the *collaboration* docs live in `src/gridq/dev/team_sync.py`.
