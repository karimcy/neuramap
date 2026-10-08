# JAO ingestion — verification report

**WP-JAO** · generated 2026-07-23 · scripts: `pipelines/jao_client.py`, `pipelines/jao_sgm.py`
Sample window fetched live from both publication tools: **2025-01-15 00:00–02:00 UTC**.

Purpose: this is the *calibration ground truth* for the DC power-flow engine
(POWERFLOW_PLAN §2.3b — reproduce JAO's published per-CNEC PTDFs on the JAO SGM
before trusting any continental power-flow output).

---

## 1. Flow-based API — CNEC counts per region (sample hour 2025-01-15T00:00Z)

| Region | Endpoint | CNEC records / hour | PTDF hub columns | RAM·Fmax·Imax·FRM populated |
|--------|----------|--------------------:|-----------------:|:---------------------------:|
| Core   | finalComputation | **12,774** | 24 | 100% (12,774/12,774) |
| Nordic | finalComputation | **904**    | 31 | 100% (904/904) |

Both endpoints verified live (HTTP 200) for both regions; `maxNetPos` also fetched
(1 row/hour, min+max net position per zone/hub).

### Core — CNEC records by TSO (sample hour)
ELIA 2990 · TENNETBV 2503 · AMPRION 2304 · MAVIR 1189 · PSE 896 · TENNETGMBH 732 ·
APG 566 · TRANSELECTRICA 554 · SEPS 180 · TRANSNETBW 188 · 50HERTZ 196 · CEPS 156 ·
RTE 136 · HOPS 106 · ELES 72 · (NA 6).
By element type: Line 8294 · TieLine 3652 · PST 578 · Transformer 226 · (null 24).

### Nordic — CNEC records by TSO (sample hour)
ENERGINET 323 · FINGRID 190 · STATNETT 156 · SVK 134 · (unnamed 101).
By CNE type: CNE 651 · PTC 184 · (null 69).

### PTDF hub columns present
- **Core (24):** AT, BE, CZ, DE, FR, HR, HU, NL, PL, RO, SI, SK, CH, plus virtual/
  interconnector hubs ALBE, ALDE (ALEGrO), DE_DK1_VH, DE_DK2_BigHub, DE_NO2_BigHub,
  DE_SE4_Baltic, NL_DK1_COBRA, NL_NO2_NorNed, PL_LT_BigHub, PL_SE4_SwePol, RO_BG_VH.
- **Nordic (31):** DK1, DK2, FI, NO1–NO5, SE1–SE4, plus interconnector/split-hub
  columns (DK1_SK, DK1_KS, DK1_SB, DK1_CO, DK1_DE, DK2_KO, DK2_SB, FI_FS, FI_EL,
  NO2_ND, NO2_NK, NO2_SK, SE3_FS, SE3_KS, SE3_SWL, SE4_BC, SE4_NB, SE4_SP, SE4_SWL).
- `maxNetPos` exposes the same hub set as `min<HUB>`/`max<HUB>` (Core) and
  `min_<HUB>`/`max_<HUB>` (Nordic) columns.

Every record carries the calibration-critical fields: per-hub PTDFs, `ram`, `fmax`,
`imax`, `frm`, `fref`, contingency list, `hubFrom/hubTo` (Core) or
`biddingZoneFrom/To` (Nordic), `u` (voltage kV), `tso`.

---

## 2. Core Static Grid Model — element counts

Release **9th (2026-03-01)**, `202603_Core Static Grid Model_External.xlsx`
(zip sha256 `e3becdb0…ba4f39`). Parsed → `data/networks/core_sgm.json`.

- **Buses:** 2,876 (unique `<substation>@<kV>` nodes)
- **Branches:** 3,491 — Lines 2,632 · Tie-lines 333 · Transformers 388 · **PSTs 138**

### Branches per TSO
| TSO | line | tieline | transformer | pst | total |
|-----|-----:|--------:|------------:|----:|------:|
| RTE | 917 | 19 | 235 | 0 | 1171 |
| AMPRION | 350 | 51 | 17 | 24 | 442 |
| TENNET (DE) | 260 | 51 | 38 | 10 | 359 |
| PSE | 222 | 15 | 0 | 38 | 275 |
| 50HERTZ | 142 | 19 | 22 | 10 | 193 |
| TransnetBW / TEL | 138 | 11 | 24 | 2 | 175 |
| ELIA | 130 | 16 | 12 | 7 | 165 |
| TransnetBW | 96 | 32 | 0 | 12 | 140 |
| APG | 73 | 25 | 0 | 24 | 122 |
| TENNET (NL) | 92 | 12 | 8 | 2 | 114 |
| CEPS | 64 | 16 | 4 | 3 | 87 |
| MAVIR | 56 | 20 | 3 | 0 | 79 |
| SEPS | 42 | 11 | 3 | 0 | 56 |
| ELES | 14 | 12 | 22 | 1 | 49 |
| HOPS | 22 | 18 | 0 | 5 | 45 |
| Creos | 14 | 5 | 0 | 0 | 19 |

(TEL = TransnetBW's Elia-border labelling in the workbook; both TransnetBW rows are
distinct TSO strings in the source.)

Each branch has R (Ω), X (Ω), B (µS), Imax (A), voltage (kV), TSO, name and EIC.
PST branches additionally carry `pst.{sym_asym, angle_reg_pct, theta_deg, taps,
phase_reg_pct}` for quadrature-booster modelling. Voltage levels span 380 kV (1630),
220 kV (1422), 400 kV (380) plus small counts at 750/410/400/240/231/230/123/115 kV.

---

## 3. Schema surprises & data-quality flags

1. **Nordic substation anonymisation.** Only 158/904 Nordic CNECs (~17%) expose a
   readable `substationFrom`; 160/904 `cneName` values are 32-char GUIDs (SVK &
   Statnett elements). Core CNECs are fully named (12,774/12,774). → For calibration,
   join Nordic CNECs on EIC + geometry, not names.
2. **Core vs Nordic schema differ.** Core uses `hubFrom/hubTo`, `elementType`,
   `minRamFactor/minRamTarget`, `presolved`, LTA fields. Nordic uses
   `biddingZoneFrom/To`, `cneType/cnecType`, `imaxMethod`, `nonRedundant`,
   `significant`, `aac`, `mrId`. Client handles both; downstream mappers must branch
   on region.
3. **PTDF columns include non-Core external hubs** (CH, and BA/RS/UA appear in Core
   `hubFrom/hubTo` for tie-lines) and virtual interconnector hubs — filter to the
   12 Core bidding zones when building the internal PTDF matrix.
4. **PST classification.** The SGM Transformers sheet mixes ordinary OLTC
   transformers and phase-shifters. 148 rows have an in-phase Phase-Regulation δu
   (voltage tapping) but only **138 are true PSTs** (Sym/Asym type set, or non-zero
   angle-regulation δu, or a Theta angle). 90 of those are named "TR … TR …" not
   "PST …" (Amprion/TenneT convention) — name-matching alone under-counts PSTs by 65%.
5. **SGM missing electrical params (minor):** R null 3/3491 · X null 3 · **B null
   250/3491** (many transformers report no susceptance) · Imax null 24 · voltage null
   2 · 2 branches reference an unnamed substation (from/to = null). All flagged, none
   dropped.
6. **Response size.** Core `finalComputation` ≈ 21 MB/hour — fetched & cached in
   hourly chunks. A full historical year ≈ 180 GB uncompressed; plan windowed pulls,
   not bulk.

---

## 4. Artefacts produced
| Path | What |
|------|------|
| `pipelines/jao_client.py` | Core+Nordic API client (throttle, retry, immutable cache, CLI: fetch/schema/sample) |
| `pipelines/jao_sgm.py` | SGM discover→download→parse→normalize, CLI |
| `pipelines/requirements.txt` | pinned deps (requests, openpyxl, pandas) |
| `pipelines/.cache/jao/` | raw API JSON (8 sample-window files) + `_manifest.json` + `PROVENANCE.md` |
| `pipelines/.cache/sgm/` | SGM release ZIP + extracted workbook |
| `data/networks/core_sgm.json` | normalized bus/branch graph (2,876 buses · 3,491 branches) |

All scripts re-run idempotently: cached windows/releases are served without
re-fetching; cached re-run of the client completes in ~0.5 s.
