# Spain nodal access-capacity snapshot — summary

*Snapshot archived 2026-07-23 · generated 2026-07-22T23:09:41Z · WP-IBERIA / Phase 0.4*

Spain publishes these files at fixed URLs and overwrites them monthly with no public history. This archiver captures each edition immutably so month-over-month drift is measurable — the time dimension no competitor holds.

## Archived this snapshot

| Operator | Kind | Sub | Edition | Bytes | sha256 (12) |
|---|---|---|---|--:|---|
| REE | demand | — | 2026-07-01 | 227,541 | `5c373dfd9357` |
| REE | generation | — | 2026-07-01 | 300,657 | `b7556862ccaf` |
| e-Distribucion | demand | R1026 | 2026-07-03 | 942 | `979ffa6532fc` |
| e-Distribucion | demand | R1299 | 2026-07-03 | 233,178 | `240816b9096c` |
| e-Distribucion | generation | R1026 | 2026-07-03 | 1,047 | `81b373c999d1` |
| e-Distribucion | generation | R1299 | 2026-07-03 | 273,688 | `e23329b2eb76` |

## REE transmission — DEMAND access capacity

Edition **2026-07-01** · source: https://www.ree.es/es/clientes/consumidor/acceso-conexion/conoce-la-capacidad-de-acceso

- Nodes with a demand-capacity value: **624** (of which **362** saturated, 0 MW available)
- Total AVAILABLE (disponible, criterio general CEP/CH): **51,481 MW**
- Total GRANTED / occupied (otorgada demanda RdT): **11,076 MW**
- Total PENDING (en curso, demanda RdT): **15,539 MW**
- Total access margin (RdT demanda): 58,102 MW
- Nodes with ≥100 MW available: **145** totalling 47,395 MW

**Drift vs app (FREE.ES `es_transport`)**

| Metric | App (data.js, ed. 2026-06-03) | Fresh snapshot (ed. 2026-07-01) | Δ |
|---|--:|--:|--:|
| Nodes (app filter ≥100 MW located) | 176 | 145 (≥100 MW) | -31 |
| Available MW | 33,256 | 47,395 (≥100 MW) | +14,139 |

> The app figure is a filtered (≥100 MW, geocoded), single-edition subset; the snapshot is the full fresh file. Compare like-for-like on the ≥100 MW row; the node-count drop is real saturation between editions.

## REE transmission — GENERATION access capacity

Edition **2026-07-01** · source: https://www.ree.es/es/clientes/generador/acceso-conexion/conoce-la-capacidad-de-acceso

- Nodes with a generation-capacity value: **937** (224 with 0 MGES margin)
- AVAILABLE (MGES renewable total access margin): **336,394 MW**
- GRANTED / occupied (otorgada GEN): **205,784 MW**
- PENDING (en curso GEN): **32,999 MW**
- Static generation nodal capacity (sum): 946,291 MW
- Nodes with ≥5 MW MGES margin: **699** totalling 336,351 MW

**Drift vs app (FREE.ES `es_ree_gen`)**

| Metric | App (data.js) | Fresh snapshot (ed. 2026-07-01) |
|---|--:|--:|
| Nodes | 549 | 937 |
| Available MW (MGES margin) | 263,059 | 336,394 |

> App `es_ree_gen.total_mw` was the MGES access-margin proxy for a prior edition + ≥5 MW/geocoding filter; not reconcilable 1:1 with the full fresh sum.

## e-Distribucion (DSO) — demand + generation

### Demand — edition 2026-07-03 (codes R1026, R1299)

- Nodes: **1855** across 2 DSO file(s)
- AVAILABLE firm capacity: **1,426 MW**
- OCCUPIED: **33,894 MW**
- Admitted-pending / not-evaluated: 782 MW
  - R1026: 5 nodes · avail 0 · occ 28 MW
  - R1299: 1850 nodes · avail 1,426 · occ 33,866 MW

### Generation — edition 2026-07-03 (codes R1026, R1299)

- Nodes: **1855** across 2 DSO file(s)
- AVAILABLE firm capacity: **22,106 MW**
- OCCUPIED: **28,196 MW**
- Admitted-pending / not-evaluated: 766 MW
  - R1026: 5 nodes · avail 23 · occ 34 MW
  - R1299: 1850 nodes · avail 22,083 · occ 28,161 MW

## Data-quality / units notes

- All figures are MW as published (REE/e-Distribucion report MW directly; no unit conversion applied). Spanish-locale decimals (comma) parsed.
- REE demand "available" = *Capacidad de acceso disponible para solicitudes criterio general* (DEMANDA / CEP / CH) — the column the app maps to node `mw`; verified against CABRA 400 (827 MW).
- REE generation "available" = MGES (renewable) *Margen de capacidad de acceso total*; generation capacity is split across MGES/MPE/storage buckets that are not additive per node — MGES is reported as the headline renewable-hosting proxy.
- "Saturated" = 0 MW available at that node under the headline criterion.
- Raw files archived immutably under `pipelines/.cache/es_snapshots/2026-07-23/` (see manifest.json for URL + sha256 + edition per file).
