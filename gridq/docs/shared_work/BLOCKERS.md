# BLOCKERS

Things stopping or threatening progress. Keep current — clear a blocker when it's resolved (move it out or strike it). Each blocker records what it is, why it matters, the owner (if known), the decision needed, and any workaround.

<!-- BLOCKERS:BEGIN -->

### B001 · M9 credit-tool ownership (PE vs Finance)
- **Why it matters:** decides who builds the Creditsafe-backed scoring tool; GridQ owns only the deterministic score→rate/tenor mapping.
- **Owner:** unresolved (boundary Open Q2 — needs Oli/Sim).
- **Decision needed:** confirm GridQ builds the *mapping* only and consumes Sim's tool output.
- **Workaround:** **Built (W4-5, D18):** the deterministic mapping `finance.credit.map_credit_to_rate_tenor` consumes a manual/stub `CreditStatus` band → rate/tenor (versioned in `financing.credit_rate_tenor_policy`); the future Creditsafe tool drops in upstream by producing the `CreditStatus`. No external API, no invented band. The *ownership* of that tool stays the open item; v1 progress is unblocked.
- **Update 2026-11-26 (WP-UW-03, D75):** the DSCR no longer waits on this blocker. A lender's terms are a typed input (`LenderTerms`, `gridq run --lender-terms`, the app's lender-terms block), so a run with terms measures a DSCR and a run without says why. Findings-register blocker B001 ("`min_dscr=None` on every run") is closed. The tool-ownership question above stays open; it now only decides whether a credit band can supply rate and tenor when no lender terms exist.

### B002 · Route-to-market (RTM) partner / API unknown
- **Why it matters:** gates precise wholesale/BM/ancillary modelling; placeholders risk being read as real.
- **Owner:** Growth/Operations (Zara/Karim) for the commercial side; GridQ owns the technical interface.
- **Decision needed:** which services are modelled precisely vs lumped as decayed upside, once the partner is chosen.
- **Workaround:** keep wholesale/P415 as labelled gated upside; never in the priced floor.

### Not a blocker — Wave 5 work (recorded here so it isn't mistaken for one)
- ~~**Sizing evaluation-grid exposure for the 3D surface.**~~ **DONE — W5-0 (decision D23, Olly, `agent/sizing`).** The full evaluated grid (`SizingRun.candidates_evaluated` — power×duration×levered-return×P90-DSCR×feasibility) is now exposed off the frozen seam: `SizingRun.sizing_grid()` → serializable `gridq.schemas.sizing.SizingGrid`; `MilpEngine.last_sizing_run()` (duck-typed hook mirroring `selected_dispatch_energy()`); `generate_quote` persists it onto the additive, Optional `QuoteRecord.sizing_grid` — **excluded from `input_hash`, no `SCHEMA_VERSION` bump, `OptimisationResult` untouched (D13)**. 12 new tests; tree green (527). The **3D surface renderer itself** (`wave5_scope.md` §4 charting) remains the open Wave-5 item that consumes this data.
<!-- BLOCKERS:END -->
