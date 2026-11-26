# WP-0 — completing the hostile review

**Scope of this document.** The previous hostile pass ran 6 of 9 lenses and lost its entire
refutation stage to a usage limit, leaving 52 of its 54 findings unverified. This run does three
things: it runs the three lenses that never ran (finance, sizing, orchestration), it puts every new
finding through an adversarial refutation panel before recording it, and it audits the pipeline
against a repo-blind literature checklist of MUST requirements. **The 54 findings from the earlier
pass are not restated or re-verified here** — this register covers only what this run produced.

**Path convention.** Source paths in the source findings were written relative to the package root.
In this repository the package root is `src/gridq/`, and the assumption release is `assumptions/gb_v1_6.yaml`
(the literature checklist's `pipeline/` prefix is the same tree). Every `file:line` below has been
rewritten to the real path and spot-checked against the working tree.

**Counts.** 31 findings survived refutation. Two of them (one from the finance lens, one from the
orchestration lens) are the same defect and have been merged, so the register carries **30**:
9 HIGH, 14 MEDIUM, 7 LOW; 27 Tier A, 3 Tier B. Seven further candidate findings were killed by the
refutation panel and are recorded in §5 so nobody re-raises them. The literature audit adds 23
unmet MUST requirements (22 Tier A, 1 Tier B).

**Merged duplicates.** N18 (orchestration lens, `quotes/sensitivities.py:81`) and N24 (finance lens,
`quotes/sensitivities.py:50`) are the same defect — `cod_delay_months` is reported on a sensitivity
row and never reaches the financial model. They are merged under **N18** and carried at the higher of
the two refuted severities (MEDIUM). N24 is retired; do not re-file it.

---

## 1. What this closes

The finance lens had never run, and finance is where the model makes its most load-bearing claims:
the DSCR the covenant is tested on, the IRR the sizing objective maximises, and the £/kWh price the
customer is quoted. It found ten defects, three of them HIGH.

The single largest thing it found is an absence: **there is no corporation tax anywhere in the tree**.
Not a rate, not a capital-allowance basis, not a taxable-profit line, not an assumption-library
section — `grep -rniE 'tax'` over 108 files including `assumptions/gb_v1_6.yaml` returns zero hits.
The pre-tax EBITDA series is renamed CFADS at the debt boundary and tested against a post-tax
covenant threshold, and the same pre-tax series is the project IRR and the equity IRR. Every finance
number in the pack is on a basis the pack never states.

Two more HIGH findings are arithmetic rather than absence, and both are live on the shipped library:

- The P90 row deducts the Capacity Market non-delivery penalty **on top of** 85% of the same CM
  revenue. A de-rated, contracted CM agreement therefore makes P90 EBITDA *worse*: it contributes
  +R to P50 and −0.15R to P90. Winning a CM contract can flip a specification from feasible to
  infeasible.
- The price-to-target recovers 100% of capex and opex from the customer while the project keeps the
  entire value stack it has already booked as revenue. That double-count is the direct cause of the
  £1.06/kWh artefact the code's own comment records against a site paying £0.21/kWh.

Alongside those, the sizing lens found that **the reported optimum is not an optimum on the default
path**: with no counterparty credit assessment (the default), no candidate is feasible, and the
engine falls back to ranking by gross priced-floor revenue — a metric with no capex term — while
project IRR sits computed and unused on every candidate. The orchestration lens found that the
quote's Low/Central/High DSCR sensitivity table is three identical rows, and that folding a
half-hourly file into a site model silently promotes a *modelled* profile to `METERED` with a `high`
confidence ceiling.

---

## 2. Finance findings

Ten findings. Presented most severe first. Every one was confirmed against source by an adversarial
refutation panel; where the panel corrected the filing, the correction is stated.

### N1 — No corporation tax exists anywhere in the model; DSCR, project IRR and equity IRR are all pre-tax
**HIGH · Tier B · `src/gridq/finance/cashflows.py:896`**

**Defect.** `p50_ebitda` / `p90_ebitda` are built as revenue − dispatch cost − operating cost, and
`p50_fcf_pre_debt_gbp` is that series with capex prepended. There is no tax line, tax rate, capital
allowance or taxable-profit computation anywhere in the tree, and `assumptions/gb_v1_6.yaml` has 25
sections (battery_capex … tnuos), none of them tax. That pre-tax series is then used verbatim as
CFADS for the DSCR, as the project IRR series and as the equity IRR series.

**Evidence.**
- `src/gridq/finance/cashflows.py:896` — `p50_ebitda.append(round(p50_rev - dispatch_cost - operating_cost, 2))`
- `src/gridq/finance/cashflows.py:904` — `p50_fcf = [round(-capex.total_gbp, 2), *p50_ebitda]`
- `src/gridq/finance/financial_model.py:110` — `p90_cfads = cashflows.p90_fcf_pre_debt_gbp[1:]` (renamed CFADS at the debt boundary)
- `src/gridq/finance/financial_model.py:127`, `:138` — the same P50 series as the project-IRR and equity-IRR inputs
- `src/gridq/finance/debt.py:325` — `dscr_by_year(cfads_window, service)` measured against `dscr_covenants.min_p90_dscr = 1.30` (`assumptions/gb_v1_6.yaml:246-247`)

**Consequence.** Lenders size and test against post-tax CFADS. Reporting a pre-tax ratio against a
post-tax covenant overstates coverage by an unquantified, **back-loaded** amount — not by the tax
rate, because tax is charged on taxable profit after capital allowances and interest, so the early-year
charge on a capital-intensive asset is near zero and the degradation lands in the late years, which
are also the binding DSCR years. Worked illustration (1 MW / 2 MWh, capex £500k, gearing 60% →
principal £300k, strong band 6.0%/12y, annuity service £35,783.11/yr, P90 CFADS £60k fading 2.5%/yr):
reported min P90 DSCR 1.2692. A naive 25% charge on the same CFADS gives 0.9519 — that figure is an
upper bound on the error, not the error, but it shows the direction and that the case can sit below
1.0x rather than marginally below 1.30.

**Fix (Tier B — needs a human input first).** A human must supply the tax basis: the SPV structure,
whether the BESS qualifies for full expensing / main-rate pool / special-rate pool, the WDA timing,
and loss carry-forward treatment. None of that is derivable from the code or from GB market
mechanics. Once supplied: add a `tax` section to the assumption release (statutory main rate +
capital-allowance basis), compute a taxable-profit line in `build_cashflow_model`
(EBITDA − capital allowances − debt interest → tax → post-tax CFADS), and feed the **post-tax** series
to `assess_debt` as CFADS and to `project_irr` / `equity_irr`.
**The Tier A subset, to do now, without waiting for the tax basis:** attach a `PRE-TAX — no
corporation tax is modelled; this ratio is not comparable to a post-tax covenant` warning to
`DscrSummary` and to `FinancialCase`, in exactly the style this module already uses for every other
missing input. Do not let `p50_fcf_pre_debt_gbp` reach `assess_debt` unlabelled.

**Panel correction.** Tier A → Tier B (the end state is not determinable from the code). Magnitude
claim softened from "overstates coverage by roughly the tax rate" to "by an unquantified, back-loaded
amount". Severity held at HIGH rather than CRITICAL because every case on the shipped library is
already stamped NOT bankable (`financial_model.py:165-175`, since `dscr_covenants.min_p90_dscr` is
`status: placeholder`), so no bankable verdict is currently issued off the pre-tax ratio.

---

### N2 — The CM non-delivery penalty is subtracted on top of 85% of the same CM revenue, so a Capacity Market contract makes P90 EBITDA worse
**HIGH · Tier A · `src/gridq/finance/cashflows.py:897`**

**Defect.** The P90 row keeps `contracted_base × (1 − contracted_haircut/100)` of contracted revenue
**and separately** subtracts `penalty_base`, which is `cm_revenue × penalties.cm_annual_cap_pct_of_revenue / 100`.
These are two deductions for one event: the annual penalty cap binds only if you failed to deliver,
in which case you also forfeit the availability revenue. The same double-hit is applied again in the
contracted-basis series at `:901`.

**Evidence.**
- `src/gridq/finance/cashflows.py:892-897` — `p90_rev = (firm_base * (1 - haircut/100) + contracted_base * (1 - contracted_haircut/100)) * decay`, then `p90_ebitda.append(round(p90_rev - penalty_base * decay - dispatch_cost - operating_cost, 2))`
- `src/gridq/finance/cashflows.py:899-901` — repeated on `p90_contracted_ebitda`
- `src/gridq/finance/cashflows.py:760` — `exposure = round(cm_revenue * pct / 100.0, 2)`
- `assumptions/gb_v1_6.yaml:557-559` — `cm_annual_cap_pct_of_revenue: 100.0`
- `assumptions/gb_v1_6.yaml` `p50_p90_haircuts.p90_revenue_haircut_pct: 15.0`; `p90_revenue_haircut_contracted_pct` is **absent**, so `_contracted_haircut_pct` (`cashflows.py:686-693`) returns the 15% merchant default

**Consequence.** A CM stream contributes **+R to P50 and −0.15R to P90**. Two otherwise identical
sites, one holding a £50,000/yr CM agreement and one holding none, produce a P90 EBITDA **£57,500/yr
lower for the site that won the contract**. Because `p90_fcf` is the CFADS input
(`financial_model.py:110-121`), this reduces the min P90 DSCR and can flip `SizingObjective.feasible`
(`sizing/objective.py:84`) from True to False. `p90_contracted_ebitda` — the lender-basis series — is
negative by arithmetic for any purely contracted site. The docstring at `cashflows.py:156-159`
rationalises this as deliberate conservatism; it is not conservatism, it is a double-count. The cap
is *defined* as a percentage of the annual capacity revenue, so the bounded worst case is losing that
revenue (net 0). You cannot lose 115% of a 100%-capped stream.

**Fix.** Drive both terms from one shortfall number. In the non-delivery scenario the contracted line
is `contracted_revenue × (1 − shortfall) − penalty(shortfall)`, with the same `shortfall` in both
terms. Bound the combined deduction at the contracted revenue itself, and assert the invariant
`P50_contracted_contribution ≥ P90_contracted_contribution ≥ −cap × R` as a test. If the shortfall
model is not built immediately, the interim is to apply the penalty to un-haircut revenue and take
the *worse* of the two deductions, never their sum.

**Panel correction.** None. Confirmed as filed.

---

### N3 — The price-to-target recovers 100% of capex from the customer while the project also keeps the entire value stack
**HIGH · Tier A · `src/gridq/finance/pricing.py:58`**

**Defect.** `annual_revenue_requirement` returns `capex_gbp * crf + annual_opex_gbp` with no deduction
for the revenue the asset already earns. `run_financial_model` calls it with the full capex and no
reference to `cashflows.total_annual_revenue_gbp` or the P50 EBITDA it has just computed. Two
secondary errors sit on the same line: the CRF is taken at `financing.target_equity_irr_pct` (12.0)
applied to **total** capex — the equity hurdle charged against debt-funded capital, live because
`debt_gearing_pct: 60.0` is in the shipped library — and `annual_opex_gbp` is
`total_annual_operating_cost_gbp`, which excludes `annual_dispatch_cost_gbp`.

**Evidence.**
- `src/gridq/finance/pricing.py:58-59` — `crf = capital_recovery_factor(target_irr_pct, horizon_years)` / `return round(capex_gbp * crf + annual_opex_gbp, 2)`
- `src/gridq/finance/financial_model.py:147-156` — passes `capex_gbp=cashflows.capex.total_gbp` and `annual_opex_gbp=cashflows.total_annual_operating_cost_gbp`, no revenue argument
- `src/gridq/finance/pricing.py:171` — `price = fixed_price_per_kwh(requirement, annual_delivered_energy_kwh)`
- `assumptions/gb_v1_6.yaml:213-214` — `debt_gearing_pct: 60.0`
- Corroborating the double-count from the code's own convention: `optimisation/valuation.py`'s
  `PPA_OR_BILL_SAVINGS` stream is priced at £0 with the note that the import-side floor is itemised
  across tariff arbitrage + DUoS red-band and "the PPA £/kWh is set by the finance pricing layer" —
  i.e. the same import-side saving is booked once as project revenue and charged again in full
  through the price.

**Consequence.** The recommended price is inflated by the full value of the revenue stack divided by
delivered energy. Worked: capex £500,000, opex £25,200/yr, horizon 15y, target 12% → CRF 0.146824,
requirement £98,610/yr; over 400,000 kWh that is **£0.2465/kWh**, above a £0.21/kWh counterfactual, so
`exceeds_counterfactual` fires and the site is reported not commercially viable. Net the £48,000/yr
the project already earns from arbitrage and DUoS avoidance and the residual requirement is
£50,610/yr → **£0.1265/kWh**, comfortably below the counterfactual. Same site, opposite verdict,
depending only on whether the booked revenue is netted. This is the mechanism behind the £1.06/kWh
artefact recorded at `pricing.py:91-92`; the 2026-07-07 change added the `exceeds_counterfactual` flag
and left the arithmetic that produces the impossible number untouched.

**Fix.** Change the signature to
`annual_revenue_requirement(capex, annual_opex, annual_dispatch_cost, other_annual_revenue, target_rate, horizon)`
returning `max(0, capex*crf + annual_opex + annual_dispatch_cost - other_annual_revenue)`, and pass
`cashflows.total_annual_revenue_gbp` (post `price_multiplier`, net of the perfect-foresight
adjustment) and `cashflows.annual_dispatch_cost_gbp` from `run_financial_model`. Discount at a
blended rate consistent with the resolved debt structure, not at the equity hurdle applied to total
capital.

**Panel correction.** Two, neither fatal. "Every viable site is now labelled not-commercially-viable"
is rhetorical — `exceeds_counterfactual` fires only when the computed price exceeds the blended import
rate (`pricing.py:183-192`), which depends on the capex/energy ratio. And the excluded dispatch cost
pushes the requirement *down*, partially offsetting the overstatement rather than compounding it.

---

### N4 (finance-adjacent, filed under sizing) — see §3
The default-path selection metric is the largest single determinant of every finance number in the
pack, because the whole financial case is computed for whatever specification the selector returned.
It is filed under sizing but must be read as a precondition for trusting any figure in this section.

---

### N10 — Min DSCR is measured over a truncated window when the horizon is shorter than the tenor
**MEDIUM · Tier A · `src/gridq/finance/debt.py:319`**

**Defect.** `cfads_window = p90_cfads_by_year[: terms.tenor_years]` silently yields a shorter list when
the projection horizon has fewer operating years than the debt tenor; `dscr_by_year` zips with
`strict=False` and truncates; `min(series)` is then taken over only the measured years. Revenue decays
at `degradation.annual_capacity_fade_pct` while costs are held flat, so CFADS falls monotonically and
**the dropped years are always the worst ones**. The result is still returned with `available=True`.

**Evidence.**
- `src/gridq/finance/debt.py:319-325` — the window and `dscr_by_year(cfads_window, service)`
- `src/gridq/finance/debt.py:279` — `zip(cfads_by_year, debt_service_by_year, strict=False)`
- `src/gridq/finance/debt.py:335-337` — `available=True`, `min_dscr=round(min(series), 4)`
- `src/gridq/sizing/objective.py:84` — that number is the hard feasibility gate; `objective.py:20-22` states the binding year may be late-life
- `src/gridq/finance/cashflows.py:890-896` — monotonic decay against flat costs
- Contrast `src/gridq/finance/debt.py:243-253` — `sculpted_schedule` *refuses* this exact condition and degrades to the annuity, so the module already knows the case is dangerous

**Consequence.** A covenant the project fails is reported as passed, and the feasibility gate — the one
thing standing between a sub-covenant spec and a recommendation — admits it. Worked: capex £500k,
gearing 60%, 6.0%/12y, service £35,783.11/yr, P90 CFADS £60k fading 2.5%/yr, caller passes
`horizon_years=10`. Measured min DSCR **1.3351 → passes 1.30**; true min over the 12 debt years
**1.2692 → fails**. The two dropped years are precisely the binding ones. Latent, not live:
`DEFAULT_HORIZON_YEARS = 15` and every shipped tenor is ≤ 12 years (`assumptions/gb_v1_6.yaml:229-231`:
strong 12y, adequate 10y, weak 7y), and `cli.py` exposes no horizon flag — it fires only through the
public `horizon_years=` parameter (`financial_model.py:71`, `sizer.py:364`, `two_stage.py:106`).

**Fix.** When `len(p90_cfads_by_year) < terms.tenor_years`, return
`DscrResult(available=False, ...)` carrying the existing explanatory warning. Never return
`available=True` on a partial window.

**Panel correction.** Severity HIGH → MEDIUM (latent on every shipped path). Tier A upheld.

---

### N11 — `equity_irr` silently drops all debt service beyond the projection horizon, converting an IRR-refused series into a reported positive return
**MEDIUM · Tier A · `src/gridq/finance/returns.py:211`**

**Defect.** The equity cashflow loop iterates over the **operating** cashflow list, not the debt
schedule, so entries of `debt_service_by_year_gbp` with index ≥ `len(operating_fcf)` are never
subtracted. The equity investor is credited with the full year-0 debt draw and never charged for the
tail of the loan that repays it. There is no symmetric branch for `t >= len(operating_fcf)`.

**Evidence.**
- `src/gridq/finance/returns.py:210-213` — `equity_cf = [-equity_outlay]` … `service = debt_service_by_year_gbp[t] if t < len(debt_service_by_year_gbp) else 0.0`
- `src/gridq/finance/returns.py:198` — `equity_outlay = capex - principal` (full principal credited at t=0)
- `src/gridq/finance/debt.py:317` — `level_payment_schedule` always emits exactly `tenor_years` payments
- `src/gridq/finance/returns.py:123-131` — `project_irr`'s sign-change reliability gate, which the truncation bypasses

**Consequence.** The levered IRR — the metric the sizing objective maximises
(`sizing/objective.py:109-115`, `sizing/evaluator.py:167`) and the headline in the sensitivity table —
is flattered by the present value of every dropped payment, and the truncation removes the late
negative flows that make the series non-conventional, so a number is published where the honest
answer is "unavailable". Worked: capex £500k, gearing 60%, 6.0%/12y, annuity £35,783.11/yr,
`horizon_years=10`, P50 operating FCF £60k/yr → reported equity IRR **+3.6389%**. The true series
includes years 11 and 12 at −£35,783.11 each, has two sign changes, and `project_irr` would return
unavailable with "IRR may be non-unique". A refused metric is published as +3.64%. Latent for the same
reason as N10 — `DEFAULT_HORIZON_YEARS = 15` > every shipped tenor, and neither `cli.py` nor
`quotes/pipeline.py` passes `horizon_years`.

**Fix.** Extend the equity series to `max(len(operating_fcf), len(debt_service))` periods, carrying
0.0 operating cashflow for the tail years so the remaining debt service appears as the negative flow
it is. If zero post-horizon cash cannot be justified, return `EquityIrrResult(available=False)` naming
the horizon/tenor mismatch — the same degrade `size_and_measure_dscr` already performs.

**Panel correction.** Severity HIGH → MEDIUM (latent on every shipped path).

---

### N12 — `hits_target_return=True` is asserted for the equity hurdle by a construction that is unlevered, pre-tax and assumes level volume
**MEDIUM · Tier A · `src/gridq/finance/pricing.py:174`**

**Defect.** `hits = target.status == AssumptionStatus.CONFIRMED`, and the recommendation is returned
with `hits_target_return=True` on the claim that the price "meets target by construction". It does
not. The key is `financing.target_equity_irr_pct` — an **equity** hurdle — but the CRF is applied to
total capex with no debt, so the construction targets an *unlevered* return. It is also pre-tax (N1),
and it assumes level annual collection, whereas a fixed £/kWh price applied to a volume fading at
`degradation.annual_capacity_fade_pct` collects a declining amount.

**Evidence.**
- `src/gridq/finance/pricing.py:174`, `:194-197` — the status-derived boolean
- `src/gridq/finance/pricing.py:58`, `:168-170` — CRF on full `capex_gbp`, no principal/service split
- `src/gridq/finance/cashflows.py:890` — `decay = (1.0 - fade_pct / 100.0) ** (year - 1)`, with `degradation.annual_capacity_fade_pct = 2.5` (`assumptions/gb_v1_6.yaml:146-147`)
- Nothing compares the price to `case.equity_irr`, which the same run computes at `financial_model.py:134-139`

**Consequence.** Once `financing.target_equity_irr_pct` is confirmed, the `FinancialCase` asserts to a
reader that the recommended price achieves the 12% equity target; with 60% gearing, tax and 2.5%/yr
fade the achieved equity IRR at that price is materially different and is never checked. Worked:
target 12%, horizon 15, capex £500k → CRF 0.14682, level requirement £98,610/yr; a fixed £/kWh over a
volume fading 2.5%/yr collects **12.55 year-equivalents of year-1 volume, not 15**, so the recovered
present value falls short of the level-annuity assumption and the realised unlevered return is below
12% before any tax or leverage effect. **What is live today** is not the boolean — with the shipped
library `financing.target_equity_irr_pct` is `status: needs_review` (`assumptions/gb_v1_6.yaml:203`),
so `hits` is False and the field serialises as None — but the **price value itself**
(`pricing.py:171`), which is always reported and drives the counterfactual-viability verdict at
`:182-192` on the same unlevered, level-collection, pre-tax construction.

**Fix.** Two parts. (a) Levelise against the degradation profile: the requirement must be recovered
over the discounted **faded** volume, not a level annuity — pure arithmetic, do it now. (b) Compute
the price, then verify it: rebuild the P50 cashflows with the priced revenue substituted, run
`project_irr` / `equity_irr` on the result, and set `hits_target_return` from the **measured** equity
IRR against the target within a stated tolerance. Never set it from an assumption's status field; if
verification cannot run, leave it None as the docstring at `pricing.py:82-84` already promises.

**Panel correction.** Dormancy caveat added — the boolean cannot fire on the shipped library; the
live defect is the price value. Tier A holds for the degradation-weighted levelisation (arithmetic);
the levered-vs-unlevered basis choice is the only judgement call in it.

---

### N13 — No inflation index exists anywhere, while nominal debt rates and a nominal equity hurdle are applied to flat-nominal revenue and opex
**MEDIUM · Tier B · `src/gridq/finance/cashflows.py:890`**

**Defect.** Revenue is projected with the degradation decay only; costs are held constant across the
horizon. There is no CPI/RPI escalation on either side —
`grep -rniE 'inflation|cpi|rpi|escalat'` over the tree and the release returns nothing. Against that
real-terms-shaped cashflow the model applies unambiguously nominal rates.

**Evidence.**
- `src/gridq/finance/cashflows.py:889-902` — decay is the only year-on-year adjustment; `cashflows.py:852` computes `operating_cost` once and reuses it every year; `cashflows.py:124-126` concedes "costs held flat"
- `assumptions/gb_v1_6.yaml:227-231` — `financing.credit_rate_tenor_policy` 6.0 / 7.5 / 9.5%, into `debt.py:209` and `debt.py:265`
- `assumptions/gb_v1_6.yaml:199-202` — `financing.target_equity_irr_pct = 12.0`, sourced from an investor's **nominal** expectation, used undeflated in `pricing.py:30-43`

**Consequence.** The basis is stated nowhere and is internally inconsistent in both directions:
flat-nominal opex understates cost by ~41% by year 15 at 2.5% CPI (flattering — £25,200 modelled vs
£35,637 actual, a £10,437 gap in the year that binds the DSCR), while flat-nominal revenue understates
revenue (conservative). They do not cancel, and the DSCR is measured against a nominal debt service
whose real burden falls every year. The covenant verdict and both IRRs are therefore neither a real
nor a nominal number.

**Fix (Tier B — needs a human input first).** A human must supply the escalation index and rate and
say which lines are indexed (opex vs contracted vs merchant revenue; a CM T-4 agreement is CPI-linked,
a merchant arbitrage spread is not). Then either (a) go nominal: add an `inflation` section, escalate
revenue and every operating cost line, keep the existing nominal debt rate and hurdle; or (b) go real:
keep flat cashflows and deflate the debt rate and equity hurdle before use in `level_payment_schedule`,
`sculpted_schedule` and `capital_recovery_factor`. Option (b) needs no new value beyond the index.
**The Tier A subset, to do now:** state the basis explicitly on every `FinancialCase` and emit the
"no escalation is modelled" data-ask warning, exactly as every other missing key in `cashflows.py` does.

**Panel correction.** Tier A → Tier B for the substantive fix; the labelling subset stays Tier A.
See also literature requirement **H-15**, which is the same gap raised repo-blind.

---

### N14 — The FCF series has no terminal value, no decommissioning cost and no replacement capex across a 15-year horizon
**MEDIUM · Tier B · `src/gridq/finance/cashflows.py:904`**

**Defect.** `p50_fcf = [round(-capex.total_gbp, 2), *p50_ebitda]` — year-0 capex, then pure operating
EBITDA to the horizon, then nothing. No residual value at horizon end, no decommissioning or
site-restoration outflow, no mid-life inverter/BOP replacement. `COST_LINE_KEYS`
(`cashflows.py:40-48`) contains no capital line, and a tree-wide grep for `decommission|terminal value`
returns nothing.

**Evidence.** `src/gridq/finance/cashflows.py:904-905` (the only capital flow in either series is
index 0); `src/gridq/finance/cashflows.py:40-48`; `src/gridq/finance/cashflows.py:414-422` (the comment
that removed the former warranty/replacement provision as a double-count of the revenue fade).

**Consequence.** Two unquantified omissions of opposite sign sit on the headline project IRR, the
equity IRR and the payback, with **no disclosure of either**. On a £500k / 15-year case at ~£22,800/yr
P50 EBITDA, a residual value of 15% of capex (£75,000) at year 15 moves the project IRR by roughly a
percentage point; a £30,000 decommissioning obligation plus the funded reserve a lender will require
moves the late-life DSCR — the binding year — the other way.

**Fix (Tier B — needs a human input first).** A human must decide whether the business books a
residual value at horizon end and on what basis (resale, re-contract, second life), and what
decommissioning/restoration obligation the host-site agreements actually impose. Once decided these
become assumption-library sections and the change is mechanical: append a terminal flow to
`p50_fcf`/`p90_fcf` and a funded-reserve line to the cost lines.
**The Tier A subset, to do now:** warn on every `FinancialCase` naming both omissions, in the same
style as the existing unavailable-line warnings.

**Panel correction.** The no-augmentation convention (day-one sizing for end-of-life capability)
genuinely disposes of *augmentation* capex, so that limb of the title is weaker than the other two.
Inverter/BOP replacement, residual value and decommissioning remain unpriced and undisclosed.

---

### N25 — The provenance hash omits the debt terms and price structure, so materially different financial cases share a `run_id` and a `case_id`
**LOW · Tier A · `src/gridq/finance/financial_model.py:321`**

**Defect.** `_hash_input` returns site, result, assumption version, horizon, scenario id, the two
multipliers and the dispatch volumes. It does **not** include `debt_terms` (gearing, rate, tenor,
sculpting basis) or `price_structure`, both of which are live parameters of `run_financial_model` and
both of which change the output.

**Evidence.** `src/gridq/finance/financial_model.py:321-331` (the returned dict);
`:177-182` (`build_run_context(_hash_input(...))`); `:185` (`case_id = f"fc_{run_context.run_id}"`);
`src/gridq/provenance/run_context.py:51-59`. `debt_terms` is threaded from
`quotes/pipeline.py:78→191` and `quotes/sensitivities.py:32→55`.

**Consequence.** Run A (gearing 60%, min DSCR 1.2692, covenant fails) and Run B (gearing 40%, min DSCR
1.90, covenant passes) emit the **same** `case_id`. The reproducibility pin promised at
`financial_model.py:8-10` does not hold for the debt structure — the single most contested input in
the pack. Contained one layer up: `quotes/assembly.py:87-89` hashes the whole
`financial_case.model_dump()` into the quote-level hash, so quote ids, artifact directories and
persisted records still differ. Nothing is overwritten; the false claim is the pin itself.

**Fix.** Add both to `_hash_input`:
`'debt': None if debt_terms is None else {'gearing_pct': …, 'annual_rate_pct': …, 'tenor_years': …, 'sculpting_basis': …}`
and `'price_structure': price_structure.value`. Hash the **resolved** terms (after the credit mapping)
so a case driven by the credit band and one with the same terms injected hash identically.

**Panel correction.** Severity MEDIUM → LOW — the quote-level hash prevents the claimed artifact
collision.

---

### N26 — Parasitic auxiliary energy is priced at the mean import rate excluding levies
**LOW · Tier A · `src/gridq/finance/cashflows.py:471`**

**Defect.** `blended_rate = sum(import_rates.values()) / len(import_rates)` omits the site's £/kWh levy
lines, while `_blended_import_rate` in the same run computes
`sum(rates.values()) / len(rates) + levies`. Auxiliary draw is imported grid energy and attracts levies
exactly as any other import does — which is the same reasoning the levies-on-charge line uses.

**Evidence.** `src/gridq/finance/cashflows.py:471-473` vs `src/gridq/finance/financial_model.py:246`;
`src/gridq/finance/cashflows.py:430`, `:447-448` (levies charged on net import);
`src/gridq/optimisation/milp_solver.py:552` — `charge_ub = np.minimum(power, np.maximum(0.0, conn_limit - load - aux_kw))`,
i.e. the aux draw is treated as real continuous grid import that consumes connection headroom.
Nothing else charges levies on `aux_kwh` — the levy line uses only `charge_kwh − discharge_kwh` from
`DispatchEnergy`, which does not include the parasitic draw.

**Consequence.** The parasitic cost line is understated by `levy_rate × aux_kwh` every year, flattering
EBITDA, the DSCR and both IRRs. Worked: 1 MW system, `parasitic_load_fraction_of_power = 0.01`
(`assumptions/gb_v1_6.yaml:128-129`) → 0.01 × 1,000 kW × 8,760 h = **87,600 kWh/yr**; at a £0.02/kWh
levy stack that is **£1,752/yr** omitted, against a year-1 P50 EBITDA of roughly £22,800 — a **7.7%
overstatement of EBITDA**, recurring every year of the horizon.

**Fix.** Extract the blended-rate calculation into one shared helper and call it from both sites, so
the parasitic line is priced at `mean(import_rates) + sum(levies)`. Update the disclosure at
`cashflows.py:474-479` to state that levies are included, and correct the false parity claim at
`financial_model.py:143-146`.

**Panel correction.** One citation in the original filing was misquoted: the comment at
`cashflows.py:450-455` does **not** claim parity with the counterfactual — it accurately describes
line 471. The false parity claim is at `financial_model.py:143-146` only. The defect stands.

---

## 3. Sizing and orchestration findings

### Sizing optimiser (9 findings)

**N4 — The reported optimum is selected by gross revenue, not by the objective the search optimised, and this is the DEFAULT path. HIGH · Tier A · `src/gridq/optimisation/engine.py:599`.**
`MilpEngine._choose` returns `run.selected` only when it exists; otherwise it ranks priceable
candidates by `_floor_value` — total annual FIRM/CONTRACTED revenue — with size as tie-break.
`run.selected` needs `objective.feasible`, which needs `min_p90_dscr is not None`, which needs
`DebtTerms`. `_run_search` passes no `debt_terms` to `two_stage_sizing` (`engine.py:547-557`) or
`enumerate_sizing` (`:560-569`); `run_financial_model` then derives them from the credit band, and
`map_credit_to_rate_tenor` returns None for anything outside {strong, adequate, weak}
(`finance/credit.py:154-158`), while `CounterpartyProfile.credit_status` defaults to
`CreditStatus.UNKNOWN` (`schemas/counterparty.py:22`). So **on any site without an explicit credit
assessment — the default — every candidate is infeasible and gross revenue picks the battery**, while
a site *with* a credit band gets the IRR-ranked winner. The switch is silent. `_floor_value`
(`engine.py:608-618`) contains no capex term and cannot be a sizing objective; it also contradicts the
module's own stated fallback ranking (`sizing/objective.py:12-14`, "the objective ranks on the
unlevered project IRR as a labelled fallback"). Illustration: an IRR-optimal 350 kW/2 h at 9.1% IRR
and £96k/yr floor revenue loses to a 1500 kW/8 h at 2.4% IRR and £188k/yr — a ~£2.3m 12 MWh quote in
place of a 700 kWh system that was evaluated, ranked first by the search, and discarded.
**Fix:** rank the fallback pool by `objective.rank_key` restricted to `candidate_viable` candidates
(project IRR when equity IRR is unavailable), and state on the run which metric selected the size.
*Panel correction: CRITICAL → HIGH — the fallback is documented at `engine.py:12-15` and every quote it
produces is stamped not-bankable/indicative.*

**N5 — The selection metric ignores `annual_cost_gbp`, so a net-cost candidate can outrank a net-positive one. HIGH · Tier A · `src/gridq/optimisation/engine.py:611`.**
`_floor_value` and its documented twin `priced_floor_value_gbp` (`sizing/evaluator.py:47-57`, whose
docstring claims it "Mirrors `MilpEngine._floor_value` exactly") sum only `v.annual_revenue_gbp`.
But `_split_signed` (`optimisation/valuation.py:144-152`) deliberately routes the negative part of a
signed dispatch component onto `annual_cost_gbp`, and the tariff-arbitrage stream is explicitly
allowed to be a net cost with a "NET COST £{cost}/yr" note (`valuation.py:485-497`). Finance nets it
(`cashflows.py:841`, `:896`); the ranking does not. Because charge-energy cost grows with size the
omission is size-correlated. Illustration: candidate A (200 kW/2 h) metric £42k = EBITDA £42k;
candidate B (800 kW/4 h) metric £78k but EBITDA £52k — the ranking sees a £36k gap where the true gap
is £10k, and B carries ~4× the capex.
**Fix:** make both functions compute `sum(v.annual_revenue_gbp - v.annual_cost_gbp for v in … if v.firmness in _PRICED_FIRMNESS)`.
The "kept in sync by a parity test" note means the test currently pins the wrong formula in two places.
*Panel note: `candidate_viable` pre-filters on the real IRR, so a wholly net-negative candidate cannot
be selected — the failure mode is inverted ranking among viable candidates.*

**N6 — A final "no viable configuration" verdict can rest on a shortlist chosen entirely by the typical-day proxy. HIGH · Tier A · `src/gridq/optimisation/two_stage.py:294`.**
Only `to_confirm` — the top `CONFIRM_TOP_N = 10` of the **proxy** ranking — is ever re-solved on the
full metered series (`two_stage.py:294-307`, `:310-335`), and only confirmed evaluations can be
selected (`:402`, `engine.py:589-596`). The proxy's representative day is the medoid of each
(month, weekday/weekend) stratum (`sizing/typical_periods.py:80-88`) — by construction the *most
typical* day, so annual-peak days, which drive DUoS red-band value and which larger batteries exploit,
are systematically absent. The polish stage probes only ±5%/±2.5% around the already-confirmed best
(`two_stage.py:342-400`) and cannot recover a region ranked outside the top 10. `engine.py:250-266`
then emits `SelectionStatus.NO_VIABLE_CONFIGURATION` — a FINAL verdict — with the exact evaluator that
would have found the viable size available and unused. Worse: when no candidate carries a positive
metric, `rank_key` degenerates to the `-power, -duration` tie-break (`objective.py:111-118`), so the
ten confirmations are literally the **ten smallest** candidates. Failure direction is a false negative
(a viable site refused), but a FINAL "this site does not work" resting on ten proxy-ranked points is
unjustified on its face.
**Fix:** before rendering a FINAL no-viable verdict, confirm a size-diverse cover on the full series —
stratify `to_confirm` across the power and duration ranges, not by proxy rank alone — or fall back to a
full-series sweep of the coarse grid. Separately, retain the extreme day per stratum alongside the
medoid, since the value being measured is peak-driven.

**N7 — "Connection reinforcement" is declared from a structural search bound, not from the connection. HIGH · Tier A · `src/gridq/optimisation/two_stage.py:219`.**
When the provisioning floor admits no coarse candidate, `ProvisioningInfeasibleError` is raised with
the customer-facing text "the obligation cannot be met by any battery this connection supports; the
honest recommendation is connection reinforcement". The domain in question is
`COARSE_POWER_FRACTIONS` × `COARSE_DURATION_HOURS` (`two_stage.py:60-76`), which the module itself
labels "Structural search-space constants (labelled; inject no £/%)", topping out at 1.50 × binding
capacity and 8.0 h — and binding capacity is the observed **peak**, falling back to MIC only when there
is no peak (`engine.py:518-523`). So the cap is a multiple of load, not of the connection.
`engine.py:231-240` converts the exception to `SolverStatus.INFEASIBLE` + `NO_VIABLE_CONFIGURATION`
with no spec. A 400 kW-peak site with a 700 kW / 6,000 kWh floor is told no battery its connection
supports can meet it, when its MIC may be 1,000 kW and a 700 kW / 8.6 h system is buildable and within
the connection.
**Fix:** test the provisioning floor against real physical limits (`connection.mic_kw` / `asc_kw`,
declared site constraints). If the floor exceeds the coarse grid but not the physical limit, extend
the search box to cover the floor and continue; raise the reinforcement verdict only when the floor
exceeds a limit that is actually a limit. The recharge-scan branch of `search_floors`
(`sizing/provisioning.py:177-185`) *is* a genuine connection-physics verdict and is correct to be final.

**N15 — The FINAL no-viable verdict quotes the proxy cloud as its evidence count. MEDIUM · Tier A · `src/gridq/optimisation/engine.py:263`.**
The verdict string formats `len(run.candidates_evaluated)` — the whole cloud, mostly typical-day proxy
solves — into "N candidate systems were evaluated (dispatch solved and priced)", while the viability
test three lines above filters on `not e.proxy` (`engine.py:253-257`). A run that confirms 10
candidates on the metered year reports ~280. **Fix:** report `len(solved_and_priced)` — already
computed at `:253-257` — and state the proxy-explored count separately and labelled as a proxy sweep.
*Panel correction: HIGH → MEDIUM — the verdict itself is correctly gated; only its evidence count is wrong.*

**N16 — The persisted sizing surface reports "no candidate selected" on runs that report a SELECTED spec. MEDIUM · Tier A · `src/gridq/sizing/sizer.py:161`.**
`SizingRun.sizing_grid()` copies `self.selected` (always None on the default no-credit path) and the
reason "no bankable candidate: no size meets the P90 DSCR covenant", while `MilpEngine._choose` picks a
candidate via the indicative fallback and `_final_result` stamps `selection_status = SELECTED`
(`engine.py:639-643`). Both objects are persisted onto the same `QuoteRecord`
(`quotes/pipeline.py:158-163`, `:212-226`). **Fix:** have `MilpEngine` record the indicatively-chosen
candidate back onto the `SizingRun` (or pass it into `sizing_grid()`) so the persisted grid marks the
point the quote reports, with a `selection_reason` naming the fallback metric. The grid must never
disagree with the result about whether a size was chosen.
*Panel correction: the recommendation IS matchable to a grid row via `selected_power_kw` /
`SizingGridPoint.power_kw` — the defect is the contradictory selection state, not an unauditable surface.*

**N27 — An unpriceable candidate is indistinguishable from a zero-value one in the continuous search's rank key. LOW · Tier A · `src/gridq/sizing/evaluator.py:171`.**
`_score` falls through to `(METRIC_PRICED_FLOOR, priced_floor_value_gbp(...))` for any evaluation with
no equity IRR and no project IRR — which covers both a candidate whose dispatch never solved
(`priceable=False`, floor 0.0) and a priced loss-maker. `search._rank_key` (`sizing/search.py:40-50`)
puts both in tier 0 and breaks ties on smaller system, so on an all-unviable domain
`dense_grid_search().best` is the smallest in-band corner presented as "the indicative winner" — the
exact failure `sizing/objective.py:99-103` records as the 2026-07-07 red-team fix. `candidate_viable`
is never imported into `search.py`. **Fix:** add `priceable` and a viability flag as the leading
components of `ScoredPoint`'s rank tier, and gate `best` on `candidate_viable`, returning None when
nothing is viable. *Panel correction: MEDIUM → LOW — no shipped path calls `dense_grid_search` or
`adaptive_refine_search`; it is an exported, documented API hole, not a customer-facing one.*

**N28 — CM de-rating is silently floored at the 4 h table key across the entire 4–8 h half of the search box. LOW · Tier A · `src/gridq/optimisation/_assumptions.py:63`.**
`value_for_duration` returns `(chosen, None)` — no warning — whenever the duration is at or above the
largest tabulated key, while its sibling `ceiling_value_for_duration` (`:101-106`) does warn above
range. The table has only 1/2/4 h (`assumptions/gb_v1_6.yaml:332-336`) and the search reaches 8 h, so
every candidate above 4 h is credited the 4 h factor. An 8 h / 500 kW candidate is credited 219.8 kW
where a linear scaled-EFC trend implies ~440 kW — ~£6.0k/yr vs ~£11.9k/yr at £27.1/kW/yr. The error is
**conservative** (it understates CM revenue and end-of-tenor capability), so no floor £, DSCR or IRR is
inflated; the harm is duration-axis search bias plus a missing disclosure. **Fix (Tier A):** emit the
warning above the largest tabulated key and surface it on the result. **Tier B:** adding 6 h / 8 h keys
needs a sourced NESO figure — the linear extrapolation is exactly the interpolation this module
refuses. *Panel correction: MEDIUM → LOW.*

**N29 — `search_space_size` counts the same candidate twice in the two-stage path, and the persisted surface carries duplicate specs. LOW · Tier A · `src/gridq/optimisation/two_stage.py:425`.**
In proxy mode `cloud = list(evaluations.values())` then `cloud.extend(confirmed)` — an append, not a
merge, in deliberate contrast to the non-proxy branch at `:409-413` which de-duplicates by key. Each
of the ≤10 confirmed coordinates appears twice with different IRR/DSCR/floor numbers, and
`search_space_size = len(cloud)` is then quoted as the size of the search when it is the length of the
evaluation log. **Fix:** apply the non-proxy branch's de-duplication (drop the proxy entry for any
(power, duration) that was confirmed), report `search_space_size` as the number of distinct candidate
specs, and expose proxy-vs-confirmed evaluation counts as separate fields. *Panel note: the duplicates
are labelled (`stage`, `proxy` on every grid point), so a renderer can recover a single-valued surface;
the unambiguously wrong number is `search_space_size`.*

### Scenarios, orchestration and quote assembly (11 findings)

**N8 — The quote's Low/Central/High DSCR sensitivity table is three identical rows. HIGH · Tier A · `src/gridq/quotes/sensitivities.py:45`.**
`compute_scenario_sensitivities` builds its default set with `build_standard_scenario_set(assumptions)`
and passes no band arguments; `scenarios/engine.py:369-372` defaults every unsupplied band to 1.0; and
`build_cashflow_model` reads only `price_multiplier` / `cost_multiplier` off the scenario
(`cashflows.py:814-815`). The three `run_financial_model` calls are therefore bit-identical, and Low,
Central and High carry equal `min_dscr` and `irr_pct` with `note=None`. The builder's own honest
data-ask — "no low sensitivity supplied — price and cost both held at identity (1.0), so this case
equals Central" (`scenarios/engine.py:348-355`) — lands on `Scenario.warnings` and is then **discarded**
one hop away (`sensitivities.py:72-83` sets `note` only for the project-IRR fallback). A lender reads
three equal rows as "the case is insensitive to price and cost" — the strongest possible statement
about the asset — when no sensitivity was run.
**Fix:** propagate `scenario.warnings` into `ScenarioSensitivity.note`, or refuse to emit the table
when every band is identity. Choosing the numeric band value is a separate Tier B sourcing question;
suppressing the false assertion is not.
*Panel correction: CRITICAL → HIGH — no computed figure is wrong; the three rows are the base case.*

**N9 — Folding a half-hourly file wipes `half_hourly_basis`, promoting a modelled series to METERED. HIGH · Tier A · `src/gridq/ingestion/assembly.py:27`.**
`assemble_site_model` rebuilds `load_data` wholesale from `to_load_data_summary(hh_summary)`, which
never sets `half_hourly_basis`, `half_hourly_reference_profile` or `half_hourly_scaled_to`
(`ingestion/hh_data.py:564-581`), and `_PRESERVED_LOAD_FIELDS` preserves only `annual_kwh`,
`max_import_kw`, `max_export_kw`. `admit_half_hourly` then hits its default branch and reports
`METERED` with a `high` confidence ceiling (`ingestion/hh_mandate.py:95-105`, `:78-86`), which
`quotes/pipeline.py:114-127` renders into the quote's Gate-2 line. The reachable path is
`gridq run site.json --hh profile.csv` (`cli.py:151-186`). The schema comment at `schemas/site.py:40-43`
names this exact failure — "the mandate gate would see a file path and report METERED, and the
confidence ceiling would be wrong in the flattering direction" — so the code violates a contract it
wrote for itself. **It is worse than filed:** `half_hourly_basis` is never *written* anywhere in the
tree; `ingestion/hh_synthesis.py` carries the modelled basis only on its own
`SyntheticHalfHourly.admission()` (`:243-248`), never back onto `SiteModel.load_data`, so any
synthesised series written to disk and attached also reads as METERED.
**Fix:** merge onto the existing `load_data` in `assemble_site_model` rather than replacing it, so any
field `to_load_data_summary` does not measure is carried forward by construction — and have
`hh_synthesis` stamp `half_hourly_basis="modelled"` onto the site it produces. Preserving the three
fields in `_PRESERVED_LOAD_FIELDS` closes only one of the two routes.

**N17 — Scenario-set warnings and `bankable_status` are computed then silently discarded. MEDIUM · Tier A · `src/gridq/quotes/sensitivities.py:47`.**
The loop reads only `scenario_id`, `name`, `cod_delay_months` and the two multipliers — never
`scenario.warnings`, `scenario_set.validation_warnings` or `scenario_set.bankable_status`, all of
which `build_standard_scenario_set` populates (`scenarios/engine.py:334-367`), and
`quotes/pipeline.py:205-226` assembles the record from `warnings_from_gate + extra_warnings` only, so
none can reach the `QuoteRecord` by another route. `assumptions/gb_v1_6.yaml:508-511` ships
`scenario_weights.central_low_high` as `status: placeholder`, so the set is NOT_BANKABLE on every run.
**Fix:** prepend `scenario.warnings` to each row's `note` and return the set's `validation_warnings`
(and a downgrade when `bankable_status is NOT_BANKABLE`) so `generate_quote` can merge them into
`extra_warnings`. *Panel correction: HIGH → MEDIUM — the placeholder-weights limb is immaterial here
(no weighted aggregate is computed); the material loss is the band-identity warning behind N8.*

**N18 (merged with N24) — `cod_delay_months` is reported on every sensitivity row and never applied to any number. MEDIUM · Tier A · `src/gridq/quotes/sensitivities.py:81`.**
The row asserts a COD delay, and the docstring says the delay is "reported, not just applied", but
`grep cod_delay_months` across `finance/` returns nothing: `build_cashflow_model` reads only the two
multipliers, and `_hash_input` (`financial_model.py:326-328`) omits the field, so two scenarios
differing only in COD produce identical numbers **and identical `case_id`s**. The intended semantics
exist and are implemented — `_shift_for_cod` (`scenarios/engine.py:134-153`) shifts operating
cashflows and drops revenue past the horizon — but that code path is `ScenarioEngine.run`, which the
sensitivity table never calls. Latent, not live: `build_standard_scenario_set` never passes
`cod_delay_months` and the field defaults to 0, so every shipped row honestly reports 0; it fires via
the public `scenario_set=` parameter, a supported documented entry point.
**Fix:** move `_shift_for_cod`'s logic into `build_cashflow_model` (zero-pad the operating years for
whole-year delays, capex stays at t=0, extend or truncate the horizon explicitly and warn either way;
sub-annual delays warn as they already do), add `cod_delay_months` to `_hash_input`, and until that
lands make `compute_scenario_sensitivities` null `min_dscr`/`irr_pct` and set `note` for any scenario
with a non-zero delay rather than reporting undelayed figures under a delayed label.
*Merged: this was filed twice — once by the orchestration lens (N18, refuted to MEDIUM) and once by
the finance lens (N24, refuted to LOW). Carried at MEDIUM. N24 is retired.*

**N19 — The T0-underwritten guard can never fire. MEDIUM · Tier A · `src/gridq/schemas/quote.py:93`.**
`_underwritten_from_t0_needs_warning` raises only when `not self.warnings`, and
`quotes/assembly.py:111-112` unconditionally appends `_NOT_BANKABLE_NOTE` for any status other than
`BANKABLE_CANDIDATE` — which no engine emits (`optimisation/milp_solver.py:1296-1307` returns
NOT_BANKABLE or at best REVIEW_REQUIRED). The guard is vacuous by three independent routes (the
not-bankable note, the zero-confirmed-assumptions warning at `financial_model.py:171-175`, and the
always-appended HH-basis note at `pipeline.py:115-127`). **Fix:** test for a *specific* T0
acknowledgement — a sentinel or typed warning the caller must add deliberately — not for a non-empty
list, and refuse `UNDERWRITTEN` on `DataTier.T0` outright. *Panel correction: HIGH → MEDIUM — the
production path already refuses every T0 site upstream at `pipeline.py:114` → `admit_half_hourly`;
exposure is limited to direct API callers.*

**N20 — A quote with no financial case at all can be advanced to `underwritten`. MEDIUM · Tier A · `src/gridq/quotes/lifecycle.py:74`.**
`transition_quote` gates only on the `_FORWARD` chain — no check that `financial_case is not None`,
that `selected_power_kw` is set, that the covenant passed, or that a real engine ran; and
`quote_mode` is a free parameter of `assemble_quote_record` with no validation against
`result.implementation_level` despite the docstring at `assembly.py:143-144`. The
`InsufficientInputsError` path produces `financial_case=None` and still assembles a record
(`pipeline.py:195-201`). **Fix:** add preconditions to `transition_quote` for UNDERWRITTEN and beyond
— `financial_case is not None`, `selected_power_kw > 0`, `implementation_level != BASELINE`, and a
recorded bankability verdict — raising `InvalidTransitionError` naming the missing precondition.
*Panel correction: HIGH → MEDIUM — no automated path sets `underwritten`; it is a missing governance
gate on a status label, not a mispriced asset.*

**N21 — The Gate-2 confidence ceiling is asserted in a warning and caps nothing. MEDIUM · Tier A · `src/gridq/quotes/pipeline.py:118`.**
`admission.confidence_ceiling` is interpolated into a prose string ("confidence is capped at MEDIUM")
and `admission` appears nowhere else in the file. `QuoteRecord` has no confidence or HH-basis field
(`schemas/quote.py:44-86`), and no branch in `lifecycle.py`, `assembly.py` or `financial_model.py`
consults the basis — a modelled-basis run and a metered run are treated identically by every gate,
carry identical precision, and permit identical `quote_mode` transitions. **Fix:** persist
`admission.basis` and `admission.confidence_ceiling` as structured fields on `QuoteRecord` and make the
lifecycle gate (N20) enforce them — a MODELLED basis cannot advance past `indicative` without an
explicit override recorded on the record.

**N22 — The CLI asserts the forecast is never priced into the floor; the engine prices it into the floor. MEDIUM · Tier A · `src/gridq/cli.py:94`.**
`--forecast` is documented as "gated upside; never priced into the floor" (repeated at `cli.py:317-318`
and `optimisation/engine.py:441-444`), and `cli.py:225` passes it to `MilpEngine(market_forecast=…)`.
When the site has consented to WHOLESALE_P415 and declares an export tariff, `_final_result` appends
`wholesale_contracted_stream_result(...)` to `value_streams` with `firmness=CONTRACTED`
(`engine.py:678-690`, `valuation.py:646`), which is inside `_PRICED_FIRMNESS`
(`cashflows.py:563`) — so it is summed into P50/P90, the DSCR and both IRRs. The forecast is a
necessary condition for the overlay that measures the export volume (`engine.py:437-439`), so
supplying it **can and does** raise the priced floor. Illustration: `export_tariff_gbp_per_kwh = 0.06`
× 400 MWh = £24,000/yr into the priced floor. **Fix:** correct the flag help text and
`_load_forecast_reference`'s docstring to state that a consented stream with an agreed export tariff is
priced into the floor as CONTRACTED (the D43 exception at `engine/conformance.py:349-361` already
encodes it), and warn on the quote whenever the forecast changed a priced-floor figure.

**N23 — Finance is handed the no-export dispatch volumes while the result carries overlay-export revenue. MEDIUM · Tier A · `src/gridq/optimisation/engine.py:285`.**
`solve` sets `self._dispatch_energy` from `_dispatch_energy_for`, which re-dispatches the chosen spec
on the plain no-export problem (`engine.py:362-377`, `scenario=None`, no forecast). It then runs
`_export_upside` — a separate `solve_with_export` — whose `export_mwh` is priced into `value_streams`
as CONTRACTED. `generate_quote` reads `selected_dispatch_energy()` (`pipeline.py:149-152`) and passes
those **floor** volumes to `run_financial_model`, where `levies_on_charge` (`cashflows.py:430-448`) is
computed from them. Exporting requires importing and storing energy the floor solve never charged, so
the extra net import and its levies never reach the cost side while the revenue they produced enters
P50, P90 and the DSCR; `_delivered_energy_kwh` for the price-to-target
(`financial_model.py:262-266`) is understated the same way. Illustration: floor dispatch 500 MWh
charge / 450 MWh discharge → levies on 50 MWh, while the overlay exports 400 MWh requiring ~940 MWh
charge, and £24,000 of contracted export revenue is priced. **Fix:** when the export overlay
contributes priced-floor revenue, derive `DispatchEnergy` from the overlay schedule that
`solve_with_export` already returns; otherwise hold the contracted export out of `value_streams`.
*Trigger conditions, so this is not mis-scoped: a wholesale forecast supplied AND
`export_dispatch_enabled(site)` AND WHOLESALE_P415 consented with a declared export tariff.*

**N30 — The not-bankable disclaimer asserts a status value the record contradicts. LOW · Tier A · `src/gridq/quotes/assembly.py:46`.**
`_NOT_BANKABLE_NOTE` hard-codes the literal `bankable_status=not_bankable` and is appended for every
status other than `BANKABLE_CANDIDATE`, including `REVIEW_REQUIRED`, which
`MilpDispatchSolver._bankable_status` returns when every used assumption is confirmed. **Fix:**
interpolate `f"…bankable_status={result.bankable_status.value}…"`, or drop the field-name claim from
the sentence. *Panel correction: MEDIUM → LOW — latent, because the only shipped release has zero
`status: confirmed` assumptions (30 `placeholder`), so today's runs are always NOT_BANKABLE and the
string is accurate. It becomes live the moment any assumption is confirmed.*

**N31 — `price_multiplier` scales NET operating cashflow, so it shrinks opex along with revenue. LOW · Tier A · `src/gridq/scenarios/engine.py:130`.**
`out = [cf * (cost_multiplier if i == 0 else price_multiplier) for i, cf in enumerate(cashflows)]`
applies the price multiplier to free cashflow before debt — revenue minus dispatch cost minus operating
cost — so fixed opex, insurance, business rates and DNO lines are scaled by it too. Year-1 revenue
£100k, costs £60k, `price_multiplier=0.8`: correct is £80k − £60k = **£20k**, the engine computes
0.8 × £40k = **£32k**, a 60% overstatement of the Low case, repeated across the horizon. **Fix:** widen
the `CashflowProvider` protocol (`engine.py:53-58`) to expose the gross revenue series — `CashflowModel`
already has `annual_revenue_by_stream_gbp` and `total_annual_operating_cost_gbp` — and apply the
multiplier to revenue before netting; as an interim, emit a per-scenario warning naming the
approximation and its direction so the number is never presented as a priced downside.
*Panel correction: MEDIUM → LOW — `ScenarioEngine` / `run_scenario_set` are exported but called from
nowhere in the pipeline; the quote's actual rows go through `compute_scenario_sensitivities` →
`run_financial_model`, which applies the multiplier to revenue only (`cashflows.py:835-837`). Tier A
only because the fix (widen the seam, or refuse the operation) is determinable.*

---

## 4. Literature gaps — MUST requirements the pipeline does not satisfy

Audited repo-blind against a GB battery-storage underwriting literature checklist, then matched to
source. 23 unmet requirements: 22 Tier A, 1 Tier B.

| ID | Status | What is missing | Sev | Fix |
|---|---|---|---|---|
| **F-03** | PARTIAL | The MILP objective prices charging at energy-band + DUoS only (`src/gridq/optimisation/milp_solver.py:531`, `price = energy_price + duos`). The per-kWh levies (CCL, RO, FiT, BSUoS, AAHEDC) are applied afterwards in finance (`cashflows.py:430-448`), so the optimiser sees charge energy 2–5p/kWh cheaper than delivered cost and books marginal cycles whose margin is smaller than the levy leakage they incur. | HIGH | Add the flat levy rate to the objective price in `_solve_milp`: `price = energy_price + duos + levy_rate`. `levy_rate` is already computed in `solve()` at line 302 — move it before the solve and pass it in. Levy leakage then prices itself through the existing `Σ price·(discharge−charge)` term; delete the now-duplicative `levies_on_charge` finance line or reconcile it to zero. |
| **F-05** | PARTIAL | A generic national 16:00–19:00 weekday red band (`src/gridq/optimisation/tariff_calendar.py:52-56`) stands in for 14 licence areas' published CDCM bands (NGED 17:00–19:30, NPg 16:00–19:30, seasonal variants) and ignores bank holidays. DUoS red-band avoidance is one of the two priced-floor streams and its £ is set entirely by *when* the band falls. | HIGH | Key the band windows by `connection.dno_region`: load each DNO's published CDCM time bands into the release as `duos.band_windows_by_dno`, including England & Wales bank-holiday treatment (→ green), and fall back **loudly**, never silently, to the generic default. |
| **D-92** | ABSENT | No unit-size eligibility gate on BM / Balancing Reserve / DC-DM-DR. Consent alone opens those gates to CONTRACTED (`src/gridq/value_stack/gb_streams.py:986-987`, `:1061-1062`); no size predicate exists in `value_stack/gates.py`. A 300 kW site can carry contracted BM revenue into the P90 series the debt is sized on, for markets it cannot physically enter. | HIGH | Add an eligibility predicate to `gates.py` (`power_kw ≥ 1000`, or `portfolio_mw ≥ 1` within one GSP Group with an aggregator cut deducted) and apply it in the BM/ancillary `check_gates` **before** the consent shortcut; gate CM on the 1 MW Connection-Capacity threshold (D-28). Consent authorises pursuit; it cannot manufacture eligibility. |
| **H-03** | WRONG | Debt quantum is asserted as gearing × capex (`src/gridq/finance/debt.py:300`); the P90 sculpt only shapes the repayment profile of that principal. On a weak-CFADS site the model reports a covenant breach on a loan no lender would size, instead of the smaller DSCR-sized debt. | HIGH | In `size_and_measure_dscr`, compute the DSCR-capacity principal = PV at the debt rate of (P90 CFADS_y ÷ target DSCR) over the tenor, then `principal = min(that, capex × gearing_pct)`. Every input (target DSCR, gearing, rate, tenor) already exists in the library. |
| **H-08** | WRONG | Everything labelled P90 is a flat 15% unsourced haircut on one deterministic run (`src/gridq/finance/cashflows.py:856-895`; `assumptions/gb_v1_6.yaml:260-268`, "No citable source identified", placeholder). It is not a percentile of anything and applies the same relative downside to a merchant-heavy and a contracted-heavy stack. | HIGH | Construct the distribution with machinery already present: re-run the dispatch and financial model over multiple price/load realisations (`scenarios/engine.py` exists; `quotes/sensitivities.py` already re-runs finance), take the 90th-percentile annual CFADS, and state the basis on `CashflowModel`. Keep the flat haircut only as a labelled fallback below N realisations. |
| **B-14** | PARTIAL | The default path is annual perfect-foresight LP with cyclic SoE, corrected by a 20% haircut the release itself says has no citable source (`assumptions/gb_v1_6.yaml:269-277`). `MpcDispatchSolver` (rolling horizon) exists in-tree but is opt-in (`src/gridq/optimisation/engine.py:125`, `use_mpc: bool = False`). | HIGH | Calibrate from the pipeline's own two solvers: run MILP (PF) and MPC (rolling horizon) on the same site/spec, set `perfect_foresight_haircut_pct = 1 − MPC/PF` per site class and record the derivation in the release notes — or make the MPC run the reported P50 basis and keep PF as the disclosed upper bound. |
| **H-15** | ABSENT | No indexation of opex, business rates, insurance or CPI-linked contracted revenue; the model never states which basis it is on (`src/gridq/finance/cashflows.py:126`, `:889-902`). 2.5%/yr compounds to ~28% cost error by year 10. *Same gap as N13.* | HIGH | Add a `macro.cpi_pct_per_year` assumption (OBR/BoE-sourced), index the operating cost lines and CPI-linked contracted revenue in the year loop, state the basis (nominal) on `CashflowModel`, and require the discount and debt rates to be nominal to match. |
| **H-17** | ABSENT | Every revenue line is earned at 100% availability for the whole horizon; no availability term exists anywhere in `finance/` (`src/gridq/finance/cashflows.py:889-902`). GB BESS fleet availability is ~95–98%, and on contracted availability streams an outage also triggers the penalty/clawback regime. | HIGH | Add `availability.fleet_availability_pct` (sourced to published GB fleet data, distinct from degradation), multiply all dispatch-derived and availability-payment revenue by it in the year loop, and wire the same factor into the contracted-stream penalty exposure so unavailability hits contracted lines twice, as the service terms do. |
| **H-04** | PARTIAL | DSCR — the covenant gate on every quote — is measured on EBITDA, not CFADS (`src/gridq/finance/debt.py:287-292`; `financial_model.py` feeds `p90_ebitda_gbp`). Cash tax, working-capital movement, augmentation/replacement reserve and DSRA funding all sit between the two, always in the unfavourable direction. | MED | Build a named CFADS series in finance: EBITDA − cash tax (0 with a loud warning until N1's tax layer exists) − maintenance/augmentation reserve contribution − working-capital delta; pass **that** to `assess_debt` and expose the line-by-line build on `CashflowModel`. |
| **H-16** | ABSENT | No construction phase: `p50_fcf = [−capex, full-revenue year 1, …]` (`src/gridq/finance/cashflows.py:904`). No IDC, no contingency, no delay LDs, no commissioning ramp, no discount premium to practical completion. | MED | Insert a construction period (months, from the consenting/connection timeline the quote pipeline already tracks) between t=0 and operations: capitalise IDC at the debt rate on the drawn balance, apply a commissioning revenue ramp in the first operating year, and start the debt tenor at COD. |
| **D-33** | WRONG | CM revenue is shrunk every year by battery capacity fade and never CPI-indexed (`src/gridq/finance/cashflows.py:889-895` applies `decay` to `contracted_base`, which contains CM availability revenue). The payment is fixed on the awarded de-rated MW for the term, and T-4 escalates with CPI. Double error on the most bankable line. | MED | Split the year-loop revenue into dispatch-derived (apply fade decay) and fixed availability-payment streams: hold the awarded MW × clearing price flat for the term and apply CPI indexation for T-4 once H-15's CPI key exists. The `rev_firm`/`rev_contracted` split already exists to carry this. |
| **D-130** | PARTIAL | A local-flex award (typically 1–2 years, terminable) sits in `rev_contracted` and earns for the full 10–25 year horizon in both P50 and P90 (`src/gridq/finance/cashflows.py:893-895`), inflating multi-year DSCR and debt capacity. | MED | Carry contract tenor on the award (the `AvailabilityCommitment` payload already exists), end each contracted stream's revenue at its tenor in the year loop, treat re-contracting beyond tenor as merchant-class (FIRM-haircut) revenue at a decayed price (D-214), and force the local-flex P90 contribution to zero after expiry. |
| **D-158** | PARTIAL | DC/DM/DR enters the contracted floor as one lumped £/MW/yr with no availability factor F, no performance factor K, no EFA-block shape and no product split (`src/gridq/optimisation/valuation.py:325-357`). Combined with the missing 1 MW gate this is the floor's most optimistic line. | MED | Represent the six EAC products separately with EFA-block granularity (D-80), and net the agreed rate by an availability factor consistent with H-17 and a K-factor default below 1.0 sourced from published EAC performance data. Keep the £/MW/yr shortcut only for a toll whose contract reference states availability risk sits with the counterparty. |
| **D-25** | PARTIAL | One de-rating table keyed only on duration (`assumptions/gb_v1_6.yaml:9`; `src/gridq/optimisation/valuation.py:107-118`). T-4 and T-1 factors differ and change per delivery year, so every year after the table's vintage is mispriced and the T-4/T-1 decision (D-45) cannot be represented. *Related to N28.* | MED | Restructure to `capacity_market.de_rating[auction_type][delivery_year][duration_h]` from NESO's published Auction Guidelines, and have `_derated_deliverable_mw` take the auction and delivery year from the commitment payload. Keep the conservative floor-lookup convention. |
| **E-06** | ABSENT | No Demand Flexibility Service stream — `MarketStream` has no DFS member (`src/gridq/schemas/enums.py:20-32`) and DFS appears nowhere in `value_stack/`. For the 0.1–5 MW C&I sites this pipeline targets, DFS is the only NESO route that exists once the 1 MW gates are correctly enforced. | MED | Add `MarketStream.DFS` with a gated stream in `gb_streams.py`: eligibility = Registered Unit Capacity 0.1–100 MW (one decimal), meter points in exactly one of the 12 DFS Zones, directional products, P376 BL01 or SNBM baseline choice, priced as utilisation £/MWh × called MWh with the piecewise performance factor K — same conservative call-rate pattern as local flex. |
| **D-169** | PARTIAL | SoE is bounded `[0, energy]` with no usable-window or DoD constraint (`src/gridq/optimisation/milp_solver.py:614`). The optimiser sweeps the full 0–100% window, overstating deliverable energy per cycle by the BMS reserve (typically 5–10%) and producing schedules that breach the warranty DoD terms the same file's cycle cap is honouring. | MED | Add `battery.soc_min_fraction` / `soc_max_fraction` (sourced from the warranty terms already carried in `warranty_cycle_limits`), bound `soe` in `[soc_min×energy, soc_max×energy]`, and report usable vs nameplate energy separately on the schedule. |
| **B-17** | ABSENT | Unity power factor assumed globally (`src/gridq/optimisation/milp_solver.py:298`, "kVA≈kW at unity PF"); no reactive variable, constraint or charge anywhere. Real C&I sites run 0.90–0.95 PF, so kVA against MIC/ASC exceeds the kW the model uses — charging at full kW near the MIC can itself trigger exceeded-capacity charges the model says are impossible — and the excess-reactive charge is absent from both sides of the bill. | MED | Ingest the reactive/kVArh series where metered, compute per-half-hour kVA against `min(MIC, ASC)` as the true charging bound, add the DNO 0.95-PF excess-reactive charge to the customer bill, and add a Q variable with an 8-segment polygon P–Q constraint so the inverter can price reactive avoidance against real-power headroom. |
| **D-174** | ABSENT | No PV representation, and negative net import (the signature of solar export) is actively clipped to zero with only a warning (`src/gridq/optimisation/milp_solver.py:425-431`). For any site with rooftop PV the optimised profile is corrupted upward in exactly the midday hours a battery would charge from surplus. | MED | Carry the signed net-load series through `_load_series` (drop the clip, keep the sign-convention warning), let `discharge_ub` use `max(load, 0)` and charge headroom use the true signed net position, and add an explicit `pv_generation_kw` input channel so charge-from-PV-surplus is separated in the energy balance and valued at the export/retail differential (E-03). |
| **B-01** | PARTIAL | RTE is applied once, all losses on the charge side (`src/gridq/optimisation/milp_solver.py:668-675`: `charge·RTE·dt` in, `discharge·dt` out). Total £ is right for pure arbitrage but the SoE trajectory is wrong: energy needed to sustain a discharge commitment is understated by ~√RTE (2–7%), so held-SoE floors and pre-position levels certify deliverability the battery cannot meet at the AC side — an obligation-breach direction, not a conservative one. | MED | Split the AC round trip: `η_ch = η_dis = √RTE` (or measured directional values when the release carries them), `soe_t = soe_{t−1} + charge·η_ch·dt − (discharge+export)·dt/η_dis`, and rescale the held-energy and pre-position compile so committed deliverable energy is net of discharge losses. |
| **B-09** | PARTIAL | Only the termination string is checked (`src/gridq/optimisation/milp_solver.py:849-865`); `mip_rel_gap` is never set and the achieved gap is never recorded, so two HiGHS versions can return different "optimal" incumbents within the default 1e-4 and the reported £ carries no statement of distance from proven optimal. | MED | Set `mip_rel_gap` explicitly in `solve_kwargs` (e.g. 1e-6, matching the £ rounding), read the achieved gap from the HiGHS solution object after every binary solve, store it on the schedule/`DispatchResult`, and degrade to INSUFFICIENT_DATA — never a priced number — when the gap exceeds the stated tolerance. |
| **B-10** | ABSENT | The sensitivity pack varies price multiplier, cost multiplier and COD delay only (`src/gridq/quotes/sensitivities.py:36-40`) — no RTE axis, though arbitrage value is super-linear in RTE and `ac_round_trip` is a library value, not a measured one. | MED | Add an RTE axis to `compute_scenario_sensitivities`: re-solve the **dispatch** (not just the financial model — the volumes change) at `ac_round_trip ± 3` points and report the DSCR/IRR deltas alongside Low/Central/High. |
| **D-116** | PARTIAL | Local flex prices utilisation £/MWh × called MWh only (`src/gridq/optimisation/valuation.py:264-322`): no availability payment (understatement — SAOU contracts earn most of their £ there) and no Performance Multiplier or POD cap (overstatement per called MWh). The two errors do not cancel. | LOW | Add the ENA Open Networks formulas: availability £/MW/h × contracted window hours × Monthly Utilisation Performance Factor (clawback on missed utilisations), and utilisation £/MWh with Grace Factor, Performance Multiplier and Payable-Over-Delivery cap, with the product type (SU/SAOU/VAOU/OU) carried on the tender award commitment. |
| **H-05** | PARTIAL *(Tier B)* | One covenant value, one annual min-DSCR test, no lock-up or event-of-default tiers, no semi-annual calculation periods (`src/gridq/finance/debt.py:346-355`). A credit committee needs first lock-up date, first default date and distribution-blocked years; annual testing also smooths over GB BESS winter-weighted seasonality, so the binding semi-annual DSCR is below the annual one. | MED | **Tier B — a human must supply the thresholds and the calculation-period convention (lender commercial policy).** Then: add distinct `dscr_covenants.lockup` and `.event_of_default` thresholds and a semi-annual calculation-period option, and report first-breach and first-lock-up periods. |

---

## 5. Killed findings

Seven candidate findings were refuted and must not be re-raised. One line each, with the reason.

1. **"Debt is asserted as a gearing percentage of capex, never sized from cashflow — the sculpt targets whatever DSCR the assertion produces"** (`finance/debt.py:300`) — **killed as a code defect**: the sculpt's ratio is then *measured* and *tested*, and a sub-covenant sculpt is honestly reported as sub-covenant (`debt.py:325`, `:346-355`; `objective.py:78-84`; `financial_model.py:165-170`); no number or verdict is flattered. The underlying *requirement* survives as literature gap **H-03** (Tier A, HIGH) — file it there, not as a wrong-number finding.
2. **"`price_multiplier` scales revenue in finance and net cashflow in the scenario engine — the same scenario yields two different Low cases"** (`scenarios/engine.py:130`) — killed: the two disagreeing artefacts are not producible (nothing calls `ScenarioEngine.run` / `run_scenario_set`; `QuoteRecord` has no scenario-run field), and the `CashflowProvider` Protocol exposes only netted series, so the split is impossible at that seam and is disclosed there. The arithmetic complaint survives separately and narrowly as **N31**.
3. **"The revenue-ranked fallback is monotone in size, so the recommendation pins to the search-space corner"** (`optimisation/engine.py:601`) — killed: the monotonicity proof is false (`aux_kw` scales with rated power and is subtracted from charge headroom at `milp_solver.py:539-552`; `discharge_ub = min(power, load)` caps discharge at site load), the covenant path *is* live on the shipped library, `use_two_stage_search` defaults True, and the tie-break prefers the **smaller** system. What survives is the narrower, differently-stated **N4/N5**.
4. **"End-of-tenor deliverability looks up the CM de-rating factor at day-one duration"** (`sizing/deliverability.py:110`) — killed: the shipped table is exactly proportional to duration (0.1099/hour), so `derate(D) × retention = derate(D × retention)` under linear evaluation; the claimed ~2× over-credit requires re-flooring the faded duration onto the 1 h node, which is a convention choice, and the code is conservative elsewhere (it also multiplies **power** by retention).
5. **"`underwrite_spec` cannot reproduce a sizing evaluation that used `extra_constraints`"** (`sizing/underwrite.py:68`) — killed: no shipped path supplies `extra_constraints` (`two_stage_sizing` does not accept the parameter), typed C026 `AvailabilityCommitment` payloads can only arrive that way, and nothing in the package calls `underwrite_spec` at all. A latent library-API gap, not a wrong number.
6. **"The finance bankability verdict is computed, carried, then dropped at the persistence boundary"** (`quotes/pipeline.py:194`) — killed: the covenant verdict **is** persisted (`CovenantStatus` on `FinancialCase` → `assembly.py:168` → `quote.financial_case.covenant_status.passes`), and the prose verdict reaches `QuoteRecord.warnings` via `financial_model.py:171-175` → `assembly.py:104-105` → `cli.py:391-394`. What remains is an ergonomics gap — the boolean is not a first-class field — which changes no number.
7. **"Scenario engine discards every P90 warning, so a degenerate P90 series reports £0 silently"** (`scenarios/engine.py:167`) — killed: `FinancialCase`'s validator forbids both degenerate shapes the claim needs (`schemas/finance.py:112-119`), the year-0 sign warning is identical for both series by construction, and nothing calls the scenario engine anyway.

---

## 6. Updated Tier A / Tier B register

Covers this run only: the 30 merged findings plus the 23 literature requirements. Most severe first.

### Tier A — the end state is determinable from the code, the arithmetic or GB market mechanics

| ID | Sev | Location | Defect | Fix |
|---|---|---|---|---|
| N2 | HIGH | `finance/cashflows.py:897` | CM penalty deducted on top of 85% of the same CM revenue; a CM contract lowers P90 EBITDA by 0.15R | Drive revenue loss and penalty from one shortfall number; bound the combined deduction at the contracted revenue |
| N3 | HIGH | `finance/pricing.py:58` | Price-to-target recovers 100% of capex+opex while the project also keeps the booked value stack | Net `total_annual_revenue_gbp`, add `annual_dispatch_cost_gbp`, discount at a blended rate |
| N4 | HIGH | `optimisation/engine.py:599` | Default path ranks candidates by gross priced-floor revenue, a metric with no capex term | Rank the fallback pool by `objective.rank_key` over `candidate_viable` candidates; state the metric used |
| N5 | HIGH | `optimisation/engine.py:611` | Selection metric ignores `annual_cost_gbp`, overstating each candidate by its dispatch cost | `sum(revenue − cost)` in both `_floor_value` and `priced_floor_value_gbp`; re-pin the parity test |
| N6 | HIGH | `optimisation/two_stage.py:294` | FINAL "no viable configuration" can rest on 10 medoid-proxy-ranked points | Confirm a size-diverse cover on the full series before any FINAL no-viable verdict; keep extreme days per stratum |
| N7 | HIGH | `optimisation/two_stage.py:219` | "Connection reinforcement" declared when the floor exceeds 1.5× peak or 8 h — search constants, not limits | Test the floor against `connection.mic_kw`/`asc_kw`; extend the box when the floor is within the physical limit |
| N8 | HIGH | `quotes/sensitivities.py:45` | Low/Central/High DSCR rows are bit-identical; the "equals Central" warning is dropped | Propagate `scenario.warnings` into the row note, or refuse to emit an all-identity table |
| N9 | HIGH | `ingestion/assembly.py:27` | HH fold wipes `half_hourly_basis`, promoting a modelled series to METERED / `high` ceiling | Merge onto existing `load_data` instead of replacing; have `hh_synthesis` stamp the modelled basis |
| F-03 | HIGH | `optimisation/milp_solver.py:531` | Charging priced without the per-kWh levy stack, so marginal cycles lose money the optimiser cannot see | `price = energy_price + duos + levy_rate`; reconcile or delete the `levies_on_charge` finance line |
| F-05 | HIGH | `optimisation/tariff_calendar.py:52` | One national red band stands in for 14 DNOs' published CDCM bands; no bank-holiday treatment | `duos.band_windows_by_dno` keyed on `connection.dno_region`, holidays → green, loud fallback |
| D-92 | HIGH | `value_stack/gb_streams.py:986` | Consent alone opens BM/ancillary gates with no 1 MW / aggregation eligibility check | Size predicate in `gates.py`, applied **before** the consent shortcut; CM gated at 1 MW |
| H-03 | HIGH | `finance/debt.py:300` | Debt quantum asserted as gearing × capex, never sized from cashflow | `principal = min(PV(P90 CFADS ÷ target DSCR), capex × gearing_pct)` |
| H-08 | HIGH | `finance/cashflows.py:856` | "P90" is a flat unsourced 15% haircut on one deterministic run, not a percentile | Take the 90th-percentile annual CFADS over multiple realisations; state the basis; haircut becomes a labelled fallback |
| B-14 | HIGH | `optimisation/engine.py:125` | Perfect-foresight LP is the default, corrected by an uncited 20% haircut; MPC exists but is opt-in | Calibrate the haircut as `1 − MPC/PF` from the repo's own two solvers, or report the MPC run as P50 |
| H-15 | HIGH | `finance/cashflows.py:889` | No indexation of any line; nominal rates applied to flat-nominal cashflows (= N13) | `macro.cpi_pct_per_year`, index opex and CPI-linked revenue, declare the basis on `CashflowModel` |
| H-17 | HIGH | `finance/cashflows.py:889` | 100% availability assumed for the whole horizon on every stream | `availability.fleet_availability_pct` applied to revenue and to the contracted-stream penalty exposure |
| N10 | MED | `finance/debt.py:319` | Min DSCR measured over a truncated window when horizon < tenor, dropping the binding years | Return `available=False` when `len(cfads) < tenor_years`; never `available=True` on a partial window |
| N11 | MED | `finance/returns.py:211` | Equity IRR drops post-horizon debt service and bypasses the sign-change gate | Extend to `max(len(operating), len(service))` periods, or refuse the metric |
| N12 | MED | `finance/pricing.py:174` | `hits_target_return` set from an assumption's status; price levelised as if volume were flat | Levelise over the faded volume; set the flag from the measured equity IRR against target, else None |
| N15 | MED | `optimisation/engine.py:263` | No-viable verdict quotes the whole proxy cloud as "dispatch solved and priced" | Report `len(solved_and_priced)`; state the proxy sweep count separately and labelled |
| N16 | MED | `sizing/sizer.py:161` | Persisted grid says "no candidate selected" on runs whose result says SELECTED | Record the indicatively-chosen candidate onto the `SizingRun` with a reason naming the fallback metric |
| N17 | MED | `quotes/sensitivities.py:47` | Scenario warnings and `bankable_status` computed then never read | Prepend `scenario.warnings` to the row note and return `validation_warnings` to `generate_quote` |
| N18 | MED | `quotes/sensitivities.py:81` | `cod_delay_months` reported on the row, never applied to any number, absent from the hash | Move `_shift_for_cod` into `build_cashflow_model`, add the field to `_hash_input`, null the row until then |
| N19 | MED | `schemas/quote.py:93` | T0-underwritten guard satisfied by any non-empty warnings list, so it never fires | Require a specific T0 acknowledgement sentinel; refuse UNDERWRITTEN on `DataTier.T0` |
| N20 | MED | `quotes/lifecycle.py:74` | A quote with `financial_case=None` can be advanced to `underwritten` | Add preconditions (case present, spec sized, non-baseline engine, recorded verdict) raising `InvalidTransitionError` |
| N21 | MED | `quotes/pipeline.py:118` | The Gate-2 confidence ceiling is prose and constrains nothing | Persist `basis` and `confidence_ceiling` on `QuoteRecord`; enforce them in the lifecycle gate |
| N22 | MED | `cli.py:94` | `--forecast` help says "never priced into the floor"; the engine prices it into the floor | Correct the help text and docstring; warn when the forecast changed a priced-floor figure |
| N23 | MED | `optimisation/engine.py:285` | Finance gets no-export dispatch volumes while the result carries overlay export revenue | Derive `DispatchEnergy` from the overlay schedule when overlay revenue is priced; else drop the stream |
| H-04 | MED | `finance/debt.py:287` | DSCR measured on EBITDA, not a built CFADS series | Build CFADS = EBITDA − cash tax − reserve contribution − WC delta; pass that to `assess_debt` |
| H-16 | MED | `finance/cashflows.py:904` | No construction phase: cheque to full revenue in zero time | Insert construction months with IDC on drawn balance, commissioning ramp, debt tenor starting at COD |
| D-33 | MED | `finance/cashflows.py:889` | CM revenue faded annually and never CPI-indexed | Split fixed availability payments from dispatch revenue; hold awarded MW flat; index T-4 |
| D-130 | MED | `finance/cashflows.py:893` | 1–2 year terminable flex contracts earn for the full horizon in P50 and P90 | End contracted revenue at contract tenor; re-contracting is merchant-class; local-flex P90 → 0 after expiry |
| D-158 | MED | `optimisation/valuation.py:325` | DC/DM/DR as one flat £/MW/yr with no availability or K factor | Six EAC products at EFA-block granularity, netted by availability and a sourced K < 1.0 |
| D-25 | MED | `assumptions/gb_v1_6.yaml:9` | One de-rating table keyed on duration only; no auction type or delivery year | `de_rating[auction_type][delivery_year][duration_h]`, context from the commitment payload |
| E-06 | MED | `schemas/enums.py:20` | No DFS stream — the only NESO route for a sub-1 MW BTM asset | Add `MarketStream.DFS` with zone, baseline, directional product and K-factor settlement |
| D-169 | MED | `optimisation/milp_solver.py:614` | SoE swept 0–100%; no usable window or DoD limit | `soc_min_fraction`/`soc_max_fraction` from the warranty terms; report usable vs nameplate |
| B-17 | MED | `optimisation/milp_solver.py:298` | Unity power factor assumed; no reactive variable, constraint or charge | kVA against `min(MIC,ASC)` as the charging bound; excess-reactive charge; polygon P–Q constraint |
| D-174 | MED | `optimisation/milp_solver.py:425` | No PV; negative net import clipped to zero | Carry the signed net-load series; add a `pv_generation_kw` channel; separate charge-from-surplus |
| B-01 | MED | `optimisation/milp_solver.py:668` | All RTE losses on the charge side, so held-SoE floors certify undeliverable energy | `η_ch = η_dis = √RTE`; discharge divides; rescale held-energy and pre-position compile |
| B-09 | MED | `optimisation/milp_solver.py:849` | No `mip_rel_gap` set, no achieved gap recorded on binary solves | Set the gap, record it on `DispatchResult`, degrade to INSUFFICIENT_DATA above tolerance |
| B-10 | MED | `quotes/sensitivities.py:36` | No RTE axis in the sensitivity pack | Re-solve the dispatch at `ac_round_trip ± 3` points; report DSCR/IRR deltas |
| N25 | LOW | `finance/financial_model.py:321` | Provenance hash omits debt terms and price structure, so different cases share a `case_id` | Add resolved `debt` and `price_structure` to `_hash_input` |
| N26 | LOW | `finance/cashflows.py:471` | Parasitic energy priced without levies; ~£1,752/yr on a 1 MW site (7.7% of EBITDA) | One shared blended-rate helper including levies; correct the false parity claim |
| N27 | LOW | `sizing/evaluator.py:171` | Unpriceable and zero-value candidates tie in the continuous search's rank key | Lead `ScoredPoint`'s tier with `priceable`/viability; gate `best` on `candidate_viable` |
| N28 | LOW | `optimisation/_assumptions.py:63` | CM de-rating silently floored at the 4 h key across the whole 4–8 h range | Warn above the largest tabulated key and surface it (adding 6/8 h keys is Tier B, see D-25) |
| N29 | LOW | `optimisation/two_stage.py:425` | `search_space_size` counts confirmed candidates twice; the surface carries duplicate specs | De-duplicate as the non-proxy branch does; report distinct specs; split proxy/confirmed counts |
| N30 | LOW | `quotes/assembly.py:46` | Disclaimer hard-codes `bankable_status=not_bankable` even on REVIEW_REQUIRED | Interpolate `result.bankable_status.value` |
| N31 | LOW | `scenarios/engine.py:130` | `price_multiplier` scales net cashflow, shrinking opex with revenue (£32k where £20k is correct) | Widen `CashflowProvider` to expose gross revenue and multiply before netting; warn in the interim |
| D-116 | LOW | `optimisation/valuation.py:264` | Local flex priced utilisation-only, with no MPF, Performance Multiplier or POD cap | Add the ENA availability and utilisation formulas with the product type on the award |

### Tier B — the end state needs a human decision or a sourced value before implementation

| ID | Sev | Location | Defect | Fix |
|---|---|---|---|---|
| N1 | HIGH | `finance/cashflows.py:896` | No corporation tax anywhere; pre-tax EBITDA used as CFADS, project IRR and equity IRR | Human supplies the tax basis (SPV structure, capital-allowance treatment, WDA timing, loss carry-forward); then add a `tax` section and a post-tax CFADS line. **Now:** stamp PRE-TAX on `DscrSummary` and `FinancialCase` |
| N13 | MED | `finance/cashflows.py:890` | No inflation index anywhere; nominal rates on flat-nominal cashflows; basis undeclared | Human supplies the index, the rate and which lines are indexed; then go nominal (escalate) or real (deflate the rates). **Now:** declare the basis and warn that no escalation is modelled |
| N14 | MED | `finance/cashflows.py:904` | No terminal value, no decommissioning, no inverter/BOP replacement, and no disclosure of any of them | Human decides the residual-value basis and the decommissioning obligation in the host-site agreements; then add the terminal flow and the funded reserve. **Now:** warn on every `FinancialCase` naming both omissions |
| H-05 | MED | `finance/debt.py:346` | One annual covenant test; no lock-up / event-of-default tiers, no semi-annual periods | Lender supplies the thresholds and the calculation-period convention; then add the tiers, the semi-annual option and first-breach / first-lock-up reporting |

---

## 7. Recommended order of work

Dependency-aware. Each item states what must be true before it starts.

**1. Truth-in-labelling batch — N8, N9, N15, N16, N17, N18, N19, N20, N21, N22, N30, plus the Tier A warning subsets of N1, N13 and N14.**
*Precondition: none.* This batch changes **no computed number**, so it lands without rebaselining a
single golden fixture, and it can run in parallel with everything below. It is first because it is
unblocked and because it stops the pack asserting things it never computed: a three-identical-row
sensitivity table presented as a sensitivity analysis, a modelled load shape reported as METERED, a
CLI flag whose documented contract is the opposite of what the engine does, and a finance case that
never says it is pre-tax, un-escalated and terminal-value-free. Deliverable: every claim in
`quote.json` and in the CLI output is either computed or labelled as not computed.

**2. Priced-floor arithmetic — F-03, N26, N23, N2, N5.**
*Precondition: item 1 merged (so the new warnings are in place while numbers move), and agreement to
rebaseline the golden fixtures once.* These five must land as one batch because each moves the same
downstream numbers and rebaselining five times is how a wrong baseline gets frozen in. Order within
the batch: F-03 (levies into the dispatch objective — this changes the schedule, so it must be first),
N23 (hand finance the dispatch that produced the revenue), N26 (levies on the parasitic line), N5
(net the cost in the value metric), N2 (single-shortfall CM penalty). Deliverable: one delivered
import price used identically by the optimiser, the levy line and the counterfactual; P90 that cannot
be made worse by winning a contract; a value metric that agrees with EBITDA on sign.

**3. Selection metric — N4, N27, N29, and re-point N16's grid at the chosen candidate.**
*Precondition: item 2 merged.* Ranking by `objective.rank_key` is only an improvement if the project
IRR it ranks on is computed from corrected cashflows and the floor metric is net of cost; doing this
first would replace one wrong ranking with another. Deliverable: the recommended kW/kWh is the
argmax of the metric the run reports, on both the engine path and the exported continuous-search API,
and `search_space_size` counts distinct specifications.

**4. Final-verdict gates — N6, N7.**
*Precondition: item 3 merged.* Both fixes change what `NO_VIABLE_CONFIGURATION` means, so the
selection semantics must be settled first. N6: confirm a size-diverse cover on the full series before
any FINAL no-viable verdict. N7: bound the provisioning test by `connection.mic_kw`/`asc_kw`, not by
`COARSE_POWER_FRACTIONS × COARSE_DURATION_HOURS`. Deliverable: no project-killing or capital-works
verdict is reachable from a proxy ranking or a tuple of search constants.

**5. Price-to-target netting — N3, and the levelisation half of N12.**
*Precondition: item 2 merged (the revenue being netted must be right).* Net
`total_annual_revenue_gbp`, add `annual_dispatch_cost_gbp`, and levelise the requirement over the
degraded volume rather than as a flat annuity. The discount-basis half of N3 and the
measured-verification half of N12 wait for item 7. Deliverable: the £/kWh recommendation and the
`exceeds_counterfactual` verdict stop double-charging the customer for revenue the project keeps.

**6. Latent finance correctness — N10, N11, N25, N28, B-09.**
*Precondition: none; can run in parallel from the start.* Cheap, independently testable, and each
closes a trap that fires the moment a caller uses a documented public parameter (`horizon_years=`), a
new tenor is added, an assumption is confirmed, or a HiGHS version changes.

**7. Post-tax CFADS and declared bases — N1, H-04, N13/H-15, N14, H-16, H-03, H-05, plus the deferred
halves of N3 and N12.**
*Precondition: a human has supplied the Tier B inputs — the tax basis and capital-allowance treatment,
the inflation index and which lines are indexed, the residual-value basis and decommissioning
obligation, the DSCR-sizing/gearing policy, and the lender's lock-up and default thresholds. Start the
sourcing conversation on day one; the implementation is blocked until it returns.* Once the inputs
exist the code work is mechanical and should land in one release: a named line-by-line CFADS series
(H-04) that already has slots for tax (N1), reserve contributions (N14) and working capital; DSCR-sized
debt as `min(DSCR capacity, gearing cap)` (H-03); a construction period with IDC (H-16); and an
explicitly declared nominal or real basis (N13/H-15). Deliverable: the DSCR the covenant is tested on
is a post-tax CFADS ratio on a stated basis, and the debt quantum is one a lender would advance.

**8. Eligibility and market mechanics — D-92, E-06, D-25, D-33, D-158, D-116, D-130.**
*Precondition: item 2 merged (so revenue lines are being netted correctly before they are re-specified);
D-33's CPI indexation additionally requires item 7's CPI key.* This is where the revenue stack stops
booking money the asset cannot earn: the 1 MW gates, the DFS stream that is the only NESO route for
the sub-1 MW sites this pipeline targets, per-auction de-rating, CM as a fixed indexed payment rather
than a fading one, availability and K factors on frequency response, ENA formulas on local flex, and
contract tenors that end when the contracts end.

**9. Dispatch physics — B-01, D-169, B-17, D-174.**
*Precondition: item 2 merged (F-03 already changes the objective; these change the feasible set, and
doing both at once makes regressions unattributable).* Split efficiencies, a usable SoC window,
reactive power and kVA, and a signed net-load series with an explicit PV channel. Expect the
recommended specification to move; re-run item 3's ranking afterwards.

**10. Risk basis — H-08, B-14, H-17, B-10.**
*Precondition: items 2, 8 and 9 merged.* Constructing a genuine P90 distribution, calibrating the
foresight haircut against the repo's own MPC solver, applying fleet availability, and adding the RTE
sensitivity axis are only meaningful once the underlying P50 is built from correct prices, correct
eligibility and correct physics. Deliverable: the number labelled P90 is a percentile of a
constructed distribution with a stated basis, which is what the debt is sized on.
