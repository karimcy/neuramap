# UI-refine loop

Self-paced loop that brings the GridQ web app to a **verse.inc-grade** level of polish, flow and
storytelling — visually enticing, animated, dynamic, high-tech — **without diminishing any step of
the data-input journey**. State lives here; every iteration reads this file first and updates it.

- **Branch:** `loop/ui-refine` (branched from `loop/olly-modules`; work ONLY here; never `main`; never push).
- **Direction (decided with Olly):** Hybrid look · Adaptive motion · Full product.
  - *Hybrid* — cinematic, story-driven public landing + a calm, premium, credible underwriting flow,
    one coherent design system across both (bold dark palette + animated bg on landing/story; light
    high-trust surface in the data/finance views).
  - *Adaptive motion* — bold scroll-driven motion + animated backgrounds on landing & the run/compute
    moment; restrained, fast micro-interactions inside the data-input flow. All motion respects
    `prefers-reduced-motion` and never blocks content/interaction.
  - *Reference genre* — verse.inc: scroll-driven cinematic storytelling, kinetic typography, generous
    negative space, motion-with-purpose.
- **In scope (presentation only):** `src/gridq/app/` (templates, static CSS/JS, `view.py`),
  `src/gridq/outputs/` (html_report / proposal / viewmodel / prose), and the design system.
- **READ-ONLY (never touch):** the deterministic core — finance, optimisation, schemas *numbers*,
  scenarios, assumptions. Presentation may re-shape/label/order/animate; it must **never compute or
  change a number** (no arithmetic in templates/JS; count-ups animate toward the record value only).

## Iteration cycle

1. Read this file. Pick the highest-priority `OPEN`, `Owner = code` item. Validate it against the
   live page first (screenshot) — correct/close the row if already met.
2. Implement the smallest coherent presentation change for that one item, reusing the design system
   (no one-off styles). Add its acceptance: a Playwright visual check + a rubric score.
3. **/run + /verify** — launch the app, drive the page/flow, screenshot **desktop (1440)** AND
   **mobile (390)**, capture key animation frames; save before/after under `docs/loop/ui-shots/`.
4. **design-judge** — score against the rubric (below) + an adversarial multi-lens critique
   (top-studio · accessibility · lender-credibility · motion/performance). Fold fixes in.
5. **Quality bar — ALL must pass** (repo root): `ruff check .` · `ruff format --check .` · `mypy` ·
   `pytest` — incl. the UI/invariant guards: `test_app_smoke`, `test_app_demo`,
   `test_app_view_islands`, `test_app_charts_wiring`, `test_app_templates_no_arithmetic`,
   `test_outputs_no_arithmetic_in_html`, `test_outputs_numbers_match_record`, determinism golden.
6. Update the row (Status + rubric scores + shot paths) + a Progress-log line. Commit
   `ui(<area>): <what changed>`. Next iteration, next item.

## Design rubric (the objective "polished" bar — 0–3 each; need ≥2 on every dim, 0 fails)

1. Typography & hierarchy · 2. Layout, grid & whitespace · 3. Colour & contrast (WCAG AA) ·
4. Motion quality (purposeful, smooth, eased, reduced-motion fallback) · 5. Narrative & flow clarity
(where am I / what next / why) · 6. Process transparency (each input explains its purpose + how it
feeds the number) · 7. Design-system consistency (tokens + reused components, no one-offs) ·
8. Responsive (desktop + mobile, no horizontal scroll @390) · 9. State coverage (loading / empty /
error / long-content) · 10. Performance (no layout shift, no jank).

## Guardrails (non-negotiable)

- **Presentation only** — never invent or alter a number; every figure traces to the run record;
  the served artifact stays byte-identical to a fresh render off the record (`test_app_demo`).
- **Never trade clarity / credibility / accessibility / performance for spectacle** (the rubric
  forbids it). Lender-facing finance views stay calm and trustworthy.
- **No data-input step removed or hidden** — the walk-through is preserved and made clearer.
- Branch-only, never `main`, never push. One coherent item per commit. If a change can't meet the
  rubric + bar, revert it and log why.

## Stop condition

Every `code` item GREEN; the full flow (landing → each data-input step → the run/compute moment →
dashboard → artifacts) is cohesive, animated, and walks the user through with transparency at every
step; desktop + mobile both polished; the design-judge finds nothing major; accessibility /
responsive / reduced-motion all pass; quality bar green. Then post a final summary + before/after
gallery + the `Owner = human` punch list (final brand sign-off, real copy/imagery/asset+font
licensing, Olly's design review).

---

## Backlog (v0 seed — validate each against the live page before building)

| ID | P | Area | Item | Acceptance | Owner | Status |
|----|---|------|------|-----------|-------|--------|
| U00 | P1 | design-system | Establish the design system: colour tokens (light tool surface + dark cinematic), Inter type scale, spacing/radius/shadow, **motion tokens** (durations/easings), base components (button/card/input/badge/progress), a reusable **animated-background** component (GPU-light, reduced-motion aware), and `prefers-reduced-motion` + focus-visible baselines. Document in `docs/design/ui-system.md`. | tokens + components live in `app.css`/`base.html`; a sample page uses them; rubric dims 3,4,7,8 ≥2; bar green | code | **GREEN** ✓ (rubric all ≥2; 3/4/7/8 = 3/3/3/3) |
| U01 | P1 | landing | Cinematic, story-driven landing: hero with animated background + kinetic headline, scroll-driven sections telling the BTM/underwriting story, clear CTA into the flow. | scroll-reveals + animated bg; desktop+mobile; rubric all ≥2; bar green | code | **GREEN** ✓ (rebuilt full-bleed cinematic — `base_immersive` + `.lp-*` + 4 feature visuals; see log iter 8) |
| U02 | P2 | wizard/intake | New-quote intake (site identity) — polished, guided, transparent (what each field is for), animated progress spine + step transitions. | step retained + clearer; micro-interactions; rubric ≥2; bar green | code | **GREEN** ✓ (added purpose intro; spine + swipe transitions validated) |
| U03 | P2 | wizard/consumption | Consumption & tariff step — polished, guided, transparent; live preview/feedback where possible. | as U02 | code | **GREEN** ✓ (validated: method cards + live kWh/day note + modelling tip) |
| U04 | P2 | wizard/load-curve | Half-hourly load-curve step — polished chart + upload UX; transparent about what HH data unlocks (tiering). | as U02 | code | **GREEN** ✓ (validated: live load-curve chart + profile stats; CSV/bill dropzones) |
| U05 | P2 | wizard/equipment | Equipment & connection step — polished selection UX. | as U02 | code | **GREEN** ✓ (validated: auto-select default + catalogue selects + solar input) |
| U06 | P2 | wizard/review | Review step — clear, confident summary before the run; "here's what we'll compute". | as U02 | code | **GREEN** ✓ (validated: summary cards + load curve + gate list + "what we compute") |
| U07 | P1 | run/compute | The run/compute moment as a cinematic pipeline sequence (ingestion → dispatch MILP → finance → quote) ending in a satisfying result reveal. | animated sequence; reduced-motion fallback; bar green | code | **GREEN** ✓ (cinematic dark overlay + .gq-ambient; rubric all ≥2) |
| U08 | P1 | dashboard | Result dashboard + islands (sizing surface, finance, dispatch, utilisation, value-stack, sensitivities, upside): cohesive, premium, animated entrances, number count-ups toward record values. | islands polished + consistent; numbers trace to record; rubric all ≥2; bar green | code | **GREEN** ✓ (section rhythm + exact count-ups + 2 mobile overflow fixes) |
| U09 | P2 | artifacts | Underwriting memo + customer proposal (HTML report) — premium document styling, clear, credible. | polished report; no-arithmetic invariant holds; bar green | code | **GREEN** ✓ (rebranded report to the design system; numbers untouched) |
| U10 | P2 | global | Global nav, page transitions, status bar, and all loading/empty/error/not-found states designed. | consistent chrome + states; bar green | code | **GREEN** ✓ (reusable .state component; 404 redesigned; chrome/transitions verified) |

(Human-gated — surfaced at stop: final brand sign-off, real copy/marketing content, real imagery/
logos/photography + font licensing, and Olly's design review. The loop drives to the rubric + judge
bar; final aesthetic sign-off is Olly's.)

## Progress log

(append one line per iteration: `<iter> · <ID> · <result> · <evidence>`)

- 0 · bootstrap · branch `loop/ui-refine` created off `loop/olly-modules` (6b084d4); state file + design-system stub seeded · (no app code changed yet)
- 1 · U00 · GREEN · Established the design system: spacing (`--sp-*`), motion (`--dur-*`/`--ease-*`) and dark-cinematic (`--ink-900`/`--on-dark*`/`--glow-*`) tokens in `app.css`; consolidated repeated easing/duration literals onto the tokens; extracted the reusable `.gq-ambient` animated background (replacing the landing's one-off `.vhero__glow`, GPU-light transform/opacity, reduced-motion aware); added a11y baselines (skip-link → `#main`, focusable `<main>`, self-contained data-URI favicon → removes the `/favicon.ico` 404). Documented tokens+components in DS; guarded by `tests/test_app_design_system.py` (6 tests). Non-regressive — hero renders byte-for-byte the same look. Rubric: all dims ≥2, **3/4/7/8 = 3/3/3/3**, 0 fails. Verified: desktop 1440 + mobile 390, no horiz scroll @390, skip-link drops in on focus, count-ups reach record values. Shots: `docs/loop/ui-shots/U00-{before,after}-landing-{desktop,mobile}*.png`.
- 8 · U01-redux · GREEN · **Olly feedback: "looks exactly the same — not verse-esque at all."** The earlier U01 was too conservative (validated the existing dark-hero-card-in-the-app-shell). Rebuilt the landing as a genuinely cinematic, full-bleed story page **outside the app shell** (new `base_immersive.html`, `.lp-*` system; the sidebar stays for app pages): fixed translucent nav; a 100vh hero with an oversized **kinetic** headline + the `.gq-ambient` glow + count-up stats + scroll cue; an "opportunity" story beat; the re-skinned dark scrollytelling pipeline (Ingest→Optimise→Underwrite); a **"What you get"** section with four **inline-SVG feature visuals** (`_viz_surface` 3D sizing surface, `_viz_stack` revenue stack, `_viz_cashflow` 25-yr cashflow w/ payback marker, `_viz_proposal` the three artifacts); a market-agnostic markets section (dropped unreliable flag emojis for ISO codes + a LIVE badge); a CTA band; a footer. Added the no-JS `.reveal` safety gate. Verified desktop 1440 + mobile 390 (0 horizontal scroll across the full page), kinetic hero, pipeline advance, feature-visual reveals. **Also fixed a verification-process bug**: my bar command piped tools through `tail`, masking exit codes — a pre-existing `E501` (109-char line in the U09 report `_STYLE`) had been failing `ruff` silently since U09. Fixed the line; re-ran the bar with explicit per-tool exit codes — ruff/format/mypy/pytest **all genuinely 0**. Shots: `docs/loop/ui-shots/LP-after-{hero,features,features2,markets-cta}-desktop.png`, `LP-after-hero-mobile.png`.
- 7 · U10 · GREEN · Global chrome + states. Audited the shared chrome (sidebar nav, topbar/breadcrumbs, persistent status strip + run tabs, footer) — consistent across every page via `base.html`; every page already shares one entrance transition (`main.main` `gq-fade-up`). States: the run **loading** + **error** states are the cinematic overlay (U07); the **400** input error renders inline on the form (contextual, not a dead-end page); **empty** states use `.recent-empty`. The gap was the bare, left-aligned **404** — added a reusable centered **`.state`** component (accent-wash icon tile + heading + message + `.state__actions`) and redesigned `not_found.html` to use it (magnifier SVG, "That run isn't here", Start-a-quote / Back-to-dashboard actions). Verified desktop 1440 + mobile 390 (0 horizontal scroll); the lone console "error" is Chromium logging the page's own correct HTTP-404 status. Documented `.state` in DS. Rubric all dims ≥2 (states = 3). Shots: `docs/loop/ui-shots/U10-{before,after}-notfound.png`.
- 6 · U09 · GREEN · Rebranded the underwriting-pack / proposal HTML report (`gridq/outputs/html_report.py`) to the GridQ design system: was a navy (`#1b3a6b`) system-font doc → now Inter type, slate surfaces, **brand-orange eyebrow section headings**, firm-green (`#1f7a4d`) trust palette, a branded letterhead (orange accent rule + GridQ "G" logomark + "GridQ by Neura" wordmark), and an `@media print` block so the proposal prints clean. **Presentation-only**: every figure is untouched and still rendered by the same generator — `test_app_demo` (served report byte-identical to a fresh render off the record), `test_outputs_numbers_match_record`, `test_outputs_no_arithmetic_in_html`, and the golden hash all stay green. Fixed report mobile overflow (long sha256/quote-id provenance values now `overflow-wrap:anywhere`): page-level 0px horizontal scroll @390, wide finance tables scroll within their own `.scroll` containers. **Process note**: discovered my earlier restart pattern (`pkill -f gridq.app.app:app`) never matched `python -m gridq.app`, so prior CSS/JS/template iterations were served live-from-disk by a long-running process; Python changes (this one) need a real restart — now launching the dev server as a detached background task. Shots: `docs/loop/ui-shots/U09-{before,after}-artifacts-desktop.png`, `U09-after-artifacts-mobile.png`.
- 5 · U02–U06 · GREEN · Walked the full new-quote wizard live (Site → Consumption → Load profile → Equipment → Review) on desktop 1440 + mobile 390. The intake is already strongly built: animated progress spine (numbered dots + fill), swipe step transitions, address type-ahead + Leaflet map, method cards with active states ("Most accurate"/"Soon"), live "~kWh/day" note, a live-rendered modelled **load-curve chart** + profile stats, equipment auto-select default, and a Review summary with a "before you can run" gate list + "what we compute" note. **Only gap closed**: step 1 (Site) lacked the per-step purpose intro every other step has — added a transparent lede tying location/coordinates/connection to DNO bands, solar sim and the sizing cap (rubric dim 6 transparency + dim 7 consistency). U03–U06 validated as already-meeting the bar (no code change needed — `correct/close if already met`). Mobile: method-grid stacks 2-up, spine collapses to dots, 0 horizontal scroll across steps. Shots: `docs/loop/ui-shots/U0{2,3,4}-before-wizard-*.png`, `U02-after-wizard-step1-mobile.png`.
- 4 · U08 · GREEN · Polished the result dashboard. **Layout**: the 8 top-level cards were contiguous (0px gap, read cramped) — wrapped them in a new `.stack-lg` flow utility (24px section rhythm) so each island/section breathes (rubric dim 2). **Count-ups toward record values**: the Sized-system trio (Power/Energy/Duration) now animate up, and crucially **land on the exact record string** — `countUp` snaps the final frame to the server-formatted value via a `data-count-exact` flag (caught + fixed a real bug where JS `toLocaleString` re-rounded 124.25→124.3 vs the record's 124.2). Finance figures stay static (calm/credible per guardrail). **Fixed 2 real mobile overflow defects @390**: long `sha256` provenance hashes (now `overflow-wrap:anywhere`) and long warning tokens (`.panel` overflow-wrap) — dashboard now 0px horizontal scroll (was 206px). Drove the real demo MILP for a live quote. Verified desktop 1440 + mobile 390. Rubric: all dims ≥2 (2 & 8 were the wins). Shots: `docs/loop/ui-shots/U08-{before,after}-dashboard-{desktop,mobile}.png`.
- 3 · U07 · GREEN · Turned the run/compute moment into a cinematic dark pipeline sequence: re-themed the `.run-overlay` to the dark cinematic gradient + the reusable `.gq-ambient` animated background (injected by `run_progress.js`), a dark glass card, an on-dark progress spine (Ingest→Load profile→Solar sim→Optimise→Financials) with a pulsing active stage and a glowing accent fill; ends in the real completion ("Complete · Opening your results"). Drove the **real demo MILP** through `/api/run` (demo=1) — caught stage 4 "Optimising… dispatch MILP". A11y: `role="dialog"` + `role="status"`/`aria-live="polite"` so stage changes are announced. Verified loading, error ("Postcode is required." readable on dark + Close), desktop 1440 + mobile 390 (spine fits, no overflow). Reduced-motion: global reset freezes ambient/pulse, run still completes. Rubric: all dims ≥2. Shots: `docs/loop/ui-shots/U07-after-run-{desktop-stage0,mobile,error}.png`.
- 2 · U01 · GREEN · Validated the cinematic landing live (hero animated bg + kinetic headline, pinned 3-step scrollytelling Input→Processing→Output with progress rail/dots, scroll-reveals, hero count-ups, parallax) on desktop 1440 + mobile 390. **Fixed a real mobile defect**: the step-03 reveal chips overflowed the sticky stage's clipped edges at 390 (side chips unreadable) — added `flex-wrap` + tighter chip sizing under `max-width:700px`; now 0 chips clipped. **Perf**: rAF-throttled the pinned-scroll handler (no layout thrash). Reduced-motion path unchanged (all steps shown, parallax/scrolly off). Rubric: all dims ≥2 (8 now 3). Shots: `docs/loop/ui-shots/U01-before-landing-{scrolly-step2,lower,mobile-scrolly}.png`, `U01-after-landing-mobile-scrolly.png`. Note (Owner=human): hero/scrolly illustrative figures (16.0% IRR · 6.8 yr · 68.9%) match the demo run; flag emojis render inconsistently cross-platform → candidate for inline-SVG flags.
