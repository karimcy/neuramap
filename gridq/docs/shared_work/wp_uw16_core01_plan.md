# WP-UW-16 + WP-CORE-01 — implementation plan for the executor

Written 2026-11-22 against `roadmap/p1-foundations` at `3163dff`. Every `file:line` below is at that
commit; re-locate by the quoted text if a line has drifted. The plan was written read-only: no
source or test was edited. Every figure marked **measured** was produced by running the code at
`3163dff` in memory; everything else is read from the source.

Binding context: `CLAUDE.md` §2.0 (D57: no real-world constraint reduces a figure; the asset's own
capability is the one bound kept), §4 ("Nothing is FIXED until it is reachable"), D61, D62, D63, D64
in `docs/decisions.md:7-78`, and `docs/shared_work/core01_failure_triage.md`.

**Working tree note.** `git status` shows ` M CLAUDE.md` (five added lines, the 2026-11-22 "roadmaps
stay current" owner rule). It is not part of this work. Do not revert it. Commit it separately or
leave it for the owner, but WP-CORE-01's "git status clean on the tagged commit" cannot be met while
it is uncommitted (see Q5).

---

## 0. Rules for the executor

1. Work in the order of section 9. Run the check after each step. A step whose check fails is not
   done; do not start the next one.
2. Tests #7 and #8 (`tests/test_local_flex_owner_attested_award.py`, about lines 600-700) are **not
   edited at all**, including their comments. D61 says they pass as written.
3. The six D62 tests are restated exactly as section 5 says. No threshold is re-chosen, no assertion
   is deleted unless section 5 names it, and each restated test gets the mutation check in section 6.
4. STOP and report (do not improvise) if any STOP condition in this plan fires. The STOP conditions
   are collected in section 10.
5. No number is invented. In particular no utilisation performance ratio is invented (section 4).
6. The two collision hotspots are `src/gridq/optimisation/milp_solver.py` and
   `src/gridq/finance/cashflows.py`. This plan touches `milp_solver.py` in exactly one block
   (section 2.4) and does **not** touch `cashflows.py` at all. If you find yourself editing
   `cashflows.py`, stop: that is outside the plan.
7. Check `df -h /` before any multi-worker run (64 GiB free when this was written).
8. Commit or push only when the owner asks.

---

## 1. What is being closed

| Item | Source | State at `3163dff` |
|---|---|---|
| D61 per-candidate reservation; #7, #8 | `docs/decisions.md` D61 | RED: #7 gets £67.64 not £3.28; #8 gets the 213.81 kW figure not £246.83 |
| D-130 tenor reaches finance | `docs/reviews/2026-11-16-findings-register-triage.md:70` | LIVE: `quotes/pipeline.py:484` passes no `commitments=` |
| D-116 performance ratio or gross label | same file `:124`; `docs/reviews/2026-08-12-findings-register.md:45-58`; `docs/bess_modelling_requirements.md:1389` | LIVE: ratio never supplied; leg booked gross with no label in `quote.warnings` |
| D62 #12, #20, #21, #22, #24, #25 | `docs/decisions.md` D62 | RED (fast: #12, #20, #21, #22; slow: #24, #25) |
| D63 highspy lock | `docs/decisions.md` D63 | `requirements.txt:50` still `highspy==1.14.0` |

Already clean at `3163dff` (measured): `.venv/bin/ruff check .` passes, `ruff format --check src
tests .github/ci` passes (407 files), `.venv/bin/mypy` passes (171 files). The mypy launcher
(`.venv/bin/mypy`) already has a working `#!/bin/sh` + quoted `exec` header, so the "mypy shebang
repaired" deliverable needs verification only, not a change.

---

## 2. D61 — per-candidate reservation

### 2.1 The defect, precisely

Two consumers of one clamp read different volumes:

- **Reservation.** `local_flex_award_constraints` (`src/gridq/value_stack/gb_streams.py:3121`) calls
  `clamped_local_flex_awards(site_model, assumptions)` at `:3130` with no candidate, so every
  candidate reserves the search-floor volume (Harwell 10.372 kW, Ash Plant 2.247 kW).
- **Invoice.** `_local_flex_value` (`src/gridq/optimisation/valuation.py:605-611`) calls the same
  clamp with `candidate_power_kw=candidate_kw`, so the invoice is the candidate's own capability
  (Harwell at 250 kW: 213.81 kW).

A second defect sits inside the first: `candidate_kw` at `valuation.py:605-609` is
`schedule.deliverable_capacity_mw * 1000` when that is set, and `deliverable_capacity_mw` is the
**Capacity Market** reserved MW (`milp_solver.py:2132-2135`), not the battery's power. On a run with
a CM reservation the local-flex invoice is therefore clamped at the CM volume. The design below
removes that read entirely.

### 2.2 Design (decided; do not redesign)

**The solver records what it reserved, and the valuation bills the record.** Invoice = reserved by
construction, at every size, rather than by two call sites agreeing on a formula.

1. The candidate's power reaches the clamp on the constraint side:
   `clamped_local_flex_awards(..., candidate_power_kw=P)` gives
   `min(declared, P x (1 - standby_kw_per_mw/1000) x (1 - fade)^tenor)`. This is the bound D57
   keeps (the asset's own physics).
2. The solver writes the committed kW it compiled, per award, onto the schedule.
3. `_local_flex_value` bills the recorded kW. When the schedule carries no record (a hand-built
   schedule that was never solved, which is what #7 and #8 use) it bills the seam default,
   `clamped_local_flex_awards(site, assumptions)` with no candidate, which is exactly what
   `local_flex_award_constraints(site, LIB)` returns and what #7 and #8 compare against.

**Measured:** with the valuation forced to the candidate-less clamp in memory,
`tests/test_local_flex_owner_attested_award.py` goes 37/37 green, and the ten local-flex test modules
(209 tests) all pass. A design that simply clamps both sides at `schedule.power_kw` leaves #7 and #8
red (they assert the floor figure on a 250 kW hand-built schedule).

**Measured feasibility** (prototype, per-candidate reservation, 2 h duration, latest release, all
streams, both sites carry an award **and** a `provisioning_requirements` entry):

| Site | P kW | reserved before | reserved after | status after | EOL verdict | discharge kWh/yr before -> after |
|---|---|---|---|---|---|---|
| harwell | 11.52 | 10.372 | 11.180 | optimal | True | 6,631 -> 6,620 |
| harwell | 25 | 10.372 | 24.253 | optimal | True | 14,435 -> 14,361 |
| harwell | 100 | 10.372 | 97.013 | optimal | True | 57,118 -> 56,805 |
| harwell | 250 | 10.372 | 213.810 | optimal | True | 135,855 -> 134,970 |
| harwell | 500 / 1000 / 2000 | 10.372 | 213.810 | optimal | True | unchanged |
| bishops_cleeve_ash_plant | 2.5 | 2.247 | 2.422 | optimal | True | 1,214 -> 1,214 |
| bishops_cleeve_ash_plant | 25 | 2.247 | 24.253 | optimal | True | 10,066 -> 10,047 |
| bishops_cleeve_ash_plant | 100 and above | 2.247 | 46.320 | optimal | True | unchanged |

No candidate went INFEASIBLE on these two sites. The other seven were not prototyped; step 9.3 makes
the executor measure all nine before any pin is touched.

### 2.3 The seam: how candidate MW reaches the constraint builder

Edit in this order. Every new parameter is keyword-only with default `None`, and `None` is
byte-identical to today.

**(a) `src/gridq/value_stack/gb_streams.py`**

- `local_flex_award_constraints` at `:3121`: add `*, candidate_power_kw: float | None = None` and
  pass it through at `:3130`:
  `clamped_local_flex_awards(site_model, assumptions, candidate_power_kw=candidate_power_kw)`.
- `LocalFlexPicloStream.constraints` at `:3340`: add `*, candidate_power_kw: float | None = None`
  and pass it at `:3352`.
- The other two callers of the constraint builder, `:3208` (`_local_flex_conditional_reason`) and
  `:3313` (`local_flex_award_disclosure`), stay candidate-less. They run before a candidate exists.
- Clamp note at about `:3100-3112` ("MODELLED AT ... The binding limit is the smallest system on
  this site's sizing grid ..."): keep the text **byte-identical when `candidate_power_kw is None`**.
  When a candidate is passed, the binding-limit sentence must instead read
  `"The binding limit is THIS CANDIDATE's own END-OF-TENOR capability ({eol:,.3f} kW = {P:,.3f} kW
  rated, less parasitic draw, less {fade:g}%/yr fade over {tenor:g} yr)."` and the sentences from
  "The £ is UNDERSTATED" to "narrow the search on a revenue assumption." are dropped in that branch
  (they describe the floor). Keep "THIS IS THE ONE BOUND D57 KEEPS ..." and the DATA ASK in both.
- Comment block at about `:3050-3063` ("TWO CALLERS, TWO CORRECT BOUNDS ... the caller who cannot,
  does not"): rewrite to say that since D61 the constraint builder is called per candidate on the
  production path and the candidate-less form is the pre-candidate default only. Do the same for the
  docstring sentence in `clamped_local_flex_awards` that says the constraint builder runs pre-solve.
- `local_flex_award_disclosure` at about `:3302` says "THE AVAILABILITY LEG OF THIS AWARD IS STILL
  £0". That has been false since `6e19c08`, but `tests/test_local_flex_owner_attested_award.py:248`
  asserts the string. **Leave the string alone** and list it under Q4; changing it means editing a
  test that is outside D61/D62.

**(b) `src/gridq/value_stack/interface.py:276` and `src/gridq/value_stack/streams.py:174`**

Add the same keyword-only `candidate_power_kw: float | None = None` to the Protocol method and to
the base `constraints`. The base ignores it. These three are the only `def constraints` in `src/`,
and no test defines its own.

**(c) `src/gridq/dispatch/contract.py:182`** `dispatch_constraints_for`

Add `candidate_power_kw: float | None = None` (keyword-only). At `:200` call
`stream.constraints(site_model, assumptions)` when it is `None` and
`stream.constraints(site_model, assumptions, candidate_power_kw=candidate_power_kw)` otherwise.

Add one new function directly below it and export it from `gridq/dispatch/__init__.py` next to
`dispatch_constraints_for`:

```python
def candidate_dispatch_constraints(
    constraints: list[DispatchConstraintDescriptor],
    requested_streams: Iterable[MarketStream],
    site_model: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    candidate_power_kw: float,
    registry: StreamRegistry | None = None,
) -> list[DispatchConstraintDescriptor]:
    """`constraints` with every candidate-dependent descriptor re-resolved at this candidate (D61).

    Order and any caller-supplied extra descriptors are preserved: a descriptor is replaced only
    when the per-candidate resolution produces one with the same ``constraint_id``.
    """
    per = {
        d.constraint_id: d
        for d in dispatch_constraints_for(
            requested_streams, site_model, assumptions,
            registry=registry, candidate_power_kw=candidate_power_kw,
        )
    }
    return [per.get(d.constraint_id, d) for d in constraints]
```

**(d) `src/gridq/sizing/sizer.py:380` `_evaluate_spec`** (the per-spec primitive; all sizing and
underwriting goes through it)

- Add a **required** keyword-only parameter `registry: StreamRegistry`. Required on purpose: a call
  site that is not updated raises `TypeError` instead of silently reserving the floor.
- First statement of the body:
  `constraints = candidate_dispatch_constraints(constraints, streams, site, assumptions,
  candidate_power_kw=candidate.power_kw, registry=registry)`.
- The same rebound list then feeds both `DispatchProblem(... constraints=constraints ...)` at `:405`
  and `assess_end_of_life_deliverability(... constraints=constraints ...)` at `:424`, so the solver
  and the W7-3 end-of-tenor check test the same volume.
- Test doubles for `_evaluate_spec` (`tests/test_unlevered_selection_basis.py:259`,
  `tests/test_two_stage_power_domain.py:270`) take `**kwargs`, so they need no edit.

Pass `registry=reg` at every caller:

| Caller | Line | Registry name in scope |
|---|---|---|
| `enumerate_sizing` | `sizing/sizer.py:573` | `reg` (`:538`) |
| `underwrite_spec` | `sizing/underwrite.py:77` | `reg` (`:66`) |
| evaluator | `sizing/evaluator.py:145` | store `self._registry = reg` at `:122`, pass it |
| two-stage | `optimisation/two_stage.py:407`, `:593`, `:653`, `:709` | `reg` (`:352`) |

**(e) Re-solves of the chosen candidate.** These build their own `DispatchProblem` and must reserve
the same volume the sizing loop did, or the chosen spec's re-solve disagrees with its own
evaluation. At each, add `candidate_power_kw=<the power_kw passed to the DispatchProblem on the next
lines>` to the existing `dispatch_constraints_for(...)` call:

| Function | `dispatch_constraints_for` line | `DispatchProblem` line |
|---|---|---|
| `engine._study_netting_check_note` | `optimisation/engine.py:1185` | `:1188` |
| `engine._dispatch_energy_for` | `:1337` | `:1340` |
| `engine._provisioning_opportunity_cost` | `:1370` | `:1373` |
| `engine._export_upside` | `:1575` | `:1578` |
| `engine._no_spec_dispatch_evidence` | `:1770` | `:1777` |
| `demo._dispatch_energy_for` | `app/demo.py:427` | `:430` |
| `demo._export_upside` | `app/demo.py:467` | `:470` |

If any of these seven appends extra descriptors after the call, keep the append unchanged.

### 2.4 The solver records the reservation (`milp_solver.py`, HOTSPOT, one block)

- `src/gridq/optimisation/schedule.py`: add one field directly after `tender_window_charge_mwh`
  (about `:61`):
  `local_flex_reserved_kw: tuple[tuple[str, float], ...] | None = None`
  with a comment: "(commitment_id, committed kW) for every LOCAL_FLEX_PICLO availability window the
  solver compiled; `None` when none compiled or the schedule was not produced by a solve. NOT part
  of `ref()`." Do **not** add it to `ref()` (`:425`); `ref()` already omits the tender fields.
- `src/gridq/optimisation/milp_solver.py`, inside the existing `if flex_avail:` block at
  about `:2141-2167`: add
  `flex_reserved = tuple((str(c.commitment_id), float(c.committed_power_kw)) for c, _m in flex_avail)`
  with `flex_reserved = None` initialised beside the existing `tender_mwh` initialisation, and pass
  `local_flex_reserved_kw=flex_reserved` in the `HalfHourlyDispatchSchedule(...)` call at `:2169`
  next to `tender_window_discharge_mwh=tender_mwh` (`:2194`).
- No constraint, objective term or variable changes. `_compile_commitments` (`:2345`) is untouched.
- The other two schedule constructors need no change: `engine.py:2715` (no-battery schedule, nothing
  compiled) and `mpc_solver.py:284` (the MPC path refuses payload constraints at `:192`).
- `_StudyBasisDispatchSolver` (`engine.py:353`, `:432`) overrides `_solve_milp` and `_solved`. Read
  both overrides; if either rebuilds a `HalfHourlyDispatchSchedule` field by field rather than using
  `dataclasses.replace`, carry `local_flex_reserved_kw` across. (grep showed only three constructor
  call sites, so this is expected to be a no-op.)

### 2.5 The valuation bills the record (`valuation.py:596-611` and `:771-800`)

Replace `:605-611` (the `candidate_kw` computation and the clamp call) with:

```python
seam_awards, _clamp_notes = clamped_local_flex_awards(site, assumptions)
reserved = schedule.local_flex_reserved_kw
if reserved is None:
    clamped_awards = seam_awards
    reservation_basis = "seam default"
else:
    by_id = dict(reserved)
    clamped_awards = [
        ClampedLocalFlexAward(
            award=entry.award.model_copy(
                update={"committed_power_kw": by_id[entry.award.commitment_id]}
            ),
            declared_kw=entry.declared_kw,
        )
        for entry in seam_awards
        if entry.award.commitment_id in by_id
    ]
    reservation_basis = "solved"
    # an award the dispatch did not reserve is not billed, and says so
    for entry in seam_awards:
        if entry.award.commitment_id not in by_id:
            notes.append(
                f"LOCAL-FLEX AWARD '{entry.award.commitment_id}' WAS NOT RESERVED IN THIS "
                "DISPATCH, so its availability leg is not billed: the invoiced MW is the MW in "
                "the dispatch reservation, and here that is none."
            )
```

(Match the existing unpacking style at `:754`, `for award, declared_kw in clamped_awards`; if
`ClampedLocalFlexAward` is a NamedTuple keep constructing it positionally.) Rewrite the comment block
at `:596-604` to describe this. `deliverable_capacity_mw` is no longer read here.

Availability note at about `:787-793`. The current parenthesis says the paid MW "is the smallest
candidate's own end-of-tenor deliverable capability", which is false once the reservation is per
candidate. New parenthesis, keeping the two substrings #7 asserts (`"the award declares X MW"` and
`"THE INVOICED MW IS THE MW IN THE DISPATCH RESERVATION"`) byte-identical:

- `reservation_basis == "solved"`: `"(the award declares {declared_mw:,.3f} MW; paid on the
  {contracted_mw:,.3f} MW the dispatch actually reserves, read from the solved schedule's own
  reservation record and bounded by this system's end-of-tenor deliverable capability, because the
  asset cannot make available more power than it has. THE INVOICED MW IS THE MW IN THE DISPATCH
  RESERVATION: see the local-flex award constraint notes for the clamp's derivation)"`
- `"seam default"`: same, with `"read from the solved schedule's own reservation record and bounded
  by this system's"` replaced by `"which on a schedule carrying no solved reservation is the seam's
  default reservation, the smallest grid candidate's"`.

### 2.6 Tests to add for D61 (each fails if its call site is deleted)

Add to `tests/test_local_flex_owner_attested_award.py` (append; do not touch existing tests) unless
another file is named. Use the module's existing helpers (`make_site`/award fixtures, `LIB`,
`default_registry`).

| # | Test | Asserts | Goes red if |
|---|---|---|---|
| T1 | `test_seam_reserves_the_candidates_own_capability` | `dispatch_constraints_for(ids, site, LIB, registry=reg, candidate_power_kw=P)` gives the award descriptor `committed_power_kw == min(declared, P*(1-standby/1000)*(1-fade)**tenor)` computed in the test from library values, for one P below and one P above the declared kW; and with no `candidate_power_kw` it equals the floor figure | 2.3(a), (b) or (c) pass-through is removed |
| T2 | `test_candidate_dispatch_constraints_replaces_only_the_award` | order preserved; an extra descriptor with an unknown id is returned unchanged (`is`); the award id is replaced | the helper is broken |
| T3 | `test_evaluate_spec_reserves_per_candidate` | call `_evaluate_spec` with a recording fake `dispatch_solver` (captures `problem.constraints`, returns a canned INSUFFICIENT_DATA or minimal result as the module's existing fakes do) for a candidate above the floor; the captured award descriptor carries the per-candidate kW, not the floor kW | the first statement in 2.3(d) is deleted |
| T4 | `test_every_evaluate_spec_caller_passes_the_registry` | run `enumerate_sizing`, `underwrite_spec`, the evaluator and `two_stage` search each once with `_evaluate_spec` monkeypatched to a recorder; `"registry" in kwargs` on every call | any `registry=reg` in the caller table is deleted (also a `TypeError` in the real function) |
| T5 | `test_chosen_spec_resolves_reserve_what_sizing_reserved` (new file `tests/test_d61_reservation_reaches_every_resolve.py`) | monkeypatch `MilpDispatchSolver.solve` with a wrapper that records `(problem.power_kw, award committed kW)` and delegates; run `generate_quote` once on an awarded synthetic site with export consented and a price series so `_export_upside` runs; assert every recorded pair satisfies `kW == expected(power_kw)` from the T1 formula. One parametrised case per engine function in table 2.3(e), each calling the method directly, is acceptable if one `generate_quote` run does not reach all five; the demo engine gets the same two direct calls | any `candidate_power_kw=` in table 2.3(e) is deleted |
| T6 | `test_solver_records_the_reservation_it_compiled` (in `tests/test_optimisation_commitments.py`) | real solve with an award constraint at candidate P: `schedule.local_flex_reserved_kw == ((commitment_id, kW),)` equal to the descriptor's kW; a solve with no award gives `None` | the 2.4 field assignment is deleted |
| T7 | `test_invoice_is_the_recorded_reservation_at_every_size` | real solves at three sizes (floor, between, above declared) through `dispatch_constraints_for(..., candidate_power_kw=P)`; `_local_flex_value` availability `£ == rate * reserved_kW/1000 * hours * (1 - aggregator cut)` with every factor read from the same sources the module's #8 test reads; and the three invoices are strictly increasing until the declared kW binds | 2.5 stops reading the record (falls back to the floor, so sizes 2 and 3 fail) |
| T8 | `test_cm_reservation_does_not_set_the_local_flex_invoice` | hand-built schedule with `deliverable_capacity_mw=0.05` and `local_flex_reserved_kw=(("id", K),)`; contracted MW in the note is `K/1000` | the old `deliverable_capacity_mw` read is restored |
| T9 | `test_unreserved_award_is_not_billed` | hand-built schedule with `local_flex_reserved_kw=()`; availability revenue 0.0 and the "WAS NOT RESERVED" note present | the `if ... in by_id` filter is removed |

#7 and #8 are the tests for the `reserved is None` branch.

---

## 3. D-130 — the award's tenor reaches finance

### 3.1 Where it enters

`run_financial_model` (`src/gridq/finance/financial_model.py:121`) already accepts `commitments=` and
hands them to `resolve_contract_tenor_evidence` at `:157-159`, which reads
`AvailabilityCommitment.tenor_years` via `tenors_from_commitments` (`finance/cashflows.py:1232`).
`site.local_flex_awards` is `list[AvailabilityCommitment] | None` (`schemas/site.py:141`). No caller
supplies it: `quotes/pipeline.py:484`, `quotes/sensitivities.py:161`, and the sizing
`finance_runner` call at `sizing/sizer.py:434` all omit `commitments=`. `build_financial_case`
(`financial_model.py:326`) is the dead wrapper that made check B count a caller.

### 3.2 The change (one site, not in the hotspot)

In `financial_model.py`, immediately before `:157`:

```python
# D-130: the site's own attested awards are contract evidence on EVERY call path (the quote, the
# scenario sensitivities and the sizing loop's finance runner all arrive here), so the sizing
# ranking and the priced quote can never disagree about how long an award runs.
commitments = [*(site.local_flex_awards or ()), *(commitments or ())]
```

Site awards first, caller-supplied after, so `tenors_from_commitments`' existing "minimum of
differing lengths, with a warning" rule applies across both. Update the docstring paragraph at
`:136-146`. Do not edit `cashflows.py`.

Why here and not `pipeline.py:484`: a pipeline-only fix leaves the sizing loop ranking candidates on
a 15-year award and the sensitivities repricing on one, which is the inconsistency class the
"one engine, called twice" contract exists to prevent.

### 3.3 What the existing arithmetic then does (read, `cashflows.py:1992-2024`)

With `tenor_years = 1.0` on LOCAL_FLEX_PICLO:

- In term (year 1): the award's contracted revenue is held flat, not faded.
- After expiry (years 2+): P50 carries `tenored_base x decay`, labelled "merchant-class
  re-contracting"; P90 carries nothing (`_NO_P90_AFTER_EXPIRY`, `:132`, contains LOCAL_FLEX_PICLO);
  the lender-basis contracted series carries nothing.
- The warning from `_warn_contract_tenor_basis` (`:1373`) names the term.

So after this change the award is **contracted** for one year, and P90 and the contracted (lender)
series earn it for one year. **P50 still carries a faded re-contracting expectation in years 2+.**
Whether "a one-year award earns for one year" also requires P50 to stop is an owner decision (Q1):
stopping P50 means changing population 3 in `cashflows.py` and reduces a figure, which D57 forbids
without the owner. The executor implements 3.2 only, reports the measured before/after for P50, P90
and the contracted series on Tanhouse, and reports acceptance item 3 as "met on the contracted and
P90 basis; P50 re-contracting awaits Q1".

STOP condition: if the run emits "Contract tenor supplied for 'LOCAL_FLEX_PICLO' ... but that stream
prices no CONTRACTED revenue this run" (`_resolve_contract_tenors`, `cashflows.py:1173`) on an
awarded site whose local-flex leg is priced, the tenor is being dropped. Stop and report; do not
reclassify the stream.

### 3.4 Tests (add to `tests/test_finance_tenor_threading.py`)

| # | Test | Asserts | Goes red if |
|---|---|---|---|
| T10 | `test_site_award_tenor_reaches_finance_from_generate_quote` | `generate_quote` on an awarded site (the cheapest awarded fixture the module or `test_local_flex_owner_attested_award.py` already builds): the financial model's `cashflows.contracted_tenor_years` contains `("LOCAL_FLEX_PICLO", 1.0)` (field at `cashflows.py:347`, written `:2128`) and `quote.warnings` contains the tenor-basis warning naming 1 year | the 3.2 line is deleted |
| T11 | `test_sizing_finance_runner_sees_the_award_tenor` | `enumerate_sizing` with a recording `finance_runner` that delegates to `run_financial_model`; every returned model has the 1.0 tenor | 3.2 is moved to `pipeline.py` only |
| T12 | `test_one_year_award_is_contracted_for_one_year` | from the T10 model: contracted (lender-basis) local-flex revenue is positive in year 1 and exactly 0.0 in years 2 to N; P90 local-flex contribution is 0.0 after year 1. State in the docstring that P50 re-contracting is unchanged pending Q1 and assert nothing about it | the tenor stops being applied |

---

## 4. D-116 — performance ratio, or the leg labelled gross

### 4.1 Finding: no sourced ratio exists

`assumptions/gb_v1_12.yaml` (the newest release) carries, at about lines 2445-2470, the ENA Monthly
Utilisation Performance Factor **formula** and its per-product grace and multiplier parameters
(`monthly_utilisation_performance_factor`, NGED_SU / SAOU / OU, UKPN_ALL), sourced to the ENA
"Standardised DNO Settlement Methodology" v1.0, August 2024. It carries **no delivery or performance
ratio value** for any site, DNO or product. A search of `assumptions/` for mupf, performance ratio
and delivery ratio finds none. `docs/bess_constants_register.md:513` has a DFS fleet factor of 0.765;
that is a different product and must not be substituted (data-access rule: ask the owner before
substituting a method).

**Therefore: label the leg gross in `quote.warnings`. Do not invent a ratio.** The parameter is still
plumbed so a sourced value has a path the day one is released.

### 4.2 Where it enters

`local_flex_availability_value(..., utilisation_performance_ratio: float | None = None, ...)`
(`gb_streams.py:2523-2625`); `None` produces the stream note "Monthly Utilisation Performance Factor
NOT applied (held at 1.0 — an UPPER BOUND)" (`:2609`). The call in `_local_flex_value`
(`valuation.py:771`) omits the argument. That note lives on the stream's notes and does not reach
`quote.warnings`.

### 4.3 The change

1. `gb_streams.py`, next to `local_flex_availability_value`:

   ```python
   LOCAL_FLEX_GROSS_LABEL = (
       "LOCAL-FLEX AVAILABILITY LEG IS GROSS: no sourced utilisation performance ratio exists in "
       "the assumption library, so the ENA Monthly Utilisation Performance Factor is held at 1.0 "
       "and the availability payment on every award at this site is an UPPER BOUND, before any "
       "under-delivery taper (D-116). DATA ASK: the DNO's settlement statements for the award, "
       "or a published delivery ratio for the product."
   )

   def sourced_utilisation_performance_ratio(assumptions: AssumptionLibrary) -> float | None:
       """The library's sourced utilisation performance ratio, or None. Never a default (D-116)."""
       return _num(_maybe(assumptions, "local_flex_piclo", "utilisation_performance_ratio"))

   def local_flex_gross_label(site_model: SiteModel, assumptions: AssumptionLibrary) -> str | None:
       if not site_model.local_flex_awards:
           return None
       if sourced_utilisation_performance_ratio(assumptions) is not None:
           return None
       return LOCAL_FLEX_GROSS_LABEL
   ```

   `_maybe` / `_num` are the accessors already used at `gb_streams.py:3011`. Confirm `_maybe`
   returns `None` for an absent key on the current release (it does for
   `parasitic_load.standby_kw_per_mw`-style lookups); if it raises instead, wrap for absence only.
   Do **not** add the key to any `assumptions/*.yaml`.
2. `valuation.py:771`: pass
   `utilisation_performance_ratio=sourced_utilisation_performance_ratio(assumptions)` to
   `local_flex_availability_value`. One source decides both the price and the label.
3. `quotes/pipeline.py`, directly after the `warnings` list is built at `:481`:

   ```python
   gross_label = local_flex_gross_label(site, assumptions)
   if gross_label is not None:
       warnings.append(gross_label)
   ```

   `warnings` is not part of `input_hash` (`pipeline.py:515`), so `quote_id` does not move. The demo
   site has no award, so the golden pins do not move.

### 4.4 Tests (new file `tests/test_local_flex_gross_label.py`)

| # | Test | Asserts | Goes red if |
|---|---|---|---|
| T13 | `test_awarded_quote_labels_the_leg_gross` | `generate_quote` on an awarded site: `LOCAL_FLEX_GROSS_LABEL in quote.warnings` | the 4.3(3) call is deleted |
| T14 | `test_unawarded_quote_carries_no_gross_label` | demo site: label absent | the `local_flex_awards` guard is removed |
| T15 | `test_a_sourced_ratio_is_applied_and_removes_the_label` | monkeypatch `sourced_utilisation_performance_ratio` (in both `gb_streams` and the name imported into `valuation`) to return 0.9; `_local_flex_value` availability equals `gross x payable_delivery_fraction(0.9, ...)` computed by calling the library function with the same product parameters, the "NOT applied" note is absent, and `local_flex_gross_label` returns `None` | the 4.3(2) argument is deleted |
| T16 | `test_no_release_carries_an_unsourced_ratio` | for every `assumptions/gb_v*.yaml`, `sourced_utilisation_performance_ratio` is `None`, **or** the entry carries a non-empty source. Guards against a ratio being added without provenance | someone adds a bare number |

---

## 5. D62 — the six restated tests

All figures in this section are **measured** at `3163dff` in the local `.venv` (highspy 1.15.1).

### 5.1 Code change for #20, #21, #22 (`valuation.py`, `schedule.py`)

D62 limb 1: the zero-dispatch note fires when discharge **outside DFS-called periods** is zero, and
states DFS delivery separately.

- `schedule.py`: add an accessor beside `annual_discharge_kwh` (`:138`):
  `annual_discharge_outside_dfs_kwh` = the same weighted, annualised sum restricted to intervals
  where `dfs_price_gbp_per_kwh` is not `> 0`; returns `annual_discharge_kwh` unchanged when
  `dfs_price_gbp_per_kwh is None`. Use exactly the weighting `annual_discharge_kwh` uses.
- `_zero_dispatch_note` (`valuation.py:1481`): key on
  `schedule.annual_discharge_outside_dfs_kwh > 0.0` instead of `annual_discharge_kwh > 0.0`.
  - Total discharge 0: the returned text is **byte-identical** to today.
  - Outside-DFS discharge 0 and total > 0: same text, with the existing statement of the tariff
    result kept, plus one added sentence:
    `f"DFS DELIVERY IS SEPARATE: the battery delivered {schedule.dfs_delivered_mwh:,.3f} MWh/yr
    inside the DFS-called periods, priced on the DFS stream. Outside those periods it dispatched
    NOTHING on the tariff."` Keep the substrings `"dispatched NOTHING"` and
    `"declining a loss-making trade"` and every arithmetic figure the existing tests assert.
- `_no_spec_notes` (about `:1620`): branch on the same accessor. In the zero branch, when total
  discharge is > 0, replace `"DISCHARGED NOTHING AT ALL (0 kWh)"` with
  `"DISCHARGED NOTHING OUTSIDE THE DFS-CALLED PERIODS (0 kWh on the tariff)"` and add the same DFS
  delivery sentence. When total discharge is 0 the text is byte-identical to today. The
  "DISPATCH WAS SOLVED AND RAN" branch (`:1652`) is reached only when outside-DFS discharge is > 0.
- `TARIFF_ARBITRAGE` revenue/cost arithmetic is not changed.

### 5.2 #20 — `tests/test_valuation_zero_dispatch_note.py` (gb_v1_9, 100 kW / 2 h)

Measured: 336 intervals, 8 DFS-called, annual factor 52.142857; `annual_discharge_kwh` 7,826.294,
all of it in DFS periods (outside-DFS 0.0; no charge in called periods); `annual_charge_kwh`
8,893.516 (= 7,826.294 / 0.88); `dfs_delivered_mwh` 7.826294; `arbitrage_value_gbp` -62.25
(= 8,893.516 x 0.095 - 7,826.294 x 0.10); DFS £1,249.95 at £0.159711/kWh.

Restate:
- Premise: `schedule.annual_discharge_outside_dfs_kwh == 0.0` and
  `schedule.annual_discharge_kwh == pytest.approx(schedule.dfs_delivered_mwh * 1000)` and `> 0`
  (replaces the old `annual_discharge_kwh == 0` premise).
- TARIFF_ARBITRAGE: revenue `0.0`; cost `== pytest.approx(charge*0.095 - discharge*0.10, abs=0.01)`
  with the two rates read from the fixture tariff, not typed as literals; the value is 62.25.
- Note present; keeps `"dispatched NOTHING"`, `"declining a loss-making trade"`, and the arithmetic
  strings `0.05198`, `0.00500`, `-0.015600`, `-0.018744` unchanged.
- New: the note contains `"DFS DELIVERY IS SEPARATE"` and the delivered MWh formatted from the
  schedule.
- `test_no_zero_dispatch_note_when_the_battery_actually_runs`: its guard becomes
  `annual_discharge_outside_dfs_kwh > 0`.

### 5.3 #21, #22 — `tests/test_no_spec_dispatch_evidence.py` (gb_v1_8, full `generate_quote`)

Triage figures: best candidate 350 kW / 3 h, 34,414.286 kWh/yr, all in DFS periods.

- #21 `test_zero_dispatch_record_reports_a_measured_zero_with_its_arithmetic`: replace the
  `"DISCHARGED NOTHING AT ALL"` assertion with `"DISCHARGED NOTHING OUTSIDE THE DFS-CALLED PERIODS"`;
  add an assertion that the DFS delivery sentence is present with a positive MWh; the arithmetic
  strings `0.06562`, `0.00600`, `-0.052464` stay as written.
- #22 `test_value_measured_now_distinguishes_the_two_records`: expected to pass with no edit once
  5.1 lands. If it does not, STOP and report what it asserts; do not edit it speculatively.

### 5.4 #12 — `tests/test_optimisation_milp_solver.py:557` (gb_v0_1, fixture `site` at `:69`)

D62 limb 3: move to a fixture with no DFS-called periods. Measured: copying the fixture site with
postcode `"IV1 1AA"` (DFS zone 1: 82 observed periods/yr, which scales to 0 called half-hours on a
7-day horizon) gives `schedule.dfs_price_gbp_per_kwh is None`, a levelled 62.539 kW on all five
weekdays and max net import 257.461123. Every existing assertion holds unchanged.

Restate: build the site for this one test as
`site.model_copy(update={...postcode "IV1 1AA"...})` (use whichever field the fixture sets the
postcode on; `tests/_milp_support.py` `multi_rate_site` uses `"LE17 4XN"`), add the guard
`assert schedule.dfs_price_gbp_per_kwh is None, "this test is about peak levelling; it must run on
a horizon with no DFS-called period (D62 limb 3)"`, and leave every other assertion as written.

### 5.5 #24 — `tests/test_windowed_levy_capacity_market.py:263` (slow tier, latest release)

Measured: 5,616 intervals, 492 CM-window half-hours, 119 DFS half-hours, 42 in both.

| Arm | discharge kWh/yr | in-window share | discharge outside (window U DFS) | discharge in window, not DFS |
|---|---|---|---|---|
| no levy | 12,513.613 | 0.33876 | n/a (100% inside DFS periods) | 0 |
| levy | 44,199.392 | 0.86869 | 0 | > 0 |

Restate, per D62 limb 2 ("the levy raises the in-window share against the no-levy arm"):
- with the window mask taken from the levy arm's `windowed_levy_rate_gbp_per_kwh > 0` and the DFS
  mask from each arm's `dfs_price_gbp_per_kwh > 0`:
  - `share_with > share_without` (the law);
  - no-levy arm: discharge outside DFS periods `== pytest.approx(0, abs=1e-6)`;
  - levy arm: discharge outside (window U DFS) `== pytest.approx(0, abs=1e-6)`;
  - in-window, non-DFS discharge: `== approx(0)` without the levy, `> 0` with it;
  - keep `with_cm.annual_discharge_kwh > 1_000` exactly as written.
- No share threshold is introduced. The two measured shares go in the docstring as evidence, not in
  an assertion.

### 5.6 #25 — same file, `:380`

Measured: year-1 on 19,994.06, off 9,003.05, delta 10,991.01; credit 11,741.78; availability 0.95;
credit x availability 11,154.691; delta-floor -163.68. Components: firm revenue on/off 14,016.93 /
13,848.37; perfect-foresight adjustment 2,803.39 / 2,769.67; contracted (DFS) 1,744.84 / 2,051.97;
DUoS 3,929.69 / 3,761.13; arbitrage 10,087.24 in both; other cost lines equal.

Exact identity: `delta_year1 = (credit + (d_firm - d_pf_adjustment) + d_contracted) x availability`.

Restate, per D62 limb 2:
- keep `delta > 0`, `delta > 1_000` and the capex equality exactly as written;
- delete the on/off revenue-equality assertion (it is the one D62 says no longer holds, because the
  levy legitimately moves dispatch);
- add `delta_floor = ((firm_on - firm_off) - (pf_on - pf_off) + (contracted_on - contracted_off))
  * availability`, computed from `annual_revenue_firm_gbp`, `perfect_foresight_adjustment_gbp` and
  `annual_revenue_contracted_gbp` on the two cashflow models, minus the difference of every
  non-credit cost line times availability (measured zero; include the term so a future cost
  movement is not silently absorbed);
- assert `delta_floor <= 0` and
  `delta == pytest.approx(credit * availability + delta_floor, abs=0.05)`.

---

## 6. Mutation checks (D62 rule; in memory only)

Write this plugin to a scratch directory **outside the repo** (for example `$TMPDIR/mut/`). It was
validated at `3163dff`: with it, a control test for mutation M24 and one for M12 each went red, and
both pass without it.

```python
# mutate_plugin.py — in-memory mutation. NEVER writes to disk.
# Env: MUT_MODULE (dotted), MUT_OLD (exact source text, must occur exactly once), MUT_NEW.
import importlib, os, types

def _swap(orig, new):
    for wrap in ("__func__", "fget"):
        if hasattr(orig, wrap) and hasattr(new, wrap):
            orig, new = getattr(orig, wrap), getattr(new, wrap)
    if isinstance(orig, types.FunctionType) and isinstance(new, types.FunctionType):
        if orig.__code__.co_freevars == new.__code__.co_freevars:
            orig.__code__ = new.__code__
            orig.__defaults__ = new.__defaults__
            orig.__kwdefaults__ = new.__kwdefaults__

def pytest_configure(config):
    mod = importlib.import_module(os.environ["MUT_MODULE"])
    old, new = os.environ["MUT_OLD"], os.environ["MUT_NEW"]
    with open(mod.__file__, encoding="utf-8") as fh:
        src = fh.read()
    if src.count(old) != 1:
        raise SystemExit(f"MUTATION NOT APPLIED: {src.count(old)} occurrences of MUT_OLD")
    ns = {"__name__": mod.__name__, "__file__": mod.__file__, "__package__": mod.__package__}
    exec(compile(src.replace(old, new), mod.__file__, "exec"), ns)
    for name, obj in ns.items():
        orig = mod.__dict__.get(name)
        if isinstance(obj, type) and isinstance(orig, type) and obj.__module__ == mod.__name__:
            for attr, member in vars(obj).items():
                if attr in vars(orig):
                    _swap(vars(orig)[attr], member)
        elif isinstance(obj, types.FunctionType) and obj.__module__ == mod.__name__:
            _swap(orig, obj)
        elif isinstance(obj, (int, float, str, bytes, tuple, frozenset)) and not name.startswith("__"):
            setattr(mod, name, obj)
    print(f"\nMUTATION APPLIED IN MEMORY: {mod.__name__}: {old!r} -> {new!r}")
```

Run form (from the repo root):

```
MUT_MODULE=<module> MUT_OLD='<old>' MUT_NEW='<new>' PYTHONPATH=<scratch>/mut \
  .venv/bin/python -m pytest -o addopts="" -q -s -p no:cacheprovider -p mutate_plugin <test id>
```

| Id | Restated test | `MUT_MODULE` | `MUT_OLD` -> `MUT_NEW` | Required result |
|---|---|---|---|---|
| M12 | #12 | `gridq.optimisation.milp_solver` | `_EPS_PEAK = 1e-6` -> `_EPS_PEAK = 0.0` | red |
| M20 | #20 | `gridq.optimisation.valuation` | inside `_zero_dispatch_note`, the new key `schedule.annual_discharge_outside_dfs_kwh > 0.0` -> `schedule.annual_discharge_kwh > 0.0` (this is the pre-D62 behaviour) | red |
| M21 | #21 | `gridq.optimisation.valuation` | inside `_no_spec_notes`, the new accessor read -> `annual_discharge_kwh` | red |
| M22 | #22 | same as M21, then separately M20 | as above | red under at least one; record which |
| M24 | #24 | `gridq.optimisation.milp_solver` | `price = energy_price + duos + levy_rate + windowed_levy + dfs_price` -> `price = energy_price + duos + levy_rate + dfs_price` | red |
| M25 | #25 | `gridq.finance.cashflows` | `windowed_year = windowed_credit_line * decay * availability_factor` -> `windowed_year = 0.0` | red |

`MUT_OLD` must occur exactly once in the module; write the new key in 5.1 so that the two call sites
(`_zero_dispatch_note`, `_no_spec_notes`) are textually distinguishable, or include enough of the
surrounding line to make each unique. After each mutation run, run the same test without the plugin
and confirm green, then `git status --short` to confirm nothing on disk changed. Record all twelve
outcomes (six red, six green) in the handoff.

A restated test that stays green under its mutation is vacuous: STOP and strengthen the assertion
that should have caught it. Do not change the mutation to something easier.

---

## 7. Pins, tests and campaign figures expected to move

### 7.1 Not expected to move

- `tests/test_determinism_golden.py` (`PINNED_DEMO_VALUES`): the demo site has no award. D61, D-130
  and D-116 do not reach it. D63 says pins stay as minted. **Any movement here is a bug: STOP.**
- The availability £ at a given size on a run with no CM reservation: reserved kW after equals the
  invoiced kW before (same formula), so that line is unchanged. What moves is dispatch.

### 7.2 Expected to move (awarded real sites, all nine)

`examples/real/sites/*.json` each carry one LOCAL_FLEX_PICLO award, `tenor_years` 1.0, and one
`provisioning_requirements` entry. Declared kW: bishops_cleeve_ash_plant 46.32, bishops_cleeve_mrf
147.48, colnbrook 915.077, ewelme 69.28, ewelme_invoice_anchored 404.839, harwell 213.81, knowl_hill
227.2, knowl_hill_invoice_anchored 95.992, tanhouse 486.35.

1. **Dispatch at every candidate whose reservation rose**: arbitrage, DUoS, DFS and utilisation
   legs shift slightly downward in the award windows (Harwell 250 kW: discharge 135,855 -> 134,970
   kWh/yr). Selection can move where two candidates were close.
2. **Local-flex availability where a CM reservation was compiled**: the invoice was clamped at the
   CM kW (2.1); it becomes the award reservation.
3. **Finance after D-130**: P90 and the contracted (lender) series lose the award after year 1;
   P90 DSCR and any P90-based verdict can move. P50 is expected unchanged (3.3); if P50 moves,
   report it, since that contradicts the reading in 3.3.
4. **`quote.warnings`** on every awarded site gains the gross label and the tenor-basis warning.

Places that hold such figures (re-run, never hand-edit): `tests/test_e2e_examples_real.py`,
`tests/test_real_sites_milp_runs.py`, `tests/test_assumptions_page.py`,
`tests/test_run_always_draws_the_dispatch_day.py`, `tests/test_local_flex_parity.py`, the campaign
documents under `docs/grundon_harwell/` (including `matrix.py`), and the outputs of
`scripts/build_client_report.py`. Find the full set with
`grep -rln "harwell\|tanhouse\|colnbrook\|ewelme\|knowl_hill\|bishops_cleeve" tests docs/grundon_harwell scripts`.

### 7.3 Hand recomputation to record for each pin that moves

For each moved pin write, in the commit body and in the test's own comment where the pin lives:

```
reserved kW  = min(declared kW, P x (1 - standby_kw_per_mw/1000) x (1 - fade%/100)^tenor)
availability = rate GBP/MW/h x reserved kW / 1000 x published window hours x (1 - aggregator cut)
```

with every factor quoted from its source: the rate and hours from the awarded zone row in the
release (Harwell: 174.99 GBP/MW/h x 136 h; Ash Plant: 1.33 GBP/MW/h x 1,098 h), `standby_kw_per_mw`
from `parasitic_load`, the fade from `degradation.annual_capacity_fade_pct`, tenor from the site's
award, aggregator cut 20%. Worked check that must still hold after the change (it is #8):
Harwell at the seam default, 174.99 x 0.0103716 x 136 = £246.83.

For a pin that moves through dispatch (items 1 and 3) the hand recomputation is the changed input
(old and new reserved kW in the award windows), the direction (the reservation removes net-injection
headroom of `reserved_after - reserved_before` kW in those windows), and the old and new value with
the difference attributed to named streams from the run record. A pin whose movement cannot be
attributed to one of items 1 to 4 is not re-minted: STOP.

---

## 8. D63 — highspy lock, and LOCK_GAPS

- `requirements.txt:50`: `highspy==1.14.0` -> `highspy==1.15.1`. This is the only lock pin.
  `pyproject.toml:29` is the range `highspy>=1.7` (leave). The Dockerfile installs from
  `requirements.txt` and has no pin of its own (leave).
- `.github/workflows/ci.yml:46`: `LOCK_GAPS: openpyxl==3.1.5 highspy==1.15.1` becomes
  `LOCK_GAPS: openpyxl==3.1.5`. highspy is no longer a departure from the lock, so installing it a
  second time would hide a future lock drift. Update the header comment at `:8-9` ("the two
  departures in LOCK_GAPS" -> one) and the comment at `:40-45`. The variable is consumed at `:64`,
  `:89`, `:115`; those lines need no edit.
- `.github/workflows/nightly.yml:36`: same change; comment at `:34-35`.
- `docs/deployment.md:61`: `highspy 1.14.0` -> `1.15.1`.
- openpyxl stays in `LOCK_GAPS`: it is not in `requirements.txt`.

Check: `grep -rn "1\.14\.0" requirements.txt .github docs/deployment.md Dockerfile` returns nothing
for highspy; `grep -n LOCK_GAPS .github/workflows/*.yml` shows one package.

**Finding to report, not to fix (Q3).** The local `.venv` differs from `requirements.txt` in 28
other packages (for example numpy 2.5.1 vs 2.4.6, pandas 3.0.5 vs 3.0.3, xarray 2024.11.0 vs
2024.10.0, polars 1.43.2 vs 1.42.0; linopy matches at 0.5.8). D63 authorises highspy only. Do not
touch any other pin. "ruff and mypy run clean from the repo's own environment" and the golden pins
must therefore be confirmed either in a fresh venv built from the lock or by the CI golden-pins job,
and the handoff must say which.

---

## 9. Order of work, with the check after each step

Abbreviation: `PT` = `.venv/bin/python -m pytest -o addopts="" -q`. Run from the repo root.

| Step | Do | Check (must pass before the next step) |
|---|---|---|
| 9.0 | Baseline. `git status --short`; `df -h /`; `PT tests/test_local_flex_owner_attested_award.py tests/test_optimisation_milp_solver.py tests/test_valuation_zero_dispatch_note.py tests/test_no_spec_dispatch_evidence.py` | exactly #7, #8, #12, #20, #21, #22 fail; record the output |
| 9.1 | D63 (section 8) | the two greps in section 8; `PT tests/test_optimisation_milp_solver.py -k "not peak"` unchanged |
| 9.2 | D62 #12 restated (5.4) | test green; M12 red; green again without the plugin |
| 9.3 | Measure before D61: for all nine real sites and the candidate powers the site's own sizing grid produces, record solver status and EOL verdict with today's floor constraints (script in scratch, built from `dispatch_constraints_for` + `MilpDispatchSolver`, about 20 s per solve; run serially or check `df -h /` first) | a table of 9 sites x sizes, kept for 9.6 |
| 9.4 | D62 code change 5.1, then #20, #21, #22 restated (5.2, 5.3) | three tests green; M20, M21, M22 red; green again without the plugin; `PT tests/test_valuation_zero_dispatch_note.py tests/test_no_spec_dispatch_evidence.py` fully green |
| 9.5 | D61 seam 2.3(a)-(c), tests T1, T2 | T1, T2 green; `PT tests/test_local_flex_owner_attested_award.py` shows only #7, #8 red (unchanged from baseline) |
| 9.6 | D61 solver record 2.4 and valuation 2.5; tests T6, T7, T8, T9 | **#7 and #8 green with zero edits to them** (`git diff --stat tests/test_local_flex_owner_attested_award.py` shows additions only, below line 700); T6-T9 green |
| 9.7 | D61 `_evaluate_spec` 2.3(d) and re-solve sites 2.3(e); tests T3, T4, T5 | T3-T5 green; delete-and-restore check: temporarily remove one `candidate_power_kw=` from table 2.3(e) in memory using the plugin (`MUT_OLD` = that argument text with enough context to be unique) and confirm T5 goes red; repeat 9.3's measurement with per-candidate constraints and compare: **no candidate that was OPTIMAL before may be INFEASIBLE after, and no EOL verdict may flip True -> False** |
| 9.8 | D-130 (3.2), tests T10-T12 | green; no "prices no CONTRACTED revenue" warning for LOCAL_FLEX_PICLO on an awarded site; record Tanhouse P50 / P90 / contracted year-by-year before and after |
| 9.9 | D-116 (4.3), tests T13-T16 | green |
| 9.10 | D62 #24, #25 restated (5.5, 5.6) | `.github/ci/run_tests.sh module tests/test_windowed_levy_capacity_market.py` or `PT` on the file: green; M24, M25 red; green again |
| 9.11 | Golden pins | `.github/ci/run_tests.sh module tests/test_determinism_golden.py`: green with **no pin edited** |
| 9.12 | Fast tier | `df -h /`, then `.github/ci/run_tests.sh fast`: zero failures. Any failure is either a real-site figure listed in 7.2 (re-mint per 7.3) or a regression (fix the code, not the test) |
| 9.13 | Slow tier and remaining heavy modules | `.github/ci/run_tests.sh slow`; each entry of `.github/ci/run_tests.sh list heavy` via `module`: zero failures |
| 9.14 | Lint and types | `.venv/bin/ruff check .`; `.venv/bin/ruff format --check src tests .github/ci`; `.venv/bin/mypy`: all clean |
| 9.15 | Campaign figures | re-run the generators for every document found by the 7.2 grep; diff; each changed figure carries its 7.3 recomputation |
| 9.16 | Records | `docs/decisions.md`: no new D-number is needed for D61-D64 work itself; add one only if the owner answers Q1 or Q2. Update the findings triage (`docs/reviews/2026-11-16-findings-register-triage.md:70`, `:124`, `:164-165`) to mark D-130 and D-116 with the commit and the reachability test names; update `docs/shared_work/core01_failure_triage.md` with the eight closures and the twelve mutation outcomes; changelog and handoff per `CLAUDE.md`; then the roadmap rule (`../atlas/roadmap/roadmap.yaml` WP-UW-16 at about `:2972`, WP-CORE-01 at about `:1872`, `build_map/status.json`, re-render and republish the Build Map per Atlas `CLAUDE.md` non-negotiable 8) |
| 9.17 | Final | `git status` clean on the commit to be tagged (see Q5); fast tier zero failures on that commit |

---

## 10. Risks and STOP conditions

| Risk | Where | Handling |
|---|---|---|
| Collision in `milp_solver.py` | 2.4 | One additive block (a tuple and one constructor keyword). Rebase before starting 9.6; if `:2136-2198` has changed upstream, re-read before editing |
| Collision in `cashflows.py` | none | Not edited. M25 mutates it in memory only |
| Award + load-service obligation sum to more than the envelope | `_compile_commitments` `:2400-2410`: awards add to `pre_reserved_kw` | 9.3 / 9.7 comparison. **STOP** on any OPTIMAL -> INFEASIBLE or EOL True -> False. Never relax the reservation, never lower the award to make it fit: that is an owner decision under D57 |
| Sizing selection moves on a real site | 7.2 item 1 | Expected in principle; report each moved selection with old and new spec and the attributing stream |
| A solved schedule with no compiled award bills the seam default | 2.5 `reserved is None` | Unreachable on the production path (every production problem is built through `dispatch_constraints_for`, and T5/T6 prove the record is present). Recorded as a known property, required by #7 and #8 passing as written |
| Per-candidate note wording | 2.3(a) | Floor wording stays byte-identical when no candidate is passed, so existing string assertions hold |
| `quote.warnings` grows on awarded sites | 3, 4 | Not part of `input_hash`; any test pinning an exact warnings list on an awarded site moves and is updated with the two new entries named |
| Solver-version sensitivity | D63 | Pins minted on 1.15.1; the lock now says 1.15.1; other venv/lock drift is Q3 |
| Mutation plugin swaps code objects | 6 | It refuses to run unless `MUT_OLD` occurs exactly once and prints the mutation; a closure whose free variables change is skipped, so prefer mutations inside plain methods (all six are) |
| Disk | multi-worker runs | `df -h /` before 9.12 and 9.13 |

STOP list (report to the owner, change nothing further): a golden demo pin moves; #22 does not pass
after 5.1; a restated test stays green under its mutation; a candidate goes INFEASIBLE or fails EOL
after D61; the LOCAL_FLEX_PICLO tenor is dropped as "no CONTRACTED revenue"; P50 moves under D-130;
a moved figure cannot be attributed to 7.2 items 1-4; any step would require editing #7 or #8,
`cashflows.py`, or an assumptions release.

---

## 11. Questions for the owner (the executor does not decide these)

- **Q1 (D-130).** After 3.2 a one-year award is contracted, P90-earning and lender-countable for one
  year, and P50 keeps a faded merchant re-contracting expectation in years 2+. Is that "earns for
  one year", or must P50 also stop at expiry? The second option edits population 3 in `cashflows.py`
  and lowers P50 on all nine sites.
- **Q2 (D-116).** No sourced utilisation performance ratio exists, so the leg is labelled gross. Can
  the owner supply DNO settlement statements or a published delivery ratio? The DFS fleet factor
  0.765 in `docs/bess_constants_register.md:513` was not substituted.
- **Q3 (D63).** The local `.venv` differs from the lock in 28 packages besides highspy. Rebuild the
  venv from the lock, or re-lock to the venv? Either is a separate decision from D63.
- **Q4 (stale text left in place).** (a) The comment inside #7/#8 describes the pre-D61 mechanism;
  the tests were left untouched as D61 requires. (b) `local_flex_award_disclosure` still says "THE
  AVAILABILITY LEG OF THIS AWARD IS STILL £0" (`gb_streams.py:3302`), asserted by
  `tests/test_local_flex_owner_attested_award.py:248`; it has been false since `6e19c08`.
  (c) `dfs_service_period_mask` (`gb_streams.py:4367`) reports "no half-hour inside the measured DFS
  event window" when the real cause is the zonal count rounding to zero on a short horizon.
- **Q5.** `CLAUDE.md` is modified and uncommitted in the working tree. Commit it as its own change
  before the WP-CORE-01 tag?
