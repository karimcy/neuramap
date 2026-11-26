# Revenue-Data Register

The data-source ledger for every GridQ revenue stream: what real data backs it, where it comes from, the equation that turns a raw source into the stream's £ input, and the intake consent it needs. Ground truth for the Revenue-Data Drive (`docs/goal/revenue-data-drive-prompt.md`).

**Legend — leaf status:** `sourced` = real, licensed, reachable source found (sample fetched); `blocked` = exists but access needs a browser/paid sub/commercial contract; `dead` = no lawful public path. **Disposition:** `data_backed_consentable` = real data + customer can consent at intake; `partial` = public data indicative-grade, per-asset value needs a commercial aggregator; `not_realistic` = evidenced dead end.

Research verified live 2026-07-18/19 (workflow `wf_8d6c54bb-236`, 10 research + 10 adversarial-verify agents). **Result: all 10 streams `data_backed_consentable` — zero not-realistic verdicts.** Adversarial re-fetch confirmed 9/10 primary sources live; the 1 "refuted" was only a *reference link* for LOAD_SERVICE (an Elexon knowledgebase page that 403s to bots) — the stream's real data is the customer's own meter under LOA, so the stream stands. Sample-fetch dates in the leaf lines.

> Not-bankable stays code-enforced: sourced numbers land as `needs_review`/`confirmed` in a versioned `gb_v1_x` release; nothing here relaxes that. The estimation ladder — real contracted > site document (invoice/agreement) > sourced public rate > cited estimation model > placeholder — is recorded per stream.

---

## Wiring status — code (Revenue-Data Drive slices 2a/2b)

How each stream reacts to per-stream customer consent (`SiteModel.stream_consents`) + its agreed rate, in the engine today. **2a** wired consent into the gate (authorisation → CONTRACTED, never FIRM; the "consent given" label). **2b** wired consent + agreed rate into the £ and the placement (a consented + priced CONTRACTED stream is moved into `value_streams` so finance underwrites it with the contracted haircut + contracted-basis DSCR). Volumes are always engine-measured; a consented stream without its needed rate stays £0 with the label.

| Stream | Rate home (agreed) | Volume basis | Priced-on-consent (2b) |
|--------|--------------------|--------------|------------------------|
| TARIFF_ARBITRAGE / DUOS / MIC_ASC / PPA | n/a (firm floor from the site tariff) | site-derived schedule | already firm floor — unchanged |
| CAPACITY_MARKET | `stream_consents[].agreed_rate_gbp` (£/kW/yr) | de-rated deliverable MW | agreed £/kW/yr ahead of the assumption clearing price; **deliverability physics still gate the floor** |
| LOCAL_FLEX_PICLO | `ExportArrangement.local_flex_agreed_rate_gbp_per_mwh` (£/MWh) | tender-window delivered MWh | agreed £/MWh ahead of the assumption; **now nets the aggregator cut** (engine-path bug fixed) |
| LOAD_SERVICE | customer-declared counterfactual (£/yr) | measured forced delivery | unchanged (declared counterfactual only; contract reference gates the floor) |
| WHOLESALE_P415 | `ExportArrangement.export_tariff_gbp_per_kwh` (£/kWh) | overlay export MWh (engine-measured) | contracted mode: agreed tariff × overlay MWh → CONTRACTED floor; **MID perfect-foresight delta stays SEPARATE indicative gated upside** |
| BALANCING_MECHANISM | `stream_consents[].agreed_rate_gbp` (£/MW/yr) | de-rated deliverable MW | agreed £/MW/yr × de-rated MW when consented WITH a rate; else £0 CONDITIONAL (label kept) |
| ANCILLARY_DC_DM_DR | `stream_consents[].agreed_rate_gbp` (£/MW/yr) | de-rated deliverable MW | agreed £/MW/yr × de-rated MW when consented WITH a rate; else £0 CONDITIONAL (label kept) |

Consented gated streams (WHOLESALE/BM/ANCILLARY) are never FIRM; they price CONTRACTED and, when priced, move into `value_streams` — a deliberate, consent-gated relaxation of the two teeth (`DispatchResult` leak, conformance `firmness.gated_excluded_from_priced_floor`) that previously assumed gated streams live only in `gated_upside`.

---

## Fetcher-backed leaves — code (Revenue-Data Drive slice 4)

Three perimeter fetchers (off the pricing path, `gridq.dev`, mirroring the 018 `fetch_prices` pattern: a single monkeypatched network seam, pure deterministic transforms, a committed provenance sidecar, never fabricates) now back the library-default figures for four revenue leaves. They are run **by hand** and print a suggested YAML block; the numbers are transcribed into a versioned release (`gb_v1_6`), each `needs_review`, each citing URL + sha256(12) + fetched-date + the sidecar path under `assumptions/provenance/gb_v1_6/`. Nothing here relaxes the not-bankable posture (0 confirmed repo-wide); the app stays pinned to `gb_v1_4`, so the golden is untouched.

| Leaf (assumption key) | Fetcher | Source (keyless, public) | gb_v1_6 value (fetched 2026-07-20) |
|-----------------------|---------|--------------------------|-------------------------------------|
| `capacity_market.clearing_price_gbp_per_kw_yr` | `fetch_neso_ckan --resource cm-clearing` | NESO CKAN `b1b58919-…` | 27.1 £/kW/yr (latest T-4 = DY 2029/30; T-1 = 5.0 in notes) |
| `capacity_market.de_rating_factor_by_duration_h` | `fetch_neso_ckan --resource cm-derating` | NESO CKAN `d94ff98d-…` | {1: 0.1099, 2: 0.2198, 4: 0.4396} (primary source; replaces the modoenergy secondary citation) |
| `ancillary_services.dc_dm_dr_value_gbp_per_mw_yr` | `fetch_neso_ckan --resource eac-results` | NESO CKAN `596f29ac-…` (EAC Response/Reserve summary) | 28296.9 £/MW/yr (mean 3.2302 £/MW/h across DC/DM/DR × 8760; **gated/indicative, never FIRM**) |
| `local_flex_piclo.utilisation_price_gbp_per_mwh` | `fetch_piclo --dataset competitions` | Piclo Data Hub S3 `piclo_flex_uk_competitions.xlsx` | 135.67 £/MWh (GB-wide median of 224,535 utilisation guide prices) |
| `wholesale_p415.gated_upside_value_gbp_per_mw_yr` | (Elexon MID, `fetch_prices`, for context) | Elexon Insights MID | **stays 0** — the real £ is engine-measured (MID overlay × consented export tariff, §7), never a library constant |
| `balancing_mechanism.gated_upside_value_gbp_per_mw_yr` | — (documented-manual, §9) | needs paid Modo index / full BOALF×BOD | **stays 0** — note-only |

`fetch_piclo --dataset bids|lcm` download+hash+row-count only (no library figure landed). Availability guide prices in the Piclo competitions dataset were all null on 2026-07-20 (a reported gap, not sourced).

---

## 1. TARIFF_ARBITRAGE — `data_backed_consentable` (firm floor)
Firm-floor rate = the customer's own invoice (collected under LOA). When no invoice exists, a **cited estimation model** composes a delivered p/kWh from live wholesale + published non-commodity adders (an estimate, never the contracted rate, never bankable).

**Data leaves**
- HH wholesale (Elexon MID) — open API, no key, free, half-hourly. `sourced` (fetched 2026-07-18: period price 120.08 £/MWh). `https://data.elexon.co.uk/bmrs/api/v1/balancing/pricing/market-index?dataProviders=APXMIDP`
- DUoS unit rates + R/A/G windows (per DNO SLC14 statement) — open PDF + Excel annex, free, annual (1 Apr). `sourced` (SSEN SEPD 2026/27: Red 16:30–19:30, LV Red 14.017 / Amber 1.866 / Green 0.095 p/kWh). `https://www.ssen.co.uk/globalassets/library/charging-statements-sepd/202627/sepd-duos-charges-2026-27_v1.1.pdf`
- TNUoS demand tariffs 2026/27 — NESO, open, annual. `sourced`. `https://www.neso.energy/document/376336/download`
- BSUoS fixed tariff — NESO, open. `sourced` (FT7 £13.74, FT8 £12.49 /MWh). `https://www.neso.energy/document/375861/download`
- CCL main rate electricity — gov.uk OGL, annual. `sourced` (£0.00801/kWh from 1 Apr 2026). `https://www.gov.uk/guidance/climate-change-levy-rates`
- RO buy-out + obligation — Ofgem, annual. `sourced` (£69.34/ROC × 0.472 ⇒ ~£32.7/MWh). `https://www.ofgem.gov.uk/data/renewables-obligation-buy-out-price-and-mutualisation-threshold-and-ceilings-2026-2027`
- CfD ILR — LCCC/EMRS, quarterly. `sourced` (Q3 2026 £4.488/MWh). `https://www.emrsettlement.co.uk/`
- FiT levelisation (Year 17) — Ofgem, quarterly (derived £/MWh). `sourced`. `https://www.ofgem.gov.uk/data/feed-tariff-levelisation-schedule-year-17`
- Line Loss Factors — Elexon BSC, annual (site LLFC from MPAN). `sourced`. `https://www.elexon.co.uk/bsc/operational/losses/`
- Ofgem default-cap Annex models (regulator-grade composition) — open XLSX, quarterly. `sourced`. `.../energy-price-cap-default-tariff-levels`
- Site actual unit rates — customer invoice + LOA (the firm-floor source). `sourced` (intake).

**Equations (all `realistic=True`)**
- Delivered estimate: `retail_est(t) = MID(t)/10·LLF + BSUoS/10 + DUoS_unit(band(t)) + TNUoS_resid/10 + RO/10 + CfD_ILR/10 + FiT/10 + CM/10 + CCL·100 + margin` p/kWh. Cite: Ofgem cap Annex methodology.
- RO cost: `0.472 × 69.34 = £32.73/MWh`.
- Arbitrage value (already the MILP objective): `Σ_t [load_avoided(t) − charge(t)] × retail_rate(t)` s.t. RTE/SoC/import limits.

**Consent inputs:** import MPAN; LOA to supplier (HH data + contract); invoice upload; acknowledgement that no-invoice pricing is an estimate; BTM dispatch-control consent; agreed import capacity (kVA).

---

## 2. DUOS_RED_BAND_AVOIDANCE — `data_backed_consentable` (firm floor)
All 14 GB DNO areas publish SLC14 statements (R/A/G windows + p/kWh + p/kVA/day) free, annually, as PDF + a standardised "Schedule of Charges" Excel annex.

**Data leaves** — `sourced` (fetched 2026-07-18): SSEN SEPD, UKPN EPN/LPN/SPN, NGED (4 areas), Northern Powergrid (NE+Yorks), ENWL (XLSX). `blocked` (browser-only): SP Energy Networks (SPD+SPM); DCUSA Schedule 16 CDCM (403 to automated fetch, downloadable in-browser).
**Equation:** `Annual DUoS = Σ_band(unit_rate·net_import_kWh_band) + capacity_rate·agreed_kVA·days + exceeded_rate·max(0,measured−agreed)·days`. Cite: SSEN "How are DUoS charges calculated".
**Consent inputs:** LOA (HH data); import MPAN (⇒ DNO area + LLFC/tariff row); DUoS pass-through confirmation; agreed capacity/ASC (kVA); voltage level; G98/G99 BESS connection consent.

---

## 3. MIC_ASC_HEADROOM — `data_backed_consentable` (firm floor)
Honest caveat: **no public source for an individual demand site's MIC/ASC** (Embedded Capacity Registers cover only ≥1MW gen/storage) — the capacity number is a customer document verified with the DNO under LOA. The *charge rates* that price it are public.

**Data leaves:** site MIC/MEC/ASC — customer connection agreement / DNO letter / invoice availability line (`sourced` via intake; no public API, confirmed across UKPN/ENWL/NGED/SSEN). Capacity + exceeded-capacity rates (p/kVA/day) — DNO LC14, open PDF (`sourced`). ECR (≥1MW only) — Opendatasoft API, no key (`sourced`). DCUSA Sch.16 CDCM — `blocked` (403). SSEN LOA templates — `sourced`.
**Equations (`realistic=True`):** breach test `exceeded_kVA = max(2·√(AI²+max(RI,RE)²) − MIC, 0)` per HH; headroom `Headroom_kVA(t) = MIC − 2·√(AI²+max(RI,RE)²)`; MEC/export variant analogous. Cite: UKPN LPN LC14.
**Consent inputs:** import (+export) MPAN; connection agreement / capacity letter / invoice line (mandatory — not public); declared MIC/MEC (kVA) + voltage; signed LOA (<12mo) to obtain/verify with DNO; consent to obtain HH active + reactive (kVArh) data (or a signed power-factor assumption); multi-MPAN aggregation confirmation.

---

## 4. PPA_OR_BILL_SAVINGS — `data_backed_consentable` (firm floor)
Almost purely derived from the TARIFF_ARBITRAGE stack + the customer's HH load; new leaves are a public counterfactual benchmark and the legal basis for a licence-exempt BTM PPA.
**Data leaves (`sourced`):** DESNZ non-domestic price benchmark (gov.uk, OGL, quarterly); Class C on-site supply exemption (legislation.gov.uk SI 2001/3270 Sch.4); DUoS/TNUoS/CCL as in §1. `blocked`: Cornwall Insight market-PPA benchmark (paid).
**Equations (`realistic=True`):** bill-savings identity `AnnualSaving = Σ_hh (E_base − E_withBESS)·p_delivered(hh)`, fixed/residual charges excluded; BTM PPA `revenue = Σ E_delivered·P_PPA(agreed)` or savings-share `= share%·AnnualSaving`.
**Consent inputs:** LOA to supplier; import MPAN + contract; **commercial-structure consent (bill-savings vs BTM PPA vs savings-share) with agreed PPA £/kWh + term + indexation + share%**; PPA counterparty + Class C exemption confirmation; G99 BESS consent; acknowledgement fixed charges aren't reduced.

---

## 5. CAPACITY_MARKET — `data_backed_consentable` (conditional floor)
De-rating factors + all clearing prices are live, free, machine-readable on the NESO Data Portal CKAN API (NESO Open Data Licence). A single BTM battery is below the 1MW CMU threshold ⇒ consent to **aggregation** is the key intake fact.
**Data leaves (`sourced`, fetched 2026-07-18):** storage de-rating factors by duration (0.5–12h) — CKAN API no key (`.../datastore_search_sql?...d94ff98d-39ba-40d6-9672-7433de0631ac`); T-1/T-4 clearing prices history — CKAN (`resource_id=b1b58919-...`); forward auction parameters — gov.uk; CM Rules 2025 (374pp) — gov.uk PDF; live CM register — CKAN.
**Equations (`realistic=True`):** `DeRatedCapacity_MW = ConnectionCapacity_MW × DeRatingFactor(duration class)`; `Revenue = DeRatedCapacity_kW × ClearingPrice_£/kW/yr` (CPI-indexed). Cite: CM Rules 2025 + DESNZ auction parameters.
**Consent inputs:** aggregation consent + agreed aggregator fee %; LOA to prequalify with EMR DB; legal-entity + Companies House + director sign-off; declared Connection Capacity (MW) + energy (MWh) ⇒ storage duration (deliverable at end-of-life to final delivery year); metering consent (import/export MPAN, HH data, CM metering); G99 connection agreement; acceptance of CM obligations + penalty exposure; no-double-subsidy declaration; consent to public CM register.

---

## 6. LOAD_SERVICE — `data_backed_consentable` (conditional floor)
Primarily derived from the site meter + a declared, evidenced counterfactual (no public source can originate the value — DNO quotes are bilateral; post-Access-SCR demand reinforcement is socialised below £1,720/kVA). Public benchmarks sanity-cap the declaration.
**Data leaves (`sourced`):** site HH + connection agreement (LOA, per-site private); declared counterfactual + evidence (customer upload); VoLL benchmarks (Ofgem London Economics PDF; ENWL SME VoLL); DNO outage CI/CML (Ofgem ED report); Access SCR charging rules (Ofgem PDF); non-domestic price + white-diesel benchmarks (gov.uk).
**Equations (`realistic=True`):** `V_load_service = min(C_declared, V_deliverable)`, `V_deliverable = Σ_windows E_served·v_unit`; genset cross-check `C_genset = E·p_diesel·f(l/kWh)+O&M+capex_annuity`; avoided-reinforcement rule = 0 unless >£1,720/kVA threshold.
**Consent inputs:** LOA (HH import data); import MPAN + connection agreement (authorised capacity, constrained windows); **declared counterfactual basis + £/yr + signed declaration**; evidence upload (DNO offer / genset spec / production-loss statement); obligation-window + M&V telemetry consent; (if backup/island) G98/G99 + DNO islanding agreement + ESQCR Reg 21 acknowledgement.

---

## 7. WHOLESALE_P415 — `data_backed_consentable` (gated upside; never in the firm floor)
Values *exported* energy at wholesale, gated on export consent + a route-to-market; priced as **indicative upside only** (no regulatory/peer-reviewed formula converts a forward wholesale curve into a guaranteed BTM export revenue — so it stays gated, never FIRM).
**Data leaves (`sourced`, verify CONFIRMED by re-fetch 2026-07-19):** MID (£/MWh + volume per SP, per provider APXMIDP/N2EXMIDP) — Elexon Insights REST, no key (`https://data.elexon.co.uk/bmrs/api/v1/datasets/MID`); imbalance/system prices (SSP/SBP, NIV) — Elexon, no key; MID/BMRS API docs — "all APIs public, no key". `blocked` (browser/manual): G99/G100 forms (ENA), P415/VLP status page (Cloudflare).
**Equations (`realistic=True`):** Market Price per BSC Section T4.3A (volume-weighted MIDP, ILT 25 MWh); £/MWh→£/kWh = /1000; indicative upside `= Σ_sp export_kWh_sp·(MID_sp/1000)·(1−RTM_fee)` — labelled INDICATIVE, no formula guarantees it.
**Consent inputs:** G99 status + acceptance ref; DNO connection agreement + agreed MEC (kW); export MPAN; G100 limitation scheme (if MEC < inverter); RTM counterparty + route (supplier export tariff or VLP/P415) + Secondary-BMU registration consent; **agreed export price (fixed £/kWh OR revenue-share/aggregator fee %)**; consent to share HH export/import data with Elexon + RTM; merchant charge/discharge operating-envelope authorization.

---

## 8. LOCAL_FLEX_PICLO — `data_backed_consentable` (conditional; README "no open API" was WRONG)
**Correction:** the Piclo Data Hub (`data.piclo.com`) publishes a **public S3 bucket, no auth** with exactly the tender/price data the README said didn't exist machine-readably. Priced on a confirmed tender window, nets an aggregator cut.
**Data leaves (`sourced`, verify CONFIRMED by re-fetch 2026-07-19 — 40.3MB HTTP 200, no auth):** Piclo UK Competitions (windows, zones, availability £/MW/h + utilisation £/MWh guide prices, min asset size) — S3 XLSX; Piclo Confirmed Bids (669k rows, accepted prices per DSO, refreshed 18/07/2026) — S3 XLSX; NESO Local Constraint Market results (day-ahead £/MWh) — S3 CSV; per-DNO competition boundary polygons (site-in-zone eligibility) — S3 GeoJSON; NGED flexibility trades + ceiling prices per CMZ — CKAN API no auth. `blocked` (login/key): UKPN Opendatasoft flexibility dataset; Piclo FSP bidding APIs (business-approved credentials only).
**Equations (`realistic=True`):** `Revenue = Σ_windows AvailabilityPrice(£/MW/h)·ContractedMW·hours + Σ_dispatch UtilisationPrice(£/MWh)·DeliveredMWh`; `E[UtilisationRevenue] = UtilisationPrice·ContractedMW·Σ window_hours·dispatch_probability`. Cite: ENA standard flexibility product.
**Consent inputs:** site postcode + DNO region (⇒ zone membership via boundary GeoJSON); import (+export) MPAN for FSP registration; LOA (HH data); route election (direct Piclo FSP vs nominated aggregator + bid consent); **agreed aggregator cut %**; **agreed availability £/MW/h + utilisation £/MWh** (or delegated bidding); telemetry-sharing consent; committed-MW + stacking-conflict declaration (no double-sell); min-asset-size + response-time confirmation.

---

## 9. BALANCING_MECHANISM — `partial` (gated upside, indicative-backed)
Public GB BM data is real, free, licensed, reachable (Elexon Insights, keyless). Per-asset *contracted* BM revenue is commercial (aggregator/VLP) — so public data gives an **indicative** value, not a firm number.
**Data leaves (`sourced`, fetched 2026-07-18):** BOALF acceptances, BOD prices, DISEBSP system prices, DISBSAD — all open API no key. Modo GB BESS benchmark — methodology free, full index paid. `blocked`: BMRS licence page (browser-only); per-asset contracted terms (commercial).
**Equations (`realistic=True`):** settlement `cashflow = Σ QAB/QAO × (bid/offer price)` (BSC Section T); indicative `= R_fleet[£/MW/yr from public BOALF×BOD] × P_asset × (1−cut)` — labelled INDICATIVE; skip-rate sensitivity ≈ −£12k/MW/yr per +10pp.
**Consent inputs:** VLP/aggregator appointment (Secondary BMU, P344); agreed cut %; MSID/MPAN association; P375 asset-metering consent; LOA (HH data); G99/G100 + export MPAN (if export actions); ≥1MW aggregation confirmation; telemetry-sharing consent.

---

## 10. ANCILLARY_DC_DM_DR — `data_backed_consentable` (gated upside, indicative-backed)
NESO publishes every DC/DM/DR auction clearing price (£/MW/h) + cleared volume on the Data Portal CKAN API (NESO Open Data Licence), keyless — full history (35,640 records) + today's day-ahead auction both fetched live 2026-07-18.
**Data leaves (`sourced`):** EAC results history + daily results (CKAN API + CSV); service technical requirements (NESO PDF); participation/aggregation rules (NESO PDF).
**Equations (`realistic=True`):** `AvailabilityPayment = Σ_periods P_ije·V_ije·F_ije·h`; contractable cap `MW = min(P_headroom, E_usable/D)` with D = 0.25h (DC)/0.5h (DM)/1.0h (DR); indicative net `= (1−cut)·Contractable_MW·mean(clearing price per product/EFA block)·hours`.
**Consent inputs:** aggregator authorization (>1MW EAC unit on NESO SMP) + agreed cut %; per-second metering + data-concentrator consent; import (+export for HF products) MPAN + G99; declared usable energy/power/SoC band; LOA for NESO onboarding/testing; acknowledgement it's merchant/gated upside (prices can be 0/negative).

---

## Cross-cutting findings
- **8/10 streams are backable with real, licensed, keyless GB public data or a customer document** (fetched-verified). Only per-asset *contracted* BM/ancillary/flex value needs a commercial aggregator — public data gives an honest **indicative** figure, which the intake consents to via an aggregator appointment + agreed cut %.
- **Recurring free sources:** Elexon Insights API (MID, BOALF, BOD, DISEBSP, LLF), NESO Data Portal CKAN (CM de-rating + prices, DC/DM/DR auctions, TNUoS/BSUoS), DNO SLC14 statements (DUoS), Ofgem/gov.uk (CCL, RO, cap models, VoLL, benchmarks). All Open-Government / open-data licensed.
- **Genuinely non-public (customer document only):** site MIC/MEC/ASC, the supply invoice rates, the load-service counterfactual — all collected at intake under LOA. None are dead ends.
- **`blocked` (browser-only, downloadable manually, not a dead end):** SP Energy Networks statements, DCUSA Schedule 16 CDCM, Cornwall Insight PPA benchmark (paid), Modo full index (paid).
