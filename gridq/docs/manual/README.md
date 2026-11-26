# GridQ Platform Manual (LaTeX)

A complete user-and-rebuild manual for the GridQ behind-the-meter BESS sizing and
underwriting platform. It explains every pipeline module in plain language and in
enough detail to re-implement the platform from scratch.

- **Compiled PDF:** `GridQ-Platform-Manual.pdf` (144 pages, 17 chapters + 2 appendices).
- **Audience:** technical and commercial readers; specialist GB-market and
  project-finance terms are explained on first use and collected in Appendix A.

## Layout

| Path | What it is |
|------|------------|
| `main.tex` | Document root: title page, table of contents, chapter includes. |
| `preamble.tex` | Frozen shared preamble: brand styling, callout boxes, TikZ flow-diagram styles, the `\screenshot` helper. |
| `chapters/` | One `.tex` per chapter (`01`–`17`) plus appendices `A` (glossary) and `B` (assumption-library reference). |
| `figures/` | Screenshots captured from the live web application (used in Chapter 16). |
| `build_chapter.sh` | Compiles one chapter standalone against `preamble.tex` (useful when editing a single chapter). |

## Build

Requires a TeX Live installation with `latexmk` and the `tikz`, `tcolorbox`,
`booktabs`, `tabularx`, `listings`, `hyperref` and `seqsplit` packages.

```sh
latexmk -pdf -outdir=build main.tex     # full document -> build/main.pdf
bash build_chapter.sh chapters/09-dispatch.tex   # one chapter standalone
```

## Conventions

- The pipeline is documented in data-flow order: a raw meter file at the top of
  Chapter 4, the three finished documents by Chapter 15.
- Each module chapter carries a plain-language opening, an inputs/outputs box, at
  least one flow diagram, and a **How to rebuild this** recipe.
- Numbers are never authored by prose; British spelling throughout; no em dashes.
