# 017 — Power provisioning: the battery as load-serving capacity

**Status:** **BUILT through stage 6's buildable scope** (2026-07-11, D42 owner authorization): stages 1–2 (schema + derivation), 3 (`load_service` forced-draw compile), 4 (sizing floors + D39 verdict), 5 (LOAD_SERVICE stream + measured opportunity cost on the record) and the HGV-depot end-to-end validation are landed and green; remaining: richer proposal/UI treatment of the provisioning story + Olly's after-the-fact review of D42/C033/C034.
**Owner:** joint — schema/derivation/finance/outputs (Oli side), solver kind + sizing floors (Olly side, M3/M4).
**Origin:** Karim, 2026-07-09. north_star "firm the customer", taken to its physical limit; sibling of C028 (PV series) and C029 (connection-headroom objective) — the three seam decisions should land as one package.

---

## 1. Product definition

A site cannot draw the power it needs from the grid in some window (e.g. 18:00–19:00: desired demand exceeds the connection's deliverable import — MIC/ASC, or a DNO-imposed windowed cap). The battery is configured as **capacity**: it guarantees to discharge in that window to serve the unmet load, and recharges **only through the same constrained connection, in the gaps**. We must be able to (a) model the obligation as solver physics, (b) verify it is deliverable worst-case at end-of-tenor, (c) **size around the limit** — the requirement sets a hard feasibility floor under the sizing search — and (d) price it honestly.

This inverts the engine's core assumption. Today load is exogenous and *already served*: the battery only reduces import (`discharge ≤ min(power, load)`, `milp_solver.py` no-export floor) and the connection limit only caps *charging* headroom. Provisioning makes the battery **load-serving infrastructure**: if it arrives at the window short, the site trips. That is a feasibility guarantee first and an economic optimisation second.

Works best for **peaky load** (short, tall unmet windows → modest energy floor, ample recharge gaps). Flat overload (long windows, thin gaps) drives the energy floor up and the recharge headroom down until the honest answer is *no viable configuration — reinforce the connection*. That answer is part of the product (D39 made it a first-class verdict).

## 2. Why this is hard — the five problems the design must answer

1. **The unmet load is invisible in our own data.** The meter records what got through the constrained pipe; clipped demand and never-installed machines don't appear. The core input is therefore a **declared requirement** (customer statement with provenance), never a measurement we can take. No-invention discipline applies in full.
2. **A guarantee is a worst-case property; the dispatch LP is a single-year perfect-foresight optimum.** The obligation needs pre-positioned energy for the *worst* window at *end-of-life* fade with the battery *available* — an exceedance basis over the load distribution, not the trace the optimiser happens to see. Economics stays LP; reliability is a separate deterministic worst-case layer that feeds the LP constraint levels.
3. **Recharge feasibility through the same constrained pipe.** Refill energy comes only from `import_cap − load` gaps between windows. A closed-form daily (and multi-day running-deficit) energy-balance check must pass before a candidate is even solvable — and its failure is a named no-viable reason, not an LP crash.
4. **Same golden hours as the grid-side products.** 18:00–19:00 is the DUoS red band and the CM stress window. Serving unmet load *is* import reduction (coexists with peak-shaving), but the same kW must never be simultaneously reserved for the site and sold to a flex tender/CM. Prerequisite met: the compiler now **aggregates** overlapping reservations (`availability_reserve_aggregate`) and validates commitments against the in-window deliverable envelope. The `load_service` reservation joins that aggregate — reservations SUM.
5. **Valuation without inventing a number.** The *cost* is internal and computable: solve the selected spec with and without the obligation; the priced-floor delta is the deterministic opportunity cost. The *value* is the customer's **declared counterfactual** (DNO reinforcement quote, genset, lost production) with provenance — supplied, never synthesised; absent → the stream prices £0 and only the measured cost is reported.

## 3. Inputs — the C033 seam (frozen `SiteModel`; joint event, filed in OPEN_COMMENTS)

New schema object (sibling module to `gridq.schemas.commitment`):

```
ProvisioningRequirement
  requirement_id: str
  windows: list[AvailabilityWindow]          # REUSE the W7 calendar shape verbatim
  additional_load_kw: float | None  (>0)     # declared NEW load inside windows (case b)
  serve_metered_shortfall: bool = False      # cover max(0, metered − import_cap) too (case a)
  import_cap_kw: float | None                # window-effective cap; None → site max_import_kw
  tenor_years: float | None
  counterparty: str | None
  reference: str | None                      # the customer contract — evidence gate key
  declared_counterfactual_gbp_per_year: float | None   # DNO quote / genset / lost production
  counterfactual_basis: str | None           # REQUIRED when the £ figure is present
```

Design rules: the **unmet series is derived, never declared** — `unmet_t = max(0, load_t + additional_t + aux_t − import_cap_t)` — so the declared part is minimal and auditable (a flat kW addition per window in v1; a declared half-hourly desired-load series ref is v2, shaped like C028's generation ref). `SiteModel` gains `provisioning_requirements: list[ProvisioningRequirement] | None = None` — additive, default None, hash-stable for absent fields (verify against `assembly._canonical_run_input`, same check C028 named). Olly co-decides: field names, flat-kW vs series v1, whether the requirement rides `SiteModel` or the `OptimisationRequest`.

## 4. Deterministic derivation layer (new module `gridq.sizing.provisioning` — pure functions, no solver)

All closed-form, all deterministic, all testable without HiGHS:

- **Unmet series**: as above, from the metered trace + declared addition + cap.
- **Window statistics**: per-window unmet peak kW and unmet energy kWh across every occurrence in the observed year; the **exceedance basis** picks the level — default `max_observed` (conservative; assumption-configurable home `provisioning.exceedance_basis`, D30 optional-section mechanism). Permanent warning: a single observed year understates the tail — additional years are a ranked data ask, never extrapolated.
- **Required floors** (end-of-tenor, reusing the W7-3 closed form):
  `power_floor = unmet_peak_kw / (1 − fade)^N`;
  `energy_floor = unmet_window_energy_kwh / (1 − fade)^N` — **in the solver's own SoE terms**: the dispatch LP books round-trip losses entirely on the charge side (`soe += charge·RTE·Δt − discharge·Δt`), so there is NO discharge-side η division; the RTE enters on the recharge side instead. (No CM derate either — that is a market payability concept, not a physics one; serving own load is metered at the boundary.)
- **Recharge feasibility**: per observed day, gap energy `Σ max(0, import_cap_t − load_t − aux_t) · dt · η_charge` between windows must cover the window's unmet energy; a running multi-day deficit scan catches consecutive tight days. Output: pass / fail-with-worst-day, feeding both the sizing floor and the no-viable reason.
- **Availability**: enters from the `availability` assumption section (the empty D30 home). Empty section + a provisioning requirement priced/asserted → loud data-ask warning (the W8 penalty-section pattern). No availability number is ever defaulted.

## 5. Solver physics (Olly, M3) — one new compiled kind: `load_service`

Registered in `_COMPILED_KINDS`, payload = the requirement (or a `ProvisioningCommitment` view of it), following the W7-2 builder pattern exactly:

- **Load balance grows the declared addition**: inside windows the effective load is `load_t + additional_t`. Consequences, both automatic in the existing formulation once effective load is substituted: `discharge_ub = min(power, effective_load)` (the product IS serving load — no-export floor untouched) and `charge_ub = max(0, import_cap − effective_load − aux)` (recharge only through the real gaps — Karim's stated constraint, already how the formulation works).
- **The obligation floor**: `discharge_t − charge_t ≥ unmet_t` on window instants (mask via `_commitment_mask`). With the §4 pre-checks passed, the floor is feasible by construction; the compiler still applies the house rule — *an infeasible commitment is compiled anyway and warned, never silently dropped*.
- **Pre-positioning**: `soe ≥ energy_floor_window` on the instants immediately preceding each window occurrence (the held-SoE builder, level from §4's exceedance basis — NOT from the LP's own trace optimum; this is where problem 2 is answered).
- **Reservation aggregation**: the in-window committed kW joins `availability_reserve_aggregate` so grid-side commitments in overlapping windows can never double-sell the same capability.
- **Absent payload → byte-identical no-op** (the golden-preserving pattern every W7 kind followed).
- **Validation instrument**: Olly's opt-in MPC rolling-horizon engine re-runs the selected spec *without* foresight to confirm the SoE floors are attainable in causal operation — evidence for the memo, not a pricing input.

## 6. Sizing around the limit (M4)

- `SizingDomain.from_site` gains optional provisioning floors: the continuous search explores only `power ≥ power_floor`, `energy ≥ energy_floor`, intersected with the C-rate band and the recharge-feasibility bound. Candidates below the floor are excluded *before* economics (never solved, never scored).
- Floor ∩ domain = ∅ → **`NO_VIABLE_CONFIGURATION` with a named provisioning reason** ("cannot serve the window and recharge through this connection at any size in the domain") — extending D39's verdict, rendered first-class in the UI.
- The objective is unchanged (metric tiers per D29/D34). Provisioning enters as constraints plus a value stream — not a new objective. The sizing surface marks the provisioning-infeasible region distinctly (the feasibility band already exists in the surface view; it gains a reason label).
- End-of-tenor deliverability (W7-3) runs per candidate with `committed_kW = unmet_peak`, `held_kwh = energy_floor` — the year-N degraded check the drive already proved can reject covenant-passing candidates.

## 7. Valuation & finance (M2 interface / M5)

- New stream through the **generic market interface** (no core branch; the window calendar is content): `LOAD_SERVICE` (enum addition — additive).
- **Priced value** = `declared_counterfactual_gbp_per_year` **only when present with basis + reference**; firmness `FIRM` only when the customer contract reference exists AND deliverability verified (the W7-4 evidence gate pattern — `CommitmentEvidence` with the contract as reference); otherwise `CONDITIONAL` at the declared figure or **£0 with no counterfactual** — never an estimated £.
- **Measured opportunity cost**: one extra deterministic solve of the *selected spec only* without the obligation; the priced-floor delta is reported as `provisioning_opportunity_cost_gbp` (informational, additive hash-excluded field — D23/D25 precedent). Surface-wide with/without is deliberately out (doubles every candidate's solve time for information only the selected spec needs).
- **The bill stays honest**: with declared *new* load, "before" is a counterfactual the site never metered. Bill before/after remains computed on **metered load only**; the enabled load is reported as its own labelled line ("enabled load — not a bill saving"), never merged into savings. `CustomerBill.basis` carries the label.
- P90: the obligation is availability-risk, not price-risk — its P90 treatment keys on the `availability`/`penalties` sections (customer-contract penalty terms, when they exist) alongside the W8 machinery; empty sections → the established warned data-ask, nothing silently zero.

## 8. Outputs & UI

- Proposal: a provisioning block — "your site can run" (window, kW served, worst-day margin, year-N verification), separate from savings; the no-viable variant states the reinforcement recommendation plainly.
- Memo: obligation terms, exceedance basis, recharge worst day, opportunity cost, counterfactual provenance, MPC causal check result.
- Inspect page: provisioning-infeasible region on the sizing surface; the obligation listed with the other commitments; the D39-style verdict card when nothing is viable.

## 9. Operating principles — how each is respected

| Principle | Where |
|---|---|
| Deterministic core, no LLM on pricing | All layers closed-form or LP; the only new £ input is customer-declared with provenance |
| Never invent data | Unmet load derived from trace + declared addition; counterfactual requires basis + reference; single-year tail understatement is a warned data ask |
| Frozen contracts by joint event only | `SiteModel`/enum/result changes all filed as **C033**; nothing lands before Olly's sign-off |
| Market-agnostic core | `load_service` is a kind in the registry; windows/caps are content; no GB branch |
| Placeholders force `not_bankable` | Empty `availability` section + priced/asserted obligation → data-ask; bankability machinery untouched |
| Traceability | Requirement carries id/reference; floors and opportunity cost land on the record (additive, hash-excluded per D23/D25) |
| Honest failure | Pre-checks turn infeasibility into named no-viable verdicts; compiled-anyway-and-warned rule kept |
| Small testable slices | §10 stages, each with its own bar |

## 10. Staged implementation plan

- **Stage 0 — seams.** C033 filed (done, with this doc); Olly co-decides schema shape + solver-kind ownership; decide jointly with C028/C029 (same package: exogenous series, capacity objective, this).
- **Stage 1 — schema.** `ProvisioningRequirement` module + validators + example JSON + tests (standalone module first; `SiteModel` wiring only after C033 sign-off, with the hash-stability check named in §3).
- **Stage 2 — derivation.** `gridq.sizing.provisioning`: unmet series, window stats, exceedance basis, floors, recharge scan. Pure functions, exhaustively tested (peaky-feasible vs flat-infeasible fixtures; multi-day deficit case).
- **Stage 3 — solver kind.** `load_service` builder per §5 (Olly's module; pairing welcome). Tests: obligation provably binds (with/without delta), recharge only through gaps, absent-payload goldens byte-identical, warned-not-dropped infeasible compile.
- **Stage 4 — sizing floors.** Domain floors + no-viable reason + end-of-tenor reuse. Test: the D39 verdict fires with the provisioning reason on a flat-overload site.
- **Stage 5 — value + finance.** `LOAD_SERVICE` stream, evidence-gated firmness, declared counterfactual, selected-spec opportunity-cost solve, record fields. Tests incl. "no counterfactual → £0 priced, cost still reported".
- **Stage 6 — outputs/UI.** Proposal/memo/inspect blocks per §8; MPC causal-check note.

Each stage: full bar green (`pytest`, `ruff check`, `ruff format`, `mypy`, `review_gates`), changelog + handoff, goldens preserved except where a priced output changes honestly.

## 11. Open questions

1. v1 requirement shape: flat kW per window (proposed) vs declared HH series (v2)? — Olly, C033.
2. Requirement on `SiteModel` vs `OptimisationRequest`? (Proposed: SiteModel — it is a property of the site's contract position.) — Olly, C033.
3. Does the customer-side obligation take precedence over grid-side commitments when jointly infeasible, or is joint infeasibility simply no-viable? (Proposed: no-viable — we never rank two contracted obligations.) — joint.
4. Where does the declared counterfactual sit in Sim's portfolio view (avoided-capex vs revenue line)? — flag to Finance; our record stores it as a stream value with basis either way.
5. Windowed DNO import caps as first-class site data (schedule, source) — v2 alongside the tariff-calendar-as-content mechanism.
