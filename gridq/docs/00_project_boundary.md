# 00 · Project Boundary

**Status:** Working draft · **Date:** 2026-06-15 · **Owner:** Product Engineering (Olly)

**Source documents (in `./source_docs`):**
- `neura_plan.html` — Neura business plan (June 2026, internal working draft)
- `System Requirements.html` — GridQ Sizing & Underwriting Engine, System Brief (5 pages)
- `Neura UK .pdf` — Team Workstreams and Responsibilities

This document defines the perimeter of the **Product Engineer (PE) workstream** so that work stays inside it. It is a boundary and ownership document, not a build spec. It introduces no new assumptions; every scope statement is traceable to a source document or to a working-split decision recorded here. Genuinely unresolved points are collected under [Open questions](#6-open-questions).

---

## 1. What this project IS

A **Claude Code project for the Product Engineer workstream** for Neura/GridQ.

The deliverable of this workstream is **GridQ: the behind-the-meter (BTM) BESS sizing and underwriting engine** — the Stage 1 "wedge" of the Neura business. GridQ takes a single commercial site's half-hourly data and, from one run, produces three artifacts that are *consistent by construction*:

1. **System specification** — power, energy and duration optimised for the site (AC-coupled, battery-only in v1).
2. **Underwriting pack** — P50/P90 cashflows, DSCR sensitivities, contracted-vs-merchant split, counterparty credit, and the price that hits target return at the optimised dispatch.
3. **Customer proposal** — bill before/after, savings, term, structure, in plain language.

Per the System Brief, this is *"one engine · one assumption set · three artifacts."* Per the Team Workstreams doc, building it is the *"big push initially"* — *"our wedge and why established parties will speak to us."* Per the plan, it is the cost-compression engine that makes small sites underwriteable at all (the plan's worked example of a site too small for utility-scale underwriting is ~500 kW).

This project is the engine and its platform. It is the thing the rest of the company sells, funds and operates around.

---

## 2. What this project is NOT

The PE workstream does **not** own the broader Neura business. Specifically, this project does **not**:

- **Build the whole Neura business.** GridQ is one workstream of four (PE, Growth, Finance, Operations/Partnerships).
- **Own Zara's Growth work** — sourcing the pilot site, installer MoUs, the qualified-site pipeline, the installer/channel value proposition, the partner programme, CRM/LinkedIn/marketing, or the market-listening loop. *(PE supports the origination→underwriting data handoff; see §3, but does not own the sales motion.)*
- **Own Sim's Finance work** — the **portfolio** financial model, capex/opex/depreciation schedules, yield-vs-cost-of-capital, equity retention, MOIC, **credit policy**, or fundraising / infrastructure-funding conversations. *(GridQ produces per-site finance outputs and consumes a credit score that maps to rate/tenor; it does not set portfolio finance or credit policy — see [Open questions](#6-open-questions).)*
- **Own Karim's Operations/Partnerships/Legal work** — legal templates, the MoU template, the £/kWh PPA skeleton, option agreements, partnership negotiations, RTM/aggregator commercial terms, or any external commercial agreements. *(PE models contract structures and defines technical interface assumptions; it does not negotiate or paper them.)*
- **Build Stage 2 compute shells.** Grid-smart compute shells (optioning under-used connections, powered-shell leasing to compute operators) are explicitly Stage 2 / "Next." GridQ holds the *primitive* Stage 2 reuses (pricing connection-headroom value), but Stage 2 itself is out of scope.
- **Build autonomous deployment or operations systems.** *(Superseded in part by **ADR-D2**, decided 2026-11-25 (option d), logged as decisions.md D69: Neura now owns live optimisation and the dispatch envelope, the route-to-market partner owns market execution, the OEM owns certified control, and control authority is staged with a compute turn-down stage that runs in shadow only. The sentence that follows is the 2026-06-15 boundary and is kept for the record. Source: `atlas/roadmap/adr/ADR-D2.md`.)* The Stage 1 MVP is *"not autonomous, just trustworthy enough that we'd put our own name on its numbers."* No autonomous dispatch/operations, no autonomous deployment. The EMS/M&V *data contract* is defined in v1; the operating system itself is not built here (see [Open questions](#6-open-questions)).

---

## 3. The exact Product Engineer scope

The PE workstream builds the **revenue-stack underwriting model** and turns it into a reusable, verifiable, modern-looking template. The scope items, with their grounding:

| # | Scope item | What it covers |
|---|------------|----------------|
| 3.1 | **Revenue-stack underwriting model** | The deterministic core that prices a site across its full revenue stack and produces the bankable underwriting pack (P50/P90, DSCR, contracted-vs-merchant split). |
| 3.2 | **Optimisation method & architecture** | *"Agree on the optimisation method and architecture first."* Half-hourly dispatch MILP (committed: linopy/HiGHS), the sizing outer loop (P · E · duration), multi-objective handling, and the bankable constraint (maximise levered return s.t. P90 DSCR ≥ covenant) sitting inside the sizing objective. |
| 3.3 | **PPA / customer contract modelling** | Commercial structures as plug-ins: fixed £/kWh (default), savings-share, capacity fee — with term, indexation and buyout schedule explicit. |
| 3.4 | **Capacity Market modelling** | CM as a contracted stream, de-rated by duration, deliverability verified through the final contract year at degraded capability. |
| 3.5 | **Local flexibility / Piclo modelling** | Local flex as a contracted stream; priced when the tender is open in the site's area and the contract holds. |
| 3.6 | **Wholesale / P415 modelling — gated upside** | Wholesale + Balancing Mechanism via P415 modelled as **gated upside, never in the priced floor**, on placeholder curves, until the route-to-market (RTM) partner / API and export-consent assumptions are known. Listed as conditional upside with its unlock condition until then. |
| 3.7 | **Reusable template that runs any site** | One engine that runs any BTM site from its own data — no per-site bespoke modelling, no archetype field; the engine reasons from the site's own half-hourly data. |
| 3.8 | **Required outputs & verifiability** | The three artifacts plus the verifiability layer: deterministic, reproducible, versioned, every number traceable (see §5). P50/P90 + DSCR mandatory; point estimates are not deliverables. |
| 3.9 | **Modern (2026-quality) outputs & visual proposal layer** | Outputs that *"should look 2026"*, including animations (*"Animations as output"*) and a polished visual proposal layer the installer channel can sell with. |
| 3.10 | **Technical interface assumptions for aggregator / API access** | The **technical** interface and API assumptions for wholesale/BM access via an aggregator — the data contract, technical considerations and integration design. *(The workstreams doc puts PE on "design agreement and technical considerations (APIs)"; the split of technical design vs. commercial terms with Growth/Operations is pending [Open Q1](#6-open-questions).)* |

**Engine design principles carried into scope (from the System Brief):**
- One engine, called twice: sizing *searches* specifications, underwriting *prices* the chosen one — same dispatch engine, value stack and scenario set.
- The market interface is the most important design artifact: every value stream implements one interface (value function, constraints, price/scenario inputs, firmness & decay, availability gates, deliverability profile); the core consumes it generically.
- GB value stack tiers: **bankable floor** (import-side, no partner: supply-rate arbitrage, DUoS red-band avoidance, MIC/ASC headroom) → **contracted** (Capacity Market, local flexibility) → **gated upside** (wholesale/BM P415, ancillary DC/DM/DR — placeholders until the RTM partner).
- Quote lifecycle: a quote is a first-class, immutable object (draft → indicative → underwritten → contracted → operating), with the two installer modes "fast & banded" and "slow & firm."

---

## 4. The platform ↔ engine split (one owner: Olly)

Product Engineering is **Olly**. Olly owns the whole GridQ engine end to end. The codebase is organised into two halves that meet at a frozen contract — the underwriting **platform** and the optimisation **engine** — so each half can be built and tested against a stable interface. Module IDs (`M0`–`M9`, `X.1`–`X.3`) are cross-references to the System Brief's module map, included for clarity.

### The underwriting platform
The platform around the engine and everything that turns a run into bankable, traceable, sellable output:
- **Ingestion** — any installer format → validated `SiteModel`, graded T0–T2 *(M0)*
- **SiteModel / schema** — the canonical site schema and input contract *(M1)*
- **Finance outputs** — P50/P90 cashflows, DSCR, debt sizing, commercial-structure plug-ins, the price that hits target return *(M5)*; the credit-score→rate/tenor **mapping inside the financial model** *(M9 — mapping only; ownership of the scoring tool / research agent is unresolved, see [Open Q2](#6-open-questions))*
- **Scenario layer** — discrete probability-weighted price/policy cases, time-to-energisation axis *(M6)*
- **Outputs** — the underwriting memo and customer proposal, plus the modern/visual proposal layer *(M8)*
- **Quote telemetry** — every run logged from day one: data tier, human-minutes, iterations, outcome, realised vs underwritten *(X.3)*
- **Assumption library** — one versioned source for every number; provenance/traceability *(X.1, X.2)*
- **Agentic perimeter** — the LLM-assisted, schema-validated, human-confirmed boundary (ingestion assistance, document drafting, and *consuming* credit-research output — the credit-research tool itself is owned per [Open Q2](#6-open-questions))

### The optimisation / revenue engine
The deterministic numerical heart that decides what to build and how it earns:
- **Value stack** — market mechanics as composable value functions and dispatch constraints; the market interface *(M2)*
- **Dispatch MILP** — half-hourly MILP on linopy/HiGHS; physics, losses, parasitic load, warranty/throughput hard constraints, degradation *(M3)*
- **Sizing optimiser** — search over power, energy and duration against the bankable objective with P90 DSCR embedded *(M4)*
- **Calibration** — backtests against historical GB data, perfect-foresight haircut, realised-performance feedback per stream *(M7)*
- **Solver benchmarking** — the single-site full-year MILP solve-time experiment that decides the outer-loop search method and whether temporal compression is needed

### The platform ↔ engine coupling — limited and explicit
The coupling between the two halves is deliberately limited to **two frozen contracts**:
1. The **`SiteModel` input contract** — what the engine consumes (ingestion/schema → the optimisation engine).
2. The **`OptimisationResult` output contract** — what the engine returns (sizing/dispatch → finance/outputs).

The two contracts are the interface that lets each half be built and tested independently.

---

## 5. Non-negotiable architecture rules

These are hard rules. They define what makes GridQ's output bankable and are not subject to convenience trade-offs.

1. **No LLM on the pricing path.** The pricing path contains no language model and no stochastic AI. *"A lender will not accept a language model pricing their asset."*
2. **Pricing must be deterministic.** Identical inputs, assumption version and seed produce identical outputs. The pricing core is deterministic numerical code (MILP on linopy/HiGHS).
3. **Every number is traceable** to its input, its **assumption version**, and its **code version**. A quote is valid only against the assumption version that produced it; material assumption updates trigger repricing. No module holds private constants.
4. **AI is allowed only on the perimeter** — ingestion assistance, counterparty/credit-research *outputs* (tool ownership per [Open Q2](#6-open-questions)), document drafting, and workflow acceleration. It is LLM-assisted, schema-validated and human-confirmed; it never touches a number a lender relies on.
5. **The core is market-agnostic; GB-specific content is isolated.** One codebase carries to any market. The GB module is *content, not architecture* (which tariffs/streams exist, CM terms, VAT, GB parsers, GB assumption values). **If the core ever branches on `market == "GB"`, the abstraction has failed** — DUoS, CM, DC/DM/DR and Piclo never leak into the core modules.

---

## 6. Open questions

These are points where the source documents leave a PE-scope boundary genuinely unclear. They are not assumptions to invent — they need a decision or an owner.

1. **Aggregator relationship vs. technical interface (§3.10).** The workstream doc lists PE as *"liaise with aggregator … design agreement and technical considerations (APIs),"* while Growth (Zara) *"shortlist[s] an optimiser / route-to-market partner and agree[s] terms (with Karim)."* Confirm the split: PE owns the **technical/API interface assumptions and integration design**; the **commercial relationship and terms** sit with Growth/Operations. Who runs the aggregator conversation day-to-day?

2. **Counterparty credit — who builds it?** The System Brief places counterparty credit (Creditsafe API + research agent → score → rate/tenor) inside GridQ as perimeter module M9, but the workstream doc assigns *"build the credit-scoring tool (Creditsafe API, research agent)"* and *"write the credit policy"* to Finance (Sim). Resolve ownership: does PE build the scoring tool and consume Sim's policy, or does Sim build the tool and PE only consume its output? (Current working assumption: PE owns the *mapping to rate/tenor inside the financial model*; Sim owns the *tool and the policy*.)

3. **Visual/output layer vs. Growth's sales materials.** Where does PE's "2026-quality outputs and visual proposal layer / animations" end and Growth's installer-facing sales materials and tailored HTML decks begin? Confirm the boundary so the proposal layer and the sales collateral don't overlap or diverge.

4. **EMS / M&V interface owner.** The underwriting dispatch envelope must export as the benchmark the operating system is measured against, and an M&V method is required before savings-share contracts. The System Brief says *"define the data contract in v1, build nothing else"* — confirm PE owns the **data-contract definition only**, and identify who owns the EMS/M&V system itself.

5. **RTM partner dependency (gates §3.6).** The route-to-market partner conversation is what replaces every front-of-meter placeholder and decides which wholesale/ancillary services are modelled *precisely* vs *lumped as decayed upside*. Until that conversation lands, wholesale/P415 stays gated. PE depends on this input but does not own the conversation — confirm the handoff and timing.

6. **Non-functional targets are placeholders.** Speed targets (indicative quote within a working hour of T0; underwritten pack within five working days of T2) are explicitly *"placeholder targets — set before the first quote."* The manual quoting benchmark must be measured **once, at the start**, before the system erases it. Confirm PE owns setting these targets and capturing the manual benchmark.

> Other items in the System Brief's own "Open questions" (ancillary forecast source, REMA tracking owner, supplier-consent precedent, business-rates treatment, G99/DNO execution owner) are **inputs/assumptions** the engine consumes rather than PE scope boundaries. They are tracked there; they inform the assumption library but do not expand or contract this workstream's perimeter.

---

## 7. Summary — the project boundary in brief

**This project is GridQ:** the Product Engineer workstream's deterministic, behind-the-meter BESS **sizing and underwriting engine** — one engine that turns a single site's data into a consistent system spec, bankable underwriting pack and customer proposal, built as a reusable template that runs any site with modern, verifiable outputs.

**It is not the rest of Neura.** Growth's pipeline (Zara), portfolio finance and credit policy (Sim), legal/commercial agreements (Karim), Stage 2 compute shells, and autonomous deployment/operations are all out of scope.

**Within PE, the split is clean:** Oli owns the underwriting platform (ingestion, SiteModel/schema, finance outputs, scenario layer, outputs, quote telemetry, assumption library, agentic perimeter); Olly owns the optimisation/revenue engine (value stack, dispatch MILP, sizing optimiser, calibration, solver benchmarking). They share exactly two things: the **`SiteModel` input contract** and the **`OptimisationResult` output contract**.

**The architecture is non-negotiable:** no LLM on the pricing path; deterministic pricing; every number traceable to input, assumption version and code version; AI confined to the perimeter; and a market-agnostic core with GB content isolated from it.

Anything that does not fit inside this boundary is, by definition, another workstream's work — or a future stage.
