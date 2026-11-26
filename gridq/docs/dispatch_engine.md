# Dispatch engine (M3) + sizing optimiser (M4)

**Status:** real engine built (Wave 4, W4-1, decision **D22**). The deterministic half-hourly dispatch
**MILP** (`linopy`/HiGHS) lives in `gridq.optimisation` and is the **default engine for real runs**.
The integration-only `SimpleSolver`/`BaselineEngine` remain the labelled fallbacks. (D17 froze the
contract; D21 made the sizing loop's objective real.)

> **One line:** the real `MilpDispatchSolver` dispatches one fixed system size against the site's
> half-hourly load and time-of-use tariff; `MilpEngine` composes it with the sizing loop and drops in
> behind the frozen platform↔engine seam with **no change** to finance, quotes or outputs. On the `gb_v0_1`
> placeholder library every quote stays honestly **not bankable**.

---

## 1. Two layers, one principle

GridQ keeps a hard line: the optimiser is the only thing that turns physics + prices into a number,
and **no number a lender relies on is ever invented**. There are two seams:

| Seam | Where | What it is |
|------|-------|------------|
| **Outer** | `gridq.engine` — `OptimisationEngine.solve(request) -> OptimisationResult` | Site → request → one `OptimisationResult`. Frozen platform↔engine contract. `BaselineEngine` (placeholder) and **`MilpEngine`** (real, default) both implement it. |
| **Inner** | `gridq.dispatch` — `DispatchSolver.solve(problem) -> DispatchResult` | Dispatch **one fixed system size** against load/price/constraints → energy accounting + per-stream value. `SimpleSolver` (placeholder) and **`MilpDispatchSolver`** (real) both implement it. |
| **Outer loop** | `gridq.sizing` — `enumerate_sizing(...)` | Enumerate the size grid, call the dispatch solver + finance per candidate, select the best feasible size. |

The production `OptimisationEngine` is **the sizer composed with the real `DispatchSolver`** —
"one engine, called twice" (M4 over M3). `MilpEngine` is exactly that composition.

---

## 2. The real engine (`gridq.optimisation`)

| Module | Role |
|--------|------|
| `milp_solver.py` | `MilpDispatchSolver` — the linopy/HiGHS half-hourly MILP (inner seam). |
| `schedule.py` | `HalfHourlyDispatchSchedule` — the solved HH series + the aggregate quantities value functions read (the value-stack `DispatchScheduleLike`). `dispatch_schedule_ref` is its content hash. |
| `valuation.py` | Per-stream £ from the solved schedule (the dispatch engine's own valuation). |
| `tariff_calendar.py` | GB tariff band → half-hour time-window calendar (**content, never a price**). |
| `engine.py` | `MilpEngine` — the outer `OptimisationEngine` (sizer + MILP + indicative selection). |

### 2.1 The MILP (per half-hour `t` over the metered horizon, Δt = interval/60 h)

- **Variables:** `charge_t`, `discharge_t` (kW, AC side), `soe_t` (kWh, state of energy).
- **SoE dynamics + round-trip efficiency:** `soe_t = soe_{t-1} + charge_t·RTE·Δt − discharge_t·Δt`
  (cyclic), using `round_trip_efficiency.ac_round_trip`. This makes total grid charge =
  total discharge ÷ RTE — the exact convention finance grosses up by (one AC round-trip number).
- **Gap segmentation (D31):** the SoE recurrence applies only between genuinely adjacent
  half-hours. At any timestamp discontinuity (missing intervals, duplicate stamp, irregular step)
  the series is segmented and each contiguous segment gets its own cyclic wrap — **SoE is never
  chained across a gap**, so the optimiser cannot charge before a data hole and discharge after it
  as if no time passed. A gapless series reduces exactly to the single global cyclic constraint;
  segmentation is surfaced as a solver warning. Unordered rows are sorted by timestamp first.
- **Connection (MIC/ASC):** `charge_t ≤ min(MIC, ASC) − site_load_t` — battery charging counts
  against the import connection on top of the site's own load.
- **Behind-the-meter:** `discharge_t ≤ site_load_t` (no export on the priced floor — export/wholesale
  is gated upside) and `charge_t, discharge_t ≤ power`.
- **Warranty throughput (HARD):** annual full-cycle-equivalents ≤
  `warranty_cycle_limits.max_cycles_per_year_by_duration_h` (per-duration, conservative flooring);
  overcycling is not available to the optimiser.
- **Objective — the site's own bill:** maximise `Σ price_t·(discharge_t − charge_t)·Δt`, where
  `price_t` is the site's time-of-use energy rate (`import_rates`) + DUoS rate (`duos_*_rate`) placed
  in time by `tariff_calendar`. Round-trip-loss energy is bought at the price faced (the `charge_t`
  term). Three negligible tie-break regularisers (not prices) break the remaining objective ties:
  one toward less throughput (no pointless cycling), one toward a lower residual **peak net
  import** — an epigraph variable over `load + charge − discharge`, the exact quantity the
  customer bill's `peak_import_after_kw` reports, so within an equal-price band tied placement
  levels rather than stacks and the firming deliverable is protected by construction — and a
  strictly-subordinate discharge-only lean toward discharging earlier (the calendar-ageing
  direction). Placement the peak binds is unique by construction; sub-peak placement stays
  solver-chosen and machine-deterministic (D38).

The metered horizon may be partial; energy/throughput/value are scaled to a full year over the
**present** intervals only (missing intervals are excluded, never back-filled).

### 2.2 Per-stream value (computed in the dispatch engine)

The value stack is consulted only for each stream's **gate** (firmness / gate-status / unlock) and
its constraint descriptors; the **£ is computed here** from the solved schedule + the site tariff
(the "site-derived value lands with the dispatch engine" the value-stack docs always promised).
Attribution partitions the objective with no double-counting:

| Stream | Value (gb_v0_1) |
|--------|------------------|
| Tariff arbitrage | energy-band bill reduction over the schedule · **firm** floor |
| DUoS red-band avoidance | DUoS rate avoided by discharging in red windows · **firm** floor |
| MIC/ASC headroom | **£0** — realised through the connection-bounded arbitrage (pricing it separately would double-count) |
| PPA / bill savings | £0 aggregate (the PPA £/kWh is finance's, not the stack's) |
| Capacity Market / local flex | conditional upside (assumption × schedule aggregate), held out of the floor |
| Wholesale / BM / ancillary | gated upside · £0 until a route-to-market partner lands |

No value-stack file is modified — the `£/MW/yr` proxies remain a labelled fallback for callers that
do not run the MILP. Parasitic-load and a degradation *marginal-cost* objective term are **omitted**
in v1 (no lifetime-throughput / £-per-cycle number exists — the warranty throughput cap carries the
cycling constraint; omission is honest, not invented).

### 2.3 The tariff calendar (the one piece of new GB content)

`tariff_calendar.py` maps each half-hour to a DUoS / time-of-use band (post-TCR red/amber/green,
day/night, peak/off-peak) so the site's *own* band rates can be placed in clock time. It is a
**time-window constant, not a price** — every £/kWh comes from the site tariff. Unrecognised band
labels or a flat tariff degrade to "no time-mapped arbitrage" with a warning (never an invented
window). The windows are an indicative GB default; a future assumption version can override them.

**A flat tariff is a fact; an absent rate is a defect — and the two must never share a path (D45,
[`decisions.md`](decisions.md)).** A genuinely flat tariff has no time-of-use spread, so "no
time-mapped arbitrage" is the true answer. A *missing* `duos_red_rate`, or an unpopulated
`import_rates`, is different in kind: the rate exists at the site, and reading its absence as £0/kWh
silently deletes that component from the objective's `price_t`, so the optimiser is solved against a
price the site does not face and cannot see value that is really there. Downstream, the same absence
prices `DUOS_RED_BAND_AVOIDANCE` — a **priced-floor** stream — at £0 that enters P50, P90 and the DSCR
looking measured. So an absent rate is sourced or derived-and-declared **before** the solve; where it
genuinely cannot be, the affected £ is refused rather than zeroed, and the weak result that follows is
a finding about the **pipeline**, not the site.

---

## 3. Determinism

Identical (inputs, assumption version, seed) → identical `DispatchResult.model_dump()` and identical
`SizingRun.fingerprint()`. HiGHS runs **single-threaded** with a **fixed random seed**, deterministic
presolve, and the **interior-point method + crossover** (the annual throughput cap is one dense
constraint over every half-hour, which makes dual simplex pathologically slow on a full year — ~15 s;
IPM reaches the identical optimum in <1 s and the crossover yields a clean vertex schedule). All
extracted quantities are rounded. No LLM, no wall-clock, no RNG on the path.

`linopy`/`highspy` are core dependencies; **`xarray` is pinned `<2025`** because linopy's expression
layer is incompatible with the 2025+/2026 xarray `Dataset` constructor — this pin is part of the
reproduction contract.

**Benchmark:** a full metered year (17 472 half-hours) solves in **≈2.5 s** per dispatch; a sizing
run is one solve per candidate (the default grid is 3 powers × 3 durations). Recorded by
`tests/test_optimisation_engine.py::test_benchmark_full_year_dispatch_solve`.

---

## 4. Honest degrade (never raises on a valid site)

The solver reports insufficiency via `solver_status` + `warnings`, never an exception:

- **No metered half-hourly series** (`load_data.half_hourly_file_ref` absent) → `INSUFFICIENT_DATA`,
  no spec, `None` volumes — a real dispatch cannot run on summary stats, and a load shape is never
  invented.
- **No observed peak** (`load_data.max_import_kw`) → `INSUFFICIENT_DATA` (cannot bound charge against
  the connection).
- **Flat tariff** → arbitrage gate closed, £0 (DUoS red-band can still accrue).

`bankable_status` is status-driven: never `bankable_candidate` from the dispatch layer, and
`not_bankable` whenever any used assumption is placeholder/needs_review — so on `gb_v0_1` it is
always `not_bankable` (the final bankable verdict is finance's: DSCR + covenant).

---

## 5. The dispatch-energy seam (resolves C010 / decision D13)

Finance needs two annual volumes it does not own: **charge kWh** (levies-on-charge, incl. losses)
and **discharge kWh** (the delivered energy for the fixed-£/kWh price-to-target). The real MILP fills
`DispatchResult.annual_charge_kwh` / `annual_discharge_kwh`; `to_finance_dispatch_energy(result)` maps
them onto finance's injected `DispatchEnergy` hook. The sizing loop injects this per candidate, and
`MilpEngine` also exposes the selected candidate's volumes via `selected_dispatch_energy()`, which
`generate_quote` threads into finance (additive optional `dispatch_energy` argument). So levies +
price-to-target now compute on both the sizing and the quote paths — with **no field added to the
frozen `OptimisationResult`** (D13: that promotion stays the deferred deliberate seam event). The
placeholder still reports both volumes as `None`, so finance stays honestly *unavailable* there.

---

## 6. The sizing outer loop (M4)

**The engine default is the legacy 3×3 enumeration.** The two-stage continuous search (D32, §6.1)
is fully built but **opt-in** (`MilpEngine(use_two_stage_search=True)`) per the **C025 gate**
(north-star D29): on per-MW-only capex any wide-duration search pins the recommendation to the
band edge, so a continuous recommendation would be a placeholder artifact, not an optimum. The
enumeration primitives below are the shared foundation (`_evaluate_spec`, the objective,
`SizingRun`) for both paths.

- **`SearchSpace`** — a validated power × duration grid. Power candidates must be supplied
  (site-specific scale, never invented); the duration grid defaults to the structural set
  `(1, 2, 4)h` that the assumption library is keyed on. Negatives/zero rejected; de-duplicated;
  sorted; energy derived (`power × duration`); deterministic `(power, duration)` ordering.
- **`SizingObjective`** — the bankable objective: **maximise levered return subject to P90 DSCR ≥
  covenant** (W4-2, decision D21). The covenant is *embedded* (a feasibility gate), so a candidate
  it would reject is never selected. The **primary metric is the levered (equity) return** — the
  finance model's equity IRR on the P50 base case (`FinancialCase.equity_irr`), computed whenever
  real `DebtTerms` are injected; with no debt there is no levered split, so it falls back to the
  unlevered project IRR (a labelled fallback that never asserts a bankable best) — never an invented
  number. `feasible` also rejects a spec *known* non-deliverable to end-of-life
  (`end_of_life_deliverable is False`); an unassessed verdict (`None`, the placeholder case) is a
  data-ask, never a fabricated pass (D12). The DSCR gate is real: with injected `DebtTerms` it
  genuinely fires and rejects sub-covenant candidates.
- **`enumerate_sizing(...)`** — for each candidate it calls the shared per-spec primitive
  `_evaluate_spec`: build a `DispatchProblem`, solve via the injected `DispatchSolver`, map the
  result onto a per-candidate `OptimisationResult` (the frozen contract — so finance/outputs consume
  it unchanged), run the deterministic finance model (passing the dispatch-energy hook), score
  against the objective. Returns a `SizingRun` (`candidates_evaluated`, `selected`,
  `rejected_by_covenant`, `bankable`, warnings; `fingerprint()` is timestamp-free). A per-candidate
  unpriceable result (`InsufficientInputsError`) is caught and recorded, never aborts the run.
- **`underwrite_spec(...)` — "one engine, called twice" (consistency contract #1).** Sizing searches
  specifications; underwriting prices the chosen one. `underwrite_spec` reconstructs the dispatch +
  finance call path **independently** from the raw `(site, assumptions, spec)` inputs and reprices
  one fixed spec through the *same* `_evaluate_spec` primitive, so the chosen spec reprices to
  identical numbers — sizing and underwriting cannot drift, and the optimiser cannot select a spec
  underwriting would reject. `financial_fingerprint(evaluation)` is the timestamp-free projection a
  consistency check compares (load-bearing numbers + `run_context.input_hash`, never `created_at`).

### 6.1 The two-stage continuous search (D32, opt-in behind the C025 gate)

`MilpEngine._run_search` with `use_two_stage_search=True` sizes via
`gridq.optimisation.two_stage.two_stage_sizing` (the demo stays on the legacy enumeration — a
customer-visible surface must not present a band-edge placeholder artifact as an optimum):

- **Stage A — coarse sweep.** 10 labelled structural power fractions spanning **5–150% of the
  site's binding capacity** (observed peak, else connection limit — never an invented kW) × 8
  durations **0.5–8 h**, scored against a **typical-period proxy**: one medoid day per
  (month × weekday/weekend) stratum of the site's own metered series, weighted by stratum size
  (`gridq.sizing.typical_periods`; only complete gap-free days qualify; nothing filled or
  invented). Weighted solves run through the same `_solve_milp` via an `interval_weights` array
  (objective, annualisation, warranty cap — `None` is byte-identical to the unweighted model) and
  weighted `HalfHourlyDispatchSchedule` aggregates, so valuation + finance consume them unchanged.
- **Stage B — seeded refinement.** An in-house deterministic differential-evolution loop
  (`gridq.sizing.refine`: fixed seed, memoised, rounded candidates, ~200-evaluation budget) over
  the same continuous box, seeded with the coarse bests and ranked by the unchanged
  `SizingObjective` (covenant gate first). Duration-keyed assumption tables keep the
  conservative-flooring `value_for_duration` lookup per the adopted north-star D28 (interpolation
  is deferred to sourced assumption content). CAUTION: the warranty table DECREASES with duration,
  so flooring an off-node duration relaxes the cycle cap — flagged as F-002 / C030 for the joint
  interpolation-policy decision.
- **Stage C — confirmation.** A DIVERSE set of top-ranked candidates (up to 10; near-clones
  within 2% on both axes are skipped) re-solve on the **full metered
  series**. Only confirmed evaluations are selectable or reportable: proxy results are labelled
  (`stage`/`proxy` on `CandidateEvaluation`/`SizingGridPoint`), carry loud provenance warnings,
  and are excluded from the engine's indicative fallback — **no reported number ever comes from
  an approximation**.
- **Stage D — polish.** A bounded deterministic compass search on the full series around the
  best confirmed candidate (±5% per axis, step halving, hard-capped probes) recovers the true
  peak where the typical-day proxy ranks the optimum's neighbourhood slightly wrong — the
  best FEASIBLE confirmed candidate wins, never merely the first to pass.

One CSV read (one confidential-access log entry) serves the whole search
(`gridq.optimisation.proxy_solver.PreloadedDispatchSolver`). The whole evaluated cloud (coarse +
refine + confirm) persists via `SizingRun.sizing_grid()` under
`SizingGrid.search_version = "m4-two-stage-v2"` — a true continuous sizing surface. Determinism is
pinned by test: `tests/test_two_stage_sizing.py` runs the search twice and asserts identical
fingerprints. When no MILP solver or readable series is available, `MilpEngine` falls back to the
legacy 3×3 enumeration (`POWER_FRACTIONS` × `DEFAULT_DURATION_HOURS`), whose per-candidate
`INSUFFICIENT_DATA` degrade is unchanged.

In both modes the engine selects the covenant-feasible best — or, when no candidate passes the
covenant (e.g. no injected debt terms on `gb_v0_1`), surfaces the best **indicative** candidate
(highest priced-floor value; smallest system as the deterministic tie-break; never a proxy
evaluation) as `not_bankable`. A run on placeholder assumptions stays `bankable=False` by
construction (unconfirmed assumptions), however good the modelled return looks.

**The full evaluated grid is exposed for the 3D sizing surface (W5-0, decision D23).** `SizingRun`
retains every evaluated candidate (`candidates_evaluated`), and `SizingRun.sizing_grid()` projects it
into a serializable `gridq.schemas.sizing.SizingGrid` (each `SizingGridPoint`: power, duration, energy,
levered/equity return, project IRR, P90 DSCR, the `passes_covenant` feasibility flag, solver status —
plus the chosen optimum and the run covenant). `MilpEngine` exposes it via a duck-typed
`last_sizing_run()` hook **mirroring `selected_dispatch_energy()`** (§5), which `generate_quote`
persists onto the additive, Optional `QuoteRecord.sizing_grid`. As with the dispatch-energy seam, **no
field is added to the frozen `OptimisationResult`** (D13), and the field is excluded from the quote's
`input_hash` so the addition is backward-compatible. Nothing is invented: an unavailable metric stays
`None` and the optimum is honestly `None` when no candidate clears the covenant.

---

## 7. Still deferred (named so the producer does not read it as complete)

- **Wholesale/Modo half-hourly price series** (D20) feeds only the *gated* upside, which the floor
  never prices; its field shape stays deferred until that route-to-market lands. The floor's price
  signal is the site tariff, which is present today.
- **Band→window calendar as versioned assumption content** (currently an indicative code default).
- **Promotion of the two annual volumes onto `OptimisationResult`** (D13, jointly with Olly).
- **Capacity-Market final-year deliverability horizon** (D12) — the engine supplies a duration-de-rated
  capability; the multi-year end-of-life verification is a sizing/degradation output.
- **Warm-start / parallel candidate solves** and **shadow-price valuation of MIC/ASC headroom** — only
  if the sizing-run benchmark demands them.
