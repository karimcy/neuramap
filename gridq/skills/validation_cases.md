# validation_cases — checks a candidate SKILLS.md must pass

Run these against a proposed `SKILLS.md` **before** accepting any edit. If a `MUST` check fails, do not write the candidate — fix or drop the edit. Some of these are asserted by the test suite (`tests/test_team_sync.py`); the rest are a human/Claude review checklist.

## Structural (MUST)
- [ ] Contains **both** markers exactly: `<!-- SLOW_UPDATE_START -->` and `<!-- SLOW_UPDATE_END -->`.
- [ ] The text **between** the markers is unchanged vs the current `SKILLS.md` (unless this is an explicit slow-update).
- [ ] All required sections are present: Project mission · Non-negotiable rules · Build workflow · Collaboration workflow · Deterministic pricing rules · Assumption/provenance rules · Testing and validation rules · Documentation update rules · Common failure modes.

## Content quality (MUST)
- [ ] No secrets, credentials, tokens, or API keys.
- [ ] No executable code blocks (it is procedural prose/bullets, not a script).
- [ ] No changelog/event content ("on 2026-… we added …") — that belongs in `docs/changelog.md`.
- [ ] No contradiction with `CLAUDE.md` or the foundational invariants in the slow-update block.

## Compactness (SHOULD)
- [ ] Stays compact — bullets over paragraphs; an edit that only adds length without changing behaviour is rejected.
- [ ] Each section remains scannable (no section balloons into prose).

## Edit-discipline (MUST)
- [ ] ≤ 3 edits in this update.
- [ ] Each edit uses only `append` / `insert_after` / `replace` / `delete`.
- [ ] Each edit cites the evidence (a success or a repeated failure) that motivates it.

## Acceptance gate (MUST be at least one)
- [ ] Improves clarity of an existing instruction, **or**
- [ ] Reduces a repeated mistake (evidence shows recurrence), **or**
- [ ] Improves a repo-specific procedure (more accurate to how GridQ works).

If none of the acceptance-gate conditions holds → reject and log in `rejected_edits.md`.
