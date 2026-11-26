/* GridQ New Quote wizard - vanilla JS, no framework.
 *
 * Drives the 5-stage guided flow: one clean screen per step with a horizontal swipe between them,
 * a progress bar, honest gating (the run stays blocked and the gaps are listed until inputs are
 * valid), a real OpenStreetMap (Leaflet) location picker with zoom + click-to-select, a canned UK
 * address type-ahead, and a live modelled load curve (vendored Plotly). On submit it hands off to
 * the shared run-progress overlay (GridQRun) which runs the real pipeline as a polled background
 * job. This file collects and validates inputs only and computes no underwriting figure.
 */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var form = $("wiz-form");
  if (!form) return;

  // ---- canned UK locations (type-ahead + DNO snap): real lat/lon + DNO --------------------
  var PLACES = [
    { name: "Inverness", pc: "IV1 1AB", lat: 57.48, lon: -4.22, dno: "SSEN Northern (SHEPD)" },
    { name: "Glasgow", pc: "G1 1XW", lat: 55.86, lon: -4.25, dno: "SP Distribution" },
    { name: "Edinburgh", pc: "EH1 1YZ", lat: 55.95, lon: -3.19, dno: "SP Distribution" },
    { name: "Newcastle", pc: "NE1 4ST", lat: 54.98, lon: -1.61, dno: "Northern Powergrid (Northeast)" },
    { name: "Leeds", pc: "LS1 4DY", lat: 53.80, lon: -1.55, dno: "Northern Powergrid (Yorkshire)" },
    { name: "Liverpool", pc: "L1 8JQ", lat: 53.41, lon: -2.99, dno: "SP Manweb" },
    { name: "Manchester", pc: "M1 1PT", lat: 53.48, lon: -2.24, dno: "Electricity North West" },
    { name: "Nottingham", pc: "NG1 5DT", lat: 52.95, lon: -1.15, dno: "NGED East Midlands" },
    { name: "Birmingham", pc: "B1 1BB", lat: 52.49, lon: -1.89, dno: "NGED West Midlands" },
    { name: "Norwich", pc: "NR1 3DA", lat: 52.63, lon: 1.30, dno: "UKPN East" },
    { name: "Cardiff", pc: "CF10 1EP", lat: 51.48, lon: -3.18, dno: "NGED South Wales" },
    { name: "Bristol", pc: "BS1 5TF", lat: 51.45, lon: -2.59, dno: "NGED South West" },
    { name: "London", pc: "EC1A 1BB", lat: 51.51, lon: -0.13, dno: "UKPN London" },
    { name: "Southampton", pc: "SO14 7DJ", lat: 50.90, lon: -1.40, dno: "SSEN Southern" },
    { name: "Exeter", pc: "EX1 1GE", lat: 50.72, lon: -3.53, dno: "NGED South West" },
  ];

  // ---- archetype daily shapes (24 hourly weights, mean ~1) -------------------------------
  var SHAPES = {
    "Office": [.3,.3,.3,.3,.3,.35,.5,.8,1.2,1.4,1.45,1.45,1.4,1.45,1.5,1.45,1.3,1.0,.7,.5,.45,.4,.35,.3],
    "Retail": [.18,.16,.16,.16,.18,.25,.4,.7,1.1,1.5,1.6,1.65,1.6,1.6,1.6,1.6,1.55,1.45,1.2,.7,.4,.3,.22,.2],
    "Hotel": [.5,.45,.42,.42,.45,.6,.95,1.25,1.2,1.05,1.0,1.0,1.05,1.0,1.0,1.05,1.15,1.3,1.5,1.55,1.4,1.1,.8,.6],
    "Restaurant": [.2,.18,.16,.16,.16,.18,.25,.4,.55,.7,1.0,1.55,1.6,1.3,.9,.8,.9,1.2,1.7,1.85,1.6,1.1,.6,.3],
    "Warehouse": [.12,.12,.12,.12,.12,.3,.9,1.5,1.6,1.6,1.6,1.6,1.55,1.6,1.6,1.6,1.4,.7,.2,.14,.12,.12,.12,.12],
    "Supermarket": [.6,.55,.55,.55,.6,.7,.9,1.15,1.35,1.45,1.5,1.5,1.45,1.45,1.45,1.45,1.45,1.4,1.3,1.1,.9,.8,.7,.65],
    "Industrial": [.5,.5,.5,.5,.55,.8,1.2,1.45,1.5,1.5,1.5,1.5,1.45,1.5,1.5,1.5,1.4,1.0,.7,.55,.5,.5,.5,.5],
    "Generic Commercial": [.35,.32,.3,.3,.34,.45,.7,1.0,1.3,1.4,1.45,1.45,1.4,1.45,1.45,1.4,1.25,1.0,.75,.55,.45,.4,.38,.36],
  };
  function shapeFor(name) { return SHAPES[name] || SHAPES["Generic Commercial"]; }

  var TARIFFS = {
    "Half-Hourly C&I (DUoS RAG)": [["Red band (peak)", "38.2p/kWh"], ["Amber band (shoulder)", "24.8p/kWh"], ["Green band (off-peak)", "16.5p/kWh"], ["Demand charge", "£8.50/kW/month"], ["Standing charge", "45p/day"], ["CCL", "0.78p/kWh"]],
    "Flat Rate": [["Unit rate", "28.0p/kWh"], ["Standing charge", "45p/day"], ["CCL", "0.78p/kWh"]],
    "Economy 7 (Day/Night)": [["Day rate", "30.0p/kWh"], ["Night rate", "14.0p/kWh"], ["Standing charge", "45p/day"]],
    "EV Time-of-Use": [["Peak", "35.0p/kWh"], ["Off-peak", "7.5p/kWh"], ["Standing charge", "45p/day"]],
    "Seasonal Time-of-Use": [["Winter peak", "40.0p/kWh"], ["Summer", "22.0p/kWh"], ["Standing charge", "45p/day"]],
    "Demand + HH Metered": [["Unit rate", "26.0p/kWh"], ["Demand charge", "£9.00/kW/month"], ["Standing charge", "55p/day"]],
  };

  var step = 1, MAX = 6;
  var panes = form.querySelectorAll(".wiz-pane");
  var stepsEl = $("wiz-steps").querySelectorAll(".wiz-step");
  function val(id) { var e = $(id); return e ? String(e.value || "").trim() : ""; }

  // ---- OpenStreetMap (Leaflet) location picker -------------------------------------------
  var map = null, marker = null;
  function pinIcon() { return L.divIcon({ className: "wiz-leaflet-pin", html: "", iconSize: [20, 20], iconAnchor: [10, 20] }); }
  function initMap() {
    if (typeof window.L === "undefined") return;
    map = L.map("wiz-map", { scrollWheelZoom: true }).setView([54.3, -2.6], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap contributors",
    }).addTo(map);
    map.on("click", function (e) { setPoint(e.latlng.lat, e.latlng.lng, true); });
    setTimeout(function () { map.invalidateSize(); }, 200);
  }
  function mapTo(lat, lon, zoom) {
    if (!map) return;
    if (marker) marker.setLatLng([lat, lon]); else marker = L.marker([lat, lon], { icon: pinIcon() }).addTo(map);
    map.setView([lat, lon], zoom || 12);
    setTimeout(function () { map.invalidateSize(); }, 50);
  }
  function setPoint(lat, lon, doReverse) {
    $("f-lat").value = (+lat).toFixed(4);
    $("f-lon").value = (+lon).toFixed(4);
    if (marker) marker.setLatLng([lat, lon]); else if (map) marker = L.marker([lat, lon], { icon: pinIcon() }).addTo(map);
    var best = null, bd = 1e9;
    PLACES.forEach(function (p) { var d = (p.lat - lat) * (p.lat - lat) + (p.lon - lon) * (p.lon - lon); if (d < bd) { bd = d; best = p; } });
    if (best && !$("f-dno").value) $("f-dno").value = best.dno;
    if (doReverse) reverseGeocode(lat, lon);
    refreshGating();
  }
  function reverseGeocode(lat, lon) {
    fetch("https://nominatim.openstreetmap.org/reverse?format=json&zoom=16&addressdetails=1&lat=" + lat + "&lon=" + lon, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j) return;
        if (j.display_name) $("f-address").value = j.display_name.split(",").slice(0, 3).join(", ").trim();
        if (j.address && j.address.postcode) $("f-postcode").value = j.address.postcode;
        refreshGating();
      })
      .catch(function () {});
  }

  // ---- address type-ahead ------------------------------------------------------------------
  var addr = $("f-address"), suggest = $("addr-suggest");
  function hideSuggest() { if (suggest) { suggest.hidden = true; suggest.innerHTML = ""; } }
  function applyPlace(pl) {
    $("f-address").value = pl.name + ", UK";
    $("f-postcode").value = pl.pc;
    $("f-lat").value = pl.lat;
    $("f-lon").value = pl.lon;
    $("f-dno").value = pl.dno;
    mapTo(pl.lat, pl.lon, 12);
    hideSuggest();
    refreshGating();
  }
  if (addr) {
    addr.addEventListener("input", function () {
      var q = addr.value.trim().toLowerCase();
      suggest.innerHTML = "";
      if (q.length < 1) { hideSuggest(); refreshGating(); return; }
      var hits = PLACES.filter(function (p) { return p.name.toLowerCase().indexOf(q) === 0 || p.pc.toLowerCase().indexOf(q) === 0; }).slice(0, 6);
      if (!hits.length) { hideSuggest(); return; }
      hits.forEach(function (p) {
        var li = document.createElement("li");
        li.textContent = p.name + " - " + p.pc;
        li.addEventListener("mousedown", function (e) { e.preventDefault(); applyPlace(p); });
        suggest.appendChild(li);
      });
      suggest.hidden = false;
    });
    addr.addEventListener("blur", function () { setTimeout(hideSuggest, 150); });
  }

  // ---- tariff card -------------------------------------------------------------------------
  var tariffSel = $("f-tariff"), tariffCard = $("tariff-card");
  function renderTariff() {
    if (!tariffSel || !tariffCard) return;
    var rows = TARIFFS[tariffSel.value];
    if (!rows) { tariffCard.hidden = true; return; }
    var html = '<div class="tariff-head">' + tariffSel.value + ' <span>typical structure</span></div><div class="tariff-rows">';
    rows.forEach(function (r) { html += "<div><span>" + r[0] + "</span><b>" + r[1] + "</b></div>"; });
    tariffCard.innerHTML = html + "</div>";
    tariffCard.hidden = false;
  }
  if (tariffSel) tariffSel.addEventListener("change", renderTariff);

  // ---- consumption method ------------------------------------------------------------------
  var methodBtns = form.querySelectorAll(".method");
  methodBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      if (b.disabled) return;
      var m = b.getAttribute("data-method");
      methodBtns.forEach(function (x) { x.classList.toggle("is-active", x === b); });
      form.querySelectorAll(".method-panel").forEach(function (p) { p.classList.toggle("is-active", p.getAttribute("data-panel") === m); });
      $("f-method").value = m;
      refreshGating();
    });
  });
  function fileNote(inputId, noteId) {
    var inp = $(inputId), note = $(noteId);
    if (!inp) return;
    inp.addEventListener("change", function () {
      note.textContent = inp.files && inp.files[0] ? "Selected: " + inp.files[0].name : "";
      refreshGating();
    });
  }
  fileNote("f-hh", "hh-name"); fileNote("f-bill", "bill-name");
  ["f-annual", "f-peak"].forEach(function (id) { var e = $(id); if (e) e.addEventListener("input", function () { dailyNote(); drawCurve(); refreshGating(); }); });
  function dailyNote() {
    var a = parseFloat(val("f-annual")), n = $("daily-note");
    if (n) n.textContent = a > 0 ? "~" + Math.round(a / 365).toLocaleString() + " kWh/day average" : "";
  }

  // ---- live load curve ---------------------------------------------------------------------
  function chosenArchetype() { var a = val("f-archetype"); if (a && a !== "auto") return a; return val("f-business") || "Generic Commercial"; }
  function curveTraces() {
    var shape = shapeFor(chosenArchetype());
    var annual = parseFloat(val("f-annual"));
    var avg = annual > 0 ? annual / 8760 : 1;
    return { x: shape.map(function (_, h) { return h; }), y: shape.map(function (s) { return +(s * avg).toFixed(2); }) };
  }
  function drawInto(divId) {
    if (typeof window.Plotly === "undefined") return;
    var elx = $(divId); if (!elx) return;
    var t = curveTraces();
    var T = window.GridQTheme.tokens();  // live design-system palette (see static/theme.js)
    var traces = [{ type: "scatter", mode: "lines", x: t.x, y: t.y, fill: "tozeroy", line: { color: T.accent, width: 2, shape: "spline" }, fillcolor: T.accentWash, hovertemplate: "%{x}:00 · %{y:.1f} kW<extra></extra>" }];
    var peak = parseFloat(val("f-peak")), shapes = [];
    if (peak > 0) shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: peak, y1: peak, line: { color: T.muted, width: 1, dash: "dash" } });
    var layout = { margin: { l: 46, r: 12, t: 8, b: 34 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { family: T.mono, size: 11, color: T.text2 }, showlegend: false, shapes: shapes, xaxis: { title: "Hour of day", gridcolor: T.line, dtick: 4, zeroline: false }, yaxis: { title: "kW", gridcolor: T.line, zeroline: false, rangemode: "tozero" } };
    try { window.Plotly.react(elx, traces, layout, { displaylogo: false, responsive: true, displayModeBar: false }); } catch (e) {}
  }
  function drawCurve() { drawInto("load-curve"); updateProfileStats(); }
  function updateProfileStats() {
    var elx = $("profile-stats"); if (!elx) return;
    var annual = parseFloat(val("f-annual")), peak = parseFloat(val("f-peak"));
    var shape = shapeFor(chosenArchetype()), mx = Math.max.apply(null, shape), mn = Math.min.apply(null, shape);
    var html = stat("Pattern", chosenArchetype());
    if (annual > 0) html += stat("Annual", Math.round(annual).toLocaleString() + " kWh");
    if (peak > 0) html += stat("Peak demand", peak + " kW");
    html += stat("Peak / mean", mx.toFixed(2) + "x");
    html += stat("Night base", Math.round(mn / mx * 100) + "%");
    elx.innerHTML = html;
  }
  function stat(k, v) { return "<div><span>" + k + "</span><b>" + v + "</b></div>"; }
  var arSel = $("f-archetype"); if (arSel) arSel.addEventListener("change", drawCurve);
  var bizSel = $("f-business"); if (bizSel) bizSel.addEventListener("change", function () { drawCurve(); refreshGating(); });

  // ---- gating --------------------------------------------------------------------------------
  function stageValid(n) {
    if (n === 1) {
      var ok = true;
      form.querySelectorAll('.wiz-pane[data-pane="1"] [data-req]').forEach(function (e) { if (!String(e.value || "").trim()) ok = false; });
      return ok;
    }
    if (n === 2) {
      var m = $("f-method").value;
      if (m === "manual") return parseFloat(val("f-annual")) > 0;
      if (m === "csv") return !!($("f-hh").files && $("f-hh").files[0]);
      if (m === "bill") return !!($("f-bill").files && $("f-bill").files[0]);
      return false;
    }
    return true;
  }
  function missingItems() {
    var out = [];
    var labels = { "site_name": "Project name", "business_type": "Business type", "address": "Address", "postcode": "Postcode", "latitude": "Latitude", "longitude": "Longitude", "dno_region": "DNO region", "tariff_type": "Current tariff", "grid_connection_kva": "Grid connection (kVA)" };
    form.querySelectorAll('.wiz-pane[data-pane="1"] [data-req]').forEach(function (e) { if (!String(e.value || "").trim()) out.push(labels[e.name] || e.name); });
    if (!stageValid(2)) out.push("Consumption data (annual usage or an uploaded file)");
    return out;
  }
  function refreshGating() {
    $("wiz-next").disabled = !stageValid(step);
    var miss = missingItems(), sub = $("wiz-submit");
    if (sub) sub.disabled = miss.length > 0;
    var box = $("gate-missing"), list = $("gate-list");
    if (box && list) {
      if (step === 6 && miss.length) { list.innerHTML = miss.map(function (m) { return "<li>" + m + "</li>"; }).join(""); box.hidden = false; }
      else { box.hidden = true; }
    }
  }
  form.addEventListener("input", refreshGating);
  form.addEventListener("change", refreshGating);

  // ---- navigation + swipe --------------------------------------------------------------------
  function show(n, dir) {
    n = Math.max(1, Math.min(MAX, n));
    panes.forEach(function (p) {
      var on = +p.getAttribute("data-pane") === n;
      p.classList.remove("swipe-fwd", "swipe-back");
      p.classList.toggle("is-active", on);
      if (on && dir) { void p.offsetWidth; p.classList.add(dir > 0 ? "swipe-fwd" : "swipe-back"); }
    });
    stepsEl.forEach(function (s) {
      var d = +s.getAttribute("data-step");
      s.classList.toggle("is-active", d === n);
      s.classList.toggle("is-done", d < n);
    });
    step = n;
    $("wiz-fill").style.width = ((n - 1) / (MAX - 1) * 100) + "%";
    $("wiz-back").hidden = n === 1;
    $("wiz-next").hidden = n === MAX;
    $("wiz-submit").hidden = n !== MAX;
    if (n === 3) drawCurve();
    if (n === 6) { drawInto("review-curve"); populateReview(); }
    refreshGating();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  $("wiz-next").addEventListener("click", function () { if (stageValid(step)) show(step + 1, 1); });
  $("wiz-back").addEventListener("click", function () { show(step - 1, -1); });
  stepsEl.forEach(function (s) { s.addEventListener("click", function () { var d = +s.getAttribute("data-step"); if (d < step) show(d, -1); }); });

  function populateReview() {
    var site = $("rev-site"), con = $("rev-consume");
    function row(k, v) { return "<dt>" + k + "</dt><dd>" + (v || " - ") + "</dd>"; }
    if (site) site.innerHTML = row("Name", val("f-name")) + row("Address", val("f-address")) + row("Business type", val("f-business")) + row("DNO region", val("f-dno")) + row("Tariff", val("f-tariff")) + row("Grid connection", val("f-mic") ? val("f-mic") + " kVA" : "") + row("Location", (val("f-lat") && val("f-lon")) ? val("f-lat") + ", " + val("f-lon") : "");
    var m = $("f-method").value, method = { manual: "Manual entry", csv: "CSV / Excel upload", bill: "Bill scan" }[m] || m;
    var rows = row("Method", method);
    if (m === "manual") { rows += row("Annual consumption", val("f-annual") ? (+val("f-annual")).toLocaleString() + " kWh" : ""); rows += row("Peak demand", val("f-peak") ? val("f-peak") + " kW" : "Estimated"); }
    else if (m === "csv") { rows += row("File", ($("f-hh").files[0] || {}).name || " - "); }
    else if (m === "bill") { rows += row("File", ($("f-bill").files[0] || {}).name || " - "); }
    rows += row("Load pattern", chosenArchetype());
    rows += row("Battery", val("f-battery") === "auto" ? "Auto-select (recommended)" : "Custom selection");
    rows += row("Existing solar", val("f-solar-kwp") ? val("f-solar-kwp") + " kWp" : "None");
    if (con) con.innerHTML = rows;
  }

  // ---- demo prefill --------------------------------------------------------------------------
  // Prefills the wizard with the illustrative demo so the user walks the real quote screens, and
  // flags demo=1 so the submit runs the curated demo dataset through the streaming job (same run
  // animation, rich result). Editing any field clears the flag, turning it into a real run.
  function loadDemo() {
    var m = PLACES.filter(function (p) { return p.name === "Nottingham"; })[0];
    $("f-name").value = "Demo Cold Store (illustrative)";
    $("f-business").value = "Supermarket";
    applyPlace(m);
    $("f-tariff").value = "Half-Hourly C&I (DUoS RAG)"; renderTariff();
    $("f-mic").value = "500"; $("f-annual").value = "1103372"; $("f-peak").value = "248.5";
    // Consents & grid arrangements - the demo declares FULL consent + an export route (issue 018),
    // so the consents page shows the approved, ready-to-price state rather than blank fields.
    ["wz_g98", "wz_g99", "wz_g100", "wz_dno_application_state"].forEach(function (id) {
      var e = $(id); if (e) e.value = "approved";
    });
    var setv = function (id, v) { var e = $(id); if (e) e.value = v; };
    setv("wz_import_mpan", "2000000000001");   // illustrative demo MPAN
    setv("wz_export_mpan", "1900000000001");   // matches the demo SiteModel
    setv("wz_mec_kw", "500");                  // MEC = MIC (import = export capacity)
    setv("wz_export_route", "flexibility_aggregator");
    setv("wz_aggregator_cut_pct", "20");
    var d = $("f-demo"); if (d) d.value = "1";
    dailyNote(); drawCurve(); refreshGating();
  }
  var demo = $("load-demo");
  if (demo) demo.addEventListener("click", loadDemo);
  // A real edit by the user turns a prefilled demo into a real run of their own data.
  form.addEventListener("input", function () { var d = $("f-demo"); if (d) d.value = ""; });
  if (/[?&]demo=1\b/.test(window.location.search)) loadDemo();

  // ---- submit -> shared run-progress overlay -------------------------------------------------
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (missingItems().length) { show(5); refreshGating(); return; }
    if (window.GridQRun) window.GridQRun.run(new FormData(form));
  });

  // ---- init ----------------------------------------------------------------------------------
  initMap();
  show(1);
})();
