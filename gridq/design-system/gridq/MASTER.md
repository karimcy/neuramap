# GridQ Design System - Master File

> **LOGIC:** When building a specific page, first check `design-system/gridq/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** gridq - energy/grid/battery analytics + financial underwriting (data-dense, technical, trustworthy; NOT a consumer landing page)
**Generated:** 2026-07-03 via ui-ux-pro-max, then reconciled against the shipped token system (see "Reconciliation" below)
**Implementation source of truth:** `src/gridq/app/static/tokens.css` - every colour, space, radius and motion value in CSS *and* JS (via `static/theme.js`) reads from it. This document describes that system; if the two ever disagree, tokens.css wins and this file must be updated.

---

## Reconciliation - what the generator proposed vs what GridQ uses, and why

| Generator proposed | GridQ canon | Why |
|---|---|---|
| Accent/CTA green `#22C55E` | Accent amber `#ffb454`; green `#4ade80` **reserved** | In an underwriting tool green may only ever mean pass / covenant-met / firm revenue. A green CTA would conflate "click me" with "bankable". Red `#f87171` is equally reserved for fail/error. |
| Fira Code / Fira Sans via Google Fonts CDN | Inter Variable + JetBrains Mono Variable, **self-hosted** (`static/vendor/fonts/`) | House invariant: no CDN, app must work offline. Pinned by `test_fonts_self_hosted_no_cdn`. |
| Card elevation via box-shadows | Hairline borders (`--line*`) + faint top gradient; **no drop shadows on dark** | Shadows are invisible against `#0e0e10`; hairlines read as precision. |
| Slate-blue darks `#020617/#0F172A` | Neutral near-blacks `#0e0e10/#131316/#18181c/#1e1e23` | Verified palette (WCAG AA, Lighthouse 98 a11y); no blue cast fighting the amber accent. |
| `cursor:pointer` on all cards | Pointer **only on genuinely clickable** elements | A pointer on a non-interactive card is a lie; GridQ cards are mostly informational. |

Adopted from the generator unchanged: the anti-pattern list and pre-delivery checklist below (its emoji-icon rule flagged real violations that this pass fixed), the "minimal glow / visible focus / high readability" effect direction, and the Real-Time Operations page pattern (the landing already follows it: live hero, metrics, how-it-works, CTA).

---

## Global Rules

### Color palette (from tokens.css)

| Role | Token | Hex |
|------|-------|-----|
| Canvas | `--bg` | `#0e0e10` |
| Deep canvas (hero/nav wells) | `--bg-deep` | `#0a0a0c` |
| Surface steps | `--surface-1/2/3` | `#131316 / #18181c / #1e1e23` |
| Hairlines | `--line / -strong / -faint` | `rgba(255,255,255, .07 / .12 / .045)` |
| Text | `--text` | `#ececee` |
| Secondary text | `--text-2` | `#a6a6ad` |
| Muted text | `--muted` | `#85858e` (5.1:1 on `--bg` - AA at any size) |
| **The one accent** | `--accent` (+`-strong/-ink/-wash/-line`) | `#ffb454` |
| Pass / firm (reserved) | `--ok` | `#4ade80` |
| Fail / error (reserved) | `--err` | `#f87171` |
| Gated / conditional | `--gated` | `#8d8d96` |
| Indicative notice | `--warn` | `#e8c468` |
| Charge direction (charts only) | `--cool` | `#7d8cf8` |

Rules: never pure black; one accent only; green/red are semantic and *never* decorative; JS never hardcodes colours - `GridQTheme.tokens()/returnScale()/rampFirm()/rampUpside()` read live token values (a guard test fails raw colours smuggled into app.css).

### Typography

- **Prose:** Inter Variable (self-hosted woff2, preloaded, `font-display: swap`).
- **Every number, unit and data label:** JetBrains Mono Variable - `.metric`, `.kv dd`, `td.num`, `.stat__num`, badges, eyebrows, statusstrip, chart axes.
- **Eyebrows:** uppercase, mono, `letter-spacing: var(--eyebrow-tracking)` (0.08em), "01 · SIZING" numbering pattern.
- No font CDN. Ever.

### Spacing, shape, layout

- Scale: `--sp-1..8` = 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 px. No arbitrary margins - compose from the scale.
- Radii: `--radius-sm` 6px, `--radius` 10px, `--radius-lg` 16px, pill for badges.
- Content max-width `--maxw` 1200px. Grid children set `min-width: 0` (overflow guard).

### Motion

- Durations `--dur-fast .15s / --dur-base .45s / --dur-slow .8s`; easing `--ease-spring: cubic-bezier(0.16, 1, 0.3, 1)` (defined exactly once - guard-tested).
- ONE IntersectionObserver (`static/motion.js`) owns all scroll triggers: `[data-reveal]` fade-ups (18px), count-ups (land verbatim on server-formatted strings), chart draw-ons. Once per element (WeakSet).
- Perpetual animation is limited to status-dot pulses and the hero loop. Everything else plays once.
- `prefers-reduced-motion`: instant final states, hero renders one static frame.

### Elevation

Hairline border + `--grad-panel` top sheen. **No box-shadows on dark surfaces.** Focus ring: global `:focus-visible` amber outline, 2px, offset 2px.

---

## Component specs (implemented in app.css - reference, don't fork)

- **Buttons** `.btn`: accent fill, `--accent-ink` text, `--radius-sm`, weight 650, `cursor: pointer`, hover brightens via `--accent-strong`, transitions ≤200ms, `:disabled` at 0.4 opacity + `not-allowed`.
- **Cards** `.card`: `--surface-1`, hairline, `--radius`, `--sp-5` padding. No hover lift unless the whole card is a link.
- **Console panels**: `.console-head` hairline header + `.console-status` (pulsing `.status-dot`, mono microcopy like `engine · solved`). Dots: accent = live/selected, `--ok` = pass, `--muted` = idle.
- **Icons**: inline SVG, Lucide outline set, 24×24 viewBox, `stroke="currentColor"` `stroke-width="2"`, `aria-hidden="true"`, sized via the parent (`.ico`, `.dz-ico`). **Never emoji or unicode glyphs (▦ ⚑ 📄...).**
- **Forms**: `--surface-2` inputs, hairline border, amber `:focus-visible`; dropzones are dashed `--line-strong` with an upload icon, drag-over state brightens to `--accent-line`.
- **Tables**: `td.num` right-aligned mono; row hover `--fill-soft`; header eyebrow-styled.
- **Badges**: pill, mono, `--ok`/`--err`/`--gated` only when the value genuinely passes/fails/is-conditional.
- **Empty states** `.recent-empty`: muted prose + a next action, never a bare "no data".
- **Charts**: palette exclusively from `GridQTheme` (amber return-scale, firm greens vs gated ambers/greys, `--cool` for charging); axes mono; draw-on-scroll with axes pinned to final extents.
- **Aurora glass layer** (added 2026-07-05, Olly; immersive landing ONLY - `body.immersive`): a living violet/amber/teal field (`.gq-aurora` + drifting `.gq-aurora__orb` blobs, transform/opacity only, reduced-motion frozen by the global reset) drifting behind frosted-glass consoles (`--glass-fill/-strong/-stroke/-blur/-sheen`, iridescent `--grad-iris` edges, holographic `--grad-holo` headline). Layered WITH the existing `.gq-ambient` (amber+cool), so the two together read as a full aurora. **Decorative only**: violet (`--aurora-violet`) and teal (`--aurora-teal`) are atmosphere and may NEVER encode state - pass/fail/firm stay amber/green/red. The data console (dashboard/run/spec/telemetry) does NOT use it: it keeps the near-black instrument look. All colours live in `tokens.css`; guard tests (`test_app_design_system.py`) stay green.

---

## Style guidelines

**Style:** precision dark - near-black neutral canvas, hairline structure, a single electric-amber accent, monospaced data. The screen should read like a well-lit instrument panel: calm surfaces, high-contrast figures, colour only where it carries meaning.

**Key effects:** pulsing status dots, count-ups that land exactly on the stamped figures, the WebGL hero drawing the *real* sizing surface of the newest solved run (guarded: no run → SVG fallback; <880px → SVG; software GL → SVG; reduced motion → static frame).

**Page pattern (landing):** Real-Time / Operations - hero with live artefact, key metrics, how-it-works, CTA in nav + after metrics.

**The sacred invariant:** the interface formats, it never computes or invents a number. Every figure on every screen comes from the stamped `QuoteRecord` via `fmt_*` helpers. (AST guard-tested.)

---

## Anti-Patterns (Do NOT Use)

- ❌ **Emojis or unicode glyphs as icons** - inline Lucide SVG only
- ❌ **A second *semantic* accent colour** - amber is the accent; green/red are verdicts, not decoration. (The landing's violet/teal aurora is the one sanctioned exception: purely decorative atmosphere on `body.immersive`, never a verdict - see "Aurora glass layer".)
- ❌ **Box-shadow elevation on dark surfaces** - hairlines + gradient sheen
- ❌ **Colour literals outside tokens.css** - CSS uses `var(--*)`, JS uses `GridQTheme`
- ❌ **Font/asset CDNs** - everything vendored, offline-safe
- ❌ **Missing cursor:pointer on clickables** (and no pointer on non-clickables)
- ❌ **Layout-shifting hovers** - transform/opacity only
- ❌ **Low contrast text** - 4.5:1 minimum (muted text included)
- ❌ **Instant state changes** - transitions 150-300ms
- ❌ **Invisible focus states** - the global amber `:focus-visible` ring must stay visible
- ❌ **Anything animating twice** - reveals/count-ups are once-only
- ❌ **Em dashes in UI copy** - house style, guard-tested
- ❌ **Numbers invented or computed in the template layer** - format-only

---

## Pre-Delivery Checklist

Before delivering any UI change, verify:

- [ ] No emojis/glyphs as icons; all icons inline Lucide SVG, `stroke="currentColor"`, `aria-hidden`
- [ ] `cursor: pointer` on all clickable elements, on *only* clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Text contrast ≥ 4.5:1 (spot-check new muted/accent-on-surface pairs)
- [ ] Focus states visible for keyboard navigation (tab through the screen)
- [ ] `prefers-reduced-motion` respected (reveals/counts/hero collapse to final state)
- [ ] Responsive at 375 / 768 / 1024 / 1440 - no horizontal scroll anywhere
- [ ] No content hidden behind the sticky topbar/nav
- [ ] Every number still arrives via `fmt_*` from the record (no arithmetic in templates)
- [ ] Quality bar: `ruff check . && ruff format . && mypy` + `review_gates` + full pytest (`TZ=UTC OMP_NUM_THREADS=1`, capture `PYTEST-EXIT:$?`)

## Extending this system

1. Need a new colour/space/duration? Add a token to `tokens.css` with a comment, then consume it via `var(--*)` / `GridQTheme` - never inline.
2. Page-specific deviation? Add `design-system/gridq/pages/<page>.md` stating the override and why; keep it minimal.
3. New component? Spec it here first (one paragraph), build it from existing tokens, add it to the design-system smoke tests if it carries chrome other screens will reuse.
