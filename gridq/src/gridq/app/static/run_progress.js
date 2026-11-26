/* GridQ run-progress overlay - shared by the New Quote wizard and the results-page improve panel.
 *
 * Posts the run as a background job (POST /api/run -> {job_id}) and polls /api/run/{job_id}. The
 * browser is never blocked. Progress is REAL and forward-only: the deterministic backend pipeline
 * reports the live phase and its (done, total) counts into the job dict, and this client maps each
 * phase to an ordered percent band, rendering a strictly monotonic bar, a numeric percent, and a
 * plain-language narration of the current phase and iteration. The server ships FACTS ONLY; the
 * client owns the percent (presentation). The completion is REAL too: the bar only reaches 100% and
 * navigates when the pipeline reports done with a quote id, or shows the error if it fails.
 */
(function () {
  "use strict";

  // The 5-step visual strip (the coarse pipeline stages the user recognises).
  var LABELS = ["Ingest", "Load profile", "Value stack", "Dispatch + sizing", "Financials"];

  // Phase -> [lo, hi] percent band. Bands are ordered and never overlap forward, so the monotonic
  // max() below can only ever move the bar right. "sizing" is the legacy single-loop fallback path.
  var BANDS = {
    start: [2, 4],
    optimise: [4, 10],
    coarse: [10, 36],
    refine: [36, 58],
    confirm: [58, 78],
    polish: [78, 86],
    sizing: [10, 86],
    dispatch: [86, 91],
    finance: [91, 96],
    finalise: [96, 99],
    done: [100, 100]
  };

  // Phase -> [status title, subtitle] (announced via the role=status live region).
  var TITLES = {
    start: ["Starting the run", "Preparing your site record."],
    optimise: ["Optimising", "Gating the value stack and building the dispatch model."],
    coarse: ["Dispatching and sizing", "A half-hourly battery schedule for a full year, at every candidate size."],
    refine: ["Dispatching and sizing", "Refining around the most promising system sizes."],
    confirm: ["Dispatching and sizing", "Confirming the shortlist on the full metered year."],
    polish: ["Dispatching and sizing", "Polishing the optimum around the best candidate."],
    sizing: ["Dispatching and sizing", "A half-hourly battery schedule for a full year, at every candidate size."],
    dispatch: ["Final dispatch", "Running the chosen system across the full metered year."],
    finance: ["Running financials", "P50 and P90 cashflows, DSCR against the covenant, returns and payback."],
    finalise: ["Assembling your pack", "Stamping the quote and its telemetry."],
    done: ["Complete", "Opening your results."]
  };

  // Phase -> which of the 5 strip steps is active. Max-clamped in setStep so it only ever advances.
  function stepForStage(stage) {
    if (stage === "done") return 5;
    if (stage === "finance" || stage === "finalise") return 4;
    return 3; // optimise / coarse / refine / confirm / polish / sizing / dispatch
  }

  // Phase -> detail narration (mono, muted): names the phase and its iteration. A total is claimed
  // only when the backend actually knows one (refine has no total, so it narrates the count only).
  function detailFor(stage, done, total) {
    var d = typeof done === "number" ? done : null;
    var t = typeof total === "number" ? total : null;
    switch (stage) {
      case "coarse":
        return d != null && t != null ? "Coarse sweep - sizing candidate " + d + " of " + t : "Coarse sweep across candidate sizes";
      case "refine":
        return d != null ? "Refining around the best region - evaluation " + d : "Refining around the best region";
      case "confirm":
        return d != null && t != null ? "Confirming shortlisted systems on the full year - " + d + " of " + t : "Confirming shortlisted systems on the full year";
      case "polish":
        return d != null && t != null ? "Polishing the optimum - probe " + d + " of up to " + t : "Polishing the optimum";
      case "sizing":
        return d != null && t != null ? "Sizing candidate " + d + " of " + t + " - a full half-hourly year each" : "Sizing candidates - a full half-hourly year each";
      case "dispatch":
        return "Final dispatch of the chosen system";
      case "optimise":
        return "Gating the value stack and building the dispatch model";
      case "finance":
        return "Underwriting the chosen system - P50, P90 and DSCR";
      case "finalise":
        return "Stamping the quote and telemetry";
      case "done":
        return "Complete";
      default:
        return "";
    }
  }

  // The target percent for a phase's (done, total): interpolate within the band when a total is
  // known; asymptotically approach the band ceiling (never reaching it) when it is not (refine).
  function targetFor(stage, done, total) {
    var band = BANDS[stage] || BANDS.start;
    var lo = band[0], hi = band[1];
    if (stage === "done") return 100;
    var haveDone = typeof done === "number" && done > 0;
    var haveTotal = typeof total === "number" && total > 0;
    if (haveDone && haveTotal) {
      return lo + Math.min(1, done / total) * (hi - lo);
    }
    if (haveDone) {
      return hi - (hi - lo) * Math.pow(0.95, done);
    }
    return lo;
  }

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  // ---- render state (module-scoped; reset in run()) ----
  var prevPct = 0;
  var curStage = "start";
  var stepIndex = 0;
  var creepTimer = null;
  var stepTimers = [];
  var finished = false;
  // Generation token: each run() bumps it, so any in-flight poll chain, retry .catch or timer
  // callback left over from a PRIOR run() (e.g. an error-then-resubmit on the same page) sees a
  // stale generation and self-cancels instead of latching `finished`/navigating on the new run.
  var gen = 0;

  function build() {
    var ov = document.getElementById("gq-run-overlay");
    if (ov) return ov;
    ov = el("div", "run-overlay"); ov.id = "gq-run-overlay";
    // Reusable animated background (design-system .gq-ambient): the cinematic run moment.
    var amb = el("span", "gq-ambient"); amb.setAttribute("aria-hidden", "true");
    amb.appendChild(el("span", "gq-ambient__orb"));
    amb.appendChild(el("span", "gq-ambient__orb two"));
    ov.appendChild(amb);
    var card = el("div", "run-card");
    card.setAttribute("role", "dialog"); card.setAttribute("aria-label", "Running your quote");
    // Live region: stage title, subtitle and the detail narration are announced as the run runs.
    var status = el("div", null); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    var h = el("h2", null, "Starting the run"); h.id = "gq-run-title"; status.appendChild(h);
    var p = el("p", "muted", ""); p.id = "gq-run-sub"; status.appendChild(p);
    var det = el("p", "run-detail", ""); det.id = "gq-run-detail"; status.appendChild(det);
    card.appendChild(status);
    // The bar itself is the progressbar; the visible NN% label mirrors aria-valuenow (aria-hidden).
    var track = el("div", "run-track"); track.id = "gq-run-track";
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "Run progress");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", "0");
    var fill = el("div", "run-fill"); fill.id = "gq-run-fill";
    track.appendChild(fill); card.appendChild(track);
    var pct = el("div", "run-pct", "0%"); pct.id = "gq-run-pct"; pct.setAttribute("aria-hidden", "true");
    card.appendChild(pct);
    var steps = el("div", "run-steps"); steps.id = "gq-run-steps";
    LABELS.forEach(function (lbl, i) {
      var d = el("div", "run-step"); d.setAttribute("data-s", i);
      d.appendChild(el("span", "rdot", String(i + 1)));
      d.appendChild(el("span", null, lbl));
      steps.appendChild(d);
    });
    card.appendChild(steps);
    var err = el("p", "run-err", ""); err.id = "gq-run-err"; err.hidden = true; card.appendChild(err);
    ov.appendChild(card); document.body.appendChild(ov);
    return ov;
  }

  // Apply a target percent under the monotonic invariant: never decrease, never fake 100 before
  // the real done signal. Updates the fill width, the numeric label and aria-valuenow together.
  function applyPct(target) {
    var ceilCap = curStage === "done" ? 100 : 99;
    var pct = Math.max(prevPct, Math.min(target, ceilCap));
    prevPct = pct;
    var rounded = Math.round(pct);
    var fill = document.getElementById("gq-run-fill");
    var pctEl = document.getElementById("gq-run-pct");
    var track = document.getElementById("gq-run-track");
    if (fill) fill.style.width = rounded + "%";
    if (pctEl) pctEl.textContent = rounded + "%";
    if (track) track.setAttribute("aria-valuenow", String(rounded));
  }

  function setStep(i) {
    stepIndex = Math.max(stepIndex, i);
    document.querySelectorAll("#gq-run-steps .run-step").forEach(function (st) {
      var d = +st.getAttribute("data-s");
      st.classList.toggle("is-done", d < stepIndex);
      st.classList.toggle("is-active", d === stepIndex);
    });
  }

  function render(stage, done, total) {
    if (finished) return;
    if (!stage || !BANDS[stage]) stage = curStage; // unknown / absent phase: hold the current one
    curStage = stage;
    applyPct(targetFor(stage, done, total));
    var ttl = TITLES[stage] || TITLES.start;
    var t = document.getElementById("gq-run-title");
    var s = document.getElementById("gq-run-sub");
    var det = document.getElementById("gq-run-detail");
    if (t) t.textContent = ttl[0];
    if (s) s.textContent = ttl[1];
    if (det) det.textContent = detailFor(stage, done, total);
    setStep(stepForStage(stage));
  }

  // A gentle between-poll creep (~0.15%/s) so a long single solve never looks frozen. Capped below
  // the current band ceiling and, by applyPct, never decreasing - it can only edge the bar right.
  function startCreep() {
    if (creepTimer) return;
    creepTimer = setInterval(function () {
      if (finished || curStage === "done") return;
      var band = BANDS[curStage] || BANDS.start;
      var ceil = band[1] - 0.5;
      if (prevPct + 0.15 <= ceil) applyPct(prevPct + 0.15);
    }, 1000);
  }

  // Sweep steps 0-2 to done within ~1.2s of job start, landing on step 3 (dispatch + sizing).
  function startupSweep() {
    setStep(0);
    [0, 1, 2, 3].forEach(function (i, k) {
      stepTimers.push(setTimeout(function () { if (!finished) setStep(i); }, k * 400));
    });
  }

  function stopTimers() {
    if (creepTimer) { clearInterval(creepTimer); creepTimer = null; }
    stepTimers.forEach(function (id) { clearTimeout(id); });
    stepTimers = [];
  }

  function finish() {
    finished = true;
    stopTimers();
    curStage = "done";
    prevPct = 100;
    var fill = document.getElementById("gq-run-fill");
    var pctEl = document.getElementById("gq-run-pct");
    var track = document.getElementById("gq-run-track");
    if (fill) fill.style.width = "100%";
    if (pctEl) pctEl.textContent = "100%";
    if (track) track.setAttribute("aria-valuenow", "100");
    document.querySelectorAll("#gq-run-steps .run-step").forEach(function (st) {
      st.classList.add("is-done"); st.classList.remove("is-active");
    });
    document.getElementById("gq-run-title").textContent = TITLES.done[0];
    document.getElementById("gq-run-sub").textContent = TITLES.done[1];
    var det = document.getElementById("gq-run-detail");
    if (det) det.textContent = "";
  }

  function fail(msg) {
    finished = true;
    stopTimers();
    var err = document.getElementById("gq-run-err");
    err.textContent = msg || "The run could not complete. Please check your inputs.";
    err.hidden = false;
    document.getElementById("gq-run-title").textContent = "Could not complete the run";
    document.getElementById("gq-run-sub").textContent = "";
    var det = document.getElementById("gq-run-detail");
    if (det) det.textContent = "";
    if (!document.getElementById("gq-run-close")) {
      var b = el("button", "btn secondary", "Close"); b.id = "gq-run-close";
      b.style.marginTop = "1rem";
      b.onclick = function () { var o = document.getElementById("gq-run-overlay"); if (o) o.remove(); };
      err.parentNode.appendChild(b);
    }
  }

  function run(formData) {
    build();
    document.getElementById("gq-run-overlay").hidden = false;
    // Supersede any prior run(): cancel its timers and invalidate its in-flight poll chain.
    stopTimers();
    var myGen = ++gen;
    // Reset render state so a second run() in the same page life starts clean.
    prevPct = 0; curStage = "start"; stepIndex = 0; finished = false;
    // Clear any leftover error UI from a previous failed run before starting fresh.
    var oldErr = document.getElementById("gq-run-err");
    if (oldErr) { oldErr.hidden = true; oldErr.textContent = ""; }
    var oldClose = document.getElementById("gq-run-close");
    if (oldClose) oldClose.remove();
    render("start", null, null);
    startupSweep();
    startCreep();
    var jobId = null, polls = 0;
    function poll() {
      if (myGen !== gen) return; // superseded by a newer run()
      fetch("/api/run/" + jobId).then(function (r) { return r.json(); }).then(function (j) {
        if (myGen !== gen) return;
        if (j.status === "done" && j.quote_id) {
          finish();
          setTimeout(function () { window.location.href = "/run/" + j.quote_id; }, 650);
        } else if (j.status === "error") {
          fail(j.message);
        } else if (++polls > 900) {
          fail("The run timed out.");
        } else {
          render(j.stage, j.stage_done, j.stage_total);
          setTimeout(poll, 800);
        }
      }).catch(function () { if (myGen !== gen) return; setTimeout(poll, 1200); });
    }
    fetch("/api/run", { method: "POST", body: formData })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (myGen !== gen) return;
        if (res.ok && res.j.job_id) { jobId = res.j.job_id; poll(); }
        else { fail((res.j && res.j.message) || "Please check your inputs."); }
      })
      .catch(function () { if (myGen !== gen) return; fail("Network error while starting the run."); });
  }
  window.GridQRun = { run: run };
})();
