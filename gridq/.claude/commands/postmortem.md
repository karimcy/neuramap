---
description: Review a failed or messy build step; record only permanent lessons into decisions.md (and CLAUDE.md if it's an operating rule).
argument-hint: <what went wrong / which step>
---

Review this failed or messy step: **$ARGUMENTS** (use the recent conversation/diff for context).

## 1. Write up what happened
Concise and blameless:
- **What happened** — the symptom and where it surfaced.
- **Root cause** — the actual underlying reason, not the first symptom.
- **Recovery** — what was changed to get unstuck.

## 2. Decide if the lesson is permanent
Ask: *will this recur, and should it change how we work?*
- **One-off / transient** (a typo, a flaky run, a local environment glitch): record **nothing** durable. Say so explicitly and stop.
- **Permanent** (a design constraint, a recurring trap, a process gap): capture it — minimally.

## 3. Record permanent lessons only
- Add a dated entry to **`docs/decisions.md`** in the house format: **date · decision · reason · alternatives considered · consequences**. (A "lesson" is a decision about how we'll work or build from now on.)
- **Only if it is a standing operating rule** that belongs in the agent's working contract, add **one concise line** to `CLAUDE.md` (§2 Working style or the relevant section). Do **not** bloat `CLAUDE.md` — most lessons live in `decisions.md`, not here.

Report what you recorded (or that nothing durable was warranted) and why.
