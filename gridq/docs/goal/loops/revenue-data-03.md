# Revenue-Data Drive — Loop 03 (Slice 3: the intake)

**Branch:** `agent/revenue-data-drive` (2a=db11b54, 2b=5fcf4f5, 2c=f7b1fe5 committed — consent flows
gate→price→floor→proposal for a site that CARRIES `stream_consents`).
**Goal of this slice:** let the customer DECLARE per-stream consent + agreed rates in the app, mapped
onto `SiteModel.stream_consents` — the intake end of the chain.

## What landed
1. **Shared per-stream consent control** — new partial `templates/_stream_consents.html`, included by
   BOTH the wizard consents pane (`new_quote.html`) and the real form (`real_form.html`). All ten
   streams get a consent checkbox (`consent_<stream>`); Capacity Market (£/kW/yr), Balancing Mechanism
   and Ancillary (£/MW/yr) — the streams with no dedicated `ExportArrangement` rate home — also get an
   optional agreed-rate input (`consent_rate_<stream>`). Export tariff / aggregator cut / local-flex
   rate keep their own fields (not duplicated). Helper copy: consent authorises us to pursue the
   revenue, it is not a guarantee. Tokens-only, no em dashes; design-system guard green.
2. **Wizard parity** — added the four fields the wizard lacked vs the real form: `supplier_consent`,
   `asc_kw`, `export_counterparty`, `local_flex_agreed_rate_gbp_per_mwh` (already mapped in
   `_site_from_form`; only the inputs were missing).
3. **Mapping** — `app.py:_site_from_form` builds `stream_consents` from the ticked boxes (+ per-stream
   agreed rate where present); `None` when nothing ticked (POPPED from the canonical run input →
   hash-stable). Canonical/duplicate rules enforced by the `SiteModel` validator.
4. **Tests** — `tests/test_app_wizard_consents.py`: both forms render the controls + helper text;
   wizard carries the parity fields; a full-consent submission maps to the expected `stream_consents`
   (streams, consented, agreed_rate_gbp) + populates ConsentState/ConnectionDetails/ExportArrangement;
   a no-consent submission → `stream_consents=None` + hash-stable canonical dump; a fully-consented
   intake flows into pricing end-to-end (CM prices at the agreed £50/kW/yr = £10,000, consent_status
   "given").

## Determinism
This slice touches only the wizard/real-form path + `_site_from_form`, NOT the JSON demo
`load_demo_site` (the wizard demo=1 path loads the JSON, so form changes cannot reach it). The demo
golden `GOLDEN_QUOTE_HASH` and the `test_app_demo` pins are UNCHANGED (verified: 6 passed, 1 skipped =
the known C034 macOS golden skip; no re-mint). ruff/format/mypy/review_gates green.

## Worked example
A wizard/real submission with `consent_capacity_market=on`, `consent_rate_capacity_market=50`,
`consent_balancing_mechanism=on`, `consent_rate_balancing_mechanism=10000`, `consent_tariff_arbitrage=on`,
`consent_local_flex_piclo=on` maps to (canonically sorted by stream value):
`stream_consents = (BALANCING_MECHANISM consented rate=10000, CAPACITY_MARKET consented rate=50,
LOCAL_FLEX_PICLO consented rate=None, TARIFF_ARBITRAGE consented rate=None)`.

## Next (drive remainder)
- Slice 4: data fetchers (perimeter, `gridq.dev` pattern) + versioned `gb_v1_x` releases per sourced
  stream (register → assumption keys → provenance).
- Slice 5/acceptance: fully-permissioned quote prices every backed stream; run twice byte-identical;
  README per-stream table updated.
