# Runbook — the `examples/real` end-to-end run (Robert Thomas Farms)

**What this is.** The reproducible recipe for running the full underwriting pipeline from ONLY
the files in `examples/real/` (no uploads, no manual steps, no hidden inputs), what the correct
output is, and how to verify it. The derivation of "correct" is
[`EXPECTED_RESULT.md`](EXPECTED_RESULT.md); the automated proof is
`tests/test_e2e_examples_real.py`.

## Inputs (all of them)

`examples/real/` (gitignored, private site facts — present on this host):

| File | Role |
|---|---|
| `haywood_oaks_prefill.json` | The machine-readable transcription of the two fact files below — every field's provenance is documented inside it (`_field_provenance`). This is what the form posts. |
| `invoices_tariff_extract.md` | Transcribed SmartestEnergy invoice EVY220LUQ/007 (rates, CCL, standing, capacity charge, excess-capacity history) |
| `robert_thomas_farms_site_facts.md` | Observed load facts (215 MWh/yr, 421 kW overnight peak, near-zero midday, day/night split) |

There is **no half-hourly CSV** in the folder — that is the point: the pipeline must produce its
best honest verdict from billing-level evidence, modelling the profile from the transcribed
constraints (T1, loudly labelled) rather than refusing or inventing.

## Run it

### Through the app (the user path)

1. `python -m gridq.app` (or use the live service).
2. Open `/real/haywood-oaks` — the form arrives prefilled from the JSON.
3. Submit. The same fields drive `POST /api/run`; the job takes ~2 minutes (the two-stage MILP
   sizing search over ~140 candidates).
4. You land on `/run/<quote_id>`.

### Headless (CI / proof)

```bash
TZ=UTC OMP_NUM_THREADS=1 /root/.venv/bin/python -m pytest tests/test_e2e_examples_real.py -q
```

Heavy module (~5 min on the 2-vCPU box: it runs the whole pipeline TWICE — the second run proves
determinism). Skips cleanly on hosts without the private `examples/real` facts.

## The correct result (what you should see)

- **Verdict: NO VIABLE CONFIGURATION** — first-class on the record
  (`optimisation_result.selection_status == "no_viable_configuration"`), on the results page, in
  the memo, the proposal and the HTML report. ~141 candidates solved `optimal` and priced; none
  produces a positive project IRR or levered return. **No system spec is presented** (the
  pre-fix pipeline recommended a 4 kW × 3.09 h grid corner selected purely by a tie-break).
- **Why (structural, per the derivation):** effective arbitrage margin
  `(0.2036+0.00775) − (0.165+0.00775)/0.88 ≈ £0.0150/kWh` × a day-band ceiling of ~117,800
  kWh/yr ⇒ **≤ £1,772/yr** from arbitrage at ANY size, vs £175/kWh energy capex (32-year simple
  payback before opex); no pass-through DUoS; no export route; capacity lines fixed on ASC.
- **Inputs fully consumed:** the site record carries the CCL levy line (0.775 p/kWh), the
  capacity charge (8.76 p/kVA/day on ASC 250 kVA), standing charge, day/night rates, tariff
  name; the modelled profile hits annual 215,000 kWh, peak 421 kW and day-band share 0.548
  exactly, near-zero midday — each traceable to a documented observation, every modelling
  choice disclosed as a warning.
- **No phantom finance:** with nothing viable there is no financial case and no PPA "price
  recommendation" (pre-fix: £1.06/kWh — 5× the site's current rate — rendered bare).
- **Ranked data asks** include the full-year HH months and export/G99 status — the two pieces
  of evidence that could change the verdict — plus tariff confirmation against the binary PDFs.
- **Not bankable**, as every quote on placeholder releases is, explicitly.
- **Deterministic:** an identical resubmission produces the identical `quote_id`.

## What would change the verdict (and how to re-run)

1. A tariff with pass-through DUoS bands or real ToU spreads → fill `duos_*_rate` /
   `import_rates` and re-submit.
2. Export consents (G99) + a route-to-market partner → the gated streams open.
3. The metered full-year HH file → upload it on the form (upgrades T1 → T2 and replaces the
   modelled profile).

## Troubleshooting

- **"Not enough data to produce a result"** — the prefill's `annual_consumption_kwh` was
  cleared; the modelled profile needs it.
- **Example page shows a blank form** — `examples/real/haywood_oaks_prefill.json` is absent
  (fresh public checkout): install the private facts.
- **Slow solve** — pin threads (`TZ=UTC OMP_NUM_THREADS=1 ...`), never run two MILP suites in
  parallel on the 2-vCPU box.
