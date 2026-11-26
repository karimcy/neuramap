# Outputs (M8) — underwriting pack · customer proposal · HTML report

`gridq.outputs` turns **one stamped `QuoteRecord`** into the three artifacts the System Brief
requires to be *consistent by construction* (Outputs 1–3): the capital-facing **underwriting
pack**, the customer-facing **proposal**, and a self-contained **HTML report** that combines them.
It is the M8 deliverable; see issues [011](issues/011-output-memo-generator.md) (memo) and
[012](issues/012-output-proposal-generator.md) (proposal).

> **It renders the record — it never authors a number.** Every figure is injected from the run
> record; a figure that does not exist degrades to an explicit *unavailable* / *placeholder* label
> that names why; the HTML generator does no arithmetic; and any prose is drafted LLM-free in v1.

## The hard rules (and how they are enforced)

| Rule | How it holds | Test |
|------|--------------|------|
| Numbers come only from the record | `viewmodel.py` extracts/classifies/aggregates values already on the `QuoteRecord`; renderers only place them | `test_outputs_numbers_match_record.py` |
| No invented values | a missing figure is `None` → rendered as `formatting.unavailable(reason)`, never a 0/guess | `test_outputs_generate.py` |
| Placeholders labelled, baseline non-bankable | a NOT-BANKABLE banner + every warning surfaced; placeholder figures (e.g. the covenant) flagged | `test_outputs_sections_and_provenance.py` |
| Provenance shown | input hash + assumption/code/schema versions + expiry in every artifact | `test_outputs_sections_and_provenance.py` |
| No arithmetic in HTML generation | `html_report.py` only formats via `formatting`; AST scan forbids arithmetic `BinOp`/`sum`/`round`/… | `test_outputs_no_arithmetic_in_html.py` |
| LLM (if any) drafts prose only | `prose.ProseDrafter` methods take **categorical inputs only** (never a £-figure); v1 default is deterministic | `test_outputs_numbers_match_record.py::test_no_record_figure_leaks_into_prose` |
| Deterministic | no wall-clock in artifacts (time comes from the record); regeneration is byte-identical | `test_outputs_numbers_match_record.py::test_regeneration_is_byte_identical` |

## Architecture — extract → format → render

```
QuoteRecord (+ optional SiteModel)
        │  viewmodel.build_view_model   ← the only reader of the record
        ▼
   OutputViewModel  (typed, render-ready; classification + aggregation of existing values)
        │  formatting.*                 ← the only place a number becomes a string
        ▼
underwriting.render_underwriting_memo  → memo (Markdown)
proposal.render_customer_proposal      → proposal (Markdown)
html_report.render_html_report         → report (self-contained HTML)
        ▲
   prose.ProseDrafter (categorical-only narrative; LLM-free default)
```

`generate.generate_artifacts(quote, site=None)` runs all three from one view model;
`generate.generate_for_run(run)` unpacks a `QuoteRunResult`. The strict separation is what makes
the rules *mechanically* true: arithmetic (incl. £k scaling) lives only in `formatting`, so the
renderers — especially `html_report` — contain none.

## What each artifact contains (Output 2 / Output 3)

**Underwriting pack** (`underwriting.py`, capital-facing): bankability banner · system spec
(P/E/duration) · **P50/P90 cashflows** (year-by-year) · **DSCR** + covenant + DSCR-by-year
trajectory + sensitivities slot · **contracted-vs-merchant split** (firm/contracted floor vs
conditional + gated upside, each with its unlock condition) · **price to target** + returns ·
debt assumptions · **counterparty credit** · consents & gate status · **provenance** · warnings.

**Customer proposal** (`proposal.py`, plain language): your system · your offer (structure, term,
validity) · **bill before/after + savings** · what's secured (contracted floor) vs what's upside
(conditional/gated, kept out of the headline) · next steps.

**HTML report** (`html_report.py`): one self-contained page (inline CSS, no external assets) with a
colour-coded banner that shows the pack and proposal essentials together.

## What is real vs labelled-pending in v1

The honest v1 state on `gb_v0_1` (all placeholders) + the baseline engine is that **most figures
are unavailable / not-bankable**, and the artifacts say so by name rather than inventing:

- **DSCR / debt** — *unavailable*: no per-site debt structure (gearing/rate/tenor come from the M9
  credit→rate/tenor mapping, BLOCKERS **B001**). The machinery activates the moment real
  `DebtTerms` are injected.
- **Price to target** — *unavailable* without a dispatch energy volume (an M3 output).
- **DSCR sensitivities** — the DSCR-by-year trajectory and the P50→P90 basis are shown from the
  record; a Low/Central/High scenario grid is a labelled-pending slot that the generator renders
  when `sensitivities` rows are passed (e.g. from the scenario engine M6).
- **Bill before / after / savings** — *pending*: there is **no customer-bill model** in v1, so the
  proposal surfaces this as a labelled seam with a data ask (never an estimate). The additive
  `FinancialCase` fields to land it are raised for the finance lane in OPEN_COMMENTS **C017**.
- **Counterparty / consents / gates** — the `QuoteRecord` does not embed the `SiteModel` (only
  `site_id`), so these sections need the `SiteModel` passed alongside the quote; without it they
  render *site context not supplied* rather than assuming anything (see OPEN_COMMENTS **C018**).

## Usage

```python
from gridq.outputs import generate_artifacts, generate_for_run

# from a QuoteRecord (+ optional SiteModel for the site-context sections)
arts = generate_artifacts(quote, site)
arts.memo_markdown, arts.proposal_markdown, arts.report_html

# from a finished pipeline run
arts = generate_for_run(run_result)
```

CLI: `gridq run SITE.json --out runs/demo` writes `memo.md`, `proposal.md`, `report.html`
alongside `quote.json`; `gridq render runs/demo/quote.json [--site SITE.json]` regenerates the
three documents from a saved record (identical output).

## Tests

`tests/test_outputs_generate.py` · `tests/test_outputs_numbers_match_record.py` ·
`tests/test_outputs_sections_and_provenance.py` · `tests/test_outputs_no_arithmetic_in_html.py`.
They exercise (a) the real baseline pipeline quote, (b) the minimal committed example record (null
result + finance — must not raise), and (c) a clearly-labelled **synthetic illustrative** quote
(`rich_quote` fixture in `tests/conftest.py`) whose populated `FinancialCase` lets the full pack be
verified against record-derived numbers.
