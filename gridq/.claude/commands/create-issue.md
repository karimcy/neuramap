---
description: Turn a feature idea into a docs/issues/ build-issue file in the house format.
argument-hint: <feature idea>
---

Create a new build issue from this idea: **$ARGUMENTS**

## Steps
1. **Pick the number & slug.** List `docs/issues/`, take the next zero-padded number (e.g. `016`), and choose a short kebab-case slug. Filename: `docs/issues/<NNN>-<slug>.md`.
2. **Confirm the owner.** Decide Oli / Olly / Shared using `docs/module_ownership.md` and `docs/00_project_boundary.md`. If it might belong to Zara/Sim/Karim, run `/review-boundary $ARGUMENTS` first and stop if it's out of scope.
3. **Ground it.** Read the relevant parts of `docs/01_project_spec.md` (module map) and `docs/03_data_contracts.md` so requirements and acceptance criteria are grounded, not invented. Do **not** write application code — this is a planning doc; "Suggested files" lists paths only.

## File format (match the existing issues exactly)
```
# <NNN> · <Title>

**Owner:** <Olly|Shared>
**Module(s):** <e.g. M5 (+M9)>
**Milestone:** <Milestone N — ...>
**Status:** Not started

## Why this matters
<2–4 sentences, grounded in the spec/mission>

## Requirements
<concrete bullets, grounded in the module's MVP scope>

## Acceptance criteria
- [ ] <objectively checkable items>

## Dependencies
- <NNN-slug.md — what it provides>   (real issues only; "None (foundational)" if none)

## Out of scope (v1)
<from the module's "Later" line and the non-MVP exclusions>

## Suggested files to create or modify
- `path` — purpose
> Suggested only — no application code is created by this issue document.
```

After writing, report the filename and offer to run `/update-project-docs` to log it.
