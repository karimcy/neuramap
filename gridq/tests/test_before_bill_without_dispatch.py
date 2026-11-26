"""The dispatch-invariant BEFORE bill exists on a run that recommends no system — and Gate 3b runs.

**The defect these tests pin.** `CustomerBill.annual_bill_before_gbp` is dispatch-invariant by
construction: the site's own metered load against its own tariff stack, with no battery in it. Gate
3b (bill-vs-invoice) deliberately reconciles that BEFORE side precisely because it tests the TARIFF
STACK rather than the battery. But the engine only ever emitted a `CustomerBill` alongside a priced
spec, so on a no-spec run `MilpEngine.last_customer_bill()` returned ``None``,
`quotes.pipeline._gate_3b` returned ``None`` on its first line, and the gate was **inert**. All six
live campaign sites return ``no_viable_configuration``, so it was inert on 100% of real runs — on
exactly the sites whose verdict most depends on the tariff model being right, since a wrong tariff
stack is a leading candidate for *why* a site prices as unviable.

Written to `docs/validation_requirements.md` §3, because "built, tested, called from nowhere" has
shipped **eight** times in this repo and every one had a passing test:

- The production call site is `MilpEngine._before_only_customer_bill`, invoked from
  `MilpEngine._no_spec_result` — the single funnel every no-spec exit in `solve` returns through —
  and reached from `generate_quote` (hence from the CLI and the app) with no test-only code.
- `test_deleting_the_call_site_is_visible` states that invariant directly, and every test here runs
  the whole `generate_quote` path rather than calling the producer, so deleting the call inside
  `_no_spec_result` fails them. Calling `_before_only_customer_bill` directly would prove only that
  the thing was built — the exact mistake the eight prior instances made.

The AFTER side is deliberately **absent**, never £0: with no dispatch there is no after-battery
bill, and a zero saving renders as a computed result ("this battery saves you nothing") instead of
as the absence of a battery.
"""

from __future__ import annotations

import pytest

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.ingestion.reconciliation import InvoiceEvidence
from gridq.optimisation import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.quotes import generate_quote
from gridq.schemas import CustomerBill, SelectionStatus
from gridq.schemas.tariff import TariffStack

FACTOR = "bill reconciliation"
_UNRUN = "was not compared against an invoice"


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_8")


@pytest.fixture(scope="module")
def unviable_site(tmp_path_factory):
    """A site that SOLVES and PRICES every candidate and still recommends nothing.

    The tariff keeps a real, time-mappable day/night structure — so the bill is computable and the
    dispatch is priceable (never the F-01 refusal path) — but the day/night spread is 0.5p and the
    DUoS red rate 0.1p, so no candidate covers its capex. That lands the run in the ECONOMICS
    verdict (`no_viable_configuration`), not in a data-shaped one, which is the branch the six live
    campaign sites take and the one this fix has to cover.
    """
    from tests._milp_support import multi_rate_site, write_hh_csv

    path = tmp_path_factory.mktemp("beforebill") / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, site_id="site_no_viable")
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
def invoice(unviable_site):
    """Real invoice evidence, so Gate 3b performs a COMPARISON rather than recording an absence.

    An absence record would also reach the warnings, which would make the acceptance assertion pass
    for the wrong reason — the gate has an absence branch that needs no bill at all.
    """
    return InvoiceEvidence(
        site_id=unviable_site.site_id,
        invoice_available=True,
        period="calendar 2025",
        invoiced_kwh=1_000_000.0,
        invoiced_cost_gbp=250_000.0,
        source="test-injected",
    )


@pytest.fixture(scope="module")
def unviable_run(unviable_site, assumptions, invoice):
    """One production run of the no-viable-configuration site (module-scoped: the MILP is slow)."""
    return generate_quote(unviable_site, assumptions, engine=MilpEngine(), invoice_evidence=invoice)


def test_the_fixture_really_is_a_no_viable_configuration_run(unviable_run) -> None:
    """Guard the premise: if this site started selecting a spec, every test below goes vacuous."""
    result = unviable_run.result
    assert result.selection_status == SelectionStatus.NO_VIABLE_CONFIGURATION, (
        f"the fixture no longer reaches the economics verdict: {result.selection_status}"
    )
    assert result.selected_power_kw is None, "a spec was selected — there IS a dispatch"


def test_gate_3b_reconciles_the_bill_on_a_run_with_no_dispatch(unviable_run) -> None:
    """THE acceptance test: a bill-reconciliation record reaches `quote.warnings` with no spec.

    Before the fix this was structurally impossible — no spec ⇒ no bill ⇒ `_gate_3b` returned
    ``None`` on its first line and contributed nothing to the record.
    """
    warnings = unviable_run.quote.warnings
    assert any("Bill reconciliation (Gate 3b)" in w for w in warnings), (
        "Gate 3b contributed no verdict to the quote on a no-viable-configuration run — the "
        "bill-vs-invoice gate is inert on exactly the sites it matters most for"
    )
    keys = [record.key for record in unviable_run.derived_assumptions]
    assert "tariff_stack.bill_cost_gbp" in keys, (
        f"the gate recorded no delivered-cost comparison; got {keys}"
    )
    # ...and the disclosure reaches the report section too, not only the record.
    assert any("tariff_stack.bill_cost_gbp" in w for w in warnings), (
        "the Gate 3b record never reached the report's assumptions-requiring-confirmation section"
    )


def test_the_gate_verdict_reaches_the_confidence_assessment(unviable_run) -> None:
    """A record is not enough: the gate's verdict must move the band it is supposed to cap."""
    confidence = unviable_run.confidence
    assert confidence is not None, "no confidence assessment was produced"
    factor = next((f for f in confidence.factors if f.name == FACTOR), None)
    assert factor is not None, (
        f"Gate 3b contributed no confidence factor; got {[f.name for f in confidence.factors]}"
    )
    assert _UNRUN not in factor.reason, (
        "the bill-reconciliation factor still reports the gate as un-run on a no-spec site even "
        "though invoice evidence was supplied — the bill is not reaching the gate"
    )


def test_the_before_side_is_real_and_the_after_side_is_absent_not_zero(unviable_run) -> None:
    """Constraint 2: with no dispatch, the AFTER side must be structurally missing, never £0.

    A £0 after-bill would satisfy the schema's sum invariant and render as a computed saving of the
    whole bill; a £0 *saving* would render as "this battery saves you nothing". Both are claims
    about a dispatch that does not exist.
    """
    bill = unviable_run.quote.customer_bill
    assert bill is not None, "no customer bill reached the record on a no-spec run"
    assert bill.annual_bill_before_gbp > 0, "the dispatch-invariant BEFORE bill priced to nothing"
    assert bill.import_before_kwh > 0
    assert bill.after_side_priced is False
    assert bill.annual_bill_after_gbp is None, "an after-battery bill was invented with no dispatch"
    assert bill.annual_saving_gbp is None, "a saving was invented with no dispatch"
    assert bill.energy_after_gbp is None
    assert bill.duos_after_gbp is None
    assert bill.levies_after_gbp is None
    assert bill.import_after_kwh is None
    assert bill.peak_import_after_kw is None, (
        "a peak was measured off a schedule that does not exist"
    )
    assert "BEFORE ONLY" in bill.basis and "NO SYSTEM IS RECOMMENDED" in bill.basis, (
        f"the bill's own honesty label does not state the absence: {bill.basis}"
    )


def test_the_tariff_line_absences_are_declared_on_the_same_record(unviable_run) -> None:
    """D45 parity with the solved path: a £0 from an ABSENT input must never read as measured.

    The fixture declares only a red DUoS rate and no fixed lines, so amber/green and both fixed
    lines contribute £0 to a bill a reconciliation gate now compares against an invoice. On the
    solved path `_solve_milp` raises these; a no-spec record reports no candidate solve, so they
    have to be raised alongside the before-only bill or the £0 is silent.
    """
    warnings = unviable_run.result.warnings
    assert any("DUoS BAND RATE ABSENT, NOT ZERO (D45)" in w for w in warnings), (
        "the absent amber/green DUoS rates are priced at £0 in the reconciled bill undeclared"
    )
    assert any("STANDING CHARGE NOT RECORDED (D45)" in w for w in warnings)
    assert any("CAPACITY/AVAILABILITY CHARGE NOT RECORDED (D45)" in w for w in warnings)


def test_deleting_the_call_site_is_visible(unviable_run) -> None:
    """The §3 rule made explicit, so the wiring is never silently removed again.

    `MilpEngine._no_spec_result` calls `_before_only_customer_bill` and assigns the result to
    `self._customer_bill`; `generate_quote` reads it back through the duck-typed
    `last_customer_bill()` hook and hands it to `_gate_3b`. Delete that call and every assertion
    below reverts to the pre-fix behaviour — which is what this test names.
    """
    assert unviable_run.customer_bill is not None, (
        "the engine hook returned no bill on a no-spec run — the `_no_spec_result` call site is "
        "gone"
    )
    assert unviable_run.quote.customer_bill is not None, "the bill did not reach the record"
    assert any("Bill reconciliation (Gate 3b)" in w for w in unviable_run.quote.warnings), (
        "the bill reached the record but not the gate"
    )


def test_the_before_side_equals_the_bill_a_solved_dispatch_produces(
    unviable_site, assumptions
) -> None:
    """The BEFORE figures must be the SAME numbers a priced run would quote, to the penny.

    This is the justification for the before-only bill reusing `HalfHourlyDispatchSchedule
    .customer_bill` over a zero-dispatch schedule rather than restating the arithmetic: the total
    Gate 3b reconciles against an invoice has to be the total the proposal would show. A separate
    implementation could drift by an annualisation factor or a levy line and nothing would notice.
    """
    engine = MilpEngine()
    before_only, _warn = engine._before_only_customer_bill(unviable_site, assumptions)
    assert before_only is not None

    solver = MilpDispatchSolver()
    solver.solve(
        DispatchProblem(
            site_model=unviable_site,
            assumptions=assumptions,
            power_kw=100.0,
            energy_kwh=200.0,
            duration_hours=2.0,
        )
    )
    solved = solver.last_customer_bill()
    assert solved is not None, "the fixture no longer produces a solved bill to compare against"

    assert before_only.annual_bill_before_gbp == solved.annual_bill_before_gbp
    assert before_only.import_before_kwh == solved.import_before_kwh
    assert before_only.energy_before_gbp == solved.energy_before_gbp
    assert before_only.duos_before_gbp == solved.duos_before_gbp
    assert before_only.levies_before_gbp == solved.levies_before_gbp
    assert before_only.standing_gbp == solved.standing_gbp
    assert before_only.capacity_gbp == solved.capacity_gbp
    assert before_only.peak_import_before_kw == solved.peak_import_before_kw
    assert before_only.levy_rate_gbp_per_kwh == solved.levy_rate_gbp_per_kwh


def test_the_before_only_bill_is_deterministic(unviable_site, assumptions) -> None:
    """Identical inputs, identical output — the engine's first architectural principle."""
    first, first_warn = MilpEngine()._before_only_customer_bill(unviable_site, assumptions)
    second, second_warn = MilpEngine()._before_only_customer_bill(unviable_site, assumptions)
    assert first is not None and second is not None
    assert first.model_dump() == second.model_dump()
    assert first_warn == second_warn


def _before_only_kwargs() -> dict[str, object]:
    return {
        "annual_bill_before_gbp": 100.0,
        "annual_bill_after_gbp": None,
        "annual_saving_gbp": None,
        "energy_before_gbp": 60.0,
        "energy_after_gbp": None,
        "duos_before_gbp": 30.0,
        "duos_after_gbp": None,
        "levies_before_gbp": 10.0,
        "levies_after_gbp": None,
        "import_before_kwh": 1000.0,
        "import_after_kwh": None,
        "levy_rate_gbp_per_kwh": 0.01,
        "basis": "before only",
    }


def test_a_half_populated_after_side_is_a_validation_error() -> None:
    """All-or-nothing is what makes the absence unmistakable: no partly-built AFTER side exists."""
    kwargs = _before_only_kwargs() | {"annual_bill_after_gbp": 90.0}
    with pytest.raises(ValueError, match="wholly present or wholly absent"):
        CustomerBill(**kwargs)  # type: ignore[arg-type]


def test_a_before_only_bill_cannot_carry_a_dispatch_measured_peak() -> None:
    """`peak_import_after_kw` is read off the solved schedule; with none, it must stay absent."""
    kwargs = _before_only_kwargs() | {"peak_import_after_kw": 3.0}
    with pytest.raises(ValueError, match="before-only bill"):
        CustomerBill(**kwargs)  # type: ignore[arg-type]


def test_a_solved_bill_still_enforces_its_after_side_invariants() -> None:
    """The relaxation must not weaken the priced case: before − after still has to hold."""
    kwargs = _before_only_kwargs() | {
        "annual_bill_after_gbp": 90.0,
        "annual_saving_gbp": 999.0,  # not before − after
        "energy_after_gbp": 50.0,
        "duos_after_gbp": 30.0,
        "levies_after_gbp": 10.0,
        "import_after_kwh": 900.0,
    }
    with pytest.raises(ValueError, match="annual_saving_gbp must equal"):
        CustomerBill(**kwargs)  # type: ignore[arg-type]
