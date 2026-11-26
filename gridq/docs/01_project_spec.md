# 01 · Project Spec — GridQ Product Engineer v1

**Status:** Working draft · **Date:** 2026-06-16 · **Owners:** Olly
**Reads with:** [docs/00_project_boundary.md](00_project_boundary.md) (scope/ownership), [CLAUDE.md](../CLAUDE.md) (operating rules), [`../source_docs`](../source_docs) (System Brief, plan, workstreams).

This is a practical Product + Engineering spec for the **first version** of Olly's GridQ underwriting tool. It defines what v1 actually builds, who owns each part, and how we know it works. It introduces no assumptions beyond the source documents; unresolved points are in [§10 Open questions](#10-open-questions).

---

## 1. Mission

Build the Product Engineer layer of the GridQ/Neura behind-the-meter (BTM) BESS sizing and underwriting system: a deterministic engine that takes one commercial site and produces three artifacts that are *consistent by construction* — the system specification, the bankable underwriting pack, and the customer proposal. v1 is a **local developer tool / web app** that proves the full pipeline end-to-end on a structured input and a mocked optimisation, with every number traceable to its input, assumption version and code version, and every run logged — so the deterministic core, the agentic perimeter and the telemetry discipline are real from the first quote, ahead of the production solver.

---

## 2. Users

| User | When | What they need from v1 |
|------|------|------------------------|
| **Oli** | Now | Build and run the underwriting platform: ingest a SiteModel, run the pipeline, inspect the quote record, generate the memo/proposal, read telemetry. Primary developer + operator. |
| **Olly** | Now | A clean `OptimisationResult` contract to build the real engine against, and a mock that honours it so finance/outputs can be developed in parallel. Primary developer of the engine. |
| **Karim / software team** | Later | A documented, modular codebase with schemas, acceptance tests and provenance they can extend and harden without reverse-engineering. |
| **Installer-facing commercial team** | Later | Fast, credible customer proposals (bill before/after, savings, term) generated from the run record — the artifact they sell with. |
| **Capital / investor reader** | Later | The underwriting pack (P50/P90, DSCR, contracted-vs-merchant, credit, gate status) plus the telemetry dataset (time-to-quote vs manual benchmark, hit rate by data tier) as the falsifiable cost-compression evidence. |

v1 is built for **Olly**. The later users define what must not be painted into a corner (clean schemas, traceable numbers, presentable outputs), not what v1 ships.

---

## 3. Product outcomes

The system should eventually produce three artifacts from a single run record, consistent by construction:

1. **System specification** — power, energy and duration optimised for the site (AC-coupled, battery-only in v1).
2. **Underwriting pack** — P50/P90 cashflows, DSCR sensitivities, contracted-vs-merchant split, counterparty credit, firm-vs-conditional stream table, gate/consent status, and the price that hits target return at the optimised dispatch.
3. **Customer proposal** — bill before/after, savings, term, structure, in plain language.

In v1 these are produced around a **mocked** optimisation, so the shapes, traceability and document generation are real even though the underlying dispatch is not yet the production MILP.

---

## 4. MVP scope

**The MVP is NOT the whole Neura plan.** It is a local developer tool / web app for Olly that proves the pipeline. The MVP can:

1. **Accept a structured `SiteModel` JSON** — the canonical input contract, supplied directly (no messy-format parsing yet).
2. **Validate it** — schema validation plus the physical/consent pass-fail gates, with clear, specific errors and a data-tier grade (T0/T1/T2).
3. **Run a mocked or simple placeholder optimisation** — a deterministic stub that returns a schema-valid `OptimisationResult` (chosen P·E·duration spec + a half-hourly dispatch/value summary) given a SiteModel. No production MILP.
4. **Run underwriting calculations around that output** — P50/P90 cashflows, DSCR, cost lines, the commercial-structure plug-in (fixed £/kWh default), and the price that hits target return.
5. **Generate a traceable quote object** — an immutable run record: inputs hash, data tier, assumption version, code version, optimisation result, finance outputs, gate/credit status, lifecycle state.
6. **Generate a draft underwriting memo and customer proposal** — from the run record; LLM drafts prose, every number injected from the record (never authored by the LLM).
7. **Log quote telemetry** — per run: data tier at intake, human-minutes, iterations, outcome, assumption/code versions — from the first quote.
8. **Keep all assumptions versioned** — one versioned assumption library, no private constants; a quote is valid only against the version that produced it.

A thin **web UI** wraps this for a demo flow (load SiteModel → run → inspect quote → view memo/proposal → see telemetry).

---

## 5. Non-MVP scope

Explicitly **excluded** from v1:

- **Full production MILP** — the real half-hourly dispatch optimiser on linopy/HiGHS (Olly's M3/M4). v1 uses a mock; the solve-time benchmark experiment is separate.
- **Real aggregator integration** — no live wholesale/BM/RTM API. Wholesale/P415 stays gated-upside placeholder.
- **Real Creditsafe integration** — unless an API key already exists; otherwise credit is a manual/stub input that still maps deterministically to rate/tenor.
- **CRM** — Growth's tool, not ours.
- **Installer MoUs** — Growth/Operations.
- **Fundraise materials** — Finance.
- **Stage 2 compute** — grid-smart compute shells are a future stage.

(See [docs/00_project_boundary.md §2](00_project_boundary.md) for the full ownership exclusions.)

---

## 6. Module map

Modules follow the System Brief's map (CORE = deterministic/shared; PERIMETER = agentic, schema-validated; CROSS-CUTTING binds every run). Owners per [CLAUDE.md §5](../CLAUDE.md). Acceptance criteria are the bar for "done" in v1.

### M0 — Ingestion · *Owner: Oli* · PERIMETER
- **Purpose:** Turn whatever an installer sends into a validated `SiteModel`, graded T0–T2, and return a ranked list of the data that would most improve the quote.
- **MVP:** Accept a structured `SiteModel` JSON directly; run schema + gate validation; assign the data tier (T0 = annual kWh + tariff + postcode, T1 = partial HH, T2 = ≥12 months validated HH). No AI parsing of arbitrary formats yet. *v1 working tier boundaries (to be confirmed):* T0→T1 = any genuine HH data present below 12 validated months; T1→T2 = ≥12 months of HH that pass completeness/plausibility validation — so the grading tests are deterministic.
- **Later:** LLM-assisted ingestion of arbitrary installer formats (CSV/PDF/email), the precise "ranked missing-data" ask, human-confirm loop.
- **Dependencies:** M1 (schema), X.1 (assumption versions for gate thresholds), X.3 (logs intake tier).
- **Acceptance:** A valid SiteModel JSON loads and grades correctly; an invalid one is rejected with specific, actionable errors; the assigned tier matches the data present.

### M1 — SiteModel / schema & validation · *Owner: Oli* · CORE (shared contract)
- **Purpose:** The canonical site schema — half-hourly load · connection (MIC/MEC, ASC band, voltage, ANM) · full tariff stack incl. levy lines · consents (G98/G99/G100, export MPAN, DNO application state) · supply boundary & supplier consent · physical pass/fail gates (footprint, fire separation) · counterparty. **No archetype field** — the engine reasons from the site's own data.
- **MVP:** A versioned JSON Schema with validators and the physical/consent gates; example fixtures at T0/T1/T2.
- **Later:** Net-load interface held open for solar (v2); richer consent state machine.
- **Dependencies:** None upstream; it is the input contract everything reads. Co-agreed with Olly.
- **Acceptance:** Schema validates good/bad fixtures correctly; gates flag fail conditions; schema is versioned and the version is recorded on every quote.

### M2 — Value & constraint stack · *Owner: Olly* · CORE
- **Purpose:** Market mechanics as composable value functions and dispatch constraints; one MWh, one decision; double-counting structurally impossible. Gates enforced at quote time (a stream failing consents/metering/portfolio thresholds is priced out and listed as conditional upside with its unlock condition).
- **MVP:** The **generic market interface** defined (value function, constraints, price/scenario inputs, firmness & decay, availability gates, deliverability profile) + bankable-floor streams (tariff/DUoS red-band avoidance, MIC/ASC headroom) as simple deterministic placeholders the mock can consume. Gated streams labelled, not priced.
- **Later:** Full composable stack incl. CM, local flex, and gated wholesale/BM + ancillary with decay; gate enforcement against real consents.
- **Dependencies:** M1 (site data), M6 (price/scenario inputs), X.1 (stream assumptions).
- **Acceptance:** Streams implement one interface; the core never branches on `market == "GB"`; a failed-gate stream is excluded from the priced case and listed as conditional upside.

### M3 — Dispatch engine · *Owner: Olly* · CORE
- **Purpose:** Half-hourly dispatch producing the value-maximising schedule under physics (charge load vs MIC/ASC, round-trip losses at the price faced, parasitic load at host tariff) and hard warranty/throughput caps; degradation as a marginal-cost proxy in-objective + ex-post SoH for life.
- **MVP:** A **deterministic mock** returning a schema-valid `OptimisationResult` (dispatch summary + value by stream) for a SiteModel — reproducible and fast. **No production MILP.**
- **Later:** Production MILP on linopy/HiGHS; the single-site full-year solve-time benchmark decides search method / temporal compression.
- **Dependencies:** M1, M2; emits the shared `OptimisationResult` contract.
- **Acceptance:** Returns a schema-valid `OptimisationResult` for every valid SiteModel; deterministic for identical inputs+seed; and respects checkable invariants — dispatch never exceeds the SiteModel's MIC/ASC charge limits, throughput ≤ the stated duration, and `value_by_stream` sums to the result's total value. Honours the `OptimisationResult` schema the real engine will later satisfy.

### M4 — Sizing optimiser · *Owner: Olly* · CORE
- **Purpose:** Search power, energy and duration against the bankable objective (maximise levered return s.t. P90 DSCR ≥ covenant), sized day-one for end-of-life capability (no augmentation; binding year may be late-life).
- **MVP:** Enumeration over a small P·E·duration grid (the grid size is a v1 implementation choice) using the mock dispatch + M5 finance to pick a "chosen" spec — committing to the search path (not a fixed spec) so the "one engine, called twice" loop is actually exercised.
- **Later:** Full search with P90 DSCR embedded; enumeration first, smarter search only if the benchmark demands.
- **Dependencies:** M3 (dispatch), M5 (objective/DSCR), M1.
- **Acceptance:** Returns a single chosen spec with rationale; the chosen spec satisfies the embedded P90-DSCR constraint; at least one grid point is *rejected* by that constraint (proving the bankable gate fires, not just decorates); reproducible.

### M5 — Financial model & underwriting outputs · *Owner: Oli* · CORE
- **Purpose:** P50/P90 cashflows; debt sized and sculpted on the P90 case; commercial structures as plug-ins (fixed £/kWh default, savings-share, capacity fee) with term/indexation/buyout explicit; full cost lines (levies on charge volume incl. losses, business rates, insurance, metering/IDNO, DNO costs); installer capex sanity-checked against a benchmark; the price that hits target return.
- **MVP:** Real underwriting calcs around the mock `OptimisationResult`: P50/P90 + DSCR, the fixed £/kWh plug-in, core cost lines, price-to-target-return. Consumes M9's credit→rate/tenor mapping. v1 uses a single **versioned placeholder installed-capex assumption** in X.1 (the plan's ~£500k/MW is the candidate); the installer-capex benchmark sanity-check is deferred to Later.
- **Later:** All plug-ins, full cost detail, debt sculpting, capex benchmark library.
- **Dependencies:** M3/M4 (`OptimisationResult`), M6 (scenarios), M9 (rate/tenor), X.1 (assumptions), X.2 (provenance).
- **Acceptance:** Produces P50/P90 + DSCR (not point estimates) for a chosen spec; numbers reproducible and traceable; rate/tenor reflect the credit input; no LLM touches a number.

### M6 — Scenario engine · *Owner: Oli* · CORE
- **Purpose:** Discrete, probability-weighted Low/Central/High price cases × policy branches; time-to-energisation as a scenario axis affecting COD; Monte Carlo deferred to portfolio behind the same interface.
- **MVP:** A small discrete Low/Central/High set with placeholder weights/curves (versioned), feeding P50/P90; time-to-energisation as a single switch.
- **Later:** Policy branches (REMA-aware), richer curves, portfolio Monte Carlo behind the interface.
- **Dependencies:** X.1 (price curves/weights as versioned assumptions), feeds M2/M5.
- **Acceptance:** P50/P90 derive from the discrete weighted cases; cases and weights live in the assumption library; changing the version changes the result traceably.

### M7 — Calibration · *Owner: Olly* · CORE
- **Purpose:** Backtest against historical GB data; calibrate, version and transparently apply the perfect-foresight haircut; feed realised performance back per stream; no confidence label asserted without evidence.
- **MVP:** Out of scope as a working backtest — interface/placeholder only (a documented hook + a default, clearly-labelled haircut constant in the assumption library). No asserted confidence.
- **Later:** Full backtesting harness; first validation set from the cold-storage wedge as test-data selection; realised-vs-underwritten feedback.
- **Dependencies:** M3 (dispatch to backtest), X.1 (haircut value), X.3 (realised data).
- **Acceptance:** v1 applies only a versioned, labelled placeholder haircut; no calibration claim is made without backtest evidence; the hook exists for later.

### M8 — Outputs · *Owner: Oli* · PERIMETER
- **Purpose:** Two documents from one run record — the underwriting memo (capital-facing: P50/P90, DSCR, firm-vs-conditional stream table, credit, consents, gate status) and the proposal (customer-facing: bill before/after, savings, term, structure, plain language). LLM drafts prose; human signs; every number from the run record.
- **MVP:** Generate a draft memo + draft proposal from the quote record; numbers injected from the record, prose LLM-drafted; render to a readable format (the format — e.g. HTML/Markdown/PDF — is a v1 implementation choice; the source specifies only "two documents from one run record").
- **Later:** Polished 2026-quality layout, animations, branded proposal layer; tighter human-sign workflow.
- **Dependencies:** M5 (finance), the quote record (X.2), M9 (credit), M2 (stream/gate status).
- **Acceptance:** Every number in both documents matches the run record exactly; no number originates in LLM prose; documents regenerate identically from the same record.

### M9 — Counterparty credit · *Owner: Oli (mapping); tool ownership open* · PERIMETER
- **Purpose:** Produce an internal credit score + short memo (Creditsafe API + research agent) that maps **deterministically** to discount rate and tenor in the financial model; flag portfolio concentration by counterparty/sector/region.
- **MVP:** No live Creditsafe unless a key exists — a manual/stub credit input (score or band) that maps deterministically to rate/tenor; the deterministic mapping itself is the v1 deliverable.
- **Later:** Creditsafe API + research agent; concentration flags. (Tool/agent ownership is unresolved — see [boundary-doc Open Q2](00_project_boundary.md).)
- **Dependencies:** Feeds M5; X.1 (rate/tenor policy mapping); X.3.
- **Acceptance:** A given credit input always yields the same rate/tenor; no "glowing return at a rate the covenant doesn't justify"; mapping is versioned in the assumption library.

### X.1 — Assumption library · *Owner: Oli* · CROSS-CUTTING
- **Purpose:** One versioned source for every assumption — no private constants, diffable, reviewed on cadence; all modules read the same release.
- **MVP:** A versioned assumptions store (file-based) with every number the pipeline uses; a release tag stamped onto each quote.
- **Later:** Review cadence tooling, tolerance-driven repricing triggers, diff review.
- **Dependencies:** Read by M0/M2/M3/M4/M5/M6/M7/M9. *(In v1 the mock M3/M4 hold no priced constants, but the real M3/M4 must read X.1 — degradation marginal-cost proxy, round-trip loss values, warranty/throughput caps, P90-DSCR covenant — so they are listed here to honour "no module holds a private constant".)*
- **Acceptance:** No module holds a private constant; changing a value bumps the version; a quote records the version it used.

### X.2 — Provenance · *Owner: Oli* · CROSS-CUTTING
- **Purpose:** Every output number traceable to inputs + assumption version + code version.
- **MVP:** The quote record captures inputs hash, assumption version, code version (git SHA or equivalent), seed; outputs link back to these.
- **Later:** Per-number lineage / drill-down.
- **Dependencies:** Wraps every run; consumes X.1 version + code version.
- **Acceptance:** Any output number can be traced to its inputs, assumption version and code version; identical inputs+assumptions+seed reproduce identical outputs.

### X.3 — Quote telemetry · *Owner: Oli* · CROSS-CUTTING
- **Purpose:** Every quote logged from day one — data tier at intake, human-minutes by person, installer iterations, win/loss with reason, realised-vs-underwritten after COD. The falsifiable cost-compression dataset.
- **MVP:** Log each run's data tier, human-minutes (manual entry ok), iterations, outcome, versions; capture the manual quoting benchmark before the system erases it.
- **Later:** Realised-vs-underwritten after COD; hit-rate-by-tier and error-by-stream analytics.
- **Dependencies:** Wraps every run; reads the quote record.
- **Acceptance:** Every run produces a telemetry record; the manual benchmark is captured at the start; telemetry ships with the first quote, not retrofitted.

> **Shared contracts (Olly agree first):** the `SiteModel` JSON schema (M1), the `OptimisationResult` JSON schema (M3/M4 → M5), acceptance tests, and assumption conventions. These are the only coupling points; everything else is owned by one person.

### `OptimisationResult` — v1 contract sketch (ratify at Milestone 1)

This is the single most load-bearing artifact: the seam between the optimisation engine and Oli's finance/outputs, consumed by M5/M8 from Milestone 3 and satisfied by the mock now and the real MILP later. The field shape below is an **engineering design choice (not from the source docs)** — Olly agree and version it at Milestone 1 so both can build against the same object:

- `chosen_spec` — `power_kw`, `energy_kwh`, `duration_h`, `coupling` (AC in v1).
- `dispatch` — half-hourly array (length = HH periods in the modelled horizon; units kW; sign convention **+ = discharge, − = charge**); charge load is counted against MIC/ASC.
- `value_by_stream` — £/yr keyed by stream id (matching M2's market-interface ids), each tagged `priced` | `contracted` | `gated/conditional` (with unlock condition); plus a `total_value` the parts must sum to.
- `energy_accounting` — round-trip losses, parasitic load, annual throughput (cycles/yr) for warranty-cap checks.
- `meta` — `seed`, `assumption_version`, `code_version`, and `model_version` (`mock` | real engine semantic id).

It is the second of the two shared contracts; field-defining it is a **Milestone 1 exit criterion**.

---

## 7. Data flow

```
SiteData ─▶ Ingestion(M0) ─▶ SiteModel(M1) ─▶ Optimisation Engine(M2·M3·M4) ─▶ Financial Model(M5) ─▶ Quote Record(X.2) ─▶ Outputs(M8) ─▶ Telemetry(X.3)
                                                          ▲                          ▲
                                              Scenarios(M6) ┘            Credit(M9) ┘
                          Assumption library(X.1) feeds every deterministic step · Provenance(X.2) stamps every number
```

- **SiteData → Ingestion → SiteModel:** v1 accepts structured SiteModel JSON directly; ingestion validates and grades it.
- **SiteModel → Optimisation Engine:** v1 uses the **mock** engine emitting a schema-valid `OptimisationResult` (the contract Olly's real MILP later satisfies). Scenarios (M6) supply price/policy cases.
- **Optimisation → Financial Model:** real underwriting calcs (P50/P90, DSCR, price-to-target); credit (M9) supplies rate/tenor.
- **Financial Model → Quote Record:** an immutable, versioned, traceable run object.
- **Quote Record → Outputs:** memo + proposal drafted from the record (numbers from the record, prose from the LLM).
- **Everything → Telemetry:** each run logged from the first quote.

The single arrow that is *mocked* in v1 is `Optimisation Engine`; every other hop is real, so swapping the mock for Olly's MILP is the only seam that changes.

---

## 8. Build milestones

Built sequentially (per [CLAUDE.md](../CLAUDE.md): plan before each, small testable changes, docs+changelog after).

**Testing strategy:** the harness is `pytest` (Python/linopy stack). Keep a minimum fixture set of valid + invalid `SiteModel`s at each tier (T0/T1/T2) plus a reference quote record. Milestone 7's conformance suite asserts that the mock and the real engine both satisfy the `OptimisationResult` schema (and stream registry), and that M5/M8 require **no code change** and stay deterministic against the real engine's output — *not* that the numbers are identical (a real optimiser legitimately produces different dispatch/value than the mock). A separate golden test holds the *mock's* `OptimisationResult` fixed and asserts M5/M8 reproduce identically against it. Filling the [CLAUDE.md §6](../CLAUDE.md) command table (test/lint/typecheck/run) is a Milestone 0 exit criterion.

- **Milestone 0 — Project setup & docs.** Repo scaffold, tooling (test/lint/typecheck/run), `CLAUDE.md`/`docs`/`CHANGELOG` in place, decide DB-vs-files (see Open questions). *Exit:* a runnable empty project with the quality bar wired, the CLAUDE.md command table filled, and the DB-vs-files storage decision recorded — it blocks Milestone 2's run-record format, so it is not relitigated mid-build.
- **Milestone 1 — Schema & contracts.** `SiteModel` JSON schema + validators + gates (M1); `OptimisationResult` JSON schema (fields enumerated per §6); fixtures at T0/T1/T2. Agreed jointly by Olly. *Exit:* both schemas versioned; the `OptimisationResult` fields are ratified; fixtures validate; Olly can build the real engine against the contract.
- **Milestone 2 — Assumption library & provenance.** Versioned assumptions store (X.1) + provenance plumbing (X.2): inputs hash, assumption version, code version, seed on a run record. *Exit:* a stub run is fully traceable and reproducible.
- **Milestone 3 — Mock optimisation & financial outputs.** Mock engine emitting `OptimisationResult` (M3 stub, minimal M2 floor streams, M4 mini-enumeration over a small grid); financial model (M5) producing P50/P90 + DSCR + price-to-target via M6 scenarios. *Exit:* SiteModel → numbers, deterministic and traceable.
- **Milestone 4 — Ingestion workflow.** M0 accept/validate/grade structured SiteModel JSON with clear errors and tier assignment. *Exit:* good/bad fixtures handled correctly; tiering correct.
- **Milestone 5 — Quote lifecycle & telemetry.** The quote as a first-class immutable object (draft → indicative → underwritten…) + X.3 telemetry per run; capture the manual benchmark. *Exit:* every run produces a versioned quote record and a telemetry row.
- **Milestone 6 — Outputs & proposal/memo generator.** M8 draft memo + proposal from the run record; numbers injected, prose LLM-drafted; rendered output. *Exit:* both documents generate, numbers match the record exactly.
- **Milestone 7 — engine integration contract.** Validate that the real engine can drop in behind the `OptimisationResult` seam (contract conformance tests; mock ↔ real swap rehearsed). *Exit:* the mock can be replaced without touching M5/M8.
- **Milestone 8 — UI polish & demo flow.** Thin web app: load SiteModel → run → inspect quote → view memo/proposal → see telemetry. *Exit:* a clean end-to-end demo from the web UI.

---

## 9. Risks

### Technical
- **Mock ↔ real divergence.** If the mock `OptimisationResult` shape or semantics drift from what the real MILP can emit, M5/M8 get built against a fiction. *Mitigation:* freeze the `OptimisationResult` contract early (Milestone 1/7); contract conformance tests.
- **MILP solve time (later, but shapes v1).** The single-site full-year solve-time benchmark decides search method and whether temporal compression is needed — unknown until run. *Mitigation:* keep the mock interface identical to the planned real one; run the benchmark experiment in parallel.
- **Determinism & traceability discipline.** Non-determinism (unseeded randomness, wall-clock, dict ordering) or private constants silently break reproducibility/provenance. *Mitigation:* X.1/X.2 from Milestone 2; seed everything; ban private constants in review.
- **LLM prose contaminating numbers.** The M8 generator must never let drafted prose create/alter a figure. *Mitigation:* numbers injected from the record only; tests assert document numbers == record numbers.
- **Schema churn.** Early `SiteModel`/`OptimisationResult` changes ripple across modules. *Mitigation:* version the schemas; fixtures; agree the contracts before downstream build.

### Commercial
- **RTM partner dependency.** Wholesale/P415 + ancillary value can't be priced precisely until the route-to-market partner/API is known; placeholders risk being mistaken for real. *Mitigation:* keep them labelled gated-upside in every output; never in the priced floor.
- **Creditsafe access.** No live credit feed without an API key; credit assumptions affect rate/tenor. *Mitigation:* deterministic manual/stub mapping in v1; swap in the API when available.
- **Data-source availability.** Tariff data, HH formats, Piclo/local-flex availability and CM assumptions are external inputs not yet pinned (see Open questions). *Mitigation:* versioned placeholders in X.1; flag as assumptions, not facts.
- **Business-rates treatment.** Wrong treatment of BTM BESS business rates could silently flatter every quote. *Mitigation:* carry as an explicit, versioned cost-line assumption; confirm before relying on it.

### Project
- **Two-person coupling.** platform/engine progress depends on the two shared contracts; ambiguity there stalls both. *Mitigation:* agree `SiteModel` + `OptimisationResult` first (Milestone 1); keep all other coupling out.
- **Scope creep into other workstreams.** Pull toward CRM, MoUs, fundraise, Stage 2 erodes focus. *Mitigation:* §4/§5 and the boundary doc gate every new ask.
- **Telemetry-from-day-one slipping.** If telemetry is retrofitted, the cost-compression claim is unfalsifiable and the manual benchmark is lost. *Mitigation:* X.3 lands with the first quote (Milestone 5); capture the manual benchmark before automating.
- **Unresolved ownership (M9 / aggregator).** Credit-tool and aggregator technical-vs-commercial splits are open; building ahead of the decision risks rework. *Mitigation:* build only the deterministic mapping / technical interface in v1; route the rest to Open questions.

---

## 10. Open questions

Grounded in the source docs ([System Brief open items](../source_docs); [CLAUDE.md §7](../CLAUDE.md); [boundary-doc §6](00_project_boundary.md)). These gate or shape v1 modules:

1. **Database vs. files in v1.** Is a database needed for v1, or do file-based SiteModels / run records / assumption library suffice? *(Decide at Milestone 0.)* — CLAUDE.md §7.
2. **Half-hourly data formats.** Which installer/meter HH formats must ingestion (M0) eventually accept? Shapes the SiteModel and later parsers. — System Brief (ingestion); CLAUDE.md §7.
3. **Tariff data sources.** Where is the full tariff stack (incl. levy lines, DUoS bands) sourced and kept current? — System Brief; CLAUDE.md §7.
4. **Capacity Market assumptions.** De-rating factors, clearing-price scenarios, auction/contract terms for M2/M6. — System Brief (M2 CM); CLAUDE.md §7.
5. **Piclo / local-flexibility data source.** Tender availability and pricing per DNO area, to know when local flex is priceable. — System Brief (M2); CLAUDE.md §7.
6. **Creditsafe / API availability.** Is an API key available for v1, and (open) who owns the credit-scoring tool — PE or Finance? — System Brief (M9); boundary-doc Open Q2.
7. **Route-to-market partner / API details.** The conversation that replaces every front-of-meter placeholder and decides which services are modelled precisely vs lumped as decayed upside. Gates all wholesale/BM/ancillary modelling. — System Brief open items; boundary-doc Open Q5.
8. **Ancillary forecast source.** A commercial provider for decay-curve calibration of DC/DM/DR. — System Brief open items.
9. **EMS / M&V interface owner.** v1 defines the dispatch-envelope export *data contract* only; who owns the operating-system / M&V build, and the M&V method needed before savings-share contracts? — System Brief open items; boundary-doc Open Q4.
10. **Business-rates treatment of BTM BESS.** Confirm before it silently flatters every quote (a versioned cost-line assumption until then). — System Brief open items.
11. **Solve-time benchmark (gates the real engine).** The single-site full-year MILP solve-time result decides the outer-loop search method and whether temporal compression is ever needed — run early, in parallel with v1. — System Brief (the one experiment).

(Other System-Brief open items — REMA tracking owner, supplier-consent precedent, G99/DNO execution owner — are assumption/ownership inputs the engine consumes; tracked in the boundary doc, they don't change v1's build shape.)
