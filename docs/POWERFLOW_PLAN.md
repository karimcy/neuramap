# OpportunityMap — Network-Aware Funnel: Execution Plan

**Goal:** evolve the funnel from *published-headroom screening* to *physics-verified,
hourly, network-aware deal underwriting* — per candidate node: measured load profiles,
independently-estimated headroom (DC power-flow N-0/N-1 thermal screen), and simulated
curtailment duty for a hypothetical flexible connection (→ battery sizing from first
principles, matching the Oxford-verified approach in the company overview).

**Status:** data-source survey COMPLETE (5 sweeps, every source live-verified) —
see `docs/data-sources.md` + `docs/sources/*`. Plan finalised against it.
Execution model: Opus 4.8 agents, one per work package, coordinated from this session.

**Findings that changed the plan:**
- JAO publishes operator PTDFs + element ratings + remaining margins hourly (Core: DE/FR/PL;
  Nordic: FI/SE/NO), open — Phase 2/4 gain a *calibration gate against TSO truth*.
- JAO Core Static Grid Model + ETYS App B = two real network models free; PyPSA-Eur OSM
  covers the rest (calibrated, ODbL noted).
- Portugal (E-Redes) is nodal out of the box → PT measured-profile pilot promoted to Phase 1
  alongside GB (and unblocked — no key needed).
- Spain's demand capacity maps are brand new with no history → snapshot archiver starts
  immediately (Phase 0.4) — the time dimension is a data moat.
- Nordics sub-zonal hourly load open via eSett/Elhub — no keys.

---

## Phase 0 — Data foundation (blocking)
- **0.1** Complete source inventory (`docs/data-sources.md`) — in progress.
- **0.2** Registrations & tokens (user action): UKPN + Northern Powergrid API keys
  (confirmed required); ENTSO-E Transparency token; others per inventory.
- **0.3** Raw-data lake layout: `pipelines/.cache/` per source, fetch scripts with
  resume + rate limits; provenance manifest (source, URL, edition, licence) per pull.
- **0.4** Snapshot archivers for perishable publications (ES REE/DSO monthly capacity
  files; Svk map; DGEG quarterly) — running from day one.

## Phase 1 — Measured node profiles: GB (blocked on keys) + PT (unblocked)
- **1.1** `pipelines/gb_profiles.py`: half-hourly circuit/substation series per prime
  GB node (UKPN 132kV circuit ops · NPg primary metering + connectivity).
- **1.2** LDC per node → peak, utilisation, min headroom, **empirically firmable added
  load at 2 h / 8 h battery** (deficit ≤8% of half-hours, no event > battery duration).
- **1.3** App integration: `data/gb_profiles.json` → popup "measured profile" block +
  funnel score upgrade (replace flat uplift with per-node LDC result where measured).
- **1.4** Validation: compare LDC-implied headroom vs DNO-published headroom per node;
  publish agreement stats (the credibility artefact for operator conversations).
- **1.5 (PT pilot, no keys):** E-Redes per-substation 15-min diagrams + published headroom
  → same LDC math → `data/pt_profiles.json`; proves the measured tier end-to-end today.

## Phase 2 — GB transmission DC power flow
- **2.1** Network model: ETYS Appendix B circuits (from/to, R/X/B, seasonal MVA) →
  bus/branch graph; snap our node coordinates to ETYS substations.
- **2.2** Injections: ENTSO-E per-unit generation (mapped to buses) + GSP-level demand
  profiles (Phase 1 + NESO/Elexon) + interconnector flows as boundary injections.
- **2.3** Solver: DC PF → PTDF/LODF; hourly flows for a sample year; validate against
  NESO boundary capabilities and constraint-limit datasets.
- **2.3b Calibration gate (new):** for Core+Nordic markets, reproduce JAO's published
  per-CNEC PTDFs with our engine on the JAO SGM before trusting any GB/continental output.
- **2.4** Products per node: physics headroom (N-0 and N-1), binding circuit,
  **hourly curtailment-duty series for +ΔP at node** → battery duty curve → exact
  BESS sizing + firming cost; wire into funnel as "network-verified" tier.

## Phase 3 — GB distribution AC checks (prime BSPs only)
- **3.1** LTDS circuit data (impedances/ratings) for DNOs covering prime nodes.
- **3.2** pandapower AC feeder models for the top ~30 pipeline candidates;
  voltage + thermal screen (DC assumptions break at distribution X/R).

## Phase 4 — Continental extension
- **4.1** 220 kV+ European model (OSM-derived: PyPSA-Eur class) for ES/DE/FR first;
  zonal injections from ENTSO-E disaggregated by unit registers (per inventory).
- **4.2** France: physics-estimated demand headroom where nothing is published —
  potentially the most differentiated dataset in the product.
- **4.3** Per-market uplift calibration from hourly zone data (replaces ×2.6/×3.4
  assumptions market by market).

## Phase 5 — Revenue leg (underwriting completeness)
- **5.1** Day-ahead + balancing price history per zone (sources per inventory) →
  arbitrage/stacking value per candidate node's market.
- **5.2** Deal economics in the funnel: firming capex + revenue stack → indicative
  IRR band per candidate; CSV export gains the economics columns.

## Verification gates (every phase)
- Cross-check outputs against operator-published figures where they exist; publish
  agreement stats. Physics screen ≠ connection study — label accordingly in-product.
- No silent data mutations: raw pulls cached immutably with provenance manifests.

## Execution model
- One Opus 4.8 agent per numbered work package, briefed from this file + the source
  inventory; artefacts land in `pipelines/` + `data/`; integration reviewed in-session
  before merging into the app.
