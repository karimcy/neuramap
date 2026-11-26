# Revenue-Data Drive — Loop 05: ACCEPTANCE (fully-consented demo)

**Branch:** `agent/revenue-data-acceptance` (off `agent/revenue-data-drive`, Slices 1–3 committed).
**Assumption release:** `gb_v1_4` (unchanged — the app pin is left as-is).
**Contributor:** Olly. **Date:** 2026-07-20.

## Goal

Make the public **demo** (`demo=1` path, `load_demo_site()`) **fully consented** so the demo
prices **every** revenue stream and the proposal reports **"consent given"** for each, then re-pin
the deliberately-moved golden/demo pins and prove determinism. No contract changed; no assumption
release flipped; no invented pricing (the demo's agreed rates are illustrative customer INPUTS,
labelled as such).

## What changed (this loop's file lane only)

- `examples/demo_site/sitemodel.json` — added `stream_consents` for **all 10** `MarketStream`
  values (`consented: true`), and added the two `ExportArrangement` rate fields.
- `tests/test_determinism_golden.py` — re-pinned `GOLDEN_QUOTE_HASH` (deliberate move; macOS pin +
  ⚠ PENDING LINUX RE-MINT).
- `tests/test_app_demo.py` — verified green (structural pins only; **no literal re-mint needed**).
- `README.md` — per-stream table marked consentable-and-priced (gated ⇒ CONTRACTED with consent +
  rate, never FIRM).
- `docs/goal/loops/revenue-data-05-acceptance.md` — this record.

## Demo `stream_consents` + illustrative rates added

All 10 streams `consented: true`. Illustrative agreed rates (customer-supplied INPUTS, labelled
illustrative — not underwritten, not market-cleared):

| Stream | `agreed_rate_gbp` | Unit | Rate home |
|--------|-------------------|------|-----------|
| CAPACITY_MARKET | 45.0 | £/kW/yr | generic carrier (`stream_consents[].agreed_rate_gbp`) |
| BALANCING_MECHANISM | 15,000.0 | £/MW/yr | generic carrier |
| ANCILLARY_DC_DM_DR | 12,000.0 | £/MW/yr | generic carrier |
| WHOLESALE_P415 | — (null) | £/kWh via `ExportArrangement.export_tariff_gbp_per_kwh` = **0.075** | ExportArrangement |
| LOCAL_FLEX_PICLO | — (null) | £/MWh via `ExportArrangement.local_flex_agreed_rate_gbp_per_mwh` = **300.0** | ExportArrangement |
| TARIFF_ARBITRAGE, DUOS_RED_BAND_AVOIDANCE, MIC_ASC_HEADROOM, PPA_OR_BILL_SAVINGS, LOAD_SERVICE | — (null) | n/a | load-derived / by-construction; no agreed rate needed |

`ExportArrangement.aggregator_cut_pct` kept at 0.20. The site is otherwise unchanged and validates
via `load_demo_site()` / `SiteModel` (the `_canonicalise_stream_consents` validator sorts and
duplicate-checks the list).

## Fully-consented demo state — which streams price + £ + source

Selected spec **166.72 kW × 3.16 h**; run stays honestly **`not_bankable`** (placeholder
assumptions). **Every stream reports `consent_status = "given"`.**

### Priced floor (`value_streams`)

| Stream | £/yr | Firmness | Gate | £ source |
|--------|------|----------|------|----------|
| TARIFF_ARBITRAGE | 19,821.23 | firm | open | load × ToU tariff spread, net of RTE (not consent-gated) |
| DUOS_RED_BAND_AVOIDANCE | 16,158.91 | firm | open | DUoS rate × red-band discharge (not consent-gated) |
| CAPACITY_MARKET | 1,571.00 | **conditional** | conditional | agreed **£45/kW/yr** × de-rated deliverable kW; deliverability physics hold it out of the floor |
| BALANCING_MECHANISM | 523.67 | **contracted** | open | agreed **£15,000/MW/yr** × engine-measured de-rated deliverable MW |
| ANCILLARY_DC_DM_DR | 418.93 | **contracted** | open | agreed **£12,000/MW/yr** × engine-measured de-rated deliverable MW |
| WHOLESALE_P415 | 72.77 | **contracted** | open | agreed export tariff **£0.075/kWh** × overlay export MWh (contracted floor entry) |
| PPA_OR_BILL_SAVINGS | 0.00 | firm | open | aggregate £0 by design (itemised across arbitrage + DUoS) |
| MIC_ASC_HEADROOM | 0.00 | firm | open | £0 by construction (value realised via connection-bounded arbitrage) |
| LOCAL_FLEX_PICLO | 0.00 | conditional | conditional | rate present (£300/MWh) but **no awarded tender window** ⇒ engine-measured £0 |
| LOAD_SERVICE | 0.00 | excluded | closed | no declared provisioning requirement ⇒ £0 |

### Gated upside (`gated_upside`)

| Stream | £/yr | Firmness | Gate | £ source |
|--------|------|----------|------|----------|
| WHOLESALE_P415 | 482.03 | conditional | open | Elexon-MID perfect-foresight export delta — **separate indicative upside**, never contracted, never in P50/P90/DSCR |

Note WHOLESALE_P415 appears twice by design: a CONTRACTED floor entry (agreed tariff × overlay MWh)
plus its unchanged CONDITIONAL gated-upside Elexon-MID delta.

**Honest £0s.** LOCAL_FLEX and LOAD_SERVICE carry consent + (for local flex) a rate, but price £0
because their **volumes are engine-measured** and no tender / provisioning requirement is present.
PPA and MIC are £0 by construction. Consent is authorisation, not value — the £0s are correct.

## Pin re-mints (old → new)

### `GOLDEN_QUOTE_HASH` (`tests/test_determinism_golden.py`)

- **old:** `sha256:20c0f382bf5fc4b187933d36557a55936af5bfcda71af8b489fbac1bc7318bf7` (slice-2a
  macOS-observed pin; unconsented demo, `consent_status: null` on every stream)
- **new (macOS-observed):** `sha256:46a897dd4e1c9c265caf54a5228bbbbc88a89b9caed3db265d1991c92f94c070`
- **reason:** the demo is now fully consented — `consent_status = "given"` on every stream, and
  the newly-priced consented streams (CAPACITY_MARKET £1,571.00; BALANCING_MECHANISM £523.67 and
  ANCILLARY_DC_DM_DR £418.93 promoted to CONTRACTED in `value_streams`; WHOLESALE_P415 CONTRACTED
  floor entry £72.77) plus the export-arrangement rates change the deterministic content. Selection
  moved 165.25 kW × 3.15 h → 166.72 kW × 3.16 h. A **deliberate** move.

> ⚠ **PENDING LINUX RE-MINT.** The new pin is the **macOS-observed** value. The pin is
> Linux-anchored (C034): the authoritative LINUX value must be RE-MINTED on the CI/deploy box
> (sub-peak dispatch placement is a solver-chosen vertex that can differ across platforms without a
> priced number moving). On macOS the exact-hash assertion **SKIPS**; the two determinism sub-tests
> (`test_demo_quote_id_and_input_hash_are_stable`, `test_demo_serialized_outputs_are_byte_identical`)
> pass everywhere. Precedent: commit 7cd6517 re-minted the Linux pin for the issue-018 demo the same
> way.

### `tests/test_app_demo.py`

- **No literal / hash pin exists** in this module — its assertions are structural
  (`len(grid.points) >= 80`, `search_version == "m4-two-stage-v2"`, a covenant-feasible,
  full-series-confirmed non-proxy optimum that the result reports, DEMO/ILLUSTRATIVE/NOT BANKABLE
  badging, and byte-identical artifact render off the record). All passed **unchanged** against the
  fully-consented demo. No re-mint required.

## Determinism proof

Two full demo solves (`load_demo_site()` + `DemoEngine` + `generate_quote`, and `POST /run/demo`):

- `quote_id` A == B: **True** (`quote_run_b09efac52bfc702b`)
- `input_hash` A == B: **True**
- normalised whole-quote hash A == B: **True** (byte-identical)

## Quality bar

`ruff check` · `ruff format` · `mypy` · `gridq.dev.review_gates` · `pytest` — green, except the
known C034 macOS golden skip (`test_demo_quote_matches_pinned_golden_hash` skips on darwin; the
id-stable + byte-identical-resolve sub-tests pass).
