# Revenue-Data Drive — Loop 01 (orient + research)

**Branch:** `agent/revenue-data-drive` (off `agent/olly-next` @ 67b70cf — has the 018 drive: README per-stream table, consents, export_arrangement).
**Goal:** every revenue stream data-backed AND consentable at intake, or an evidenced not-realistic verdict; a fully-permissioned quote prices every backed stream and the proposal says "consent given" per stream. Prompt: `docs/goal/revenue-data-drive-prompt.md` (authoritative).

## Method
Self-paced loops. Per stream, recurse leaves (data / equations / consent inputs / wiring) until each is sourced, built, or evidenced-dead. Deliverable ground truth: `docs/revenue_data_register.md`.

## Loop 01 — what's running
1. **Research workflow** (`wf_8d6c54bb-236`): 10 parallel live-web agents (one per stream) → sourced data leaves (URL/licence/access/cost/cadence/sample-fetch) + equation citations + consent inputs + disposition; then adversarial verify — refute every not-realistic verdict, fetch-check every claimed source.
2. **Code ground-truth map** (Explore): per-stream registry/pricing/gate/firmness, GateContext + consent plumbing, the intake forms (new_quote.html / real_form.html / app.py), the proposal wording, and the tests/determinism guards that will need migrating.

## Build order (provisional — confirm after research + map return)
1. Intake consent model — the spine: schema fields (G98/G99/G100, import/export MPAN, MIC/MEC/ASC, DNO region, export route, agreed rates: export £/kWh, aggregator cut %, local-flex rate) + per-stream consent; carried on the quote.
2. Gate/firmness rewrite — explicit consent + agreed rate opens a stream and drives pricing (overwrite GATED_UPSIDE_STREAMS / never-FIRM rules per the authoritative prompt).
3. Data fetchers + assumption releases — per sourced stream (perimeter fetchers, versioned/hashed inputs).
4. Proposal — "consent given" per stream when supplied; conditional only when not.
5. Acceptance — fully-permissioned quote prices every backed stream, run twice byte-identical, suite green.

## Open decisions (resolve as research lands)
- Wholesale→tariff conversion: **REALISTIC — confirmed.** Elexon MID (keyless, fetched working) + every non-commodity adder has an open published source (DUoS SLC14, NESO TNUoS/BSUoS, Ofgem RO/cap models, gov.uk CCL, LCCC CfD ILR); composed delivered-p/kWh formula cited to the Ofgem cap Annex methodology. Estimate only, never the contracted rate.
- Not-realistic streams: **NONE are dead ends.** 8/10 fully data-backed with keyless GB public data; BM is `partial` (public data → indicative; per-asset value needs an aggregator, consentable via VLP appointment + cut %); ANCILLARY fully backed (NESO CKAN auction prices). Private inputs (MIC/ASC, invoice, load-service counterfactual) are customer documents under LOA.

## Loop 01 — outcomes
- **Register written:** `docs/revenue_data_register.md` — 8 streams sourced + cited; WHOLESALE_P415 + LOCAL_FLEX_PICLO baseline-pending (research finishing in bg `wjx3zy8l0`; Elexon MID for wholesale already confirmed keyless/working).
- **Recurring free sources:** Elexon Insights API, NESO Data Portal CKAN, DNO SLC14 statements, Ofgem/gov.uk. All OGL/open-data licensed.
- **Build started — Slice 1 (bg feature-builder):** `RevenueStreamConsent` schema + `SiteModel.stream_consents` (hash-safe pop-when-None, canonicalised, carried on the quote) + tests. Determinism-neutral; no gate/pricing change.

## Build sequence (each an independently-verifiable slice)
1. **[in progress]** Consent schema spine (`RevenueStreamConsent`, `SiteModel.stream_consents`, hash-safe).
2. Gate/firmness reads consent + agreed rate → opens the stream, elevates firmness; relax the 4 never-FIRM layers + the indicative-gated guard for CONSENTED streams (prompt authorises overwriting these).
3. Intake: wizard parity with real form + a per-stream consent control; map in `app.py:_site_from_form`.
4. Data fetchers (perimeter, `gridq.dev` pattern) + versioned `gb_v1_x` releases per sourced stream (register → assumption keys → provenance).
5. Proposal: "consent given" per stream when supplied; conditional only when not.
6. Acceptance: fully-permissioned quote prices every backed stream; run twice byte-identical; suite green; README table updated.
