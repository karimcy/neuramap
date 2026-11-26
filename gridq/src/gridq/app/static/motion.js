/* GridQ motion - the single reveal/count-up utility for every page.
 *
 * Discipline (docs/goal/redesign-audit.md + the cinematic-redesign spec):
 * - ONE IntersectionObserver drives all scroll reveals. Elements opt in with `data-reveal`;
 *   an optional `data-reveal-group` staggers children by 70ms steps.
 * - Reveal = fade-up (translate var(--reveal-shift), var(--dur-base), var(--ease-spring)),
 *   defined in CSS; this module only toggles `.is-revealed`. Nothing animates twice.
 * - Count-ups (`data-count`) animate the PRESENTATION of a server-rendered value only:
 *   the target is what the server printed (or data-count/data-count-exact attrs) - this
 *   module never computes or invents a figure, it interpolates toward one.
 * - prefers-reduced-motion: everything lands in its final state immediately; observers
 *   still fire so `.is-revealed` styling applies, but with no transition and no count-up.
 * - No-JS: html:not(.js) keeps [data-reveal] fully visible (CSS side).
 */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var counted = new WeakSet();  // nothing counts up twice, however it was reached

  /* ---- count-up: presentation-only interpolation toward the server value ----
   * data-count carries the RAW record value (with data-dec/-suffix/-prefix for the shown
   * form); data-count-exact marks that the element's server-formatted textContent is
   * canonical - the animation must LAND verbatim on that string, never a re-rounded one. */
  function runCount(el) {
    if (counted.has(el)) return;
    counted.add(el);
    var raw = el.getAttribute("data-count");
    var isExact = el.hasAttribute("data-count-exact");
    var exact = isExact ? (el.getAttribute("data-count-exact") || el.textContent) : null;
    if (raw === null && exact === null) return;
    var target, dec, suffix, prefix;
    if (raw !== null) {
      target = parseFloat(raw);
      dec = parseInt(el.getAttribute("data-dec") || "0", 10);
      suffix = el.getAttribute("data-suffix") || "";
      prefix = el.getAttribute("data-prefix") || "";
    } else {
      // Pure-exact mode: split the server string around its first numeral so the units
      // ("165.3 kW", "10.4%", "£1,234/yr") stay in place while the number interpolates.
      var m = exact.match(/^([^0-9-]*)(-?\d[\d,]*(?:\.\d+)?)([\s\S]*)$/);
      if (!m) { el.textContent = exact; return; }
      prefix = m[1];
      suffix = m[3];
      target = parseFloat(m[2].replace(/,/g, ""));
      dec = decimalsOf(m[2]);
    }
    if (isNaN(target)) { if (exact !== null) el.textContent = exact; return; }
    function shown(v) { return prefix + v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suffix; }
    var endText = exact !== null ? exact : shown(target);
    if (reduced) { el.textContent = endText; return; }
    animate(el, target, function (v) { el.textContent = shown(v); }, function () {
      el.textContent = endText;
    });
  }

  function decimalsOf(s) {
    var m = (s || "").match(/\.(\d+)/);
    return m ? m[1].length : 0;
  }

  function animate(el, target, frame, done) {
    var t0 = null;
    var dur = 900;
    function step(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      frame(target * eased);
      if (p < 1) {
        requestAnimationFrame(step);
      } else {
        done();
      }
    }
    requestAnimationFrame(step);
  }

  /* ---- the one observer ----
   * Drives every scroll trigger on a page: [data-reveal] fade-ups, standalone count-ups,
   * and callbacks registered via watch() (charts that draw in when first seen). */
  var io = null;
  var seen = new WeakSet();
  var watchers = new WeakMap();

  function fire(el) {
    if (seen.has(el)) return;
    seen.add(el);
    if (io) io.unobserve(el);
    if (el.hasAttribute("data-reveal")) settle(el);
    if (el.matches("[data-count], [data-count-exact]")) runCount(el);
    var cb = watchers.get(el);
    if (cb) { watchers.delete(el); cb(el); }
  }

  function ensureObserver() {
    if (io || !("IntersectionObserver" in window)) return io;
    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) fire(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
    return io;
  }

  function observe(el) {
    if (seen.has(el)) return;
    if (ensureObserver()) io.observe(el);
    else fire(el);
  }

  /* Charts and other modules hook the same observer: cb runs once, on first visibility
   * (immediately when IntersectionObserver is unavailable). */
  function watch(el, cb) {
    if (!el || typeof cb !== "function") return;
    if (seen.has(el)) { cb(el); return; }
    watchers.set(el, cb);
    observe(el);
  }

  function init() {
    document.querySelectorAll("[data-reveal], [data-count], [data-count-exact]").forEach(observe);
  }

  function settle(el) {
    var group = el.hasAttribute("data-reveal-group");
    var items = group ? el.querySelectorAll(":scope > *") : [el];
    items.forEach(function (item, i) {
      if (reduced) {
        item.style.transitionDuration = "0s";
      } else if (group) {
        item.style.transitionDelay = (i * 70) + "ms";
      }
      item.classList.add("is-revealed");
    });
    el.classList.add("is-revealed");
    el.querySelectorAll("[data-count], [data-count-exact]").forEach(runCount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.GridQMotion = { init: init, watch: watch, reduced: reduced };
})();
