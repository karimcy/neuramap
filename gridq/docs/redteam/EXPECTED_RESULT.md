# The correct result for `examples/real` — derivation and acceptance criteria

**Status:** authoritative definition for the red-team E2E rebuild (2026-07-07, Olly).
**Inputs considered:** ONLY the three files in `examples/real/` — `haywood_oaks_prefill.json`,
`invoices_tariff_extract.md`, `robert_thomas_farms_site_facts.md` (the `invoices/` directory is
empty; the referenced `robert_thomas_haywood_oaks_HH.csv` is NOT present). No other information
is assumed. Where the invoice is silent the engine's assumption release supplies the value under
its normal placeholder/disclosure rules.

---

## 1. What the files actually establish (input inventory)

From `invoices_tariff_extract.md` (SmartestEnergy invoice EVY220LUQ/007, Oct 2025, transcribed):

| Fact | Value | Where |
|---|---|---|
| Energy rate, day | 20.36 p/kWh | invoice line |
| Energy rate, night | 16.5 p/kWh | invoice line |
| Climate Change Levy | 0.775 p/kWh on every imported kWh | invoice line |
| Standing charge | £25.61/day | invoice line |
| Capacity (availability) charge | 8.76 p/kVA/day × **250 kVA** ASC | invoice line |
| Excess-capacity charges | £472.51 (May 2025) + £507.20 (Jun 2025) back-billed | invoice lines |
| Excess reactive power | ~£1–2/month (negligible) | invoice lines |
| DUoS | **no separate red/amber/green lines** — bundled into the unit rates (SmartFix fixed contract) | extract note 3 |
| DNO region | NGED East Midlands (MPAN distributor 11) | MPAN |
| Contract | SmartFix Standard, ends 01 Apr 2026 | invoice header |

From `robert_thomas_farms_site_facts.md` (observed on 252 days of HH data, Apr–Dec 2025 —
the CSV itself is not shipped; these are its documented summary statistics):

| Fact | Value |
|---|---|
| Annual consumption (implied) | ~215,000 kWh/yr (588 kWh/day) |
| Peak demand | ~421 kW (max half-hour 210.5 kWh) |
| Load shape | heavy overnight (~24 kWh/HH ≈ 48 kW avg 00:00–04:00), **near-zero 09:00–14:00**, rising evening |
| Day/night split (Oct invoice volumes) | day 10,240.7 kWh / night 8,450.3 kWh ≈ **54.8% day** |
| HH coverage | 252 days (not a full year) — T1, not T2 |
| Export / G99 / PV | unknown (open question: why near-zero midday?) |
| Counterparty | Robert Thomas Farms Ltd, company number unknown, credit unassessed |

**Immediate consequences.** The observed peak (421 kW) exceeds the contracted capacity
(250 kVA) — the site already breaches its ASC (hence the excess-capacity lines). There is no
pass-through DUoS exposure, no export route, no confirmed PV. The half-hourly series is
summarised but not supplied, so any modelled profile is T1 "modelled, not metered" by
definition, and must honour the documented constraints (annual energy, day/night split,
overnight-heavy shape, 421 kW peak) rather than substitute a daytime business archetype.

## 2. Value-lever analysis (what a battery can earn here)

Using the engine's own conventions (no invented numbers; placeholder assumptions disclosed).
RTE = 0.88 AC-AC (`gb_v1_3 round_trip_efficiency.ac_round_trip`, placeholder).

### L1 — Day/night tariff arbitrage (the only firm energy lever)

Every discharged kWh in the day band avoids `day + CCL`; it was charged in the night band at
`(night + CCL) / RTE` (losses are bought):

```
margin = (0.2036 + 0.00775) − (0.165 + 0.00775)/0.88
       = 0.21135 − 0.19631 = £0.01504 per discharged kWh
```

The discharge opportunity is bounded by day-band import: 54.8% × 215,000 ≈ **117,800 kWh/yr**.

**Ceiling: 117,800 × £0.01504 ≈ £1,772/yr** — and capturing all of it needs ≥ ~330 kWh usable
cycled daily (capex ≈ £75k at gb_v1_3 rates). Per-kWh: £0.01504 × 365 = **£5.49/kWh-yr revenue
vs £175/kWh energy capex — a 32-year simple payback before any opex**. No battery size makes
day/night arbitrage at a 3.86p spread pay back. This is structural, not a tuning issue.

### L2 — DUoS red-band avoidance

**£0 — correctly excluded.** The SmartFix contract bundles DUoS into the unit rates; there is
no separately avoidable red-band line. (The generic 18k £/MW-yr placeholder must NOT be applied;
the stream stays `excluded` for this site.)

### L3 — Capacity: availability + excess-capacity charges

- Availability: 8.76 p/kVA/day × 250 kVA = £21.90/day ≈ **£7,993/yr — fixed, not avoidable by a
  battery** (the ASC cannot drop below the un-shaved peak; the site is already over it).
- Excess capacity: £979.71 observed across two back-billed months (May, Jun 2025). The observed
  evidence supports **≤ ~£2k/yr** of exposure, and the breaches are overnight (the peak is
  overnight), so shaving them requires charging in the day band and discharging at night —
  *paying* the arbitrage spread in reverse (−1.5p/kWh moved) to avoid the charge. Requires
  ≥171 kW of discharge sustained across the night peak. Net value after the energy inversion:
  marginal at best; the honest treatment is to model the capacity lines on the bill, quantify
  the observed exposure, and rank the missing capacity-breach history as a data-ask.

### L4 — Capacity Market (conditional)

Non-exporting BTM storage can only reach the CM via an aggregated DSR route — conditional, not
firm. At gb_v1_3 values (£30/kW-yr clearing, needs_review; 0.95 de-rate at 4h): an 80 kW system
⇒ ~£2.3k/yr conditional; even 250 kW ⇒ ~£7.1k/yr conditional. Never in the firm floor.

### L5 — Wholesale / BM / ancillary (gated)

**£0** — no export consents (G99 unknown), no route-to-market partner. Correctly gated.

## 3. Cost stack (engine's own release, corrected shape)

Capex (gb_v1_3, placeholder, research-shaped): £150k/MW power + £175/kWh energy.
Recurring site lines (placeholders): metering/IDNO £1.2k, DNO £1.5k, insurance 0.5% capex,
business rates £8k/MW, O&M (see below), parasitic load ~1% of rated power bought at the blended
import rate.

> **Red-team note (self-corrected):** `battery_opex.annual_fixed_opex` initially read as a flat
> £12k/yr per site — which would have been the wrong shape. Verification against the consuming
> code (`finance/cashflows.py:391`) shows it is **£12,000 per MW-yr, multiplied by rated power**
> (the yaml's `unit: GBP/MW/yr`): the shape is already power-scaled and the value (£12/kW-yr)
> sits at the bottom edge of the published £12–15/kW-yr band (NREL ATB 2024 commercial ~2.5% of
> capex/yr; Lazard LCOS v10). The register records the corrected reading. The verdict below
> never depended on it: 15 years of the arbitrage ceiling (£5.49/kWh-yr × 15 ≈ £82/kWh) does
> not repay the £175/kWh energy capex with zero opex at all, and the flat per-site lines
> (metering + DNO ≈ £2.7k/yr) exceed the whole arbitrage ceiling below ~500 kWh.

### Best-case candidate check (most favourable size, generous assumptions)

80 kW × 4 h (320 kWh): capex = £12k + £56k = **£68k**.

| Line | £/yr |
|---|---|
| L1 arbitrage (all cycles, warranty-capped 250/yr @4h: 320×250×0.01504) | +£1,203 |
| L4 CM (conditional, not firm) | +£2,280 |
| L3 excess-capacity avoidance (generous, ignores energy inversion cost) | +£1,000 |
| O&M (power-scaled, ~£15/kW-yr) | −£1,200 |
| Metering/IDNO + DNO | −£2,700 |
| Insurance (0.5% × £68k) | −£340 |
| Business rates (£8k/MW × 0.08) | −£640 |
| Parasitic (0.8 kW × 8,760 h × ~£0.19/kWh) | −£1,331 |
| **Net (firm + conditional, before degradation/haircuts)** | **≈ −£1,700/yr** |

Negative annual cashflow on £68k of capital — before the 20% perfect-foresight haircut, P90
haircuts, and degradation. Smaller systems lose the fixed site lines ratio; larger systems add
capex faster than the capped revenue. **No candidate in any search space is cashflow-positive,
let alone covenant-feasible.**

## 4. The correct result (definition)

Given ONLY `examples/real`, a correct underwriting pipeline must output, deterministically:

1. **A completed quote** (no crash, no "insufficient data" cop-out): data graded **T1** (modelled
   profile from documented billing constraints — annual energy, 54.8%/45.2% day/night split,
   overnight-heavy shape honouring the 421 kW observed peak), loudly labelled modelled-not-metered.
2. **The full transcribed tariff stack consumed**: day/night unit rates, CCL 0.775 p/kWh as a
   levy line, standing charge £25.61/day, capacity 8.76 p/kVA/day × 250 kVA ASC, and the observed
   excess-capacity history — with the customer bill model carrying energy + levy + capacity lines
   (standing charge displayed; unavoidable lines marked unavoidable).
3. **Stream values**: TARIFF_ARBITRAGE small positive (bounded by §2 L1 — order £1–2k/yr at
   capture sizes, never more than the £1,772 ceiling); DUOS_RED_BAND_AVOIDANCE excluded;
   CAPACITY_MARKET conditional only; wholesale/BM/ancillary gated £0.
4. **Sizing verdict: NO VIABLE CONFIGURATION.** Zero candidates price (no IRR at any size). The
   engine must say exactly that as a first-class verdict — it must NOT present the smallest
   corner of the search grid as a "recommended system" (the pre-fix behaviour: the rank-key
   tie-break selected 3.98 kW × 3.09 h purely because nothing was priceable).
5. **Price recommendation grounded in the counterfactual**: the fixed-PPA price required to hit
   the target return (~£1.06/kWh) exceeds the customer's current blended import cost
   (~£0.21/kWh) by ~5× ⇒ reported as **not commercially viable**, never as a bare number.
6. **Ranked data-asks** carried on the quote: the overnight-profile question (PV vs night
   operations), the missing Jan–Mar HH months, tariff detail confirmation against binary PDFs,
   MIC/ASC reconciliation (421 kW peak vs 250 kVA), G99/export status, company number/credit.
7. **Not bankable** (placeholder assumptions) — as always, explicitly stamped.
8. **Deterministic**: an identical re-run produces the identical quote id and hashes.

**In one sentence: the correct output is a transparent, evidence-complete "do not proceed at the
current tariff" underwriting verdict with quantified ceilings and ranked asks — not a
recommended micro-battery.** A battery case at this site only opens if (a) the tariff moves to
pass-through DUoS / ToU with real spreads, (b) export + RTM unlock stacked revenues, or (c) the
midday near-zero import turns out to be PV that a battery could time-shift — each of which is a
data-ask, not an assumption.

## 5. Acceptance criteria (the automated E2E test asserts these)

As implemented in `tests/test_e2e_examples_real.py` (amendments vs the first draft noted —
each is the *more* honest shape discovered during the fix):

- `AC-1` Pipeline runs headlessly from `examples/real` alone (prefill → POST /api/run → done);
  the quote persists and the results page serves it.
- `AC-2` The site record's tariff stack carries the CCL levy line (0.00775), the capacity
  charge (0.0876 £/kVA/day), the standing charge, the day/night rates and the tariff name;
  `connection.asc_kw = 250`. *(Amended: a no-viable-verdict quote carries no customer bill at
  all — there is no chosen dispatch to decompose — so the bill-line rendering of
  standing/capacity is asserted on the viable demo path instead.)*
- `AC-3` The modelled profile's annual energy ≈ 215,000 kWh (±0.1%), its peak = 421 kW (±1%),
  day-band share ≈ 54.8% (±2pp), near-zero midday; the stated annual figure survives on
  `annual_consumption_kwh` (INV-M0-13); the run carries the MODELLED-not-metered label and the
  per-choice modelling notes; tier T1.
- `AC-4` `selection_status == no_viable_configuration`, `solver_status == optimal` (the
  dispatch SOLVED; the economics said no), no selected spec, the verdict warning present, and
  the preserved sizing surface (≥50 points) shows no positive return anywhere. *(Amended from
  "no candidate is priceable": candidates ARE priceable — cashflow models exist — they are all
  unviable, which is the distinction the fix introduced.)*
- `AC-5` A representative candidate (80 kW × 4 h) solved directly against the stored site earns
  TARIFF_ARBITRAGE ≤ the £1,772 analytic ceiling, and DUoS-red-band value is exactly £0.
  *(Amended: the final no-spec result carries gate-only £0 streams — correct, no system ⇒ no
  revenue — so the ceiling is asserted on a per-candidate dispatch.)*
- `AC-6` No phantom finance: `financial_case is None` with the "Financial model skipped"
  disclosure. *(Amended from "price recommendation flagged vs counterfactual": with no viable
  system the honest output is NO price at all. The counterfactual flag
  (`PriceRecommendation.exceeds_counterfactual` + warning) exists and applies to any run where
  a price IS computed.)*
- `AC-7` The ranked data-asks include the full-year HH ask and the export/G99 ask.
- `AC-8` A second full pipeline run reproduces the identical `quote_id` (determinism).
- `AC-9` The quote is stamped not_bankable, and all three artifacts (report, proposal, memo)
  plus the results page present the verdict as a first-class statement, never an empty spec.
