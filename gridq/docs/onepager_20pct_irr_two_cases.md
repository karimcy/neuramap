# Two worked cases: what would take each to a 20% levered IRR

### Demo Cold Store (the engine) · Robert Thomas Farms (the ingestion)

*Olly, 2026-07-21. Every number is produced by the deterministic engine on `gb_v1_6` and is traceable
to the run record; all figures are placeholder-based and **not bankable**. Two live examples, each
showcasing a different current capability of the system.*

---

## Case A — Demo Cold Store · showcases the optimisation + finance engine

The fully-consented demo runs the **real MILP dispatch + two-stage sizing** over a synthetic 12-month
half-hourly load, with illustrative (labelled, not-underwritten) debt injected so the full levered
case populates. It is a viable, priced, covenant-gated result:

| Metric | Value |
|---|---|
| Chosen system | **166.7 kW / 527 kWh** (~3.2 h) |
| Project (unlevered) IRR | **10.84%** |
| Equity (levered) IRR | **11.86%** (at the illustrative 25% gearing) |
| Min P90 DSCR | **2.38** vs a 1.3 covenant (passes) |
| Simple payback | 6.6 years |
| Priced floor | **≈ £37,043/yr** (arbitrage £19.8k + DUoS red-band £16.2k firm; BM/ancillary/wholesale £1.1k contracted) |
| Price to customer | **£0.208/kWh** vs £0.236 grid counterfactual (~12% saving) |

**What it proves:** the engine sizes a real asset, sculpts debt, gates on the P90 DSCR covenant,
solves the customer price against the counterfactual, and reports a full levered return — all
deterministic and traceable, and still honestly `not_bankable` on placeholder assumptions.

**Path to 20% — and why the engine won't fake it.** Gearing is positive leverage here (project
10.84% > debt 7.5%), so more debt lifts equity IRR — but the **P90 DSCR covenant (1.3) binds at
~46% gearing**, capping equity IRR at **~13%**. Reaching 20% would need ~70% gearing, where DSCR ≈
0.85 (below 1.0 — the asset can't even cover debt service), which the covenant correctly forbids.

| Gearing | Equity IRR | Min P90 DSCR | Covenant (1.3) |
|---|---|---|---|
| 25% | 11.86% | 2.38 | ✅ |
| 40% | 12.85% | 1.49 | ✅ |
| ~46% | ~13.4% | 1.30 | ← binding |
| 60% | 15.25% | 0.99 | ❌ |
| 70% | 17.59% | 0.85 | ❌ |

So **capital structure alone cannot reach 20%.** The genuine lever is raising the **10.84% unlevered
return**, which lifts equity IRR *and* unlocks higher covenant-feasible gearing:

- **Contract real-magnitude flexibility revenue into the floor** (only FIRM + CONTRACTED streams count
  in CFADS): a signed Piclo tender, an export PPA, contracted DSR/CM. Today's conditional/gated
  streams are only ~£2.1k/yr, so firming them *as quoted* adds ~1 point — the uplift needs real
  contracted volumes, not the placeholder figures. This is the two-sided firming thesis in one number.
- **Capex below the £500k/MW placeholder** (a real installer quote) moves the unlevered return directly.
- Confirmed cheaper debt (6% strong-credit) helps a little but still can't reach 20% within the covenant.

> Gearing and credit terms are **Sim's** portfolio-finance calls; the demo's 25% is an illustrative
> display value, not GridQ underwriting. Covenant-failing gearings above are shown only to locate the
> ceiling — they are an infeasible region, never an offer.

## Case B — Robert Thomas Farms · showcases the data ingestion

A **real** prospect (Haywood Oaks). The ingestion stack turned a messy real-world intake into a
validated `SiteModel` and priced every stream from real data:

- **Partial-year half-hourly** (252 days) → correctly graded **T1** (modelled, not metered), not T2.
- **Hand-transcribed invoice tariff** (day/night, CCL, standing charge, 250 kVA capacity), an observed
  **421 kW peak breaching the 250 kVA ASC**, consents and an export arrangement — all schema-validated.
- Result: an honest **NO VIABLE CONFIGURATION** across 141 priced candidates, with ranked data-asks —
  no invented system. (A *levered* return was never computable: `credit_status = unknown` → no debt
  terms → the sizer fell to its labelled unlevered indicative basis.)

**Why unviable:** the SmartFix **fixed** tariff gives only a ~1.5p/kWh arbitrage spread → a **£1,772/yr
ceiling**; DUoS is bundled so red-band avoidance is **£0**; and fixed site opex (~£2.7k/yr) *exceeds*
the whole arbitrage ceiling — CFADS is structurally negative at any size.

**What would improve it — precision vs viability are different asks:**

- **Data-asks** (raise fidelity + unlock a levered number): the **company number → a credit
  assessment** — verified that this *alone* assembles full debt terms (60% gearing from the library,
  rate/tenor from the band), so a levered IRR becomes computable; **12 months of HH → upgrades T1 to
  T2**. These sharpen the quote; they do **not** flip the verdict.
- **Commercial changes** (the only lever that makes the site viable): the SmartFix tariff **expired
  1 Apr 2026** — re-contract to pass-through / ToU with real spreads; contract Piclo local flex; and
  with export consent + a signed route-to-market, export is priced **CONTRACTED into the floor** (not
  merely gated upside).

---

**Honest framing.** *Demo:* the engine computes a full, covenant-gated levered case and refuses to
manufacture 20% — the covenant caps it near 13%; 20% needs real contracted revenue and cheaper capex
(the firming thesis), not leverage. *Robert Thomas:* the deliverable is the honest refusal — every
stream priced from real, messy data, proving the site unviable on its now-expired fixed tariff; the
forced re-contract is the commercial opening, and the ranked data-asks are the path to a levered,
T2 re-run.

*Guardrails: deterministic core, no invented numbers on the pricing path, placeholders labelled,
contracts changed only deliberately. See [north_star.md](north_star.md), CLAUDE.md §4.*
