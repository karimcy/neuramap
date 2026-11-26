# The Goal Drive — production-release verification + north-star harmonisation

**Branch:** `agent/dispatch-mpc` · **Contributor:** Olly (goal session, directed by Olly) · **Dates:** 2026-07-02 (loops 01–02)
**Companion record:** [README_NORTH_STAR_DRIVE.md](README_NORTH_STAR_DRIVE.md) describes Karim's parallel drive on `karim/north-star-drive`; this branch **adopts** that drive's decisions (see §4) and was aligned to them mid-session on Olly's direction.

This README explains everything this branch contains, in the order it happened: the verification sweep (§2), the defects found and fixed (§3), the north-star adoption and what it changed (§4), the pre-merge critique of the adopted branch (§5), and the exact state a merger needs to know (§6). Ground truth for all of it lives in `docs/goal/` (STATE / FINDINGS / DECISIONS / loops) — this document is the narrative index.

---

## 1. What this branch is

A long-running goal session taking GridQ from "built and green" to **verified production release**: every module's manual-derived invariants made executable, every hollow capability either implemented behind versioned assumptions or honestly flagged, and the whole thing kept deterministic and `not_bankable`-on-placeholders throughout.

The method: extract testable invariants from the platform manual and docs (~350, in `docs/goal/spec/`), map them to existing tests, and close every gap with a numbered finding (F-001…) — evidence, fix, test — plus a numbered decision (D31–D33) for every design change.

## 2. The verification sweep (WS1 + WS3)

**Module × check matrix:** `docs/goal/STATE.md`. Status after loop 02:

| Verified | How |
|---|---|
| M0 ingestion | tz/DST invariants (`test_goal_invariants_hh_tz.py`, F-003) + the spec-extract gap rows: offenders named, derived-power labelled, annual never inferred from a partial trace, conditional wholesale ask, access-log hash-neutrality + fail-closed sink, ingestion-level honest degrade (`test_goal_invariants_m0.py`) |
| M1 schema | T0-annual required; exactly two documented negative exceptions; archetype smuggling rejected; dispatch-profile convention ratified (F-009) (`test_goal_invariants_modules.py`) |
| M2 value stack | by-stream sums exact at the finance AND outputs seams; excluded streams counted nowhere; gated joins only underwritten; no-GB-branch AST guard (platform file) |
| M3 dispatch | SoE gap segmentation (F-001/D31); min(MIC,ASC) cap; warranty cap; bill-reduction partition; parasitic headroom (F-007); tz/DST (F-003) |
| M4 sizing | two-stage continuous search built + determinism-pinned (D32) — **opt-in, see §4**; F-002 flagged open (C030) |
| M5/M9 finance | P90≤P50 with compounding degradation; the DSCR covenant gate now fireable in real mode (F-005/D33); real P90-DSCR sculpting with honest ANNUITY default (F-006/D33); degradation marginal + parasitic cost lines, release-gated |
| M6 scenarios | P90≤P50 element-wise through the engine and the weighted summary; warn-never-fake edge cases; forecast refs never set multipliers |
| X.1 assumptions | placeholder-never-bankable swept across EVERY shipped release; gb_v0_1 = Appendix B byte pins; off-zero moves always material |
| X.2 provenance | hashing raises on unhandled types; a record with no assumption-version stamp can never present bankable (F-010) |
| X.3 telemetry | no priced number can enter an event (vocabulary + leak scan + smuggle rejection); order-independent aggregation |
| M8 outputs / CLI | double-render byte-identity; no arithmetic in templates; covenant `None` renders "not evaluated"; unknown assumption version = fatal exit 2 |
| WS3 pipeline | sizing-vs-underwriting fingerprint identity (F-008); T2 CLI run persists the sizing surface end to end; T0 degrades honestly |

Remaining: the `app` row rides with the UI-overhaul workstream (WS4, not started).

## 3. Findings (all in `docs/goal/FINDINGS.md`, F-numbered with evidence + fix + test)

- **F-001 · HIGH · FIXED (D31)** — the dispatch MILP chained state-of-energy across metered gaps (~45 MWh/yr phantom discharge on a crafted series). Now: per-segment SoE recurrence with per-segment cyclic wrap; gapless data byte-identical.
- **F-002 · MEDIUM · OPEN (C030)** — duration-keyed floor lookup is anti-conservative for warranty cycle caps (the table *decreases* with duration). Fix built, then **withdrawn in the north-star adoption** (their D28 keeps flooring); flagged for the joint decision with a proposed no-invention alternative (conservative-neighbour lookup).
- **F-003 · MEDIUM · FIXED** — naive/UTC/mixed-offset timestamps now parse deterministically; Europe/London banding for tz-aware series (a UTC-labelled summer series previously mis-placed the DUoS red band by an hour for ~7 months/yr).
- **F-005 · HIGH · FIXED (D33)** — the P90-DSCR covenant gate could NEVER fire in real mode (no release carried gearing). gb_v1_2 adds labelled placeholder gearing + the D19 credit-policy fold-in; the gate genuinely admits/rejects; everything stays not bankable.
- **F-006 · MEDIUM · FIXED (D33)** — `sculpting_basis="P90_DSCR"` was decorative. Default flipped to the honest `ANNUITY`; P90-DSCR sculpting implemented for real (closed form, honest fallback, applied basis recorded).
- **F-007 · MEDIUM · FIXED** — parasitic (auxiliary) load existed nowhere: dispatch now reserves connection headroom for it and finance prices it, both release-gated on gb_v1_2.
- **F-008 · HIGH · FIXED** — the sizing-vs-underwriting `input_hash` identity silently broke on a `model_version` stamp mismatch; both call sites now prefer the solver's stable `MODEL_VERSION`. (This is the only reason the demo golden moved — one deliberate re-pin, lineage in the test comment.)
- **F-009 · LOW · ratified-amended-doc** — manual ch03 claimed a signed dispatch series; the frozen contract stores two non-negative series. Schema ratified; manual amended.
- **F-010 · MEDIUM · FIXED** — a claimed-bankable record with **no assumption-version stamp** presented as bankable in outputs; now downgraded with a warning ("a quote is valid only against the assumption version that priced it").

~90 new invariant tests across `tests/test_goal_invariants_*.py`, `test_two_stage_sizing.py`, `test_sizing_search_components.py`, `test_goal_ws3_pipeline.py`.

## 4. The north-star adoption (what changed mid-session and why)

Karim's `karim/north-star-drive` (adopted product goal + Waves 6–9, committed 2026-07-02) was declared **authoritative** by Olly mid-loop. Both lanes had, unknowingly, spent the same day in the same territory: two continuous sizing searches, two `gb_v1_1` releases, and the same D27–D29 decision numbers claimed for different decisions. This branch yielded on every collision:

1. **Decision numbers:** goal decisions renumbered **D31 (gap segmentation) / D32 (two-stage search) / D33 (finance capabilities)**; the north-star D27–D30 are imported verbatim into `docs/decisions.md`. Comments citing their numbers always say "north-star D2x".
2. **The C025 gate (their D29) is enforced:** on per-MW-only capex, ANY wide-duration search pins its recommendation to the band edge — a placeholder artifact, not an optimum. The two-stage search is therefore **opt-in** (`MilpEngine(use_two_stage_search=True)`); the engine default and the customer-visible demo stay on the legacy 3×3 grid; the transient two-stage golden pin was withdrawn. Post-C025 (capex gains a sourced £/kWh component), the two-stage search enters their Wave-6 bake-off as a fourth method — its typical-day proxy is ~15× faster per evaluation and its confirm stage guarantees no proxy number is ever reported.
3. **Their D28 adopted (duration flooring kept):** the warranty interpolation fix was withdrawn (`duration_policy.py` deleted); the anti-conservative direction it fixed remains flagged (F-002 / **C030**) with a proposed resolution that invents nothing.
4. **Release renamed gb_v1_1 → gb_v1_2:** their Wave-8 `gb_v1_1` (availability/penalties/tnuos under D30 optional sections) owns the name. `gb_v1_2` (gearing, credit fold-in, degradation marginal, parasitic) must be **re-cut as a superset of their gb_v1_1 at merge** — recorded in the file header.
5. `docs/north_star.md` is now in this tree and is the session-start reconciliation reference it declares itself to be.

## 5. The pre-merge critique of the adopted branch (Olly's instruction: adopt, but review)

Three independent read-only review lenses were run over `b13ec56`. Full record: **`docs/reviews/2026-07-02-north-star-adoption-review.md`**, filed as **C031**; their C025–C029 are imported into `OPEN_COMMENTS.md` with Olly's responses. Headlines (none block adoption; the first three should block those streams *pricing* anything before fixes):

- **W8 finance:** the perfect-foresight deduction keys on stream id but subtracts from the FIRM base — a contracted-firmness arbitrage stream makes **P90 exceed P50**; no branch test pins P90≤P50 element-wise.
- **W7 solver:** committed kW is reserved against rated power while discharge is bounded by site load (undeliverable commitments pass and price); overlapping commitments bind at max, not sum (**double-sold kW**); the CM valuation prints "capability-checked" even when the deliverability verdict FAILED.
- **W7 tz:** commitment windows and the branch's DUoS calendar map on the raw timestamp clock — one hour wrong all summer on tz-aware series (the same bug class F-003 just fixed).
- **W6:** their own continuous search exploits the warranty flooring bug (C030); "step-aware" holds only in the favourable-above-key direction; the 0.5 % certification tolerance is recorded, not tested.
- Plus a **10-item semantic merge-collision register** (§4 of the review doc) any merger must work through — including two ImportError-on-merge cases and an honesty regression (their `assess_debt` rebuild drops the applied `sculpting_basis`).

## 6. The merge (landed on this branch, same day)

`karim/north-star-drive` @ b13ec56 **is now merged into this branch**, worked through the collision register (review doc §4). What the merge commit did beyond textual resolution:

- **Both additive QuoteRecord fields kept** (`scenario_sensitivities` + `customer_bill`); the record-persisted sensitivities renderer supersedes the branch's generation-time block, and `generate_artifacts` no longer shadows record rows with library-computed ones (precedence: explicit caller > record-persisted > library-computed).
- **The bill hook rides the split-solver architecture:** under `use_mpc=True` the MPC solver has no bill hook, so `customer_bill` honestly stays `None` (the documented pending seam) — never a bill from the wrong solve. MPC runs with commitment payloads now carry a loud "commitments NOT compiled in MPC mode" warning.
- **Commitment windows are tz-correct:** `_commitment_mask` converts tz-aware series to Europe/London exactly like the DUoS calendar (closes adoption-review W7-3); the availability reservation nets off the parasitic draw (F-007); distinct commitments colliding on a sanitised constraint name are uniquified + warned, never dropped; the infeasible-solve path keeps its compile warnings + the HiGHS termination string.
- **Honesty guards from the critique:** the P90 > P50 inversion mechanism is clamped + warned (perfect-foresight deduction can no longer push the FIRM base negative — W8-1); the CM "capability-checked" note is printed only when the deliverability verdict genuinely confirmed (W7-4); their negative-merchant-haircut hole is covered by this lane's clamp.
- **Fingerprint + provenance conventions unified:** `ContinuousSizingEvaluator` prefers the solver's `MODEL_VERSION` (F-008); their dense/adaptive/PSO surfaces persist as `search_version="w6-<method>-v1"`, never mislabelled as the legacy grid; weighted (typical-period) solves weight the bill and tender-window aggregates like every other annualised figure.
- **`gb_v1_2` re-cut as a strict superset of their `gb_v1_1`** (availability/penalties/tnuos carried forward verbatim) — the W8 risk shapes read identically on both; the golden re-pinned once for the persisted demo bill (selection unchanged; lineage in the test comment).
- **Still open (joint decisions, not merge scope):** C030 (warranty lookup direction), the W7 constraint-shape amendments in the C026 response (undeliverable-vs-load, overlap aggregation), C025 (capex £/kWh — resolves in the next release), C027–C029.
- **Next work on this lane:** WS4 UI overhaul (surface consumption follows the merged production shape — their D29 makes the dense exact grid the persisted surface); the remaining `app` STATE row rides with it.

## 7. Verifying this branch

```bash
# quality bar (thread-pinned; this box swap-thrashes otherwise)
TZ=UTC OMP_NUM_THREADS=1 python -m pytest -q
ruff check . && ruff format --check . && mypy
python -m gridq.dev.review_gates

# the goal invariants specifically
TZ=UTC OMP_NUM_THREADS=1 python -m pytest -q tests/test_goal_invariants_*.py \
    tests/test_two_stage_sizing.py tests/test_sizing_search_components.py \
    tests/test_goal_ws3_pipeline.py

# the opt-in two-stage cloud (the C025-gated path)
python -c "
from gridq.optimisation.engine import MilpEngine
print(MilpEngine(use_two_stage_search=True))"
```

Every number on this branch remains deterministic, traceable, and `not_bankable` on placeholder assumptions — the goal session changed what is *verified*, never what is *claimed*.
