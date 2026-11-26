"""The D45 null-input guard: a null can never be priced into the floor as a silent £0.

**The rule being enforced** (`CLAUDE.md` §2, `docs/validation_requirements.md` D45):

> A null is never an acceptable input. It is worse than a blocked run. A blocked run produces no
> number. A null produces a **confidently wrong one**. A missing ``duos_red_rate`` does not yield
> "unknown": it prices ``DUOS_RED_BAND_AVOIDANCE`` at **£0**, one of only two priced-floor streams,
> and that zero then flows into P50, P90 and the DSCR indistinguishable from a measured result.

Until this guard, the only thing standing between a null and that £0 was each stream's hand-written
``check_gates`` remembering to test the field it declared — convention, checked by review. The guard
(`gridq.value_stack.gates.enforce_declared_inputs`) turns the stream's own ``required_inputs()``
declaration into an invariant evaluated on **every** gate, wired in automatically by
``BaseValueStream.__init_subclass__`` so a stream author cannot omit it.

**Why these tests are shaped the way they are.** `docs/validation_requirements.md` §3 records seven
defects that were built, tested, merged and never called, *every one of which had a passing test* —
the tests called the new function directly, so deleting the wiring left them green. So the tests
below that matter run through **`optimisation.valuation.build_value_streams`**, the production
function `optimisation.milp_solver` calls (`milp_solver.py:2064` / `:2123`, reached from
`cli.py` → `MilpEngine.optimise`), and assert the £ and the gate status on the returned
``ValueStreamResult``:

- delete the ``gate = stream.check_gates(...)`` call in ``valuation.build_value_streams``
  (``optimisation/valuation.py:721`` at the time of writing) → four of them fail;
- delete the ``enforce_declared_inputs(...)`` call in ``streams._guard_check_gates``
  (``value_stack/streams.py:113``) → four of them fail.

Both mutations were run. Neither is detectable by a test that calls the guard directly, which is
why the direct-call tests here are the *supporting* cases, never the acceptance ones.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams
from gridq.value_stack import gates
from gridq.value_stack.gates import D45_NULL_INPUT_LABEL
from gridq.value_stack.interface import GateContext, GateResult, GateStatus, RequiredInput
from gridq.value_stack.registry import StreamRegistry, default_registry
from gridq.value_stack.streams import FloorStream

LIB: AssumptionLibrary = load_assumptions("gb_v1_8")


# --------------------------------------------------------------------------- helpers


def _schedule(*, discharge_kw: float = 100.0) -> HalfHourlyDispatchSchedule:
    """A synthetic solved schedule with real red-band discharge, so the DUoS stream prices > £0.

    Deliberately not a MILP solve: the guard lives on the gate, and what has to be proved is that
    the gate's verdict reaches the production ``ValueStreamResult``. A synthetic schedule isolates
    that and keeps the file fast.
    """
    n = 48
    arr: Callable[[object], np.ndarray] = lambda v: np.asarray(v, dtype=float)  # noqa: E731
    discharge = np.zeros(n)
    discharge[12:24] = discharge_kw  # inside the "red" hours below
    mask = np.zeros(n, dtype=bool)
    mask[12:24] = True
    return HalfHourlyDispatchSchedule(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=arr([0.0] * n),
        discharge_kw=discharge,
        soe_kwh=arr([0.0] * n),
        site_load_kw=arr([500.0] * n),
        duos_rate_gbp_per_kwh=arr([0.1] * n),
        red_band_mask=mask,
        energy_price_gbp_per_kwh=arr([0.25] * n),
        deliverable_capacity_mw=0.2,
    )


def _with_tariff(site: s.SiteModel, **rates: float | None) -> s.SiteModel:
    """The same site with DUoS band rates replaced (``None`` = the null case)."""
    tariff = site.tariff_stack.model_copy(update=rates)
    return s.SiteModel.model_validate({**site.model_dump(mode="python"), "tariff_stack": tariff})


def _duos(streams: list[s.ValueStreamResult]) -> s.ValueStreamResult:
    match = [v for v in streams if v.stream == s.MarketStream.DUOS_RED_BAND_AVOIDANCE]
    assert match, "the DUoS red-band stream must always be reported, priced or not"
    return match[0]


def _guard_notes(result: s.ValueStreamResult | GateResult) -> list[str]:
    return [w for w in result.warnings if w.startswith(D45_NULL_INPUT_LABEL)]


# --------------------------------------------------------------------------- acceptance
# These four run through the production valuation path.


def test_a_null_red_rate_is_held_out_of_the_priced_floor_and_says_so(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The exact case D45 names: ``duos_red_rate`` is NULL on a priced-floor stream.

    The £ must not merely *be* zero — a computed zero and an unpriced stream are the same number and
    the whole defect is that they are indistinguishable. So this asserts the structural difference:
    ``EXCLUDED`` firmness (outside `finance.cashflows._PRICED_FIRMNESS`, so outside the floor, P50,
    P90 and the DSCR), plus a labelled note naming the dotted key and a data ask.
    """
    site = _with_tariff(make_site(), duos_red_rate=None)
    value_streams, _, _ = build_value_streams(_schedule(), site, LIB)
    duos = _duos(value_streams)

    assert duos.gate_status == GateStatus.CLOSED.value
    assert duos.firmness == s.Firmness.EXCLUDED  # never counted into the priced floor
    assert duos.annual_revenue_gbp == 0.0

    notes = _guard_notes(duos)
    assert len(notes) == 1, "the null must be reported exactly once, not repeated or swallowed"
    assert "tariff_stack.duos_red_rate" in notes[0]
    assert "not a measured zero" in notes[0]
    assert "held" in notes[0].lower() and "OUT of the priced floor" in notes[0]


def test_absent_off_peak_bands_are_labelled_on_the_priced_stream_and_move_no_number(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The other direction, and the reason the guard is not simply "close the gate".

    With red declared and amber/green absent, the absent bands are consumed as zero, which widens
    the modelled red-to-green spread beyond the one the site faces: the stream is **overstated**,
    not zeroed (`optimisation.milp_solver.duos_absence_warning` documents the same asymmetry at run
    level). Closing the gate would refuse a stream the site genuinely earns, so the guard states the
    error and its direction and changes nothing — which is what the £ assertion pins.
    """
    site = _with_tariff(make_site(), duos_red_rate=0.10, duos_amber_rate=None, duos_green_rate=None)
    schedule = _schedule()
    value_streams, _, _ = build_value_streams(schedule, site, LIB)
    duos = _duos(value_streams)

    assert duos.gate_status == GateStatus.OPEN.value
    assert duos.firmness == s.Firmness.FIRM  # still in the priced floor — nothing was refused
    assert duos.annual_revenue_gbp == pytest.approx(schedule.duos_avoidance_value_gbp)
    assert duos.annual_revenue_gbp > 0.0, "fixture sanity: this schedule really does earn DUoS £"

    notes = _guard_notes(duos)
    assert len(notes) == 1
    assert "tariff_stack.duos_amber_rate" in notes[0] and "tariff_stack.duos_green_rate" in notes[0]
    assert "OVERSTATES" in notes[0]
    assert "do not block pricing" in notes[0]


def test_a_declared_zero_is_evidence_and_the_guard_stays_silent(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """A recorded ``0.0`` is a FACT — the site faces no charge on that line — and must stay silent.

    This is the half of the defect that runs the other way: ``or 0.0`` conflated a genuine zero with
    a null, and a guard that fires on both would relabel complete data as missing. The £ is
    identical either way, so only the label distinguishes them, and it must not appear here.
    """
    site = _with_tariff(make_site(), duos_red_rate=0.0, duos_amber_rate=0.0, duos_green_rate=0.0)
    value_streams, _, _ = build_value_streams(_schedule(), site, LIB)
    duos = _duos(value_streams)

    assert duos.gate_status == GateStatus.OPEN.value
    assert _guard_notes(duos) == []


def test_a_stream_that_forgets_its_own_null_check_still_cannot_price_one(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """**The drift test — the point of the whole exercise.**

    Every GB stream today happens to check the field it declares. Nothing enforced that, so the next
    stream, or a refactor of `gates.has_duos_rate_data`, silently reintroduces the defect. This
    stream declares a blocking input and returns OPEN regardless — exactly that mistake — and the
    guard must overrule it, through the production valuation path, with no cooperation from the
    stream itself.

    Without the guard this stream is FIRM and prices the full arbitrage value into the floor.
    """

    class _ForgetfulFloorStream(FloorStream):
        def __init__(self) -> None:
            super().__init__(
                stream_id=s.MarketStream.TARIFF_ARBITRAGE,
                display_name="forgetful floor stream",
                required_inputs=[
                    RequiredInput(
                        key="tariff_stack.duos_red_rate",
                        description="a blocking input this gate never looks at",
                    )
                ],
            )

        def check_gates(
            self,
            site_model: s.SiteModel,
            assumptions: AssumptionLibrary,
            context: GateContext | None = None,
        ) -> GateResult:
            return self._gate(GateStatus.OPEN)  # the mistake: never reads the declared input

    site = _with_tariff(make_site(), duos_red_rate=None)
    schedule = _schedule()
    registry = StreamRegistry([_ForgetfulFloorStream()])
    value_streams, _, _ = build_value_streams(schedule, site, LIB, registry=registry)

    assert len(value_streams) == 1
    result = value_streams[0]
    assert schedule.arbitrage_value_gbp > 0.0, "fixture sanity: there IS a £ to wrongly claim"
    assert result.gate_status == GateStatus.CLOSED.value
    assert result.firmness == s.Firmness.EXCLUDED
    assert result.annual_revenue_gbp == 0.0
    assert _guard_notes(result), "the overruled gate must say why it was overruled"


# --------------------------------------------------------------------------- structure


def test_the_guard_is_wired_into_every_registered_stream() -> None:
    """No stream in the production registry can opt out — the wiring is class-creation-time.

    Reachability of the *wrapper*, complementing the acceptance tests' reachability of the *effect*.
    """
    for stream in default_registry().all():
        own = type(stream).check_gates
        assert getattr(own, "_gridq_null_input_guarded", False), (
            f"{type(stream).__name__}.check_gates is not guarded — "
            "BaseValueStream.__init_subclass__ should have wrapped it"
        )


def test_every_floor_stream_declares_blocking_inputs_the_guard_can_actually_read(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The guard's blind spot, pinned so it cannot spread to the priced floor.

    A declared key that resolves to no SiteModel path at all (``route_to_market.partner``,
    ``dfs.zone``) is invisible to the guard — it abstains rather than pretending to judge. That is
    tolerable on conditional/gated streams, whose value never enters the floor. It is **not**
    tolerable on a floor stream, where an unreadable declaration means a null could still price.
    """
    site = make_site()
    for stream in default_registry().all():
        if not isinstance(stream, FloorStream):
            continue
        for required in stream.required_inputs():
            if not required.blocks_pricing:
                continue
            assert gates.requirement_presence(site, required) != gates.INPUT_UNRESOLVABLE, (
                f"{stream.stream_id.value} declares blocking input {required.key!r}, which "
                "resolves to no SiteModel path — the D45 guard cannot see it, so a null there "
                "would price into the floor unchecked"
            )


def test_the_guard_only_ever_tightens_and_never_invents(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """It may close a gate; it may never open one, and it never supplies a value.

    A guard that could re-open a gate would be deriving eligibility, which is precisely the
    invention D45 forbids. Applied to a CLOSED gate whose declared input is present, it is a no-op.
    """
    stream = default_registry().get(s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    closed = gates.build_gate_result(
        s.MarketStream.DUOS_RED_BAND_AVOIDANCE, GateStatus.CLOSED, missing_requirements=["x"]
    )
    site = _with_tariff(make_site(), duos_red_rate=0.1, duos_amber_rate=0.05, duos_green_rate=0.01)

    unchanged = gates.enforce_declared_inputs(
        s.MarketStream.DUOS_RED_BAND_AVOIDANCE, stream.required_inputs(), site, closed
    )
    assert unchanged == closed  # no null, nothing to say

    # ...and with a null it stays closed rather than being reopened, gaining only the label.
    null_site = _with_tariff(site, duos_red_rate=None)
    tightened = gates.enforce_declared_inputs(
        s.MarketStream.DUOS_RED_BAND_AVOIDANCE, stream.required_inputs(), null_site, closed
    )
    assert tightened.gate_status == GateStatus.CLOSED
    assert _guard_notes(tightened)


def test_the_guard_is_idempotent(make_site: Callable[..., s.SiteModel]) -> None:
    """Re-applying it adds nothing: an override that calls ``super()`` cannot double-report."""
    stream = default_registry().get(s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    site = _with_tariff(make_site(), duos_red_rate=None)
    once = stream.check_gates(site, LIB)
    twice = gates.enforce_declared_inputs(
        s.MarketStream.DUOS_RED_BAND_AVOIDANCE, stream.required_inputs(), site, once
    )
    assert twice == once


def test_a_disjunctive_requirement_is_satisfied_by_its_alternative(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """MIC *or* ASC; a measured peak *or* HH coverage — declared, so the guard does not misfire.

    Without ``alternatives`` the guard would read the declaration literally and close MIC/ASC
    headroom on every site that records an ASC but no MIC: a false refusal, which is a different
    way of getting the number wrong.
    """
    base = make_site()
    connection = base.connection.model_copy(update={"mic_kw": None, "asc_kw": 400.0})
    site = s.SiteModel.model_validate({**base.model_dump(mode="python"), "connection": connection})

    stream = default_registry().get(s.MarketStream.MIC_ASC_HEADROOM)
    gate = stream.check_gates(site, LIB)
    assert gate.gate_status == GateStatus.OPEN
    assert _guard_notes(gate) == []


# --------------------------------------------------------------------------- known limit


def test_the_guard_cannot_tell_not_applicable_from_not_sourced(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """**The residual limit, now that the recorded remedy has landed.**

    Colnbrook and Tanhouse carry a null ``duos_red_rate`` legitimately: they are 100% private-wire
    and the residual is already back-solved from the invoice, so adding DUoS would double-count. A
    site nobody has sourced a rate for carries the same ``None``.

    **This was once a limitation of the schema and is now a limitation of the RECORD.**
    `schemas.derived.DerivedAssumption` grew a third class, ``not_applicable``, and
    `value_stack.gates.evidenced_absences` is read by the guard, so a site whose register declares
    the absence is reported differently: same gate, same firmness, same £, but a note saying the
    component does not exist for this site and the register's own confirmation ask instead of a
    request for a rate (`tests/test_evidenced_absence_reaches_the_data_ask.py`). What survives is
    what this test pins: with **no record either way** the two sites are identical inputs and are
    reported identically, which is correct — the guard reads the field and the register, and it
    derives nothing from silence.

    The two expressible resolutions: record the absence as ``not_applicable``, or record the
    evidenced ``0.0`` with its reason (the second half of this test), and the guard reads a declared
    zero as the fact it is.
    """
    not_applicable = _with_tariff(make_site(), duos_red_rate=None)  # private wire, by decision
    never_sourced = _with_tariff(make_site(), duos_red_rate=None)  # nobody looked

    stream = default_registry().get(s.MarketStream.DUOS_RED_BAND_AVOIDANCE)
    assert _guard_notes(stream.check_gates(not_applicable, LIB)) == _guard_notes(
        stream.check_gates(never_sourced, LIB)
    ), "an UNRECORDED absence must never be inferred: the guard derives nothing from silence"

    # The workaround available today: an evidenced zero is a fact and is read as one. Note it takes
    # ALL THREE bands — recording only the red one leaves the off-peak bands null and the guard
    # still (correctly) reports them, which is the right answer for a site that really does face a
    # DUoS charge and the wrong shape of record for one that does not.
    evidenced_zero = _with_tariff(
        make_site(), duos_red_rate=0.0, duos_amber_rate=0.0, duos_green_rate=0.0
    )
    assert _guard_notes(stream.check_gates(evidenced_zero, LIB)) == []
