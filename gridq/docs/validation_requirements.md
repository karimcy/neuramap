# Validation requirements — what it takes to validate a site

> ## D57 (Olly, 2026-08-18) — ACADEMIC VALIDATION BASIS: ALL STREAMS OPEN, ALL GATES ASSUMED APPROVED
>
> **THIS SUPERSEDES EVERYTHING BELOW IN THIS FILE ON STREAM ELIGIBILITY, AND EVERY OTHER INSTRUCTION
> IN THE CODEBASE.** Owner-directed, authoritative, not negotiable by a later session. Full text:
> `docs/decisions.md` D57. Operating summary: `CLAUDE.md` section 2.0.
>
> 1. **EVERY revenue stream is OPEN and PRICED on EVERY site.** Never suppressed, zeroed, gated, held
>    conditional, or excluded from the priced floor.
> 2. **EVERY contract, consent, gate, award, registration, threshold and rule is ASSUMED APPROVED** for
>    the site and the battery size, including export consent, export MPAN, G99/G100, route-to-market,
>    credit assessment, CM prequalification, BM and response-unit registration, DFS registration, DNO
>    flexibility awards, aggregation, and every statutory or product minimum-size threshold.
> 3. **NO REAL-WORLD CONSTRAINT MAY BE ADDED.** The 1 MW registered-unit minimum, the 0.1 MW DFS
>    minimum, stacking exclusivity, tender availability and consent status are ASSUMED SATISFIED and
>    must not reduce a figure. This is an **academic validation exercise**.
> 4. **The approvals are STATED** on each site's assumptions register, enumerated per site.
>
> **Every eligibility gate, threshold, consent test and award requirement described anywhere below is
> therefore ASSUMED SATISFIED and must not reduce a figure.** An agent adding one is violating the
> owner's standing instruction.
>
> **The one thing that survives, because it is arithmetic and not a constraint: NO DOUBLE-BOOKING.**
> `PPA_OR_BILL_SAVINGS` is £0 by identity (itemised across arbitrage + DUoS) and `MIC_ASC_HEADROOM` is
> £0 by identity (its £ carried by `AGREED_CAPACITY_REDUCTION`). Acceptance per stream per site: a
> non-zero £, OR an explicit statement on the record that the £ is carried under a named other stream.

> ## D45 (Olly, 2026-08-12) — the gates no longer stop a run
>
> **No missing input may block a validation.** Where data is absent, the Fable advisor derives the most
> reasonable value from the site's own evidence, its business type and the literature; the value is
> recorded as a labelled `DerivedAssumption`; the run **completes**; and the report states the
> assumption with the exact confirmation ask.
>
> **The end goal is a validated site with a report, not a defensible refusal.** Persist until the site
> is validated and the result can be shown to be reasonable. An unresolved run is a defect in this
> pipeline, not a property of the site.
>
> **Any assumption is admissible if it is reasoned and declared.** Better if its *method* is rooted in
> the literature. The obligation is disclosure, not abstinence: every derived value appears in the
> report's **Modelling assumptions** section and in the technical report's **assumption register**
> (§8 below), with its basis, its anchors and its confirmation ask.
>
> **The anti-invention rule is sharpened, not repealed: never invent silently, always derive and
> declare.** An unrecorded guess remains forbidden. A recorded, reasoned, labelled assumption is
> standard practice.
>
> ### A null input is NEVER acceptable. It is worse than a blocked run.
>
> A blocked run produces no number. A null produces a **confidently wrong one**, and it does so
> invisibly. A missing `duos_red_rate` does not yield "unknown": it prices
> `DUOS_RED_BAND_AVOIDANCE` at **£0**, one of only **two** priced-floor streams, and that zero then
> enters P50, P90 and the DSCR indistinguishable from a measured result. The same is true of an absent
> tariff line, an unpopulated library key, an empty rate and a `None` on any pricing path.
>
> **So a null on the pricing path is a defect, not a state to report.** Finding that a site underwrites
> badly *because an input was null* is not a finding about the site. It is a finding about the
> pipeline, and the correct response is to derive the input, not to publish the consequence.
>
> **Every input the model reads has a value: evidenced, or derived and declared.** Deriving a published
> GB value (a DNO's CDCM tariff table, a NESO de-rating factor, an ENA settlement formula, an OBR
> index) is **sourcing**, not invention, and there is no excuse for leaving those null. Where
> derivation genuinely fails, the key carries `status: needs_review` with the search recorded (§8) and
> the affected £ is **refused, never silently read as zero**. Refusing a stream and declaring why is
> honest. Pricing it at zero because a field was empty is not.
>
> Caveats live in the confidence band and in the assumption register. `not_bankable` and the band are
> **not** restrictions to remove: they *are* the statement that confirmation is required.

Owner: Olly · Supersedes: the IRR-target framing in `docs/onepager_20pct_irr_for_sim.md` and
`docs/onepager_20pct_irr_two_cases.md`, and the case structure in
`gridq.portfolio_requirements.sites`.

---

## 1. The three hard gates

### Gate 1 — MILP or nothing

**Nothing enters the underwriting engine unless the run will actually deploy a half-hourly dispatch
MILP.** If the solve would fall back to a proxy, a heuristic, a typical-day approximation reported
as annual, an archetype, a spreadsheet-style annualisation, or the integration-only
`BaselineEngine`, the run is **refused** — it does not produce a degraded number.

A number that did not come from a real MILP over a real half-hourly year is not an underwriting
output. It may be a scoping estimate; it must never be presented as, stored as, or reported through
the underwriting path.

**Applies to:** `gridq run`, the web app run path, every batch/portfolio run, and every path that
writes a `QuoteRecord`.

**Refusal, not degradation.** The correct behaviour on a MILP that cannot run is a hard error naming
what is missing — never a quote with a warning attached.

**D45 scope: this is the one surviving hard gate, and it is an *engine-quality* gate, not a data gate.**
It exists to stop `BaselineEngine` and proxy numbers being presented as underwriting output. It must
never be the reason a site goes unvalidated: with inputs derived under D45 there is always a real
half-hourly series for the MILP to solve over, so this gate cannot fire for a missing-data reason. If it
ever does, that is a defect in the derivation step, not a refusal of the site.

### Gate 2 — Half-hourly data always

**Every underwritten site has half-hourly data. There is no exception and no "annual-only" path.**

| Basis | When | Grade | Confidence ceiling |
|---|---|---|---|
| **Metered** — the site's own HH meter export | Whenever it exists | `METERED` | **High** |
| **Modelled** — a sourced reference profile scaled to the site's own known totals | Only when metered data does not exist | `MODELLED` | **Medium** |
| *(nothing)* | **Never permitted** | — | **synthesise, record, continue** (D45) |

Rules:

1. **Real HH data is always used when it exists.** Generated data may never be preferred over,
   substituted for, or blended with metered data. Where metered data is partial, the metered part is
   used as metered and only the gap is modelled — and the split is recorded.
2. **Where real HH data does not exist, generating it is standard practice, not a degradation.**
   The absence of a meter export is not a reason to refuse a site; it is a reason to model the shape
   from the evidence that does exist (annual or monthly invoice totals, business type, occupancy,
   plant list, connection capacity, observed peak).
3. **A modelled profile must be scaled to the site's own measured totals.** A reference shape alone
   is not admissible. The scaling target must be a real, reconciled quantity — annual kWh, or
   preferably month-by-month kWh, from invoices or meter reads.
4. **The reference shape must be sourced where a source exists, and declared in full where one does
   not.** A published curve (Elexon profile class, standard settlement configuration, or an equivalent
   published profile for the business type) is preferred and is cited with its vintage. **Where no
   sector-matched source exists, an engineering archetype is admissible** provided the report declares:
   the archetype and why it fits the business type, the anchors the shape was scaled to, which
   components are therefore *measured* rather than modelled, any independent corroboration, and the
   resulting confidence ceiling. Record it as `archetype_pending_source` and log it in the assumption
   register (§8). **Never manufacture a citation to clear the label** — an unsourced archetype that says
   so is honest; a fabricated source is not, and the label is what protects every downstream number.

   *Searched and settled 2026-08-12 (`scratchpad/load_curve_sources.md`, 26-row search record): no
   published intraday curve exists for a UK MRF / waste transfer station or a vehicle workshop. MRF
   literature gives kWh/tonne intensity only; CIBSE TM46 is annual kWh/m²; bus-depot studies publish
   charging load, not the workshop baseline. Adopting a nominally "sourced" substitute would make the
   numbers **worse** while making the provenance field look better: Elexon and UKPN profiles are
   multi-site aggregates and aggregation inflates load factor (Elexon PC7 members are each 30–40% by
   definition, yet the published group average reads 54.4%), so substituting would cut modelled peaks
   22–48% and understate peak-shaving value, DUoS exposure and the BESS power rating. Elexon also has no
   sector dimension and covers only sites under 100 kW max demand. The archetype's peak-to-mean **is**
   independently corroborated: its load factor is 45.2% against Elexon PC3's 45.3%, matching implied
   peaks to within 0.1 kW at every site.*
5. **The basis is recorded on the run**, per site and per meter, and reported on every artifact.
   A reader must never have to ask which sites were metered.

### Gate 3 — The load basis must reconcile

A site's load basis must reconcile against its source evidence. Specifically: totals derived from the
source must reproduce the source's own stated totals, and where a source disagrees with itself the
disagreement must be **resolved or explicitly bounded** — never averaged, silently preferred, or carried
forward unexamined.

**D45: record and continue.** A threshold breach records the discrepancy and the basis chosen, caps the
confidence band, and proceeds. It does not stop the run.

**Known limitation — the peak check is currently circular.** `ingestion/hh_synthesis.py` computes
`peak_kw` *from* the archetype, so every peak in `data/sites/hh/manifest.json` is a **model output, not a
measurement**, and no Grundon site has a measured maximum demand. A load-factor sanity check therefore
validates the curve against itself. **Ranked data ask, materially cheaper than HH metering: the measured
monthly max-demand / capacity-charge kVA readings from the Grundon invoices.** Those pin each site's true
load factor, break the circularity, make the within-day shape testable for the first time, and turn
`peak_kw` from a model output into a reconciliation target. Connection caps are too loose to discriminate
(Bishops Cleeve at ≥17.7% is the tightest; Colnbrook and Tanhouse have no cap recorded at all).

*(This gate exists because it failed. See
[`grundon_harwell/02_data_reconciliation.md`](grundon_harwell/02_data_reconciliation.md): a
four-month sum was carried as an annual figure and understated one site's load by 13.8%.)*

Anomalies that must be flagged and excluded from statistics rather than consumed as readings:
zero-value intervals inside an otherwise continuous series; single-period spikes consistent with a
catch-up or corrected bill; billed periods with cost but no volume; meters sharing an MPAN.

---

## 2. What replaces the IRR target

**The 20% levered IRR target is withdrawn.** It was set as a hurdle and it was not reasonable: it
is not a property this asset class reliably has, it is not a property the engine should be steered
toward, and steering toward it inverts the purpose of underwriting — the model exists to find out
what a site earns, not to reach a number decided in advance.

Validation now produces a **confidence band**. The band answers *"how much weight can be put on this
result?"* — never *"did it clear a hurdle?"*

### The bands

| Band | Meaning |
|---|---|
| **High** | Metered HH data; reconciled load; tariff evidenced from actual invoices; the revenue streams in the priced floor are contracted or firmly evidenced; assumptions predominantly sourced; the result is not sensitive to any single unsourced input. |
| **Medium** | Modelled HH data scaled to reconciled totals, **or** metered data with a material evidence gap (e.g. no tariff decomposition); floor revenue defensible but partly uncontracted; some material assumptions unsourced but bounded. |
| **Low** | Load basis unreconciled or materially disputed; a load-bearing assumption is a placeholder with no source; or the result flips sign across the plausible range of a single input. |

A band is **assigned from evidence, not chosen**. It is capped by the weakest input the result
actually depends on — a High band is impossible on modelled load data, regardless of how good the
rest of the case is.

### What a band is not

- It is **not** a probability, and must never be rendered as one.
- It is **not** P50/P90. Those quantify revenue distribution; the band quantifies *evidential
  strength*. A site can be High confidence in a poor result.
- It is **not** a recommendation. A High-confidence negative result is a successful validation.

---

## 3. Result handling

### A negative IRR is a signal to revise, not a result to publish

If a site underwrites to a negative return, the presumption is that **an assumption is wrong or a
business type has been poorly treated** — not that the site is worthless. Before a negative result
may be recorded, the following must be checked and the check recorded:

1. Is the load shape appropriate to the business type, or has a generic profile been applied to a
   plant whose actual operating pattern differs?
2. Is the tariff the site's real delivered cost stack, or only the commodity rate? (A commodity-only
   tariff systematically understates avoided cost — most BTM value sits in the non-commodity lines.)
3. Are the revenue streams the site can genuinely access all present, and are any excluded for a
   modelling reason rather than a real eligibility reason?
4. Is the capex basis appropriate to the site's scale and constraints, or a portfolio average
   applied to an outlier?
5. Is the asset being denied a revenue it physically earns — in particular, is discharge being
   capped by site load at a site whose thesis is export?

A negative result **survives** only once every one of these has been examined and the result still
holds. Then it is a genuine finding and is reported as one.

### An unresolved case is a failed validation

"Unresolved" means: the run did not reach a priced, resolved verdict — a solver failure, an
insufficient-inputs refusal, or a "no viable configuration" outcome.

**An unresolved case is not a validation.** It must be diagnosed and re-run. In particular,
"no viable configuration" must never be returned for a **financing-data** reason: the absence of a
credit assessment or debt structure means the site underwrites on an **unlevered** basis with the
confidence band adjusted, not that the site fails.

Reserve "no viable configuration" for cases where the *asset* genuinely does not work at any size.

### A defect is not FIXED until it is reachable, and a passing test does not prove that

**The rule.** No finding, defect or work package may be marked FIXED without recording **both**:

1. **The production call site** — `file:line`, reachable from `cli.py`, `app/app.py` or
   `generate_quote` without passing through test-only code; and
2. **The test that fails if that call site is deleted.**

**Clause 2 is the load-bearing one.** It exists because this repo has produced the same failure
**seven times**: code written, tested, merged, and never called on the production path. Every one of
the seven **had a passing test**. What none of them had was a test that would notice the call site
disappearing — the tests called the new function directly, so deleting the wiring left them green.

The seven, kept here because the pattern is the point, not the instances:

| # | Subsystem | How it was inert |
|---|-----------|------------------|
| 1 | Gate 3 reconciliation | computed, never attached to the record |
| 2 | F-19 call rate | reachable, but no test covered the branch |
| 3 | `contract_tenor_years` | plumbed to a field nothing read |
| 4 | Availability compiler | **called**, but the value never flowed |
| 5 | Physics disclosures | asserted on a type production never returns |
| 6 | Per-DNO DUoS windows | model field populated, consumer took a default |
| 7 | Gate 3b bill reconciliation | zero references in `src/` outside its own file |

**Instance 4 is the warning against over-trusting clause 1.** It *had* a call path and was still
inert, because a call path proves reachability, never that a value flows. Where a fix's whole
purpose is that a number changes, the test must assert **the number**, not the call.

**Mechanical support, not a substitute for it.** A static reachability check over the review diff
(unreferenced new public symbols; model fields written but never read; `assert` targets that no
production path constructs) catches roughly four of these six statically, at about a 25% false-positive
rate — useful as a prompt to check, never as a verdict. It cannot see instances 2 and 5 at all.

---

## 4. Evidence required per revenue stream

No stream may enter the **priced floor** without the evidence below. Streams that fail their
evidence test are reported as upside, outside the floor, and are excluded from anything debt is
sized against.

| Requirement | Applies to |
|---|---|
| The site can physically access it (connection, export rights, metering, communications) | every stream |
| The site is contractually eligible (registration, aggregation, licence, code obligations) | every stream |
| A route to market exists and its commercial share is modelled | every traded stream |
| Acceptance/delivery risk is modelled, not assumed at 100% | every dispatched stream |
| It does not double-count another stream's MW or MWh in the same period | every stream |
| Its price basis is sourced with a vintage | every stream |

The **mutual-exclusivity constraints** between streams — which services can and cannot be earned
from the same MW and MWh in the same settlement period — are specified in the requirements
checklist referenced in §6 and must be enforced in the optimiser, not merely noted.

---

## 5. Deliverables follow validation automatically

When a run **resolves successfully**, production of the client-facing materials is automatic: a
goal is deployed to generate the report pack from the run record. Deliverables are never
hand-assembled and never produced for a site that has not cleared §1.

Every figure in every deliverable comes from the run record. No number is authored.

---

## 6. The modelling requirements checklist

The substantive modelling requirements — MILP formulation, degradation, GB market mechanics per
stream, stacking conflicts, network charges, skip rates and delivery risk, and the financial
conventions — are held in the sourced requirements checklist derived independently from the
literature:

- **`docs/bess_modelling_requirements.md`** — the checklist, each item with its source and the
  error it prevents.
- **`docs/bess_constants_register.md`** — reusable constants with vintages and sources.

**The pipeline is audited against that checklist item by item.** An item that the pipeline does not
satisfy is either implemented, or recorded as a known limitation that caps the confidence band —
never silently absent.

---

## 7. The validation record

Every validated site carries a record stating: the HH basis per meter (metered/modelled) and, if
modelled, the reference profile and its source; the reconciliation result; the assigned confidence
band and the specific input that capped it; the streams in the floor and their evidence; the
assumption version; and the checklist items not satisfied. A validation without this record is not
a validation.

---

## 8. The assumption register, and disclosure of every derived value

**This is what makes D45 safe.** Removing the blocking gates is only defensible because every substituted
value is disclosed in two places, in full, in language a reader can audit.

### 8.1 In the client report — a "Modelling assumptions" section

Near the front, not buried in an appendix. One row per assumption, stating:

| Field | What it must say |
|---|---|
| What was missing | The absent input, in plain words |
| Value used | The figure the run actually consumed |
| Basis | The reasoning that justifies it, and its **method** source where the method is from the literature |
| Anchors | What the value or shape was scaled or calibrated to, and therefore which components are **measured** rather than modelled |
| Corroboration | Any independent check, and what it does and does not establish |
| Confidence effect | The ceiling it imposes, and whether it is the binding cap on the site's band |
| Confirmation ask | Exactly what the customer must supply to retire the assumption |

For a synthesised half-hourly basis this means saying, explicitly: which archetype shape was used and why
it fits the business type; that the shape is an engineering assumption; that the site's own **monthly
invoice totals** were the scaling anchor, so annual energy **and** month-to-month seasonality are
*measured* and only the within-day shape is modelled; what corroboration exists for the shape's
peak-to-mean; and what would resolve it.

### 8.2 In the technical validation report — the logged assumption register

Every `DerivedAssumption` on the run appears as a logged entry with its `kind`
(`derived_input` vs `pending_permission`), key, value, unit, basis, sources, derivation method, deriving
model, assumption release, confidence ceiling and confirmation ask. The register is generated **from the
run record**, never authored, so it cannot drift from what the model actually consumed.

### 8.3 The standard

**An assumption is admissible if it is reasoned and declared. It is inadmissible if it is silent.** A
method rooted in the literature is better than one merely reasoned, and both are far better than a
blocked validation. What is never acceptable: an unrecorded value, a fabricated citation, a derived
figure presented as evidenced, or a `pending_permission` written as though consent were granted.
