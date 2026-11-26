/* GridQ theme bridge - the ONE way JS reads the design system.
 *
 * charts.js and wizard.js previously mirrored app.css colours by hand and the two palettes
 * drifted (see docs/goal/redesign-audit.md, risk notes). This module reads the live token
 * values off :root at runtime, so a palette change in tokens.css propagates everywhere
 * with no hand-sync. No colour/font literal may live in any other JS file.
 */
(function () {
  "use strict";

  var cache = null;

  function read(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function tokens() {
    if (cache) return cache;
    cache = {
      bg: read("--bg", "#0e0e10"),
      surface1: read("--surface-1", "#131316"),
      surface2: read("--surface-2", "#18181c"),
      line: read("--line", "rgba(255,255,255,0.07)"),
      lineStrong: read("--line-strong", "rgba(255,255,255,0.12)"),
      text: read("--text", "#ececee"),
      text2: read("--text-2", "#a6a6ad"),
      muted: read("--muted", "#85858e"),
      accent: read("--accent", "#ffb454"),
      accentStrong: read("--accent-strong", "#ffc678"),
      accentWash: read("--accent-wash", "rgba(255,180,84,0.10)"),
      cool: read("--cool", "#7d8cf8"),
      ok: read("--ok", "#4ade80"),
      okLine: read("--ok-line", "rgba(74,222,128,0.35)"),
      err: read("--err", "#f87171"),
      gated: read("--gated", "#8d8d96"),
      warn: read("--warn", "#e8c468"),
      mono: read("--font-mono", "ui-monospace, Menlo, monospace"),
      sans: read("--font-sans", "system-ui, sans-serif"),
    };
    return cache;
  }

  /* Sequential scale for the return surface: deep charcoal up to the accent - "higher
   * return = brighter amber". Endpoints derive from the tokens; midpoints are fixed
   * perceptual steps of the same hue ramp (display geometry, not data). */
  function returnScale() {
    return [
      [0.0, "#131316"],
      [0.25, "#3a2f1d"],
      [0.5, "#7a5c2b"],
      [0.75, "#c08a3e"],
      [1.0, tokens().accent],
    ];
  }

  /* Categorical shade ramps for stacked segments (display geometry, endpoints from tokens):
   * firm/priced-floor streams step down from the semantic green; gated-upside streams step
   * down from the accent amber. Order = segment order, not meaning. */
  function rampFirm() {
    return [tokens().ok, "#3cb56a", "#2f8f56", "#256f45", "#1c5335"];
  }
  function rampUpside() {
    return [tokens().accent, "#d99845", "#b37d38", "#8c622c", "#664720"];
  }

  window.GridQTheme = { tokens: tokens, returnScale: returnScale, rampFirm: rampFirm, rampUpside: rampUpside };
})();
