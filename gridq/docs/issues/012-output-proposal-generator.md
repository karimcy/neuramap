# 012 · Customer proposal generator

**Owner:** Oli
**Module(s):** M8 (proposal)
**Milestone:** Milestone 6 — Outputs & proposal/memo generator
**Status:** Not started

## Why this matters
The customer proposal is one of the three artifacts v1 must produce consistent-by-construction from a single run record — the document the installer-facing commercial team sells with. It translates the underwriting into plain language for the customer: the bill before/after, the savings, the term and the commercial structure. Without it the pipeline produces numbers but nothing a customer can read, and the "two documents from one run record" guarantee (M8) is unmet. If LLM prose were allowed to author or alter any figure, the proposal would silently diverge from the underwriting that backs it.

## Requirements
- Assemble a draft customer-facing proposal from the immutable quote/run record (X.2) — no recomputation; read finance outputs from the record.
- Surface the customer-facing essentials in plain language: bill before, bill after, savings (absolute and/or %), contract term, and commercial structure (fixed £/kWh default plug-in per M5).
- Inject every number directly from the run record into the template; the LLM drafts only prose around the slotted numbers — it never originates or modifies a figure.
- Render to a readable format (HTML / Markdown / PDF — the format is a v1 implementation choice); regeneration from the same record produces identical output.
- Stamp the proposal with the provenance the record carries (assumption version, code version) so the document is traceable to the quote that produced it.
- Keep gated/wholesale upside labelled as conditional, not folded into the customer's headline savings (spec §9 RTM risk; M2 gated-stream labelling).

## Acceptance criteria
- [ ] A draft proposal generates from a valid quote/run record without manual number entry.
- [ ] Every number in the proposal matches the run record exactly (bill before/after, savings, term, structure values).
- [ ] No number in the proposal originates in LLM-drafted prose; numbers come only from injected record fields.
- [ ] The proposal regenerates identically from the same run record (deterministic).
- [ ] The proposal renders to a readable format and is legible as a customer-facing document.
- [ ] Any conditional/gated upside is labelled as such and excluded from the headline priced savings.

## Dependencies
- 006-financial-model-wrapper.md — provides the P50/P90, savings, term, and fixed £/kWh structure figures injected into the proposal.
- 009-quote-lifecycle.md — provides the immutable quote/run record (the single source of every number and its provenance) the proposal is assembled from.
- Shared contract: consumes finance outputs that derive from the mock `OptimisationResult` (005) — buildable now without the real engine.
- Assumption library (003 / X.1): version tag carried on the record and stamped onto the proposal.

## Out of scope (v1)
- Polished 2026-quality layout, animations, and a branded proposal layer (M8 Later).
- Tighter human-sign workflow beyond producing a draft for a human to sign (M8 Later).
- Pricing or headlining wholesale/P415/ancillary upside — stays labelled gated-upside (spec §5, §9).
- The underwriting memo (capital-facing) — covered by 011-output-memo-generator.md.
- CRM/proposal delivery integration — Growth's tool, not ours (spec §5).

## Suggested files to create or modify
- `gridq/outputs/proposal.py` — assemble the customer proposal from the run record; inject numbers, invoke prose drafting, render output.
- `gridq/outputs/templates/proposal.*` — plain-language template with explicit number slots filled from the record.
- `tests/test_proposal_numbers_match_record.py` — assert every proposal number equals the corresponding run-record field and that regeneration is deterministic.

> Suggested only — no application code is created by this issue document.
