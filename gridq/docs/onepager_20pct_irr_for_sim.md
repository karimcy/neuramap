# What needs to change to reach a 20% levered IRR

### Robert Thomas Farms (Haywood Oaks) — a worked case, and a first build for Sim

*Drafted for Sim's onboarding, 2026-07-21. Every number below traces to a source (run record, the
`gb_v1_6` assumption library, or the red-team derivation in [redteam/EXPECTED_RESULT.md](redteam/EXPECTED_RESULT.md)).
The site is a real prospect; all finance numbers are **placeholder / not bankable** — which is exactly
the point of the work.*

---

## Bottom line

On today's engine and our newest data release (`gb_v1_6`), Robert Thomas Farms prices to **"no viable
configuration"** — 141 candidate battery sizes are evaluated, dispatch solves, and **none earns a
positive return at any size**. A 20% levered (equity) IRR isn't a knob we can turn: the site needs
roughly **9–14× more firm annual revenue** than it can currently earn, *and* one hard precondition
before a levered number can even be computed. This one-pager is the map of what must change — and it
sits squarely in your domain, so it's the ideal first thing to own.

## 1. What the engine says today

```
gridq run examples/real/robert_thomas_sitemodel.json \
  --hh examples/real/robert_thomas_haywood_oaks_HH.csv --assumptions gb_v1_6

→ selection_status = no_viable_configuration   (solver_status = optimal)
→ 141 candidates evaluated; none has a positive project IRR or levered return
→ financial model skipped (no positive system to price) · bankable_status = not_bankable
```

This is the *correct* answer, not a bug — the engine refuses to recommend a micro-battery just to
have something to show. The honest gap right now is that it can say **"no"** but can't yet say
**"here's exactly what would have to be true for 20%."** Closing that gap is the build (§4).

## 2. Why the site fails (the numbers)

Haywood Oaks is on a **SmartFix fixed tariff** (day 20.36p / night 16.5p /kWh, CCL 0.775p, DUoS
bundled into the unit rate). That leaves almost nothing for a battery to arbitrage:

| Lever | Value | Why it's capped |
|---|---|---|
| **Day/night arbitrage** (only firm energy lever) | **≈ £1,772/yr ceiling** | Spread after 88% round-trip losses = **1.5p/kWh** moved; day-band import ≈ 117,800 kWh/yr. At £175/kWh energy capex that's a **~32-year payback**. |
| **DUoS red-band avoidance** | **£0** | SmartFix bundles DUoS into the unit rate — there's no separable red-band charge to avoid (correctly excluded). |
| **Capacity Market** | conditional only | Non-exporting BTM storage reaches CM only via aggregated DSR — never enters the priced floor. |
| **Wholesale / BM / ancillary** | **£0 (gated)** | No export consents evidence, no route-to-market partner. |
| **Fixed site opex** | **−£2.7k/yr** | Metering/IDNO (£1.2k) + DNO (£1.5k) alone **exceed the entire arbitrage ceiling.** |

The engine prices only **FIRM + CONTRACTED** streams into the cashflow floor. On this site that floor
is ~£1.8k/yr of shaky arbitrage against £2.7k/yr of fixed costs before the battery is even built. No
size fixes that — it's structural.

## 3. What must change — the four levers, ranked

1. **A credit band — the precondition (blocks *everything* levered).** The counterparty's
   `credit_status` is `unknown`, which our credit policy deliberately refuses to map to a rate/tenor.
   No band → no `DebtTerms` → **the equity IRR is literally uncomputable** (the engine falls back to
   an unlevered project IRR). Get the company number → assess → and a levered number exists. This is
   step zero.
2. **A tariff restructure — the biggest firm lever.** The SmartFix contract **ended 1 Apr 2026**, so
   re-contracting is open *now*. A pass-through / time-of-use supply with real DUoS bands turns
   arbitrage and red-band avoidance into firm revenue with genuine spreads. (This is a **data event** —
   new contract terms into intake — never a number we invent.)
3. **Contracted local flexibility (Piclo).** The only non-gated stream that can join the floor as
   CONTRACTED. Needs a real NGED East Midlands tender + signed contract; our sourced GB median
   utilisation price is **£135.67/MWh** (`gb_v1_6`, live Piclo data).
4. **Real capex/opex below the placeholders.** £500k/MW installed is an indicative `needs_review`
   figure; the required-revenue maths is highly sensitive to it, so a real installer quote moves the
   answer materially.

> **Export consents + a route-to-market partner cannot help the IRR.** They surface *gated upside* for
> the customer story, but the floor excludes them by design — worth knowing so we don't chase them for
> bankability.

**The size of the gap:** a representative candidate needs **≈ £16–23k/yr of firm + contracted
revenue** to clear a 20% equity IRR (60% gearing, adequate credit, 1.3× P90 DSCR covenant) — versus
the £1,772 ceiling today. And at a **20% target the equity-IRR requirement binds, not the covenant**
(the covenant only starts to bind down around the current 12% hurdle) — a genuinely
finance-interesting inversion.

## 4. Why this is yours to own, Sim — and the build

Every dial that sets the 20% answer is a **placeholder waiting for portfolio finance** — most are
already tagged *"confirm with Sim"* in the library:

| Assumption (`gb_v1_6`) | Current value | Status |
|---|---|---|
| `financing.target_equity_irr_pct` (the hurdle itself) | 12% | **needs_review** — *"confirm with Sim"* |
| `financing.debt_gearing_pct` | 60% | **placeholder** — *"confirm with Sim"* |
| `financing.credit_rate_tenor_policy` | strong 6%/12y · adequate 7.5%/10y · weak 9.5%/7y | **placeholder** |
| `dscr_covenants.min_p90_dscr` | 1.3× | **placeholder** |
| `p50_p90_haircuts.p90_revenue_haircut_pct` | 15% | **placeholder** |
| `p50_p90_haircuts.perfect_foresight_haircut_pct` | 20% | **needs_review** |

**The build: a "required-uplift" (target-inversion) tool.** Instead of only reporting "no viable
config," the engine should answer *"what must be true for X% levered IRR?"* deterministically:

1. **Inversion core** (`src/gridq/finance/required_uplift.py`, new) — bisect an additive annual
   firm-revenue uplift *through the existing forward finance model* until the P50 equity IRR hits the
   target and the P90 covenant holds. Reuse the real model (no second solver to drift); report a
   per-credit-band table when the band is unassessed. **Never invent a revenue number** — output a
   *requirement*.
2. **Wire into the pipeline + CLI** — a `--target-equity-irr` flag (default = the library hurdle), and
   an additive `required_uplift` field on the quote record (no frozen-contract change). Runs with the
   flag off stay byte-identical.
3. **Per-stream translation** — turn the £ uplift into quantified asks priced *only* at sourced values
   ("needs X MWh/yr contracted Piclo at £135.67/MWh"; "needs a Y p/kWh spread vs today's 1.5p").
4. **A labelled what-if pack for RT** — a credit-band-known variant that produces the site's **first
   real levered IRR number today**, plus tariff-restructure and Piclo variants shipped *blocked on
   data* with named evidence slots. (These are full `gridq run` variants, not scenario multipliers —
   a tariff change alters the dispatch problem itself.)

Full ordered plan with files, tests and acceptance criteria is ready to hand you on day one.

## 5. First decisions for you

- **Is 20% the right portfolio hurdle**, or is 12% (or something else) our real target? `target_equity_irr_pct`
  is yours to set — the tool takes it as a parameter so we can ask both without editing the library.
- **Confirm or replace** the gearing (60%), credit bands, DSCR covenant (1.3×) and P50→P90 haircuts
  against a real term sheet — each is illustrative today.
- **Hold gearing fixed, or expose a gearing sensitivity** in the per-band table? (Recommend fixed +
  flagged placeholder, to avoid over-claiming precision on a number that isn't sourced yet.)

---

*Guardrails this work lives under: deterministic core (identical inputs → identical output), no
LLM-generated or invented numbers on the pricing path, placeholders stay labelled, and the
platform↔engine contracts change only deliberately. See [north_star.md](north_star.md) (the two-sided
firming thesis) and CLAUDE.md §4 for the full principles.*
