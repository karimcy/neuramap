# 2026-07-03 — Capex shape research (C025 resolution basis)

**Purpose:** ground the `battery_capex` energy (£/kWh) component that resolves **C025** (per-MW-only
capex makes every wide-duration sizing search pin duration to the band edge — added energy was
capital-free). Perimeter research, X.1 discipline: these figures enter the library ONLY as
labelled placeholders via `assumptions/gb_v1_3.yaml`; nothing here touches a pricing path
directly, and no quote using them can present as bankable.

## Findings (perimeter web research, 2026-07-03 — UNVERIFIED external references)

- **BloombergNEF Energy Storage System Cost Survey 2025** (596 submissions): global average
  turnkey BESS price **US$117/kWh** (−31% YoY); 2-hour systems US$124/kWh, 4-hour US$110/kWh.
  Regional: China US$73/kWh, **Europe US$177/kWh**, US US$219/kWh.
- Reported European installed range for turnkey 4-hour LFP systems: **US$180–260/kWh**
  (site/scale/content dependent). At ~0.79 GBP/USD that is roughly **£142–205/kWh**.
- **NREL 2025 utility-scale benchmark** (bottom-up, 4-hour, US context, fully loaded EPC):
  US$334/kWh — an upper anchor showing fully-integrated project £/kWh sits well above pack
  prices (BNEF stationary pack average 2025: US$70/kWh).
- Cost-structure convention (NREL ATB and industry practice): decompose system capex into a
  **power component** (PCS/inverter, BoP, grid connection, EPC power share — £/MW) plus an
  **energy component** (cells/racks/enclosures — £/kWh). This is exactly the shape C025 asks
  for.

## The gb_v1_3 decomposition (labelled placeholder, reconciled to the sourced all-in)

The library's only sourced capex anchor is the Neura plan's **"roughly £500k per MW"** all-in
installed cost (needs_review, gb_v1_0). Treating that as a **2-hour reference system** (the
plan's indicative C&I shape) and taking an energy component inside the European research band:

- `installed_cost_gbp_per_kwh` = **175** (£/kWh) — inside the £142–205/kWh European installed
  band; C&I behind-the-meter at 100–500 kW scale sits above utility-scale averages.
- `installed_cost_gbp_per_mw_power_component` = **150000** (£/MW) — the residual that makes the
  decomposition reconcile: 150,000 + 2h × 1,000 kWh/MW × £175 = **£500,000/MW at 2h** — exactly
  the sourced all-in anchor.
- Implied all-in at other durations: 1h ≈ £325k/MW · 4h ≈ £850k/MW · 8h ≈ £1.55M/MW — added
  duration now has real marginal capital cost, so a wide-duration optimum is an economic
  trade-off, not a band-edge artifact.

**Status discipline:** both entries are `placeholder`, confidence `low`, owner Olly; the split
ratio (30/70 at 2h) is research-grade, not a quoted contract price. Confirm against real
installer quotes / a capex benchmark library before any bankable use — tracked in
`docs/assumptions_to_source.md` alongside the parent `installed_cost` item.

## Sources

- [Energy-Storage.news — Battery storage system prices continue to fall sharply, BNEF and Ember reports find](https://www.energy-storage.news/battery-storage-system-prices-continue-to-fall-sharply-bnef-and-ember-reports-find/)
- [BloombergNEF — New record lows for battery prices](https://about.bnef.com/insights/clean-transport/new-record-lows-for-battery-prices/)
- [NREL — Cost Projections for Utility-Scale Battery Storage (2025)](https://docs.nrel.gov/docs/fy25osti/93281.pdf)
- [howtostoreelectricity.com — BESS cost per MWh, utility scale 2026](https://howtostoreelectricity.com/bess-cost-per-mwh-utility-scale-2026/)
- [Ember — How cheap is battery storage?](https://ember-energy.org/latest-insights/how-cheap-is-battery-storage/)
