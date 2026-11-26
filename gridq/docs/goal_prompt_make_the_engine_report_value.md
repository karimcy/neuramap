# /goal prompt — hand to Opus 5

Copy everything inside the fence. Do not run it from this document.

---

```
/goal ultracode

Make the GridQ underwriting engine report the value it actually measures. Right now it
measures revenue on every candidate and reports GBP 0.00 on every stream, and the packs
therefore tell a client their site earns nothing when the model found otherwise. Fix that
end to end, with the literature corrections already landed, and prove it at the layer a
reader reads.

Repo: /Users/oliverpugh/Desktop/Karim Project/Neura GitHub/uk, branch agent/revenue-data-drive.
Read CLAUDE.md first, then docs/validation_requirements.md section 3, then docs/changelog.md
from 2026-08-15 onward. Olly is the sole contributor; attribute everything to Olly and log a
HANDOFF_LOG entry before finishing.

=====================================================================
THE DEFECT, MEASURED. Do not re-derive this, but DO verify it before building on it.
=====================================================================

On Bishops Cleeve Ash Plant (runs/quote_run_223a0b29daae98ab/quote.json):

  sizing grid points ............ 170
  priceable ..................... 170  (every one solved and priced)
  with a project_irr_pct ........   0
  with a min_p90_dscr ...........   0
  passes_covenant ...............   0
  reported recommended_spec ..... None
  reported value_streams ........ every stream GBP 0.00

The candidates DISPATCH. An independent sweep measured 18,792 kWh/yr discharged here, and
best priced floors across the campaign of GBP 1,463.15 (BC Ash Plant), GBP 7,885.68 (BC MRF),
GBP 22,116.69 (Ewelme), GBP 15,361.71 (Harwell). None of it reaches the record.

THE CHAIN, in order:

1. No debt structure is resolved, so no P90 DSCR is computed for any candidate.
   Two independent causes, BOTH must be fixed:
     (a) debt_terms is threaded to the top-level finance call (quotes/pipeline.py) but
         appears NOWHERE in optimisation/engine.py, so _run_search calls the sizer without
         it even though two_stage.py and sizing/sizer.py both ACCEPT it.
     (b) financing.debt_gearing_pct is absent from EVERY assumption release, so
         build_debt_terms_from_credit returns None. Both roads to a DSCR are closed.
2. sizing/objective.py SizingObjective.feasible returns False when min_p90_dscr is None,
   so every candidate is infeasible.
3. No candidate is selected. The reported OptimisationResult carries no spec.
4. valuation.build_value_streams is then called with schedule=None, so it prices nothing
   and every stream reads GBP 0.00.
5. The sizing grid CANNOT rescue the figure: a grid point carries power_kw,
   duration_hours, energy_kwh, project_irr_pct, levered_return_pct, min_p90_dscr,
   passes_covenant, priceable, bankable, solver_status, stage, proxy,
   end_of_life_deliverable - and NO REVENUE FIELD AT ALL.

CLAUDE.md section 2 and docs/validation_requirements.md:211 forbid exactly this:
"missing credit/debt data means underwriting UNLEVERED with the band adjusted", and
"never return no viable configuration for a data reason of any kind". The current
behaviour is a data reason wearing an economic verdict.

Note what is ALREADY correct and must not be undone: sizing/sizer.py _select was fixed so
the REASON is now honest ("THE COVENANT GATE DID NOT RUN on any of the 27 candidates ...
nothing here is evidence that any size failed the covenant"). That fix stays. What is
missing is the unlevered fallback it points at.

=====================================================================
WHAT TO BUILD - four outcomes, in dependency order
=====================================================================

1. AN UNLEVERED SELECTION BASIS.
   When no debt structure is resolvable, the search must select on an unlevered basis and
   the confidence band must be capped accordingly, per CLAUDE.md section 2. Do NOT invent
   gearing to manufacture a DSCR - that is the single worst available outcome. Watch this
   trap: sizing_grid() stamps selection_basis="covenant" whenever `selected` is not None,
   so an unlevered pick made inside _select would MISLABEL itself as covenant-feasible.
   The right seam is believed to be engine._choose / candidate_viable, which already exists
   but needs a positive return metric. Establish that yourself.

2. PER-CANDIDATE REVENUE ON THE RECORD.
   Persist each candidate's priced floor and stream breakdown on the sizing grid, so a
   no-spec run can still show what the best candidate would have earned. This is what makes
   the difference between "this site earns nothing" and "the best 25 kW candidate earns
   GBP 1,463/yr and still does not cover opex". Both are negatives; only the second is true.
   Frozen-contract change: the SizingGrid schema is in CLAUDE.md section 7's frozen list, and
   it feeds input_hash. Flag it, version it, do not sneak it.

3. THE PACKS MUST SHOW IT.
   A GBP 0.00 row on a no-spec pack currently reads as "no value". Render the best
   candidate's measured figures, labelled as the best CANDIDATE and explicitly NOT a
   recommendation. The machinery to build on already exists: stream notes now reach the
   rendered bytes, and each GBP 0 row already states WHICH zero it is (decided, gated, or
   unmeasured). Extend that rather than replacing it.

4. RE-RUN AND RE-PUBLISH the eight-site campaign, then re-verify.
   Script: the scratchpad run_campaign.py pattern - eight runs (five metered Grundon sites,
   the two invoice-anchored variants, Harwell), release PINNED, D46 study basis stated, and
   it must FAIL if any two runs share an identity. Then refresh deliverables/.

=====================================================================
NON-NEGOTIABLE RULES - this codebase has earned every one of them
=====================================================================

NOTHING IS FIXED UNTIL IT IS REACHABLE. Name the production call site file:line, reachable
from cli.py / app/app.py / generate_quote with no test-only code, AND write the test that
FAILS if that call site is deleted. This repo has shipped the same built-tested-never-called
defect ELEVEN times and EVERY ONE HAD A PASSING TEST. Mutation-verify each fix: break it,
confirm red, restore byte-identically, check md5.

ASSERT AT THE LAYER A READER READS. Ten of the eleven were tested at the layer their code
lived in and passed while being unreachable. For anything that must appear in a pack, assert
on the RENDERED BYTES of a pack produced end to end by publish_run from a reloaded record.
Note runs/ is gitignored, so a test that loads a real run directory passes VACUOUSLY on a
fresh checkout - build the record in the test instead.

D45: no null on the pricing path, and never invent a number. Deriving a published GB value
(a DNO CDCM tariff, a NESO de-rating factor, an ENA formula, an OBR index, a statutory
instrument) is SOURCING. Picking a plausible figure is not. A declared ask that can never
silently read as zero is a legitimate outcome.

STATE THE DIRECTION OF EFFECT of every number that moves, per stream. This work moves
revenue UPWARD, which is the direction needing most scrutiny. A direction was assumed
backwards earlier in this project and an agent had to correct it.

NEVER use pytest.skip or xfail to make a failure disappear. A test that silently skipped
had checked ZERO of six sites while showing green, and an entire e2e module had never run on
any host because its gate named a file with no git history.

=====================================================================
ORCHESTRATION
=====================================================================

Use ultracode. Fan out on the four outcomes with disjoint file ownership, and put an
ADVERSARIAL VERIFIER behind each one, briefed to default to REFUTED and to attack on four
fronts: is the call site genuinely reachable (trace it, do not trust the report); would the
test fail if the fix were reverted; was any number invented and is the stated direction
right; did anything break. Verifiers have overruled fixers repeatedly here and been right
every time.

Then run a FINAL REVIEW with three DIVERSE LENSES rather than three identical reviewers -
the client's eyes, the numbers traced to source, and test/reachability integrity - followed
by a COMPLETENESS CRITIC whose only job is finding what all three missed. That structure
matters: three lenses reviewed eight packs and all three globbed *_report.html, while 22
contradicting client decks sat in the same folder unexamined. The critic found them. Ask it
explicitly: what else is in the folder that would be sent, and what else does one publish
call write.

=====================================================================
STATE YOU ARE INHERITING
=====================================================================

Suite 203/203 files green, zero vacuous. Release gb_v1_10 (its CM de-rating curve is the
published CONCAVE one; the earlier table was linearised). Eight packs in deliverables/ on
real metered half-hourly data, em/en dash zero, Gate 3b firing on all eight. 22 archetype-era
deliverables retired to superseded_2026-08-17_pre_metered_archetype_era/ with a DO_NOT_SEND.md.
MPC is QUARANTINED and use_mpc=True raises - do not un-quarantine it.

Findings that must survive this work, because they were expensive to establish:
  - Four of five Grundon sites are STRUCTURALLY too small: a 25 kW battery at BC Ash Plant
    captures 100% of available red-band energy, so anything larger is dead capital by
    construction. Measured from their own meters.
  - Tanhouse and Colnbrook DECLINE TO CYCLE AT ANY SIZE: a round trip needs 4.788 p/kWh and
    they offer 3.525 p and 3.086 p. The margin has no size term. That is a TARIFF-STRUCTURE
    finding, not an economics one, and Tanhouse's verdict survives even at zero degradation
    cost (best saving ~GBP 8,110/yr against GBP 37,476/yr opex).
  - Knowl Hill is 92.1% self-supplied by landfill gas; a battery there firms its OWN
    generation rather than avoiding import.
  - Ewelme's metered pack covers ONE of two MPANs; the other is a 750 kVA workshop carrying
    82% of site energy.
  - Harwell's tariff is taken BY ANALOGY from Ewelme and has never met an invoice.

If the unlevered basis changes any of those conclusions, that is a MAJOR result and must be
reported loudly rather than folded in quietly.

TWO ITEMS THAT ARE NOT YOURS. GOLDEN_QUOTE_HASH: the exact-hash assertion was removed
because the demo LP is degenerate by construction (all ten charging half-hours sit at an
identical price with slack, so placement is a free vertex choice at whole-kW scale) and a
cross-platform exact hash is therefore impossible, not merely inconvenient. Twenty-eight
value pins replaced it. Do not reinstate the hash. And the BM/ancillary volume basis: the
owner has decided to KEEP the current CM-parity basis for now, with a guard that refuses at
the first site of 1 MW or more. Honour that decision; do not change the basis.
```
