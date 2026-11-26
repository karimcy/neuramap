# 011 · Underwriting memo generator

**Owner:** Oli
**Module(s):** M8 (memo)
**Milestone:** Milestone 6 — Outputs & proposal/memo generator
**Status:** Not started

## Why this matters
The underwriting memo is the capital-facing artifact of the three the system produces consistent-by-construction: the document a capital/investor reader uses as the falsifiable cost-compression evidence. It is where P50/P90, DSCR, the firm-vs-conditional stream split, credit, consents and gate status are read by a human who underwrites the deal — so if any figure is authored by the LLM rather than injected from the run record, the whole traceability claim collapses. Because it builds on the mock `OptimisationResult` (005) flowing through the financial model (006) and the quote record (009), it can be built in full now without waiting on the real engine.

## Requirements
- Assemble the memo entirely from the quote record (009), which wraps the immutable run record (provenance spine, X.2): the LLM drafts prose only; every number is injected from the record.
- Include the capital-facing figures the spec names for the memo: P50/P90 cashflows, DSCR, the firm-vs-conditional (priced/contracted vs gated/conditional) stream table, counterparty credit, consents, and gate/pass-fail status.
- Build the firm-vs-conditional stream table from `value_by_stream` in the `OptimisationResult` (005), respecting each stream's `priced` | `contracted` | `gated/conditional` tag and listing the unlock condition for conditional/gated streams — never pricing a gated stream into the firm case.
- Source DSCR, P50/P90 and the credit-driven rate/tenor from the financial model (006); do not recompute or re-author any number in the generator.
- Show the gate status and data tier from the validated `SiteModel` / quote record so the reader sees what was checked and what failed.
- Stamp the memo with the provenance the record carries (assumption version, code version, seed) so the reader can tie the document to the run that produced it.
- Render to a readable format (HTML/Markdown/PDF is a v1 implementation choice — the spec specifies only "one document from the run record").
- Be deterministic in its numbers: regenerating from the same record yields byte-identical figures (prose phrasing may vary, but no figure may).

## Acceptance criteria
- [ ] Every number in the memo matches the run record exactly.
- [ ] No number in the memo originates in LLM-drafted prose — all figures are injected from the record.
- [ ] The memo regenerates with identical numbers from the same run record.
- [ ] The memo includes P50/P90, DSCR, a firm-vs-conditional stream table, counterparty credit, consents, and gate status.
- [ ] Gated/conditional streams appear in the conditional column with their unlock condition and are not counted in the firm/priced case.
- [ ] The memo records the assumption version, code version and seed from the run record.
- [ ] A test asserts memo numbers equal the corresponding run-record values (figures fail the build if they diverge from the record).

## Dependencies
- 005-mock-optimisation-contract.md — the schema-valid `OptimisationResult` (`value_by_stream` with priced/contracted/gated tags + unlock conditions) that backs the firm-vs-conditional table; building on the mock means the memo does not wait on Olly.
- 006-financial-model-wrapper.md — P50/P90, DSCR, credit-driven rate/tenor, and price-to-target figures injected into the memo.
- 009-quote-lifecycle.md — the quote record (wrapping the X.2 run record) that the memo is assembled from, including data tier, gate/consent status and lifecycle state.
- Shared contract: the `OptimisationResult` schema (M3/M4 → M8) ratified at Milestone 1, and the assumption-library version (003) carried on the record.

## Out of scope (v1)
- Polished 2026-quality layout, animations, or a branded layer — Later.
- A tighter human-sign / approval workflow — v1 produces the draft; a human signs out of band.
- The customer-facing proposal — that is issue 012 (M8 proposal); this issue covers the memo only.
- Real production MILP / real dispatch — the memo is generated against the mock `OptimisationResult` only.
- Real Creditsafe-sourced credit or research-agent narrative — credit is the deterministic stub mapping from 006.

## Suggested files to create or modify
- `gridq/outputs/memo.py` — assemble the underwriting memo from the quote/run record; inject every number, hand prose-only slots to the LLM, render to the chosen format.
- `gridq/outputs/templates/memo.*` — prose template with explicit number slots filled from the record (no figure authored in-template).
- `tests/test_memo_numbers_match_record.py` — assert every memo figure equals its run-record value and that regeneration is numerically identical.
> Suggested only — no application code is created by this issue document.
