# GridQ UI design system

The shared design contract for the `loop/ui-refine` work (see `docs/loop/ui-refine-loop.md`).
**Hybrid** direction: a cinematic, story-driven public landing + a calm, premium, credible
underwriting flow, unified by one token set. **Adaptive** motion: bold on landing/story, restrained
inside the data-input flow; everything respects `prefers-reduced-motion`.

> Status: **live — established in U00.** Tokens + base components live in
> `src/gridq/app/static/app.css` and are wired through `base.html`. Do not invent one-off styles:
> add a token/component here first, then use it. The landing (`/`) is the reference page that
> exercises the dark palette, motion tokens and the animated-background component.

## Principles

- Motion with purpose; never blocks content or interaction; always a reduced-motion fallback.
- Every number on screen traces to the run record — the UI formats, it never computes.
- Lender-facing finance/data views stay calm and trustworthy; spectacle lives on the landing/story.
- One component set; no page-local CSS hacks. New work reuses the tokens below.

## Tokens

All tokens are CSS custom properties on `:root` in `app.css`. Reference them with `var(--token)`;
never hard-code a value that a token already names.

### Colour

**Light tool surface** (data/finance views — the calm, trusted surface):

| Role | Token | Value |
|------|-------|-------|
| Page background | `--paper` | `#f8fafc` |
| Raised fill | `--raised` | `#f1f5f9` |
| Hairline / divider | `--stone` | `#e5e7eb` |
| Input / strong border | `--stone-deep` | `#cbd5e1` |
| Faint card border | `--line-soft` | `#f3f4f6` |
| Heading ink | `--ink` | `#1a1a2e` |
| Body text | `--body` | `#374151` |
| Secondary text | `--muted` | `#6b7280` |
| Tertiary text | `--muted-2` | `#9ca3af` |
| Brand accent (action) | `--accent` | `#ff6b35` |
| Accent hover/pressed | `--accent-soft` | `#e85d2a` |
| Accent wash | `--accent-wash` | `rgba(255,107,53,0.10)` |

**Semantic status** — colour is meaningful, never decorative. `not_bankable` is the honest normal
state (neutral); red (`--error`) is reserved for true validation errors.

| Role | Token | Value |
|------|-------|-------|
| Firm / priced floor / feasible | `--firm` / `--firm-wash` | `#1f7a4d` / `#ecfdf5` |
| OK | `--ok` / `--ok-wash` | `#16a34a` / `#ecfdf5` |
| Indicative / caution | `--indicative` / `--indicative-wash` | `#b45309` / `#fffbeb` |
| Error | `--error` / `--error-wash` / `--error-line` | `#b91c1c` / `#fef2f2` / `#fecaca` |

**Dark cinematic palette** (landing/story + the run/compute moment — bold surface):

| Role | Token | Value |
|------|-------|-------|
| Deepest canvas | `--ink-900` | `#14142a` |
| Mid canvas | `--ink-800` | `#25253f` |
| Warm shadow stop | `--ember-900` | `#3a2418` |
| Text on dark | `--on-dark` | `#ffffff` |
| Muted on dark | `--on-dark-muted` | `rgba(255,255,255,0.78)` |
| Faint on dark | `--on-dark-faint` | `rgba(255,255,255,0.62)` |
| Hairline on dark | `--on-dark-line` | `rgba(255,255,255,0.12)` |
| Fill on dark | `--on-dark-fill` | `rgba(255,255,255,0.06)` |
| Warm accent text on dark | `--on-dark-accent` | `#ffb59a` |
| Energy glow (warm) | `--glow-accent` | `rgba(255,107,53,0.50)` |
| Energy glow (cool) | `--glow-cool` | `rgba(59,130,246,0.32)` |

Contrast: on-dark text (`#fff`, `0.78`, `0.62`) over the `#14142a–#3a2418` gradient and the light
surface inks all clear **WCAG AA** for their sizes; `--muted` (`#6b7280`) on `--paper` is the floor
(≈ 4.8:1). Don't introduce greys lighter than `--muted` for body-sized text.

### Type

`--sans` (Inter, with a full system fallback stack) everywhere; `--mono` for code/figures detail.
Headings are 700 weight, `letter-spacing: -0.012em`, line-height `1.2`.

| Step | Element | Size |
|------|---------|------|
| Display (hero) | `.vhero h1` | `clamp(2.1rem, 5.2vw, 3.6rem)` |
| Section title | `.section-head h2` | `clamp(1.5rem, 3.5vw, 2.1rem)` |
| h1 | `h1` | `1.8rem` |
| h2 | `h2` | `1.3rem` |
| h3 | `h3` | `1.02rem` |
| h4 | `h4` | `0.9rem` |
| Body | `body` | `15px / 1.6` |
| Small | `small` | `0.82rem` |

Figures use `font-variant-numeric: tabular-nums` so count-ups don't reflow.

### Space, radius, shadow

- **Spacing scale** (4px base): `--sp-1` `4` · `--sp-2` `8` · `--sp-3` `12` · `--sp-4` `16` ·
  `--sp-5` `24` · `--sp-6` `32` · `--sp-7` `48` · `--sp-8` `72`. Use these for new layout.
- **Radius:** `--radius` `12px` · `--radius-sm` `8px` · `--radius-pill` `9999px`.
- **Shadow:** `--shadow` (hairline lift) · `--shadow-lg` (hover/overlay). Light surface is flat by
  default (cards have borders, not drop shadows); shadow signals interactivity/elevation only.

### Motion

Durations and easings are tokens so motion reads as one system, not per-element guesses.

| Token | Value | Use |
|-------|-------|-----|
| `--dur-fast` | `0.15s` | micro-interactions (hover, press, nav) |
| `--dur-base` | `0.34s` | page entrance, wizard step transitions |
| `--dur-slow` | `0.7s` | scroll-reveals, cinematic entrances |
| `--ease-standard` | `cubic-bezier(0.4,0,0.2,1)` | general transitions |
| `--ease-spring` | `cubic-bezier(0.22,0.61,0.36,1)` | settle/overshoot — reveals, swipes |
| `--ease-out` | `cubic-bezier(0.16,1,0.3,1)` | decelerate-in entrances |

**Reduced motion:** a global `@media (prefers-reduced-motion: reduce)` rule collapses every
animation/transition to ~0ms, and the JS (`dashboard.js`) shows reveals/count-ups instantly and
skips the pinned scrollytelling + parallax. Motion is never required to read or use a page.

## Components

Base components, all token-driven and reused across screens (no page-local variants):

- **button** — `.btn` (accent, primary action) · `.btn.secondary` (outline) · `.btn.big` (hero).
- **card** — `.card` (flat, hairline border, `--radius`); `.card-head` for a title + action row.
- **input / field** — `.field` (label + `.hint` + control); `fieldset`/`legend` group steps;
  focus ring = `--accent` with `--accent-wash` glow.
- **badge / tag** — `.badge` + a status modifier (`-firm` / `-gated` / `-indicative` / `-ok` /
  `-neutral` / `-warn` / `-demo`); colour matches the semantic token, with a `.dot`.
- **progress spine** — wizard: `.wiz-steps` + `.wiz-track`/`.wiz-fill`; run/compute: `.run-track`/
  `.run-fill` + `.run-step`; scrollytelling: `.pin-rail`/`.pin-fill`.
- **section** — `.section-head` (centred eyebrow + title + lede) for story sections.
- **animated background** — `.gq-ambient` (see below).
- **state** — `.state` (centered icon + heading + message + `.state__actions`): the one pattern for
  empty / not-found / error pages (e.g. the 404). Icon sits in an `.state__icon` accent-wash tile.
- **chart frame** — `.surface-canvas` / `.chart-canvas`, each with a `.surface-cap`/fallback table.
- **table** — `table.data` (uppercase muted headers, hairline rows, `.num` for tabular figures).
- **skip-link** — `.skip-link` (in `base.html`): visually hidden until focused, jumps to `#main`.

### Animated background (`.gq-ambient`)

A reusable, GPU-light animated background for dark surfaces (landing hero now; the run/compute
moment next). Markup — first child of a `position:relative; overflow:hidden` dark surface:

```html
<span class="gq-ambient" aria-hidden="true">
  <span class="gq-ambient__orb"></span>
  <span class="gq-ambient__orb two"></span>
</span>
```

Two blurred glow orbs (warm `--glow-accent` + cool `--glow-cool`, the energy/behind-meter motif)
float on a slow `transform`/`opacity` loop only — compositor-friendly, no layout work, no jank. It is
`aria-hidden` (decorative) and freezes under reduced motion via the global reset. Keep it subtle and
behind content (`z-index:-1`); it must never reduce text contrast below AA.

## Accessibility & state baselines

- **Focus:** every interactive element gets a visible `:focus-visible` ring (`--accent`, 2px).
- **Keyboard:** skip-link first in tab order; `#main` is focusable (`tabindex="-1"`).
- **Reduced motion:** honoured globally (CSS reset + JS guards), per *Motion* above.
- **States** to design for each screen: loading, empty, error, long-content (rubric dim 9).

## Immersive landing (`body.immersive` / `.lp-*`)

The public landing is a **full-bleed cinematic page outside the app shell** (no sidebar) — `base_immersive.html`
instead of `base.html`. It is all-dark (the hybrid: bold cinematic landing, calm light app behind the CTAs)
and built on the dark palette + motion tokens + `.gq-ambient`. Namespace: `.lp-*`.

- **Shell** — `body.immersive` (dark canvas) + `.lp-shell` (centred max-width). Fixed translucent `.lp-nav`
  that solidifies on scroll (`.scrolled`, toggled by `dashboard.js`).
- **Hero** — `.lp-hero` (100vh, layered radial-gradient glows + `.gq-ambient`); oversized kinetic headline
  `.lp-hero__title` with per-line `.lp-line` rise (CSS-animated, so it works with JS off; reduced-motion
  freezes it); `.lp-stats` count-up tiles; `.lp-scrollcue`.
- **Sections** — `.lp-section` (+`--alt` band), `.lp-section__head`/`.lp-h2`/`.lp-lead`, `.lp-eyebrow`.
- **Feature rows** — `.lp-feature` (text + visual, `--flip` to alternate) with `.lp-feature__list`.
- **Feature visuals** — `.lp-viz` glass panels wrapping inline-SVG product motifs (`_viz_surface`,
  `_viz_stack`, `_viz_cashflow`, `_viz_proposal`); animate in on `.reveal.in` (mesh draw, bars grow).
- **Pipeline** — reuses the `#how-pin` scrollytelling machinery, re-skinned for dark under `.lp`.
- **Markets / CTA band / footer** — `.lp-market`, `.lp-cta-band`, `.lp-foot`.

## Changelog

- **LANDING** — rebuilt the public landing as a full-bleed cinematic story page (`base_immersive.html`
  + `.lp-*`): oversized kinetic hero, four inline-SVG feature visuals, a re-skinned dark pipeline, markets,
  CTA band and footer. Added the no-JS `.reveal` safety gate. (Verse.inc-grade per Olly's feedback.)
- **U10** — added the `.state` centered empty/not-found/error component; redesigned the 404 page to
  use it (icon + message + actions).
- **U00** — established the system: spacing/motion/dark-palette tokens; extracted the reusable
  `.gq-ambient` animated background (was the landing's one-off `.vhero__glow`); added the skip-link +
  focusable `#main` + a self-contained favicon; documented tokens/components here. Guarded by
  `tests/test_app_design_system.py`. Non-regressive — the rendered surface is unchanged.
