# 018 — drive RESUME STATE (session paused 2026-07-17, Anthropic session limit)

Olly's session hit the API session limit mid-D8. The tree is left **coherent and green** (targeted
tests + ruff/format/mypy clean; full MILP suite not re-run but unaffected by the remaining partial
work). This file is the exact resume pointer. Read it + `018-meeting-readiness-drive.md` +
`018-D8-consents-screen-design.md` + `018-A2A3-export-design.md`.

## DONE and green (durable)
- **A1** dispatch diagnosis → `docs/goal/FINDINGS.md` **F-013** (engine correct; RT `no_viable_configuration` is genuine load shape, not a bug).
- **D1** — HH ingest accepts a bare `kwh`/`energy_kwh` column (`ingestion/hh_data.py`); RT loads at T1. Test + `docs/input_data_formats.md` updated.
- **A4** — `ExportArrangement` schema (`schemas/export_arrangement.py`, `ExportRoute` enum, `SiteModel.export_arrangement`, re-exports, validator `_check_export_arrangement_coherence` WARN-only, assembly POP). NOTE: `export_arrangement.py` is UNTRACKED (git add on commit).
- **B1** — `dev/fetch_prices.py` + tests: Elexon Insights **MID** endpoint `data.elexon.co.uk/bmrs/api/v1/datasets/MID` (£/MWh, **no API key**), off the pricing path, deterministic, live-smoke confirmed.
- **C1/C2** — `assumptions/gb_v1_5.yaml` (new release; adds `local_flex_piclo.aggregator_cut_pct: 0.20`) + `gb_streams.LocalFlexPicloStream._price` nets the cut (`_aggregator_cut`: site override → assumption → 0.0). `tests/test_value_stack_aggregator_cut.py` (4 tests).
- **D4** — Excel (.xlsx/.xls) HH ingest via `_read_tabular` (multi-sheet rejected, never guessed); `openpyxl>=3.1` added to pyproject. Tests + docs updated.
- **C034** — `test_determinism_golden.py::test_demo_quote_matches_pinned_golden_hash` now **skips on non-Linux** (Linux-anchored pin; priced outputs are platform-stable). Confirmed my changes moved NO priced value: demo still **165.25 kW × 3.15 h, IRR 9.6768, equity 10.3484, payback 7.0937**. FILE this under OPEN_COMMENTS C034 for Olly (skip-marker option).

## D8 — NOW SUBSTANTIALLY COMPLETE (2026-07-17 continuation)

**UPDATE:** the continuation session finished the consents feature. **DONE & green:** T1 soft-backend,
T2 schema field, T3 form plumbing, **T4 wizard consents pane (6 steps)**, **T5 real-form
"Consents & grid arrangements" fieldset**, **T6 demo forced consent (MEC=MIC, priced £ verified
unchanged)**, **T8 tests** (`test_app_wizard_consents.py` + the two `test_ingestion_site_json.py`
fixes). Both the guided wizard and the real form now carry the screen; both flow through
`_site_from_form`; blank forms keep `export_arrangement=None` (hash-stable). **Still open (minor):**
T7 (optional consents/export rows in the memo/inspect/proposal outputs — reverted earlier to avoid a
test cascade; redo WITH its `_MEMO_SECTIONS` test update), T6-RT (apply forced consent to the
gitignored RT fixtures — bundle with D7), and the **Linux golden re-pin on CI/deploy** (the demo
forced-consent change moves the demo quote; macOS skips, Linux must re-mint).

## D8 — original PARTIAL snapshot (superseded by the update above)
**Applied & green (KEEP — this is the owner-wanted soft-backend + plumbing):**
- **T1** `ingestion/validators.py` — g99/g100 export-incoherence downgraded ERROR→WARNING (backend never hard-blocks on consents, issue 018 §9). `tests/test_ingestion_site_json.py` two incoherent-site tests updated to expect a WARNING + successful load (T8 fragment done).
- **T2** `schemas/export_arrangement.py` — `local_flex_agreed_rate_gbp_per_mwh` field added.
- **T3** `app/app.py` `_site_from_form` — wires g98, dno_application_state, export_mpan (ConsentState), import_mpan (SupplyBoundary), and ExportArrangement (built ONLY when an export field is declared → None otherwise → hash-stable, VERIFIED via `test_quotes_assembly.py`). aggregator_cut_pct is UI percent → /100.

**Reverted (redo on resume — half-done or cascaded test breaks at the limit):**
- **T6** demo/RT forced-consent fixtures — reverted to pristine (`examples/demo_site/sitemodel.json` back to baseline; RT sitemodel untouched).
- **T7** display layer (`outputs/viewmodel.py`, `outputs/underwriting.py`, `app/templates/inspect.html`) — reverted to baseline (the memo heading rename broke `test_outputs_sections_and_provenance.py::test_memo_has_all_required_sections`; redo T7 WITH its T8 test update: update `_MEMO_SECTIONS` if the heading is renamed).

**NOT started:**
- **T4** `app/templates/new_quote.html` new "05 · CONSENTS & GRID" wizard pane + `static/wizard.js` (MAX 5→6, renumber review hooks, route-toggle, populateReview rows, loadDemo prefill).
- **T5** `app/templates/real_form.html` parity fieldset.
- **T6** (redo, full) + **T7** (redo, with test update) + rest of **T8** (wizard smoke test, viewmodel row test, changelog, HANDOFF).

**RT prefill additions for Olly** (gitignored `examples/real/haywood_oaks_prefill.json`, absent on checkout — apply when doing T6): keys `g98/g99/g100/dno_application_state/supplier_consent: "approved"`, `import_mpan` (from invoice), `export_mpan`, `mec_kw: "500"`, `asc_kw: "500"`, `export_route: "flexibility_aggregator"`, `aggregator_cut_pct: "20"`.

**Golden on T6 resume:** forced-consent legitimately changes the demo quote (gated-upside gate text only — priced £ must stay identical: 165.25 kW×3.15 h / IRR 9.6768 / equity 10.3484 / payback 7.0937). The macOS golden-hash skips; the **Linux pin must be re-minted on CI/deploy** after T6. Verify priced invariants before re-pinning; if any £ moves, STOP.

## FINAL STATE (2026-07-17 continuation — drive substantially complete)
The remaining chain is now DONE and green (targeted + full quality bar): **B2** (price-series alignment `optimisation/export_prices.py` + `MilpEngine(market_forecast=…)` wiring), **A2/A3** (the consent-gated export overlay + `WHOLESALE_P415` gated-upside valuation; G1/C036 + D43 filed; export-off byte-identical, hand-computed +£18.8k/yr net delta, 23 tests), **D5** (observed-peak floor in the MIC data-ask, never auto-fills), and **E1/E2/E3** (README honest-status box, project_status refresh, changelog, decisions/OPEN_COMMENTS). C4 Piclo note done.

**D7 outcome (honest):** Robert Thomas stays **no-viable as a standalone BTM battery** (load shape, F-013) — the sizing floor selects no spec, so the export overlay (which runs on the selected spec) doesn't apply to RT; its value case is export/flex + the open PV/tariff data questions. The export capability is proven by the 23 tests + the hand-computed positive case. A live full-year DEMO export run is blocked by a 2-half-hour DST mismatch between the SYNTHETIC demo's naive 48/day grid and real GB settlement-period timestamps (fixture artifact — a real meter's GB-local timestamps align); the demo floor is unchanged and the overlay correctly refuses on incomplete coverage.

**D6 (bill-PDF) — DEFERRED to roadmap** (explicit stretch in the plan). The bill upload is already accepted and recorded as provenance; deterministic text extraction (pdfplumber) → a field-by-field human-confirmation screen is the v1 design, not built this drive. No fabricated extraction; the honest story is "upload recorded; extraction on the roadmap."

## Pre-commit chores (for whoever commits — no commit made without the user asking)
- `git add src/gridq/schemas/export_arrangement.py src/gridq/optimisation/export_prices.py` (both untracked; plus the new `tests/test_*` files).
- **Re-mint the Linux golden pin** on the deploy box / CI: the demo forced-consent change moves the demo quote; it correctly SKIPS on macOS (C034), but CI (Linux) needs `GOLDEN_QUOTE_HASH` recomputed. Verify priced invariants are unchanged first (165.25 kW×3.15 h / IRR 9.6768 / equity 10.3484 / payback 7.0937 / DSCR 2.2019).
- Do NOT revert T1 (backend-never-blocks) — the owner's explicit requirement.
- The R1–R7 amendments on D43 / C036 are logged for Olly's review (not blockers).
