# Report specification: the client-facing BESS feasibility validation report

**Author role:** independent report architect (design judgement only; no code, no numbers).
**Date:** 2026-08-12. **Branch reviewed:** `agent/revenue-data-drive`.
**Verified against source:** `src/gridq/outputs/consulting_report.py`, `src/gridq/outputs/viewmodel.py`,
`src/gridq/schemas/{quote,sizing,dispatch_profile,customer_bill,site,connection,supply,optimisation,finance,export_arrangement,provisioning}.py`,
`src/gridq/app/view.py` (chart islands), `docs/wave5_scope.md` §4, `docs/north_star.md`,
`docs/validation_requirements.md`, `docs/grundon_harwell/04_site_models.md`, the brand spec
(`neura_brand_spec.md`, this directory) and both pages of
`Neura Energy Power-Provision Overview (1).pdf`.

**What this report is.** One self-contained HTML document per run record, per site: a feasibility
validation for a behind-the-meter battery, produced automatically from the `QuoteRecord`. The
engagement covers Harwell plus the Grundon sites (six site models are on file per
`docs/grundon_harwell/04_site_models.md`; only Harwell has metered half-hourly data; every Grundon
shape is modelled from a reference profile still pending a source, so every Grundon band is Low
today; three sites have no supplied connection capacity). The report must therefore be at its best
on a degraded, marginal or negative case, because that is what it will mostly show. A
high-confidence negative result is a successful validation and is presented as one.

**Non-negotiable constraints this spec is designed inside:**

1. `consulting_report.py` computes no number; an AST test fails the build on any arithmetic
   operator in that module. Chart geometry therefore lives in a separate module (E-0 below).
2. Every displayed figure comes from the run record via `OutputViewModel` (or the schema objects
   already passed alongside: `SiteModel`, confidence, data asks). A figure the record does not
   carry renders as a reasoned "unavailable", never blank, never zero, never estimated.
3. The brand spec is non-negotiable: palette, type, layout idioms, the hatched
   available-capacity device, the cream area fill, the orange emphasis series. One deliberate
   divergence: **no em-dashes anywhere in the rendered report** (and no en-dashes; see §C).
4. Self-contained output: no external assets, no CDN, no remote fonts. Inline SVG only.
   No JavaScript is required by any exhibit in this spec (see the 3D-surface decision, §B recommendation).

---

## A. Report structure

### A.1 Verdict on the existing SCR spine: keep it, relabel its surface

The existing running order (executive summary, situation, complication, resolution, evidence,
what would change this) survives contact with this engagement and should be kept:

- It leads with the answer, which is what a client paying for a validation needs first.
- The Complication section is the honesty machinery `docs/validation_requirements.md` demands:
  the caveat ladder, the unfiltered warnings, the "what the case leans on" table. Nothing in the
  brand reference conflicts with it; the PDF itself leads with a constraint ("Grids are massively
  constrained") before the product.
- Sections V and VI map one-to-one onto the §7 validation record (HH basis, reconciliation,
  band and the input that capped it, streams and their evidence, assumption version, unmet
  checklist items). Discarding the spine would mean rebuilding exactly that.
- The section behaviour is pinned by tests; the spine is load-bearing code, not just prose order.

What changes is the **surface**: the internal McKinsey labels ("Situation", "Complication") are
consultant jargon on a client page. Adopt the PDF's labelling idiom: grey roman numeral above a
small-caps orange label in the left margin, with a substantive name. Renaming is a deliberate,
test-updating change (tests currently assert the numbered headings).

### A.2 Running order, purpose, length targets

Print target: **8 A4 pages, hard ceiling 10** (the brand reference fits a company thesis on 2;
a six-site engagement cannot afford 20-page site reports). Lengths below are print-equivalent.

| # | Margin label | Content (existing function it evolves) | Target |
|---|---|---|---|
| 0 | Masthead | Neura logo (inline SVG), right-aligned small caps `SITE FEASIBILITY VALIDATION` plus the record date, `PRIVATE & CONFIDENTIAL` in orange, serif sentence-case headline naming the site, sub-line with quote id, mode, data tier. Then the two banners (bankability; dispatch-not-solved) and the confidence strip. (`_head`, `_banner`, `_confidence_strip`) | 0.5 page |
| I | THE ANSWER | Verdict + qualifier, key-figures panel (Exhibit 1), caveat ladder, worst first, none filtered. (`_executive_summary`) | 1 page |
| II | THE SITE | The physical and contractual object being validated: system diagram (Ex 2), load-duration curve (Ex 3), load/basis table (Ex 4), tariff and connection table, consents and gates. (`_situation`) | 1.5 to 2 pages |
| III | WHAT COULD BE WRONG | Unchanged in content: solver alarm, covenant prose, band cap, "what the case leans on", the run's own warnings verbatim. Deliberately figure-free; this section is read, not skimmed. (`_complication`) | 0.75 to 1 page |
| IV | THE SYSTEM AND ITS ECONOMICS | Spec panel (Ex 5), firming diagram (Ex 6), sizing map (Ex 7), revenue stack (Ex 8), bill before/after (Ex 9), cashflow chart (Ex 10), DSCR vs covenant (Ex 11), returns table (Ex 12). (`_resolution`) | 2.5 to 3 pages |
| V | THE EVIDENCE | Assumption register (Ex 13), scenario spread (Ex 14), counterparty (Ex 15), provenance spine (Ex 16), cashflow appendix table. (`_evidence`) | 1 to 1.5 pages |
| VI | WHAT WOULD MOVE THIS | Ranked data asks (Ex 17), binding factors of the band (Ex 18). (`_what_would_change`, minus the static boilerplate, §D) | 0.5 to 0.75 page |
| — | Footer | Hairline rule; `NEURA ENERGY` left; run identity (quote id, code/schema/assumption versions) right; the no-arithmetic statement. | — |

Notes:

- **Headline formula** (never authors a claim): `<Site name>.` then a fixed sub-clause branched
  on the recorded basis, exactly as `_basis_row` branches today: metered = "A battery feasibility
  validation from the site's own metered data."; modelled/mixed = "A battery feasibility
  validation on a modelled load basis, stated as such."; basis absent = "A battery feasibility
  validation. Load basis unrecorded."
- **Page numbers**: `PAGE n OF m` in pure CSS is unreliable (no cross-engine `counter(pages)`).
  In-browser HTML renders the footer without page numbers; if a paged renderer (e.g. WeasyPrint)
  is later added for PDF export, enable `@page` margin boxes then. Do not fake page numbers.
- **Negative and refused results restructure themselves**: on `no_viable_configuration` or a
  not-solved solver status, section IV's exhibits each render their reasoned unavailable state
  (§B, per-exhibit degradation), the verdict leads with the refusal language already in
  `_recommendation`, and the report stays short. A capacity-blocked site (three of six) must
  read "connection capacity unknown", never "no viable configuration"
  (`docs/grundon_harwell/04_site_models.md` §2 blocking finding).

### A.3 Where chart arithmetic lives (architecture rule for the builder)

`consulting_report.py` keeps its zero-arithmetic AST invariant and embeds finished SVG strings.
A new module (working name `src/gridq/outputs/report_charts.py`, item E-0) turns record series
into inline-SVG geometry, following the precedent of `gridq.app.view`'s chart islands
(`surface_island`, `dispatch_island`, `finance_island`, `value_stack_island`), which already do
scaling arithmetic outside the report module and pre-format all text server-side. Its
invariants, test-enforced:

- Geometry only: it may scale, sort, count and position record values; it may never create a
  **displayed financial figure**. Every text label, tick, callout and legend value in the SVG is
  either (a) a record/view-model field passed through `gridq.outputs.formatting`, or (b) a fixed
  categorical string.
- Axis ticks show round values only where they are pure geometry (a 0 to 100% axis, clock labels
  via the `_hhmm` pattern); any £/kW/kWh/ratio tick label must be a formatted record value
  (the series min, max, or a recorded reference value), not an invented round number.
- Deterministic: same record produces byte-identical SVG.
- Single-look light document (a fixed-brand printed page, not a themed app surface); colors only
  from the brand tokens plus the one signal color (§B.0).

---

## B. Figure inventory

### B.0 Conventions that apply to every exhibit

- **Figure title**: small caps, letterspaced, `--neura-ink`, hairline rule beneath (brand figure
  idiom). Beneath it the existing exhibit machinery: `EXHIBIT n` eyebrow plus the **finding
  sentence** (every caption is a finding traceable to the record, not a description). Source line
  stays, reworded without the dash: `Run record <quote_id> · <path>. Formatted, never computed.`
- **Unavailable state**: an exhibit that cannot render draws its empty frame (title, hairline,
  axis box in `--neura-cream-light`) with the reasoned unavailable sentence centred in grey
  italic. The frame keeps layout stable and makes the absence a first-class exhibit. Never a
  blank, never a zero, never a placeholder chart.
- **Hatching semantics are reserved**: the 45-degree orange-peach hatch means *available or
  unused capacity* (page-1 idiom) and, by extension in the revenue stack, *potential not counted
  in the floor*. It is never used for errors, infeasibility or absence.
- **The one signal color**: the brand palette contains no red, but this report has hard-stop
  states the marketing PDF never has (NOT BANKABLE, DISPATCH NOT SOLVED, covenant FAIL).
  Decision: keep one out-of-palette signal color, the current `#A5261F`, documented as the only
  non-brand color, used **only** for: the two stop banners, covenant-fail markers/text, and
  unavailable-reason text. Severity ordering elsewhere is carried by weight and contrast, not
  color: the LOW confidence chip is solid `--neura-ink` with white text (the heaviest object on
  the page; LOW is the most common state on this portfolio and must not read as quiet), MEDIUM
  is `--neura-orange-mid`, HIGH is `--neura-orange`. A band is evidential strength, not an
  alarm, so bands never borrow the signal red.
- **Readiness tags** used below:
  - `DATA READY`: every field is already exposed to the report layer (view model or the objects
    already passed in); the exhibit needs only E-0 (the SVG chart module).
  - `PLUMBING`: the data is persisted on the `QuoteRecord` or `SiteModel` today but is not
    exposed to the report layer; a copy-through or parameter is needed (named per exhibit).
  - `ENGINEERING-GATED`: the data does not exist on any record today; an engine/ingestion change
    must land before the exhibit can ever render honestly.
- **Alt text**: every SVG carries a one-sentence finding as alt text, same prose rules as §C.

### B.1 Exhibit 1 · THE CASE IN SEVEN FIGURES  `DATA READY`

- **Sits in:** section I, directly under the verdict.
- **Question:** what does the record say this site earns, returns and costs, at a glance?
- **Data:** the existing `_executive_summary` rows, unchanged: `vm.split.floor_total_gbp` (via
  `_floor_total`, empty floor stays unavailable), `vm.split.upside_total_gbp`, `vm.returns.irr_pct`,
  `vm.returns.payback_years`, `vm.dscr.min_dscr` + basis + covenant verdict, `vm.cashflows.year0_p50`,
  `vm.price.value_gbp_per_kwh` + `_price_note`. Add one row: levered (equity) return,
  `vm.returns.equity_irr_pct` (needs E-5 copy-through; reasoned unavailable until then, reason:
  "no debt structure on the record").
- **Form:** the existing key-value panel, brand-restyled (cream-light zebra, hairline rules). Not
  stat tiles: half these figures are routinely unavailable on this portfolio, and a reasoned
  absence reads honestly in a table row but looks broken in a big-number tile.
- **Brand idiom:** table typography; orange only for the verdict line above it.
- **Degrades:** per-row reasoned unavailable (existing `_num`/`_na` machinery), never a zero.

### B.2 Exhibit 2 · THE SITE AS CONNECTED (system diagram)  `DATA READY`

- **Sits in:** section II, first exhibit; the reader sees the physical object before any number.
- **Question:** what physical arrangement and metering boundary is being underwritten, and what
  are its recorded capacities?
- **Data:** all from objects already passed to the renderer. Grid/connection: `site.connection`
  (`mic_kw`, `asc_kw`, `mec_kw`, `voltage_level`, `dno_region`, `anm_or_curtailment_notes` as a
  footnote when present). Boundary: `site.supply_boundary` (`import_mpan`, `behind_the_meter`,
  `metering_point`). Load block: `site.load_data.annual_kwh`, `max_import_kw`. Battery block:
  `vm.spec.selected_power_kw`, `selected_energy_kwh`, `duration_hours`. Export path basis:
  `site.connection.mec_kw`, `site.export_arrangement.route`/`counterparty`, and whether the
  record carries any export or gated stream (`vm.split.upside` rows).
- **Form:** a left-to-right inline-SVG block diagram: GRID node, connection bar carrying the
  MIC/ASC/MEC value chips, import meter node (MPAN beneath), then inside a dashed perimeter box
  the site busbar, SITE LOAD block and BATTERY + INVERTER block. A diagram, not a chart: the
  question is topology and boundary, and a picture answers it in one glance where a table
  cannot.
- **Metering boundary:** the dashed perimeter is labelled "Behind the meter" only when
  `supply_boundary.behind_the_meter is True`; `None` renders the label "boundary unconfirmed"
  (never assumed, matching the schema's own comment).
- **Export flow (decision):** show it. Export restrictions are lifted for this study, so the
  report must show where exported energy would flow; but the arrow's *state* is recorded, not
  assumed: solid orange arrow with the MEC chip when `mec_kw` is recorded or
  `export_arrangement.route` is not UNKNOWN or an export/gated stream is on the record;
  otherwise a dashed grey arrow labelled "export path: no arrangement recorded". The study
  assumption never upgrades the record.
- **Brand idiom:** ink boxes and rules, orange battery block, peach meter node, cream-light page
  tint behind the perimeter. No hatch (reserved).
- **Degrades:** unknown fields render "not recorded" chips (e.g. no ASC, no MPAN). No sized
  system (`selection_status` refused / unsized record): battery block drawn as a dashed outline
  labelled "no system selected on this record". Site absent: whole exhibit renders the framed
  unavailable with `_NO_SITE`.

### B.3 Exhibit 3 · THE SHAPE OF THE LOAD (load-duration curve)  `ENGINEERING-GATED (E-2)`

- **Sits in:** section II, beside/after the system diagram; page-1 idiom of the PDF.
- **Question:** how much of the year does the site actually use its connection, and how much
  capacity sits unused for the battery to work with?
- **Data:** a persisted load-duration projection of the site's own half-hourly import series.
  **Not exposed today**: the full-year series is a file reference
  (`site.load_data.half_hourly_file_ref`), not record data; nothing downstream of the run
  carries the year. What must be plumbed: E-2, a `LoadDurationSummary` persisted additively on
  the `QuoteRecord` (mirror of the `DispatchProfile` pattern: engine/ingestion-side, built from
  the same series the MILP solved, hash-excluded, `None` for engines/runs without it), carrying
  an ordered percentile vector of import kW (e.g. 101 points, p0 to p100), the peak, the
  interval count and the basis stamp. Reference lines: `site.connection.mic_kw` / `asc_kw`
  (already on the site object).
- **Form:** load-duration curve: x = share of hours (0 to 100%, pure-geometry axis), y = kW;
  ink curve over cream fill ("the load"); hatched region between the curve and the connection
  capacity rule labelled "Available capacity". The single most brand-native exhibit, and the
  right form for "how often" questions a time-series plot cannot answer.
- **Callout:** the PDF's "battery imports in N% of hours" callout is **omitted for now**: that
  is an annual dispatch statistic no record carries (the persisted dispatch profile is one day).
  If wanted later it is a small engine-side stat rides E-2 or the dispatch profile (E-8,
  optional). Never computed at render time from one day.
- **Brand idiom:** the signature hatch device, exactly as page 1; peak labelled with
  `fmt.kw(peak)` at x = 0; capacity rule labelled with `fmt.kw(mic_kw)`.
- **Degrades:** no persisted projection: framed unavailable, reason "the run record carries no
  load-duration projection of the half-hourly series" plus the basis note. MIC not supplied
  (three of six sites): curve and cream fill render, the hatch and capacity rule are **omitted**
  and an in-frame label states "connection capacity unknown: not supplied with the site data",
  so unused capacity is never implied. Modelled basis: the exhibit carries the basis stamp
  ("modelled from <reference profile>, scaled to <scaled_to>") from `site.load_data`.

### B.4 Exhibit 4 · HOW THE LOAD IS KNOWN (load and basis table)  `DATA READY`

- **Sits in:** section II, after the curves.
- **Question:** what is the evidence status of the load data every number depends on?
- **Data & form:** the existing `_load_rows` + `_basis_row` table unchanged (basis stamp with its
  meaning, reference profile, scaled-to, annual kWh, interval count, coverage window, peak,
  tier), plus the existing tariff/connection table (`_tariff_rows`) and consents/gates grid.
  These stay tables; they are registers, not shapes.
- **Degrades:** as today (reasoned per-row unavailability; `_NO_BASIS` wording kept).

### B.5 Exhibit 5 · THE SYSTEM ON THE RECORD (spec panel)  `DATA READY`

- **Sits in:** section IV, first.
- **Question:** what system did the search select, and on what verdict?
- **Data:** existing `_spec_exhibit` rows (`vm.spec.*`) plus two additions: `selection_basis`
  and `selection_reason` from the sizing grid (needs E-3; renders the honest distinction between
  a covenant-feasible optimum and an indicative fallback, which `SizingGrid` records precisely
  for this purpose), and `constraints_binding` from the optimisation result (needs E-6) so the
  reader sees whether the chosen size was limited by connection, throughput or energy.
- **Form:** key-value panel.
- **Degrades:** as today (`no_viable_configuration` renders the verdict prose; unsized renders
  the no-spec reason). With E-3/E-6 absent, the two new rows render reasoned unavailable.

### B.6 Exhibit 6 · HOW THE BATTERY FIRMS THE CONNECTION (firming diagram)  `PLUMBING (E-3) now, full after E-1`

The report's centrepiece, replicating the PDF's page-2 figure from the real solved dispatch.

- **Sits in:** section IV, immediately after the spec panel.
- **Question:** on a real solved day, when does the battery carry the site, when does it
  recharge, and how far below the connection ceiling does the site stay?
- **Data:** `quote.dispatch_profile` (persisted; one deterministic representative day, the
  highest-total-discharge day of the metered horizon): per half-hour `site_load_kw`,
  `charge_kw`, `discharge_kw`, `soe_kwh`, `red_band`; plus `profile.power_kw`, `energy_kwh`,
  `interval_hours`, `selection_basis`, `representative_day_index`. Reference rules:
  `site.connection.mic_kw` (solid ink), `asc_kw` (dashed grey). Obligation overlay when
  `site.provisioning_requirements` exist: the declared windows and `import_cap_kw` line.
  **Plumbing:** the view model does not carry the dispatch profile; pass `quote.dispatch_profile`
  into the renderer exactly as `site`/`confidence`/`data_asks` are passed (E-3).
  **Engineering for the full figure:** `grid_import_kw` per point (E-1). Grid import is a solver
  variable; persisting it keeps the "From the grid" area a recorded series rather than a
  render-time derivation (load minus discharge plus charge), which would author a series the
  record does not carry.
- **Form:** two stacked SVG panels sharing a 00:00 to 24:00 clock axis (labels via the `_hhmm`
  pattern), exactly the PDF composition:
  - **Top panel (y in kW, not %):** cream area = `grid_import_kw` ("From the grid"); ink line =
    `site_load_kw`; the gap where load exceeds grid import filled solid orange ("From the
    battery"); the gap where grid import exceeds load filled peach ("Recharge"). Both fills are
    pure geometry between two recorded series; no derived series is plotted. DUoS red-band
    half-hours shaded as light vertical bands (`--neura-cream-light`) with one bracket label
    "DUoS red band". MIC rule solid ink, labelled `fmt.kw`; ASC rule dashed grey when recorded.
    **Honesty note the builder must respect:** no record carries a half-hourly available-import
    series, so the PDF's *dipping* capacity line must not be imitated; capacity renders as the
    flat recorded rule(s) only. If a `ProvisioningRequirement` exists, its windows render as
    bracketed spans with the recorded `import_cap_kw` line inside them, and the exhibit becomes
    the PDF's shortfall story with a recorded basis.
  - **Bottom panel (y in kWh, deliberately not %):** orange line = `soe_kwh`; grey dashed rule
    at `profile.energy_kwh` labelled "usable capacity" with `fmt.kwh`. Axis in kWh avoids any
    percent derivation. A "minimum reserve" dashed rule renders **only** when a recorded
    held-state-of-energy commitment exists on the record (Wave-7 commitment schema); it is never
    drawn otherwise, because the PDF's reserve line is an illustration and this one may not be.
  - **Caption row:** the day's mode shares (charging / discharging in red band / discharging
    off-peak / idle), counted from the same persisted day exactly as `utilisation_island` counts
    them, with the standing basis note: "Representative day (<selection_basis>), a real solved
    day of the metered horizon. Not an average over days."
- **Y-axis unit decision:** kW/kWh, not the PDF's percentages. Percent axes require dividing by
  a base (authored derivation) and hide the actual scale a diligence reader needs. The PDF is a
  concept sketch; this is an exhibit.
- **Brand idiom:** the full page-2 idiom: cream "From the grid", solid orange "From the
  battery", peach "Recharge" bands, dashed grey references, orange SoC trace.
- **Degrades:** no `dispatch_profile` (Grundon sites until they run with the MILP hook; any
  unsolved run): framed unavailable, reason "no representative dispatch day is persisted on this
  record" plus the solver-status consequence line. Without E-1 (`grid_import_kw` absent on older
  records): render the degraded top panel: load line, discharge as an orange area from the
  x-axis, charge as peach area, capacity rules, with an in-frame note "grid import series not
  recorded on this run; battery contribution shown against load only". MIC not supplied: no
  capacity rule; in-frame label "connection capacity unknown: not supplied with the site data"
  (the `04_site_models` blocking-finding wording). SoC panel renders whenever the profile
  exists.

### B.7 Exhibit 7 · THE SIZING MAP (power by duration by return)  `PLUMBING (E-3)`

**Recommendation: a 2D cell map, not a 3D surface.** The client asked for "the 3D sizing
surface"; the requirement it names (Wave 5 §4: power by duration by levered return, P90-DSCR
feasibility, optimum marked) is better served in a static report by a flat evaluated-cell map.
The defence, in the order that matters in this repo:

1. **Honesty.** The app's WebGL surface interpolates a dense mesh (`_dense_surface`,
   `_bilinear` in `gridq.app.view`): it displays return values at sizes the solver never
   evaluated. Tolerable in an interactive explorer with tooltips on real points; not in a
   client validation document where every number must trace to the record. A cell map draws
   one cell per real solve and nothing between cells. For the same reason, **no interpolated
   iso-return lines**: an iso-line asserts a return at coordinates that were never solved. The
   feasibility boundary emerges from cell colouring, which asserts nothing between evaluations.
2. **Reading.** A static page cannot rotate a surface: one fixed projection self-occludes
   (ridges hide valleys), and reading (power, duration) coordinates off a projected axis is
   guesswork. The underwriting questions are 2D questions: where is the feasible region, how
   flat is the optimum (selection risk), is the chosen point interior or pressed against a
   boundary (a binding constraint), did the search actually explore around it. Position plus
   colour answers all four; projected height answers none of them better.
3. **Medium.** WebGL does not print; vendored Plotly adds megabytes and a JS dependency to a
   document that otherwise needs none; an inline-SVG map prints perfectly, is deterministic and
   byte-stable, and keeps the report self-contained.

- **Sits in:** section IV, after the firming diagram ("this size, chosen from this space").
- **Question:** where in the power-duration space did the search look, what did each candidate
  return, which candidates could service debt, and why was this one chosen?
- **Data:** `quote.sizing_grid` (persisted since W5-0/D23; **not exposed to the report layer**:
  pass it through like `site`, E-3). Fields per `SizingGridPoint`: `power_kw`,
  `duration_hours`, `levered_return_pct` (z; fall back to `project_irr_pct` exactly as
  `surface_island` does, and say which in the legend), `min_p90_dscr`, `passes_covenant`,
  `stage`, `proxy`; grid-level `selected`, `selection_basis`, `selection_reason`, `covenant`,
  `search_version`. Reuse `surface_island`'s established rules: the cell lattice comes from the
  lattice stages only; refine/confirm evaluations of a continuous search overlay as point
  markers at their true coordinates; where one cell was evaluated twice, confirmed beats proxy.
- **Form:** x = power (evaluated values as ordinal ticks, labelled with `fmt.kw`), y = duration
  (`fmt.hours`); each evaluated cell filled on a single-hue orange ramp (peach to deep orange)
  by return. Monochrome ramp is colour-blind-safe by lightness. In-cell value labels
  (`fmt.pct`) whenever the lattice is small enough to stay legible (at most ~6 by 6; the legacy
  `enumerate-v1` grid is 3 by 3, where a labelled matrix beats colour alone). Unevaluated cells
  stay white with a faint rule and one legend entry "not evaluated" (holes stay holes).
  Overlaid markers: confirm-stage points as filled ink dots ("confirmed on the full series"),
  proxy points as hollow grey dots with the legend line "typical-day proxy: guides the search,
  never a reported figure". The selected optimum: orange ring plus label
  `<fmt.kw> × <fmt.hours>` from `selected`, with `selection_basis` in the caption; an
  `indicative_fallback` selection must be captioned as "a recommendation, not a feasible
  optimum" (the grid schema records exactly this distinction, N16).
- **Feasibility layer:** covenant-passing cells at full saturation; failing cells desaturated
  toward cream with their value in grey italic; `passes_covenant is None` renders the cells on
  the ramp but the legend states "debt feasibility not measured: no debt structure on the
  record (B001)". On this portfolio that None case is the common case, so it is a designed
  state, not an edge case. Never coerce None to fail (the schema forbids it; so does this spec).
- **Caption:** the provenance line `surface_island` already composes (search version, evaluation
  counts, confirmed vs proxy), plus `constraints_binding` (E-6) when present: "the selected size
  was limited by <binding constraints, verbatim>".
- **Brand idiom:** orange sequential ramp, ink markers, cream desaturation. No hatch
  (reserved; infeasibility must not read as "available").
- **Degrades:** `sizing_grid is None` or empty points: framed unavailable, reason "the run
  record carries no evaluated sizing grid" plus, when the engine was not the MILP, the solver
  consequence line. No return metric on any point (`has_surface` false in the island's terms):
  render the honest table of evaluated points instead of a map (the app already makes this
  exact fallback decision; keep it).

### B.8 Exhibit 8 · THE REVENUE STACK, FIRM AND GATED  `DATA READY`

- **Sits in:** section IV, after the sizing map.
- **Question:** which streams make up the priced floor, what sits outside it as upside, and how
  big is each?
- **Data:** `vm.split.floor` / `upside` / `excluded` rows (`stream`, `annual_revenue_gbp`,
  `firmness`, `gate_status`, `unlock_condition`, `consent_status`), `floor_total_gbp`,
  `upside_total_gbp`; stream labels via `fmt.stream_label`.
- **Form:** horizontal bar per stream, grouped floor / upside / excluded with subtotal rules
  between groups; bar length = recorded annual revenue; value labels `fmt.gbp` at bar end.
  Floor bars solid orange; **upside bars hatched** (the brand device in its extended sense:
  available, not counted in the floor); excluded streams grey outline with the "excluded"
  label. A "consent given" chip renders on rows whose `consent_status == "given"`. The stream
  table (gate, unlock condition columns) stays beneath as the register; the chart answers the
  proportion question tables cannot.
- **Brand idiom:** hatch = not-yet-firm potential; orange = counted; the `_FLOOR_NOTE`
  ("priced floor is a firmness classification, not a payer claim") renders once beside the
  totals, reworded without dashes.
- **Degrades:** empty floor: the chart renders only the upside/excluded groups and the floor
  subtotal line shows the reasoned unavailable (`_NO_FLOOR`); no streams at all: framed
  unavailable ("the record carries no value streams").

### B.9 Exhibit 9 · THE BILL, BEFORE AND AFTER  `PLUMBING (E-4)`

- **Sits in:** section IV; the customer-promise exhibit.
- **Question:** what does the site's annual bill look like with and without the battery, and
  where does the change come from?
- **Data:** `quote.customer_bill` (persisted, W7-5/C017): full decomposition
  `energy_before/after_gbp`, `duos_before/after_gbp`, `levies_before/after_gbp`,
  `standing_gbp`, `capacity_gbp` (battery-invariant fixed lines), totals, `annual_saving_gbp`,
  `import_before/after_kwh`, `peak_import_before/after_kw`, `basis`. **Plumbing:** the view
  model's `BillSavingsView` exposes only the totals, peaks and basis; extend it with the
  component fields (verbatim copy-through, E-4).
- **Form:** two stacked columns (BEFORE, AFTER), segments coloured energy `--neura-orange-mid`,
  DUoS `--neura-orange`, levies `--neura-peach`, fixed lines `--neura-rule` grey with the label
  "fixed: unchanged by the battery"; each segment's recorded value labelled. A bracket between
  column tops labelled with `annual_saving_gbp`. **A negative saving renders exactly the same
  way**, bracket pointing up, captioned in plain words ("the bill rises under this dispatch;
  losses are bought at the price faced"): the schema explicitly represents this case and the
  exhibit must not hide it. Beneath: a two-dot row for peak import before/after with the
  standing note that peaks are informational and never priced (C029).
- **Brand idiom:** stacked-area family colours; the recorded `basis` string renders under the
  headline verbatim (tests already require the basis alongside the saving wherever shown).
- **Degrades:** no persisted bill: the framed unavailable with the existing pending seam
  (`_BILL_PENDING_REASON`) and the data ask. Totals-only records (pre-E-4): paired plain columns
  without decomposition plus the note "component split not exposed on this record".

### B.10 Exhibit 10 · THE CASHFLOW SERIES, P50 AND P90  `DATA READY`

- **Sits in:** section IV.
- **Question:** what does the case pay back, on the central and the conservative series?
- **Data:** `vm.cashflows.p50` / `p90` (verbatim series), `year0_p50`, `operating_years`.
- **Form:** per-year vertical bars for P50 (orange, year 0 negative below the zero rule) with
  the P90 value overlaid as an ink tick per year; zero rule in ink; first/last/minimum values
  labelled via `fmt.gbp_compact`. Bars-plus-ticks reads the P50/P90 gap year-by-year without
  implying a continuous curve between annual points.
- **Brand idiom:** orange series + ink reference marks; cream background band for the
  construction year (year 0) if labelled.
- **Degrades:** no series: framed unavailable ("the record carries no P50/P90 cashflow
  series"). The full year-by-year table moves to section V as the audit register (§D).

### B.11 Exhibit 11 · DEBT SERVICE AGAINST THE COVENANT  `DATA READY`

- **Sits in:** section IV, beside the returns table.
- **Question:** does the case service its debt in every year, on the basis the covenant tests?
- **Data:** `vm.dscr.by_year` (verbatim), `basis`, `covenant`, `covenant_passes`, `min_dscr`;
  debt terms from `vm.debt` in the caption.
- **Form:** one dot per year on a common scale, covenant as a grey dashed rule labelled
  `fmt.ratio`; the minimum year's dot labelled; failing years' dots in the signal color (the
  only chart use of it). A dot plot, because DSCR is a per-year test against a threshold, not a
  trend to smooth.
- **Brand idiom:** dashed reference rule (brand dashed grey); ink dots.
- **Degrades:** the common case on this portfolio: `vm.dscr.available` false renders the framed
  unavailable with the recorded reason ("no debt structure on the record; DSCR is measured,
  never guessed", B001) and the standing rate/tenor seam note. This exhibit unavailable is
  itself a finding and stays in the flow.

### B.12 Exhibit 12 · WHAT THE PROJECT RETURNS  `DATA READY (fuller with E-5)`

- **Sits in:** section IV, last.
- **Data & form:** the existing `_resolution` returns key-value panel (project IRR with its
  unlevered note, payback, min DSCR + covenant, gearing/rate/tenor with the seam note, price
  hits-target verdict), plus the levered return row once E-5 lands. Stays a table.
- **Degrades:** as today; every row individually reasoned.

### B.13 Exhibit 13 · EVERY ASSUMPTION, WITH ITS SOURCE  `DATA READY`

- **Sits in:** section V, first; the traceability moat where a lender looks.
- **Data & form:** the existing register grid (`vm.assumption_sources`: key, value, unit,
  status, confidence, source) with cream-light zebra. Status column gets a small chip:
  `confirmed` ink, `placeholder`/`needs_review` orange-mid so unconfirmed content is visibly
  loud (these force `not_bankable` by design and the register is where that fact is legible).
- **Degrades:** no cited keys: the existing two-part honest note (no register can be built; the
  warnings name what the run itself flagged).

### B.14 Exhibit 14 · THE SPREAD UNDER SCENARIOS  `DATA READY`

- **Sits in:** section V.
- **Question:** how far do the return and the debt test move across the recorded scenario set?
- **Data:** `vm.sensitivities` (persisted Low/Central/High rows: `label`, `min_dscr`,
  `irr_pct`, `cod_delay_months`, `note`).
- **Form:** the existing table, plus one compact dot strip per metric (IRR dots for the three
  scenarios on one axis; min-DSCR dots on another with the covenant rule) when at least two
  rows carry the metric. With three rows a table is honest and sufficient; the strip earns its
  place only by making the *spread* visible at a glance. Cells a scenario did not evaluate stay
  the reasoned unavailable, and that scenario simply has no dot.
- **Degrades:** none persisted: the existing "run once, at one set of inputs; a single point is
  not a range" note, framed.

### B.15 Exhibit 15 · WHO CARRIES THE CONTRACT  `DATA READY`

- **Sits in:** section V. The existing counterparty panel (`vm.counterparty`) unchanged, with
  the credit seam note. Table.

### B.16 Exhibit 16 · ONE RUN, PINNED  `DATA READY`

- **Sits in:** section V, last. The existing provenance spine (`vm.provenance`: quote id, mode,
  site id, input hash, assumption/code/schema versions, expiry, created). Table. The honest
  wording about what the stamps do and do not pin (the input hash does not yet pin HH or
  assumption content, F-06/F-09) stays.

### B.17 Exhibit 17 · WHAT TO SUPPLY FIRST  `DATA READY`

- **Sits in:** section VI. The existing ranked data-ask grid (rank, ask, why, what it
  unblocks), finding line naming the top ask. Table.

### B.18 Exhibit 18 · WHAT SETS THE BAND  `DATA READY`

- **Sits in:** section VI. The existing binding-factors block: the band can only be raised by
  the named factors; fixing anything else moves nothing. List, with the band chip per §B.0.

### B.19 Considered and rejected (so nobody re-litigates them silently)

- **A "TIME TO POWER" timeline bar (PDF page 2):** no record carries programme/timeline data
  (only scenario `cod_delay_months` deltas). Drawing one would author a schedule. Excluded
  until a recorded timeline exists.
- **A portfolio comparison exhibit (six sites side by side):** this report renders one run
  record; a cross-site exhibit belongs in a separate engagement-level cover document fed by six
  records, not in the per-site report.
- **An annual "% of hours the battery imports" callout on the load-duration curve:** annual
  dispatch statistic; no record carries it (the dispatch profile is one day). Optional E-8.
- **Interpolated 3D surface / iso-return contours:** rejected on honesty grounds (§B.7).
- **A bill waterfall (before-to-after bridge):** the decomposition supports it, but a waterfall
  of four deltas duplicates the paired stacked columns while being harder to read against the
  battery-invariant fixed lines; the paired columns keep before and after as real, auditable
  totals.

---

## C. Prose rules (checkable; a writing agent follows them, an adversarial reviewer tests them)

Rules 1 to 6 are machine-checkable and should land as tests alongside the existing AST test.
Rules 7 to 14 are review rules for the adversarial pass.

1. **No em-dashes, no en-dashes.** Zero `—` (U+2014) and `–` (U+2013) in generator-authored
   text: prose, findings, chart labels, axis captions, legends, footnotes, alt text, `<title>`.
   Ranges use "to" ("00:00 to 24:00", "8 to 10 pages"); asides use commas, colons, parentheses
   or a new sentence.
   **The verbatim-text collision, resolved:** the report reproduces record text verbatim
   (`quote.warnings` "verbatim and unfiltered", `selection_verdict_reason`, the bill `basis`,
   assumption `source` strings), and some of that text contains em-dashes today. Verbatim
   record text is rendered inside an element the test exempts (`<span class="verbatim">`), and
   the test asserts: after removing `.verbatim` elements, the document contains no em/en dash.
   The seam-note constants in `viewmodel.py` (`_RATE_TENOR_NOTE`, `_BILL_PENDING_REASON`, the
   DSCR unavailable reason) are **generator-authored, not record text**: they are rewritten
   without dashes as part of this work, not exempted.
2. **Prose never authors a number.** Unchanged AST invariant on the report module (no
   arithmetic operators, no `sum`/`round`/`min`/`max`/`abs`). Additionally checkable: any digit
   in a generator-authored string literal must belong to the fixed vocabulary {P50, P90, P415,
   G98, G99, G100, T0, T1, T2, T3, DUoS band names, ISO section numerals}; every other digit in
   the rendered document must have arrived through a `gridq.outputs.formatting` call on a
   record/view-model field. Two positional-label exemptions, both existing machinery kept by
   this spec: the sequential exhibit numerals (`Exhibit 1`, `Exhibit 2`, the one counter the
   module owns) and enumeration indices over record series (`Year 0`, `Year 1` from
   `enumerate` over the recorded cashflows). Ordinal labels are positions, not figures; nothing
   else joins this exemption without a recorded decision. The chart module inherits the same
   rule for SVG text nodes (its geometry coordinates are exempt; its *visible text* is not).
3. **Bold lead-in clause.** Every body paragraph in sections I to VI opens with a bold clause
   (the brand's device): checkable as `<p>` (body class) beginning with `<strong>`, clause of
   at most twelve words ending in a full stop or colon.
4. **Banned lexicon** (case-insensitive, generator-authored text; verbatim spans exempt):
   delve, leverage/leveraged/leveraging (the financial noun "leverage" is allowed only in the
   fixed phrase "levered return"/"unlevered"), robust, seamless, holistic, cutting-edge,
   state-of-the-art, game-changing, unlock (except the record field label "unlock condition"),
   journey, empower, "it's worth noting", "it is worth noting", "in today's", "landscape",
   "dive into", "moreover", "furthermore", "significantly", "substantially", "excitingly",
   "importantly", "unfortunately", "may potentially", "could possibly".
5. **Sentence discipline.** No authored sentence over 35 words; median under 20 per section.
   Checkable by script over the rendered text with verbatim spans removed.
6. **Findings-first captions.** Every `figcaption` finding is a claim about this record or its
   absence, not a chart description: must not begin "This chart", "Chart of", "A plot",
   "Figure showing". (The existing exhibit machinery already produces findings; keep it.)
7. **No tricolon padding, no restatement.** Never three near-synonyms where one word does the
   work; no sentence that only restates its predecessor. Reviewer tests by deleting each
   sentence and asking whether information was lost.
8. **Lead with the finding, then the evidence.** First sentence of every section and every
   caption carries the section's conclusion.
9. **The record is the speaker.** "The record shows / carries / does not carry", "the run
   recorded", "no figure on this page is authored". Never first person, never "we believe".
10. **Negative results are findings, not apologies.** A high-confidence negative validation is
    reported in the same register as a positive one: "No configuration is economically viable
    at these inputs. That is a result, not a gap." Softening words (only, just, merely,
    unfortunately) are banned around verdicts.
11. **Unavailable always carries its reason** (existing `_na` discipline; the reason names the
    missing input and, where one exists, the ranked ask that would supply it).
12. **British English throughout** (modelled, metered, capitalise DUoS, MIC, ASC, MEC, SoE);
    currency and units only through `gridq.outputs.formatting`.
13. **Headline style:** serif, sentence case, ends with a full stop (brand); section labels
    small caps letterspaced; figure titles small caps above a hairline.
14. **Density:** prefer an exhibit to a paragraph wherever a number can be shown; a paragraph
    that restates an exhibit's finding sentence is deleted, not tightened.

---

## D. What to cut from the current report

1. **`_BANKABLE_WORK` (the static four-bullet "path to bankable" list).** Fixed boilerplate,
   identical in all six site reports, partially duplicating the ranked data asks and the
   binding factors that already render beside it from the record. Cut the exhibit; keep one
   authored sentence pointing at the asks and the band factors. (Its finding line survives as
   the section lead.)
2. **The full year-by-year P50/P90 cashflow table in the body.** Up to 26 three-column rows;
   it reads as filler between exhibits. Replaced by Exhibit 10; the verbatim table moves to
   section V as the audit register (it must still exist somewhere: every figure traceable).
3. **The grey-corporate chrome.** The current `_STYLE` (Segoe/Roboto stack, `#a5261f`-on-white
   banner styling, "GridQ · Underwriting review" eyebrow) is replaced wholesale by the brand
   sheet (E-7): serif headline stack, humanist sans body, left-margin roman-numeral labels,
   cream panels, hairline rules, the header/footer idiom. The signal color survives only in the
   narrow role defined in §B.0.
4. **Duplicated caveat framing.** "Every other caveat this pack tests for that fired on this
   record, in the same order:" tightens to "Also fired, in order:". The ladder itself, worst
   first and unfiltered, is untouchable.
5. **Throat-clearing meta-sentences.** One framing sentence per section survives; the rest go.
   Cut, for example, "This is the site as the record describes it:" (the exhibits say it) and
   "Nothing above is legible without this section:" (true, but the register proves it). Keep
   "This section exists to be read against the recommendation." (III) and "The useful question
   about a result like this is not whether it is good but what would move it." (VI); each does
   real work, though the VI sentence must drop nothing when the static list (§D.1) goes.
6. **Em-dash idioms in authored copy throughout** (rewritten per §C.1), including the source
   line "— formatted, not computed." which becomes ". Formatted, never computed."
7. **Not cut, explicitly:** the two banners; the caveat ladder; verbatim warnings; the
   assumption register; the provenance spine; the basis-stamp rows; the covenant prose in
   Complication; the reasoned-unavailable machinery. These are the report's honesty apparatus
   and survive every redesign.

---

## E. Engineering required before exhibits can render

Ordered by leverage. None of this is report-module work; items are engine/schema/view-model
changes this spec depends on. Every one follows an existing additive, hash-excluded pattern.

- **E-0 · The SVG chart module** (`gridq.outputs.report_charts` or similar), per §A.3: record
  series in, finished inline-SVG strings out; geometry arithmetic allowed, displayed financial
  values only via `gridq.outputs.formatting`; deterministic; its own invariant tests. The AST
  zero-arithmetic test stays scoped to `consulting_report.py`, which embeds the returned
  strings. Blocks: every charted exhibit (2, 3, 6, 7, 8, 9, 10, 11, 14 strips).
- **E-1 · `grid_import_kw` on `DispatchProfilePoint`** (engine-persisted per half-hour;
  optionally `available_import_kw` inside provisioning windows). The solver holds these
  variables; persisting them keeps the firming diagram's "From the grid" area a recorded
  series. Additive field, default `None`; older records degrade per §B.6. Blocks: Exhibit 6
  full form.
- **E-2 · A persisted load-duration projection** (`LoadDurationSummary`, additive on
  `QuoteRecord`, mirroring the `DispatchProfile` pattern: built engine/ingestion-side from the
  same half-hourly series the run used, hash-excluded, `None` when absent): ordered import-kW
  percentile vector (e.g. 101 points), peak, interval count, coverage window, basis stamp.
  Blocks: Exhibit 3 entirely (the full-year series is only a file reference today).
- **E-3 · Pass `quote.sizing_grid` and `quote.dispatch_profile` into the report renderer**
  (new keyword parameters on `render_consulting_report` / read in `render_report_for_quote`,
  exactly as `site`, `confidence`, `data_asks`, `half_hourly_basis` are passed today; or
  equivalent views on `OutputViewModel`). Both objects are already persisted on the record;
  nothing reaches the report layer without this. Blocks: Exhibits 6 and 7; feeds 5.
- **E-4 · Bill decomposition copy-through on `BillSavingsView`** (`energy/duos/levies` before
  and after, `standing_gbp`, `capacity_gbp`, `import_before/after_kwh`; verbatim from
  `CustomerBill`). Blocks: Exhibit 9 full form (totals-only fallback renders without it).
- **E-5 · `equity_irr` copy-through on `ReturnsView`** (exists on `FinancialCase`; dropped by
  the view model today). Needed so the returns panel can show the levered return the sizing
  map plots; without it the two exhibits appear to disagree about what "return" means.
  Blocks: one row each in Exhibits 1 and 12.
- **E-6 · `constraints_binding` copy-through on `SystemSpecView`** (exists on
  `OptimisationResult`). One line of caption truth: whether the chosen size sits against a
  binding constraint. Feeds Exhibits 5 and 7.
- **E-7 · Brand assets:** the inline-SVG logo mark and the brand stylesheet implementing
  `neura_brand_spec.md` (tokens, type stacks with graceful system-font degradation, margin
  labels, header/footer, hatch pattern defs, the signal-color rule of §B.0).
- **E-8 · Optional, deferred:** annual dispatch statistics (share of hours charging etc.)
  persisted alongside the dispatch profile, to restore the PDF's "battery imports in N% of
  hours" callout on Exhibit 3; a recorded minimum-reserve/held-SoE commitment (Wave 7) before
  any reserve line may be drawn on Exhibit 6.

**Readiness summary of the four mandated exhibits:** the firming diagram renders degraded after
E-3 and fully after E-1; the sizing map renders after E-3; the system diagram renders now
(needs only E-0/E-7); the load-duration curve is engineering-gated on E-2.

---

## F. Acceptance sketch for the build (how a reviewer knows this spec was followed)

1. Byte-identical re-render from the same record (existing determinism test extends to SVG).
2. The AST no-arithmetic test still passes on `consulting_report.py`; the chart module's
   displayed-text invariant tests exist and pass.
3. A record with `dispatch_profile`, `sizing_grid` and `customer_bill` renders Exhibits 6, 7
   and 9 with every visible number matching the record field it cites (test: parse SVG text
   nodes, compare to formatted record values).
4. A minimal record (no optimisation result, no financial case) renders every exhibit as its
   framed, reasoned unavailable; zero em/en dashes outside `.verbatim`; no banned-lexicon hits.
5. The three capacity-blocked site models render "connection capacity unknown" wording in
   Exhibits 2, 3 and 6 and never render "no viable configuration" for that reason.
6. A `no_viable_configuration` record renders the verdict as a first-class result, all section
   IV exhibits in their reasoned unavailable states, and the report stays within the page
   ceiling.
7. Print check: A4, exhibits unbroken across pages (`break-inside: avoid`), legible grey ramp,
   no JavaScript required for any content.
