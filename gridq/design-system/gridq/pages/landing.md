# Page override: landing (`/`)

Deviates from MASTER because it is the only page on `base_immersive.html` (no app
sidebar/topbar; its own `#lp-nav` that solidifies on scroll).

- Hero: `#hero-3d` mounts the WebGL sizing surface of the newest solved run; the inline
  SVG in `_viz_surface.html` is the designed fallback, not an error state. Guards
  (island present / ≥880px / hardware GL / motion-ok) live in `static/hero.js` - do not
  weaken them.
- Uses `--bg-deep` wells and larger display type; still 100% tokens.
- CTAs: `.lp a.btn` keeps `--accent-ink` text (specificity guard - `.lp a` would
  otherwise re-colour it).
- Everything else (icons, motion, contrast, checklist) follows MASTER.
