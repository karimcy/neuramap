---
proposed_by: agent/assumptions
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation
insert_after   (one edit)

## Target
`## Assumption / provenance rules` — insert as a new bullet immediately after the
"**External forecasts are referenced, not stored as assumptions — and never invented.**" bullet.

## Proposed text
- **A `source` must quote the source's *actual* figure — a requirement is not a number.** When you populate an assumption, the `source` field has to point at where that *specific value* came from (document + the figure/quote, or publisher + URL + access date), precise enough that an output/frontend can show where the number originated. If a document only establishes that something must be modelled but states **no figure** (e.g. "round-trip losses are bought at the price faced" with no %), the value **stays `placeholder`** with a `source` that says so — never back-fill an industry-typical number and cite the doc as if it gave it. Carrying a `gb_v0_1` value forward into a new release is fine **only** if its `status` stays `placeholder`/`needs_review` (status, not the `source` string, gates the pipeline); log the gap in `docs/assumptions_to_source.md` with the authoritative source to consult. Releasing a *sourced* version (e.g. `gb_v1_0`) is a **new file**, never an in-place edit of a frozen one, and it confirms nothing unless a value is actually checked.

## Evidence
W4-3 (`gb_v1_0` provenance pass; `docs/changelog.md` 2026-06-24; decision **D19**). `gb_v0_1`
cited `round_trip_efficiency.ac_round_trip` (0.88) as `system_requirements (≈12% round-trip loss)`,
but `source_docs/System Requirements.html` states **no** round-trip figure — the ≈12% had been
invented and attributed to the doc. Caught and corrected only in the new file `gb_v1_0` (`gb_v0_1`
left frozen); the gap was logged to `docs/assumptions_to_source.md`. Raised **C020** (per-value
`source` not yet threaded to outputs/frontend).

## Notes
Procedural "how to act" when sourcing assumptions — it sharpens the existing "never invent" /
"forecasts referenced, not stored" rules with the concrete failure mode actually hit (a citation
that the cited document does not support) and the carry-forward/new-file discipline. The *facts*
(what was built, D19) live in `docs/changelog.md` / `docs/decisions.md`; this is the reusable rule,
so it belongs in `SKILLS.md`. One bounded edit; does not touch the protected slow-update block.
