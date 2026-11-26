# Review gates & skill-update helpers

Local, dependency-light checks the `agent/dev-quality` lane provides so a session can
verify its work *before* a handoff or merge. They live in `src/gridq/dev/` and touch **no
numbers a lender relies on** — they only inspect the repo's structure and text, and run
the mechanical (non-LLM) parts of the skill-update protocol.

There are two pieces:

1. **`review_gates`** — `check_quality_gate()`, a fast structural pre-flight.
2. **`skills`** — non-LLM helpers for the `SKILLS.md` self-evolution loop.

> These do **not** replace the full quality bar (`pytest` / `ruff` / `mypy` /
> `ruff format` — see [`CLAUDE.md` §6](../CLAUDE.md) and [`/run-quality`](../.claude/commands/run-quality.md)).
> They check the *structural* invariants those tools don't, and are a useful first
> screen in the parallel-work [definition of done](parallel_development_plan.md#6-definition-of-done-per-lane-before-merge).

---

## 1. The quality gate — `check_quality_gate()`

```python
from gridq.dev.review_gates import check_quality_gate

report = check_quality_gate()        # defaults to this repo
print(report.summary())              # one PASS/WARN/FAIL line per check
assert report.passed                 # True iff no error-severity check failed
```

Or from the shell (prints the report; exits non-zero if a blocking check failed):

```bash
python -m gridq.dev.review_gates      # or: gridq-review-gates
```

### What it checks

| Check | Severity | Why |
|-------|----------|-----|
| `pytest` importable | warn | the quality bar can't run without it; a missing tool is noted, never fatal (matches `/run-quality`) |
| `SKILLS.md` exists | error | the procedural memory the whole repo reads |
| protected slow-update markers present + ordered | error | the foundational-invariants block must stay guarded ([SKILLS.md](../SKILLS.md)) |
| `skills/edit_log.md` exists | error | accepted-edit audit trail for the skill-update loop |
| `skills/rejected_edits.md` exists | error | rejected-edit trail (so the same bloat isn't re-proposed) |
| `skills/skill_update_protocol.md` exists | error | the protocol itself |
| `docs/shared_work/*` exist | error | the live coordination surface (TEAM_SYNC / ACTIVE_PLAN / HANDOFF_LOG / OPEN_COMMENTS / BLOCKERS) |
| assumption library file(s) under `assumptions/` | error | every number reads from a versioned library; if it's gone, nothing can price honestly |
| example input file(s) under `examples/` | error | the model-generated fixtures the pipeline/tests read |
| no banned smell strings in core modules | error | guards the failure modes in [SKILLS.md](../SKILLS.md) — invented numbers / spoofed bankable output |

**Severity rule:** only `error`-severity failures fail the gate (`report.passed`). A
missing dev tool is a `warn` and never blocks — same stance as `/run-quality` and the
`quality-regression-checker`.

### The banned-string scan

`scan_for_banned_strings(root)` walks the **core** `*.py` files under `src/gridq/`
(excluding the `dev/` tooling package, which defines the patterns and would otherwise
self-match) and flags any case-insensitive occurrence of the smell strings in
`review_gates.BANNED_STRINGS` — currently `"fake bankable"`, `"hallucinated revenue"`,
`"todo invent"`. It returns `(file, line, pattern)` hits. It is a deliberately blunt
detector for the "invented number / spoofed bankable output" failure mode, **not** a
parser — keep those literal phrases out of core code (put them in `dev/` or tests).

### Pointing it at another tree

`check_quality_gate(repo_root=...)` and `scan_for_banned_strings(root=...)` both accept a
root, which is how the tests exercise each failure mode against a synthetic skeleton in
`tmp_path`.

---

## 2. Skill-update helpers — `gridq.dev.skills`

The deterministic, **non-LLM** rails for [`skills/skill_update_protocol.md`](../skills/skill_update_protocol.md).
The *judgement* (is an edit worth making? what is the evidence?) stays with the
human/agent running the loop; this module only enforces the safety rails and writes the
logs. Under parallel work, only the designated **skills-maintainer** (the `agent/dev-quality`
lane) applies `SKILLS.md` edits — every other lane proposes via
[`skills/proposed_updates/`](../skills/proposed_updates/) (see
[parallel_development_plan.md §4](parallel_development_plan.md#4-skills-governance-under-parallelism)).

| Function | Purpose |
|----------|---------|
| `load_skill(path=None)` | read the SKILLS.md text (defaults to the repo file) |
| `validate_skill_structure(text, *, slow_update=False, max_chars=12000, allow_long=False)` | check a *candidate*: both markers present + ordered, every required heading present, no fenced code block, compact (unless `allow_long`) |
| `check_protected_region(original, candidate, *, slow_update=False)` | True iff the candidate respects the slow-update block — byte-identical between markers unless `slow_update=True`; markers must always survive |
| `append_skill_edit_log(op, target, summary, evidence, *, type_="normal", date=None)` | append one **accepted**-edit row to `skills/edit_log.md` (newest first) |
| `append_rejected_edit(proposed, reason, *, date=None)` | append one **rejected**-edit row to `skills/rejected_edits.md` |
| `apply_patch_if_valid(patch, ...)` | validate a bounded patch against the rails and apply it iff it passes |

### Patch shape & operations

A *patch* is `{"edits": [...], "slow_update": bool, "allow_long": bool, "evidence": str}`
(or a bare list of edits). Each edit is one operation:

```python
{"op": "append",       "text": "...new block..."}                       # add to end of file
{"op": "insert_after", "anchor": "## Some heading", "text": "..."}        # after first line containing anchor
{"op": "replace",      "old": "<exact unique span>", "new": "..."}        # exact-match replace
{"op": "delete",       "old": "<exact unique span>"}                      # remove an exact span
```

`insert_after`/`replace`/`delete` reject (don't crash) when the anchor/span is missing or
not unique — so a patch never silently edits the wrong place. Each edit may carry its own
`summary` / `evidence` for the log; otherwise the patch-level `evidence` is used.

### Safety rails (the order they fire)

1. **≤ 3 edits per patch.** More → rejected (`MAX_EDITS_PER_PATCH`).
2. **Mechanically applicable.** A bad anchor/span/op → rejected.
3. **Protected block.** If the edit would change the text between
   `<!-- SLOW_UPDATE_START -->` / `<!-- SLOW_UPDATE_END -->`, or drop a marker, it is
   rejected **unless** the patch passes `slow_update=True` (a deliberate slow-update,
   logged with `type=SLOW_UPDATE`).
4. **Structure + compactness.** The candidate must keep every required heading and stay
   under `MAX_SKILL_CHARS` (12 KB) unless `allow_long=True`.

On **accept**: the new `SKILLS.md` is written and one row per edit is appended to
`edit_log.md`. On **reject**: nothing is written to `SKILLS.md`, and one row with the
reason is appended to `rejected_edits.md`. The result is a `PatchResult(accepted, reason,
candidate, edits_applied)`.

```python
from gridq.dev.skills import apply_patch_if_valid

result = apply_patch_if_valid({
    "edits": [{
        "op": "insert_after",
        "anchor": "## Common failure modes",
        "text": "- A new, evidence-backed procedural bullet.",
        "summary": "codify a recurring lesson",
        "evidence": "HANDOFF_LOG 2026-06-17 — repeated review finding",
    }],
})
print(result.accepted, result.reason)
```

See [`skills/validation_cases.md`](../skills/validation_cases.md) for the full acceptance
checklist these helpers mechanise.
