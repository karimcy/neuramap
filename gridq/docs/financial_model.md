# Financial Model (M5) — v1

**Status:** v1 (deterministic, baseline-compatible) · **Package:** [`src/gridq/finance/`](../src/gridq/finance/) · **Owner hint:** Oli (`agent/finance`)

The financial model wraps any `OptimisationResult` (the baseline result now; Olly's real MILP later)
in a deterministic **`FinancialCase`**: P50/P90 cashflows, cost lines, a capex estimate, an
EBITDA-like project cashflow and free cashflow before debt, DSCR + a covenant check, payback +
project IRR, and a price recommendation. It is **site-level**, deterministic and traceable — it
reads every number from the versioned assumption library and stamps each run with a `RunContext`.

> **Reads with:** [`docs/03_data_contracts.md`](03_data_contracts.md) (§4 `FinancialCase`), [`docs/assumption_library.md`](assumption_library.md), [`SKILLS.md`](../SKILLS.md) (non-negotiable rules), [`docs/parallel_development_plan.md`](parallel_development_plan.md) (§5 shared input-data rule).

---

## 1. What is implemented

The model runs end-to-end against the existing schemas **without waiting for dispatch/sizing**.
It degrades honestly when an input is missing rather than inventing one.

| Module | What it does |
|--------|--------------|
| [`cashflows.py`](../src/gridq/finance/cashflows.py) | Revenue by stream (from `OptimisationResult.value_streams`); the cost lines (below); the capex estimate; the EBITDA-like project cashflow and free cashflow before debt; the P50/P90 transformation; warnings. |
| [`debt.py`](../src/gridq/finance/debt.py) | `DebtTerms` (with a `sculpting_basis` of `ANNUITY` or `P90_DSCR`); the level-payment (annuity) debt-service schedule; the P90-DSCR sculpted schedule (closed form, honest annuity fallback); DSCR by year; the minimum-DSCR; the covenant check. **Gated on real debt inputs** (see §4). |
| [`returns.py`](../src/gridq/finance/returns.py) | Simple (undiscounted, interpolated) payback; robust project IRR by bisection (reported only when a unique root exists); NPV. |
| [`pricing.py`](../src/gridq/finance/pricing.py) | The price-recommendation structure: fixed £/kWh (default, with the capital-recovery → revenue-requirement → £/kWh machinery), and savings-share / capacity-fee as labelled placeholders. |
| [`financial_model.py`](../src/gridq/finance/financial_model.py) | The orchestrator: `build_financial_case(...)` / `run_financial_model(...)` assemble the `FinancialCase`, run debt/returns/pricing, dedupe warnings, and stamp a `RunContext`. |

**Pipeline:** `cashflows → P50/P90 series → debt/DSCR → payback/IRR → price → FinancialCase`.

### Cost lines (from assumptions only)

Each line is computed from one named assumption (or, for levies, from the site's own tariff
levy rates). A line that cannot be computed is **explicitly `None` (unavailable) with a warning**,
never silently zero or invented.

**`None` is the refusal branch, not a resting state (D45, [`decisions.md`](decisions.md)).** It is the
honest way to hold a line open while its input is sourced; it is not an acceptable end state for a
published case. The reason is arithmetic: a null revenue or cost input that reaches this model as
`0.0` does not read "unknown" — it reads as a measured £0, and it propagates through the P50/P90
haircut (§3) into CFADS, the minimum DSCR and the covenant verdict (§4) with nothing marking it out.
So a null on this path is either derived-and-declared upstream or **refused with the line withheld and
named**, and a case whose economics turn on a null input is a finding about the **pipeline**, not the
site.

| Cost line | Source | Formula |
|-----------|--------|---------|
| Capex | `battery_capex.installed_cost` (£/MW) | × power (MW) × scenario cost-multiplier |
| Opex | `battery_opex.annual_fixed_opex` (£/MW/yr) | × power (MW) |
| Insurance | `insurance.annual_pct_of_capex` (%) | × capex |
| Metering / IDNO | `metering_idno.annual_gbp_per_site` | flat per site |
| DNO costs | `dno_costs.annual_gbp_per_site` | flat per site |
| Business rates | `business_rates.annual_gbp_per_mw` (£/MW/yr) | × power (MW) |
| Levies on the net import increase | site `tariff_stack.levies` (£/kWh) × (charge − discharge) kWh | **D36:** a discharge displaces levied site import on the no-export floor, so the battery's levy cost falls on the round-trip losses only — the same net-import convention the dispatch objective and the customer bill use. Either volume derives from the other via RTE; unavailable until dispatch provides the volumes (see §5). |

### Cashflow definitions

- **EBITDA-like project cashflow (year n)** = priced revenue(n) − dispatch-level costs − operating cost lines.
- **Free cashflow before debt** = year 0 `−capex`, then the EBITDA-like operating cashflows for years 1..N. v1 models **no tax and no working-capital** movements (documented simplification), so the operating-year FCF-before-debt equals the EBITDA-like figure.
- **Multi-year extension:** revenue is decayed each year by `degradation.annual_capacity_fade_pct` (a capacity-proportional revenue-decay proxy); operating costs are held flat. **Degradation is charged exactly once (D36):** the revenue decay is the fade's whole price, with the warranty throughput cap as the hard cycling limit — the former fade×capex replacement-proxy and £/MWh wear cost lines were removed as double-counts (F-012); the library keys remain as unconsumed homes pending M7's calibrated SoH model. Indexation follows `financing.cpi_pct_per_year` (WP-UW-17, D129; see §4.2): absent, every line is flat-nominal and un-escalated, bit-identical to before; null, the same with a D45 warning; numeric, the five fixed cost lines escalate and a tenored T-4 CM payment is indexed per SI 2014/2043 Sch 1 para 3.

---

## 2. What is placeholder / proxy

`gb_v0_1` is **entirely placeholders** (`status: placeholder`/`needs_review`, `confidence: low`).
Therefore **every** run on `gb_v0_1` is **not bankable** and carries warnings naming each
non-confirmed assumption used. The model never flips a placeholder to confirmed and never
presents placeholder-driven output as bankable.

Specific proxies / simplifications, all clearly labelled:

- **Degradation / warranty cost proxy** — a single annual % of capex (capacity-fade reserve). The real treatment is M7's ex-post state-of-health / warranty model.
- **Degradation marginal cost** — the `gb_v1_2` per-MWh rate is an illustrative placeholder (no OEM warranty schedule sourced yet); any case pricing it stays not bankable until confirmed.
- **Revenue degradation** — capacity-proportional revenue decay; a simplification until dispatch models year-by-year throughput decay. A negative P90 haircut is clamped to zero with a warning so P90 never exceeds P50.
- **Debt service** — two shapes, selected by `DebtTerms.sculpting_basis`. `ANNUITY` (the default) is the level-payment schedule; the DSCR is measured and drifts with the profile. `P90_DSCR` sculpts the service to the P90 CFADS profile: the flat target DSCR `x` is solved so PV(P90 CFADS / x, debt rate, tenor) = principal, and each year's service is CFADS / x (flat DSCR = x by construction; deterministic closed form; zero-rate handled). When a P90 CFADS year within the tenor is non-positive, or the horizon is shorter than the tenor, sculpting is unavailable and the schedule falls back to the annuity with a warning; the case records the basis **actually applied**, never a sculpt that did not happen. The `gb_v1_2` gearing that lets debt assemble at all is itself a labelled placeholder (Sim's portfolio finance, B001).
- **Projection horizon** — `DEFAULT_HORIZON_YEARS` is a **structural modelling parameter, not an underwriting £-assumption**. It injects no £/% into any output; using the default emits a warning. It cannot live in the library yet (the 23 sections are frozen and none is a horizon) — see OPEN_COMMENTS.
- **Price recommendation** — savings-share and capacity-fee are structure-only placeholders (no value). Fixed £/kWh is computed only when delivered-energy volume is supplied (see §5).

---

## 3. How P50/P90 are generated

A **deterministic, assumption-driven haircut** — not a stochastic simulation (no Monte Carlo;
that is portfolio work, §6).

1. **P50 (central):** priced revenue at the basis year, scaled by the optional scenario
   `price_multiplier` and decayed by degradation across the horizon.
2. **P90:** P50 revenue × `(1 − p50_p90_haircuts.p90_revenue_haircut_pct / 100)`, applied per year.
   Costs are unchanged between P50 and P90 (the haircut is a revenue confidence haircut).
3. Both series share the same year-0 capex; they coincide only when revenue is zero.

Because both `p50_p90_haircuts` entries are `placeholder`/`needs_review`, any P50/P90 output
carries a warning and is not bankable until the haircut is calibrated (M7) and confirmed.
**DSCR is measured on the P90 case** (the bankable constraint), per the brief.

---

## 4. How DSCR is generated — and when it is available

Debt is sized at the gearing on capex, the service schedule is built on the recorded sculpting
basis (annuity or P90-DSCR sculpt, see §2), and DSCR = **P90 CFADS ÷ debt service**, year by
year, with the minimum compared to the covenant. Supplied lender terms (§4.0) can choose another
case, a straight-line or target-sized sculpted schedule, and their own covenant tiers.

**Availability is release-gated.** On `gb_v0_1`/`gb_v1_0` DSCR is reported as `unavailable`,
with a warning — never guessed: rate & tenor come from the deterministic credit mapping (§4.1)
but **gearing** is absent (portfolio finance, Sim's, B001). From **`gb_v1_2`** the library
carries `financing.debt_gearing_pct` as a clearly labelled placeholder (plus the D19 fold-in of
`financing.credit_rate_tenor_policy`), so in real (non-injected-debt) mode `DebtTerms` assemble,
DSCR computes, and the P90-DSCR covenant gate in the sizing objective can genuinely fire —
while every case priced on those placeholders stays **not bankable**. So:

- On old releases: `DscrSummary.min_dscr` is `None`, `by_year` is empty; `CovenantStatus.passes` is `None` (the covenant value `dscr_covenants.min_p90_dscr` *does* exist, but a verdict needs a DSCR, which needs gearing). Rate/tenor still populate from the credit mapping with `gearing_pct` `None`.
- On `gb_v1_2`: the credit-driven `build_debt_terms_from_credit` assembles full terms; DSCR and a real covenant verdict populate; `bankable` stays `False` (placeholder gearing/policy/covenant).
- `build_debt_terms` (the legacy flat-key reader) still → `None` on every release: the flat rate/tenor keys are deliberately absent (rate/tenor are the credit mapping's to supply).

Injected terms always win: `run_financial_model(..., debt_terms=DebtTerms(...))` bypasses the
credit path entirely (callers/tests keep full control).

**Every DSCR carries a status and a reason (WP-UW-03, D75; closes B001).** `DscrSummary.status` is
one of `measured`, `no_debt_terms`, `all_equity`, `tenor_beyond_horizon`, `period_not_modelled` or
`no_service_years`, and `DscrSummary.unavailable_reason` states the cause in words whenever
`min_dscr` is `None`. A run with no debt terms reads "No lender terms supplied: …" (plus the credit
band, when the credit mapping could not supply a rate or tenor either). Never a bare `None`, never
a default number. The CLI summary prints "min DSCR" as a value with its basis or as
"unavailable: <reason>"; the app finance panel and the underwriting pack print the same reason.

### 4.0 Supplied lender terms (`LenderTerms`, WP-UW-03, D75 to D78)

`gridq.schemas.LenderTerms` is a lender's terms as stated, the way a real site gets a DSCR. Supply
it with `gridq run SITE --lender-terms terms.json` (JSON or YAML), the app's optional lender-terms
block (wizard and real form), or `generate_quote(lender_terms=...)` /
`run_financial_model(lender_terms=...)`. It replaces the credit mapping; passing it together with
the legacy `debt_terms` is refused.

- **Required, never defaulted:** `tenor_years`, `interest_rate_pct` (all-in, percent),
  `gearing_pct` (maximum debt, percent of capex), `amortisation` (`annuity`, `straight_line`,
  `sculpted`), `cashflow_case` (`P50`, `P90`, `LENDER`), `calculation_period` (`annual`,
  `semi_annual`, `quarterly`) and `provenance` (`source`: `term_sheet`, `lender_indication` or
  `synthetic`; a `reference`; optional `as_of` and `evidence_sha256`).
- **Optional:** `covenant` (`default_dscr`, `lock_up_dscr` or both) and `sculpting_target_dscr`
  (sculpted only).
- **Case (D77).** `P50` and `P90` measure the free cash flow before debt of that case; `LENDER`
  measures the contracted P90 case bearing every operating cost (W8). The basis label follows.
- **Shape and size (D77).** Annuity (level payment), straight-line (equal principal) or sculpted (flat
  DSCR on the case). A sculpting target sizes the loan as min(capex × gearing, PV(case CFADS ÷
  target)); `DebtAssumptions.sized_by` says which bound bit and `principal_gbp` records the loan.
- **Covenant (D76).** The default and lock-up thresholds come only from the terms. Terms with no
  covenant leave the verdict not evaluated, with the reason; the library placeholder is never
  substituted. Runs without lender terms keep the library covenant, labelled `assumption_library`.
- **Period (D76).** Only an annual test is measured. A semi-annual or quarterly test gives
  `period_not_modelled` and no debt, never an annualised ratio.
- **Labelling.** Synthetic terms carry a "SYNTHETIC LENDER TERMS" warning and are labelled on every
  surface that shows the DSCR; indications are labelled indicative. Only term-sheet terms with a
  default covenant can support a bankable verdict.
- **Identity (D78).** Supplied terms enter `case_id` and the quote `input_hash`; a run without them
  keeps its historical hash. The sizing loop (`MilpEngine`) does not see the terms yet (WP-UW-09).

A synthetic example (not a lender's terms) is `tests/fixtures/lender_terms.synthetic.json`.

### 4.1 Counterparty credit → rate/tenor mapping (M9, `finance/credit.py`)

The deterministic mapping — M9's v1 deliverable — turns a counterparty's coarse `CreditStatus`
band into a debt rate and tenor:

- `map_credit_to_rate_tenor(credit_status, assumptions)` reads the **versioned** assumption
  `financing.credit_rate_tenor_policy` (a per-band table: `strong`/`adequate`/`weak` → `{rate_pct,
  tenor_years}`). No private constants — the numbers are Sim's credit *policy*, placeholders until
  confirmed, so a credit-driven case is never bankable on them.
- **`unknown`/`not_assessed` → unavailable + a warning** — never defaulted to a band, never a
  guessed number; this becomes a ranked data ask at ingestion (`counterparty_credit_status`).
- **Monotonicity is enforced in code**: a worse band must have a rate that is non-decreasing and a
  tenor that is non-increasing. A non-monotonic policy table refuses to map — the literal guard
  against "a glowing return at a rate the covenant doesn't justify."
- Deterministic: rate rounded to 4dp, tenor to an integer year, at parse time — identical inputs
  reproduce identical terms (and an identical run-context hash, since `credit_status` is in the
  hashed site dump and the policy is pinned by the assumption version).
- `build_debt_terms_from_credit(...)` combines the mapped rate/tenor with gearing
  (`financing.debt_gearing_pct` or an explicit override) into a full `DebtTerms`. **Gearing is out
  of M9's scope** (portfolio finance, Sim's); when absent, rate/tenor still flow to the case and
  DSCR stays unavailable. `expected_tenure_years` shorter than the debt tenor is *warned*, not
  auto-capped (a deferred, library-sourced policy decision).

**Creditsafe seam ([B001](shared_work/BLOCKERS.md)).** The input boundary is `CreditStatus`. A
future Creditsafe-backed tool produces `score → CreditStatus` (its own deterministic
band-threshold mapping) *upstream* and plugs in by populating `SiteModel.counterparty.credit_status`
— this `band → rate/tenor` function is unchanged. `Counterparty` is deliberately **not** widened
with a raw score now (no contract change ahead of the producer). No external API is called and no
credit band is invented in v1.

---

### 4.2 Finance completeness: levelisation, indexation, construction, lifecycle flows (WP-UW-17)

Module [`lifecycle.py`](../src/gridq/finance/lifecycle.py); typed inputs in
[`schemas/lifecycle.py`](../src/gridq/schemas/lifecycle.py) (`LifecycleInputs`, each with provenance; no
figure defaulted). Supplied through `generate_quote(lifecycle=)`, `gridq run --lifecycle FILE` (JSON or YAML)
or the app's `lifecycle_json` field. Decisions D128 to D132.

| Piece | What the run does | Without input |
|---|---|---|
| Price (N12) | Levelises the run's P50 pre-debt flows at the target rate over the modelled delivered volume each year (`delivered_energy_kwh_by_year`: discharge × fade × availability); flat-nominal, floored at £0; `hits_target_return` measured with the price in the loop (equity IRR if debt is sized); equity-solved price as a diagnostic | Volume unknown: the legacy CRF path, "volume not yet known" |
| Indexation (N13, H-15) | `E_t = (1+c)^(t−1)` on opex, insurance, metering/IDNO, DNO, business rates; tenored T-4 CM × `U × E_t` (`U = CPI_x/CPI_base` from the agreement); T-1 flat; every line tagged in `line_basis`; real ledger and real IRR beside the nominal | Key absent: flat, bit-identical. Null: same, one D45 warning. No agreement: T-4 assumed, `U = 1.0`, warned with the ONS data ask |
| Construction (H-16) | Month-dated timeline: draws before COD, IDC factor `A = Σ w_j (1+i)^(n_j + D)` in the principal at COD, dated headline IRRs; payback and `p50_cashflows` stay annual | "CONSTRUCTION PHASE EXCLUDED … This flatters IRR." |
| COD delay | A zero-weight `cod_delay` scenario row, applied: operations start D months late, tenored terms consumed pro rata | "NO COD-DELAY SENSITIVITY: no delay was supplied; none is assumed." |
| Lifecycle flows (N14) | Terminal value in year N (IRR only, never CFADS); decommissioning and replacements in their year (CFADS too); beyond-horizon flows excluded by name; a sculpt floored at zero in a negative year | The legacy N14 warning, and three `excluded_no_input` ledger rows with data requests |

The derived `FinancialCase.lifecycle` record is always populated and rendered as the pack's "Lifecycle capital
flows" table. Lifecycle inputs enter `case_id` and `input_hash`; the derived record does not. Open: the
sizing loop does not see lifecycle inputs (warned "SIZE SELECTED WITHOUT LIFECYCLE FLOWS").

## 5. How future dispatch outputs feed this

The model is coded against the **current** `OptimisationResult` contract and treats dispatch-only
quantities as forward seams:

- **Revenue by stream** already flows straight from `OptimisationResult.value_streams`. When the
  real MILP prices streams, revenue (and so EBITDA, FCF, IRR, payback) follows with no change here.
  The baseline result prices nothing (zeros), so v1 produces a cost-only, not-bankable case.
- **Charge volume for levies** is not on the current schema. Supply it via the `DispatchEnergy`
  hook (`annual_charge_kwh`, or `annual_discharge_kwh` grossed up by round-trip efficiency); the
  `levies_on_charge` line then activates. Until then it is `None` with a warning.
- **Delivered-energy volume for pricing** (fixed £/kWh) likewise comes from dispatch; until it is
  known the price-to-target structure is returned without a value.
- **Sizing** (`selected_power_kw`) drives capex. If a result is unsized/infeasible the model
  raises `InsufficientInputsError` rather than inventing a system — the caller turns that into a
  ranked data ask / not-bankable quote (per the shared input-data rule).

Nothing in the model branches on `market == "GB"`; GB specifics live entirely in the assumption
library content.

---

## 6. What belongs to Sim's later portfolio model (out of scope here)

This is the **site-level** financial model. The following are explicitly **not** built here and
belong to the Finance (Sim) portfolio workstream (see [`CLAUDE.md`](../CLAUDE.md) §3 and the
boundary doc):

- Portfolio financial model, capex/opex/depreciation **across a fleet**, yield-vs-cost-of-capital, equity retention, MOIC.
- The **credit policy** and the real per-site debt structure (credit line + warrant); v1 carries placeholder per-site debt terms only and reports DSCR unavailable until they exist.
- **Fundraising** artifacts and portfolio concentration limits.
- **Monte Carlo** / stochastic risk — deferred to portfolio work behind the same interface; v1 is discrete and deterministic (P50/P90 by haircut, scenarios by the M6 engine).
- The M9 **scoring tool / research agent** (ownership unresolved, B001). `agent/finance` owns only the deterministic score → rate/tenor *mapping* once that input exists.

---

## 7. Determinism & traceability

- Identical `SiteModel + OptimisationResult + assumption_version + horizon + scenario` → identical `FinancialCase` (verified by a determinism test). `case_id` is derived from the run's input hash, not the clock.
- Timestamps (`created_at`) are **excluded** from the run hash — per the data contract, timestamps are metadata and never feed a calculation.
- Every number traces to an input or a library assumption read via `load_assumptions(version)`. There are **no private £/%/ratio constants** in the finance code (the only module constants are the structural projection-horizon default and the IRR numerical-search bounds, neither of which injects an underwriting number).

## 8. Tests

[`tests/test_finance_cashflows.py`](../tests/test_finance_cashflows.py),
[`tests/test_finance_debt.py`](../tests/test_finance_debt.py),
[`tests/test_finance_returns.py`](../tests/test_finance_returns.py) cover: the financial case
validates; P50/P90 present and P90 ≤ P50; DSCR unavailable when debt assumptions are missing;
placeholder assumptions produce warnings; major assumptions are not hidden constants (output
scales when the library value changes); deterministic output on identical inputs; negative capex
rejected; plus the debt-schedule/DSCR/covenant maths, payback and IRR robustness.
[`tests/test_goal_invariants_finance.py`](../tests/test_goal_invariants_finance.py) locks the
goal invariants: P90 ≤ P50 with degradation compounding (revenue actually falls year over year);
negative haircut clamped with a warning; the sculpted schedule's PV equals the principal and its
DSCR is flat at the computed target; the annuity fallback and the recorded applied basis; the
covenant gate firing in real mode on `gb_v1_2` while staying not bankable; and the release-gated
degradation marginal cost line.
[`tests/test_lender_terms.py`](../tests/test_lender_terms.py) covers `LenderTerms` (every core term
required, no defaults, fraction guard, covenant coherence), each case, each amortisation shape, the
target-sized sculpt, the covenant tiers with no library fallback, the refused sub-annual period and
the `case_id` keys (D75 to D78). [`tests/test_dscr_on_every_run.py`](../tests/test_dscr_on_every_run.py)
is the WP-UW-03 acceptance: a DSCR or its reason through `generate_quote`, the CLI and the app,
quote identity unchanged without terms, and the B001 register row's pointers. All lender terms in
tests are synthetic and labelled so.
[`tests/test_finance_lifecycle.py`](../tests/test_finance_lifecycle.py) and
[`tests/test_lifecycle_entry_points.py`](../tests/test_lifecycle_entry_points.py) are the WP-UW-17
acceptance: levelisation and the closed loop, the CPI reader with a hand recomputation, CM indexation,
construction and IDC by hand, the applied COD delay, each lifecycle flow, the CLI, the app and the pack.
