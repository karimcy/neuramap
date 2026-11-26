---
proposed_by: agent/value-stack
date: 2026-06-22
status: proposed   # proposed | accepted | rejected
supersedes: the 2026-06-17 value-stack proposal (status-gating rule, edit 1 below — still unapplied)
---

Two procedural techniques from the value-stack lane (M2: gating in W2, real value functions in
W3-1). Both are reusable "how to act" rules, not changelog facts. ≤3 edits, per the protocol.

## Edit 1

### Operation
append

### Target
`## Assumption / provenance rules`

### Proposed text
- **Gate not-yet-real streams on assumption *status*, not a hand-written flag.** A market value
  stream that depends on an external partner/source (e.g. gated wholesale/BM/ancillary, or any
  placeholder-priced stream) should open its gate only when the backing assumption is
  `status: confirmed` *and* its value is positive — read via `load_assumptions(version)`. Then a
  placeholder-zero / `needs_review` entry keeps the stream **conditional upside** automatically,
  and promoting it to the priced floor is a **version bump** (a real source confirming the value),
  never a code edit or a fabricated availability flag. Surface every unmet precondition as an
  explicit missing-requirement, not a default `True`.

## Edit 2

### Operation
append

### Target
`## Deterministic pricing rules`

### Proposed text
- **A value function prices only over a dispatch schedule, and only as `library number ×
  dispatched quantity`.** With no dispatch schedule (or a closed gate) it returns `0.0` — you
  cannot value dispatch you do not have, so nothing is invented. Keep the £ formulas in the
  market **content** module (a per-stream `_price` hook); the market-agnostic base only
  orchestrates, so the core never holds a market number and never branches on `market == "GB"`.
  A placeholder/`needs_review` assumption still flows through as a labelled, **not-bankable**
  number that names its key — never a silent figure. Firmness (gate) decides floor membership;
  the £ is orthogonal (a `conditional` stream may carry labelled upside but stays out of the
  floor). Pre-solve facts the SiteModel cannot hold (engine-derived deliverability, time-varying
  tender availability, fleet-level portfolio MW) arrive via a **generic, market-agnostic context**
  threaded additively into the gate/value seam — never a SiteModel field, never a `market` branch.

## Evidence
W3-1 (HANDOFF_LOG 2026-06-22 "agent/value-stack"; changelog 2026-06-22): `gb_streams._price`
hooks compute each stream's £/yr from `gb_v0_1` × dispatched quantities (e.g. Capacity Market =
`clearing_price_gbp_per_kw_yr × de-rated kW`), the base `streams.value_function` prices nothing
without a schedule, and the generic `GateContext` (decision D12) opens CM/Piclo when a producing
lane supplies deliverability/tender. Pinned by `tests/test_value_stack_value_functions.py`
(`*_traces_to_gb_v0_1`, `test_no_dispatch_schedule_prices_nothing`,
`test_closed_gate_prices_nothing_even_with_dispatch`, `test_gated_streams_price_zero_and_never_firm`,
`test_gate_context_additive_backward_compatible`). Edit 1's evidence is the W2 gating build
(`route_to_market_available` + `test_p415_without_rtm_partner_is_conditional`).

## Notes
Both belong in `SKILLS.md` as procedural memory and are distinct from the existing "every number
lives in the versioned library" rule (which is about *where numbers live*, not *how a value
function consumes them or how a gate consumes their status*). Not the protected slow-update block.
