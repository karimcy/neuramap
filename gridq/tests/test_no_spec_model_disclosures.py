"""The dispatch model's own disclosures reach a record that recommends no system.

**The defect these tests pin.** `MilpDispatchSolver._solve_milp` states, on every solve, what the
half-hourly model did and did not represent: the usable SoC window it applied, the unity power
factor it assumed, the marginal cycle cost it charged in the objective (DEG-01), the parasitic
draw it took off the charge headroom, the warranty point it extrapolated from, the availability
commitments it did not compile, and the tariff lines that were ABSENT rather than zero. On a
SOLVED run all of them reach the reported result (`sizing.sizer._to_optimisation_result` copies
``DispatchResult.warnings`` onto the candidate, `MilpEngine._final_result` carries the chosen
candidate's). A NO-SPEC record reports no candidate, so it carried none of them — and all six live
campaign sites return ``no_viable_configuration``, so the disclosures were missing from 100% of
real runs. The verdict "no candidate is economically viable" is a claim about a priced floor, and
a reader cannot weigh that floor without the physics basis it was priced on (CLAUDE.md §5
traceability; `docs/validation_requirements.md` on declaring rather than silently assuming).

Commit 80e12f1 closed the tariff-absence limb by RE-DERIVING the D45 lines beside the before-only
bill. This closes the rest by CARRYING the solver's own statements.

Written to `docs/validation_requirements.md` §3, because "built, tested, called from nowhere" has
shipped **eight** times here and every one had a passing test. Row 5 of that table
("Physics disclosures | asserted on a type production never returns") is this defect's own prior
instance: `tests/test_optimisation_milp_solver.py` and `tests/test_milp_dispatch_physics.py`
assert the disclosures on `DispatchResult` (the INNER contract) and on the producing helpers
directly, and `tests/test_duos_null_consumers.py` /
`tests/test_reservation_dormancy_and_fixed_lines.py` assert them on the helper return values.
Production reports an `OptimisationResult`, which none of those files ever obtains, so all of them
stayed green while the record carried nothing. They are correct at their own layer and are left
alone; this file supplements them at the type production actually returns.

The sharpest illustration is `test_reservation_dormancy_and_fixed_lines.py`, whose comment above
its assertion reads "they must arrive on the SOLVE-MODEL warnings, which the sizing candidates
(and so the reported result) inherit". That claim is TRUE on a run that selects a spec and FALSE
on one that does not, and the test cannot tell the two apart because it never leaves
`DispatchResult`. The inheritance it relies on is the thing this file measures.

- Production call site: `MilpEngine._candidate_model_disclosures` invoked from
  `MilpEngine._no_spec_result` at `optimisation/engine.py:975` (the ``no_viable_configuration``
  exit, which is the branch all six live sites take) and `:935` (the undeclared-capacity exit),
  reached from `generate_quote` (hence the CLI and the app) with no test-only code.
- `test_deleting_the_wiring_is_visible` states that invariant directly, and every test here runs
  the whole `generate_quote` path on a real MILP rather than calling the producer. Calling
  `_candidate_model_disclosures` directly would prove only that the thing was built, which is the
  exact mistake the eight prior instances made.
"""

from __future__ import annotations

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation import MilpEngine
from gridq.quotes import generate_quote
from gridq.schemas import SelectionStatus
from gridq.schemas.tariff import TariffStack

#: The header `_candidate_model_disclosures` puts in front of the carried block.
HEADER = "DISPATCH MODEL DISCLOSURES OF THE CANDIDATE SOLVE THIS VERDICT RESTS ON"

#: Physics statements `_solve_milp` raises that describe the MODEL rather than the tariff, so none
#: of them can be satisfied by 80e12f1's re-derived before-bill limb. Each names a different thing
#: a reader must know before weighting the priced floor: how much of the nameplate the dispatch was
#: allowed to sweep, whether kVA was distinguished from kW, whether wear was priced into the
#: objective at all, and what the connection headroom was reduced by.
PHYSICS_DISCLOSURES = (
    "SoC window",
    "UNITY POWER FACTOR ASSUMED",
    "marginal cycle cost",
    "parasitic auxiliary draw",
)


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_8")


@pytest.fixture(scope="module")
def unviable_site(tmp_path_factory):
    """A site that SOLVES and PRICES every candidate and still recommends nothing.

    Same construction as `tests/test_before_bill_without_dispatch.py`, and for the same reason: a
    real, time-mappable day/night tariff keeps the dispatch priceable (never the F-01 refusal
    path), but a 0.5p spread and a 0.1p DUoS red rate mean no candidate covers its capex. That
    lands the run in the ECONOMICS verdict (``no_viable_configuration``) rather than a data-shaped
    one, which is the branch the six live campaign sites take and the one this fix has to cover.
    """
    from tests._milp_support import multi_rate_site, write_hh_csv

    path = tmp_path_factory.mktemp("nospecdisclosures") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, site_id="site_no_viable_disclosures")
    return site.model_copy(
        update={
            "tariff_stack": TariffStack(
                basis=SYNTHETIC_BASIS,
                provenance=SYNTHETIC_PROVENANCE,
                supplier="MilpTestCo",
                import_rates={"day": 0.30, "night": 0.295},
                duos_red_rate=0.001,
                levies={"ro": 0.02, "fit": 0.01},
            )
        }
    )


@pytest.fixture(scope="module")
def unviable_run(unviable_site, assumptions):
    """One production run of the no-viable-configuration site (module-scoped: the MILP is slow)."""
    return generate_quote(unviable_site, assumptions, engine=MilpEngine())


@pytest.fixture(scope="module")
def undeclared_capacity_site(tmp_path_factory):
    """A site with NO declared import capacity, which takes the OTHER wired no-spec exit.

    `milp_solver` resolves an absent connection limit to 0 kW, so ``charge_ub`` is 0 in every
    half-hour: every candidate still solves and prices, at a mechanically-forced £0. The engine
    correctly refuses to call that an economics verdict and exits at
    `_connection_capacity_undeclared` instead. That exit needs its own coverage, because the
    mutation check on the economics exit alone would leave it wired but unpinned.
    """
    from tests._milp_support import multi_rate_site, write_hh_csv

    from gridq.schemas.connection import ConnectionDetails

    path = tmp_path_factory.mktemp("nocapacity") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, site_id="site_no_capacity")
    # `max_import_kw` stays declared (the solver refuses outright without it); only the CONNECTION
    # capacity is absent, which is the state this exit exists for.
    return site.model_copy(
        update={"connection": ConnectionDetails(mic_kw=0.0, mec_kw=None, asc_kw=None)}
    )


@pytest.fixture(scope="module")
def undeclared_capacity_run(undeclared_capacity_site, assumptions):
    return generate_quote(undeclared_capacity_site, assumptions, engine=MilpEngine())


def test_the_undeclared_capacity_exit_carries_the_disclosures_too(undeclared_capacity_run) -> None:
    """The second wired exit (`engine.py:935`), pinned on its own so it cannot rot separately.

    Its £0 floors are MECHANICAL rather than economic, which makes the physics statement more
    load-bearing here, not less: a reader has to be able to see that the dispatch model ran, what
    it assumed, and that the zero came from a missing connection capacity rather than from the
    asset.
    """
    result = undeclared_capacity_run.result
    assert result.selection_status == SelectionStatus.INSUFFICIENT_DATA, (
        f"the fixture no longer reaches the undeclared-capacity exit: {result.selection_status}"
    )
    assert result.warnings[0].startswith("connection capacity unknown"), (
        f"the verdict is no longer warnings[0]; got: {result.warnings[0][:120]}"
    )
    assert any(HEADER in w for w in result.warnings), (
        "the undeclared-capacity exit carries no dispatch-model disclosures"
    )
    missing = [d for d in PHYSICS_DISCLOSURES if not any(d in w for w in result.warnings)]
    assert not missing, f"physics disclosures absent from the undeclared-capacity record: {missing}"


@pytest.fixture(scope="module")
def viable_site(tmp_path_factory):
    """The unmodified multi-rate fixture, which DOES select a spec (the solved-path control)."""
    from tests._milp_support import multi_rate_site, write_hh_csv

    path = tmp_path_factory.mktemp("specdisclosures") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows, site_id="site_viable_disclosures")


@pytest.fixture(scope="module")
def viable_run(viable_site, assumptions):
    return generate_quote(viable_site, assumptions, engine=MilpEngine())


def test_the_fixtures_really_are_the_two_branches(unviable_run, viable_run) -> None:
    """Guard both premises: if either fixture flips branch, the tests below go vacuous."""
    assert unviable_run.result.selection_status == SelectionStatus.NO_VIABLE_CONFIGURATION, (
        f"the no-spec fixture no longer reaches the economics verdict: "
        f"{unviable_run.result.selection_status}"
    )
    assert unviable_run.result.selected_power_kw is None, "a spec was selected — there IS a spec"
    assert viable_run.result.selection_status == SelectionStatus.SELECTED, (
        f"the control fixture no longer selects: {viable_run.result.selection_status}"
    )
    assert viable_run.result.selected_power_kw is not None


def test_deleting_the_wiring_is_visible(unviable_run) -> None:
    """THE acceptance test: the carried block reaches the `OptimisationResult` production returns.

    Asserted on the object a REAL run produced, obtained by running `generate_quote` end to end.
    The prior instance of this defect (validation_requirements §3, row 5) survived precisely
    because its tests asserted the same statements on `DispatchResult`, a type no production caller
    ever reads. Delete either `candidate_disclosures=` argument in `MilpEngine.solve` and this
    fails.
    """
    warnings = unviable_run.result.warnings
    assert any(HEADER in w for w in warnings), (
        "the no-spec record carries no dispatch-model disclosures: the verdict reports a priced "
        "floor with no statement of the physics it was priced on"
    )
    missing = [d for d in PHYSICS_DISCLOSURES if not any(d in w for w in warnings)]
    assert not missing, (
        f"physics disclosures absent from the no-spec record: {missing}. These describe the MODEL, "
        "not the tariff, so the before-bill limb (80e12f1) cannot supply them."
    )


def test_the_disclosures_reach_the_quote_the_renderers_read(unviable_run) -> None:
    """A record entry nobody reads is the same defect one layer up.

    `outputs.viewmodel` builds its warnings list from ``quote.warnings``, so this is the step
    between "on the OptimisationResult" and "in front of the reader".
    """
    warnings = unviable_run.quote.warnings
    assert any(HEADER in w for w in warnings), "the carried block never reached QuoteRecord"
    missing = [d for d in PHYSICS_DISCLOSURES if not any(d in w for w in warnings)]
    assert not missing, f"physics disclosures absent from the quote record: {missing}"


def test_the_verdict_still_comes_first(unviable_run) -> None:
    """``warnings[0]`` is a documented contract: callers and tests read it as the verdict.

    The carried block is spliced after the supporting statements precisely so it cannot displace
    the verdict, and a disclosure that pushed the verdict down the list would trade one honesty
    problem for another.
    """
    first = unviable_run.result.warnings[0]
    assert first.startswith("no viable configuration:"), (
        f"the verdict is no longer warnings[0]; got: {first[:120]}"
    )
    assert HEADER not in first


def test_the_block_names_the_candidate_it_was_measured_on(unviable_run) -> None:
    """The figures inside the disclosures are one candidate's, so the record must say which.

    Without the spec the reader cannot tell whether "usable energy 373.6 kWh of 467.0 kWh" refers
    to the system being discussed or to some other point on the search grid.
    """
    header = next(w for w in unviable_run.result.warnings if HEADER in w)
    assert " kW / " in header and " kWh (" in header, (
        f"the disclosure header does not name the candidate's spec: {header[:200]}"
    )
    assert "best priced floor" in header
    assert "confirmed on the full metered series" in header


def test_the_tariff_absences_are_stated_once_not_twice(unviable_run) -> None:
    """De-duplication against 80e12f1's before-bill limb, which re-derives the same D45 lines.

    Both limbs legitimately produce the DUoS-band and fixed-line absences. Emitting each twice on
    one record devalues both copies and makes the block read as padding rather than as evidence.
    """
    warnings = unviable_run.result.warnings
    for marker in (
        "DUoS BAND RATE ABSENT, NOT ZERO (D45)",
        "STANDING CHARGE NOT RECORDED (D45)",
        "CAPACITY/AVAILABILITY CHARGE NOT RECORDED (D45)",
    ):
        hits = [w for w in warnings if marker in w]
        assert len(hits) == 1, f"{marker} appears {len(hits)} time(s) on one record"
    assert len(warnings) == len(set(warnings)), "the no-spec record repeats a warning verbatim"


def test_the_solved_path_still_carries_its_own_disclosures(viable_run) -> None:
    """The control: the SOLVED path already worked, and must not regress while the other is fixed.

    Measured, not assumed: this is the leg `sizing.sizer._to_optimisation_result` and
    `MilpEngine._final_result` provide, and it needs no carried block because the chosen
    candidate's own warnings ARE the reported result's.
    """
    warnings = viable_run.result.warnings
    missing = [d for d in PHYSICS_DISCLOSURES if not any(d in w for w in warnings)]
    assert not missing, f"the SOLVED path lost its dispatch disclosures: {missing}"
    assert not any(HEADER in w for w in warnings), (
        "a selected run carried the no-spec disclosure block: the chosen candidate's own "
        "warnings are already the reported result's, so the header would be a second account"
    )


def test_the_carried_block_is_deterministic(unviable_site, assumptions) -> None:
    """``warnings`` is inside ``input_hash``, so unstable ordering would move the run identity.

    Two independent engine instances on identical inputs must produce byte-identical warnings, in
    the same order. The de-duplication uses a set for membership only; the output order is the
    solver's own.
    """
    from gridq.schemas import OptimisationRequest

    def _warnings() -> list[str]:
        request = OptimisationRequest(
            request_id="req_determinism",
            site_model=unviable_site,
            assumption_version=assumptions.metadata.version,
        )
        return list(MilpEngine().solve(request).warnings)

    first, second = _warnings(), _warnings()
    assert first == second, "two identical runs produced different warnings"
    assert any(HEADER in w for w in first)
