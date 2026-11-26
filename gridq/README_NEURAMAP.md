# GridQ in NeuraMap

This folder is GridQ, Neura Energy's site flexibility and battery underwriting engine. Here is what it holds:

- the dispatch optimiser and sizer;
- the shared constraint library;
- finance and underwriting: lifecycle cashflows, DSCR, site eligibility and revenue tiers;
- the frozen interface contracts IC-1 to IC-14 (`src/gridq/contracts`);
- the thermal plant model, the adequacy simulator and edge security zones;
- the telemetry series contract;
- the full decision record (`docs/decisions.md`).

It is copied from the `roadmap/p1-foundations` integration branch of NeuraEnergy/uk. `.source_sha` gives the commit. The copy has no git history, and from now on GridQ is built here on the `neura/gridq` branch.

## What is not here, and why

Only customer data is left out: the data GridQ's own DPA guard quarantines (`src/gridq/governance/customer_data_manifest.json`). That covers:

- the host sites' half-hourly, metered and invoice data (`data/sites/`, `examples/real/`);
- the customer proposals and reports (`deliverables/`);
- the analysis notes that quote those figures (`docs/grundon_harwell/*.md`);
- the generated knowledge graph that indexed them (`graphify-out/`).

These belong to the customers and were shared without data-processing agreements, and this repository is public. Tests that need them skip, or fail with the DPA guard's reason.

## Running it

```bash
cd gridq
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
PYTHONPATH=src .venv/bin/python -m gridq.contracts --check
bash .github/ci/run_tests.sh fast
```

No licence has been granted yet; all rights are reserved by Neura Energy until one is added.
