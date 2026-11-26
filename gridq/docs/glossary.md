# Glossary

Terms used across the GridQ docs and code. GB electricity-market context.

| Term | Meaning |
|------|---------|
| **BESS** | Battery Energy Storage System — the asset GridQ sizes and underwrites. |
| **BTM** | Behind-the-Meter — sited on the customer's side of the supply meter (vs front-of-meter / grid-scale). GridQ is BTM. |
| **PPA** | Power Purchase Agreement — the customer contract for the energy/service; in v1 the default structure is fixed £/kWh (also savings-share, capacity fee). |
| **CM** | Capacity Market — GB mechanism paying assets for available, de-rated capacity. A *contracted* revenue stream; de-rated by duration, deliverability verified to the final contract year at degraded capability. |
| **Piclo** | Piclo Flex — a marketplace through which DNOs procure **local flexibility** services. A contracted stream, priceable when a tender is open in the site's DNO area. |
| **P415** | Elexon/Ofgem modification enabling a flexibility provider to access the **wholesale market** independently of the site's energy supplier. Gates the wholesale upside; placeholder until an RTM partner's terms are known. |
| **BM** | Balancing Mechanism — NESO's near-real-time mechanism to balance supply and demand. Part of the gated wholesale/BM upside (needs an RTM partner). |
| **DNO** | Distribution Network Operator — owns/operates the regional distribution grid; counterparty for connection, consents and local flexibility. |
| **MIC** | Maximum Import Capacity — the agreed maximum import at the connection (kVA). Battery charging counts against it. |
| **MEC** | Maximum Export Capacity — the agreed maximum export at the connection (kVA). |
| **ASC** | Available/Authorised Supply Capacity — the capacity reserved (and billed) at the connection; a binding constraint where it applies. |
| **G99** | Engineering Recommendation G99 — the standard for connecting generation/storage above the small-scale (G98) thresholds to the distribution network. An export-side consent. |
| **G100** | Engineering Recommendation G100 — export-limitation scheme that caps export to within the agreed limit. |
| **DSCR** | Debt Service Coverage Ratio — cash available for debt service ÷ debt service. The bankable constraint is **P90 DSCR ≥ covenant**; debt is sized/sculpted on the P90 case. |
| **P50** | The median (50th-percentile) estimate — equal chance of over/under. The central case. |
| **P90** | The conservative 90%-probability-of-exceedance estimate (the value met or beaten 90% of the time). Lenders underwrite on it; point estimates are not deliverables. |
| **COD** | Commercial Operation Date — when the asset starts operating; the point after which realised-vs-underwritten performance is tracked. |
| **RTM** | Route To Market — the aggregator/optimiser partner that provides access to wholesale/BM/ancillary markets. Its terms replace the front-of-meter placeholders. |
| **SoC** | State of Charge — the battery's current energy level (as a fraction of usable capacity). |
| **SoE** | State of Energy — the battery's stored energy in kWh (the dispatch solver's `soe_kwh` variable; SoC × usable capacity). |
| **SoH** | State of Health — the battery's degradation state over life; an ex-post model informs end-of-life capability. |
| **DUoS** | Distribution Use of System — charges for using the distribution network, with red/amber/green time bands; **red-band avoidance** is part of the bankable import-side floor (post-TCR treatment, no Triad story). |

> Related terms seen in the contracts: **G98** (small-scale connection recommendation below G99), **ANM** (Active Network Management / curtailment), **TCR** (Targeted Charging Review — reformed network charging), **IDNO** (Independent DNO), **EMS/M&V** (Energy Management System / Measurement & Verification), **MPAN** (Meter Point Administration Number).

> **`QuoteMode` vs `ManualQuoteType` (don't confuse them).** `QuoteMode` is the **lifecycle state** of a system `QuoteRecord` (`draft → indicative → underwritten → contracted → operating`, plus `lapsed`). `ManualQuoteType` is the **artifact type** of a hand-built *benchmark* quote (`indicative`/`underwritten`/`proposal`/`other`, in `telemetry/manual_benchmark.py`). They share the `indicative`/`underwritten` spellings but answer different questions — *where is this quote in its life?* vs *what kind of thing did the human produce?*
