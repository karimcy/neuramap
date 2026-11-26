# IC-2 real-data fixtures (git-ignored)

**Test fixture, internal. Data from UK Power Networks (CC BY 4.0) and Northern Powergrid
(Northern Powergrid Open Data Licence v1.0). Attribution: UK Power Networks Open Data Portal;
Northern Powergrid Open Data.**

## The gate: why the data is not in git

The UKPN and NPg datasets are served through Huwise (OpenDataSoft) portals. The Neura Constraint
Atlas treats everything from them as **eyes-only** until Huwise's written authorisation for
automated retrieval is recorded:

- Atlas `research/DATA_REQUESTS.md`, "Gate" (and rows DR-01, DR-03);
- Atlas `atlas/curated/data_authorisations.json`: `ukpn_curtailment` and `npg_curtailment` are
  `not_recorded` (checked 2026-11-17).

This repository pushes to GitHub (`NeuraEnergy/uk`). Committing even a small extract would let
anyone with repository or CI access see Huwise-sourced data, which the gate forbids. So:

- `.gitignore` here ignores everything except itself, this README and `MANIFEST.json`.
- `MANIFEST.json` (committed) holds only provenance: request URLs (no credentials), retrieval
  times, SHA-256 of every response and of every built set, licences and attribution. It holds no
  data rows and no figure derived from them.
- The committed fixtures one level up (`../ic2_*.synthetic.json`) copy the **shape** of these
  sets with synthetic values, so CI runs the full IC-2 suite without the gated data.
- The tests that need the real files skip, with this reason, when the files are absent.

When the authorisation is recorded in the Atlas register, the user can decide whether to commit
these files. CC BY 4.0 allows redistribution with attribution. The NPg licence must be read
first. The gate is the user's own rule and stays stricter than either licence.

## What is here (locally)

| File | What |
|---|---|
| `ukpn_der4_june_events.response.json` | UKPN `ukpn-curtailment-events-site-specific`, DER4 (scheme NORWICH), events overlapping 11-15 June (UTC) of 2023-2026: 15 events |
| `ukpn_der4_years.response.json` | the same dataset: DER4 event count per calendar year (2023: 18, 2024: 11, 2025: 14, 2026: 12) |
| `ukpn_dataset_meta.response.json` | the dataset's catalogue metadata (licence: CC BY 4.0) |
| `npg_der0000099_events.response.json` | NPg `curtailment-events-site-specific`, customer DER-0000099 (scheme Norton): all 3 published events |
| `npg_dataset_meta.response.json` | the dataset's catalogue metadata (licence: Northern Powergrid Open Data Licence v1.0) |
| `retrieval.json` | URL, HTTP status, retrieval time and SHA-256 of each response |
| `ic2_ukpn_der4_norwich_june.real.json` | IC-2 set: 4 scenarios, weights 1/4 each, minute + half-hourly |
| `ic2_npg_der0000099_norton_events.real.json` | IC-2 set: 3 scenarios, weights 1/3 each, minute + half-hourly |

Retrieved 2026-11-17 00:22 UTC, directly from the publishers' Explore API v2.1 with the Atlas's
existing keys (DR-01, DR-03 granted). The running Atlas server was not used: its `/api/distribution`
returns only aggregates, and its `/api/asof` router is not mounted. The Atlas store
(`atlas/data/atlas.duckdb`) was not opened.

## How each set is derived (stated rules, nothing invented)

**Depth (both sets).** An event's depth is the publisher's own *average access reduction*,
held uniform over the published `[start, end)`. An interval's value is that depth times the
fraction of the interval the event covers, summed over events. UKPN's peak goes in
`peak_depth_mw`. NPg publishes no peak, and neither publishes notice, so both are `Unknown`
with the reason. Both are **generation-export** curtailment (`signal_basis`), not demand-import
restriction: spec A1-04's data risk, recorded on every set.

**Weights, UKPN DER4.** There is one scenario per calendar year in which the dataset publishes
any DER4 event (2023-2026, from the recorded per-year count). Each scenario replays 11-15 June
(UTC) of its year. A year with no event in the window is a zero-restriction scenario (2023 and
2024). Weights are equal: 1/4 each.

**Weights, NPg DER-0000099.** There is one scenario per published event for the customer (3
events, all the dataset holds for it). Each covers the 48 h from 00:00 UTC on the day the event
starts. Weights are equal: 1/3 each. This set is the restriction profile **conditional on an
outage-related event occurring**. It carries no annual frequency.

## Regenerate

```bash
python tests/contracts/_ic2_real.py fetch   # network + Atlas keys (ATLAS_ENV, default ../atlas/.env)
python tests/contracts/_ic2_real.py build   # pure; rewrites the sets and MANIFEST.json
```

The tests rebuild each set from its recorded response and require the manifest's content hash.
A fresh `fetch` will change the retrieval times, and so the hashes, so commit the new manifest
with it.
