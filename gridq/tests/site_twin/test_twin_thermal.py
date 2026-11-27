"""WP-TWIN-01 acceptance 2, thermal: the twin's cooling plant responds as the design-time thermal
model does for the same inputs (D198).

1. chiller power: with no instruction the site draws exactly its facility load; during a hold the
   import falls by exactly the instructed kW, and ``P(x) - P(x - F) = K`` to 1e-9;
2. trajectories: over a hold of at least the minimum off time and the recovery after it, the
   twin's loop and hall deviations match the independent WP-THERM-03 oracle
   (`tests/thermal/_thermal_oracle.py`) within the oracle's own error bound;
3. firmness: instructions the IC-11 surface accepts keep the twin inside ``loop_up`` and
   ``hall_up`` with ``F <= f_rate``, replaying the certified driver history;
4. refusals: an instruction the surface refuses, or one offered while the plant recovers, moves
   nothing;
5. a drift tripwire: the certificate's own update lines are still the ones `gridq.thermal.nodes`
   restates, and the two give the same numbers;
6. the cooling registers reach a gateway as valid IC-14 batches.

All inputs are the WP-THERM-03 SYNTHETIC fixture (plant, surface and invented driver year).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import _twin_support as ts
import numpy as np
import pytest

from gridq.contracts import NotApplicable
from gridq.contracts.telemetry_series import check_batch, verify_batch_chain
from gridq.contracts.thermal_flex import (
    FlexInstruction,
    InstructionIssuer,
    availability_ref,
    query_at,
)
from gridq.edge_southbound import Commissioning, SouthboundGateway
from gridq.site_twin import SiteTwin
from gridq.thermal import nodes

tfx, Batch, simulate = ts.tfx, ts.oracle.Batch, ts.oracle.simulate

#: Local winter afternoon: the fixture surface declares 16 kW for 20 minutes at 14:00 in January.
WINTER = datetime(2027, 1, 14, 14, 0, tzinfo=UTC)
#: The same day of the fixture's driver history.
HISTORY = datetime(2025, 1, 14, 13, 30, tzinfo=UTC)


def _na(reason: str) -> NotApplicable:
    return NotApplicable(kind="not_applicable", reason=reason)


def _instruction(start: datetime, minutes: int, kw: int, n: int = 1) -> FlexInstruction:
    s = tfx.built("base").surface
    return FlexInstruction(
        instruction_id=f"SYNTHETIC-TWIN01-I{n}",
        site_id=s.site_id,
        availability=availability_ref(s),
        issued_at=start - timedelta(minutes=30),
        issuer_role=InstructionIssuer.NEURA_DISPATCH,
        issuer_id="synthetic-test",
        on_behalf_of=_na("synthetic test: the site's own cost"),
        stream=_na("synthetic test: the site's own cost"),
        reduction_kw=kw,
        start=start,
        duration_min=minutes,
        ack_deadline=start - timedelta(minutes=10),
    )


def _declared(start: datetime, minutes: int) -> int:
    kw = query_at(tfx.built("base").surface, start, minutes).deliverable_kw
    assert isinstance(kw, int) and kw >= 1
    return kw


def _twin_at(start: datetime, history: datetime, minutes: int) -> SiteTwin:
    return ts.thermal_twin(start - timedelta(minutes=30), history, minutes + 30)


# --------------------------------------------------------------------------- 1


def test_the_import_falls_by_exactly_the_instructed_kw_and_rebounds_after() -> None:
    kw = _declared(WINTER, 20)
    twin = _twin_at(WINTER, HISTORY, 60)
    assert twin.offer_flex(_instruction(WINTER, 20, kw)).accepted
    twin.advance(70 * 60_000)
    hold0 = 30 * 60
    before, hold, after = (
        twin.seconds[:hold0],
        twin.seconds[hold0 : hold0 + 20 * 60],
        twin.seconds[hold0 + 20 * 60 :],
    )
    assert all(s.import_w == s.facility_w and s.reduction_w == 0 for s in before)
    for s in hold:
        th = s.thermal
        assert th is not None and not th.clamped and not th.infeasible
        assert s.facility_w - s.import_w == kw * 1000
        assert math.isclose(th.reduction_kw, kw, rel_tol=1e-9)
        assert th.deficit_kw > 0
    rebound = [s for s in after if s.import_w > s.facility_w]
    assert rebound, "recovering the stored heat costs chiller power"
    assert after[-1].import_w == after[-1].facility_w
    assert twin.cooling is not None and twin.cooling.e == 0.0


# --------------------------------------------------------------------------- 2


def test_the_twin_trajectory_matches_the_independent_oracle() -> None:
    hold = 20
    assert hold >= tfx.RAW["min_off"]
    kw = _declared(WINTER, hold)
    horizon = 45
    twin = _twin_at(WINTER, HISTORY, horizon)
    assert twin.offer_flex(_instruction(WINTER, hold, kw)).accepted
    twin.advance((30 + horizon) * 60_000)
    s0 = 30 * 60
    x = np.asarray([twin.load_at(30 + m)[1] / 1000.0 for m in range(horizon)])
    assert twin.config.thermal is not None
    t = np.asarray(twin.config.thermal.ambient_c[30 : 30 + horizon], dtype=float)
    plant = ts.numeric_plant()
    out = simulate(
        tfx.RAW,
        Batch(
            x=x[None, :],
            t=t[None, :],
            duration=np.asarray([hold]),
            kw=np.asarray([float(kw)]),
            switch_off=np.asarray([False]),
            keep=np.asarray([0]),
            u_cap=plant.u_cap,
        ),
    )
    assert bool(out.held[0])
    # The oracle steps every 2 s; read the twin at the same instants.
    states = [twin.seconds[s0 + 2 * k - 1].thermal for k in range(1, horizon * 30 + 1)]
    w = np.asarray([s.loop_dev_k for s in states if s is not None])
    a = np.asarray([s.hall_dev_k for s in states if s is not None])
    tol = out.tol_k
    assert tol > 0
    assert abs(w.max() - out.loop_max[0]) <= tol
    assert abs(min(w.min(), 0.0) - out.loop_min[0]) <= tol
    assert abs(a.max() - out.inlet_dev_max[0]) <= tol
    assert abs(min(a.min(), 0.0) - out.inlet_dev_min[0]) <= tol
    assert w.max() > 10 * tol, "the comparison is not vacuous"
    assert twin.cooling is not None
    assert abs(twin.cooling.e - out.stored_end_kj[0]) <= 1e-6


# --------------------------------------------------------------------------- 3


@pytest.mark.parametrize(
    ("start", "history", "minutes"),
    [
        (WINTER, HISTORY, 15),
        (WINTER - timedelta(hours=4), HISTORY - timedelta(hours=4), 20),
        (
            datetime(2027, 7, 14, 13, 0, tzinfo=UTC),  # 14:00 BST
            datetime(2025, 7, 14, 12, 30, tzinfo=UTC),
            15,
        ),
    ],
    ids=["jan-1400-15min", "jan-1000-20min", "jul-1400-15min"],
)
def test_accepted_instructions_stay_inside_the_certified_limits(
    start: datetime, history: datetime, minutes: int
) -> None:
    kw = _declared(start, minutes)
    twin = _twin_at(start, history, minutes + 30)
    assert twin.offer_flex(_instruction(start, minutes, kw)).accepted
    twin.advance((30 + minutes + 30) * 60_000)
    plant = ts.numeric_plant()
    hold = twin.seconds[30 * 60 : (30 + minutes) * 60]
    ths = [s.thermal for s in hold if s.thermal is not None]
    assert len(ths) == minutes * 60
    assert max(t.loop_dev_k for t in ths) <= plant.loop_up + 1e-9
    assert max(t.hall_dev_k for t in ths) <= plant.hall_up + 1e-9
    assert max(t.deficit_kw for t in ths) <= plant.f_rate
    for s in twin.seconds:
        assert s.thermal is not None
        assert abs(s.thermal.deficit_kw) <= plant.f_rate + 1e-9


# --------------------------------------------------------------------------- 4


def test_refused_instructions_move_nothing() -> None:
    kw = _declared(WINTER, 20)
    twin = _twin_at(WINTER, HISTORY, 60)
    over = twin.offer_flex(_instruction(WINTER, 20, kw + 1, 1))
    assert not over.accepted and over.reasons == ("exceeds_declared_availability",)
    assert twin.offer_flex(_instruction(WINTER, 20, kw, 2)).accepted
    busy = twin.offer_flex(_instruction(WINTER + timedelta(minutes=30), 15, 1, 3))
    assert not busy.accepted and busy.reasons == ("plant_not_at_rest",)
    twin.advance(35 * 60_000)
    late = twin.offer_flex(_instruction(WINTER, 20, kw, 4))
    assert not late.accepted
    assert twin.cooling is not None and len(twin.cooling.accepted) == 1
    plain = ts.twin()
    assert plain.offer_flex(_instruction(WINTER, 20, kw)).reasons == ("no_thermal_plant",)


# --------------------------------------------------------------------------- 5


CERTIFICATE_LINES = (
    "    decay = math.exp(-lam * 60.0)",
    "        e = e + f * 60.0",
    "        d_inf = -f / (cw * lam)",
    "        d = d_inf + (d - d_inf) * decay",
    "            (e - ca * d) / c > plant.loop_up,",
    "            e / c > plant.hall_up,",
)


def test_the_certificate_still_steps_the_nodes_as_nodes_restates() -> None:
    src = (ts.SRC / "gridq" / "thermal" / "certificate.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    for line in CERTIFICATE_LINES:
        assert line in lines, f"the certificate changed; fold or update gridq.thermal.nodes: {line}"
    plant = ts.numeric_plant()
    rng = np.random.default_rng(20261126)
    cw, ca, c, lam = plant.c_loop, plant.c_hall, plant.c_total, plant.lam
    decay = math.exp(-lam * 60.0)
    for _ in range(200):
        e, d, f = (float(v) for v in rng.normal(0, [5e4, 0.5, 300]))
        e2, d2 = nodes.step(plant, e, d, f, 60.0)
        # The certificate's lines, as they stand.
        e_c = e + f * 60.0
        d_inf = -f / (cw * lam)
        d_c = d_inf + (d - d_inf) * decay
        assert (e2, d2) == (e_c, d_c)
        w, _ = nodes.deviations(plant, e2, d2)
        assert w == (e_c - ca * d_c) / c
    # Sixty one-second steps equal one sixty-second step (the exact solution composes).
    e, d = 1.0e5, -0.3
    for _ in range(60):
        e, d = nodes.step(plant, e, d, 250.0, 1.0)
    e60, d60 = nodes.step(plant, 1.0e5, -0.3, 250.0, 60.0)
    assert math.isclose(e, e60, rel_tol=1e-12) and math.isclose(d, d60, rel_tol=1e-9)
    with pytest.raises(ValueError):
        nodes.step(plant, 0.0, 0.0, 0.0, -1.0)


# --------------------------------------------------------------------------- 6


def test_the_cooling_registers_reach_the_gateway_as_ic14_batches() -> None:
    twin = _twin_at(WINTER, HISTORY, 10)
    assert twin.offer_flex(_instruction(WINTER - timedelta(minutes=29), 20, 1)).accepted
    server = twin.serve()
    com = Commissioning.from_json(twin.commissioning(ts.SENDER))
    assert len(com.points) == 11
    gw = SouthboundGateway("127.0.0.1", server.port, com, [], ts.TwinClock(twin))
    batches = []
    try:
        for _ in range(125):
            gw.poll()
            twin.advance(1000)
            batches += gw.seal()
    finally:
        gw.close()
        server.stop()
    by: dict[str, list] = {}
    for b in batches:
        p = next(p for p in com.points if p.descriptor.series_id == b.series_id)
        check_batch(p.descriptor, b)
        by.setdefault(b.series_id, []).append(b)
    for chain in by.values():
        verify_batch_chain(chain)
    loop = next(v for k, v in by.items() if k.endswith("temperature.device_reported.supply"))
    first = loop[0].segments[0].model_dump()["values"]
    assert first[0] == 10_000  # the chilled-water setpoint, at rest
    assert loop[1].segments[0].model_dump()["values"][-1] > 10_000  # the hold warms the loop
