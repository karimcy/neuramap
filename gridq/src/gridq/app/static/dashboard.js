/* GridQ page-specific motion: the pinned scrollytelling pipeline + landing nav chrome.
 *
 * Vanilla JS, no framework, no external assets. Scroll REVEALS and COUNT-UPS live in
 * static/motion.js (the single IntersectionObserver utility) - this file only keeps the
 * scroll-position-driven pieces that are not reveal-shaped: the pinned pipeline stage,
 * the landing nav solidify, and the hero orb parallax. Everything degrades gracefully;
 * nothing here computes or displays a figure the server did not already put on the page.
 */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 1. Pinned scrollytelling: advance the pipeline stages with scroll through the section.
  //    Reduced motion and small screens get the STATIC layout (.how.static lays every stage
  //    out vertically) - stacking absolutely-positioned panels as all-.active overlapped
  //    illegibly, and 7 stages of pin choreography have no place on a phone.
  var pin = document.getElementById("how-pin");
  var small = window.matchMedia && window.matchMedia("(max-width: 700px)").matches;
  if (pin && (reduce || small)) {
    pin.classList.add("static");
  } else if (pin) {
    var steps = pin.querySelectorAll(".pin-step");
    var fill = document.getElementById("pin-fill");
    var dots = pin.querySelectorAll(".pin-dot");
    function onScroll() {
      var rect = pin.getBoundingClientRect();
      var span = pin.offsetHeight - window.innerHeight;
      var prog = span > 0 ? Math.min(1, Math.max(0, -rect.top / span)) : 0;
      var idx = Math.min(steps.length - 1, Math.floor(prog * steps.length + 0.0001));
      steps.forEach(function (s, i) { s.classList.toggle("active", i === idx); });
      dots.forEach(function (d, i) { d.classList.toggle("active", i <= idx); });
      if (fill) fill.style.width = Math.round(prog * 100) + "%";
    }
    // rAF-throttle: coalesce scroll bursts into one update per frame (no layout thrash).
    var ticking = false;
    window.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; requestAnimationFrame(function () { onScroll(); ticking = false; }); }
    }, { passive: true });
    onScroll();
  }

  // 2. Immersive landing nav: solidify on scroll past the hero.
  var lpNav = document.getElementById("lp-nav");
  if (lpNav) {
    var onNav = function () { lpNav.classList.toggle("scrolled", window.scrollY > 40); };
    window.addEventListener("scroll", onNav, { passive: true });
    onNav();
  }

  // 3. Subtle hero parallax on the ambient-background orb.
  var glow = document.querySelector(".gq-ambient__orb");
  if (glow && !reduce) {
    window.addEventListener("scroll", function () {
      glow.style.transform = "translateY(" + (window.scrollY * 0.18) + "px)";
    }, { passive: true });
  }
})();
