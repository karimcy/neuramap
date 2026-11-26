# Rejected SKILLS.md edits

Append-only. One row per **rejected** candidate edit and why — so the same speculative or bloated change isn't re-proposed. Newest first.

| date | proposed edit (op · target · gist) | reason for rejection |
|------|------------------------------------|----------------------|
| 2026-06-22 | insert_after · Testing and validation rules · run the local review gate (`python -m gridq.dev.review_gates` / `check_quality_gate()`) before a handoff | **Deferred, not rejected** — valid, evidence-backed (dev-quality lane built the gate; HANDOFF_LOG 2026-06-17) and procedural. Held over the ≤3-edits/cycle cap: this cycle's budget went to the higher-value 3→2 Assumption/provenance merge (finance+value-stack+scenarios) + the hook-enforced team-sync ritual. Re-propose next cycle. |
| 2026-06-22 | append ×2 · Testing rules (non-inventing loader invariant, proven by a test) + Common failure modes (structural-vs-pricing constant placement) | **Deferred, not rejected** — both valid and evidence-backed (ingestion v1, HANDOFF_LOG 2026-06-18; `test_loader_does_not_fill_gaps`/`_no_interpolation_by_default`/`_does_not_extrapolate`; constant decision OPEN_COMMENTS C013, `BASELINE_POWER_FRACTION` precedent C003). Two edits alone would consume most of a cycle, so held over the ≤3 cap; the loader invariant also overlaps the existing "no invented numbers" failure mode, so tighten/merge it when re-proposing. Re-propose next cycle. |
| 2026-06-18 | 1 edit(s): replace·the | edit could not be applied: replace span is not unique (39 matches): 'the' |
| 2026-06-18 | 1 edit(s): insert_after·NO SUCH ANCHOR ZZZ | edit could not be applied: insert_after anchor not found: 'NO SUCH ANCHOR ZZZ' |
| 2026-06-18 | 1 edit(s): replace·- The pricing path is deterministic and | edit touches the protected slow-update block (pass slow_update=True for a deliberate slow-update) or drops a slow-update marker |
| 2026-06-18 | 4 edit(s): append·—; append·—; append·—; append·— | too many edits: 4 > max 3 per patch |
| 2026-06-18 | 0 edit(s): | empty patch: no edits to apply |
| 2026-06-17 | append · Testing rules · "SolverStatus now has an insufficient_data value" | belongs-in-changelog/contracts — a fact about the enum, not a procedure; already in the changelog and (pending) 03_data_contracts.md. |
| 2026-06-17 | append · Common failure modes · "code_version may be 'unknown'/pkg fallback when not a git checkout" | speculative — not a repeated mistake; an implementation detail of `get_code_version`, documented in docs/provenance.md, not a procedure for SKILLS. |
| 2026-06-17 | append · Common failure modes · "hand-authored fixtures drift from strict models" | bloat — overlaps the accepted Testing rule ("generate fixtures from the models"); adds length, no new behaviour. |
| 2026-06-17 | append · Deterministic pricing rules · "connection capacities are mic_kw/mec_kw/asc_kw (kW not kVA); code is source of truth" | belongs-in-decisions — a fact/decision (tracked in OPEN_COMMENTS C002 / for decisions.md), not a procedure for SKILLS. |

<!-- Add rejected candidates above this line, newest first. Reason categories:
speculative (no evidence) · bloat (restates / adds length, no behaviour change) ·
belongs-in-changelog · belongs-in-decisions · touches-protected-block · fails-validation
-->
