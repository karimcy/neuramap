# Capacity conventions

**Status:** reference. Written 2026-08-15 (Olly) from a read-only audit of `src/`, `assumptions/`,
`examples/real/sites/` and `scripts/`. It states what is true in the code **today**. It proposes no
refactor. Where the code and a docstring disagree, the disagreement is recorded as the finding.

**Why this page exists.** A pack asserted "The connection permits 500.0 kW of import on the recorded
agreed capacity" for a site whose `asc_kw` is null and whose `mic_kw` is a labelled
`DerivedAssumption`, on a record that says in its own words that no kVA figure exists for the site.
Nothing in that sentence was an arithmetic error. Every term in it was a **capacity**, and the code
had no single place saying which capacity it was.

House rule: this page uses no em dashes and no en dashes, including inside quotations. Quoted code
comments are paraphrased or shortened where the original used one.

---

## 1. How many conventions are actually in play

The recorded finding named three axes. All three are real. The audit adds a fourth that the framing
did not name, and sharpens the second.

| Axis | Verdict | The sharpening |
|---|---|---|
| (a) kW vs kVA, related by power factor | **Real, but a single declared convention** | v1 deliberately collapses kVA into kW (`docs/03_data_contracts.md:20`). The B-17 power-factor key exists in code but is **absent from all eleven assumption releases**, so unity PF is universal today. The exposure is latent, not live, and it is **asymmetric**: the key would de-rate one reader and not the other five. |
| (b) MIC vs ASC vs MEC | **Real, and the largest divergence** | Three named quantities, but **five different idioms** resolve "the import capacity". Two of the five are deliberate and documented. Three are not reconciled with each other. |
| (c) battery power vs connection capacity vs measured peak | **Real** | One live convention (`_sizing_basis`, connection-first) plus one **superseded** convention still present in code (`SizingDomain.from_site`, peak-first) whose docstring still claims the two agree. The superseded one is unreachable from production today. |
| (d) **provenance class of the same field** | **Real, and it is what produced the Tanhouse sentence** | `connection.mic_kw` carries **six** distinct provenance classes across the nine live site files, the portfolio register and the intake path, with no field that says which. This was not a units error and not a MIC/ASC error. |

So: **three axes as framed, plus a fourth that is the one the Tanhouse defect actually rode in on.**
Within axis (b) the honest count is five resolution idioms, not "two conventions confused".

---

## 2. The quantities

### 2.1 MIC, `connection.mic_kw`

- **Definition.** Maximum Import Capacity: the agreed maximum import at the connection. Battery
  charging counts against it (`docs/glossary.md:15`).
- **Unit.** Declared as **kW** by the v1 contract (`docs/03_data_contracts.md:20`), which states that
  MIC is conventionally apparent power and that v1 deliberately records it as kW. The schema module
  note (`src/gridq/schemas/connection.py:3-5`) still describes the point as open and defers to that
  same doc; the doc has since ratified it. **The schema note is stale prose, not an open question.**
- **Authoritative home.** `src/gridq/schemas/connection.py:19`. `float`, `ge=0`, **required and
  non-nullable**. There is no "unknown" representation, which is the root of everything in §4.
- **Read by.** The dispatch charge bound, the sizing basis, the sizing search box, the capacity
  charge on the customer bill, four gate predicates, two renderers, the ingestion asks. Full list in
  §6.
- **May be substituted with.** Nothing, silently. A derived value is permitted under D45 **only**
  with a `DerivedAssumption(kind="derived_input", key="connection.mic_kw")` written onto the site
  (`examples/real/sites/tanhouse.json` is the reference implementation of this).
- **May not be substituted with.** ASC (a different contract), MEC (the other direction), the site's
  measured peak (a fact about load, not about the wire), or a summed multi-meter kVA figure without
  the sum being stated.
- **Violation signature in a rendered pack.** The phrase "on the recorded agreed capacity" over a
  figure with no evidence; a MIC chip drawn `strong` beside an "ASC not recorded" chip; a MIC rule
  drawn on the dispatch panel for a site whose MIC is its own peak.

### 2.2 ASC, `connection.asc_kw`

- **Definition.** Available or Authorised Supply Capacity: the capacity reserved **and billed** at
  the connection (`docs/glossary.md:17`). It is the quantity the C&I availability charge is levied
  on (`src/gridq/schemas/tariff.py:28-32`).
- **Unit.** kW by the same v1 collapse. `src/gridq/schemas/tariff.py:30` records the convention
  explicitly as "kVA approximately equals kW at unity PF, disclosed where used".
- **Authoritative home.** `src/gridq/schemas/connection.py:21`. `float | None`, `ge=0`, **nullable**,
  default `None`. Null is the correct representation of "not stated".
- **Read by.** The capacity charge basis (ASC preferred, MIC as fallback), the dispatch limit (as one
  of two candidates for a `min`), two gate predicates, `capacity_is_supplied`.
- **May be substituted with.** Nothing. It is a **different contract from MIC**, not a synonym.
  Where a site genuinely has no separate ASC, `None` is the correct value and the four real site
  files that carry `asc_kw == mic_kw` do so because the workbook states one figure for both
  (`MIC/ASC 250 kVA`), which is evidence, not a fallback.
- **Violation signature.** An ASC chip drawn `strong` with a value identical to MIC on a site whose
  record nowhere states an ASC. This is what `src/gridq/app/app.py:554-555` produces on every
  wizard run.

### 2.3 MEC, `connection.mec_kw`

- **Definition.** Maximum Export Capacity: the agreed maximum export at the connection
  (`docs/glossary.md:16`).
- **Unit.** kW, same collapse.
- **Authoritative home.** `src/gridq/schemas/connection.py:20`. `float | None`, `ge=0`, nullable.
- **Read by.** Export capability and consent gates (`value_stack/gates.py:131,188`), the export
  overlay cap (`optimisation/engine.py:38,1531,1571`), the export-path validators
  (`ingestion/validators.py:180-233`), the MEC chip.
- **May be substituted with.** Nothing. `None` is **not** "unlimited" and must never read as one
  (`value_stack/gates.py:123-130` says this explicitly). Every live site records `mec_kw = None`,
  which is why the export overlay resolved its cap to 0 kW and was an unannounced no-op
  (`optimisation/engine.py:189-190`).
- **Violation signature.** An export revenue line on a site with no MEC and no `ExportFeasibilityOverride`.

### 2.4 The observed peak, `load_data.max_import_kw`

- **Definition.** The site's measured or modelled maximum half-hourly import. A fact about the
  **load**, not about the wire.
- **Unit.** kW, genuinely (a metered kWh series divided by the interval is real power).
- **Authoritative home.** `src/gridq/schemas/site.py:36`. `float | None`, `ge=0`.
- **Read by.** The sizing basis fallback, the sizing lower edge, the insufficient-data gates in all
  four solvers, the load exhibit, `capacity_is_supplied`'s third heuristic.
- **Relationship to MIC.** The peak is a **floor** on MIC, never a value for it
  (`ingestion/missing_data.py:139-146`, which states that MIC is a DNO fact and is never
  auto-populated). A MIC below the measured peak is refuted by the meter itself
  (`optimisation/engine.py:1758-1760`).
- **Violation signature.** `mic_kw == max_import_kw` exactly, with `import_headroom_kw == 0.0`. Two
  live sites are in this state by convention (Harwell, Colnbrook) and say so in their own connection
  notes.

### 2.5 The battery's own power rating

- **Definition.** The sized system's AC power (kW), a **sizing output**, never a site input.
- **Read by.** The dispatch bounds (`charge, discharge <= power`), the behind-the-meter discharge cap
  `discharge <= max(net_load, 0)`.
- **Relationship to the other two.** All three bind simultaneously and none substitutes for another:
  charge is bounded by `min(power, conn_limit - load - aux)`; discharge is bounded by
  `min(power, load)`. `value_stack/gates.py:70-72` is explicit that the connection bound is
  deliberately **not** the sized battery's power.

### 2.6 `import_headroom_kw` / `export_headroom_kw`

- **Definition.** Signed margins, MIC minus peak import and MEC minus peak export. Negative means
  over-subscribed, a documented exception to the no-negative-kW rule
  (`src/gridq/schemas/connection.py:24-31`).
- **Critical property.** `import_headroom_kw` is **read nowhere on the pricing path**. Its only
  readers in `src/` are an ingestion range check (`ingestion/validators.py:248`), one renderer
  heuristic (`outputs/report_charts.py:299`) and the disclosure that exists to flag stale notes about
  it (`quotes/disclosures.py:139`). Setting it to 0.0 constrains nothing that gets priced.
- **Violation signature.** A verdict sentence claiming "capacity-blocked" that rests on
  `import_headroom_kw == 0` while the record shows candidates were dispatch-solved and priced.
  `quotes/disclosures.py:130-180` detects exactly this and discloses both accounts rather than
  choosing.

### 2.7 Power factor, `mic_asc_headroom.assumed_power_factor`

- **Definition.** The factor de-rating a kW bound against a kVA connection limit (B-17).
- **Home.** A **release key**, not a constant: `src/gridq/optimisation/milp_solver.py:178`, read at
  `:447`.
- **Present in:** **no release.** Verified by parsing all eleven releases, `assumptions/gb_v0_1.yaml`
  through `gb_v1_9.yaml`: the `mic_asc_headroom` section exists in every one and its only key in
  every one is `value_gbp_per_mw_yr`.
- **Consequence today.** `_charge_power_factor` returns `1.0` plus a long labelled warning on every
  solve (`milp_solver.py:459-472`). kVA equals kW everywhere. This is stated, not silent, and it is
  the correct D45 treatment of a value that cannot be derived without a metered kVArh series.
- **Consequence if a release ever ships it.** See defect **D5**. Only one of six readers moves.

---

## 3. Axis (b): the five idioms that resolve "the import capacity"

| Idiom | Where | Intent | Verdict |
|---|---|---|---|
| `min(MIC, ASC)` over positive values, else `None` | `optimisation/two_stage.py:118-133` (the canonical definition), `optimisation/milp_solver.py:1101-1104`, `optimisation/mpc_solver.py:157`, `sizing/provisioning.py:141-146` | The **binding physical limit**. The narrower of two contracts is what the wire actually passes. | **CORRECT.** This is the house convention and four call sites agree on it. |
| `max(MIC, ASC)` | `value_stack/gates.py:147-155` | A deliberate **over-stating upper bound**, so a size gate can safely *disprove* eligibility without ever wrongly blocking. Documented in the docstring. | **CORRECT with reason.** Not a competing convention: a different question. |
| `max(MIC, MEC)` | `value_stack/gates.py:74-78` | The most generous MW the connection could present in **either direction**. Documented. | **CORRECT with reason.** Mixes import and export deliberately and says so. |
| `ASC or MIC or 0.0` | `optimisation/milp_solver.py:320`, `:728`, `optimisation/engine.py:2645` | The **billing basis**. The availability charge is levied on the ASC (`schemas/tariff.py:28-32`), so ASC-preferred is right for the *supplier* line. | **RISKY.** Right for the supplier availability line; **not** what `docs/bess_modelling_requirements.md` D-161 requires for the *DNO* capacity charge, which it puts on the contractual MIC. The repo carries one field for both. See §5 for why the `or` is behaviourally safe, **D1** for the input that poisons it, and **D8** for the two-charges-one-field question. |
| `MIC or ASC` | `ingestion/hh_synthesis.py:487` | A cap on a **synthesised** load series. | **RISKY.** Reverse preference from the billing idiom, undocumented, and it caps a synthesised peak at the connection, which manufactures the `mic == peak` state that `capacity_is_supplied` reads as "placeholder". |
| `ASC := MIC` at intake | `app/app.py:554-555` | None stated beyond "fill fields the customer leaves blank". | **WRONG.** See **D1**. |

The first three are reconciled. The last three are not reconciled with each other or with the first
three.

---

## 4. Axis (d): the six provenance classes of one required field

`connection.mic_kw` is non-nullable, so a site with no known capacity still carries a number. Across
the nine files in `examples/real/sites/`, the portfolio register and the intake path, that number is
one of **six** different kinds of thing, and **only one of the six is machine-detectable**.

| Class | Example | Machine-detectable? |
|---|---|---|
| Evidenced agreed capacity, transcribed from a workbook kVA figure | `bishops_cleeve_ash_plant.json` (93), `bishops_cleeve_mrf.json` (250), `knowl_hill.json` (325), `ewelme.json` (200) | No. Free-text note only. |
| **Derived under D45 with a labelled record** | `tanhouse.json` (500, smallest standard GB rating above the measured 486.35 kW peak) | **Yes.** `capacity_is_derived` reads it. |
| Placeholder equal to the site's own **measured** peak | `harwell.json` (213.81, `import_headroom_kw = 0.0`) | Partially. Two heuristics catch it. |
| Placeholder equal to a **modelled** peak of an assumed shape | `colnbrook.json` (915.077, the weakest connection figure in the portfolio by its own note) | Partially. Same two heuristics. |
| Summed multi-meter capacity | `portfolio_requirements/register.py:82` (480 = 250 kVA MRF + 230 kVA Import), `:160` (950 = 750 + 200) | No. Inline code comment only. |
| **Fabricated at intake** (`ASC := MIC`) | every wizard run through `app/app.py:914` | No. |

Only class 2 leaves a record. This is why `capacity_is_supplied`'s three heuristics all passed
Tanhouse: a derivation that lands on a *standard rating above* the peak leaves none of the side
effects the heuristics look for. The current code fixes that specific case by checking the
`DerivedAssumption` register **first** (`outputs/report_charts.py:294-295`), which is the right
shape. The residual gap is that it watches one key. See **D2**.

---

## 5. The `or` chain at `milp_solver.py:728`: safe today, and the comment above it is right

The recorded finding asks whether `capacity_kva = site.connection.asc_kw or site.connection.mic_kw or 0.0`
is a null-as-zero defect. **It is not, today.** The reasoning, because "an agent deliberately copied it
verbatim rather than fixing it" deserves a verdict rather than a shrug:

1. The chain cannot cause a silent £0 **on a priced line**. The capacity charge is guarded
   independently: `capacity = 0.0 if tariff.capacity_charge_gbp_per_kva_day is None else (rate * capacity_kva * 365.25)`.
   With no rate the term is £0 whatever `capacity_kva` says, and that absence is loudly warned by
   `fixed_line_absence_warnings` (`milp_solver.py:319-331`).
2. For the chain itself to reach `0.0` with a **declared** rate, the site needs `asc_kw` falsy **and**
   `mic_kw == 0.0`. That state is the "no import capacity declared" state, and it is already the
   loudest failure in the engine: `conn_limit` resolves to 0.0, the charge bound is 0 in every
   half-hour, and `engine.py:1963-1972` reports it as a DATA verdict in full sentences. A £0 capacity
   charge is not the reader's problem in that state.
3. A legitimately recorded `asc_kw == 0.0` falling through to MIC is arguably **correct**, not a bug.
   Zero agreed supply capacity for an importing site is a sentinel, not a fact. No live site records it.
4. `mic_kw` is non-nullable, so the trailing `or 0.0` is unreachable except through the `mic_kw == 0`
   sentinel, which `ingestion/validators.py:241-246` warns on and `ingestion/missing_data.py:128`
   raises as an ask.

The comment at `milp_solver.py:723-727` is therefore **accurate and the right call**: the chain
selects between two capacity *fields*, not between a price and a null, and rewriting it to `is None`
would change behaviour for a recorded `0.0` ASC. Leaving it and flagging it was correct.

**The real exposure at 728 is not the `or`. It is what feeds `asc_kw`.** See **D1**: on the wizard
path the ASC this line prefers is a copy of MIC that no one supplied. The chain then reads a
fabricated number in preference to a real one, and it does so silently because from its point of view
an ASC is present.

The schema-semantics question the comment defers to the owner of `connection` is real, and it is
stated precisely in **D4**.

---

## 6. Every place the repo reads or derives a capacity

Verdicts: **CORRECT** (right today for a reason that will hold), **RISKY** (right today for a reason
that could change, or right in isolation but not reconciled), **WRONG** (produces a false statement
or a wrong number now).

### Dispatch and sizing

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `optimisation/two_stage.py:118-133` | `connection_power_limit_kw` = `min(MIC, ASC)` over positive values | CORRECT. The canonical definition; docstring names the three call sites that must agree, and they do. |
| `optimisation/milp_solver.py:1101-1104` | `conn_limit` = same, `0.0` when none | CORRECT. The `0.0` consequence is reported in full at `engine.py:1963`. |
| `optimisation/milp_solver.py:1109-1111` | `charge_conn_limit = conn_limit * power_factor` | RISKY. The only PF-aware reader in the repo. See **D5**. |
| `optimisation/mpc_solver.py:157` | same `min` over positive | CORRECT. |
| `sizing/provisioning.py:141-146` | `conn_limit` = same `min`, used to resolve an absent `import_cap_kw` | CORRECT. |
| `sizing/provisioning.py:390` | `cap = requirement.import_cap_kw or site_max_import_kw` | RISKY. A **different** fallback for the same absent field, in the same module, defaulting to the load peak rather than the connection. Reachable only from tests today (`assess_provisioning` has no `src/` caller). See **D7**. |
| `optimisation/engine.py:1742-1815` | `_sizing_basis`: connection limit first, observed peak as fallback, larger of the two when the limit is below the peak | CORRECT. The live convention, and the docstring records the measured reason it replaced the peak-first version. |
| `optimisation/two_stage.py:161-171` | `load_relevant_capacity_kw` = min over {binding capacity, `min(MIC,ASC)`, declared peak, series peak} | CORRECT. Min-only, so the search floor can only fall, never rise. |
| `sizing/domain.py:110-112` | `base = peak if peak > 0 else mic` | RISKY. This is the **superseded** peak-first basis, and the docstring at `:96-97` still claims it mirrors the engine's basis "exactly", which `engine.py:1746-1751` says it does not. Unreachable from `src/` (`SizingDomain.from_site` has test-only callers), so latent. See **D6**. |
| `optimisation/engine.py:1959-1985` | `mic ~= peak` placeholder detector, tolerance 1% (`_PLACEHOLDER_MIC_TOLERANCE`) | CORRECT. The version CLAUDE.md flags has been repaired: the note now carries measured headroom, whether the limit actually bound, and the best priced floor, and says only what those support. |
| `optimisation/engine.py:1400-1418` | required import capacity from the solved schedule, reported as the data ask when nothing is declared | CORRECT. |
| `optimisation/engine.py:226,244-247,1531,1571,1643` | `ExportFeasibilityOverride.mec_kw`, study-basis export cap `min(power, mec)` | CORRECT. Refuses a non-finite or non-positive value rather than defaulting. |
| `optimisation/milp_solver.py:1308-1312` | reports `connection.mec_kw` as "NOT DECLARED" when unrecorded | CORRECT. |

### Billing and the customer bill

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `optimisation/milp_solver.py:728` | `capacity_kva = ASC or MIC or 0.0`, multiplied by the £/kVA/day rate | RISKY. Behaviourally safe (see §5); the exposure is its input (**D1**) and the unit convention (**D5**). |
| `optimisation/milp_solver.py:320` | same chain, inside the absence warning text | RISKY, same reason. |
| `optimisation/engine.py:2645-2649` | same chain, no-battery bill | RISKY, same reason. The comment at `:2636` correctly explains why it was copied unchanged. |
| `optimisation/schedule.py:276-279` | basis string: capacity charge billed on agreed supply capacity, "kVA taken as kW at unity power factor" | CORRECT. Discloses the collapse at the point of use. |
| `schemas/customer_bill.py:86-89` | same disclosure on the field | CORRECT. |
| `schemas/customer_bill.py:100-101`, `optimisation/schedule.py:287-288` | `peak_import_before_kw` / `peak_import_after_kw` measured off the series | CORRECT. Explicitly labelled informational; `customer_bill.py:95-99` states that no ASC or capacity £ is ever priced from them, and that the after-peak may legitimately exceed the before-peak. |

### Gates and eligibility

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `value_stack/gates.py:52-57` | `has_connection_limits`: MIC > 0 **or** ASC > 0 | CORRECT. |
| `value_stack/gates.py:74-78` | `connection_bounded_response_mw` = `max(MIC, MEC) / 1000` | CORRECT with reason (deliberate over-statement so a gate may only disprove). |
| `value_stack/gates.py:131-134` | `evidenced_export_capacity_mw` = MEC, else a declared record | CORRECT. |
| `value_stack/gates.py:146-155` | `evidenced_import_capacity_mw` = `max(MIC, ASC)`, else a declared record | CORRECT with reason. |
| `value_stack/gates.py:110-119` | `_declared_capacity_mw` accepts a record whose `unit` is `kw` **or** `kva` interchangeably, refuses anything else | RISKY. Correct under the declared unity-PF collapse and it says so; it becomes a silent 5 to 11% error the moment a real PF exists. |
| `value_stack/gates.py:142-144` | docstring: `mic_kw = 0` is "the schema's way of saying not declared" | **WRONG as a repo-wide statement.** Contradicted 400 lines below in the same file. See **D4**. |
| `value_stack/gates.py:535-542` | `input_presence`: a declared `0.0` is `INPUT_PRESENT`, because a recorded zero is evidence | CORRECT in isolation, and it is the D45-consistent reading. The contradiction is with `:142-144`. |
| `value_stack/gb_streams.py:59,100,910-936,1045-1046` | declares `connection.mic_kw` with `connection.asc_kw` as a disjunctive alternative, `connection.mec_kw` for the generating route | CORRECT. |

### Ingestion

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `ingestion/validators.py:241-246` | warns when `mic_kw == 0` | CORRECT. WARNING not ERROR, so it never blocks. |
| `ingestion/validators.py:248-254` | INFO on negative import headroom | CORRECT. |
| `ingestion/validators.py:180-233` | export path evidenced by `mec_kw > 0` and an export MPAN | CORRECT. |
| `ingestion/missing_data.py:126-146` | asks for MIC/MEC/ASC; suggests the observed peak as a **floor** for MIC and states that MIC is never auto-filled | CORRECT, and it is the doctrine `app.py:554` violates. |
| `ingestion/hh_synthesis.py:487` | `peak_kw_cap = MIC or ASC` on a synthesised series | RISKY. Reverse preference from the billing idiom, and capping a synthesised peak at the connection manufactures the `mic == peak` signature that two downstream heuristics read as "placeholder capacity". |
| `ingestion/reconciliation.py:293-295` | reconciles declared vs measured `max_import_kw` | CORRECT in itself. Note CLAUDE.md §3: `reconcile_declared_against_measured` has zero `src/` callers, so this comparison is unenforced on production runs. Out of scope here. |

### Intake and the app

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `app/app.py:163-167` | MIC required at intake, `SiteModelLoadError` if absent | CORRECT. |
| `app/app.py:186-194` | builds `ConnectionDetails` from the form | CORRECT. |
| `app/app.py:554-555` | `if data.get("mic_kw") and not data.get("asc_kw"): data["asc_kw"] = data["mic_kw"]` | **WRONG.** See **D1**. |
| `app/app.py:912-913` | `if data.get("grid_connection_kva") and not data.get("mic_kw"): data["mic_kw"] = data["grid_connection_kva"]` | **WRONG.** See **D3**. |
| `app/static/wizard.js:306` | demo prefill sets MEC = MIC = 500 | CORRECT. A demo prefill on a form the user then edits, not a pipeline default. |
| `app/templates/real_form.html:66-71`, `new_quote.html:201-204` | separate MIC / MEC / ASC inputs, MIC marked required | CORRECT. Note the two templates label ASC differently ("available" vs "authorised"); cosmetic, both are in the glossary. |
| `app/demo.py:256-265` | reads the engine's own `_sizing_basis` rather than re-deriving | CORRECT. |

### Outputs

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `outputs/report_charts.py:263-275` | `capacity_is_derived`: any `DerivedAssumption` keyed `connection.mic_kw` | RISKY. Right shape, one key. A derived or fabricated **ASC** is invisible to it. See **D2**. |
| `outputs/report_charts.py:294-302` | `capacity_is_supplied`: derived-check first, then `asc_kw is not None` returns True, then `import_headroom_kw == 0.0`, then `mic == peak` | **WRONG.** The second test treats the presence of an ASC as proof a capacity was supplied, and `app.py:554` guarantees an ASC is present. See **D2**. |
| `outputs/report_charts.py:481-505` | MIC / ASC / MEC chips, `strong` styling driven by `capacity_is_supplied` and by `asc is not None` | Inherits **D2**. |
| `outputs/report_charts.py:683-690`, `753-756` | capacity note on the load exhibit; MIC rule drawn on the dispatch panel only when `supplied` | Inherits **D2**. CORRECT in design: not drawing a ceiling from a placeholder is exactly right. |
| `outputs/consulting_report.py:934-955` | `_capacity_finding`, three states: derived, supplied, never supplied | CORRECT as written, and this is the **repaired** version of the sentence in the recorded finding. Inherits **D2** through `capacity_is_supplied`. |
| `outputs/consulting_report.py:910-912` | MIC / ASC / MEC rows, with explicit "no agreed supply capacity on the site record" fallback | CORRECT. |
| `quotes/disclosures.py:130-180` | `capacity_conflict_disclosure`: `import_headroom_kw == 0` plus priced candidates implies a stale capacity-block note; quotes both accounts | CORRECT, and the best-designed capacity reader in the repo. It chooses nothing for the reader. |

### Fixtures and registers

| File:line | What it reads / derives | Verdict |
|---|---|---|
| `portfolio_requirements/register.py:66,82,160` | `mic_kw` from **summed** multi-meter kVA (480 = 250 + 230; 950 = 750 + 200), with the sum in an inline comment only | RISKY. A summed capacity across two meters is not a single connection's MIC, and the fact lives in a comment the pipeline cannot read. Compare `ewelme.json`, whose note records that using the Workshop's 750 kVA for the MRF's series would overstate headroom 3.75x. |
| `portfolio_requirements/register.py:262-264` | writes `mec_kw=None`, `asc_kw=None` | CORRECT. Honest nulls. |
| `engine/conformance.py:141` | fixture `mic_kw=300.0, mec_kw=150.0` | CORRECT. |
| `dispatch/simple_solver.py:53-57`, `engine/baseline.py:68-77`, `optimisation/mpc_solver.py:99-104`, `optimisation/milp_solver.py:623-628,809` | `max_import_kw` as an insufficient-data gate | CORRECT. All four refuse rather than invent. |

---

## 7. Ranked defects

Ranked by "would a wrong number or a false sentence reach a reader today". Each entry names the
production call site and what a test must assert. **D1 to D3 are the three to fix.**

### D1. Intake fabricates ASC from MIC, unlabelled, on the production run path

- **Where:** `src/gridq/app/app.py:554-555`, inside `_apply_reference_data`.
- **Reached from:** `src/gridq/app/app.py:914`, in the `POST /api/run` handler, on the non-demo branch.
  This is the wizard's production path.
- **What it does:** sets `asc_kw = mic_kw` whenever ASC is blank. No `DerivedAssumption` is written,
  no warning is emitted (the function's return value only discloses the *tariff* reference rates),
  and the docstring calls it "reference defaults".
- **Why it is wrong, in the repo's own terms:** ASC and MIC are different contracts (§2.2). D45
  permits deriving an absent input and forbids doing it silently. `ingestion/missing_data.py:139-141`
  states the doctrine directly for this exact field family: the capacity is never auto-filled.
- **Blast radius, all silent:**
  - `outputs/report_charts.py:297-298` returns `capacity_is_supplied = True` for **every** wizard
    site, because an ASC is always present. This is the Tanhouse sentence's remaining live route.
  - `outputs/consulting_report.py:947-951` then prints "The connection permits X of import on the
    recorded agreed capacity".
  - `outputs/consulting_report.py:911` prints an ASC row instead of "no agreed supply capacity on
    the site record".
  - `optimisation/milp_solver.py:728`, `:320` and `optimisation/engine.py:2645` prefer the
    fabricated ASC over the real MIC as the capacity-charge basis. Numerically identical while the
    copy holds, but a **priced £ line on the customer bill** is then levied on a number nobody
    supplied, and `docs/bess_modelling_requirements.md` D-161 puts that charge on the contractual
    MIC in the first place (see **D8**).
  - `value_stack/gates.py:52-57` and `:146-155` read it as recorded evidence.
- **Test (fails today; passes once the copy is removed or labelled):**
  `POST /api/run` (or call `_site_from_form` after `_apply_reference_data`) with `mic_kw` set and
  `asc_kw` blank; assert `site.connection.asc_kw is None`. Then assert
  `charts.capacity_is_supplied(site) is False` for a site whose MIC is a placeholder, and assert the
  rendered pack contains "no agreed supply capacity on the site record" and does **not** contain
  "on the recorded agreed capacity".
- **Note on the fix shape (not a refactor, a choice for the owner of `connection`):** either drop the
  copy entirely and let `asc_kw` stay `None`, or keep it and emit a
  `DerivedAssumption(kind="derived_input", key="connection.asc_kw", ...)` plus a disclosure. The
  second is D45-compliant; the first is simpler and matches `missing_data.py`'s stated doctrine.

### D2. `capacity_is_supplied` treats the presence of an ASC as proof, and `capacity_is_derived` watches one key

- **Where:** `src/gridq/outputs/report_charts.py:294-302` and `:263-275`.
- **Reached from:** `outputs/consulting_report.py:942,947` (`_capacity_finding`),
  `outputs/report_charts.py:481` (MIC chip), `:687` (load exhibit note), `:754` (dispatch panel MIC
  rule).
- **What is wrong:** `capacity_is_derived` matches only `d.key == "connection.mic_kw"`. A site whose
  **ASC** is the derived or fabricated figure returns `False`, then falls into
  `capacity_is_supplied`, whose very next test is `if conn.asc_kw is not None: return True`. So a
  derived ASC is reported as a recorded agreed capacity. With **D1** in place, that is every wizard
  site.
- **Test:** build a site with `mic_kw = peak`, `asc_kw = peak`, and a
  `DerivedAssumption(key="connection.asc_kw")`; assert `capacity_is_derived(site) is True` and
  `capacity_is_supplied(site) is False`. Second case: same site with **no** derived record at all
  (the D1 state); assert `capacity_is_supplied(site) is False`, which fails today.
- **Relationship to the recorded finding:** the specific Tanhouse miss is **already fixed**. The
  `capacity_is_derived` check was added and is consulted first, `_capacity_finding` has three states,
  and `tanhouse.json` now carries `mic_kw = 500.0` with a `derived_input` record and
  `import_headroom_kw = 13.65`. D2 is the residual gap in the same mechanism, not a re-report.

### D3. The kVA to kW boundary is crossed silently at intake

- **Where:** `src/gridq/app/app.py:912-913`, `data["mic_kw"] = data["grid_connection_kva"]`.
- **Reached from:** the same `POST /api/run` handler, immediately before D1.
- **What is wrong:** the form field is named kVA, the target field is kW, and the assignment is a
  bare copy. Under the v1 collapse (`docs/03_data_contracts.md:20`) the **number** is right. What is
  missing is any record that a unit boundary was crossed. The docstring at `app.py:889-890` mentions
  it ("the grid-connection field is in kVA"), so the fact exists in prose and nowhere the pipeline
  can read. Compare the four real site files, which each carry the crossing explicitly in the
  connection note ("treated as kVA approximately equals kW at unity power factor").
- **Direction of error if a PF ever lands:** a real C&I PF of 0.90 to 0.95 means true kVA exceeds the
  kW this model uses by roughly 5 to 11%, so charging at full kW near the MIC can trigger the
  exceeded-capacity charge the model reports as impossible (`milp_solver.py:463-468`).
- **Test:** post `grid_connection_kva=500` with no `mic_kw`; assert the resulting site carries either
  a `DerivedAssumption` on `connection.mic_kw` naming the unity-PF conversion, or a run warning
  containing the conversion statement. Today neither exists.

### D4. `mic_kw == 0.0` means two contradictory things, in one file

- **Where:** `src/gridq/value_stack/gates.py:142-144` says `mic_kw = 0` is the schema's way of saying
  "not declared", and treats it as absent. `src/gridq/value_stack/gates.py:535-542` says a declared
  `0.0` is `INPUT_PRESENT` because a recorded zero is evidence, and names the `or 0.0` idiom as the
  defect that conflated the two.
- **Who else is on the sentinel side:** `ingestion/validators.py:241` ("the connection cannot
  import; confirm MIC"), `ingestion/missing_data.py:128` ("MIC (import capacity reads 0)"),
  `optimisation/milp_solver.py:1102` and `two_stage.py:131` (both filter on `v > 0`).
- **Who is on the evidence side:** the D45 guard at `gates.py:568-677`, and the whole
  `fixed_line_absence_warnings` design at `milp_solver.py:282-332`.
- **Why it matters:** this is exactly the schema-semantics question `milp_solver.py:723-727` defers
  to the owner of `connection`, and it is unanswered. The resolution is a **schema decision**, not a
  code edit: either `mic_kw` gains a nullable representation of "unknown" (so `0.0` can mean zero),
  or the sentinel reading is written into the field description and `gates.py:535-542` is made to
  exempt it. Today the field description says neither.
- **Test:** whichever way it is decided, one test asserting the chosen meaning for
  `ConnectionDetails(mic_kw=0.0)` across `has_connection_limits`, `input_presence`,
  `connection_power_limit_kw` and `_check_connection`, so the four cannot drift apart again.

### D5. If a power factor ever ships, exactly one of six readers moves

- **Where the PF is applied:** `src/gridq/optimisation/milp_solver.py:1109-1111` only.
- **Where it is not applied, and would need to be:**
  `optimisation/milp_solver.py:728` and `:320` (the £/kVA/day capacity charge, which is billed on a
  genuine kVA quantity and is the reader with the strongest claim to it),
  `optimisation/engine.py:2645-2649` (same on the no-battery bill),
  `ingestion/hh_synthesis.py:487` (the synthesised-peak cap),
  `value_stack/gates.py:110-119` (which accepts `kva` and `kw` units interchangeably),
  `optimisation/two_stage.py:118-133` (the reported connection limit, deliberately left as declared
  per `milp_solver.py:1105-1108`).
- **Status today:** latent, verified. `mic_asc_headroom.assumed_power_factor` is absent from all of
  `gb_v0_1` through `gb_v1_9`, so `_charge_power_factor` returns `1.0` on every solve and warns.
  Every reader agrees at PF = 1.0.
- **Why it is RISKY and not CORRECT:** the divergence is invisible until a release adds one key, at
  which point the dispatch bound and the bill silently stop agreeing about what the connection is.
- **Test:** a release fixture carrying `mic_asc_headroom.assumed_power_factor: 0.95`; assert either
  that every capacity-charge reader de-rates too, or that the run emits an explicit warning naming
  the readers that do not. Neither holds today.

### D6. `SizingDomain.from_site` still carries the superseded peak-first basis, with a docstring claiming otherwise

- **Where:** `src/gridq/sizing/domain.py:110-112`, `base = peak if (peak and peak > 0) else (mic if (mic and mic > 0) else None)`.
- **The contradiction:** `sizing/domain.py:96-97` says this mirrors `MilpEngine._default_search_space`'s
  basis "exactly". `optimisation/engine.py:1746-1751` says that basis **used to** be peak-first and
  was changed, and records the measurement: a 325 kW connection whose metered peak is 22.6 kW had its
  box capped at 33.9 kW, hiding 302 kW of evidenced headroom, and tripling another site's MIC
  produced an identical candidate set.
- **Reachability:** `SizingDomain.from_site` has **no caller in `src/`**. Every caller is in
  `tests/test_sizing_domain.py`. So this is a latent trap, not a live wrong number.
- **Test:** assert `SizingDomain.from_site(site).power_kw_max` scales from the connection limit, not
  the peak, for a site with `mic_kw = 325` and `max_import_kw = 22.6`. It fails today. Alternatively,
  if the divergence is intentional, the docstring at `:96-97` must stop claiming they agree.

### D7. `sizing/provisioning.py` carries two different fallbacks for the same absent field

- **Where:** `src/gridq/sizing/provisioning.py:141-146` resolves an absent `import_cap_kw` from
  `min(MIC, ASC)`. `src/gridq/sizing/provisioning.py:390` resolves the same absent field from
  `site_max_import_kw`, the **load peak**.
- **Reachability:** `search_floors` (the `min(MIC, ASC)` one) is live via
  `optimisation/two_stage.py:370`. `assess_provisioning` (the peak one) has no `src/` caller.
- **Why it matters:** a provisioning obligation is a commitment to draw power through the wire, so
  the wire's capacity is the right cap; `two_stage.py:123-125` says exactly this and adds that an
  obligation exceeding the load peak is normal. The peak fallback would silently under-cap.
- **Test:** call `assess_provisioning` with a requirement whose `import_cap_kw` is `None`, on a site
  whose `max_import_kw` is well below `min(MIC, ASC)`; assert the resolved cap is the connection
  limit. It fails today.

### D8. One field carries two different capacity charges, levied on two different capacities

A **modelling-scope question for the owner of `tariff`**, not a code bug. Recorded because it is the
reason §3's ASC-preferred idiom cannot simply be marked CORRECT.

- **Where:** `src/gridq/schemas/tariff.py:27-32` defines a single
  `capacity_charge_gbp_per_kva_day`, described as the C&I capacity line billed on the site's ASC and
  evidenced against one invoice (Robert Thomas Farms, 8.76 p/kVA/day on 250 kVA). It is consumed at
  `optimisation/milp_solver.py:728-733` and `optimisation/engine.py:2645-2649` against
  `ASC or MIC`.
- **What the register requires:** `docs/bess_modelling_requirements.md` D-161 (`:1780`) says to model
  DUoS capacity charges against the **contractual MIC/MEC**, not against measured peak demand, citing
  the LPN Use of System Charging Statement 2026 paras 2.37 to 2.40. Its implementation line is
  `capacity_charge[t] = MIC_contracted_kVA * rate`.
- **The gap:** GB C&I sites carry two capacity-denominated lines, the DNO DUoS capacity charge (on
  MIC) and the supplier availability charge (on ASC). They coincide whenever ASC equals MIC, which is
  the common case and is why nothing has broken. They diverge the moment a site records a separate
  ASC, and the code's ASC-preference then puts the DNO line on the wrong capacity. `docs/glossary.md`
  already distinguishes the two contracts; the tariff schema does not.
- **Where the repo is already conservative, and should stay that way:** the charge is treated as
  strictly battery-invariant, carried identically on both sides of the bill
  (`optimisation/schedule.py:271-279`, `schemas/customer_bill.py:86-90`). This automatically honours
  D-161's "do not credit capacity-charge savings in year 1" and its 12-month MIC-reduction lock,
  because no capacity saving is ever booked at all. `schemas/customer_bill.py:95-99` states the same
  discipline for the peak fields. **Nothing here asks for that to change.**
- **Test, once the owner decides:** a site with `mic_kw = 500`, `asc_kw = 250` and a declared rate;
  assert the DNO capacity line is computed on 500 and the supplier availability line on 250, or (if
  one field is kept) assert the run warns that the two bases were collapsed and names which capacity
  was used.

---

## 8. Things checked that are **not** defects

Recorded here so they are not re-investigated.

- **The `or` chain at `milp_solver.py:728`.** Behaviourally safe today, and the deliberate-copy
  comment above it is correct. Full reasoning in §5.
- **`max(MIC, ASC)` at `gates.py:147-155` and `max(MIC, MEC)` at `gates.py:74-78`.** Deliberate
  over-stating upper bounds so a size gate may only ever disprove eligibility. Documented in both
  docstrings. Not competing conventions.
- **ASC-preferred billing at `milp_solver.py:728` / `engine.py:2645`.** Correct for a charge levied
  on the agreed supply capacity (`schemas/tariff.py:28-32`).
- **`asc_kw == mic_kw` on four real site files.** Evidence, not a fallback: the workbook states one
  figure for both, and each file's connection note says so.
- **The `mic ~= peak` heuristic at `engine.py:1959-1985`.** The version CLAUDE.md flags has been
  repaired; the note now carries the measurements and says only what they support.
- **`import_headroom_kw` being read nowhere on the pricing path.** True, and already disclosed as a
  record conflict rather than silently dropped (`quotes/disclosures.py:130-180`).
- **Unity power factor.** Not a defect: it is the D45-correct treatment of a value that cannot be
  derived without a metered kVArh series, it is warned on every solve, and the warning states the
  direction and magnitude of the error.

---

## 9. Two live records that disagree about Knowl Hill's peak

Not a defect in this page's sense (nothing wrong is computed), but a reader following the code
comments will be misinformed.

- `examples/real/sites/knowl_hill.json` records `max_import_kw = 227.2` and its connection note says
  the 22.6 kW figure was an artefact of an archetype peak and is **withdrawn**.
- `optimisation/engine.py:1749` and `optimisation/two_stage.py:146-148` both still cite 22.6 kW as
  Knowl Hill's metered peak, as the motivating measurement for two design decisions.
- `examples/real/sites/knowl_hill_invoice_anchored.json` records a third figure, 95.99 kW, as a
  `derived_input`.

The design decisions those docstrings justify are sound on their own terms. The measurement quoted in
them is superseded. This page does not adjudicate which basis is right; it records that three
coexist.

---

## 10. Cross-references

- `docs/glossary.md:15-19` for MIC / MEC / ASC / G99 / G100 as market terms.
- `docs/03_data_contracts.md:20` for the ratified kW-not-kVA v1 convention (this is the authority;
  the note at `src/gridq/schemas/connection.py:3-5` is its stale echo).
- `docs/03_data_contracts.md:133-134` for the headroom fields.
- `docs/bess_modelling_requirements.md` **D-161** (`:1780`), which requires DUoS capacity charges to
  be modelled against the contractual MIC/MEC and **not** against measured peak demand: the sharpest
  external statement of the axis-(c) rule this page records. Also **D-162** (`:1787`, the
  exceeded-capacity apparent-power formula, and the check that battery charging cannot itself trigger
  it) and **D-163** (`:1794`, "do not assume unity power factor"), both of which unity PF defers.
- `CLAUDE.md` §2 for D45 and for the rule that a heuristic must never relabel an economic result as a
  data problem.
