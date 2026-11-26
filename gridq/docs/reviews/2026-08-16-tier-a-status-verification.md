# Tier A status re-verification — what is actually left

**Supersedes the status columns of [`2026-08-12-findings-register.md`](2026-08-12-findings-register.md).**
That register's *structure* (the numbering-scheme hazard, the collision map, the KILLED list) stands
and is not restated here. Only the **statuses** are re-derived.

| | |
|---|---|
| Verified against | `agent/revenue-data-drive` @ **`299e535`**, 2026-08-16 |
| Register verified against | `a0b3e07` — **35 commits earlier** |
| Method | direct code predicate per item, from the literal signatures the register quotes |
| Mutation testing | **NOT RUN — this task was read-only.** See "What this document does not prove" |

> **The tree was dirty while this ran, and that is load-bearing.** 18 files under `src/`,
> `scripts/` and `data/` carry uncommitted edits from concurrent agents, including six of the
> register's own collision hotspots (`milp_solver.py`, `financial_model.py`, `debt.py`,
> `two_stage.py`, `tariff_calendar.py`, `reconciliation.py`, plus `hh_mandate.py` and
> `hh_synthesis.py`). Two consequences, both recorded per item below: (1) line numbers drifted
> *during* this audit — `financial_model.py` moved a definition between two reads twenty minutes
> apart — so **verify by signature, never by the line numbers in this file**; (2) one item is
> fixed in the working tree and **still open at `HEAD`**, which is a distinct status and is
> called out as such.
>
> **The drift was measured, not assumed.** `finance/financial_model.py`'s `build_financial_case`
> was reported at `:258` by the audit tool, read at `:287`, and sat at `:284` on the final check —
> three positions inside one session. `_blended_import_rate` moved `:286` to `:312` over the same
> span. A concurrent agent is writing that file **now**. Every line number below is a snapshot.

---

## 0. Headline

**The register is materially out of date, and most corrections run the same way: more is fixed than
it records.** Six of its status rows move to FIXED, including its own flagship `DEG-01` and the
`MD-F-05` limb it called "open behind a FIXED claim". None of the items it calls fixed turned out to
be unreachable.

**One correction runs the other way, and it is mine.** I marked the `engine.py` bill-basis claim
FIXED on a grep that returned zero hits, then found the sentence intact and merely line-wrapped
(§2.1a). It is OPEN. I have left the reversal in place rather than quietly editing the row, because
the mechanism — *absence of a literal is the weakest evidence of a fix, and worthless when the
defect class is a false sentence* — is the most reusable thing in this document.

**The honest counter-finding is not in the register at all.** A cluster of run-publication code —
`publish_run`, `render_publishable_pack`, `is_persisted`, `PublishedRun`, plus the D46 study basis
end to end — is built, carries **40+ tests**, and is called from **no shipped entry point**. That
is this repo's signature failure mode, and it is the largest genuinely-open item found.

---

## 1. Counts

Presented as **verified moves**, not as a fresh audit of all 66 filed ids. I evaluated a code
predicate for roughly 25 items: every row in the register's §2 (claimed-landed-actually-open), every
row in §3 (PARTIAL), its §5 headline, its §6 contradictions, and the two items its own update block
newly filed. **I did not re-verify the 27-item §1 "already fixed" list** beyond the spot checks the
audit tool forced; those rows still rest on the register's clause-(a)-only verification.

| | Register (2026-08-12) | Verified (2026-08-16) | Move |
|---|---|---|---|
| **FIXED** (filed ids) | 27 | **33** | +6 confirmed |
| **FIXED, uncommitted only** | — | **1** | N9 route 2, working tree only |
| **PARTIAL** | 8 | **6** | −2 |
| **OPEN** (filed ids) | 66 | **~60** | −6 |
| **OPEN** (distinct defects) | 62 | **~56** | −6 |
| **Newly filed here** | — | **+4** | see §4 |
| **Reversed by me** | — | **1** | `engine.py` bill basis, §2.1a |
| KILLED / RETIRED | 7 / 1 | unchanged | — |

The `~` is deliberate. The 60/56 figures carry forward the register's own arithmetic for the rows I
did not re-derive. A number I did not measure is not presented as one I did.

**Read the named items, not the aggregate.** The +6 mixes register-filed ids (`DEG-01`, `N26`,
`MD-F-05`(b)) with contradictions (`C-1`, `C-7`) and one post-register filing (`asc_kw`), so the
27→33 arithmetic is defensible but not the point. The item-by-item delta in §2 is the deliverable.

---

## 2. THE DELTA, item by item

### 2.1 Moved to FIXED (6 confirmed)

| Item | Register said | Verified now | Direction of effect |
|---|---|---|---|
| **`DEG-01`** | OPEN, §5 headline, "no degradation cost in the MILP objective" | **FIXED** | **Worsens returns** |
| **`N26`** | OPEN, §2 "claimed landed, actually open" | **FIXED** | **Worsens returns** |
| **`MD-F-05` limb (b)** | PARTIAL / C-10 "open behind a FIXED claim" | **FIXED, both paths** | **Worsens returns** (when export is live) |
| **`C-7`** (N1/N13/N14 warning subsets) | OPEN, "silently dropped from WP-1" | **FIXED** | None — labels only |
| **`C-1`** (CLAUDE.md §9 stale) | OPEN, "actively harmful" | **FIXED** | Neutral |
| **`asc_kw = mic_kw`** | filed 2026-08-16 as a live D45 violation | **FIXED** | Neutral |

**`engine.py` bill basis was in this table as FIXED and has been removed from it. It is OPEN. See
§2.1a — I filed a false FIXED and caught it, and the mechanism is worth more than the verdict.**

**Every direction of effect that moves a number moves it against the case.** No verified fix in this
delta improves returns. That is the cheap-to-check half of the direction rule and it passes.

---

**`DEG-01` — FIXED.** `optimisation/milp_solver.py`, signature
`deg_term = deg_cost * (export_flow * w_da * dt).sum()`, subtracted from `revenue_term` in the
**base** objective, so it applies in **both** weight branches (the zero-weight LP branch and the
aging branch). The register's complaint was precisely that aging terms applied "only when non-zero
weights are passed"; `ObjectiveWeights`' docstring now states the inverse — "a zero-weight call is
no longer a zero-wear objective".

The trap I checked for and did not find: `export_flow` looked like it might be gated-upside export
only, which would have made `deg_term` zero on every underwriting run and `DEG-01` latent rather
than fixed. It is not. `export_flow` is assigned `discharge` outright when no export variable
exists, and `discharge + export` when one does, with the invariant stated in-source
("`export_flow` is exactly `discharge` when no export variable exists"). Every kWh leaving the pack
is charged on both paths.

`deg_cost` comes from `degradation_cost_gbp_per_kwh(assumptions)`, which resolves
`degradation.marginal_cycle_cost_gbp_per_mwh_discharged` from the release and falls back to a
**sourced** C-34 derivation (`c_deg = C_repl / (N_EFC,EOL x eta_dis)`, £120k/MWh, 6,000 EFC,
eta_dis 0.94, ~£21.3/MWh) — a derivation, not a picked figure, so D45-clean.

*Direction:* subtracts a real £ from every discharged kWh. Break-even arbitrage spread rises from
~£11.4/MWh toward ~£32.7/MWh, so trades in that band flip from booked-profitable to refused.
**Strictly conservative.** The 2026-08-15 changelog corroborates independently: this is what made
the F-03 fixture go inert, because `gb_v0_1` carries no degradation key.

*What a reader-layer test asserts:* two runs of the same site on the same release, one with the
degradation key present and one absent, produce **different** `TARIFF_ARBITRAGE` £ in the pack —
and the with-degradation figure is the **lower** of the two. Asserting `deg_term` is in the
objective expression is the wrong layer; it passes while the coefficient is zero.

**`N26` — FIXED.** One shared helper, `finance/cashflows.py`, signature
`def blended_import_rate_gbp_per_kwh(site) -> float | None`, returning
`round(sum(rates.values()) / len(rates) + levies, 6)`. The `+ levies` is the fix. Both former
callers now delegate: the parasitic-load cost line (`cashflows.py`, `lines["parasitic_load"]`) and
the price counterfactual (`financial_model._blended_import_rate`, now an explicit "thin alias so
the two cannot drift apart again"). The register's exact signature,
`sum(import_rates.values())/len(import_rates)`, has **zero hits** in the tree.

*Direction:* the parasitic-load cost line rises by `levy_rate x aux_kwh` every year of the horizon.
**Worsens returns.** Correct — auxiliary draw is imported grid energy and attracts levies like any
other import.

*What a reader-layer test asserts:* for a site with a non-empty `levies` block, the pack's
parasitic-load cost line is strictly greater than `mean(import_rates) x aux_kwh`. A unit test on the
helper alone would have passed throughout the defect's life, since the defect was that one of two
callers used a different helper.

**`MD-F-05` limb (b) — FIXED on both paths, which is more than the register expected.** The engine
half: `optimisation/engine.py`, `MilpEngine._rebase_dispatch_floor_streams`, wired from the export
overlay assembly. The register's signature — the contracted export stream appended to the
**unchanged** no-export `value_streams` — is gone.

The half nobody had checked: **`app/demo.py` had the identical defect verbatim**, filed as "N3" by
the 2026-08-13 engine review (see §5 on that file's numbering). It now delegates to
`MilpEngine._rebase_dispatch_floor_streams`, with the reason stated in-source: "THE ONE netting
site, delegated to the engine's own implementation so the demo and the engine can never disagree
about where it is." That matters because `DemoEngine` is the real MILP behind `POST /api/run`.

The design has **exactly one** netting site and the two paths that deliberately do *not* net both
document why, naming each other: `wholesale_contracted_stream_result(cannibalised_floor_gbp=)` is
the mutually-exclusive alternative implementation and is deliberately never passed, because doing
both would deduct the same £ twice.

*Direction:* **reduces the priced floor** whenever export is enabled, by removing the physically
impossible "full no-export arbitrage + full export volume". Conservative. C-10 warned this went live
the moment export was lifted; it is now closed **before** that lift, which is the right order.

*What a reader-layer test asserts:* on a site with an export overlay, the pack's priced floor total
is **strictly less than** (no-export floor + gross export revenue), and the pack contains no stream
row still labelled "GROSS — NOT NETTED".

**`C-7` — FIXED (the warning subset; the substantive fixes remain Tier B).** `finance/cashflows.py`
now defines `PRE_TAX_CFADS_BASIS`, `FLAT_NOMINAL_UNESCALATED_BASIS` and `OMITTED_CAPITAL_FLOWS`
(terminal/residual value, decommissioning/site restoration, inverter and BoP replacement), plus
`DSCR_BASIS_P90_PRE_TAX` in `finance/debt.py`. The design note is the right one and worth quoting:
"These are LABELS, not numbers: none of them injects a £ or a % into any output... The substantive
fixes (a tax layer, an escalation index, a residual-value basis) are Tier B: they need a human input
that is not derivable from code or from GB market mechanics, and NONE of them is invented here."

That is textbook D45 — a declared ask that can never silently read as zero, rather than a picked
figure. **N1, N13/H-15 and N14 themselves stay OPEN as Tier B**; only C-7, the contradiction that
the *disclosure* was missing, is closed.

**Reader-layer flow, traced end to end** (I initially verified this at the module layer only, which
is the layer this document's own test-specs call wrong; a reviewer caught it):

`cashflows.py` builds the three `BASIS —` warnings on **every** model → `FinancialCase.warnings` →
**`quotes/assembly.py`, `warnings.extend(financial_case.warnings)`** → `QuoteRecord.warnings` →
`outputs/viewmodel.py`, `warnings = list(quote.warnings)` → `outputs/consulting_report.py` reads
`vm.warnings`. The labels reach the reader. **C-7 stands.**

> **But there is a real hole, and it is live on all eight published packs.** The labels ride on a
> `FinancialCase`. The 2026-08-15 changelog records `financial_case` as **`None` on all eight runs**
> ("Financial model skipped, no positive sizing to price"). `assembly.py` can only extend from a
> case that exists — so **on a no-spec run the pre-tax / un-escalated / omitted-capital-flows
> disclosures never reach the pack at all**. The disclosure is present exactly when there are
> numbers to qualify and absent when there are not, which is defensible; but no current pack
> carries it, so C-7 must not be treated as demonstrated by inspecting today's output. Not
> re-filed — it is the same shape as the ninth instance (a no-spec record carrying less than the
> priced one), which was fixed for the dispatch note and not for this.

*What a reader-layer test asserts:* a pack from a run **with** a priced financial case contains the
pre-tax basis string, the un-escalated basis string, and all three omitted capital flows by name.
Asserting the constants exist in `cashflows.py` is the wrong layer — that is exactly how a label
reaches a module and never a reader.

**`C-1` — FIXED.** `CLAUDE.md` §9 now reads "**MD-F-15 is fixed** (verified 2026-08-12): the CLI
resolves `latest_version()` from disk and warns loudly when a superseded release is chosen
explicitly. Do **not** pin `gb_v0_1` 'to be safe'". The harmful instruction is gone. The cosmetic
residue the register noted (`cli.py` docstrings still saying `gb_v0_1`) is acknowledged in
`CLAUDE.md` itself.

**`asc_kw = mic_kw` — FIXED.** `app/app.py` now carries an explicit negative:
"**ASC IS NOT FILLED HERE, DELIBERATELY.** This used to copy MIC into `asc_kw`..." with the
downstream consequence named (`report_charts.capacity_is_supplied` returns True the moment
`connection.asc_kw` is set). Filed 2026-08-16 as a live D45 violation on production `POST /api/run`;
closed same day.

---

### 2.1a A false FIXED I filed, caught, and reversed — read this one for the method, not the item

**`engine.py` hardcoded bill basis: I marked it FIXED. It is OPEN. My predicate was wrong.**

I grepped `src/` for the literal `"priced on its own tariff stack"` — the exact phrase the
2026-08-15 changelog used to file it — and got **zero hits**. I wrote FIXED.

The sentence is still there. It is **wrapped across a source line break**:

```python
"dispatch-invariant: the site's own metered half-hourly load priced on its own "
"tariff stack with no battery in it. Modelled lines: time-of-use energy (the "
```

A contiguous-literal grep cannot match a string the formatter split. The claim is inside a `basis=`
field and is **not conditioned on the site** — the only conditional in the whole string governs the
standing/capacity fixed lines. So for **Harwell**, whose tariff is taken by analogy from Ewelme and
was never compared to any bill, the pack asserts the load is "priced on its own tariff stack" and
that is false. A second instance of the same phrasing sits earlier in the file, on the with-spec
path.

**Three things this teaches, all of which apply to the rest of this document:**

1. **Absence of a literal is the weakest possible evidence of a fix**, and it is weakest exactly
   where the defect class is "a false sentence" — because a *reworded* false sentence also produces
   zero hits. The changelog documents this recurring in this repo the same week: "I fixed the
   sizer's reason... and **left the engine string that was the actual source**, so it came straight
   back."
2. **`ruff format` is an adversary to grep-based verification.** Any asserted string longer than the
   line limit is stored split. Verify prose claims by reading the region, never by matching the
   sentence.
3. It is the only verdict in this document I reversed, and I reversed it because a reviewer asked
   what *replaced* the removed string rather than whether it was gone. **That question should be
   asked of every "removed a false sentence" fix.**

*What a test asserts, at the reader's layer:* for a site whose tariff is borrowed by analogy, the
rendered pack does **not** contain the phrase "its own tariff stack", and does state the borrowed
source. Sites carrying their own evidenced tariff keep the sentence. A grep-for-absence test would
reproduce my error exactly.

---

### 2.2 Moved to FIXED **in the working tree only** (1) — OPEN at `HEAD`

**`N9` route 2.** The register: "`hh_mandate.py:108-110` still defaults an absent basis to `METERED`
with a HIGH ceiling."

- **At `HEAD` (`299e535`): still open.** `git show HEAD:src/gridq/ingestion/hh_mandate.py` contains
  `basis = HalfHourlyBasis.METERED`.
- **In the working tree: fixed.** An undeclared basis returns `basis=None` with a
  `DerivedAssumption` record and a MEDIUM ceiling, commented "Undeclared: admitted and priced, but
  never promoted to METERED (N9, route 2)". A *corrupt* basis string still raises, correctly
  distinguishing corrupt data from missing data.

`hh_mandate.py` is **+220 lines uncommitted**. This is another agent's fix, mid-flight. Do not
dispatch it. Re-verify after that agent commits.

**Route 1 is genuinely committed at `HEAD`**: `ingestion/hh_synthesis.py` stamps
`basis=HalfHourlyBasis.MODELLED`, present in both `HEAD` and the working tree.

*Direction:* caps the confidence band at MEDIUM for sites that previously reached HIGH on an
undeclared basis. **Worsens the stated evidential strength.** Correct.

---

### 2.3 Confirmed FIXED and genuinely reachable (re-verification, no move)

**`C-6` / Gate 3 — reachable, and a naming trap is worth recording.** `quotes/pipeline.py` calls
`reconcile_declared_against_measured(site, summary)`, and passes the result into `assess_confidence`
as **`reconciliation=`**.

The register's own text says the call was made "without `reconciles=`". The parameter was
**renamed** during the fix. **A grep for `reconciles=` in `src/` returns only `report_charts.py` and
will look like Gate 3 is still unwired.** I nearly filed it. Recording the trap so the next reviewer
does not.

The in-source comment states the failure and the fix mode: wired "in record-and-continue mode: a
breach caps the band and states the ask; it never blocks and never re-bases the run" — which is
Gate 3's rule in `validation_requirements.md` §1, correctly implemented as record-and-continue
rather than a hard gate.

---

### 2.4 Confirmed STILL OPEN (no move)

| Item | Predicate evaluated | Result |
|---|---|---|
| **`N27`** | `grep -c candidate_viable src/gridq/sizing/search.py` | **0** — unchanged |
| **`N29`** | `cloud.extend(confirmed)` in `two_stage.py` | present — unchanged |
| **`N3`(a)** | `annual_dispatch_cost_gbp` in `COST_LINE_KEYS` | **absent** |
| **`N3`(b)** | `TARGET_IRR_KEY = ("financing","target_equity_irr_pct")` | present — unchanged |
| **`N12`** | `pricing.py` "levelises as if volume were flat" | present — unchanged |
| **`C-11`** | `schedule.py` "each clamped at £0" | present — unchanged |
| **mypy/tests** | `pyproject.toml` `packages = ["gridq"]` | unchanged — `tests/` still uncovered |
| **`D-116` MUPF** | `local_flex_availability_value(utilisation_performance_ratio=)` | **corroborated by the audit tool** |
| **`MD-F-04` residue** | SoC-headroom / export-rights / eligibility predicate in `value_stack/` | **zero hits** |
| **`MD-F-08`** | inferred interval honoured downstream | recorded "for downstream coverage maths only" |

Detail on the three that are ranked in §3, plus one nuance:

**`N27` is partially mitigated in a way the register does not capture, and I am recording it as still
open anyway.** `_METRIC_RANK` tiers the objective metric (`METRIC_PRICED_FLOOR` 0 <
`METRIC_PROJECT_IRR` 1 < `METRIC_LEVERED_RETURN` 2) and `_rank_key` sorts on that tier first, so a
priceable point *does* already outrank an unpriceable one — the module docstring claims exactly
this. What has **not** landed is the register's specific ask: no `candidate_viable` field exists
anywhere in the file, and `best=max(scored, key=_rank_key)` (three occurrences) is still ungated on
viability, so the "best" point can be one no covenant passes. Half a fix, and the half that is
missing is the one that selects.

---

## 3. Ranked — what genuinely remains

Ranked by **live on the pricing path now** > **goes live on a known upcoming change** > **latent**.

### R1. The publishable-pack path is built, tested 40+ times, and reachable from no entry point

**NEW — not in the register. This is the repo's twelfth instance of its signature defect, or a
deliberate library-only API that has never been written down as one.**

| Symbol | Defined | Refs in `src/` outside its own module | Tests |
|---|---|---|---|
| `publish_run` | `quotes/persistence.py` | **0 calls** (4 docstring mentions) | 22 |
| `render_publishable_pack` | `outputs/consulting_report.py` | **0 calls** (3 docstring mentions) | 18 |
| `is_persisted` | `quotes/persistence.py` | **0** | 3 |
| `PublishedRun` | `quotes/persistence.py` | **0** | — |
| `generate_artifacts(require_persisted=)` | `outputs/generate.py` | **never passed by any caller** | — |

`cli.py`'s `render` command calls `generate_artifacts(quote, site)` — **without**
`require_persisted`. `app/app.py` calls `generate_artifacts(ctx["quote"], ctx["site"])` — likewise.
Neither goes near `publish_run`. So `persistence.py`'s own headline claim, "**A pack is rendered
from the file, not from memory.** `publish_run` writes the run's artifacts", is true of a path
**nothing ships**, while both shipped paths render from memory with the persistence guard switched
off by default.

**Why this is ranked first:** the provenance receipt is the artifact that makes a pack checkable by
a lender, and `docs/architecture` treats publication as the traceability boundary. A pack rendered
from memory without `require_persisted` cannot prove the record it came from was ever written.

**Framing caveat, stated because it changes the verdict:** this may be intentional — an operator
driving publication from Python during the validation campaign is a legitimate design. If so it
needs to be written down (`docs/dormant_code.toml` exists for exactly this and is currently empty)
and the eight published packs need to name which path produced them. If not, it is instance twelve.
**I could not resolve which from the code alone, and I am not guessing.**

*What a test asserts, at the reader's layer:* invoking the shipped CLI publication command produces
a directory containing a provenance receipt, and the same command **refuses** on an unpersisted run.
Today no such CLI command exists, which is the finding.

### R2. D46's study basis has a wired consumer and no producer

**NEW.** `StudyBasis` is **never instantiated anywhere in `src/`** — `grep "StudyBasis("` returns
only the class definition in `schemas/study.py`. `derive_flat_export_tariff`
(`optimisation/valuation.py`) has **zero callers** in `src/`. `cli.py` has **zero** hits for
"study".

The consuming half is fully wired: `MilpEngine.__init__` accepts `study_basis=`, refuses it
alongside `market_forecast` and `export_feasibility_override`, `study_basis_export_stream_result`
prices the export entry, and `html_report.py` / `proposal.py` / `viewmodel.py` render the basis
banner. The producing half does not exist on any shipped path.

**Direction of effect if this is wired later: study basis prices export INTO the priced floor, so it
RAISES the floor.** That is the direction requiring the most justification, and D46 already fences
it — explicit per run, never a default, enters `input_hash`, never presentable as an underwriting
output. Recording it here so that when a producer is built, the reviewer knows this is the
returns-improving direction and checks the fence rather than the arithmetic.

*What a test asserts:* running the shipped entry point with the study-basis flag produces a record
whose `input_hash` differs from the same site's underwriting run, and whose every artifact states
the basis. Today there is no flag to pass.

### R3. `N3`(a) — dispatch cost never reaches the price recommendation

`finance/cashflows.py`: `COST_LINE_KEYS` is `(opex, insurance, metering_idno, dno, business_rates,
levies_on_charge, parasitic_load)`. `annual_dispatch_cost_gbp` is a **separate field**, and
`total_annual_operating_cost_gbp` sums only `annual_cost_by_line_gbp`. `finance/financial_model.py`
passes `annual_opex = cashflows.total_annual_operating_cost_gbp` into the price recommendation.

So the price-to-target is computed against a cost base that excludes the dispatch's own cost.

*Direction if fixed: raises the required price / lowers the return.* **Worsens returns** —
conservative, low justification burden.

*What a test asserts:* for a site whose dispatch cost is non-zero, the pack's recommended price is
strictly higher than the same site's price with dispatch cost zeroed. A test on
`total_annual_operating_cost_gbp` alone is the wrong layer — the register's own text shows this
defect surviving a correct-looking module.

### R4. `N29` — proxy-mode candidate cloud double-counts

`optimisation/two_stage.py`: `if proxy_mode: cloud.extend(confirmed)` appends confirmed candidates
to a cloud that already contains their search-mode evaluations, then reports
`search_space_size=len(cloud)`. The non-proxy branch immediately below de-duplicates by
`(power_kw, duration_hours)` correctly. `proxy_mode` is `typical is not None`.

*Direction:* inflates a **reported count**, not a £. No priced number moves. Low severity, cheap
fix, and it makes every proxy-mode `search_space_size` in every existing pack overstated.

*What a test asserts:* a proxy-mode run's `search_space_size` equals the number of **distinct**
(power, duration) pairs evaluated. Two candidates confirmed means the count does not rise by two.

### R5. `N27` — the selected size is not gated on viability

`sizing/search.py`: no `candidate_viable` anywhere; `best=max(scored, key=_rank_key)` at three sites.
Partially mitigated by metric tiering (§2.4). What remains: `best` can be a point that fails every
covenant, and `ScoredPoint` carries no `priceable` flag.

*Direction:* unknown until measured — it can select a better-looking point than any viable one, so
the risk is **returns-improving**, which is the direction needing the most justification. Rank it
above R6 for that reason alone.

*What a test asserts:* on a site where no candidate passes the covenant, the pack's selected
specification is absent or explicitly labelled non-viable — never a size presented as chosen.

### R6. `MD-F-04` residue — no SoC-headroom reservation, export-rights check or eligibility predicate

`grep` across `value_stack/` for `soc_headroom|soe_headroom|headroom_reservation|export_rights|
eligibility_predicate` returns **zero hits**. The closed limb (power + energy budget, no double-sold
MW) remains closed. Goes live with multi-service stacking.

### R7. `D-116` MUPF clawback — latent, corroborated mechanically

The register filed this on 2026-08-15 with the severity already corrected to latent. **The audit
tool independently reproduces it**: check B flags
`value_stack.gb_streams local_flex_availability_value(utilisation_performance_ratio=)` as supplied
by no production call site. Both callers pass `contracted_mw=None`, so the £0-with-a-data-ask branch
returns first and the taper is never reached — correct D30 behaviour today. Carry, do not re-file.

Related and worth pairing when it is fixed: `payable_delivery_fraction(grace=, multiplier=,
payable_over_delivery=)` is called positionally at both sites, so its ENA parameters ride on
defaults. Defensible while the ratio is `None`; check it in the same change.

### R8. `engine.py` bill basis asserts "its own tariff stack" on a borrowed tariff

Full detail in §2.1a. Live on the pricing path's **reader** rather than its arithmetic: no £ is
wrong, but the pack tells a lender the delivered cost was priced on the site's own tariff when for
Harwell it was priced on Ewelme's by analogy. Two occurrences in `optimisation/engine.py` (the
no-spec `basis=` string and the with-spec path), neither conditioned on the site.

Ranked here rather than higher because it moves no number; ranked above the cosmetic tail because
it is a **false statement to a reader**, which is the defect class this drive has spent eleven
instances on.

### R9. `N12`, `N3`(b), `C-11`, mypy-over-`tests/`, `MD-F-08`

Unchanged from the register, all correctly described there, none worth re-deriving here. `C-11` is
one stale docstring sentence (`schedule.py`, "each clamped at £0") and is the cheapest item on this
list — it is the exact sentence a future reviewer will re-raise `MD-F-02` from
(`src/gridq/optimisation/schedule.py`, verified present).

### Unresolved, ranked nowhere: `eac_response_value(toll_reference=)`

Two production callers (`value_stack/gb_streams.py`, `optimisation/valuation.py`), neither supplies
`toll_reference`. I did not determine whether that default is deliberate. Flagging rather than
ranking it, because I did not verify it.

---

## 4. Newly filed

1. **The publishable-pack path is unreachable** (R1) — or is a library-only API that has never been
   declared as one.
2. **D46's study basis has no producer** (R2).
3. **`dfs_performance_factor`** (`value_stack/gb_streams.py`) — **zero** references in `src/`, two in
   tests. Smallest member of the same family as R1.
4. **A FOURTH numbering scheme is in circulation** — see §5. This is the register's own §0 hazard,
   one scheme wider than the register knows.

---

## 5. THE HAZARD IS WORSE THAN §0 OF THE REGISTER RECORDS

The register warns about **three** colliding `F-` schemes and mandates `MD-F-` / `JSON-F-` / `LIT-F-`
prefixes. It also lists `N-nn` as unambiguous: "plain `N-nn` (WP-0)".

**`N-nn` is not unambiguous.** `docs/reviews/2026-08-12-engine-review-findings.txt` — filename dated
08-12, content dated **08-13**, i.e. written *after* the register — opens a `=== NEW DEFECTS ===`
section numbering its own findings **`N1` through `N7`**, colliding head-on with WP-0's `N1`–`N31`:

| id | WP-0 meaning | engine-review meaning |
|---|---|---|
| `N1` | corporation tax not modelled (Tier B) | **search box loses its bottom** |
| `N2` | (already fixed, §1) | **`app/demo.py` runs the deleted capacity rule** |
| `N3` | netting / dispatch cost / discount basis | **`app/demo.py` double-counts the export floor** |
| `N9` | modelled-basis stamping | — |

Dispatching "fix N3" against that file fixes a different defect from dispatching "fix N3" against
the register. **Two of those engine-review items are now fixed** (its N2 and N3 — `demo.py` mirrors
the engine's capacity basis and delegates its export netting), which is how I found the collision.

**Recommended addition to the mandatory convention:** `ER-N-nn` for
`2026-08-12-engine-review-findings.txt`, leaving plain `N-nn` to WP-0. Also worth noting: that file
records a verdict the register never absorbed — **`MD-F-05`(b) confirmed implemented on the engine
path on 2026-08-13**, two days before the register's own update block was written and did not say
so.

---

## 6. The call-path audit — measured, not quoted

`python -m gridq.dev.call_path_audit`. **Measured on this tree, 2026-08-16.**

| Baseline | Findings | Runtime |
|---|---|---|
| vs `HEAD` (default; uncommitted work only) | **4** | 1.48s |
| vs `a0b3e07` (**the register's own commit** — the full delta scope) | **35** | 1.47s |

The 35 split B **18** / A **5** / C **12** (C advisory, and by the module's own description "adds
context rather than new detections"). Suppressed and *not* shown: **1,742** pre-existing findings,
14 call targets with unresolvable arity, 20 parameters deduped into A/C.

**Measured false-positive rate.** I adjudicated all 23 non-advisory findings individually, counting
a finding false-positive when the source already carries a legitimate documented reason:

| Check | Findings | False positives | FP rate |
|---|---|---|---|
| **B** (unsupplied defaulted parameter) | 18 | **11** | **61%** |
| **A** (public symbol unreferenced in `src/`) | 5 | **0** | **0%** |
| **A + B combined** | 23 | **11** | **48%** |

**This is materially worse than the ~25% (6 of 24) its own footer advertises**, and the task was
right to ask for a measurement rather than a quotation. The footer's other claim holds exactly:
the false positives are **concentrated entirely in check B**.

The 11 check-B false positives, each with the reason already in the source: three
`report_charts` SVG styling defaults (`_notes(fill=)`, `_polygon(opacity=)`, `_rect(opacity=)`);
`wholesale_contracted_stream_result(cannibalised_floor_gbp=)`, which is the deliberately-not-passed
alternative implementation of `MD-F-05`(b) and is documented as such in two files;
`derive_half_hourly(profile=, year=)`, whose defaults are the reference profile and a fixed constant
that is explicitly "never the clock"; `build_financial_case(commitments=, contract_tenor_years=)`, a
public wrapper whose production path uses the inner builder; and `payable_delivery_fraction`'s three
ENA parameters, called positionally.

**Check A is the one that earned its keep.** Zero false positives, and it is what surfaced R1, R2 and
finding 3 — the largest genuinely-open item in this document. A reviewer short of time should read
check A and skim check B.

**The tool's own stated limit held exactly.** It proves a call path exists, never that a value
flows. It reported green on `DEG-01`, `N26` and `MD-F-05`(b), all three of which I verified by
reading the arithmetic; and it would have reported green on the `Gate 3` rename trap in §2.3.

---

## 7. What this document does not prove

**Mutation verification was not run, and the repo's FIXED bar is therefore only half met on the
six items in §2.1.** The task was read-only; more to the point, a break/restore cycle races the
concurrent agents holding `milp_solver.py`, `financial_model.py`, `two_stage.py`, `debt.py`,
`tariff_calendar.py`, `reconciliation.py`, `hh_mandate.py` and `hh_synthesis.py` open right now,
and a byte-identical restore cannot be guaranteed against a file another process is writing.

Every §2.1 row therefore carries this grade:

- **Clause (a) — production call site: MET.** Named per item, reachable from `cli.py`,
  `app/app.py` or `generate_quote` without test-only code.
- **Clause (b) — a test that fails if that call site is deleted: NOT VERIFIED.** For each item I
  have stated *what a test would assert and at which layer*; I have not run it, and I have not
  confirmed an existing test does it.

**So §2.1 should be read as "verified to the standard the register itself used before 2026-08-15",
not to the raised bar.** Closing clause (b) on those six is the natural next task, and it is cheap:
six assertions, all at the pack layer, all already specified above.

**§2.1a is the standing argument against relaxing this.** One of my seven original verdicts was
wrong, and it was wrong in the *optimistic* direction, from a grep that looked conclusive. Six
survived scrutiny; one did not. Assume a similar rate in any status list produced without mutation
verification, including this one.

The other three limits, stated plainly:

1. **I did not re-verify the register's 27-item §1 fixed list.** Those rows still rest on
   clause-(a)-only verification from 2026-08-12.
2. **`N9` route 2's status is a moving target** — fixed in an uncommitted working tree, open at
   `HEAD`.
3. **R1 and R2 are findings I could not fully adjudicate.** Both may be deliberate library-only
   APIs. I have said so rather than filing them as defects, and both are cheap to resolve: an entry
   in `docs/dormant_code.toml` (currently empty) settles either one in a line.
