# Assumption register — every pipeline assumption vs the literature

**Red-team audit, 2026-07-07/08 (Olly).** Format: assumption → platform value → evidence →
verdict → action. Verdicts: CONFIRMS (evidence supports the value), PARTIAL (in range but with
caveats), CONTRADICTS (evidence against — action taken), UNSOURCED (no authority found — stays
placeholder). Sources verified by web research 2026-07-07/08; every value in the library keeps
its `status` field (placeholder/needs_review) and the platform's own rule stands unchanged:
**nothing is bankable until an assumption is `confirmed`** — this register is the map from
placeholder to confirmable, not a mass confirmation.

## A. Assumption library (gb_v1_3, priced on every run)

| # | Assumption | Platform value | Evidence (sources) | Verdict | Action |
|---|---|---|---|---|---|
| A1 | CCL on electricity (site levy line) | 0.775 p/kWh (from the invoice) | HMRC CCL rates: 0.775 p/kWh FY2024-25 and FY2025-26; **legislated steps: 0.801 p/kWh from 1 Apr 2026, 0.827 from 1 Apr 2027** (gov.uk CCL rates + 2026 change notice) | CONFIRMS | Keep. Recorded refinement: multi-year cashflows currently hold the year-1 levy flat; step the CCL line by the legislated schedule (issue for M5; conservative today since levies raise the charge cost) |
| A2 | BESS installed capex | £150k/MW power + £175/kWh energy (2h ≈ £250/kWh) | Gridcog (Jan 2025): UK C&I BTM ≈ £350/kWh after a ~40% yearly fall; 2025-26 UK C&I quotes £200-450/kWh; Modo GB BESS outlook (capex/IRR) | PARTIAL | Keep as `placeholder` with the band note: defensible at the ~1 MW containerised end, optimistic at 50-250 kW (≈£350/kWh benchmark). The decomposed power+energy SHAPE (C025/gb_v1_3) is what the sizing needs; values await procurement quotes |
| A3 | Fixed O&M | £12,000 **per MW-yr** (× rated power, `cashflows.py:391`) | NREL ATB 2024 commercial BESS: fixed O&M ≈ 2.5% of capex/yr; Lazard LCOS v10 (Jun 2025) C&I; EIB appraisal ranges → defensible band £12-15/kW-yr | CONFIRMS (shape), value at band's bottom edge | Keep. **Audit self-correction recorded:** the audit initially misread this as a flat per-site £12k (which would have been the wrong shape); the code multiplies by MW — the register's job is exactly to catch both errors, including the auditor's |
| A4 | Parasitic/auxiliary load | 1% of rated power, continuous | PNNL-29378 field data: Li-ion means 0.4-1.1% of rated power; HVAC-dominated | CONFIRMS (top of range = conservative) | Keep; optional M7 refinement: temperature-dependent HVAC uplift |
| A5 | AC-AC round-trip efficiency | 0.88 | Spec range 85-92% BOL; NREL ATB baseline 0.85; PNNL field-measured 83-87% incl. aux | PARTIAL | Keep 0.88 as spec-level base; recorded action: underwrite sensitivity at 0.85 (scenario case), since field RTE runs below spec |
| A6 | Degradation | 2.5%/yr capacity fade | Energy-Storage.News (May 2026): 20-30%/decade for daily-cycled LFP; calendar ~2-3%/yr at 25C | CONFIRMS | Keep; M7 replaces the linear rate with a measured SoH curve (already the plan) |
| A7 | Warranty cycle caps | {1h: 548, 2h: 365, 4h: 250}/yr | Trina warranty ≈548/yr (1.5 c/d); "1 cycle/day" = 365 is the classic 2h term (DNV: no industry standard, vendor-specific, often throughput-based) | PARTIAL | 1h/2h match documented terms; 4h 250/yr unsourced — stays placeholder; D35's conservative-neighbour lookup already guards off-node durations |
| A8 | CM clearing price | £30/kW-yr (needs_review) | T-4 clears: £63 (DY26/27), £65 (DY27/28), £60 (DY28/29), **£27.10 (DY29/30, Mar 2026)**; T-1: £35.79 → £20 → **£5 (DY26/27, Mar 2026)** (Modo; adversarially re-verified vs Timera/Drax/gov.uk) | PARTIAL | Keep £30 as the T-4-shaped placeholder (close to the latest T-4 clear) with the register note: series falling fast on surplus; near-term T-1 entry earns far less. Recorded refinement: split by auction type + downside stress. Official series: NESO EMR DB auction results (referenced, not stored) |
| A9 | **CM de-rating by duration** | was {1h 0.40, 2h 0.65, 4h 0.95} | NESO scaled-EFC factors (2024 ECR methodology, applied Mar-2026 auctions): **1h 10.47%, 2h 20.94%, 4h 41.74%**, 8h 83.78% (Modo ×2, adversarially re-verified with fresh sources: overstatement ~2.3-4×) | **CONTRADICTS → FIXED (D41)** | **Replaced in `gb_v1_4`** (the release the app prices on): CM stream value corrected (demo CM £3,222 → £1,038); returns/DSCR/selection unchanged — independently confirming conditional CM was never in the underwritten floor. Golden re-pinned with the line-by-line delta |
| A10 | CM eligibility, <1 MW non-exporting BTM | modelled as conditional (gated) | Aggregated (proxy) DSR CMU route: min CMU 1 MW de-rated (2019/20 Rules; kept in the Dec-2024 five-year review), NO minimum component size; DSR generic de-rating ~0.79-0.86 + aggregator fees + proving tests apply | CONFIRMS (route exists; conditional is right) | Keep conditional. Note added to gb_v1_4 header: duration factors are the CONSERVATIVE bound vs the DSR-route treatment; model aggregator fees when a route is real; DESNZ consultation = regulatory risk |
| A11 | Day/night arbitrage cannot alone fund a battery (the screening logic behind the RTF verdict) | ~1.5p/kWh effective margin ⇒ no viable size | Gridcog (2025): C&I battery LCoS ~16p/kWh at £350/kWh capex vs typical C&I retail commodity spread ~1.5-4p/kWh — retail-only value ~⅓ to ½ of stacked value | CONFIRMS | The EXPECTED_RESULT derivation and the engine's verdict match published analysis. Keep |
| A12 | Perfect-foresight haircut | 20% (needs_review) | Modo Energy BESS benchmark methodology (May 2026): a flat **0.80 calibration factor validated against GB** virtual-vs-real asset data; Sioshansi et al. (2009): ~15%+ backcast loss | CONFIRMS (exactly) | Keep 20%; upgrade the yaml source note to cite Modo's GB-validated 0.80 factor at the next release cut (text-only change) |
| A13 | DUoS post-TCR posture (red 16:00-19:00 weekday; residual fixed; bundled contracts have no avoidable DUoS) | tariff_calendar defaults + the RTF exclusion | NGED East Midlands charging statement eff. 1 Apr 2025: red 16:00-19:00 Mon-Fri, HH unit rates 6.621/1.214/0.087 p/kWh (LV); TCR residual in fixed p/MPAN/day bands (Ofgem TCR decision); fixed bundled contracts embed non-commodity costs | CONFIRMS (window EXACTLY matches) | Keep; the RTF site's zero-DUoS-avoidance treatment is right. Refinement recorded: per-DNO windows/rates as versioned content when multi-DNO sites arrive |
| A14 | Excess-capacity (exceeded ASC) charges | observed on the invoice; NOT annualised (evidence-bounded) | Post-DCP161 exceedance exists in p/kVA/day, but 2025/26 NGED/SEPD schedules bill exceeded kVA at the STANDARD rate for the breach month (the old penalty premium is gone) | PARTIAL | Our conservative treatment stands (hard cap at ASC; observed charges flagged as data-ask, never annualised). The invoice's "£472.51/kVA" reading remains flagged for PDF confirmation |
| A15 | Availability charge 8.76 p/kVA/day (RTF invoice) | transcribed | **Exact match**: NGED East Midlands HV site-specific capacity rate, 2025/26 schedule (LV would be 7.72) | CONFIRMS | Keep. Note: implies the RTF site is HV-connected — recorded as a small corroborating datum for the unknown voltage_level field |
| A16 | Target equity IRR | 12% (needs_review) | GB BESS hurdle range 10-12% (Modo Q4-24: achieved ~10.7% for 2h assets, below hurdle) | CONFIRMS (top of range = conservative screen) | Keep, documented as a screening threshold, not a market-observed return |
| A17 | Debt pricing 6-9.5% by credit band | placeholder | **No public UK BESS source discloses margins** (deal pricing confidential; Modo 2025 financing report, Pexapark) | NO_SOURCE_FOUND | Stays placeholder, now labelled "unverified market estimate — override per term sheet". Plausible band (senior over SONIA → mezz) but unverifiable publicly |
| A18 | Debt tenor 7-12 yr | placeholder | Public deals: 10-yr toll-backed (Eku Ocker Hill), 5-7 yr tolls typical (ABN AMRO/BBDF 2026) | PARTIAL | Keep 7-12 with the note: >10 yr requires a matching long-dated toll/floor |
| A19 | Min P90 DSCR 1.3× | placeholder | Lenders ~1.3× typical for contracted revenue; merchant-heavy sized 1.5×+ (2025 surveys) | PARTIAL | Keep for the contracted basis; recorded refinement: differentiate covenant by revenue type (contracted ~1.2-1.35 vs merchant ~1.5-2.0) |
| A20 | Gearing 60% | placeholder | BESS deals 40-60% debt (Pexapark 2025); 60-70% implied by 2025 sponsor-equity ranges; high gearing needs ~80% tolled revenue | CONFIRMS (top of range) | Keep; recorded refinement: condition gearing on contracted-revenue share (merchant-heavy BTM nearer 40-50%) |
| A21 | Business rates £8,000/MW-yr | needs_review | England: renewables-charged storage P&M **excepted from rating to 1 Apr 2035** (SI 2022/405); grid-charged/arbitrage BTM likely rateable, no public £/MW benchmark | PARTIAL | Keep for the grid-charging BTM case (ours); recorded refinement: zero the line when the battery charges mainly from on-site renewables (statutory exemption) |

### Adversarial re-checks (fresh independent sources per finding)

- **O&M shape CONTRADICTS re-confirmed** — against the premise as posed; see A3: the code
  already scales per MW (the audit's own premise was the error; the evidence band £12-15/kW-yr
  stands and the platform value sits at its bottom edge).
- **CM de-rating CONTRADICTS re-confirmed** (1h 10-27%, 2h 21-28%, 4h 42-56% across recent
  years) — fixed in gb_v1_4 (A9/D41).
- **RTE 0.88** re-graded: optimistic end (field central 85-87% incl. aux) — keep as spec-level
  base ONLY with a vendor RTE guarantee; run 0.85 sensitivity (recorded action, A5).
- **Degradation 2.5%/yr** re-graded PARTIAL: GB fleet-measured ~4% per 365 cycles — mildly
  optimistic for daily cycling; fade is front-loaded/nonlinear (recorded for M7's SoH model).
- **Warranty caps**: 2h 365/yr conservative vs current terms (Huawei C&I ~500/yr); 4h 250/yr
  still unsourced (stays placeholder; D35 conservative-neighbour guards off-node durations).
- **CM clearing PARTIAL re-confirmed** against Timera/Drax/gov.uk/NESO announcements.

## B. Structural constants in code (not in the yaml — audited by reading + tests)

| # | Assumption | Where | Verdict | Evidence / action |
|---|---|---|---|---|
| B1 | DUoS windows: red 16-19 weekday, amber 07-16+19-23, green rest | `tariff_calendar.py` (release-overridable via `duos.band_windows_weekday`, W9) | CONFIRMS (as an indicative default) | Post-TCR banded ToU DUoS persists for HH C&I; actual windows vary by DNO/year — correctly versioned as content with a loud override note |
| B2 | Day/night boundary 23:00-07:00 | `tariff_calendar.py` | PARTIAL | Common E7-style boundary; real register times vary by meter. The RTF invoice's day/night volumes were honoured via the day-share constraint rather than the window guess — the honest treatment |
| B3 | Charge headroom hard-capped at min(MIC, ASC) | `milp_solver.py` | CONFIRMS (conservative) | Physically the DNO tolerates ASC breaches at a price (DCP161 excess charges); hard cap never worsens a breach. Follow-up recorded: soft-cost formulation for sites already above ASC |
| B4 | Tie-breaks: throughput 1e-7, peak 1e-6, age 1e-9 | `milp_solver.py` (D38) | CONFIRMS | £-identity verified vs epsilon-zero baseline (every stream £, returns, bills identical); peak-aware direction protects the C029 deliverable |
| B5 | Sizing bounds: 5-100% of observed peak, durations 0.5-8h | `sizing/domain.py` | CONFIRMS | Site-scaled, never an invented kW; C&I BTM durations beyond 8h are degenerate at current capex shapes |
| B6 | Synthetic profile shapes (archetypes) | `app/synthetic.py` | PARTIAL | Only used when NO observed shape facts exist; sites with billing facts now use the constraint-based generator (D39) — exact annual/split/peak, disclosed placement |
| B7 | Projection horizon default 15y | finance | CONFIRMS (structural, disclosed) | Warned on every run ("confirm the horizon"); typical BESS underwriting horizons 10-20y |
| B8 | 365.25 days/yr for standing/capacity lines | `milp_solver.py` (new) | CONFIRMS | Matches annualisation convention; disclosed in the bill basis |

## C. Verdict-level cross-checks (analytic, run against the engine)

- Arbitrage margin formula `(day+CCL) − (night+CCL)/RTE` = £0.01504/kWh on the RTF tariff;
  engine's TARIFF_ARBITRAGE for an 80 kW × 4h candidate lands under the £1,772/yr day-band
  ceiling (`test_e2e_examples_real.py::test_ac5`) — the engine's stream valuation and the
  hand-derivation agree.
- G9 closed-form week (£360.91 = 5 weekday red cycles + 2 weekend day cycles, losses at night
  rate) reproduced exactly by the solver after D38.
- Demo £-identity across D38 (all streams/returns/bills) and across the bill fixed-line change
  (savings identical, both totals +standing only) — verified before each golden re-pin.

Full source URLs for A8-A21 live in the research workflow record (wf_8ee1e775-7cf journal;
headline anchors: gov.uk CCL rates + 2026 change notice, NGED East Midlands charging statement
eff. 1 Apr 2025, Ofgem TCR decision, NESO EMR Delivery Body auction results/guidelines, Modo
Energy GB CM + BESS benchmark methodology (May 2026), Gridcog BTM economics (2025), NREL ATB
2024, Lazard LCOS v10 (Jun 2025), PNNL-29378 + ESGC 2022, DNV warranty guidance, SI 2022/405,
Pexapark BESS financing (2025), Sioshansi et al. Energy Economics 2009).
