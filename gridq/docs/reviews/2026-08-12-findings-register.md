# Authoritative FIXED / OPEN register — verified against code, 2026-08-12

> **⚠ STATUS COLUMNS SUPERSEDED (2026-08-16).** Re-verified against `299e535`, 35 commits after the
> tree this was built on:
> [`2026-08-16-tier-a-status-verification.md`](2026-08-16-tier-a-status-verification.md). Six rows
> move to FIXED — including **DEG-01** (§5) and **MD-F-05 limb (b)** (§3, C-10) — and §0's hazard is
> **one scheme wider than recorded here**: `2026-08-12-engine-review-findings.txt` files its own
> `N1`–`N7`, colliding with WP-0's `N-nn`. This document's *structure* (§0 conventions, §4 collision
> map, §8 KILLED) still stands; its *statuses* do not.

Built against working tree `agent/revenue-data-drive` @ `a0b3e07`. Every status verified against
**current source**, not the prose fix logs. Line numbers are current working-tree lines.

**Caveat on every FIXED row.** The remediation landed in one WIP commit `642621b` whose own message
says "NOT quality-barred". FIXED means *the code reads correct*, not *proven by a green suite*.

> **2026-08-15 UPDATE — the caveat above is now partly discharged, and the FIXED bar has been
> raised.**
>
> **A suite baseline exists.** The first complete run on this branch measured **176/176 test files,
> zero timeouts, seven failures in three files**, on a settled tree and cross-validated by an
> independent single-process run reproducing exactly the same seven. All seven are closed
> (`docs/changelog.md`, 2026-08-15). So "the code reads correct" is no longer the only evidence
> behind a FIXED row — though a green suite still proves only that the tests present would notice,
> which is precisely what the next paragraph exists to address.
>
> **New bar for marking anything FIXED** (`validation_requirements.md` §3, `CLAUDE.md` §4): name the
> **production call site** *and* **the test that fails if that call site is deleted**. The second
> clause is load-bearing — **all seven built-tested-never-called instances in this repo had a passing
> test**. What none had was a test that would notice the wiring disappearing. Rows in this register
> marked FIXED before 2026-08-15 were verified against clause (a) only.
>
> **Status changes since this register was written:**
>
> | Item | Was | Now |
> |---|---|---|
> | LOCAL_FLEX 20x proxy/engine divergence | OPEN | **FIXED** — the 20x *was* the F-19 parity fix working (100%/5% call rate); the failing test was stale |
> | Gate 3b bill reconciliation | *not yet filed* | **FIXED (wired)** — was the SEVENTH never-called subsystem; `quotes/pipeline._gate_3b` |
> | MPC rolling-horizon dispatch | *not yet filed* | **QUARANTINED** — `use_mpc=True` raises; fails closed-but-silent with `status="optimal"` and 0.0 volumes |
> | Gate 3 load reconciliation | OPEN (zero callers) | **FIXED** — wired at `quotes/pipeline.py:267` |
>
> **Newly filed OPEN — LATENT, not live. Severity corrected 2026-08-15 after checking the code
> rather than trusting the report it came from.** `local_flex_availability_value.utilisation_performance_ratio`
> is indeed passed at **neither** production call site (`gb_streams.py:1751`, `valuation.py:351` —
> both pass `contracted_mw=None`). But the first framing of this ("the ENA MUPF taper is inert on
> every run") overstates it. The taper at `:1585` is unreachable today for an **earlier** reason:
> with no award there is no `contracted_mw`, so `:1547-1552` returns **£0 with a labelled data ask**
> before control ever reaches the ratio. That £0 is correct, D30-compliant behaviour, **not a
> defect** — an availability price is asserted only from an awarded tender, and the register's
> per-DNO prices span ~40x.
>
> **The defect is what happens next.** The moment an award lands and `contracted_mw` / `window_hours`
> are supplied, the function reaches `:1577`, finds the ratio `None`, and returns `gross`
> **unreduced** — correctly labelled "held at 1.0 — an UPPER BOUND" with its own data ask, so the
> code is honest about it, but **neither caller has any way to supply the ratio**, so the clawback
> half of D-116 cannot be applied even when the data exists. Fix by plumbing the parameter through
> both call sites *before* the first award, not after. Recorded here because it will otherwise look
> like a new defect at exactly the moment local flex first earns money.
>
> **Also newly filed:** `mypy` does not cover `tests/` (`pyproject.toml:79`); enabling it flags the
> `GateContext`-in-the-`scenario`-slot bug's exact line, at a measured cost of 160 errors across 41
> of 178 files.

---

## 0. READ FIRST — three colliding `F-` numbering schemes

**The single largest hazard here. Never pass a bare `F-nn` to a fix agent.**

| Scheme | Where | Range |
|---|---|---|
| **MD** | hostile-underwriting-review.md §4 | `F-01…F-19`, then a collapsed `F-20–F-49` row |
| **JSON** | hostile-review-findings.json | `F-01…F-54`, original per-lens order, has duplicates |
| **LIT** | wp0-review-completion.md §4 literature table | a **third** unrelated meaning of F-03 / F-05 |

The same id means three different defects:
- `F-03` = MD "data tier is cosmetic" = JSON "gross export into the floor" = **LIT "MILP charge price omits the levy stack"**
- `F-05` = MD "gross export into the floor" = JSON "flat tariff prices charge at £0" = **LIT "one national DUoS red band for 14 DNOs"**

**Mandatory convention:** `MD-F-nn` (MD ids 01–19) · `JSON-F-nn` (anything above MD F-19) ·
`LIT-F-nn` (literature table) · `N-nn` (WP-0) · plain `H-/D-/B-/E-` (literature checklist).

JSON duplicates merged: `F-05+F-07`→MD-F-01 · `F-14+F-23+F-24`→MD-F-02 · `F-03+F-06`→MD-F-05 ·
`F-02+F-21`→MD-F-04. `N24` RETIRED into `N18` — never re-file.

---

## 1. DO NOT DISPATCH — already fixed

Dispatching these re-fixes working code and rebaselines fixtures for nothing.

`N2` `N4` `N5` **`N6`** **`N7`** `N8` `N15` `N16` `N17` `N18` `N19` `N20` `N21` `N22` `N23` `N30` ·
`LIT-F-03` · `MD-F-01` `MD-F-02` `MD-F-03` `MD-F-11` **`MD-F-15`** `MD-F-16` `MD-F-17` `MD-F-19` ·
`JSON-F-26` `JSON-F-52` · `JSON-F-27`/`JSON-F-37` (resolved as duplicates of N19/N20).

**§7 item 4 (N6, N7) is COMPLETE. Strike it from the plan.** Evidence: `two_stage.py:96,197,557-615`
(N6 size-diverse full-series cover before any no-viable verdict); `two_stage.py:105-148,343-379`
(N7 floor tested against `min(MIC, ASC)`, extends the search rather than declaring reinforcement).

---

## 2. DO DISPATCH — claimed landed, actually OPEN

The handoff says "WP-2/WP-3 landed". Three of those seven are not.

| ID | Location | Why it is still open |
|---|---|---|
| **N26** | `finance/cashflows.py:483` | `blended_rate = sum(import_rates.values())/len(import_rates)` — levies still excluded. The false parity claim is now *worse*, restated at `financial_model.py:143-146` while `_blended_import_rate` (`:243-254`) **does** add levies. |
| **N27** | `sizing/search.py:43-50,127` | `_rank_key` still `(metric_tier, value, -power, -duration)`; no `priceable` on `ScoredPoint`; `best=max(...)` not gated on `candidate_viable` (`grep -c candidate_viable sizing/search.py` = **0**). Only a docstring changed (`evaluator.py:19`, `fab9c6f`). |
| **N29** | `two_stage.py:620-622,639` | Proxy mode still `cloud.extend(confirmed)` (append, not merge); `search_space_size=len(cloud)`. Only the non-proxy branch de-duplicates, which it already did. |

---

## 3. PARTIAL — a named half still open

| ID | Closed | Still open |
|---|---|---|
| **N3** | Netting on the real path (`pricing.py:51,86`, `financial_model.py:159`) | (a) `annual_dispatch_cost_gbp` still NOT added (`financial_model.py:147` passes only operating cost); (b) discount still the equity hurdle on total capex (`pricing.py:20`); (c) nets the raw stream sum **before** the perfect-foresight adjustment, so it over-nets |
| **N9** | Fold route (`assembly.py:38,75` preserves `half_hourly_basis`) | `hh_synthesis.py` **never stamps** `half_hourly_basis="modelled"` (zero hits); `hh_mandate.py:108-110` still defaults an absent basis to `METERED` with a HIGH ceiling |
| **N12** | Measured verification (`pricing.py:223-250`, `financial_model.py:163`) — the half §7 deferred | Levelisation over the faded volume — the cheap arithmetic half §7 wanted first. `pricing.py:216` still concedes the flat-volume assumption |
| **MD-F-04** | Power + energy budget (no double-sold MW) | No SoC-headroom reservation, no export-rights check, no eligibility predicate (= D-92) |
| **MD-F-05** | Limb (a): overlay charge volumes reach finance (N23). Double-reporting-as-upside | **Limb (b) OPEN: cannibalised arbitrage not netted.** `engine.py:898-901` appends the contracted export stream to the **unchanged** base no-export `value_streams`; `valuation.py:629` prices it **gross**. Floor is still the physically impossible "full no-export arbitrage + full export volume" |
| **MD-F-08** | Granularity mismatch detected and reported (`hh_data.py:534`) | File not refused; inferred interval not honoured downstream |
| **MD-F-10** + **H-1** | Module built and tested | **NOT WIRED — see C-6** |

---

## 4. Collision map — the batching boundaries (all OPEN Tier A)

| File | # OPEN | Findings |
|---|---|---|
| **`optimisation/milp_solver.py`** | **10** | B-01, D-169, B-17, D-174, B-09, **DEG-01**, JSON-F-29, F-42, F-46, F-49 |
| **`finance/cashflows.py`** | **9** | N1, N13/H-15, N14, N26, H-08, H-17, H-16, D-33, D-130 |
| `finance/debt.py` | 4 | N10, H-04, H-03(B), H-05 |
| `optimisation/valuation.py` | 4 | D-158, D-116, JSON-F-30, MD-F-05 `:629` |
| `optimisation/tariff_calendar.py` | 3 | LIT-F-05, JSON-F-38≡F-41, JSON-F-39 |
| `value_stack/gb_streams.py` | 3 | MD-F-04 residue, MD-F-12, JSON-F-50 |
| `quotes/assembly.py` | 2 | MD-F-06, MD-F-09 |
| `finance/pricing.py` | 2 | N3, N12 |
| `ingestion/hh_data.py` | 2 | MD-F-08, JSON-F-25 |
| `optimisation/mpc_solver.py` | 2 | JSON-F-44, JSON-F-45 |
| `optimisation/engine.py` | 2 | B-14/JSON-F-47, MD-F-05 limb (b) |
| `finance/financial_model.py` | 1 | N25 ≡ MD-F-14 (one defect, two ids) |

**The two hotspots must each be ONE agent, sequenced internally.** `milp_solver.py` holds seven
findings in the model build alone (B-01, D-169, B-17, D-174, JSON-F-46, JSON-F-49, DEG-01).

---

## 5. DEG-01 — no degradation cost in the MILP objective (CONFIRMED)

**HIGH · Tier A · `optimisation/milp_solver.py:864-880` · in no §7 item.**

Objective is `price_da·w_da·(discharge−charge)·dt`, plus aging terms **only when non-zero weights are
passed** (`:874`, `:877`). `ObjectiveWeights` (`:188-200`): "**Default zero values** preserve the
existing annual-sizing LP behaviour". All three regularisers explicitly disclaim being costs:
`:81-83` `_EPS` "NOT a price"; `:85-86` `_EPS_PEAK` "NOT a price, NOT a calibrated degradation cost";
`:93-95` `_EPS_AGE` "breaks ties in exact arithmetic only".

Consequence: break-even arbitrage spread ~£11.4/MWh against a true ~£32.7/MWh (**~65% too low**).
Every trade in that band is booked profitable and is actually loss-making.

---

## 6. Contradictions, ordered by cost of getting them wrong

- **C-1 · `CLAUDE.md` §9 is stale and actively harmful.** It says "The CLI still defaults to the
  superseded `gb_v0_1` (open defect F-15) — pass the newest explicitly." **MD-F-15 is FIXED**:
  `cli.py:206-221` resolves `latest_version()` and warns loudly when a superseded release is chosen.
  Following the stale instruction pins runs to a library whose CM de-rating the repo's own audit says
  is overstated ~2.3–4x. (Independently confirmed by a baseline test failure asserting `gb_v0_1`.)
  Residual cosmetic staleness: `cli.py:7`, `:136` docstrings still say "gb_v0_1".
- **C-6 · Gate 3 is built, tested, and called from nowhere.** *Highest-value unclaimed item.*
  `reconcile_declared_against_measured` (`ingestion/reconciliation.py:123`) has **zero callers in
  `src/`** — only `tests/test_reconciliation_gate.py`. `assess_confidence` is called at
  `pipeline.py:272-279` **without** `reconciles=`, so `confidence.py:227-233` permanently takes the
  "was not reconciled" branch. A gate `validation_requirements.md` calls mandatory is unenforced on
  every production run. Exactly the "correct in module, wrong on the real path" class the handoff §8
  warns about.
- **C-7 · The N1/N13/N14 warning subsets were silently dropped from WP-1.** §7 item 1 required them;
  handoff §4 records only "N8, N15–N22, N30". Confirmed absent: `grep -rn "PRE-TAX|corporation tax"`
  finds only incidental price prose; no CPI/escalation machinery in `finance/`; zero hits for
  terminal value / decommissioning / replacement capex. **The pack still never states that it is
  pre-tax, un-escalated and terminal-value-free.**
- **C-10 · MD-F-05's cannibalised-arbitrage limb is open behind a FIXED claim.** See §3. **Critical for
  the export-lift work**: lifting export makes this double-count live and material.
- **C-3 · "WP-2/WP-3 landed" is false for N26, N27, N29.** See §2.
- **C-4 · Item 4 (N6, N7) is already complete.** See §1.
- **C-5 · N12's halves landed in reverse plan order.** The remaining work is the cheap arithmetic half.
- **C-8 · N9 route 2 open behind a FIXED claim.** See §3.
- **C-9 · MD-F-04 recorded fully FIXED; only the double-sell limb is.**
- **C-11 · Stale docstring `schedule.py:175`** still says components are "each clamped at £0" while
  `:136-170` compute them signed. Cosmetic, but it is the exact sentence the review cited as evidence,
  so a future reviewer will re-raise MD-F-02 from it.
- **C-13 · Every FIXED row rests on an un-quality-barred commit.** `pytest` had never had a verified
  full run before this session.

---

## 7. Tier B, and whether the required Tier A warning subset landed

Per the handoff ruling only **N1** and **H-03** are genuinely Tier B.

| ID | Human input needed | Tier A warning subset landed? |
|---|---|---|
| **N1** tax | SPV structure, capital-allowance basis, WDA timing, loss carry-forward | ❌ NO |
| **H-03** DSCR-sized debt | Whether DSCR-sizing replaces gearing x capex as default | n/a, none specified |
| **N13** indexation | The index, the rate, which lines are indexed | ❌ NO |
| **N14** terminal value | Residual-value basis, decommissioning obligation | ❌ NO |
| **H-05** covenant tiers | Lender lock-up / EoD thresholds, calculation-period convention | n/a; recommend adding one |

---

## 8. KILLED — never re-raise (WP-0 §5)

1. Debt asserted as gearing x capex *as a code defect* (survives only as H-03).
2. `price_multiplier` two-different-Low-cases (survives narrowly as N31).
3. Revenue-ranked fallback monotone in size (monotonicity proof false; survives as N4/N5).
4. End-of-tenor deliverability de-rating at day-one duration (table is exactly duration-proportional).
5. `underwrite_spec` cannot reproduce `extra_constraints` (no shipped path supplies it).
6. Finance bankability verdict dropped at persistence (it is persisted).
7. Scenario engine discards P90 warnings (validator forbids the degenerate shapes).

---

## 9. Counts

**FIXED** 27 filed findings (+2 duplicate-resolved) · **PARTIAL** 8 · **OPEN** 66 filed ids =
**62 distinct defects** after merging N13≡H-15, N25≡MD-F-14, JSON-F-38≡F-41, JSON-F-40≡F-43;
includes DEG-01 · **KILLED** 7 · **RETIRED** 1 (N24→N18).

## 10. Operational notes

- `graphify` is **not on `PATH`**; it is at `/Users/oliverpugh/.gamspy_venv/bin/graphify`. Its graph is
  dated 18 July, so it is **stale against all the August work**. Orientation only; verify in source.
- Findings that belong to **no** §7 item: LIT-F-05, N31, DEG-01, and the whole unscheduled
  hostile-review tail (provenance MD-F-06/F-09/F-13/JSON-F-31; tariff MD-F-18/JSON-F-38/F-39;
  ingestion MD-F-08/F-10/H-1/JSON-F-25/F-28/F-40; schema JSON-F-35/F-36; value stack MD-F-12/
  JSON-F-29/F-30/F-50; dispatch JSON-F-42/F-44/F-45/F-48/F-53/F-54; JSON-F-32; library
  JSON-F-33/F-34/F-51).
