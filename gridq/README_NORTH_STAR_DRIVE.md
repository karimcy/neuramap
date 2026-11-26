# The North-Star Drive — what this branch adds

**Branch:** `karim/north-star-drive` · **Author:** Karim (via an autonomous Claude loop, 19 iterations, 1–2 July 2026) · **Base:** `main` @ `d217659`
**State:** whole tree green — **674 tests** (main had 568), `ruff` / `ruff format` / `mypy` / `review_gates` all clean on every slice.

This branch is one continuous piece of work: it sets GridQ's product goal in writing, then builds four waves of the roadmap under it. Every quote produced by this code remains **honestly `not_bankable`** — no gate was softened anywhere; several changes exist specifically to make outputs *less* flattering.

---

## 0 · The goal (new)

| File | What it is |
|---|---|
| `docs/north_star.md` | **The adopted product goal (D27)**: the battery as a *two-sided firming asset* — firm the **customer** against a volatile grid (peak protection → a capped bill), firm the **grid** with contracted, provably deliverable flexibility, firm the **lender** with a P90 that survives diligence. Includes the world-class definition (8 criteria) and the Waves 6–11 roadmap. |
| `docs/reviews/2026-07-01-deep-review.md` | The evidence record: a 13-agent deep review of the whole codebase + market landscape (all repo claims source-verified), plus the 4-lens adversarial critic pass the north star had to survive before adoption. All market figures inside are labelled **unverified research** — none is a pricing input. |

Decisions this drive recorded: **D27** (adoption), **D28** (duration-key flooring retained; search must be step-aware), **D29** (sizing production method; engine-default swap *gated* on C025), **D30** (assumption-schema evolution: 23 sections frozen forever, new sections grow optional-with-empty-default).

---

## 1 · Wave 6 — the adaptive sizing search (issue 016, all 9 stages)

Replaces the fixed 3×3 power×duration grid with a continuous, certified search. **The inner evaluator never changed: every candidate is still priced by the exact full-year half-hourly LP + the real finance model.**

New modules (all under `src/gridq/sizing/`):

- `domain.py` — the continuous 2-D power×energy box, site-scaled (`from_site`: observed peak else connection MIC — never an invented kW), C-rate banded 0.5–8 h. *Fixed a real defect:* the Latin-hypercube sampler could emit out-of-band specs; power is now sampled only within the band-feasible window.
- `evaluator.py` — score any continuous (power, energy) point through the identical dispatch+finance path, **recording which objective metric each point was scored on** (levered return / project IRR / priced-floor fallback) so a search can never claim the levered objective while debt terms are absent (B001).
- `parallel.py` — the concurrent solve harness (one solver per worker, results provably identical to sequential). Measured: 2.0 s/solve → 0.8 s effective at 4 threads; a ~1,600-point dense grid ≈ 21 min.
- `search.py` — `dense_grid_search` (the exact reference surface), `adaptive_refine_search` (pure zoom sampling — no surrogate, step-aware per D28, feasibility-first incumbent, budgeted, logged convergence curve), `pso_search` (the seeded comparison baseline), and `search_result_to_sizing_grid` (the wider surface rides the existing `QuoteRecord.sizing_grid` seam with zero schema change).
- `deliverability.py` — see Wave 7.

**Validation on the demo site (real MILP):** the wider scope changes the answer — 124 kW×4 h → 165 kW×8 h, **+10.8 % objective** vs the shipped 3×3; the refiner matches-or-beats the dense reference (≈ zero information loss, D29's certification target).
**Finding C025 (filed):** the optimum pins to the 8 h band edge because placeholder capex is per-MW only (energy is capex-free). **The engine default deliberately stays on the 3×3** until a £/kWh capex component is sourced.

## 2 · Wave 7 — the firming substrate (the thesis, made physics)

The deep review's central finding was that firming existed as *vocabulary without mechanics*. All five mechanics are now real, tested code:

| Piece | What it does | Key files |
|---|---|---|
| **Contract shape** (C026 filed for Olly first) | `AvailabilityCommitment` / `AvailabilityWindow` — committed kW, optional held kWh, recurring windows, tenor, provenance (nothing defaulted into existence) + a typed `parameters` payload on `DispatchConstraintDescriptor` so firming constraint kinds become **compilable, not just nameable**. | `src/gridq/schemas/commitment.py`, `src/gridq/value_stack/interface.py` |
| **Solver physics** | `problem.constraints` is finally read: a kind→builder registry compiles commitments into reserved net-injection headroom (`discharge−charge ≤ power−C` in windows) and held state-of-energy floors. Test-proven: the unconstrained solve full-discharges through a committed window; the committed solve provably holds capability — at a measured cost (the constraint binds economics). Payload-less descriptors change **nothing** (every pre-existing golden survives). | `src/gridq/optimisation/milp_solver.py` (`_compile_commitments`, `_commitment_mask`) |
| **Battery aging** | The closed-form end-of-tenor check: `power × derate × (1−fade)^N ≥ committed kW` (+ the energy-side hold), three-state honest. **M4's dormant acceptance criterion now fires**: a candidate that *passes its DSCR covenant* is rejected for year-15 capability shortfall. | `src/gridq/sizing/deliverability.py`, `sizer.py` |
| **Evidence-bearing gates** | The first `GateContext` producer: gate facts asserted **only** from commitments carrying a contract reference, each paired with `CommitmentEvidence` (who confirmed, on what basis, against which contract; engine-computed verdicts say so). End-to-end: a referenced, deliverable CM commitment flips CONDITIONAL→CONTRACTED and prices the *reserved* capability; an undeliverable one stays CONDITIONAL. | `deliverability.py` (`produce_gate_context`), `milp_solver.py`, `valuation.py` |
| **The customer bill** (resolves **C017**) | Bill before/after computed from the exact series the MILP optimised (energy/DUoS/levies decomposed; losses honestly priced — negative savings representable), persisted hash-safe (`QuoteRecord.customer_bill`), rendered in the proposal **with a permanent label of what is NOT modelled** (standing charges, capacity charges, TNUoS). | `src/gridq/schemas/customer_bill.py`, `optimisation/schedule.py`, `quotes/`, `outputs/` |

## 3 · Wave 8 — underwriting risk shapes (lender-grade P90)

- **D30 — schema evolution**: new optional assumption sections (`availability`, `penalties`, `tnuos`, `bill_products`) with empty defaults — frozen releases load byte-identically; *empty means "predates the section", never zero*. (`src/gridq/assumptions/models.py`)
- **The perfect-foresight haircut is consumed** — it sat in the library with zero code consumers, so P50 was the raw full-knowledge LP optimum. It now enters as a **named deduction** on dispatch-optimised streams only, loudly labelled INTERIM until the M7 backtest. Consequence handled: the demo's illustrative gearing re-tuned 55 %→25 % (the correction halved its thin CFADS; the covenant gate now demonstrably fires — 1 pass / 8 rejected). (`finance/cashflows.py`, `app/demo.py`)
- **Firmness-differentiated P90** (contracted revenue gets its own additive haircut key; shares the merchant one *with a loud warning* until a release carries it) + the **worst-case CM penalty line** entering the P90 series only (an empty `penalties` section = a warned data-ask whenever contracted revenue is priced). (`finance/cashflows.py`)
- **Contracted-basis DSCR** measured against the same debt service on a deliberately conservative all-costs contracted CFADS (no cost allocation invented) — reported, gated only informationally until the per-basis covenant policy exists; **DSCR stays unavailable without real gearing (B001)**. (`finance/debt.py`, `financial_model.py`)
- **`assumptions/gb_v1_1.yaml`** (new frozen release): folds `financing.credit_rate_tenor_policy` from the v0_1 line (**the release divergence closes**) and populates the D30 homes as fully-labelled placeholders (the CM penalty caps are research-grade with the review record named as source — confirm against the CM Rules before real use).
- **The memo grew up**: scenario sensitivities render from real `run_scenario_set` output (identity bands honestly noted), and — resolving **C020/C023** — every stream-cited assumption displays its value/status/confidence/**source** from the pinned release in a new "Assumptions & sources" section (memo + HTML report). `generate_for_run` auto-loads the release; the CLI/app get it with zero caller change. (`outputs/sensitivities.py`, `viewmodel.py`, `underwriting.py`, `html_report.py`, `generate.py`)
- **C027 filed** — the `ValueStreamResult` growth (availability/utilisation split, tenor, per-stream penalties) is a frozen-contract joint event awaiting Olly; deliberately **not** built unilaterally.

## 4 · Wave 9 — customer products & site completeness (buildable scope)

- **The GB charging calendar is versioned content**: a release may override the DUoS red/amber/green windows (`duos.band_windows_weekday`); absent, the documented code fallback applies byte-identically — insurance against the Reformed-National-Pricing / DUoS-SCR reshaping over a 15-year quote. (`optimisation/tariff_calendar.py`)
- **Connection headroom, honestly**: the dispatch is bill-optimised, *not* peak-optimised, so pricing ASC reduction today would be invention — the objective design is **C029** (filed for Olly); meanwhile the measured peak import before/after rides the bill into the proposal with a permanent not-priced label. (`schemas/customer_bill.py`, `outputs/proposal.py`)
- **The capped-bill product exists as an honest shape**: `PriceStructure.CAPPED_BILL`, structure-only, naming both hard dependencies verbatim — the M&V method (boundary Open Q4, owner unresolved) and sourced volume-variance keys (`bill_products.*`, explicitly *not* the P90 haircut). (`schemas/enums.py`, `finance/pricing.py`)
- **C028 filed** — explicit PV on `SiteModel` (generation series, self-consumption dispatch, compilable G99 export limit) is a frozen-contract joint event awaiting Olly.

## 5 · Defects found and fixed along the way

1. `SizingDomain.latin_hypercube` could emit specs outside the duration band (clamp-branch defect).
2. A rounding-order bug (power rounded after the energy window was derived) could breach the 8 h band edge — in the PSO clip *and* latent in the LHS sampler.
3. The HTML report ignored supplied sensitivity rows entirely (permanent "pending" note).
4. *(Caught by the guard, not shipped)*: the AST no-arithmetic invariant rejected a string `+` in the HTML renderer during the build — the invariant works.

## 6 · How to review this branch

- **Slice-by-slice narrative**: `docs/changelog.md` (2026-07-01 → 02) — every slice records its tests, bar result, and honesty reasoning. Decisions: `docs/decisions.md` D27–D30.
- **Open threads for Olly**: OPEN_COMMENTS **C026–C029** (all four are designs awaiting co-decision; nothing frozen was changed unilaterally). Resolved this drive: C017, C020, C023.
- **The visual summary**: `/static/changes-2026-07.html` on the live VPS (an additive file, safe to delete).
- **Run it**: `pip install -e ".[dev]"` then `pytest` (674 green), `ruff check .`, `mypy`, `python -m gridq.dev.review_gates`.

## 7 · What is deliberately NOT in this branch

- No change to the two frozen platform↔engine contracts (`SiteModel`, `OptimisationResult`) — every needed change is a filed design (C026–C029).
- No bankable anything: 0 confirmed assumptions exist; the gates that enforce that are untouched and test-pinned.
- No engine-default swap to the wider sizing search (gated on C025 per D29).
- No merchant pricing (wholesale/BM/ancillary stay £0 gated upside pending the RTM partner, B002).
- The live-URL auth gate (`GRIDQ_BASIC_AUTH`) — a human/live-host action, still open, still urgent.
