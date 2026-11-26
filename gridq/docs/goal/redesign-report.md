# Cinematic frontend redesign - report

**Branch:** `goal/cinematic-redesign` (staged commits 1/6 - 6/6, left unmerged per the goal spec)
**Scope:** presentation layer only - `src/gridq/app/` templates, static CSS/JS, and two
view/route additions that *select* record fields for the new hero and payback marker.
No engine, `QuoteRecord`, route-contract or numeric-logic change. The house invariant held
throughout: **the interface formats, it never computes or invents a number.**
**Audit basis:** [redesign-audit.md](redesign-audit.md) (functional-parity checklist written first).

---

## 1. Before / after

| | Before | After |
|---|---|---|
| Landing | [docs/loop/ui-shots/DEPLOY-live-landing-cinematic.png](../loop/ui-shots/DEPLOY-live-landing-cinematic.png) (indigo/orange gradient hero, hand-drawn SVG surface) | [redesign-shots/landing-1440-hero.png](redesign-shots/landing-1440-hero.png) - live WebGL return surface from the newest solved run, pulsing beacon at the real optimum |
| Landing features | (same page, static cards) | [redesign-shots/landing-1440-features.png](redesign-shots/landing-1440-features.png) - mono eyebrows, console-cap panels, draw-in mesh |
| Run page | [docs/manual/figures/app-run.png](../manual/figures/app-run.png) (white cards on slate, hand-mirrored chart palette) | [redesign-shots/run-1440-top.png](redesign-shots/run-1440-top.png), [run-1440-surface.png](redesign-shots/run-1440-surface.png), [run-1440-finance.png](redesign-shots/run-1440-finance.png) |
| Mobile / tablet | - | [landing-390.png](redesign-shots/landing-390.png), [run-390.png](redesign-shots/run-390.png), [run-768.png](redesign-shots/run-768.png) (no horizontal scroll at any width) |
| Reduced motion | - | [landing-1440-reduced.png](redesign-shots/landing-1440-reduced.png) (counts landed, reveals settled, hero = one static frame) |

The old surface was two design languages (light EASOS app + dark immersive landing) with a
chart palette duplicated by hand in `charts.js`. The new surface is ONE dark system consumed
from ONE token file by CSS and JS alike.

## 2. Design system (single source: `static/tokens.css`)

- **Canvas / surfaces:** `--bg #0e0e10`, `--bg-deep #0a0a0c`, `--surface-1 #131316`,
  `--surface-2 #18181c`, `--surface-3 #1e1e23`. Never pure black.
- **Elevation:** hairline borders (`--line rgba(255,255,255,.07)`, `--line-strong .12`,
  `--line-faint .045`) + faint top gradient `--grad-panel`. **No drop shadows on dark.**
- **Text:** `--text #ececee`, `--text-2 #a6a6ad`, `--muted #85858e` (5.1:1 on `--bg` - AA at
  any size; raised from #77777f after Lighthouse flagged 4.34:1).
- **The ONE accent:** electric amber `--accent #ffb454` (+ `-strong/-ink/-wash/-line`).
  Semantic colour is reserved: `--ok #4ade80` strictly for pass/covenant-met/firm,
  `--err #f87171` strictly for fail/error, `--gated #8d8d96` neutral grey (deviation note
  below), `--warn #e8c468` for indicative notices, `--cool #7d8cf8` for charge direction in
  charts (directional, never semantic).
- **Type:** self-hosted variable fonts (no CDN) - Inter Variable for prose, JetBrains Mono
  Variable for **every number, unit and data label** (`.metric`, `.kv dd`, `td.num`,
  `.stat__num`, badges, eyebrows, statusstrip, chart axes via `GridQTheme`). Uppercase
  eyebrows at `--eyebrow-tracking: 0.08em`, mono, "01 · SIZING" pattern kept.
- **Space / shape / layout:** `--sp-1..8`, radii 6/10/16px + pill, `--maxw 1200px`.
- **Motion:** `--dur-fast .15s / --dur-base .45s / --dur-slow .8s`,
  `--ease-spring cubic-bezier(0.16, 1, 0.3, 1)` (the spec curve, named once - a guard test
  asserts the literal appears exactly once and the old curve nowhere), `--reveal-shift 18px`.
- **JS reads the same tokens:** `static/theme.js` (`GridQTheme.tokens()/returnScale()/rampFirm()/rampUpside()`)
  reads live values off `:root`; `charts.js` and `wizard.js` carry no colour literals. The
  audit's "palette drift" risk is structurally closed, and a new test
  (`test_app_css_defines_no_tokens_of_its_own`) fails any raw colour smuggled into app.css.

## 3. Cinematic hero (landing)

`view.hero_surface_island()` projects the newest solved run's persisted sizing cloud (the
same island the results page uses - dense mesh + selected optimum, field selection only)
into a `hero-surface-data` island. `static/hero.js` (ES module) lifts it into a Three.js
surface: BufferGeometry with a charcoal-to-amber height ramp, slow autonomous camera drift,
pulsing beacon at the **real** chosen optimum (165.2 kW × 3.1 h @ 10.3% levered on the
current deploy).

Guard rails, in order:
1. No island (no solved run) → the static SVG fallback simply stays. Nothing invented (F-011).
2. Viewport < 880px → SVG (the art is below the fold on phones; no 690KB parse on battery).
3. No *hardware* WebGL (`failIfMajorPerformanceCaveat` + unmasked-renderer check for
   SwiftShader/llvmpipe) → SVG.
4. Otherwise: Three.js is **dynamically imported after window load + an idle slot**, inits
   when the mount nears the viewport, pixel ratio ≤ 2, pauses off-screen and on
   `document.hidden`, one static frame under `prefers-reduced-motion`, stops on context loss.

## 4. Console panels + motion discipline

- Every run panel: hairline console header + pulsing status dot + `engine · solved` mono
  readout. The dot pulse and the hero loop are the only perpetual animations.
- `static/motion.js` is the **single IntersectionObserver** for all scroll triggers:
  `[data-reveal]` fade-ups (18px / 450ms / spring, 70ms group stagger), count-ups, and a
  `watch(el, cb)` hook the charts use. Once-only via WeakSet; instant final state under
  reduced motion; `html:not(.js)` keeps everything visible with no JS. `dashboard.js` shed
  its duplicate observers and keeps only scroll-position work (pinned pipeline, nav, parallax).
- Count-ups animate the **presentation** of server-formatted strings and land verbatim on
  them (`data-count-exact` splits around the first numeral so units never move).
- Charts: cashflow is a draw-on-scroll area chart (P50 filled amber, P90 dotted green) with
  the record's payback year marked (field passed through the island); the value stack's firm
  greens / gated ambers grow to their record values on first view; axes are pinned to the
  real series extent so growth plays inside final axes.

## 5. Performance

| Measure | Result |
|---|---|
| Landing JS, gzipped (Three r170 + hero + motion + theme + dashboard) | **174 KB** (budget < 250 KB; Three alone 167 KB) |
| Lighthouse 11.7, desktop preset | **99 perf / 98 a11y / 100 best-practices**, TBT 0ms |
| Lighthouse 11.7, mobile preset | **96 perf / 98 a11y / 100 best-practices**, TBT 50ms |
| CLS | **0** (mobile), 0.008 (desktop) - fixed chart/hero container heights, font preload + swap |
| FCP / LCP (mobile, 4x throttle, on the 2-vCPU VPS) | 1.8s / 2.4s |

Enablers: `GZipMiddleware` added to `create_app` (the 53KB landing HTML ships at 19KB; the
60KB app.css at 12KB); fonts preloaded; the hero module parse deferred past the interactive
window. Measured against the local preview on the production VPS - a shared 2-vCPU box with
software GL; on real hardware the numbers only improve. Plotly (4.5MB vendored, run pages
only) predates the redesign and is unchanged.

## 6. Verification (all gates)

- **Functional parity:** every route in the audit renders 200; islands, div ids,
  `GridQCharts.init(` contract, copy pins ("Sharpen this quote", "NOT BANKABLE"), skip-link,
  `#main` focus, favicon - pinned by the test suite (`test_app_smoke`, `test_app_charts_wiring`,
  `test_app_design_system`, `test_app_view_islands`, `test_app_demo`) - full suite green with
  `PYTEST-EXIT:0` captured from pytest itself.
- **No hardcoded figures:** the no-arithmetic AST guard + em-dash sweep pass; every number on
  every screen still renders from the stamped record via `fmt_*`.
- **New smoke tests:** hero mount + SVG fallback + island-absent-when-no-run honesty;
  console chrome + `data-reveal` wiring on a run page.
- **Screenshots:** 390 / 768 / 1440 reviewed (no horizontal scroll after fixing a grid
  min-width overflow at 768); reduced-motion re-screenshot.
- **Console:** 0 errors on landing and run pages (headless Chrome; only driver-noise warnings).
- **Keyboard:** first Tab = skip-link, Enter lands focus on `#main`; runtabs/details preserved.
- **Contrast:** WCAG AA - Lighthouse contrast audit passes after the `--muted` bump.

## 7. Dependency delta

| Added (vendored, no CDN) | Size |
|---|---|
| `static/vendor/three/three.module.min.js` (r0.170.0) | 692 KB raw / 167 KB gz |
| `static/vendor/fonts/inter-latin-wght-normal.woff2` (fontsource 5.1.0) | 47 KB |
| `static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2` (fontsource 5.1.0) | 39 KB |

No package.json, no build pipeline, no SPA framework - vanilla JS modules only. Python deps unchanged.

## 8. Deviations from the spec, with reasons

1. **Gated upside is neutral grey, not a second accent.** The spec reserves green/red for
   pass/fail + firm/gated; gated upside is *conditional*, not negative - grey keeps red
   unambiguous for true failures. (Gated *chart segments* use the amber ramp so the stack
   reads firm-vs-conditional at a glance; the amber accent also marks "conditional value".)
2. **WebGL hero is desktop-only (≥880px) and hardware-GL-only.** On phones the art is below
   the fold; under software rasterisation a render loop burns CPU for nothing. The static
   SVG is the designed fallback, not an error state.
3. **"engine · solved" rather than the spec's "engine · active" microcopy.** These panels
   render a stamped, immutable record - "active" would imply a live feed that does not
   exist. Honest-labelling policy wins over copy aesthetics.
4. **Count-ups run once per element** (WeakSet) even though panels re-enter the viewport -
   the spec's "nothing animates twice" applied to counts as well as reveals.
5. **`GZipMiddleware`** is app plumbing, not presentation - added anyway because the
   performance budget is measured over the wire and the middleware touches no logic.

## 9. Follow-ups (not blocking)

- The orphans flagged in the audit remain orphans: `templates/demo.html` (unrouted),
  `static/changes-2026-07.html`, the dead `/equipment/upload` link.
- `docs/manual/figures/app-*.png` (the manual's screenshots) now show the previous design;
  re-shoot when the manual is next rebuilt.
- Mobile FCP is TTFB-bound on the shared VPS; if it matters, cache the landing's hero-island
  JSON (it re-reads the newest run per request).
