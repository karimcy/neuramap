# North Star — the GridQ goal

**Status:** Adopted (Karim, 2026-07-01 — [decisions.md D27](decisions.md)) · **Set by:** Karim (product intent) · **Executed by:** Olly (per the [boundary](00_project_boundary.md) split)

This document states the product goal every wave iterates toward, the honest gap between that goal and what is built, and the sequenced path to close it. It is the reconciliation reference for every session: when a request clashes with this document, flag it. It changes only by a further decision recorded in [decisions.md](decisions.md).

> **Provenance note.** Market figures and competitive-landscape claims in this document (de-rating factors, revenue benchmarks, charge levels, dates, "no comparator ships X") come from perimeter research and are **unverified external references**; the full findings with their web sources are recorded in [docs/reviews/2026-07-01-deep-review.md](reviews/2026-07-01-deep-review.md). None of them is an input to any pricing calculation. Before any figure touches the pricing path it must enter the assumption library as a sourced, versioned entry per X.1 discipline — this document never overrides that door.

---

## 1. The goal

**Size, price, underwrite — and warrant in operation — the behind-the-meter battery as a two-sided firming asset, and be the engine counterparties trust to transact on.**

At the company level the thesis is Karim's: the battery behind the customer's meter peak-shaves, arbitrages time-of-use and network charges, and stacks contracted and merchant markets — that is the revenue stack. But the organising product idea is **firming against an increasingly flexible, volatile grid**, in both directions:

- **Firm the customer against the grid.** As half-hourly settlement (MHHS) pushes every business onto time-of-use and dynamic exposure, the battery converts a volatile bill into a certain one: peak protection, capacity-charge management, and ultimately a fixed-or-capped-bill product. *"We firm your energy cost."*
- **Firm the grid with the battery behind the customer's meter.** Dependable, contracted flexibility — Capacity Market agreements, DSO flexibility windows, and (when the route-to-market gate opens) balancing and wholesale participation — where committed capability is **actually reserved in dispatch and provably deliverable to the final contract year at degraded capacity**, not assumed.

Between the two sits the promise that makes the business fundable:

- **Firm the lender's case.** Contracted revenue enters the priced floor only with computed deliverability, penalty-aware downside, and firmness-differentiated uncertainty — a P90 a lender can diligence, not a labelled guess.

**Who operates.** *Superseded by **ADR-D2** (decided 2026-11-25, option d; decisions.md D69): Neura owns live optimisation and the dispatch envelope, the RTM partner executes in the market, the OEM owns certified control, and control authority is staged, with a compute turn-down stage (L4) in shadow only. The paragraph below is the earlier working position, kept for the record; source `atlas/roadmap/adr/ADR-D2.md`.* GridQ does not build the operating system ([boundary §2](00_project_boundary.md)): dispatch in operation is run by the RTM/aggregator partner against the **dispatch envelope GridQ exports** — the underwriting schedule is the benchmark operations are measured against. PE owns the dispatch-envelope export and the M&V *data contract* only; the M&V method owner is an open dependency (boundary Open Q4). "Operate" in the company thesis is delivered by that handoff, not by code built here.

**Whose engine.** The working assumption (Karim to confirm at sign-off): GridQ underwrites **Neura's own deployments first** — "the engine the industry trusts" means installers sell on its proposals and lenders transact on its numbers, not a standalone SaaS play. Under the default fixed-£/kWh structure the asset is **Neura-owned** behind the customer's meter, so availability-penalty exposure (Wave 8) lands on Neura's book.

This thesis is not new scope. It is latent in the System Brief — the three-tier GB ladder (bankable floor → contracted → gated upside), the market interface's *firmness & decay* and *deliverability profile* slots, CM "deliverability verified through the final contract year at degraded capability" ([boundary §3.4](00_project_boundary.md)), MIC/ASC headroom as the connection primitive. This document elevates it from latent design intent to the explicit goal the roadmap is ordered by.

## 2. The three promises, concretely

| Promise | What the product says | What the engine must therefore do |
|---|---|---|
| **Customer** | "Your energy cost is firmed: peak-shaved, capacity-managed, and (contractually) capped." | A real bill model (before/after, decomposed: energy ToU, DUoS bands, capacity/ASC charges, the un-firmable residual honestly labelled); a priced connection-headroom stream; fixed/capped-bill structures with an explicit volume-variance downside. |
| **Grid** | "This capacity is dependably available in the committed windows, for the life of the contract." (Committed by Neura as asset owner; delivered via the RTM partner's operation of GridQ's dispatch envelope.) | Availability windows, held state-of-energy and reserved power headroom as *solver constraints* (not post-hoc attribution); simultaneous multi-service allocation with no double-sold kW; penalty/non-delivery economics. |
| **Lender** | "The P90 is real: contracted and merchant revenue carry different risk shapes, deliverability is computed at end-of-tenor degraded capability, and penalties are in the downside." | Firmness-differentiated P90; contracted-vs-merchant CFADS in debt sizing; a computed `end_of_life_deliverable` verdict per sizing candidate; calibrated perfect-foresight haircuts (M7); contract tenor and re-contracting cliffs in cashflows. |

## 3. Where we stand (honest baseline, 2026-07-01)

Every repo claim in this section was verified against source by the [2026-07-01 deep review](reviews/2026-07-01-deep-review.md) and re-verified by an independent fact-check pass; market claims are that review's unverified research.

**The chassis is right, and it is the moat candidate.** Nothing in our (unverified, mid-2026) competitive survey appeared to ship GridQ's combination of: deterministic hash-derived quote identity reproducible over a public URL, versioned assumption releases whose per-entry status structurally forces `not_bankable`, firm-vs-gated separation that mirrors how lenders read CFADS, an exact full-year half-hourly (~17.5k-interval) LP dispatch behind a frozen contract, and quote telemetry from quote one. Wave 5 shipped the end-to-end product surface live (568 tests green at the W5-5 acceptance record). The nearest real comparator on modelling breadth is **Gridcog** — treat Gridcog-level simulation as the baseline to beat, not HOMER-class tools. The *durable* moat is this chassis **plus** the calibrated realised-data flywheel (Wave 11) — process discipline alone is copyable; calibration evidence tied to provenance-stamped quotes is not.

**The firming proposition is currently vocabulary without mechanics.** The deep review found every firming-shaped concept present as a *name* and absent as a *behaviour*:

1. **The stream→dispatch constraint seam is dead.** `dispatch_constraints_for()` builds descriptor lists onto `DispatchProblem.constraints`, but `MilpDispatchSolver` never reads them. The declared kinds — `held_state_of_energy`, `availability_window`, `deliverability` (`src/gridq/value_stack/gb_streams.py`) — are compiled into nothing. (Connection-limit and throughput-cap physics *are* enforced, by hardcoded solver logic that bypasses the descriptors — the seam is dead, not the physics; the firming kinds have no such parallel.) A dispatch can drain the battery straight through a would-be CM stress event.
2. **`GateContext` has no producer.** Every callsite passes `context=None`, so `CapacityMarketStream` and `LocalFlexPicloStream` are permanently CONDITIONAL — contracted firming revenue has **no live path into the priced floor**.
3. **End-of-tenor deliverability is a stub.** `end_of_life_deliverable` is hardcoded `None` (`src/gridq/sizing/sizer.py`); nothing computes power × de-rating × (1−fade)^N against a committed MW. The boundary doc's own CM promise, and M4's "at least one candidate rejected" acceptance criterion on this axis, cannot fire.
4. **No penalty economics anywhere.** `ValueStreamResult.annual_cost_gbp` is already *consumed* by finance (`cashflows.py`) but no producer ever sets it non-zero — penalties and clawback are a producer-side gap; commitments are priced as free option value.
5. **The uncertainty machinery is one flat haircut.** P90 = P50 × (1 − one placeholder %) for every stream and year; the library's `perfect_foresight_haircut_pct` has zero code consumers; M7 does not exist as code. Contracted availability and merchant arbitrage share an identical downside — the wrong risk shape for a lender.
6. **The customer promise is blank.** No bill model (C017 — bill before/after renders "pending"), no TNUoS representation at all, and the connection-headroom stream (MIC/ASC — our own named connection primitive) is deliberately priced at £0 with no max-import variable in the solver.
7. **On-site generation is materially under-modelled.** A solar-equipped site quotes only on its net-import series — PV self-consumption interaction, sized-PV what-ifs and export value are invisible to the solver (no generation series on `SiteModel`, no export variable). The dominant installer channel shape is under-modelled, not un-quotable; explicit PV representation needs no RTM partner (only merchant export pricing does).
8. **Content gaps flagged by research (to verify before use):** the CM de-rating placeholder (4h → 0.95) is roughly 2.3× more optimistic than the NESO-methodology figures reported by Modo (~0.42 at 4h); the TNUoS demand residual — un-avoidable post-TCR — roughly doubles from April 2026; Piclo-style DSO contracts pay availability (£/MW/h) *plus* utilisation (£/MWh) and we model only utilisation. Sources in the [review record](reviews/2026-07-01-deep-review.md). Each is a sourcing task into the assumption library, not a prose fix.

None of this breaks the architecture. The review's strongest structural finding is that the firming products **fit the existing abstractions**: `ContractedStream` + `GateContext` (D12) + additive hash-excluded `QuoteRecord` fields (D13/D23/D25 pattern) + assumption-version promotion. Roughly half the work lands on frozen seams (deliberate seam events by design); the other half is content and constraint-compilation inside existing lanes.

## 4. Definition of world-class

GridQ is world-class when all of the following hold. W1–W5 and W7 are **test-enforced** (CI proves them); W6 and W8 are **evidence-based** (measured in the world — CI cannot prove them, telemetry and external review record them).

- **W1 — Expressible.** Every firming mechanic (availability window, held SoE, reserved power, deliverability, penalty) is a solver constraint or cashflow term, declared by streams and compiled by the market-agnostic core. The constraint seam is alive; no `market == "GB"` branch appears.
- **W2 — Deliverable, computed.** Every contracted commitment carries a computed capability-vs-commitment verdict through its final contract year at degraded capacity, with provenance (who confirmed, which contract, which year, what SoH — the human confirmation itself is evidence, not a test). Sizing rejects undeliverable candidates — the rejection test exists and fires.
- **W3 — Risk-shaped.** P90 differs by firmness; penalties enter the downside; debt can be sized on contracted CFADS; the perfect-foresight haircut is consumed, then calibrated (M7) against a causal dispatch policy and, eventually, realised data.
- **W4 — Customer-legible.** A quote shows bill before/after with the un-firmable residual honest, the firming commitments (windows, tenor, counterparty) visible, and the dispatch chart shows *held available* as a first-class state.
- **W5 — Sizing-credible.** The continuous sizing search (issue 016) is landed: benchmarked against a dense exact-grid reference with a certified tolerance, deterministic, and honest about which objective metric it optimised.
- **W6 — Externally validated.** The lifecycle machinery is exercised end-to-end; at least one underwriting pack has survived an external lender/credit-adviser review with findings logged, and one customer has signed off the proposal artifact. When the pipeline delivers a real contract (Zara/Karim-dependent — not a PE deliverable), the transition and realised-vs-underwritten telemetry capture it with **zero code change**.
- **W7 — Honest, always.** Everything above routes through the existing gates: placeholders force `not_bankable`, missing data degrades to ranked asks, no number originates in prose. World-class never means softening the honesty discipline; it means fewer placeholders left to disclose, because more of the stack is real and sourced.
- **W8 — Cost-compressing.** The wedge GridQ exists for: measured time-to-quote and human-minutes vs the captured manual benchmark hit the set, versioned targets (boundary Open Q6 — indicative within a working hour of T0, underwritten within five working days of T2, placeholders until set); both quote modes ("fast & banded", "slow & firm") exist; hit-rate-by-tier is reported from telemetry.

## 5. The road — sequenced waves

**Immediate action (outside the wave sequence):** a real prospect's data (the Haywood Oaks / Robert Thomas Farms prefill) is reachable via an open public URL. Enable the built-and-dormant `GRIDQ_BASIC_AUTH` gate before the next external share; the branded-domain choice remains Olly's judgment call. A trust-branded product cannot carry this footnote.

**Commercial anchor.** The pilot site is the forcing function, not the wave logic: Wave 7 makes the pilot **credibly quotable** (firming substrate + a record-backed bill headline), Wave 8 makes the pack **lender-readable**, Wave 9 makes the proposal **signable** (customer products + PV representation). The road is sequenced, not scheduled — cadence is reviewed with Karim monthly against the pipeline.

**Process rules for every wave:** plan first; small testable changes; frozen-contract changes are deliberate seam events; docs+changelog after; candidate issues created via `/create-issue` at wave start. Waves overlap deliberately — each bullet carries its lane (Oli / Olly / joint), and later-wave lanes may start once the joint schema events they depend on have landed, not when the prior wave closes. The **dense-grid bake-off (Wave 6) is a re-runnable harness**: re-running it is an acceptance item of every subsequent wave that touches the solver objective, constraints, or the finance metric being optimised.

### Wave 6 — Sizing scope *(engine lane; in flight on `agent/sizing-search`)*
Finish [issue 016](issues/016-adaptive-sizing-search.md) to its acceptance bar (stages 2–9: site-scaled bounds, continuous evaluator, parallel LP harness + benchmark, adaptive refiner certified against the dense exact grid). Three review-mandated scope additions are recorded **in the issue file** (kept in sync with this wave): the duration-key interpolation policy (a versioned decision that supersedes D22's flooring sub-decision, with golden fingerprints re-baselined), covenant-feasibility-aware refinement (injected-DebtTerms cases in the bake-off; the *deliverability* carve arrives with Wave 7 and re-runs the bake-off there), and the LHS band-clamp fix.
*Acceptance:* per issue 016 — adaptive optimum == dense-grid optimum within tolerance; determinism goldens; the evaluator records which objective metric it optimised (levered return vs priced-floor fallback).

### Wave 7 — The firming substrate + the bill headline *(the load-bearing wave)*
The single common substrate every firming product needs, built once at the seam:
- *(joint)* A market-agnostic **`AvailabilityCommitment`/window schema** (days/hours/season, committed kW, held kWh, service id, contract years) and a typed `parameters` payload on `DispatchConstraintDescriptor` — descriptors become compilable, not just nameable.
- *(Olly)* **Wire `problem.constraints` into the solver**: a kind→builder table compiling `availability_window` (reserved net-injection headroom: `discharge_t − charge_t + C_w ≤ power` in committed windows, with a per-direction form for charge-side services), `held_state_of_energy` (`soe_t ≥ C_w·D_w` in windows), `deliverability`, `export_limit` — and absorb the two already-enforced hardcoded kinds (connection-limit, throughput-cap) into the same table so the seam is the single path. Keep the inner problem an LP wherever the commitment is fixed by the outer loop (no binaries needed for fixed windows); re-validate determinism, the ~2.5 s/solve budget, and re-run the Wave-6 bake-off with the deliverability carve.
- *(Olly)* **Populate the dormant fields**: `deliverable_capacity_mw`, `tender_window_discharge_mwh` from the solved model.
- *(Olly)* **The closed-form deliverability check** per sizing candidate: power × derate(duration) × (1−fade)^N ≥ committed MW at final contract year — arithmetic, no extra solves — producing a real `end_of_life_deliverable` verdict.
- *(Oli)* **The first `GateContext` producer** with provenance (an evidence object: who confirmed, contract/tender id, final year, verified de-rated MW — replacing bare booleans).
- *(Oli, parallel — pulled forward for the pilot)* **The deterministic bill model** (C017): tariff calendar × metered HH load, before vs after the solved schedule — both series already exist inside the MILP layer — so a record-backed proposal headline exists as soon as the substrate lands, not two waves later.
*Acceptance:* a dispatch with a committed window provably holds SoE and net-injection headroom through it (including the test that a full-discharge dispatch inside a committed window is rejected); at least one sizing candidate is REJECTED for end-of-tenor shortfall (M4's dormant criterion fires); CM/Piclo can flip CONDITIONAL→CONTRACTED on evidence; the proposal bill headline renders from the record on a T2 site; all still `not_bankable` on unconfirmed content.

### Wave 8 — Underwriting risk shapes *(Oli lane, after Wave 7's joint schema event)*
- **First: the assumption-schema evolution mechanism.** `SECTION_NAMES` makes all 23 sections required fields, so *adding* sections (availability, penalties, TNUoS) is a code+schema event, not content — decide and record the mechanism (optional sections or per-version section sets) so gb_v1_1 can grow without invalidating frozen releases.
- Extend `ValueStreamResult` (deliberate D8 joint event): availability-vs-utilisation revenue split, `contract_tenor_years`, penalty exposure (a producer-side change — finance already consumes `annual_cost_gbp`).
- **Firmness-differentiated P90** (per-firmness haircut keys) + a deterministic worst-case **penalty cost line** — CM non-delivery is bounded by published caps, computable with no modelling invention *once the cap values enter the assumption library as sourced, versioned entries* — + contract-tenor cliffs in cashflows.
- **Contracted-vs-merchant CFADS split** in `CashflowModel`, per-basis covenants in `assess_debt`; DSCR stays unavailable until Sim's gearing lands (B001) — the gate does not soften.
- **Consume `perfect_foresight_haircut_pct`** as a labelled interim P50 adjustment (it is in the library and currently ignored).
- Cut **gb_v1_1**: reconcile the v0_1/v1_0 divergence (`credit_rate_tenor_policy`), add the firming sections (availability prices, penalty regimes, outage factor, **TNUoS residual** — Wave 9's bill decomposition needs the content) as labelled placeholders. Scenario multipliers: scale merchant revenue only, via an **additive** field (e.g. `merchant_price_multiplier`) or a recorded decision superseding D13's semantics — plus the cross-lane attribute test D13 recommends. Feed `run_scenario_set` output into the memo's dormant sensitivities slot.
*Acceptance:* memo shows a penalty-aware, firmness-differentiated P90 and a contracted/merchant split a lender can read; every new number arrives via the library; C020/C023 (per-assumption source in outputs) closed alongside.

### Wave 9 — Customer products & site completeness *(mixed lanes)*
- *(joint — a deliberate D8 frozen-`SiteModel` event, flagged as such)* **Explicit PV representation**: generation series on `SiteModel` + ingestion, PV-battery self-consumption dispatch, and a G99 export-*limit* constraint. **None of this is RTM-gated** — only merchant export *pricing* is (Wave 10).
- *(Olly)* **Price the connection-headroom primitive**: a max-import decision variable (or shadow price on the min(MIC,ASC) bound) + an ASC-reduction/exceedance-avoidance stream — FIRM, no-counterparty. This prices the connection primitive only (the seam Stage 2 will later reuse; Stage 2 itself stays out of scope).
- *(Oli)* **Fixed/capped-bill and savings-share structures** with explicit volume-variance downside keys — noting the hard dependency: savings-share and capped-bill products gate on the **M&V method** (boundary Open Q4 — owner unresolved; PE ships the M&V data contract only). Labelled pending until each input is real.
- *(Olly)* Move the GB charge calendar (`tariff_calendar.py` windows) into versioned assumption content, with charging-regime change as a scenario dimension.
*Acceptance:* the proposal headline is a record-backed number, never "pending", on a T2 site; a PV site's self-consumption is modelled explicitly, not just embedded in net metering; the un-firmable residual is shown, not hidden; the UI dispatch chart gains the *held available* state.

### Wave 10 — Open the merchant stack *(externally gated)*
Code readiness that waits on partnerships, built so promotion is an assumption bump: merchant export pricing through the `export_t` machinery; wholesale price-series shape (D20/C012) wired from `ForecastReference` into dispatch; per-service discharge allocation variables (Σ_s d_{t,s} = discharge_t) replacing post-hoc max(0,·) attribution once >1 dispatch-revenue service exists — retiring the `valuation.py` stream-id elif chain behind per-stream hooks at the same time; the **dispatch-envelope export + M&V data contract** (the operate handoff of §1).
External (Karim/Zara-led, PE consumes): the RTM aggregator (B002 — also the first third-party `GateContext` facts and the operational data feed), NESO de-rating + clearing series, Piclo per-DNO tenders, vendor degradation curves, Modo (or equivalent) forecast subscription, Sim's gearing (B001), the M&V-method owner (Open Q4).
*Acceptance:* when a gate opens, the stream prices from a confirmed assumption version with **zero code change** on the pricing path — the promotion path is exercised for real.

### Wave 11 — Calibration & the flywheel (M7) *(Olly lane; realised data externally gated)*
A **simulation-only** causal dispatch policy — receding-horizon or rule-based, **never deployed to hardware; it exists solely to calibrate the perfect-foresight haircut** (a decisions.md entry records this boundary clarification) — backtested over the same metered year; its information set (what forecasts the causal policy sees) is a named design decision at wave start. The measured achievable/LP value ratio per stream becomes the calibrated, versioned haircut. Lifecycle transitions wired into the app (quotes stop being born INDICATIVE and staying there).
*Acceptance — in our control:* the P50→P90 machinery cites backtest evidence; per-stream haircuts versioned into the library. *Externally gated (like Wave 10's):* the first realised-vs-underwritten rows flow from the partner feed. This is the moat-compounding wave: in our (unverified) survey we found no comparator with per-stream calibration tied to provenance-stamped quotes.

**Continuous, alongside every wave:** assumption sourcing per [assumptions_to_source.md](assumptions_to_source.md) (every confirmation = version bump), the manual benchmark capture, and the W8 speed targets set and measured from telemetry.

## 6. What does not move

- The five non-negotiable rules ([boundary §5](00_project_boundary.md)): no LLM on the pricing path; deterministic pricing; full traceability; AI on the perimeter only; market-agnostic core.
- The honesty discipline: placeholders force `not_bankable`; missing data → ranked asks, never invention; DSCR unavailable rather than guessed; gated upside never in the priced floor.
- The boundary: RTM commercial terms are Karim's, gearing/credit policy is Sim's, the pipeline is Zara's, no EMS/autonomous operation is built here (we define data contracts only), and **Stage 2 compute shells stay out of scope — GridQ prices the connection-headroom primitive only**.
- The seam discipline: `SiteModel`/`OptimisationResult` change only by deliberate seam events; everything else rides additively (hooks + hash-excluded `QuoteRecord` fields) until then.

## 7. Using this document

- The session-start team-sync reconciles requests against **this goal** as well as the active plan; clashes get flagged, not silently absorbed.
- Wave starts: `/create-issue` the wave's items in house format; plan before build; decisions land in [decisions.md](decisions.md). Wave-level detail lives in issue files (the build specs); this document holds the goal, the promises, the bar, and the sequence — it should not need editing when implementation detail shifts.
- Review lenses for firming work: the grid promise (is capability *reserved*?), the lender promise (is deliverability *computed*? is downside *penalty-aware*?), the customer promise (is the bill *real*?), and always W7 (is it *honest*?).
- Adopted 2026-07-01 (D27). The §1 "whose engine" working assumption stands unless Karim overrides it.
