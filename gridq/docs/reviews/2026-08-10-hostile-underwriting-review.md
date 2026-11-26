# Hostile review of the underwriting pipeline — Tier A / Tier B + remediation plan

**Method.** Nine hostile lenses were run by Fable 5 agents against a *sandboxed copy* of the
underwriting pipeline only — 20,256 lines of pipeline source plus `gb_v1_6.yaml`, with **no** README,
no `CLAUDE.md`, no `docs/`, no tests, no git history, and no descriptive project material. The
agents were instructed to treat docstrings and comments as marketing copy rather than evidence, and
to cite `file:line` for every claim. This isolation was deliberate: a reviewer who can read the
project's own self-description tends to ratify its framing.

**Status and honesty note.** Six of nine lenses completed and returned **54 findings**. Three lenses
(**orchestration, sizing, finance**) and the entire **adversarial refutation panel** were lost to a
session usage limit. Consequences, stated plainly:

- **52 of the 54 findings are UNVERIFIED** by an independent refuter. They are the product of one
  competent hostile pass, not of a confirm-and-refute cycle.
- **2 findings I re-verified myself against source** before writing this: F-01 and F-02 below. Both
  **CONFIRMED**.
- **The finance lens never ran.** Finance is the highest-suspicion area in this domain. What
  coverage exists is my own (§6), and it is thinner than a dedicated lens would have been.

Nothing in this document should be treated as adjudicated until the refutation pass is re-run
(work package WP-0).

Author: Olly · Date: 2026-08-10 · Sandbox: pipeline-only copy · Reviewers: Fable 5 (attack), Opus 5
(verification + synthesis)

---

## 1. Verdict

**No. These numbers cannot be shown to an investor today.** Not because any single line is wrong,
but because the priced floor — the revenue figure that debt is sized against and that both IRRs are
computed from — is **systematically overstated by construction** through at least four independent
mechanisms, and the labels that are supposed to tell a reader how much to trust a case
(`data_tier`, `bankable_status`, `input_hash`) **do not change any number**.

The single worst thing is this: **on a flat or unrecognised tariff, the dispatch MILP prices
charging energy at £0.** It will therefore cycle the battery every day, book the full DUoS red-band
avoidance as FIRM floor revenue, and never pay for the energy or the round-trip losses. On the
worked example this fabricates ~£16,100/yr of a reported £16,960/yr — **95% of the stream, and it
flips the sign of the business case.** Knowl Hill has a flat 15.41 p/kWh tariff with no night rate.
Harwell has no tariff at all.

---

## 2. What the pipeline actually does

`cli.py run` → load + validate `SiteModel` → optionally fold in an HH CSV and re-grade the data tier
→ `MilpEngine.build_optimisation_request` → sizing search enumerates power×duration candidates →
each candidate evaluated by a half-hourly dispatch LP/MILP (linopy/HiGHS) over the metered year →
schedule converted to money per stream → value-stack gating assigns firmness → `run_financial_model`
builds cashflows, sizes debt, computes DSCR/P50/P90/IRRs → `assemble_quote_record` stamps provenance
→ artifacts rendered.

The dispatch model is genuinely a real MILP: `soe_t = soe_{t-1} + charge_t·RTE·Δt − discharge_t·Δt`
with cyclic wrap, per-segment SoE recurrence across metered gaps, connection bounds
`charge ≤ min(MIC,ASC) − load − aux`, and `discharge ≤ load`. Its objective maximises
`Σ price_t·(discharge_t − charge_t)·Δt`. That much is sound and is not in dispute.

The defects are almost entirely in **what price is fed to that objective**, **what is done with the
answer afterwards**, and **what is allowed through the door in the first place**.

---

## 3. Structural findings — five, and they set the order of everything else

### S-1 · The reported priced floor is not the MILP's optimum, and exceeds it

`optimisation/schedule.py:140,145` computes each revenue component with an **independent** zero
clamp:

```python
arbitrage_value_gbp   = round(max(0.0, self._net_value(energy_price)), 2)
duos_avoidance_value_gbp = round(max(0.0, self._net_value(duos_rate)), 2)
```

The MILP maximised the **sum**. Where the optimum buys energy at a loss to capture a larger DUoS
spread — exactly the behaviour a DUoS-driven site should exhibit — the arbitrage component is
negative and is clamped to zero, so the reported floor is `0 + duos` against a true objective of
`arbitrage + duos`. **Reported floor revenue can exceed the LP's own optimal objective value.**

This is not speculative. The same module already contains the honest, unclamped
`priced_floor_terms_gbp` (`schedule.py:150-164`) and its docstring states the per-component values
are "each clamped at £0" and that the true signed terms are needed so a negative window "nets
honestly". The correct computation exists and is used **only** for the export upside delta — not for
the floor handed to finance. *(Findings 14, 23, 24. **CONFIRMED by me against source.**)*

### S-2 · Consent is treated as delivery

A tick-box on an intake form plus an `agreed_rate` is sufficient to move Balancing Mechanism and
DC/DM/DR frequency-response revenue into the **CONTRACTED floor that debt is sized against**, with:

- no check that the asset can physically deliver the service while running the modelled dispatch,
- no state-of-charge headroom reserved for it,
- no reduction in the arbitrage revenue earned from the same MWh,
- no export-rights check (a site with `MEC=0` can book BM revenue),
- no mutual-exclusivity constraint between services competing for the same MW.

The same de-rated MW can be sold simultaneously to the Capacity Market, the BM and ancillary
services — **up to three times** — and BM/ancillary volumes borrow the CM de-rating table, which has
no contractual basis for them. *(Findings 2, 12, 13, 21, 29, 30.)*

This is the defect the literature calls the value-stacking fallacy, and it is the reason the
mutual-exclusivity matrix in the requirements checklist is the highest-value artefact of the
literature survey.

### S-3 · The data-quality labels are cosmetic

`ingestion/data_tier.py` grades T0/T1/T2, and **no number anywhere depends on the grade**. A quote
built from a two-interval window (`T1_MIN_PERIODS = 2`) carries the same headline IRR, DSCR and
revenue stack as one built from twelve validated months — separated only by warning strings the
numbers do not reflect. Compounding it:

- T2 "completeness" counts **timestamps, not usable readings**: a series 40% full of null imports
  still grades T2 (finding 8).
- `annual_factor` (`milp_solver.py:477`) extrapolates **any** coverage to a full year with no
  minimum-coverage gate and no seasonality correction — a winter month scales ×13 (findings 40, 43).
- `quality_flag` is parsed at the door and read by nothing: supplier-**estimated** readings are
  priced as actual (finding 25).
- Declared `annual_kwh` is never reconciled against the measured HH total — which is never even
  computed (finding 26).
- The one tier-based gate in the codebase (`schemas/quote.py:88`) is vacuous and can never fire
  (findings 27, 37).

**This is the same class of error as the Grundon four-month-sum defect** documented in
`grundon_harwell/02_data_reconciliation.md`. The pipeline has no mechanism that would have caught it.

### S-4 · Provenance is nominal, and fails in the dangerous direction

- The assumption library is pinned by **version string only** (`quotes/assembly.py:90`). An in-place
  edit to `gb_v1_6.yaml` is invisible; two contradictory quotes can both be stamped `gb_v1_6`, and
  the reprice baseline is re-read from the mutable file (finding 4).
- **`input_hash` never pins the half-hourly series content** — only its path string
  (`quotes/assembly.py:84`). The primary input of the entire case is unpinned, while the
  gated-upside forecast is pinned more strongly (finding 10).
- `FinancialCase` identity omits `debt_terms` and `price_structure`, so different cases collide on
  one `case_id` (finding 16).

The claim "every output number is traceable to its input, its assumption version and its code
version" is **not currently true of the metered data**.

### S-5 · Export is modelled incoherently, and it decides Bishops Cleeve

The base MILP forbids export (`discharge_ub = min(power, load)`, `milp_solver.py:519`). A separate
overlay adds contracted wholesale export at **gross** `tariff × MWh` into the priced floor
(`engine.py:687`) with **no charging cost deducted** and **no reduction of the arbitrage revenue**
from the no-export dispatch it is added to — then reports the same MWh again as gated upside. The
result presented as a contracted floor is the physically impossible sum *"full no-export arbitrage +
full export volume"* (findings 3, 6).

Separately, and independently of that defect: the blanket rule that export is *always* gated upside
is the wrong shape. **Bishops Cleeve exports ~6.7 GWh/yr from LFG gensets and consumes little** — its
entire investment thesis is shifting and firming that export. Under `discharge ≤ site_load` it
underwrites to approximately zero, which is a modelling artefact, not a result. `portfolio_requirements/sites.py`
opens by declaring the load-capped model "**the wrong model**"; the MILP still implements it.

---

## 3a. Fix log (updated as work lands)

| ID | Status | What changed | Pinned by |
|---|---|---|---|
| **F-01** | ✅ **FIXED** | `tariff_calendar.energy_price_series` returns a **constant array** for a flat tariff instead of `None`, so charge energy is costed and round-trip losses are paid for; `milp_solver` now **refuses** (`INSUFFICIENT_DATA`) when the price is genuinely unknown, instead of substituting zeros | `test_optimisation_tariff_calendar.py` (2 new tests incl. the arithmetic of a losing cycle) |
| **F-02** | ✅ **FIXED** | Per-component zero clamps removed; `arbitrage_value_gbp` / `duos_avoidance_value_gbp` are **signed**, and a negative component routes to `ValueStreamResult.annual_cost_gbp`, which finance already subtracts in EBITDA. Reported floor now equals the MILP objective by construction | `test_floor_equals_objective.py` (6 tests) |
| **F-16** | ✅ **FIXED** | Closed with F-02 — no schema change needed; the existing non-negative cost channel carries the negative part | as above |
| **F-07** | ✅ **FIXED** | Completeness is measured on **usable readings**, not timestamps. `HalfHourlyCoverage` gains `usable_intervals`, `null_reading_count`, `zero_reading_count`, `negative_reading_count`; the old timestamp figure survives under its own name so the two can never be confused | `test_reconciliation_gate.py` |
| **F-26** | ✅ **FIXED** | The measured energy total is now computed at all (`total_import_kwh`, `annualised_import_kwh`) — it previously existed nowhere, which is why F-10 was impossible | `test_reconciliation_gate.py` |
| **F-10** | ✅ **FIXED** | New `ingestion/reconciliation.py` implements Gate 3: declared annual kWh, peak kW and row count are compared against the measured series, with warn/error thresholds | `test_reconciliation_gate.py` (7 tests, incl. a regression reproducing the real Grundon four-month-sum defect) |
| **H-1** | ✅ **FIXED** | A scattered minority of exact-zero readings on an otherwise never-idle site is flagged as probable meter dropout; a genuine weekend/night shutdown is **not** flagged (the discriminator is the *share* of zeros, not the baseload height) | `test_reconciliation_gate.py` |
| **Gate 2** | ✅ **BUILT** | `ingestion/hh_mandate.py` + enforcement in `generate_quote`: no underwriting without a half-hourly basis; the basis (metered/modelled) is recorded on every quote and caps the confidence band | `test_hh_mandate.py` (6 tests) |
| **F-17** | ✅ **FIXED** | `agreed_rate_gbp` now requires an explicit `agreed_rate_unit` (`RateUnit`: £/kWh, £/MWh, £/kW/yr, £/MW/yr, £/yr); an unlabelled rate is **rejected at the door**, not defaulted — guessing the unit is the failure the field exists to prevent | `test_unit_coherence.py` |
| **F-52** | ✅ **FIXED** | `gearing_pct`/`rate_pct` reject values in `(0, 1)` as fractions written into a percent field, naming what the user probably meant. The `[0,100]` bound could never catch this because `0.6` is a valid 0.6% | `test_unit_coherence.py` |
| **F-15** | ✅ **FIXED** | `--assumptions` now defaults to the **newest release resolved from disk** (`assumptions.latest_version()`), never a hard-coded string; versions sort numerically so `gb_v1_10` follows `gb_v1_9`. Explicitly choosing a superseded release is allowed but adds a loud note to the run | `test_unit_coherence.py` |

| **F-11** / **S-2** | ✅ **FIXED** | New `value_stack/capacity_budget.py`: one asset, one power budget. Capacity-shaped streams are allocated the rating in priority order (CM → local flex → ancillary → BM) under BOTH the shared power constraint and the shared **energy sustainability** constraint, using NESO's per-service sustain durations. A stream that does not fit is cut back and says so; it is never sold twice. Wired into `build_value_streams` as a post-pass | `test_capacity_budget.py` (10 tests) |
| **F-19** | ✅ **FIXED** | Local-flex utilisation now applies a **call rate** instead of paying every in-window discharge as an instructed call. Default 5% — NGED's own published revenue-estimation-tool assumption; UKPN's median utilisation-to-availability ratio is 22%; only 19% of NGED day-ahead zones saw any dispatch in 2024/25. Overridable via `local_flex_piclo.utilisation_call_rate` | `test_capacity_budget.py` + valuation suite |

**The literature independently corroborated S-2.** The repo-blind survey — whose agents were
forbidden from reading this codebase — produced requirements **E-01** and **E-02** demanding exactly
the two constraints the pipeline lacked, with the equations and citations (Xu et al., IEEE TPWRS
33(2):2248-2259 eqs 17-23; Engels et al., IEEE Trans. Smart Grid 10(2):1971-1981 eq. 1e; NESO's
frequency-response product durations). Two independent lines of evidence, one reading the code and
one blind to it, reached the same conclusion. The fix implements their formulation directly.

**A test caught an error in the fix itself.** The first implementation applied the energy
sustainability cap **per stream**, but E-01's constraint is a *sum across services*
(`E >= Σ_s (D_s/60) × r_s`) — a shared energy budget. Per-stream caps would have let two services
each reserve the same kWh: the same double-sell as the power budget, one level down. Corrected, and
the Xu worked example is now pinned as a regression test.

**F-17 found a live defect in committed data.** Applying the unit requirement immediately failed
four test modules on `examples/demo_site/sitemodel.json`, which carried three consented rates —
`45.0`, `12,000.0` and `15,000.0` — with nothing recording that the first is £/kW/yr and the other
two are £/MW/yr. The demo data had exactly the ambiguity the finding describes. Now labelled.

Verified against the real data: the reconciliation gate reproduces Harwell's 908,590 kWh exactly
and independently flags its 338 zero-reads — the two defects §3 of the data-reconciliation document
had to find by hand.

**Still open: 42 Tier A + 5 Tier B**, plus WP-0 (the lost lenses and the whole refutation pass).

## 4. Tier A — agent-fixable, no human decision required

49 of 54. Ordered by severity, then by how much other work depends on them. **Length is not a tier
criterion** — several of these are multi-day changes and are still Tier A because the correct end
state is determinable.

| ID | Sev | Location | Defect | Fix |
|---|---|---|---|---|
| F-01 | CRIT | `milp_solver.py:497` | Flat/unmapped tariff ⇒ `energy_price=None` ⇒ charging priced at **£0**; battery cycles daily at a fabricated margin | Refuse to price arbitrage/DUoS when no time-mappable energy price exists. Never substitute zeros into the objective price. |
| F-02 | CRIT | `schedule.py:140,145` | Per-component `max(0,·)` clamps ⇒ reported floor **exceeds the LP optimum** | Report the signed `priced_floor_terms_gbp` (already implemented) as the floor; clamp only the total, and only with a warning. |
| F-03 | CRIT | `data_tier.py:55` | Tier changes no number and blocks nothing | Make tier load-bearing: gate underwriting on T2, cap the confidence band on T1, refuse T0. |
| F-04 | CRIT | `gb_streams.py:986` | Consent alone ⇒ BM/ancillary into the debt-sized floor | Require deliverability: SoC headroom reserved, mutual exclusivity enforced, export rights checked. |
| F-05 | CRIT | `engine.py:687` | Contracted export added **gross** to a no-export floor | Deduct charging cost at the price actually faced; net off cannibalised arbitrage; stop double-reporting as upside. |
| F-06 | CRIT | `assembly.py:90` | Library pinned by version **string** only | Hash the resolved assumption content into `input_hash`; fail a reprice against a changed hash. |
| F-07 | HIGH | `hh_data.py:285` | T2 completeness counts timestamps, not usable readings | Count non-null, non-flagged, physically plausible readings. |
| F-08 | HIGH | `hh_data.py:424` | Inferred non-30-min granularity warned then ignored ⇒ 2× kW error and zeroed dispatch | Honour the inferred interval throughout, or refuse the file. |
| F-09 | HIGH | `assembly.py:84` | `input_hash` never pins HH **content** | Hash the series content. |
| F-10 | HIGH | `loaders.py:33` | Declared `load_data` never reconciled with the priced file | Compute the measured total and peak; refuse on material divergence. |
| F-11 | HIGH | `valuation.py:89` | Same de-rated MW sold to CM + BM + ancillary; CM de-rating borrowed for BM | One MW, one sale per period; per-service de-rating. |
| F-12 | HIGH | `gb_streams.py:810` | One referenced requirement opens the gate for unreferenced £ | Gate each requirement on its own evidence. |
| F-13 | HIGH | `reprice.py:154` | Reprice silently skips optional sections (penalties) | Include every section that can move a number. |
| F-14 | HIGH | `financial_model.py:321` | `case_id` omits `debt_terms`/`price_structure` | Include them in the identity. |
| F-15 | HIGH | `cli.py:103` | CLI defaults to **`gb_v0_1`**, superseded; CM de-rating ~2.3–4× overstated | Default to the newest release; refuse superseded ones without an explicit flag. |
| F-16 | HIGH | `market.py:25` | `annual_revenue_gbp ge=0` **deletes** negative (cost) components from the FIRM floor | Allow signed values; a cost is not a zero. |
| F-17 | HIGH | `stream_consent.py:38` | `agreed_rate_gbp` is one unit-less float read as £/kWh, £/MWh, £/kW/yr **and** £/MW/yr | Make the unit explicit and validated. ×1000 error class. |
| F-18 | HIGH | `tariff.py:21` | Cannot express whether `import_rates` already include DUoS/levies; code stacks DUoS unconditionally | Add an explicit basis field; refuse to stack when inclusive. |
| F-19 | HIGH | `valuation.py:252` | Local-flex utilisation prices **every** in-window discharge as a paid DNO call — 100% call rate | Apply a sourced utilisation rate. Literature says this is 10–40× overstated. |
| F-20–F-49 | MED/LOW | *(see §8)* | 30 further findings | *(detail retained in the appendix)* |

**Selected medium/low items that matter more than their grade suggests:**

- `tariff_calendar.py:236` — a **missing tariff band rate is invented as the mean of the other
  bands**, silently, with no warning (findings 38, 41). Directly relevant: several Grundon meters
  quote day/night only.
- `tariff_calendar.py:198` — `peak`/`offpeak` labels are **guessed** onto the DUoS red window
  (weekday 16:00–19:00 only), so ~95% of consumption is billed at the offpeak rate (finding 39).
- `milp_solver.py:684` — a missing `warranty_cycle_limits` key **silently removes** the throughput
  cap described as HARD (finding 42).
- `mpc_solver.py:189` — MPC can breach the annual warranty cap ~7× (each window gets a 7-day quota,
  1 day is applied) (finding 44).
- `finance/cashflows.py:962` — the solver can never emit `BANKABLE_CANDIDATE`, so **finance's entire
  bankability gate is unreachable dead logic** (finding 32). Invisible today at 0 confirmed
  assumptions; the day the library is confirmed, everything still stamps NOT bankable.
- `schemas/finance.py:26` — percent-vs-fraction collision: `gearing_pct` accepts `0.7` meaning 70%
  as 0.7% (finding 52). ×100 debt sizing error.
- `milp_solver.py:410` — the solver **silently rewrites negative imports to zero**, contradicting
  ingestion's stated "flagged, never corrected" rule (finding 49). Structural for net-metered
  solar sites — which includes Tanhouse and Ewelme.

## 5. Tier B — requires a human decision

Five findings. For each, the interim safe behaviour is itself Tier A and should be implemented now.

| ID | Location | The decision | Who | Recommendation | Interim behaviour |
|---|---|---|---|---|---|
| B-1 | `gb_v1_6.yaml:200` | `target_equity_irr_pct = 12.0` sits at the **low end** of the plan's own 12–13%+ band, which flatters every price-to-target and viability verdict — while its provenance note claims conservatism | Exec (Sim) | Use the mid of the stated band, or state plainly that the low end is deliberate | Emit a warning naming the band and the position taken within it |
| B-2 | `gb_v1_6.yaml:334` | CM de-rating factors are exactly duration-proportional (1:2:4) — the signature of a **derived** value wearing a primary-source citation. Unverifiable in-repo | Modeller | Re-fetch from the EMR Delivery Body and machine-check the transcription | Flag the rows as unverified; cap confidence band |
| B-3 | `data_tier.py:87` | No plausibility screen: constant or single-day-tiled data earns T2 | Modeller | Define the screen (variance floor, distinct-day count, day-of-week signal) | Refuse to underwrite a series failing a basic variance test |
| B-4 | `milp_solver.py:477` | Partial-year samples annualised by flat `8760/covered_hours` with no seasonality correction | Modeller | Adopt a seasonality adjustment, or set a minimum coverage below which underwriting is refused | Refuse below ~12 months; warn loudly above |
| B-5 | `milp_solver.py:791` | P50 dispatch revenue is a **full-year perfect-foresight LP optimum**; the foresight haircut defaults to **zero** | Modeller | Set a sourced haircut — literature suggests 5–10% for ToU/DUoS floors, more for wholesale | Apply a labelled non-zero default and warn when unset |

**B-5 deserves emphasis.** With the haircut at zero, every P50 in the system is an *upper bound*
presented as a *central estimate*. That is a systematic optimism affecting every site.

---

## 6. Coverage gap — what was NOT reviewed

The **finance**, **sizing** and **orchestration** lenses never ran. My own partial coverage:

- **Finance — `returns.py` is sound.** Equity IRR correctly subtracts debt service
  (`equity_cf[t] = fcf[t] − service[t]`), gates IRR on exactly one sign change, and refuses to report
  a non-unique root. **The interest-as-income defect found in `Sim Work/Model_v5.xlsx` is NOT
  reproduced here.** Recorded so finance is not assumed guilty by association.
- **Sizing** — `SizingObjective.feasible()` requires a DSCR, so the covenant path is unreachable
  without debt terms; but the sizer **does** fall back to unlevered project-IRR ranking. Verified
  empirically: Robert Thomas Farms run with `credit_status` `unknown` vs `adequate` produced
  **identical** `no viable configuration` verdicts, for genuine economic reasons.
- **Orchestration** — the "no viable configuration" verdict conflates *"the asset does not work"*
  with *"we could not compute a financing case"*. A reporting defect, not a blocker.

**These three lenses must be re-run.** Finance especially — it is the highest-suspicion area in this
domain and it is the one area with no independent hostile pass.

---

## 7. Remediation plan

Ordered so nothing is built on what is about to be replaced.

**WP-0 · Re-run the missing review** *(blocks nothing; do first, cheaply)*
Re-run the three lost lenses (finance, sizing, orchestration) and the refutation panel over all 54
findings. Until this lands, every finding here is one-pass. *Acceptance:* every finding carries a
verdict; killed findings are recorded so they are not re-raised.

**WP-1 · Make the floor equal the optimum** — closes F-01, F-02, F-16
The floor must be the MILP's own signed objective value. Stop clamping components; stop deleting
negative revenue at the schema; refuse to price when no time-mappable energy price exists.
*Acceptance:* an invariant test asserting `reported_floor == LP objective value` to the penny, on a
site whose optimum has a negative arbitrage component. This is the highest-value package: it is
where the money is being manufactured, and every downstream number moves.

**WP-2 · Admission gates and the HH mandate** — closes F-03, F-07, F-08, F-09, F-10, B-3, B-4
Make `data_tier` load-bearing; count usable readings; honour inferred granularity; hash HH content;
reconcile declared against measured; refuse below a coverage floor. Implements
`docs/validation_requirements.md` Gates 1–3. *Acceptance:* a two-interval series cannot produce a
priced quote; a 40%-null series cannot grade T2; the Grundon four-month-sum defect is caught
automatically.

**WP-3 · Revenue-stack integrity** — closes F-04, F-05, F-11, F-12, F-19, and S-2/S-5
Enforce mutual exclusivity per settlement period; reserve SoC headroom for committed services;
check export rights; deduct charging cost from export; apply sourced utilisation rates. **Depends on
the literature checklist's mutual-exclusivity matrix** — this is where that artefact is consumed.
*Acceptance:* the sum of stream revenues cannot exceed what one asset can physically deliver in any
period; a site with `MEC=0` cannot book export or BM revenue.

**WP-4 · Tariff representation** — closes F-18, and findings 38, 39, 41
Add an explicit basis field (inclusive/exclusive of DUoS and levies); stop inventing a missing band
as the mean of the others; stop guessing `peak`/`offpeak` onto the red window. *Acceptance:* an
unquoted band refuses rather than fabricates. **Directly required for Grundon and Harwell.**

**WP-5 · Provenance** — closes F-06, F-13, F-14, and finding 31
Content-hash the assumption library and the HH series into `input_hash`; include debt terms in
`case_id`; reprice across all sections. *Acceptance:* editing a leaf in `gb_v1_6.yaml` invalidates
every quote priced against it.

**WP-6 · Units and schema coherence** — closes F-17, findings 35, 36, 52
Explicit units on `agreed_rate_gbp`; percent-vs-fraction discipline; `energy == power × duration`;
observed peak vs MIC coherence. *Acceptance:* the ×1000 and ×100 error classes become
unrepresentable.

**WP-7 · Tier B decisions** — B-1…B-5 to the modeller with interim behaviour shipped now.

---

## 8. Appendix — full finding register

The complete 54-finding register with evidence, failure scenarios and magnitudes is retained at
`docs/reviews/2026-08-10-hostile-review-findings.json`. Every finding carries its originating lens,
`file:line`, and its verification status (`CONFIRMED` for F-01/F-02, `UNVERIFIED` for the rest).
