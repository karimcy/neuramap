# SCOREBOARD — Grundon Portfolio + Harwell underwriting drive

**Owner:** Olly · **Started:** 2026-08-05 · **Target:** unlevered ≈7.7%, levered 15–25% (20% golden)
**Status:** Phases 0-3 COMPLETE. All five sites modelled on the merchant + PPA model, all deliverables
produced, all five registered as platform Projects. Awaiting four gating documents (see Next action).

## Per-site state

Base year **2025** (2026 is a part-year in the source workbook).

| # | Site | HH data? | 2025 load | Key asset | Status | Unlev | Lev | Rnds | Blocking issue |
|---|------|----------|-----------|-----------|--------|-------|-----|------|----------------|
| 1 | Grundon — Bishops Cleeve | ✗ monthly | 550,330 kWh | **LFG gensets 6.90 GWh, −19.9% YoY** | data done | — | — | 0 | Export price basis unconfirmed (0.49p line) |
| 2 | Grundon — Tanhouse & Colnbrook | ✗ monthly | **4,712,985 kWh** | EfW private wire + 139 MWh solar | data done | — | — | 0 | Private-wire contract terms unknown |
| 3 | Grundon — Knowl Hill | ✗ monthly | 535,052 kWh | LFG 489 MWh (91%), declining | data done | — | — | 0 | LFG decline curve needs client input |
| 4 | Grundon — Ewelme | ✗ monthly | 694,062 kWh | **750 kVA spare connection** | data done | — | — | 0 | Solar <1mo history |
| 5 | Harwell | ✓ **758 d, 0 gaps** | 908,590 kWh | flat 43% LF, POWEN PV options | data done | — | — | 0 | POWEN assumes 426 MWh vs metered 909 MWh |

Four-site Grundon load **6.49 GWh/yr** (72.5% of the estate) + BC export **6.70 GWh/yr** + Harwell
0.91 GWh/yr.

> ⚠️ **SUPERSEDED (2026-08-05):** an earlier revision of this scoreboard put the plausible fleet at
> 2–4 MW. That was an artefact of the *avoided-cost* model, which wrongly capped discharge at site
> consumption. On the correct merchant + PPA model, **sizing is capped by CONNECTION CAPACITY and
> throughput, not by consumption** — see Phase 3 below. Connection capacity per site: Bishops Cleeve
> 480 kVA import **plus an existing ≥1 MW export route**; Ewelme 750 kVA (8.7% utilised); Knowl Hill
> 325 kVA; Tanhouse & Colnbrook **none — 100% private wire**; Harwell private campus network.

## Phase 0 findings (2026-08-05)

### F1 — No half-hourly data exists for any Grundon site
`examples/real/Grundon/NEW Electricity Usage and Costs.xlsx` is **invoice/billing data at monthly
cadence**, 57 tabs (one per meter), 2018-01 → 2026-12. Verified median date gap = 31 days on every
target tab. Columns are `Date | kWh | Cost | kgCO2e | p/kWh | kWh % diff`. There are no HH workbooks
in the folder.

Consequence: ground rule §3.1 and DoD §8 ("dispatch ran on real HH data") **cannot be met for sites
1–4**. Battery value is overwhelmingly intra-day-shape-dependent (peak shaving, DUoS red band,
arbitrage spread capture); monthly totals constrain none of it.

### F2 — Bishops Cleeve is NOT off-grid (contradicts the client brief)
- `BC Import` — MPAN **1460001527004**, meter K10W570102, **230 kVA**, real kWh + £ + p/kWh.
  It is a genuine **electrical** import meter, not waste tonnage. 24,791 kWh in 2018.
- `BC Self-Supply (LFG Gen)` — has an **"Export to Grid"** column running 638–842 MWh/month
  through 2023–2026. The site has a substantial grid *export* connection.

So Bishops Cleeve has both import and export grid connections. Islanded-mode modelling is not
required; grid-connected LFG firming + export optimisation is the correct frame.

### F3 — Model_v5's 20.88% levered IRR is not a per-site result, and contains arithmetic errors
Model_v5 is a **100 MW portfolio build-out model on per-MW unit economics** — not a site model and
not a half-hourly dispatch. Its headline returns come from three assumptions, not from a simulation:
£65,690/MW/yr net Year-1 revenue, £452,000/MW capex, £17,000/MW/yr opex, 60% gearing @ 8.5% / 10 yr.

Two sheets in the same workbook disagree on the same assumptions:

| Sheet | Unlevered IRR | Levered IRR |
|---|---|---|
| `Asset Economics` (one representative MW) | 6.15% | **−4.97%** |
| `Portfolio Build` (100 MW aggregate) | 7.75% | **+20.88%** |

Cause — three confirmed spreadsheet defects:

1. **`Portfolio Build` adds interest expense as income.** `D34 = D32+D24+D25+D28+D29`, where
   `D28 = D27*CoD/4` is a **positive** number on positive debt outstanding. Interest is added to
   equity cash flow every period instead of subtracted. Verified: Q2 equity CF −163,600 =
   25,000 − 452,000 + 271,200 − 13,560 **+ 5,763**. This flatters the 20.88% in every period.
2. **`Asset Economics` double-counts the debt draw.** `D36 = D26-D27` = −452,000 − 271,200 =
   −£723,200 Year-0 equity outflow, against an actual equity cheque of £180,800. Should be `D26+D27`.
3. **`Asset Economics` never charges interest.** `D29 = -D27` stores debt outstanding **negative**,
   so `E29 = MAX(D29+D31,0)` collapses to 0 in Year 1 and `E30 = E29*CoD` = 0 for all 15 years —
   while principal still repays £27,120/yr.

Also: `Neura's Equity IRR` annualises quarterly cash flows with `^2` (`D48`) while the portfolio uses
`^4` (`D39`), over a shorter range (`AM` vs `BW`).

**Consequence: the premise "these numbers are known to be achievable — Sim has already produced
them" does not hold as stated.** 20.88% is a portfolio aggregate that is materially overstated by
defect (1). The benchmark must be repaired before it can be reconciled against — otherwise matching
it means reproducing an error.

### F4 — Harwell HH data is clean and usable
`Meter_Data_Interval_Meter_K16W304527.XLSX`, meter id `2000056536831`, wide format, 48 periods/day.
- **758 days, 2024-04-01 → 2026-05-01, zero missing slots (0.00%)**
- Full year 2025: **908,590 kWh**; 2024 (Apr–Dec): 625,260 kWh
- Peak half-hour 106.9 kWh → **213.8 kW**; mean 46.25 kWh/HH (92.5 kW average)
- Load factor ≈ 43% — a flat, high-baseload profile (campus/lab, not office)

**Scope mismatch to flag:** `POWEN_UK_Harwell.pdf` sizes rooftop PV on the **Genesis building** at
"annual energy consumption of around 426,000 kWh". The meter shows 908,590 kWh/yr — 2.1×. Either the
meter is broader scope than Genesis, or POWEN's consumption input is wrong. POWEN's own table also
mislabels units: "PV Capacity (MWp) 87.20 / 259.42" are **kWp**, not MWp.
POWEN options: Opt 1 87.20 kWp / 79.92 MWh p.a. / 92% self-consumption; Opt 2 259.42 kWp /
228.5 MWh p.a. / 50% self-consumption. Yield 917 / 881 kWh/kWp.

## Decisions taken (2026-08-05)

- **D-A — Grundon load shape: RESOLVED.** No HH data exists (client email offers it in future tense:
  "I *can provide* half hourly data"). Direction: derive shapes from **plant-type engineering
  knowledge + the monthly invoice data + the site briefs**, stated and documented. Harwell is *not*
  a valid donor (different industry). Issue a ranked data ask for true HH against the known MPANs.
- **D-B — Model_v5: RESOLVED — repair, then reconcile to corrected.** Done, see
  [benchmark audit](docs/grundon_harwell/00_model_v5_benchmark_audit.md).

## Phase 1 COMPLETE — benchmark repaired (2026-08-05)

Replication of Sim's `Portfolio Build` is **exact** (published 7.75% / 20.88% / 3.325 all reproduced
to the penny; equity CF row max abs diff £0.0000). Three defects found; Fable advisor CONFIRMED all
findings.

| Metric (Central/Central) | Published | Corrected |
|---|---|---|
| Portfolio unlevered IRR | 7.75% | **7.75%** (unchanged) |
| Portfolio levered IRR | 20.88% | **7.19%** (−13.68 pp) |
| Portfolio equity MOIC | 3.325 | **2.228** |

Cause: `Portfolio Build` r34 **adds** interest expense as income (`+r28`); £11.23m mis-signed,
£22.46m swing. Two further defects corrupt `Asset Economics` (debt draw double-counted; interest
never accrued). Both residual checks pass → the sign flip is the only correction needed.

### The blocking structural finding
**Leverage is dilutive at Central: 8.5% cost of debt > 7.75% unlevered return.** Corrected levered
(7.19%) falls *below* unlevered (7.75%). Per-MW **DSCR = 0.996×**; portfolio DSCR 0.94–1.03× across
the debt term — unbankable at 60% gearing. Unlevered 7.75% is also below the model's own 8% discount
rate, so Central is NPV-negative pre-financing.

**Therefore the brief's target pair is internally impossible.** Unlevered ≈7.7% *cannot* coexist with
levered 15–25% at 8.5% debt — it yields ≈7.2%. And **portfolio aggregation cannot close the gap**:
Sim's roll-up only appeared to, via the defect. All legitimate structuring levers together buy
~2–3 pp against a 13.7 pp gap. This is an **assumptions** question, not a dispatch/sizing/portfolio
one.

### 20% *is* reachable — on exactly one defensible combination
Corrected 3×3 matrix: only **Revenue-Optimistic × Capex-Conservative** lands in the band →
**unlevered 15.39%, levered 20.51%, MOIC 4.29**. Needs £103,500/MW Year-1 net revenue → £128,200/MW
steady state, at £675,000/MW installed. Commercially coherent (peak revenue + constrained-site
capex). Cells above the band (44%, 62%) are suspect per brief §3.4.

## Adopted direction (user steer, 2026-08-05)
Model the four Grundon sites **individually** at site level, then add a **portfolio roll-up layer**
following Sim's `Portfolio Build` structure — with defect D1 fixed, not copied. **Caveat now
established:** the roll-up is the right *reporting* structure but is not a route to the target.

## Phase 2 COMPLETE — data foundation (2026-08-05)
Full write-up: [`docs/grundon_harwell/01_data_foundation.md`](docs/grundon_harwell/01_data_foundation.md).
Meter inventory (MPAN/kVA/coverage), 2025 source-split reconciliation from the workbook's own
`2025 Summary` tab, tariff structure, per-site BESS thesis, 9 logged data-quality anomalies, and a
ranked data ask. Headline discoveries beyond the brief:
- **Bishops Cleeve is not off-grid** and its LFG fleet is declining **−19.9% YoY** (8.61→6.90 GWh).
- **Tanhouse & Colnbrook is 100% non-grid** (EfW private wire), 73% of four-site load, at 13.97 p/kWh
  — the cheapest tariff in the estate, which is the hurdle for avoided-cost value.
- **Ewelme Workshop: 750 kVA connection at ~8.7% utilisation** — the headroom play.
- Tariff sheet rates are **commodity-only**; blended delivered cost is 23–33 p/kWh, so ~½–⅔ of cost is
  network/levy — where the real BTM value sits.
- **⚠️ Unconfirmed "Feed in tariff 0.49 p" line.** If that is BC's *export* price rather than a cost
  levy, re-contracting 6.7 GWh/yr is the largest single opportunity here (≈£33k → ≈£470k/yr).
  Not assumed either way; logged `needs_review`.

## Target basis — DECIDED (user, 2026-08-05)
**Central + Stretch, both reported.** Central = honest base (expect low single-digit levered,
DSCR <1.0×). Stretch = engineered into the 15–25% band on Revenue-Optimistic × Capex-Conservative
territory, every carrying assumption classified `Stretch` with a tornado over it.

## Phase 2b COMPLETE — the 20% requirements solver (2026-08-05, commit `c94c0a3`)

New module `gridq.portfolio_requirements` (Fable-designed, 25 tests, quality bar green, output
byte-identical across runs). Answers the **inverse** question: what portfolio would be *required*
to earn 20% levered, on realistic numbers — and returns "infeasible" rather than a number when the
answer sits outside defensible bounds. Live report:
https://claude.ai/code/artifact/a1c664f1-bc25-4cb2-9c68-53cfc50e8477

**Integrity feature:** debt is **DSCR-sized**, never asserted —
`min(60% × capex, PV(CFADS_P90 ÷ DSCR))`. The Model_v5 pathology is structurally unreachable.

### THE FINDING
Across the 231-point grid, **48 configurations reach 20% levered IRR and ZERO are defensible.**

| Required for 20% levered | Value |
|---|---|
| Unlevered IRR needed | **13.92%** (vs 7.75% at Model_v5 Central) |
| Net revenue needed | **£116,666/MW/yr** — 2.83× realistic Central |
| Implied captured spread | **£178/MWh** on 657 MWh/MW/yr |
| Max capex | £500k/MW · Gearing 60% · DSCR 1.90× |
| Classification | **Stretch on every revenue line simultaneously** |

**Realistic own-and-operate ceiling: ~10–14% levered.** 20% is *not* a portfolio-scale question —
IRR is scale-invariant above the fixed-cost floor, and small scale makes it worse through the
capex/opex floors. It is a property of **host quality** (tariff differential, load shape,
connection headroom, capex/MW).

### The five real sites: INFEASIBLE for 20%
Closing the gap needs **£200,823/MW/yr — 4.87× Central and 1.42× above even the Stretch ceiling.**
Load-coupled capacity caps the fleet at **3.9 MW** regardless. Per user direction (2026-08-05) the
20% target therefore lives in this solver as *"what would be required"*; the five sites are
modelled and reported on their **honest Central** returns.

### Where this module disagrees with Model_v5 (per-stream, £/MW/yr gross)
| Stream | Model_v5 | Realistic Central | Why |
|---|---|---|---|
| Frequency response | 18,000 | **5,000** | Saturated since 2023; BTM availability clashes with the peak window. Largest indefensible line. |
| DUoS red-band | 6,000 | 3,500 | Post-TCR only the red unit rate is avoidable |
| Co-optimisation factor | 0.88 | 0.80 | Stacking costs more than 12% |
| Capacity Market | 5,643 | **9,500** | The one line Model_v5 *understated* (used £27.10/kW) |
| Per-site fixed capex/opex | **absent** | £250k/site + £6k/site/yr | Model_v5 omits these entirely — £6.25m missing at 100 MW / 25 sites |

## Phase 3 COMPLETE — REBUILT on the merchant + PPA model (2026-08-05)

**A fundamental reframe was issued and the model was rebuilt.** The earlier version treated the
battery as an *avoided-cost* asset with discharge capped at `site load x 0.35`, making site
consumption the ceiling on earnings. That was wrong. The correct model:

- The customer buys power at a **fixed GBP/kWh PPA rate** — that is the PPA revenue leg.
- **Site load and shape are a CONSTRAINT, not a cap** — they say when the battery must hold energy.
- The battery is otherwise a **merchant asset** trading as hard as its remaining cycles allow.
- **Sizing is capped by CONNECTION CAPACITY and throughput (657 MWh/MW/yr)**, never by consumption.

Three mechanics now drive every number:
1. **PPA-first throughput allocation.** PPA margin (9.7-15p/kWh) beats merchant arbitrage
   (6.4p/kWh), so the obligation is served first. **At sub-load-scale batteries the PPA consumes
   the entire throughput and the merchant energy leg is ZERO** — only capacity streams survive.
2. Energy streams (arbitrage, BM, DUoS) scale with the merchant share; capacity streams
   (CM, FR, flex) do not — they need export rights and SoC headroom, not net cycles.
3. **Sleeved supply is a structural precondition.** If the battery must charge on the customer's
   flat tariff (15-33p), the PPA leg is loss-making at EVERY site.

### Levered IRR by site and case  (✗ = fails the ~11.7% gross-yield bar · ⚠ = >25%, self-flagged suspect)

| Site | Pessimistic | Central | Optimistic |
|---|---|---|---|
| Grundon — Bishops Cleeve | -11.1% ✗ | 8.2% ✗ | 13.7% |
| Grundon — Tanhouse & Colnbrook | 13.9% | 18.8% | 18.9% |
| Grundon — Knowl Hill | -10.5% ✗ | -2.3% ✗ | 7.9% ✗ |
| Grundon — Ewelme | -16.1% ✗ | 1.0% ✗ | 19.5% |
| Harwell Science & Innovation Campus | 25.5% ⚠ | 29.8% ⚠ | 52.0% ⚠ |

### Risk-adjusted priority

1. **Harwell Science & Innovation Campus** — Harwell solar — ~15-20% levered Central, the estate's strongest return, and it depends on neither the campus network agreement nor a battery. The wide band is purely because the campus tariff is unverified: get one invoice. NO battery IRR is quotable here.
2. **Grundon — Tanhouse & Colnbrook** — Tanhouse & Colnbrook solar — ~13-16% levered Central. Strong, but gated on the EfW private-wire contract permitting displacement. Colnbrook at 13.97 p/kWh CANNOT take a battery PPA at any discount; a new grid connection for merchant trading does not clear the bar at Central and is an option to price, not a case to underwrite.
3. **Grundon — Bishops Cleeve** — Bishops Cleeve — ~7-9% levered Central, rising to ~17-20% Optimistic. THE REFRAME'S BIG WINNER: it holds the estate's only >=1 MW export route, already built and carrying 6.7 GWh/yr. Under the old avoided-cost model this site looked worst; on the merchant + PPA model it is the only battery whose Central case approaches fundable.
4. **Grundon — Ewelme** — Ewelme — fails at Central (~1-3%), clears at Optimistic (~14-17%). The 750 kVA headroom is real, but at 0.6 MW the PPA obligation consumes the entire throughput so no merchant energy leg exists, and the sub-1MW cost stack still bites. An Optimistic-case project.
5. **Grundon — Knowl Hill** — Knowl Hill — ~0-2% levered Central, fails. 325 kVA binds everything and the PPA consumes all throughput. Clears only at Optimistic (~9-11%) with a connection upgrade. Hold.

### The three levers that move the estate's Central case

1. Bishops Cleeve export and genset-charging confirmation — it is the only >=1 MW market access in the estate and the only battery whose Central case approaches fundable.
2. The Knowl Hill tariff basis and the Ewelme MIC status — two documents that decide whether either site has a project at all.
3. Attacking the GBP250k per-site fixed capex plus the GBP700k/MW sub-1MW cost stack. This is the single biggest destroyer of value across the estate: every site except Tanhouse is sub-1MW, where fixed costs dominate. Portfolio procurement across all five sites is the lever.

### What the reframe changed
- **Bishops Cleeve went from worst to best battery site.** It holds the estate's only >=1 MW export
  route, already built and carrying 6.7 GWh/yr. Central 8.2% levered, Optimistic 13.7%. Under the
  old model it looked uninvestable.
- **Solar leads the estate.** Harwell ~29.8% and Tanhouse ~18.8% levered Central — but both sit
  behind unverified documents, so both are financed on the **merchant tier** and Harwell is
  **self-flagged suspect** (>25%) because its rate is assumed, not billed.
- **Colnbrook cannot take a battery PPA at any discount** — at 13.97 p/kWh delivered the margin is
  negative against a 13-15.6p cost floor. Solar can undercut it (LCOE ~5-6p); a battery cannot.
- **Per-site fixed capex reduced to GBP25k for solar-only.** A rooftop array needs no connection,
  ICP, protection or metering package; loading GBP250k onto it would be a modelling artefact.
- Knowl Hill's "15.41p vs 33.02p" was not a contradiction: 15.41p is the commodity day rate,
  33.02p the blended delivered cost. The PPA reference is the delivered rate.

## Deliverables — ALL PRODUCED (`deliverables/`, 22 files)
One command regenerates everything, byte-identically:
`python -m gridq.portfolio_requirements.generate --out deliverables`

- `00_portfolio_requirements.html` — what portfolio would be required to earn 20% levered
- `01_portfolio_summary.html` — the estate, five sites x three cases
- `<site>_info_pack.html` x5 — consultancy-grade packs
- `<site>_deck.html` x5 — 16:9 slide decks
- `<site>_assumptions_register.md` + `.json` x5 — classified, sourced registers

Live: [estate summary](https://claude.ai/code/artifact/155d5c10-7a79-40e5-9efc-a6b17fd12523) ·
[Bishops Cleeve pack](https://claude.ai/code/artifact/42891fb4-37d4-4c3f-a686-b9714aa9bb6e) ·
[20% requirements](https://claude.ai/code/artifact/a1c664f1-bc25-4cb2-9c68-53cfc50e8477)

## Platform Projects — ALL FIVE REGISTERED
`python -m gridq.portfolio_requirements.register` — saved via `gridq.app.store.save_run` under
deterministic `quote_id`s from `build_run_context`. **`quote_mode=indicative`, never underwritten**
(derived shapes for four sites + unresolved blocking verifications). Every data-quality anomaly and
blocking item is carried onto the `QuoteRecord.warnings`, not left in a document.

## Quality bar
32 tests pass; ruff, format and mypy clean; deliverable output byte-identical across runs.
New integrity tests: thin yield can never reach 60% gearing; debt + equity must equal capex;
returns >25% must self-flag AND carry a Stretch classification; no battery may be proposed at
Tanhouse without an explicit new-connection dependency.

## Next action
Awaiting the four gating documents. Nothing further can be modelled without them:
1. **Bishops Cleeve** — export MEC/G99 headroom in writing, the genset-charging arrangement, and
   whether the 0.49p line is a levy or the export price.
2. **Tanhouse & Colnbrook** — the EfW private-wire contract (displacement permitted? parallel DNO
   connection prohibited?), plus a budget DNO connection offer (~GBP1-2k) to price the option.
3. **Ewelme** — connection MIC status post-aerosol-plant.
4. **Harwell** — one invoice, and the campus network agreement.
Plus, for all four Grundon sites: **the half-hourly data the client has already offered.**
