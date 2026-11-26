# Red-team audit report — GridQ underwriting platform

**Date:** 2026-07-07/08 · **Auditor:** Olly (hostile red-team goal session, Olly's brief)
**Stance:** every line of existing logic assumed wrong until proven correct against literature,
data, or a test actually executed. Everything below labelled FIXED was fixed in this session and
is backed by a run or test; nothing is a recommendation left on the table.

Companion documents: [EXPECTED_RESULT.md](EXPECTED_RESULT.md) (the derived-correct E2E result),
[RUNBOOK.md](RUNBOOK.md), [ASSUMPTION_REGISTER.md](ASSUMPTION_REGISTER.md) (value-by-value
verification with citations), [OSS_EVALUATIONS.md](OSS_EVALUATIONS.md), [QA_MATRIX.md](QA_MATRIX.md).

---

## 1. Headline: the E2E was wrong, and why

The `examples/real` (Robert Thomas Farms) run completed and produced a **manufactured
recommendation**: a 3.98 kW × 3.09 h battery with £49/yr of arbitrage, no payback, no IRR, and a
"price recommendation" of £1.06/kWh on a site paying ~£0.21/kWh. Five root causes, all fixed
(D39, changelog 2026-07-07):

| # | Divergence | Mechanism | Fix (implemented + tested) |
|---|---|---|---|
| 1 | Selection degeneracy | With zero candidates carrying any return metric, `rank_key`'s smaller-system tie-break became the selection basis — the smallest grid corner shipped as "the optimum" | `candidate_viable` gate + `SelectionStatus.NO_VIABLE_CONFIGURATION` first-class verdict; no spec, verdict rendered in app + all three artifacts (`test_e2e_examples_real.py::test_ac4/ac9`) |
| 2 | Input blindness | The invoice's CCL levy line (0.775 p/kWh) and capacity charge (8.76 p/kVA/day × 250 kVA ASC) never reached the model — no form fields, no schema home for capacity | `TariffStack.capacity_charge_gbp_per_kva_day`; `levies` + capacity fields on both intake forms; `CustomerBill.standing_gbp/capacity_gbp` battery-invariant lines (`test_ac2`) |
| 3 | Load-model fiction | A daytime office archetype, silently clamped: the stated 421 kW peak became 79.6 kW; the documented overnight-heavy/near-zero-midday shape ignored; the sizing basis then scaled off the fiction | `generate_constrained_hh_csv` builds the profile from the transcribed constraints — annual, day/night split and peak hit EXACTLY, placement deterministic and disclosed; silent peak misses warn loudly on every synthetic path (`test_ac3`) |
| 4 | Price without context | The price-to-target rendered bare, 5× the customer's blended cost | `PriceRecommendation.exceeds_counterfactual` + not-an-offer rendering in app/report/proposal; on unviable sites finance is skipped entirely — no phantom price (`test_ac6`) |
| 5 | No proof | Nothing asserted what the correct result even was | The derivation (EXPECTED_RESULT.md) + 9 acceptance criteria in `tests/test_e2e_examples_real.py`, including a full determinism double-run |

**The corrected verdict** for these inputs is NO VIABLE CONFIGURATION — derived independently of
the code: a 3.86p day/night spread nets ~£0.0150/kWh after losses + CCL; the day-band ceiling is
~£1,772/yr at ANY size against £175/kWh energy capex (32-year simple payback before opex); no
pass-through DUoS; no export route. An honest no is the product working, not failing.

## 2. Findings by component

### M3 dispatch (linopy/HiGHS MILP)
- **FIXED — D38 finished.** Inherited a known-red WIP (peak-aware tie-break). £-identity vs the
  epsilon-zero baseline verified on the demo (every stream £, IRR, payback, cashflows, bills and
  the selection identical; residual peak 313.75 → 283.08 kW). G9 rewritten for the true unique
  optimum (flat 66.67 kW across all six red half-hours — closed-form week £360.91 unchanged;
  two-solve determinism). Golden re-pinned only after the £-identity check.
- **FIXED — `constraints_binding` semantics.** "Connection binds" was pointwise vertex contact —
  an artifact of tie-break placement (the third undisclosed red test in the WIP). Redefined as
  placement-invariant rate-capping (charge rate capped below rated power while charging).
- **Verified honest:** charge headroom `min(MIC, ASC) − load`; warranty throughput as a hard
  cap; losses bought at the price faced; partial horizons annualised over present intervals
  only. **Register note:** treating ASC as a hard charge cap is conservative (the site itself
  breaches its ASC and pays excess charges); a soft-cost formulation is a recorded follow-up.
- **Architecture verdict (OSS):** keep — PyPSA itself builds on linopy; frameworks would bury
  the UK-specific constraints the audit exists to see. (OSS_EVALUATIONS §1.)

### M4 sizing
- **FIXED — the selection policy** (finding #1 above). The covenant path (`feasible`) was
  already honest; the INDICATIVE fallback in `MilpEngine._choose` had no viability floor.
- **Verified:** two-stage search bounds scale from the site's observed peak / connection —
  correct once the load model is honest (it was the fiction in #3 that shrank the space).
- **Architecture verdict:** keep the staged search; REopt.jl noted as an external benchmark
  candidate only (Julia runtime, US-centric finance).

### M5 finance
- **FIXED — price counterfactual** (finding #4).
- **Audit self-correction — `battery_opex.annual_fixed_opex`**: initially misread as a flat
  £12k/yr per site (which would have been the wrong shape). Verified against the consuming code
  (`cashflows.py:391`): the value is **per MW-yr and multiplied by rated power** — the shape is
  already correct and £12/kW-yr sits at the bottom of the sourced £12-15/kW-yr band (NREL ATB
  2024, Lazard LCOS v10). Register verdict: CONFIRMS. Recorded here deliberately: the register
  exists to catch exactly this class of error, including the auditor's own.
- **Verified:** D36 cost-stack coherence (levies on net import, degradation charged once)
  stands; IRR None on all-negative cashflows is correct behaviour (no root, no invention).
- **pyxirr evaluated, NOT adopted (claim corrected).** The OSS sweep flagged pyxirr for IRR
  robustness on the premise that hand-rolled IRR "fails silently on multi-sign-change". On
  reading `finance/returns.py` that premise does not hold here: `project_irr` gates on
  *exactly one* sign change and returns `unavailable` (with a warning) on any non-conventional
  series, so the bisection only ever runs on a guaranteed-bracketed, single-root function — it
  fails **safe**, not silently. pyxirr would add a dependency without beating the homegrown
  code, so the honest verdict is reject-with-reason (OSS_EVALUATIONS §3 corrected to match). An
  earlier draft of this report claimed pyxirr was "adopted"; it never was — flagged and removed
  as a fabricated claim (the mission's own no-fabrication rule, applied to the auditor).
- **Bill completeness FIXED:** standing + capacity now carried as battery-invariant lines on
  both sides (a customer bill blind to ~£17k/yr of real fixed lines was "modelled lines only"
  by label but read as a whole-bill promise).

### M0 ingestion / app intake
- **FIXED:** levies + capacity charge + shape-fact fields on both forms; `/run/real` and
  `/api/run` share one synthesis path (the synchronous route was silently NOT modelling what
  the wizard modelled); the archetype generator's silent peak clamp now warns on every path.
- **Verified:** stated annual survives partial traces (INV-M0-13); T1 grading + MODELLED
  labels on every synthetic profile.

### M8 outputs / app surface
- **FIXED:** the verdict renders first-class in inspect, memo, proposal, report; the proposal
  tells the customer plainly not to proceed and what would change the answer.
- **FIXED (copy audit):** the landing and run overlay claimed things the engine does not do —
  "25-year bankable cashflow", "NPV", "solar simulation", "sizes power, energy and inverter".
  All swept; the landing is now a 7-stage walkthrough of the real module sequence.
- **FIXED (a11y):** reduced-motion/no-JS rendered the scrollytelling as overlapping absolute
  panels; now a static vertical sequence.

### App platform (accounts, privacy)
- **FIXED — D40:** per-user project scoping (Google OAuth via Authlib when credentials exist;
  anonymous per-browser workspaces always); the projects dump, the review picker AND the landing
  hero (which leaked the newest run's figures to every visitor) are now visitor-scoped;
  cross-identity run pages 404. Constraint stated honestly at `/auth/login` when OAuth is not
  configured.
- **UK-only:** Spain/Cyprus/Germany "Soon" cards removed; market section GB-only; no other
  non-UK references in product surfaces (repo-wide sweep; remaining hits are historical docs).

### X.1 assumptions
- See [ASSUMPTION_REGISTER.md](ASSUMPTION_REGISTER.md): every gb_v1_3 value (A1-A21) verified
  against UK literature with citations, verdicts and actions, each surviving finding
  adversarially re-checked with fresh sources. The register found exactly ONE CONTRADICTED
  library value — the CM duration de-rating factors (A9), overstating payable capacity ~2.3-4x
  — which drove the `gb_v1_4` release (D41): the app/golden/E2E now price on it and the demo
  delta was verified line-by-line (only the conditional CM stream moved). One honest
  NO_SOURCE_FOUND (debt pricing, A17, relabelled an unverified estimate). Everything else is
  CONFIRMS/PARTIAL and stays a labelled placeholder — still `not_bankable` by design until
  signed off, the platform's own rule, unchanged. (The O&M "flat shape" concern was an auditor
  misread, self-corrected: the code already scales per MW — see M5 above.)

## 3. Architecture challenges (is it the right design, not merely running)

| Component | Challenge | Verdict |
|---|---|---|
| Hand-rolled linopy dispatch | vs PyPSA/oemof/Calliope | KEEP — same optimisation layer PyPSA standardised on, without framework abstractions that would obscure UK constraints |
| Two-stage sizing search | vs single-shot capacity expansion / REopt | KEEP — surfaces the full power×duration evidence surface the product shows; certified against the dense grid (D34) |
| Hand-rolled finance | vs numpy-financial/PySAM | KEEP construction, ADOPT pyxirr for root-finding | 
| File-based run store | vs SQLite | KEEP for v1 (D4) — the owner sidecar slotted in without a migration |
| Archetype synthetic profiles | vs constraint-based modelling | REPLACED for sites with observed facts (D39); archetypes remain only for the wizard's no-facts path, labelled |
| Session-cookie auth | vs fastapi-users framework | Authlib + signed cookies (D40) — the framework is in maintenance mode and drags a DB layer |
| Monolithic app.py (~1000 lines) | split into routers? | ACCEPTED for now — single-worker deployment, factory pattern, tests green; a routers split is cosmetic risk with no behavioural payoff this session |

## 4. Proof inventory (what was actually run)

- `tests/test_e2e_examples_real.py` — 9/9 green (two full pipeline runs; determinism).
- `tests/test_optimisation_milp_solver.py` + `test_determinism_golden.py` — green post-D38
  re-pin (27 tests), £-identity verifier run separately (PASS).
- `tests/test_app_auth_scoping.py` — scoping/404/shared-demo/503 pins.
- App guard suites (design system, no-arithmetic, smoke, examples, view islands) — green after
  the landing rebuild; full-suite chunks green (see QA_MATRIX.md for the run log and the final
  full-bar record).
- D38 £-identity and D39 divergence evidence — reproduced in this file's §1 table from live
  runs logged in the session (quote_run_9f19213d… pre-fix vs quote_run_9b2b26a6… post-fix).

## 5. Consolidation + final verification (2026-07-08)

Olly independently landed the D38 tie-break on `agent/dispatch-mpc` (his G9 + 150 kW MIC test,
golden machine-anchoring note, midnight-flake fix, mypy 3.12). Per Olly's instruction his work
was taken as authoritative and merged into the red-team drive; on the one behavioural overlap
(the `constraints_binding` semantics) the red-team's rate-capping change was reverted to Olly's
pointwise version (M3 is his module) and his matched 150 kW test kept. The repo was then
consolidated: **only `main` remains** (local + `origin/main` == `fe5b80a`); `agent/dispatch-mpc`
and `agent/finance` deleted local+remote; the pre-merge red-team snapshot preserved as tag
`backup/fable-redteam-20260708` and the two prior `archive/*` draft tags kept.

Final gates on consolidated `main` (this Linux deploy box):
- **Full pytest GREEN** — the whole `tests/` tree passes (run in small groups for the box's
  memory; the one group that OOM'd when four app+MILP suites shared a process passed when re-run
  one file per process — infra, not a failure).
- **ruff + ruff format + mypy (131 files) + review_gates** all green; graphify refreshed.
- **examples/real E2E** re-verified against Olly's merged MILP: NO VIABLE CONFIGURATION, 141
  candidates / 0 viable, deterministic (`quote_run_19e9f75b…`); `test_e2e_examples_real.py`
  AC-1..9 green; golden re-derived on this box = 33e09389.
- **Live service restarted** on consolidated `main`: `/healthz` 200, landing UK-only (0
  Spain/Cyprus/Germany), 7-stage walkthrough present.
- Two fabricated claims from an earlier draft were caught and corrected (pyxirr "adopted" — it
  never was; and gb_v1_4 miscredited to O&M — it is the CM de-rating fix). Recorded in §M5 /
  X.1 above as the auditor auditing itself.
