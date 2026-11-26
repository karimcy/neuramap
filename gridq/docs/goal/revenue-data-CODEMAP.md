# Revenue-Data Drive — code ground-truth map (Loop 01, 2026-07-18)

Condensed from the Explore map. File:line anchors for building. Streams enum: `src/gridq/schemas/enums.py:20-32` (MarketStream); Firmness `:35-41` (FIRM/CONTRACTED/CONDITIONAL/EXCLUDED). Gated set `src/gridq/schemas/market.py:12-18` = {WHOLESALE_P415, BALANCING_MECHANISM, ANCILLARY_DC_DM_DR}. App pins `gb_v1_4` (`app.py:47`); newest release `assumptions/gb_v1_5.yaml`.

## Never-FIRM enforcement (4 layers — the prompt authorises overwriting these where consent+rate is given)
1. `ValueStreamResult._gated_cannot_be_firm` — `schemas/market.py:33-40`.
2. `GateResult._consistency` — `value_stack/interface.py:132-158` (gated can't be FIRM :151; CONDITIONAL needs unlock_condition :154).
3. `GatedUpsideStream` — `value_stack/streams.py:297-344` (refuses OPEN gate :335).
4. `build_gate_result` status→firmness — `value_stack/gates.py:166-203`.

## Per-stream (priced_by · gate · firmness · £0-today · wiring gap)
- **TARIFF_ARBITRAGE** — engine `valuation.py:274-280` (real £ = site tariff × HH load); gate `gates.py:31-40` (≥2 rates)+load. Gap: wizard uses reference-tariff defaults (`app.py:469-501`) → needs confirmed customer rates + consent flag on the row.
- **DUOS_RED_BAND_AVOIDANCE** — engine `valuation.py:281-286`; real on MILP. Gap: real per-DNO band rates at intake + consent flag.
- **MIC_ASC_HEADROOM** — engine £0 (double-count guard `valuation.py:287-291`); proxy £12k placeholder. Gap: capacity-charge/ASC-reduction model (`tariff.capacity_charge_gbp_per_kva_day` exists) + consent field.
- **PPA_OR_BILL_SAVINGS** — always £0 by design (itemised elsewhere); `gb_streams.py:241-263`. Gap: PPA-structure consent + agreed £/kWh surfaced to proposal.
- **CAPACITY_MARKET** — priced `valuation.py:75-123`; keys `capacity_market.clearing_price_gbp_per_kw_yr` (30, needs_review), `de_rating_factor_by_duration_h`. Gate `context.deliverability_confirmed`. Firmness CONTRACTED-when-confirmed else CONDITIONAL. Gap: intake path to declare CM contract reference (no form field) so gate opens.
- **LOCAL_FLEX_PICLO** — `gb_streams.py:633-679` nets cut; engine `_local_flex_value valuation.py:126-152` **ignores cut**. Keys `local_flex_piclo.utilisation_price_gbp_per_mwh` (150), `aggregator_cut_pct` (gb_v1_5). Gate `dno_region` + `context.local_flex_tender_open`. Gap: intake source for tender_open + tender_window_discharge_mwh; engine must net cut; `local_flex_agreed_rate_gbp_per_mwh` (on real form) never consumed.
- **LOAD_SERVICE** — `valuation.py:155-195`; customer-declared £ only. Gap: NO form collects provisioning requirements (JSON-only) → needs form fields + contract ref + declared £/yr.
- **WHOLESALE_P415** (gated) — proxy £0 `gb_streams.py:834-852`; engine overlay `export_upside_stream_result valuation.py:198-243` + `MilpEngine._export_upside engine.py:424-495`. Gate: RTM confirmed assumption (`gates.py:119-132`) + `has_export_consent` (`gates.py:91-96`: MEC>0 ∧ G99 ∧ G100) + overlay `export_dispatch_enabled` (`gates.py:99-111` adds export_mpan). Gap: overlay needs a wholesale price series (`MarketForecast`) that no intake supplies (`engine.py:443`); `export_tariff_gbp_per_kwh` never used; RTM assumption 0/needs_review.
- **BALANCING_MECHANISM** (gated) — £0. Gate RTM + `meets_portfolio_threshold(portfolio_mw, portfolio_threshold_mw)`. Gap: no producer sets portfolio_* in GateContext; no consent field.
- **ANCILLARY_DC_DM_DR** (gated) — £0. Same as BM + decay. Gap: same.

## Consent plumbing (fields EXIST from 018 — the missing piece is per-stream consent)
- `GateContext` — `value_stack/interface.py:161-205`: deliverability_confirmed, local_flex_tender_open, portfolio_mw, portfolio_threshold_mw, evidence. **No consent fields.** Producer `sizing/deliverability.py:185-269` → called `optimisation/milp_solver.py:1194-1203`; unsolved path `context=None`.
- Consents read directly off SiteModel: `gates.has_export_consent` reads `site.consents.g99/g100`+`connection.mec_kw` (`gates.py:85-96`); `export_dispatch_enabled` adds `consents.export_mpan` (:99-111).
- `ConsentState` — `schemas/consents.py:9-18`: g98,g99,g100, export_mpan, dno_application_state, supplier_consent_state, notes.
- `ConnectionDetails` — `schemas/connection.py:16-33`: mic_kw,mec_kw,asc_kw, voltage_level, dno_region, headrooms. Into SiteModel `site.py:69`; consents `:70`; SupplyBoundary `:77` (`schemas/supply.py`: import_mpan:19); `export_arrangement` `site.py:94`.
- `ExportArrangement` — `schemas/export_arrangement.py:22-61`: route, counterparty, aggregator_cut_pct(:36), export_tariff_gbp_per_kwh(:44), local_flex_agreed_rate_gbp_per_mwh(:50). Consumed only at `gb_streams.py:571-573` (cut). Coherence validator `validators.py:213` (WARNING only).
- **MISSING: a per-stream consent object** — not in ConsentState/GateContext/ExportArrangement/either form/StreamRow. THIS is the core new schema.
- Hash policy: None `export_arrangement`/`provisioning_requirements` POPPED from canonical run input `site.py:82-94` (D42) — new None-defaulted fields must follow this to keep input-hash stable.

## Intake
- Wizard `app/templates/new_quote.html` (consents pane 5, :183-216): has g98/g99/g100/dno_application_state, import_mpan, export_mpan, mec_kw, export_route, aggregator_cut_pct, export_tariff. MISSING: supplier_consent, asc_kw (defaulted to MIC `app.py:493-494`), export_counterparty, local_flex_agreed_rate, real tariff/DUoS/levies, **per-stream consent**.
- Real form `app/templates/real_form.html`: has ALL 018 export/consent fields incl. supplier_consent(:128), local_flex_agreed_rate(:152), mic/mec/asc, duos r/a/g, levies, capacity_charge. MISSING: only **per-stream consent**.
- Mapping `app/app.py:_site_from_form:122-266`: ConsentState :184-199; SupplyBoundary(import_mpan) :202; ExportArrangement :208-232 (% → 0-1 fraction :227-229); ConnectionDetails :175-183; SiteModel :243-257. Routes: `/api/run` :822-935 (wizard), `/run/real` :965-981.

## Proposal (record-driven — "consent given" must originate in the gate layer / a new StreamRow field)
- `outputs/viewmodel.py`: `_stream_row:288-295` copies firmness/gate_status/unlock_condition; `_split:299-335` (floor/upside/excluded; `_GATED_CITING_MODES:45`).
- Customer proposal `outputs/proposal.py:_value_today:179-202` ("secured vs upside", "Potential upside (not promised)").
- Memo `outputs/underwriting.py:183-213` + `_consents_gates:298-310`.
- HTML `outputs/html_report.py:419-458`, `_consents:536-545`.
- Prose (number-free) `outputs/prose.py:firmness_explainer:90-96`, banner :53-61, next_steps :116-133.
- Entry `outputs/generate.py:42-107`.
- **Tension:** `test_outputs_indicative_gated.py` + `_GATED_CITING_MODES` forbid indicative artifacts citing gated streams — conflicts with "consent given" for a consented gated stream. Prompt authorises relaxing this for consented streams.

## Tests / determinism to migrate
Registry/interface `test_value_stack_interface.py`; gates `test_value_stack_gates.py` (:95-293); schema `test_schemas.py:122` (gated-cannot-be-firm); firmness→finance `test_finance_firmness_p90.py`; GateContext `test_sizing_deliverability.py`; engine conformance `test_optimisation_contract_conformance.py`; export `test_optimisation_export*.py`, `test_app_real_mode_gating.py`; intake `test_app_wizard_consents.py`; **golden `test_determinism_golden.py` GOLDEN_QUOTE_HASH :78** (recompute per :23-25); demo pins `test_app_demo.py`; indicative guard `test_outputs_indicative_gated.py`; bankable guard `test_outputs_bankable_guard.py`.
