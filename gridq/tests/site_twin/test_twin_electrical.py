"""WP-TWIN-01 acceptance 2, electrical: the twin's battery responds exactly as the design-time
adequacy kernel does for the same inputs (D-NEW-TWIN01-2).

The twin replays `gridq.adequacy.kernel.run_kernel`; these tests restate the setpoint encoding
independently, run the kernel themselves and require equality to the joule:

1. encoded equality: per-second state of energy equals ``Trace.soe_after``, the shortfall equals
   ``unserved_j``, no floor breach;
2. native-policy equality: the adequacy kernel's own import-limit policy over half an hour of load
   equals the twin driven second by second with the setpoints that policy implies;
3. mid-minute changes: re-writing the same setpoint changes nothing; a different one equals two
   chained kernel runs; a write takes effect from the next whole second;
4. the meter: the site reads facility plus battery, the battery's AC and grid joules respect the
   efficiencies, the registers report what the twin holds;
5. structure: the twin never imports the kernel's private steps and takes its state of energy
   only from the trace;
6. the coarse (60 s) kernel path stays within 60 J per active minute of the twin's 1 s path.

All inputs are synthetic (`_twin_support`).
"""

from __future__ import annotations

import ast

import _twin_support as ts
import numpy as np
import pytest

from gridq.adequacy.kernel import KernelInputs, Trace, run_kernel
from gridq.site_twin import BessSpec, BessTwin

SMALL = ts.bess_spec(usable=20, discharge_kw=1000, charge_kw=500, initial=10)


def _kernel_minute(spec: BessSpec, sp: int, net: int, soe: int) -> tuple[list[int], int, int]:
    """The D-NEW-TWIN01-2 encoding, restated here: one fine minute from ``soe``."""
    s = max(-max(sp, -spec.discharge_w), 0) if sp < 0 else 0
    c = min(sp, spec.charge_w) if sp > 0 else 0
    one = lambda v: np.asarray([v], dtype=np.int64)  # noqa: E731
    k = KernelInputs(
        net_w=np.asarray([[net]], dtype=np.int64),
        permit_w=one(net - s + c),
        restricted=np.zeros(1, dtype=bool),
        cover_cap_w=one(s),
        floor_j=one(0),
        cover_floor_j=one(0),
        merchant_w=one(0),
        merchant_floor_j=one(0),
        charge_target_j=one(spec.usable_j if c else 0),
        charge_cap_w=one(c),
        thermal_active=np.zeros(1, dtype=bool),
        fine=np.ones(1, dtype=bool),
        eta_discharge=spec.eta_discharge,
        eta_charge=spec.eta_charge,
        soe0_j=soe,
        event_start_s=(),
        event_end_s=(),
        recharge_metrics=False,
        unserved_threshold_w=1,
    )
    tr = Trace()
    out = run_kernel(k, skip=False, trace=tr)
    return [int(a[0]) for a in tr.soe_after], int(out.unserved_j[0]), out.floor_breaches


# --------------------------------------------------------------------------- 1


@pytest.mark.parametrize(
    "spec", [SMALL, ts.bess_spec()], ids=["empties-and-fills", "two-megawatt-hours"]
)
def test_the_twin_battery_equals_the_kernel_minute_by_minute(spec: BessSpec) -> None:
    script = [-800_000, -2_000_000, -1_000_000, 0, 400_000, 900_000, 500_000, -1, 1, 0]
    nets = [1_500_000 + 37_123 * i for i in range(len(script))]
    twin = BessTwin(spec)
    shorts = 0
    for sp, net in zip(script, nets, strict=True):
        start = twin.soe_j
        want, unserved, breaches = _kernel_minute(spec, sp, net, start)
        got = [twin.second(sp, net, minute_start=j == 0) for j in range(60)]
        assert [g.soe_after_j for g in got] == want
        assert sum(g.short_j for g in got) == unserved
        assert breaches == 0
        shorts += unserved
    assert twin.kernel_runs == len(script)
    if spec is SMALL:
        assert shorts > 0, "the small battery empties, so the shortfall path is exercised"
        assert twin.soe_j > 0, "and it charges again"


# --------------------------------------------------------------------------- 2


def test_the_kernels_own_import_limit_policy_equals_the_twin_following_its_setpoints() -> None:
    spec = ts.bess_spec(usable=200, discharge_kw=300, charge_kw=200, initial=50)
    load = ts.load_array()
    net = load.facility_w[1, 960:990]
    limit = int(np.median(net))
    m = net.size
    k = KernelInputs(
        net_w=net[None, :].astype(np.int64),
        permit_w=np.full(m, limit, dtype=np.int64),
        restricted=np.zeros(m, dtype=bool),
        cover_cap_w=np.full(m, spec.discharge_w, dtype=np.int64),
        floor_j=np.zeros(m, dtype=np.int64),
        cover_floor_j=np.zeros(m, dtype=np.int64),
        merchant_w=np.zeros(m, dtype=np.int64),
        merchant_floor_j=np.zeros(m, dtype=np.int64),
        charge_target_j=np.full(m, spec.usable_j, dtype=np.int64),
        charge_cap_w=np.full(m, spec.charge_w, dtype=np.int64),
        thermal_active=np.zeros(m, dtype=bool),
        fine=np.ones(m, dtype=bool),
        eta_discharge=spec.eta_discharge,
        eta_charge=spec.eta_charge,
        soe0_j=spec.soe0_j,
        event_start_s=(),
        event_end_s=(),
        recharge_metrics=False,
        unserved_threshold_w=1,
    )
    tr = Trace()
    out = run_kernel(k, skip=False, trace=tr)
    want = [int(a[0]) for a in tr.soe_after]
    twin = BessTwin(spec)
    got: list[int] = []
    short = 0
    for n in net.tolist():
        if n > limit:
            sp = -min(n - limit, spec.discharge_w)
        else:
            sp = min(limit - n, spec.charge_w)
        for j in range(60):
            sec = twin.second(sp, n, minute_start=j == 0)
            got.append(sec.soe_after_j)
            short += sec.short_j
    assert len(want) == 60 * m
    assert got == want
    assert short == int(out.unserved_j[0])
    assert out.floor_breaches == 0
    assert min(got) < spec.soe0_j < max(got), "the half-hour both discharges and charges"


# --------------------------------------------------------------------------- 3


def test_rewriting_the_same_setpoint_mid_minute_changes_nothing() -> None:
    a, b = ts.twin(), ts.twin()
    for t in (a, b):
        t.write("bess_setpoint", -500_000)
    a.advance(150_000)
    b.advance(80_500)
    b.write("bess_setpoint", -500_000)
    b.advance(69_500)
    assert a.seconds == b.seconds
    assert a.battery.kernel_runs == b.battery.kernel_runs == 3


def test_a_new_setpoint_mid_minute_equals_two_chained_kernel_runs() -> None:
    twin = ts.twin()
    spec = twin.config.bess
    twin.write("bess_setpoint", -500_000)
    twin.advance(80_500)
    twin.write("bess_setpoint", 300_000)  # at 80.5 s: in force from 81 s
    twin.advance(39_500)
    secs = twin.seconds
    assert [s.setpoint_w for s in secs[60:]] == [-500_000] * 21 + [300_000] * 39
    net = secs[60].facility_w
    first, _, _ = _kernel_minute(spec, -500_000, net, secs[59].bess.soe_after_j)
    second, _, _ = _kernel_minute(spec, 300_000, net, first[20])
    want = first[:21] + second[:39]
    assert [s.bess.soe_after_j for s in secs[60:]] == want


# --------------------------------------------------------------------------- 4


def test_the_meter_reads_facility_plus_battery_and_the_registers_agree() -> None:
    twin = ts.twin(spec=SMALL)
    spec = twin.config.bess
    dn, dd = spec.eta_discharge
    cn, cd = spec.eta_charge
    twin.write("import_limit", 1_000_000)
    for sp in (-700_000, -1_000_000, 0, 450_000):
        twin.write("bess_setpoint", sp)
        twin.advance(60_000)
    assert twin.exceedance_s > 0, "the site imports above 1 MW: reported, not enforced"
    for s in twin.seconds:
        b = s.bess
        assert s.import_w == s.facility_w + b.power_w
        assert b.ac_j == 0 or b.grid_j == 0
        taken = b.soe_before_j - b.soe_after_j
        if b.ac_j:
            assert b.ac_j * dd <= taken * dn, "no more AC out than the efficiency allows"
            assert b.ac_j + b.short_j == -max(s.setpoint_w, -spec.discharge_w)
        if b.grid_j:
            assert -taken * cd <= b.grid_j * cn, "no more stored than the efficiency allows"
            assert b.grid_j <= min(s.setpoint_w, spec.charge_w)
    bank = twin.bank()
    read = {key: f() for key, f in bank.readers.items()}
    assert read[(1, "input", 4)] == twin.battery.soe_j // 3600
    assert read[(1, "input", 8)] == twin.battery.soe_j * 10_000 // spec.usable_j
    assert read[(1, "input", 0)] == twin.seconds[-1].bess.power_w
    assert read[(2, "input", 0)] == twin.seconds[-1].import_w
    assert read[(1, "holding", 0)] == 450_000
    assert read[(1, "holding", 4)] == 1_000_000


def test_the_twin_refuses_what_is_not_synthetic_and_needs_an_explicit_draw() -> None:
    with pytest.raises(ValueError, match="SYNTHETIC"):
        ts.twin(site="SITE-ALPHA", org="ORG-ALPHA", load=ts.load_array("SYNTHETIC-X"))
    with pytest.raises(ValueError, match="own site"):
        ts.twin(load=ts.load_array("SYNTHETIC-ANOTHER-SITE"))
    with pytest.raises(ValueError, match="draw"):
        ts.twin(draw=2)
    with pytest.raises(ValueError, match="half-hour"):
        ts.twin(boot=ts.BOOT.replace(minute=10))
    a, b = ts.twin(draw=0), ts.twin(draw=1)
    a.advance(60_000)
    b.advance(60_000)
    assert a.seconds[0].facility_w != b.seconds[0].facility_w


# --------------------------------------------------------------------------- 5


def test_the_twin_takes_its_state_of_energy_only_from_the_kernel_trace() -> None:
    private = {"_dispatch", "_charge", "_ceil_div", "_active_minutes"}
    assigned: list[str] = []
    for path in sorted((ts.SRC / "gridq" / "site_twin").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "gridq.adequacy.kernel":
                assert not {a.name for a in node.names} & private, path.name
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) and t.attr == "soe_j":
                        assert not isinstance(node, ast.AugAssign), path.name
                        assigned.append(f"{path.name}: {ast.unparse(node)}")
    assert sorted(assigned) == [
        "electrical.py: self.soe_j = after",
        "electrical.py: self.soe_j = spec.soe0_j",
    ]
    src = (ts.SRC / "gridq" / "site_twin" / "electrical.py").read_text(encoding="utf-8")
    assert "after = seg.soe_after[seg.pos]" in src
    assert "soe_after=[int(a[0]) for a in trace.soe_after]" in src


# --------------------------------------------------------------------------- 6


def test_the_coarse_kernel_path_is_within_60_joules_per_active_minute() -> None:
    spec = ts.bess_spec(usable=200, discharge_kw=300, charge_kw=200, initial=50)
    net = ts.load_array().facility_w[0, 960:1020]
    limit = int(np.median(net))
    m = net.size

    def run(fine: bool) -> list[int]:
        k = KernelInputs(
            net_w=net[None, :].astype(np.int64),
            permit_w=np.full(m, limit, dtype=np.int64),
            restricted=np.zeros(m, dtype=bool),
            cover_cap_w=np.full(m, spec.discharge_w, dtype=np.int64),
            floor_j=np.zeros(m, dtype=np.int64),
            cover_floor_j=np.zeros(m, dtype=np.int64),
            merchant_w=np.zeros(m, dtype=np.int64),
            merchant_floor_j=np.zeros(m, dtype=np.int64),
            charge_target_j=np.full(m, spec.usable_j, dtype=np.int64),
            charge_cap_w=np.full(m, spec.charge_w, dtype=np.int64),
            thermal_active=np.zeros(m, dtype=bool),
            fine=np.full(m, fine, dtype=bool),
            eta_discharge=spec.eta_discharge,
            eta_charge=spec.eta_charge,
            soe0_j=spec.soe0_j,
            event_start_s=(),
            event_end_s=(),
            recharge_metrics=False,
            unserved_threshold_w=1,
        )
        tr = Trace()
        run_kernel(k, skip=False, trace=tr)
        per_step = [int(a[0]) for a in tr.soe_after]
        stride = 60 if fine else 1
        return per_step[stride - 1 :: stride]

    fine, coarse = run(True), run(False)
    twin = BessTwin(spec)
    twin_minutes: list[int] = []
    for n in net.tolist():
        sp = -min(n - limit, spec.discharge_w) if n > limit else min(limit - n, spec.charge_w)
        for j in range(60):
            twin.second(sp, n, minute_start=j == 0)
        twin_minutes.append(twin.soe_j)
    assert twin_minutes == fine
    full = spec.usable_j
    for i, (f, c) in enumerate(zip(fine, coarse, strict=True)):
        if 0 < f < full and 0 < c < full:
            assert abs(f - c) < 60 * (i + 1), (i, f, c)
