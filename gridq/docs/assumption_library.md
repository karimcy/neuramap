# Assumption Library

Every underwriting number lives here — in versioned YAML/JSON, never as a private constant in a pricing, finance, dispatch or output module. Modules read assumptions via `gridq.assumptions.load_assumptions(version)`; the returned `AssumptionLibrary` is the only door to these numbers.

## The record

Each assumption is a fully self-documenting entry — all eight fields are **required**:

| field | meaning |
|-------|---------|
| `value` | the number (or a structured value, e.g. a per-duration mapping) |
| `unit` | e.g. `GBP/MW`, `ratio`, `percent`, `cycles/yr` |
| `source` | where it came from (a document, a provider, or `placeholder`) |
| `status` | `confirmed` · `placeholder` · `needs_review` |
| `owner_or_reviewer` | who owns confirming it |
| `confidence` | `low` · `medium` · `high` |
| `last_reviewed` | ISO date of the last review |
| `notes` | context, caveats, what would raise confidence |

A missing `source`, `status`, `confidence` (or any required field) is a validation error — no number may be undocumented.

## Sections

A release carries `metadata` plus 23 sections: `battery_capex`, `battery_opex`, `degradation`, `round_trip_efficiency`, `parasitic_load`, `warranty_cycle_limits`, `financing`, `dscr_covenants`, `p50_p90_haircuts`, `tariff_arbitrage`, `duos`, `mic_asc_headroom`, `capacity_market`, `local_flex_piclo`, `wholesale_p415`, `balancing_mechanism`, `ancillary_services`, `business_rates`, `insurance`, `metering_idno`, `dno_costs`, `quote_validity`, `scenario_weights`. A missing or mistyped section fails validation.

## Versioning & releases

A release is a single file, e.g. [`assumptions/gb_v0_1.yaml`](../assumptions/gb_v0_1.yaml), identified by `metadata.version` (`gb_v0_1`). GB-specific values are **content** in these files — never branches in the core. A new market or a material change is a **new versioned file**, never an in-place edit of a released one. **A quote is valid only against the assumption version that produced it** (see [provenance.md](provenance.md)); a material change beyond tolerance triggers a reprice, not a silent edit.

[`assumptions/gb_v1_0.yaml`](../assumptions/gb_v1_0.yaml) (`gb_v1_0`) is the **sourced successor** to `gb_v0_1` — a provenance pass (W4-3) that attaches a citable, display-ready `source` to every value, promotes the two values backed by an in-repo citation (`battery_capex.installed_cost`, `financing.target_equity_irr_pct`) to `needs_review`, and corrects an unsupported citation (`round_trip_efficiency`). It still **confirms nothing** (0 `confirmed`) and is not bankable; `gb_v0_1` stays frozen for provenance. The remaining gaps — each with the authoritative public source to consult — are ranked by impact in [`assumptions_to_source.md`](assumptions_to_source.md).

### Releases

| Version | Date | What it adds / changes | Confirmed |
|---------|------|------------------------|-----------|
| `gb_v0_1` | 2026-06-17 | Initial GB set — all placeholders. | 0 |
| `gb_v1_0` | 2026-06-24 | Sourced-provenance pass over `gb_v0_1` (W4-3). | 0 |
| `gb_v1_1`–`gb_v1_2` | 2026-07 | Additive shape/keys (C025 capex decomposition, etc.). | 0 |
| `gb_v1_3` | 2026-07 | C025 capex shape carried; search/caps re-based. | 0 |
| `gb_v1_4` | 2026-07-08 | CM de-rating corrected to NESO scaled-EFC (red-team D41). **App is pinned here.** | 0 |
| `gb_v1_5` | 2026-07-17 | Adds `local_flex_piclo.aggregator_cut_pct` (0.20 placeholder, issue 018). | 0 |
| `gb_v1_6` | 2026-07-20 | **Fetcher-backed** (Revenue-Data Drive slice 4): four revenue leaves re-sourced from LIVE keyless public fetches — CM clearing 30.0→27.1 £/kW/yr, CM de-rating →{1:0.1099, 2:0.2198, 4:0.4396}, DC/DM/DR 0→28296.9 £/MW/yr (gated/indicative), Piclo utilisation 150.0→135.67 £/MWh — each transcribed from a hashed provenance sidecar under `assumptions/provenance/gb_v1_6/`, never invented. Wholesale + BM stay 0 (source/notes only). Priors carried forward unchanged. | 0 |

[`assumptions/gb_v1_6.yaml`](../assumptions/gb_v1_6.yaml) (`gb_v1_6`) is the **fetcher-backed** successor to `gb_v1_5`: the perimeter fetchers `gridq.dev.fetch_neso_ckan` (NESO Data Portal CKAN) and `gridq.dev.fetch_piclo` (Piclo Data Hub) were run live to obtain real current values, and those numbers were transcribed by hand — each with `source` citing the fetched URL + `sha256(12)` + the fetched date + the committed provenance sidecar in [`assumptions/provenance/gb_v1_6/`](../assumptions/provenance/gb_v1_6/). It still **confirms nothing** (0 `confirmed`), the nonzero DC/DM/DR value remains gated/indicative (never FIRM), and the app stays pinned to `gb_v1_4` — so the demo golden is unchanged. `gb_v0_1`–`gb_v1_5` stay frozen for provenance.

## How assumptions are reviewed

- Each entry names an `owner_or_reviewer` and a `last_reviewed` date. Status moves along a lifecycle: **`placeholder` → `needs_review` → `confirmed`**.
- Reviews happen on cadence and whenever an entry is used in a quote that matters. Confirming an entry means: a real source is cited, the value is checked, `status` is set to `confirmed`, `confidence` is raised, and `last_reviewed` is updated.
- Changing a confirmed value bumps the library version (new file/tag), so existing quotes stay pinned to the version that produced them.

## How placeholders are treated

- `placeholder` / `needs_review` values are **usable** so the pipeline can run end-to-end — but they are **never presented as confirmed or bankable**. v0.1 (`gb_v0_1`) is entirely placeholders.
- Any quote built on placeholder assumptions must surface that (e.g. via warnings and the `bankable_status` of the run), and a baseline/placeholder run can never be marked a `bankable_candidate`.
- The gated-upside streams (`wholesale_p415`, `balancing_mechanism`, `ancillary_services`) are placeholder **zero** until the route-to-market partner's real numbers replace them.
- **A `placeholder` / `needs_review` key must never silently read as zero on the pricing path (D45, [`decisions.md`](decisions.md)).** "No number may be undocumented" now binds the *value* as well as the record: every key the model reads carries a value that is either evidenced or derived-and-declared. Where one genuinely cannot be sourced, the affected £ is **refused** — the consumer withholds the line and says so — never coerced to `0.0`. An absent value read as £0 is not "unknown": it is a confidently wrong number, and it enters P50, P90 and the DSCR indistinguishable from a measured one. The gated-upside zeros above are not this case: they are a *declared* £0 outside the priced floor, not an absent value.
- **A publicly published GB value left null is a defect, not an acceptable placeholder state.** A DNO CDCM tariff table, a NESO de-rating factor, an ENA settlement formula, an OBR index — transcribing one is **sourcing, not invention**, and the `gb_v1_6` fetcher-backed pass is the pattern for doing it with provenance. Only where the search genuinely fails does a key rest at `needs_review`, with that search recorded in `notes`. The open ones are a worklist ranked in [`assumptions_to_source.md`](assumptions_to_source.md), not a steady state.

## Why every quote shows the assumption version + input hash

So a number can be **reproduced and audited**: knowing the input hash + assumption version + code version fully pins the computation. Without the assumption version, a quote's numbers can't be tied to the values that produced them — and "bankable" requires exactly that traceability. See [provenance.md](provenance.md).
