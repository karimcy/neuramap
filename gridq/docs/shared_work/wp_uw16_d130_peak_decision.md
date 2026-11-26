# WP-UW-16 / WP-CORE-01: decisions on D-130 and the demo peak pin

Written 2026-11-23 by the Fable advisor, read-only, against `roadmap/wp-uw16-core01` at `60af5a1`
with the one uncommitted D-130 line in `src/gridq/finance/financial_model.py`. No source or test was
edited. It supersedes `docs/shared_work/wp_uw16_core01_plan.md` §3.3, the T12 row of §3.4, the
"P90" sentence of §11 Q1, and §7.1's claim that no golden pin moves.

Every figure marked **measured** was produced in this session by running the code in memory. The
scripts and outputs are in
`/private/tmp/claude-501/-Users-oliverpugh-Desktop-1---Neura-UK/5963dc34-c5e3-4f59-834d-5ff238ebf1aa/scratchpad/fable_peak/`.

| Question | Decision | Blocked on |
|---|---|---|
| Q1, D-130 | Implement D-130 as the code already rules (option a): the in-term award is absent from P90 and the lender series. The owner must be asked again first. | Owner's answer to the question in §1.4 |
| Q2, peak pin | A second solve that minimises the peak at the optimal £, on the one solve that produces the customer bill. New pin 235.841 kW. | Owner told that a published figure moves (§2.5); Linux golden job green |

---

## 1. Q1: D-130 against the in-term P90 rule

### 1.1 Decision

1. **D-130 does what the code already rules.** Once the award's tenor reaches finance, the
   local-flex line is absent from P90 and from the lender (contracted) series in every year, year 1
   included. P50 is unchanged. `_NO_P90_IN_TERM` is not edited.
2. **The owner must be asked again before the D-130 line is committed.** The figures fall, so D57
   needs the owner's approval, and the approval obtained today rested on a sentence that was wrong.
3. **Option (b), an in-term exception, is not available to the executor or to me.** It reverses a
   standing rule that rests on a MUST requirement and on a recorded decision. Only the owner can
   choose it. It is offered to the owner as option B.

### 1.2 What is true, and what the plan and the owner question got wrong

There are three states, and the plan described only two of them.

| State | P90 and lender treatment of the award | Tanhouse P90 year 1 / 15-year sum | Tanhouse lender year 1 / 15-year sum |
|---|---|---|---|
| Today (no tenor reaches finance) | 85% of the award counts in all 15 years | 1,391.65 / 16,196.02 | 1,370.19 / 14,107.93 |
| D-130 as the code rules (option a) | None of it counts, in any year | 1,028.23 / 11,602.63 | 1,006.76 / 9,514.54 |
| What plan §3.3 and the owner option text described (option b) | 85% counts in year 1, none after | 1,391.65 / about 11,966 | 1,370.19 / about 9,878 |

The first two rows are the executor's measurements. The third row is hand arithmetic: row two plus
the £363.42 that the award contributes to year 1 (£450.06 x 0.85 haircut x 0.95 availability). It
has not been run.

Plan §3.3 read the post-expiry rule (`_NO_P90_AFTER_EXPIRY`, `cashflows.py:132`) and missed the
in-term rule (`_NO_P90_IN_TERM`, `:171`; `_stream_contracted_haircut_pct`, `:1517`). The owner
question inherited the error twice: "P90 and the lender case already stop at contract end" is false
today (they count 15 years), and "count only the contracted year" is false after D-130 (they count
none).

### 1.3 Reason

**Option (a) is the standing rule, and it was set deliberately.**

- `docs/bess_modelling_requirements.md:1590`, D-139 (MUST): model the DNO flexibility line "with
  the P90 case set at or near zero, and never allow it to carry the DSCR".
- `docs/decisions.md:297`, D54 (owner-directed, 2026-08-17), records the consequence in words: "P90
  and the lender-basis DSCR move by exactly zero at every site ... its P90 contribution is forced to
  zero even inside a live award term (D-139/D-1410)".
- Every awarded quote already publishes it. `local_flex_award_disclosure`
  (`value_stack/gb_streams.py:3335`) emits "P90 AND THE LENDER-BASIS DSCR DO NOT MOVE", and
  `tests/test_local_flex_owner_attested_award.py:253` and `:257` assert it.
- Five tests in `tests/test_finance_in_term_contracted_p90.py` pin the in-term zero to the pound.

So today's quotes say the award does not reach P90 while £363 a year of it does, for 15 years. That
is the D-130 finding, seen from the other side. Option (a) makes the published sentence true.

**Is option (b) consistent with D-139 and D-1410?** Partly, and not enough to adopt it without the
owner.

- It contradicts D-139's headline ("at or near zero", "never carry the DSCR") and D54's recorded
  consequence.
- It is consistent with the implementation note of requirement D-130
  (`bess_modelling_requirements.md:1513`): "DSCR base case includes only contracted years".
  Requirements D-130 and D-139 pull in different directions on year 1, and the code chose D-139.
- D-1410 does not decide it. D-1410 is a published P90 *utilisation* rate of 0.00 to 0.02. It
  justifies a zero for the utilisation leg. It says nothing about the availability leg, which is a
  fixed fee.

**A defect found on the way, which must be fixed with option (a).** The reason the in-term zero
prints into the pack (`_NO_P90_IN_TERM_REASON`, `cashflows.py:180-189`) says GridQ prices local flex
"as UTILISATION revenue only ... with no availability component at all, so the published P90
utilisation floor of 0.00 IS this line's P90". That has been false since the availability leg was
priced (`6e19c08`, then D61; the executor's T7 and T8 hand figures are availability payments). The
moment D-130
lands, that sentence prints on every awarded quote. The module's own rule is that a wrong reason on
a right treatment is a defect, so the text is corrected in the same commit (§1.5 step 3).

**Why I recommend A to the owner and not B.**

- The award is the owner's attestation, not a sighted contract, and the published registers
  contradict it at six of nine sites (D54). The lender series exists to show what a lender would
  count.
- The ENA standard agreement lets either party end the award on 90 days' notice, so year 1 is not
  secure for the full year.
- The availability leg is priced gross, with no sourced delivery ratio (D-116).
- A hybrid (count the availability leg, zero the utilisation leg) would follow the evidence most
  closely. It is not buildable today: finance receives one £ per stream, not one per leg.

### 1.4 The owner question, word for word

> **Correction to a question you answered earlier today, and one decision.**
>
> Earlier you were asked about the one-year local-flex award and you chose "Keep faded renewal".
> That choice was about the central case (P50) and it stands: P50 does not change. But the option
> you picked also said "P90 and the lender case count only the contracted year". That sentence was
> wrong. Please choose how the cautious case (P90) and the lender case should treat the award.
>
> The facts:
>
> - Today the model does not know the award lasts one year. P90 and the lender case count 85% of
>   the award's income in all 15 years.
> - Once the model is told the award lasts one year, the engine's existing rule counts none of the
>   award's income in P90 or the lender case, in any year, including year 1. The rule dates from
>   August. Its reasons are that a DNO flexibility contract can be ended on 90 days' notice, and
>   that the award is your attestation and not a contract we have seen.
> - Whatever you choose, P50, the IRR and the payback stay the same. Under A and B, P90 and the
>   lender case fall.
>
> Tanhouse as the example, in £ a year. Every site whose award is modelled moves the same way.
>
> | | P90, year 1 | P90, 15-year total | Lender case, year 1 | Lender case, 15-year total |
> |---|---|---|---|---|
> | Today | 1,391.65 | 16,196.02 | 1,370.19 | 14,107.93 |
> | A | 1,028.23 | 11,602.63 | 1,006.76 | 9,514.54 |
> | B | 1,391.65 | about 11,966 | 1,370.19 | about 9,878 |
>
> The A figures are measured. The B figures are worked by hand and would be confirmed by a run.
>
> - **A. Count none of it (recommended).** P90 and the lender case leave the award out in every
>   year. This is the existing rule, and it is what every awarded quote already says in its notes.
> - **B. Count the contracted year only.** P90 and the lender case count the award in year 1 and
>   nothing after. This is what the earlier option text described. It reverses the August rule for
>   local flex.
> - **C. Leave it as today.** No figure falls. A one-year award keeps counting towards P90 and the
>   lender case for 15 years, the quote notes stay wrong, and finding D-130 stays open.

### 1.5 Exact instructions for the executor

**Before the owner answers: do not commit the D-130 line.** T10 and T11 below do not depend on the
answer and may be written now. Steps 9.9 onward of the plan do not depend on D-130 and may proceed.

**If the owner answers A:**

1. `src/gridq/finance/financial_model.py`: keep the uncommitted line and docstring as they are.
2. `src/gridq/finance/cashflows.py`: no arithmetic change. `_NO_P90_IN_TERM`,
   `_NO_P90_AFTER_EXPIRY` and `_stream_contracted_haircut_pct` keep their bodies.
3. `src/gridq/finance/cashflows.py`, **text only**. This lifts the plan's "never edit
   `cashflows.py`" rule for these two edits and nothing else. The coordinator confirms no other
   agent holds the file.
   - `_NO_P90_IN_TERM_REASON[MarketStream.LOCAL_FLEX_PICLO.value]` (`:181-189`) becomes:

     ```
     "the DNO flexibility line's P90 case must sit at or near zero and must never carry the "
     "DSCR (D-139). Its utilisation leg is paid only when the DNO calls, and D-1410 publishes "
     "the P90 utilisation rate as 0.00 to 0.02 against a P50 of 0.05 (NGED SAOU_DA) or 0.22 "
     "(UKPN SAOU). Its availability leg is a fixed fee for the award's term, but the ENA "
     "standard agreement lets either party end the award on 90 days' notice without cause "
     "(D-130), and the fee is priced gross with no sourced delivery ratio (D-116), so the "
     "cautious case does not count it either"
     ```

     It keeps the substrings the tests assert ("D-139", "utilisation"), starts lower-case and has no
     em dash (`tests/test_finance_in_term_contracted_p90.py:319-346`).
   - In the docstring of `_stream_contracted_haircut_pct` (`:1524`), "and is priced here as pure
     utilisation revenue" becomes "and is priced here as an availability fee plus utilisation
     revenue".
4. Tests, in `tests/test_finance_tenor_threading.py`:

   | # | Test | Asserts | Mutation it must catch (in memory, plan §6 plugin) |
   |---|---|---|---|
   | T10 | `test_site_award_tenor_reaches_finance_from_generate_quote` | As plan §3.4. Add: `quote.warnings` holds the in-term disclosure (a warning containing `"Contracted stream LOCAL_FLEX_PICLO earns £"` and `"ZERO for the whole award term"`), and no warning contains `"prices no CONTRACTED revenue"` | M-T10: in `gridq.finance.financial_model`, `commitments = [*(site.local_flex_awards or ()), *(commitments or ())]` -> `commitments = list(commitments or ())`. Red |
   | T11 | `test_sizing_finance_runner_sees_the_award_tenor` | As plan §3.4. Add: the recording runner asserts the sizer passed no `commitments` keyword, so the tenor can only have come from the site | M-T10 again. Red |
   | T12 | `test_one_year_award_leaves_the_cautious_and_lender_cases_in_every_year` (replaces the plan's T12) | See below | M-T12a and M-T12b below. Red under each |

   T12 runs at `run_financial_model` level with the module's own helpers: `_MIXED` (local flex
   £15,000 CONTRACTED), `_run`, `_site`, `_commitment`, release `gb_v1_6`. Two arms: `base =
   _run()`, and `awarded = _run(site=<_site() with local_flex_awards=[_commitment(LOCAL_FLEX_PICLO,
   1.0)]>)`, with no `commitments=` and no `contract_tenor_years=`. With `haircut` and `fade` read
   from the library the way `tests/test_finance_in_term_contracted_p90.py` reads them:
   - `awarded.cashflows.contracted_tenor_years` contains `("LOCAL_FLEX_PICLO", 1.0)`;
   - vacuity guard: `base.cashflows.annual_revenue_by_stream_gbp["LOCAL_FLEX_PICLO"] ==
     approx(15_000)`;
   - for every year `y` in 1..15, `base.p90_ebitda_gbp[y-1] - awarded.p90_ebitda_gbp[y-1] ==
     approx(15_000 x (1 - fade/100)^(y-1) x (1 - haircut/100), abs=0.05)`, and the same for
     `p90_contracted_ebitda_gbp`. Year 1 is asserted on its own line with the message "the
     contracted year is not counted either (D-139, owner decision D66)";
   - `sum(awarded.p50_ebitda_gbp) == approx(sum(base.p50_ebitda_gbp), abs=0.05)`, and nothing else
     about P50;
   - the in-term disclosure is in `awarded.case.warnings`.

   Mutations: **M-T12a**, in `gridq.finance.cashflows`, `return 100.0 if stream in _NO_P90_IN_TERM
   else contracted_pct` -> `return contracted_pct` (this is option B's arithmetic; the year-1 line
   goes red). **M-T12b**, `if k not in _NO_P90_AFTER_EXPIRY` -> `if True` (years 2 to 15 go red).
   The existing module tests stay green and unedited.
5. Report under D57, before the commit: the before and after P90 and lender series for each of the
   nine sites in `examples/real/sites/`. The fixed-spec method in
   `scratchpad/d130/tanhouse.py` (re-price the captured result with the awards as commitments) is
   sufficient and avoids nine 13-minute runs. Tanhouse's selected spec did not move (24.318 kW /
   12.159 kWh before and after, measured by the executor). If any site's selection moves, report it.
6. `docs/decisions.md`, above D64, in house style:
   - **D65**: the first question as put, the verbatim answer "Keep faded renewal", the date, and
     "population 3 in `cashflows.py` unchanged by design". Add one sentence: "The option text also
     said P90 and the lender case count only the contracted year. That was inaccurate, and it was
     corrected and re-decided in D66."
   - **D66**: the §1.4 question, the owner's verbatim answer, the Tanhouse figures, and the nine-site
     table from step 5.
7. Commit D-130, the text fix, T10 to T12, D65 and D66 together. Acceptance item 3 is reported as
   "met: the tenor reaches finance from every call path; P90 and the lender series exclude the
   award in every year (D66); P50 keeps faded re-contracting (D65)".
8. Triage row `docs/reviews/2026-11-16-findings-register-triage.md:70`: D-130 closed, citing
   `financial_model.py` (the line) and T10/T11 as the tests that fail if it is deleted.

**If the owner answers B:** stop and return to the advisor for a short addendum. The outline: remove
`LOCAL_FLEX_PICLO` from `_NO_P90_IN_TERM` and its reason; restate the three local-flex tests in
`tests/test_finance_in_term_contracted_p90.py` and
`test_p90_is_structurally_zero_for_this_stream_even_inside_the_award_term`; reword the "P90 AND THE
LENDER-BASIS DSCR DO NOT MOVE" disclosure and its assertion; T12 asserts year 1 equal to the base
arm and years 2 to 15 lower; D66 records that D54's "exactly zero" consequence is reversed. DFS
stays in the set.

**If the owner answers C:** revert the uncommitted line, leave D-130 open in the triage with the
owner's answer cited, and record D66.

### 1.6 Rejected

- **Committing option (a) on today's answer.** The owner approved a description, and the
  description was wrong. D57 needs an informed approval for a figure that falls.
- **Option (b) on the advisor's authority.** It matches what the owner was told, but it reverses
  D-139's operative words and D54's record, and it would make the published "do not move" sentence
  false by design.
- **Leaving the reason text for later.** It would print a false derivation on every awarded quote
  from the commit that makes it visible.
- **A per-leg split in finance.** Right in principle, but it needs the availability and utilisation
  £ to travel separately on `ValueStreamResult`, which is a frozen contract.

---

## 2. Q2: the demo golden pin `bill.peak_import_after_kw`

### 2.1 Decision

1. **Fix: a second solve that minimises the peak while holding the £ at its optimum, applied only
   to the solve that produces the customer bill** (the chosen-spec re-solve,
   `_dispatch_energy_for`, in `optimisation/engine.py` and `app/demo.py`). Sizing candidates keep
   the single solve.
2. **New pin: `bill.peak_import_after_kw` = 235.841.** No other golden pin moves (measured).
3. **Customer-facing change: the "peak import after" figure, and nothing else.** On the demo it
   goes from 278.407 kW to 235.841 kW. No £ moves. The figure can only fall or stay, on any site.
4. **The cross-platform claim can be made true for this figure**, because the minimum peak at the
   optimal £ is a single value fixed by the data, where today's figure is whichever optimal schedule
   the solver happened to return. A test holds it with an independent certificate (§2.4, T-P2).

### 2.2 Reason

**The cause is wider than the executor's summary, and it rules out both cheap fixes.** At the true
minimum (235.841 kW) the peak is a charging plateau: **2,540 of 17,520 half-hours sit exactly at
the peak** (measured, `derive.py`). The battery levels its night charging across them. The
tie-break's weight of 1e-6 is shared across every half-hour on the plateau, so the pressure on any
one of them is about 1e-6 / 2,540 = 4e-10. HiGHS's default dual tolerance is 1e-7. The second half
of that explanation is my inference from LP duality; the plateau count is measured.

| Candidate | Result here | Why rejected or chosen |
|---|---|---|
| Dual feasibility tolerance 1e-10 | 235.841 on macOS; fast tier 3,505 passed | Rejected. 1e-10 is HiGHS's floor and is only about 4 times below the 4e-10 above. A site with a longer plateau fails again, silently. It cannot be checked on Linux from here |
| Raise `_EPS_PEAK` to 1e-4 | 235.841 on macOS | Rejected. Per half-hour that is 4e-8, still under the default tolerance, so it worked for reasons the arithmetic does not guarantee. It also makes the tie-break a price: the solver may give up real £ (up to 1e-4 x the kW shaved) to cut the peak, which breaks the comment's promise that it "never moves a reported £" |
| Two passes on every solve | Fast tier with it on every LP solve: 4 failed, 3,501 passed; second-pass time 670 s against 472 s for the first passes; wall 492 s against 302 s baseline, on a loaded machine | Rejected. About 2.4 times the solver time on every sizing candidate, and it breaks measured zeros (see below) |
| **Two passes on the bill-producing solve only** | Full demo quote: one pin moves, +12.2 s on a 321 s run | **Chosen** |

In the second pass the peak is the whole objective, so the pressure per plateau half-hour is at
least 1/17,520 = 5.7e-5, more than 500 times the default tolerance at the worst case. It does not
depend on the plateau's length. It is also the design the golden module's docstring already names
as the owner's filed follow-up ("lexicographic two-pass").

**Two things the second pass must not do, both measured.**

- *It must not move the £.* With the £ held to within 1e-6 of its optimum, the chosen-spec solve
  gives the same saving (£34,650.07), the same bill, the same eleven stream figures and the same
  annual charge and discharge to 3 dp. Loosening the hold to £0.01 moves the saving by a penny and
  the peak by 0.26 kW, so the hold is 1e-6 and a test guards it.
- *It must not disturb a measured zero.* Run on every solve, the second pass leaked about 0.001
  kWh a year of dispatch into schedules whose first-pass answer was exactly zero, and four fast-tier
  tests that assert a measured zero went red
  (`test_deg01_the_cycle_cost_stops_a_trade_whose_own_wear_erases_its_margin`,
  `test_zero_dispatch_note_states_the_decision_and_its_arithmetic`, and two in
  `test_no_spec_dispatch_evidence.py`). The second schedule is therefore adopted only when it lowers
  the peak by more than 0.001 kW, the resolution the bill reports. Otherwise the first schedule is
  kept untouched.

**Cost on the sizing grid: none.** The sizing loop does not set the flag, so every candidate solve
is byte-identical to today. The cost is one extra solve per quote: 12.2 s on the demo (first pass
6.7 s). In the fast tier the upper bound is the every-solve experiment above; the real cost is the
few tests that run a whole quote on a 7-day fixture. The fast tier ran 485 LP solves in about 300 s
wall here under heavy load from other sessions (104 to 119 s unloaded, per the executor's log), so
the 10-minute bound is not at risk. The golden job adds about 25 s (two demo runs) against a
25-minute timeout.

### 2.3 The new pin and how it is derived

`bill.peak_import_after_kw`: 278.407 -> **235.841**. Four independent solver routes agree on it:

| Route | Peak, kW |
|---|---|
| Second pass (minimise peak, £ held to 1e-6), interior point + crossover | 235.84122 to 235.84125 |
| Single pass, dual feasibility tolerance 1e-10 | 235.841364 |
| Single pass, `_EPS_PEAK` 1e-4 and 1e-3 (executor) | 235.841364 |
| Single pass, primal simplex (executor) | 235.841364 |

The second pass lands about 0.0001 kW under the exact vertex value because the £ hold has 1e-6 of
slack. Both round to 235.841, and the pin's tolerance is 0.005 kW. The pin is accepted only when
(a) the certificate test T-P2 passes and (b) the Linux golden job passes on the same commit. If
Linux reports anything other than 235.841 within 0.005, STOP and report the value: that would
falsify this section.

`bill.peak_import_before_kw` stays 248.45. After the fix the demo battery lowers the site's peak by
12.6 kW. Today's figure says it raises the peak by 30 kW.

### 2.4 Exact instructions for the executor

Do this as its own commit, after the D-130 question is with the owner. `milp_solver.py` is a
collision hotspot: one block, and the coordinator confirms no other agent holds the file.

**Code**

1. `src/gridq/dispatch/contract.py`, `DispatchProblem` (`:54`): add a last field
   `minimise_published_peak: bool = False`, with the comment "Set only on the solve whose schedule
   is published as the customer bill. The solver then re-solves once to return the minimum-peak
   schedule among the £-optimal ones. Never set in the sizing loop."
2. `src/gridq/optimisation/milp_solver.py`: add beside `_EPS_PEAK`
   - `_PEAK_PASS_ECON_SLACK_GBP = 1e-6`
   - `_PEAK_PASS_MIN_GAIN_KW = 1e-3`

   and correct the `_EPS_PEAK` comment: at this scale the tie-break is not enforced under HiGHS's
   default tolerances on a full year (2,540-half-hour plateau on the demo), and the published peak
   is made exact by the second pass, not by this term.
3. Same file, in `_solve_milp`, directly after the `status[1] != "optimal"` return and the
   `binary_solve` gap block, and before `charge_sol` is read (about `:2106`). The second pass runs
   only when **all** hold: `problem.minimise_published_peak`; not `binary_solve`; the zero-weight
   LP branch built `peak_import`; `capacity_price_gbp_per_kw_year is None`. Steps:
   - copy the first-pass solution arrays (`charge`, `discharge`, `soe`, `export` if present) and
     `peak_1 = float(peak_import.solution)`;
   - `econ_expr = <the objective expression built above> + peak_coefficient * peak_import` (the
     objective with the peak term removed; the `_EPS` and `_EPS_AGE` terms stay inside it);
   - `econ_star = float(model.objective.value) + peak_coefficient * peak_1`;
   - `model.add_constraints(econ_expr >= econ_star - _PEAK_PASS_ECON_SLACK_GBP,
     name="peak_pass_econ_floor")`;
   - `model.add_objective(1.0 * peak_import, overwrite=True, sense="min")`;
   - `model.solve(**solve_kwargs)` with the same options;
   - adopt the second solution only if it is optimal **and**
     `peak_1 - float(peak_import.solution) > _PEAK_PASS_MIN_GAIN_KW`. Otherwise use the copied
     first-pass arrays, bit for bit;
   - if the second solve is not optimal, append a warning ("the peak-minimising second solve did
     not reach an optimum; the reported peak is the first solve's and may not be the minimum") and
     keep the first pass. Never fail the run on it.

   Keep the three constants and the adopt test on their own lines so each mutation below has a
   unique `MUT_OLD`.
4. `src/gridq/optimisation/engine.py`, `_dispatch_energy_for` (`:1332`), and
   `src/gridq/app/demo.py`, `_dispatch_energy_for` (`:418`): pass
   `minimise_published_peak=True` in the `DispatchProblem(...)` call. No other `DispatchProblem`
   call site changes (there are eight in `src/gridq`).
5. `_StudyBasisDispatchSolver` (`engine.py:353`) forwards `problem` to `super()._solve_milp`, so the
   flag rides through. Confirm by reading; no edit expected.

**Tests**

| # | Test | Asserts | Mutation it must catch |
|---|---|---|---|
| T-P1 | `tests/test_determinism_golden.py`: re-pin `bill.peak_import_after_kw` to 235.841. No other pin edited. Add a dated comment with the §2.3 table. Rewrite the docstring sentence that says the `_EPS_PEAK`-pinned peak is platform-stable: it is stable because the bill-producing solve returns the minimum peak at the optimal £, held by T-P2 | all pins equal | M-P1: in `gridq.app.demo`, `minimise_published_peak=True` -> `minimise_published_peak=False`. Red on macOS (278.407) |
| T-P2 | same module, `test_demo_published_peak_is_the_minimum_at_the_optimal_saving` | The certificate. Rebuild the chosen-spec problem (157.94 kW x 4 h, as `scratchpad/ci_peak/probe.py` does), solve it once with the flag off for `saving_1`, then a third solve with `peak_import`'s upper bound set to `pinned_peak - 0.05` via a `linopy.Model.solve` wrapper. Assert the capped solve's `annual_saving_gbp` is **lower** than `saving_1` by at least £0.01, or the solve is infeasible. Also assert the flagged solve's saving equals `saving_1` to the penny and its peak is `<=` the unflagged peak | M-P2: `_PEAK_PASS_MIN_GAIN_KW = 1e-3` -> `_PEAK_PASS_MIN_GAIN_KW = 1e9` (second pass never adopted). T-P1 and T-P2 red. M-P3: `_PEAK_PASS_ECON_SLACK_GBP = 1e-6` -> `_PEAK_PASS_ECON_SLACK_GBP = 1.0`. The saving-equality line goes red |
| T-P3 | `tests/test_optimisation_milp_solver.py`, `test_peak_pass_leaves_a_measured_zero_untouched` | On the zero-dispatch fixture of `tests/test_valuation_zero_dispatch_note.py` (gb_v1_9, 100 kW / 2 h), solve with `minimise_published_peak=True` and with it off: `charge_kw`, `discharge_kw` and `soe_kwh` are `np.array_equal`, and `annual_discharge_outside_dfs_kwh == 0.0` | M-P4: `> _PEAK_PASS_MIN_GAIN_KW` -> `> -1.0` (always adopt). Red (measured: this is the leak that failed four tests) |
| T-P4 | `tests/test_d61_reservation_reaches_every_resolve.py` or beside it | With `MilpDispatchSolver.solve` wrapped to record `problem.minimise_published_peak`, one `generate_quote` on the module's synthetic site: the flag is `True` on the `_dispatch_energy_for` solve and `False` on every sizing solve; the same for `DemoEngine` by direct call | M-P5: in `gridq.optimisation.engine`, the new `minimise_published_peak=True` -> `False`. Red |

The 0.05 kW cap in T-P2 costs about £0.002 on the measured slope (26 kW per £), so the executor
measures the capped saving first. If the drop is under a penny, widen the cap to 1 kW (expected
drop about £0.04) and record the measured figure in the test comment. Do not weaken the assertion
to make it pass. #12 (`test_g9_peak_tiebreak_pins_levelled_red_band_discharge`) and M12 are
untouched: the first pass is unchanged.

**Checks, in order**

1. `PT tests/test_optimisation_milp_solver.py tests/test_valuation_zero_dispatch_note.py
   tests/test_no_spec_dispatch_evidence.py tests/test_milp_dispatch_physics.py`: green.
2. `.github/ci/run_tests.sh module tests/test_determinism_golden.py`: green with exactly one pin
   edited.
3. Fast tier: zero failures. Record the wall time beside the pre-change time on the same machine.
   If it grows by more than 30 s, STOP: the flag has reached a sizing solve.
4. Each mutation M-P1 to M-P5 red, then green without the plugin, then `git status --short`.
5. Real sites: for each of the nine, record `peak_import_after_kw` before and after on the selected
   spec. Every value must fall or stay, and no £ may move. A peak that rises, or any £ that moves,
   is a STOP.
6. Push and confirm the Linux golden job reports 235.841. Until it does, the pin is provisional.
7. `docs/decisions.md`: D67 (or the next free number) recording the fix, the pin, the direction
   and the owner's acknowledgement (§2.5). Changelog entry stating the direction of effect.

### 2.5 D57 and the owner

A published figure falls: "peak import after" goes from 278.407 to 235.841 kW on the demo, and can
fall on every real-site quote. It is not a revenue figure, lower is better for the customer, and no
£ moves. D57's purpose (no constraint may quietly reduce value) is not engaged, but its letter is:
a figure falls. The coordinator tells the owner before the pin is re-minted. This is a notice, not
a design choice:

> The "peak import after the battery" figure in proposals was not reliable: it was whichever of
> several equally profitable schedules the solver happened to pick, and it differed between Mac and
> Linux (278.4 against 260.2 kW on the demo). The fix always reports the lowest peak available at
> the same saving. On the demo it becomes 235.8 kW, which is below the 248.5 kW peak without the
> battery. No £ figure changes. On real sites the figure can only fall or stay the same. OK to
> re-mint?

### 2.6 Rejected, and limits to state

- **Tighten the dual tolerance; raise `_EPS_PEAK`; two passes on every solve.** See the table in
  §2.2.
- **Widening the pin's tolerance or dropping the pin.** 278 against 260 against 235 kW is a real
  difference in a number customers read.
- **Fixing the whole dispatch placement.** This makes one published number unique. The half-hourly
  placement and the sizing surface remain solver-chosen and unpinned, as the golden docstring says.
- **Limit 1.** The second pass does not run on a binary (MILP) solve or on the MPC path. A bill
  from such a solve keeps today's behaviour. By the comment at `engine.py:1237`,
  `_dispatch_energy_for` re-dispatches on the plain no-export LP, so no production bill should come
  from one. The executor confirms this on the study-basis engine in check 5.
- **Limit 2.** Sizing candidates are not re-solved, so the "FLOOR dispatch leaves X kW" sentence
  in the agreed-capacity overlay note (`milp_solver.py:1041`) can still quote a first-pass peak that
  differs from the bill's. It prices nothing: the overlay's £ comes from its own solve, where the
  peak carries a real price. The executor checks whether that note reaches a published quote for
  the selected spec and reports it; do not fix it in this commit.
- **Limit 3.** Linux was not available here. The claim that the second pass gives 235.841 on x86
  rests on the argument in §2.2 and is proved or falsified by check 6.

## 3. Owner answers (2026-11-23)

Both questions were put to the owner as written in §1.4 and §2.5, the same day.
- **Q1 (D-130):** the owner chose "A. Count none (Recommended)". The in-term award is absent from P90 and the
  lender series in every year; `_NO_P90_IN_TERM` is unchanged. Proceed with §1.5.
- **Q2 (peak pin):** the owner answered "OK to re-mint". Proceed with §2.4; the pin stays provisional until the
  Linux golden job reports 235.841.
