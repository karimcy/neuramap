# UI-refine loop — final state

**Status: COMPLETE.** Every `Owner = code` backlog item (U00–U10) is GREEN; the full flow
(landing → data-input → run/compute → dashboard → artifacts) is one cohesive, animated, transparent
product on desktop (1440) and mobile (390); the quality bar is green; design-judge found no majors.
Branch `loop/ui-refine`, 7 iteration commits on top of the bootstrap. Nothing pushed; `main` untouched.

> Re-read `ui-refine-loop.md` (the working log: per-iteration result, rubric scores, evidence) and
> `../design/ui-system.md` (the design system: tokens + components) alongside this file.

## What shipped

**Direction delivered — Hybrid look · Adaptive motion · Full product.** A bold dark, animated,
cinematic public face (landing + the run/compute moment) and a calm, premium, trustworthy light
surface for the data-input and finance/underwriting views — unified by one token set.

| ID | Area | Outcome |
|----|------|---------|
| U00 | design-system | Token layer (spacing `--sp-*`, motion `--dur-*`/`--ease-*`, dark-cinematic `--ink-900`/`--on-dark*`/`--glow-*`); reusable `.gq-ambient` animated background; a11y baselines (skip-link, focusable `#main`, self-contained favicon); documented in the DS; guard test. |
| U01 | landing | Validated the cinematic landing (animated-bg hero, kinetic headline, pinned Input→Processing→Output scrollytelling, reveals, count-ups, parallax); fixed a mobile reveal-chip clipping defect; rAF-throttled the scroll handler. |
| U02–U06 | wizard | Validated the full intake (progress spine, swipe transitions, map type-ahead, method cards, live load-curve, review gate); added the missing step-1 purpose intro for transparency + consistency. |
| U07 | run/compute | Re-themed the compute moment into a cinematic dark pipeline sequence reusing `.gq-ambient` (glass card, on-dark stage spine, pulsing active stage, glowing fill); `role="dialog"`/`aria-live` status; real completion + error states. |
| U08 | dashboard | Section rhythm (`.stack-lg`, was contiguous cards); count-ups that land on the **exact** record value (no JS re-rounding); fixed two real mobile horizontal-overflow defects (provenance hashes, warning tokens). |
| U09 | artifacts | Rebranded the underwriting-pack / proposal HTML report to the design system (Inter, brand-orange eyebrows, firm-green trust, branded letterhead, print stylesheet); numbers untouched & all output invariants green. |
| U10 | global | Reusable `.state` empty/error/not-found component; redesigned the 404; verified consistent chrome + page transitions + loading/empty/error states. |

## Invariants held (deterministic-core boundary)

- **Presentation-only throughout.** No core / finance / optimisation / schema / scenario / assumption
  files were touched. Every figure still traces to the stamped `QuoteRecord` via `gridq.outputs`.
- Count-ups animate **toward** record values and snap to the exact server-formatted string
  (`data-count-exact`) — the UI never re-rounds or authors a number.
- No arithmetic in templates/JS on figures; the served report stays byte-identical to a fresh render
  off the record; the determinism golden hash is unchanged.
- Quality bar green: `ruff check` · `ruff format --check` · `mypy` · `pytest` (incl. app_smoke,
  app_demo, app_view_islands, app_charts_wiring, app_templates_no_arithmetic,
  outputs_no_arithmetic_in_html, outputs_numbers_match_record, determinism golden, + a new
  `test_app_design_system`).
- Accessibility: skip-link → focusable `#main`, visible focus rings, AA contrast, `aria-live` run
  status, decorative motion `aria-hidden`. Responsive: 0 horizontal scroll @390 on every page.
  `prefers-reduced-motion`: global CSS reset + JS guards (instant reveals/count-ups, no scrolly/parallax).

## Before / after gallery (`docs/loop/ui-shots/`)

- **Landing** — `U00-before-landing-{desktop,mobile}.png` → `U00-after-landing-{desktop,mobile}-hero.png`;
  scrollytelling `U01-before-landing-scrolly-step2.png`, mobile chip fix
  `U01-{before,after}-landing-mobile-scrolly.png`.
- **Wizard** — `U02-before-wizard-step1-desktop.png`, `U03-before-wizard-step2.png`,
  `U04-before-wizard-step3.png` → `U02-after-wizard-step1-mobile.png`.
- **Run/compute** — `U07-after-run-desktop-stage0.png`, `U07-after-run-mobile.png`,
  `U07-after-run-error.png` (new cinematic dark sequence; no prior equivalent).
- **Dashboard** — `U08-before-dashboard-desktop.png` → `U08-after-dashboard-{desktop,mobile}.png`.
- **Artifacts** — `U09-before-artifacts-desktop.png` → `U09-after-artifacts-{desktop,mobile}.png`.
- **404 state** — `U10-before-notfound.png` → `U10-after-notfound.png`.

## Owner = human (gated — surface to Olly)

1. **Brand sign-off / final aesthetic review (Olly).** The loop drove to the rubric + judge bar;
   the final look is Olly's call. Review the gallery + the live app.
2. **Real copy / marketing content.** Landing headline, section blurbs, and the wizard intros are
   engineering placeholders — replace with approved marketing copy.
3. **Illustrative landing figures.** The hero/scrollytelling numbers (16.0% IRR · 6.8 yr · 68.9%)
   are the demo run's values used as illustrative marketing; confirm they're acceptable as public
   example figures (they are not a live quote).
4. **Imagery / logos / iconography.** Real GridQ/Neura logo + any photography. Note: country **flag
   emojis** render inconsistently cross-platform (boxes on some OSes, letter-pairs on Windows) —
   replace with inline-SVG flags for reliability.
5. **Font licensing.** The UI specifies **Inter** but ships no font file (system fallback only) —
   decide whether to self-host Inter (and clear its OFL licence) or keep the system-font fallback.
6. **Asset licences.** Vendored Leaflet/Plotly and OpenStreetMap tiles — confirm attribution/licence
   terms for production use.

## Notes for the next maintainer

- Dev server: `python -m gridq.app` (host/port via `GRIDQ_APP_HOST`/`GRIDQ_APP_PORT`). CSS / JS /
  template edits are read from disk per request, **but** the browser cache-bust `?v=` is fixed at
  startup — restart the server after editing static assets so the `?v=` bumps. Python changes
  (e.g. `outputs/html_report.py`) always require a restart.
- The reusable `.gq-ambient` background and the dark palette are the seam for any future cinematic
  surface; the `.state` component is the seam for any future empty/error page.
