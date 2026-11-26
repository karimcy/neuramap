# examples/demo_site/ — illustrative demo dataset

**This is synthetic, illustrative data — not a real site, not real-engine-confirmed, and not
bankable.** It exists so the Wave-5 UI (`gridq.app`, "Demo" mode) can show the full end-to-end flow —
a populated 3D sizing surface, the firm-vs-gated value split, P50/P90 finance and a customer
proposal — without anyone's real data. Its numbers are never to be presented as a real quote.

## Files
- `sitemodel.json` — a representative C&I cold-store `SiteModel` (T2), generated from the schema.
- `hh_2025.csv` — a **synthetic** 12-month half-hourly import series (`timestamp`, `import_kw`),
  deterministically generated (fixed seed): a steady base with a midday rise and a sharp 16:00–19:00
  DUoS-red evening peak. ~126 kW mean, ~248 kW peak, ~1.1 GWh/yr. No real meter data.

## What the demo run does (and why it is honest)
The demo runs the **real** engine (`MilpEngine`'s dispatch solver via `gridq.sizing.enumerate_sizing`)
on this synthetic load, with **illustrative (not underwritten) debt terms injected** (see
`gridq.app.demo.DEMO_DEBT_TERMS`) so the sizing surface populates a levered-return z-axis, a
P90-DSCR-feasible region and a chosen optimum. Nothing is fabricated: every figure is derived by the
deterministic engine/finance from these labelled inputs, and the run still comes back honestly
**`not_bankable`** (the assumption library is placeholders; the debt is illustrative). The UI badges
the run as illustrative throughout.

## Privacy
Synthetic data only — safe to commit and to show publicly. Real third-party offtaker/meter or
counterparty-credit data is **never** preloaded here or shipped in the public demo (wave5_scope §5);
a user's own real-mode submission is processed for their run and stored only under the gitignored run
store (`$GRIDQ_RUN_STORE`, default `runs/`).

## Regenerating
Deterministic: regenerate with the documented seed/profile if the schema changes. Generated, like all
fixtures, from the Pydantic models (never hand-edited) so it stays valid under the strict config.
