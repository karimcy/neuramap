"""WP-THERM-03 unit and conformance tests: the IC-11 output (acceptance 1), the certificate's
monotonicity, provenance (stand-ins force a synthetic surface), unknowns, the cited presets, the
psychrometrics and the OPS-01 dossier mapping. Every plant figure is the SYNTHETIC fixture's.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import _thermal_fixture as fx
import numpy as np
import pytest

from gridq.contracts._base import Unknown, not_applicable, unknown
from gridq.contracts.thermal_flex import (
    AshraeClass,
    AshraeLimit,
    AvailabilityStatus,
    Flex,
    FlexInstruction,
    InstructionIssuer,
    LimitSource,
    MeasurementPoint,
    ModelledBasis,
    ResponseReason,
    TemperatureBand,
    ThermalFlexAvailability,
    TimeBand,
    UnavailableReason,
    ZeroFlex,
    availability_ref,
    check_instruction,
    query,
)
from gridq.ops.dossier import build_dossier
from gridq.ops.dossier_inputs import default_register
from gridq.ops.dossier_model import CoolingPlant, CoolingTopologyValue
from gridq.thermal import PlantDataRequest, build_surface, thermal_plant_model
from gridq.thermal.certificate import (
    _REASONS,
    MAX_DURATION_MIN,
    MIN_DURATION_MIN,
    _strategy_bounds,
    _sweep,
)
from gridq.thermal.inputs import UnavailableWindow
from gridq.thermal.plant import Resolved, Unresolved, resolve
from gridq.thermal.psychro import (
    ceiling_for_min_rh,
    dew_point_c,
    floor_for_max_rh,
    relative_humidity_percent,
    saturation_vapour_pressure_hpa,
)
from gridq.thermal.site_requests import (
    ALL_KEYS,
    PARAMETER_ASKS,
    assess_topology,
    request_for_site,
)
from gridq.thermal.sources import (
    ASHRAE_2011,
    ASHRAE_A1_ALLOWABLE,
    ASHRAE_A1_RECOMMENDED,
    MAGNUS,
    WMO_JCOMM_TR63,
    compare_to_preset,
)
from gridq.thermal.surface import METHOD_VERSION, MODEL, staircase

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _surface() -> ThermalFlexAvailability:
    return fx.built().surface


def _flex_cells(s: ThermalFlexAvailability) -> list[tuple[str, str, Flex]]:
    return [
        (c.season_id, c.band_id, c.availability)
        for c in s.cells
        if isinstance(c.availability, Flex)
    ]


# ------------------------------------------------------------- acceptance 1: IC-11 conformance


def test_the_surface_is_a_valid_ic11_v1_1_0_record_and_reparses_to_itself() -> None:
    s = _surface()
    assert s.contract == "IC-11" and s.contract_version == "1.1.0"
    again = ThermalFlexAvailability.model_validate_json(s.model_dump_json(indent=2))
    assert again == s and again.content_hash() == s.content_hash()
    assert s.record_class == "synthetic_fixture" and s.site_id == fx.SITE_ID
    assert isinstance(s.evidence, ModelledBasis) and s.evidence.model_version == METHOD_VERSION
    assert s.evidence.model.startswith(MODEL + " [stand-ins: ")
    assert s.clock == "Europe/London"
    assert s.limits.binding_source is LimitSource.CUSTOMER_SLA
    assert (s.limits.binding.min_tenths_c, s.limits.binding.max_tenths_c) == (180, 260)
    assert s.limits.chiller_min_run_min == fx.RAW["min_run"]
    assert s.limits.chiller_min_off_min == fx.RAW["min_off"]
    assert _flex_cells(s), "the fixture plant declares flex"
    for _, _, a in _flex_cells(s):
        for p in a.curve:
            assert p.duration_min >= MIN_DURATION_MIN and p.reduction_kw >= 1
            assert p.rebound.energy_kwh >= 1 and p.rebound.recovery_min >= 1


def test_the_build_is_deterministic() -> None:
    again = build_surface(fx.plant(), fx.drivers(), fx.request())
    assert again.surface.content_hash() == _surface().content_hash()


def test_every_local_minute_and_month_reads_back_the_cell_curve() -> None:
    s = _surface()
    cells = {(sid, bid): a for sid, bid, a in _flex_cells(s)}
    for month in range(1, 13):
        season = next(x for x in s.seasons if month in x.months)
        for minute in range(0, 1440, 15):
            band = next(b for b in s.bands if b.start_minute <= minute < b.end_minute)
            flex = cells[(season.season_id, band.band_id)]
            for duration in (15, 60, 240):
                ans = query(s, month, minute, duration)
                assert ans.status is AvailabilityStatus.FLEX
                expect = next((p.reduction_kw for p in flex.curve if p.duration_min >= duration), 0)
                assert ans.deliverable_kw == expect


def _instruction(s: ThermalFlexAvailability, start: datetime, d: int, kw: int) -> FlexInstruction:
    return FlexInstruction(
        instruction_id="SYNTHETIC-THERM03-I1",
        site_id=s.site_id,
        availability=availability_ref(s),
        issued_at=start - timedelta(minutes=30),
        issuer_role=InstructionIssuer.NEURA_DISPATCH,
        issuer_id="synthetic-test",
        on_behalf_of=not_applicable("synthetic test: the site's own cost"),
        stream=not_applicable("synthetic test: the site's own cost"),
        reduction_kw=kw,
        start=start,
        duration_min=d,
        ack_deadline=start - timedelta(minutes=10),
    )


def test_an_instruction_at_the_declared_kw_fits_and_one_more_kw_exceeds() -> None:
    s = _surface()
    start = datetime(2027, 1, 14, 2, 0, tzinfo=UTC)  # winter, GMT
    ans = query(s, 1, 120, 60)
    assert isinstance(ans.deliverable_kw, int) and ans.deliverable_kw >= 1
    ok = check_instruction(s, _instruction(s, start, 60, ans.deliverable_kw))
    assert ok.ok, ok.reasons
    over = check_instruction(s, _instruction(s, start, 60, ans.deliverable_kw + 1))
    assert over.reasons == (ResponseReason.EXCEEDS_DECLARED_AVAILABILITY,)


_CONSUMER = """
import json, sys
from gridq.contracts.thermal_flex import ThermalFlexAvailability, query
s = ThermalFlexAvailability.model_validate_json(open(sys.argv[1], encoding="utf-8").read())
grid = [query(s, m, b.start_minute, 15).deliverable_kw for m in (1, 7) for b in s.bands]
mods = sorted(
    m for m in sys.modules if m.startswith("gridq.") and not m.startswith("gridq.contracts")
)
print(json.dumps({"hash": s.content_hash(), "grid": grid, "engine": mods}))
"""


def test_a_consumer_reads_the_surface_with_the_contract_package_alone(tmp_path: Path) -> None:
    """The NEW-IC-11 output is what a future interface-level adapter would be conformance-tested
    against (note 35): a fresh interpreter reads it with ``gridq.contracts`` only."""
    s = _surface()
    path = tmp_path / "surface.synthetic.json"
    path.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    res = subprocess.run(
        [sys.executable, "-c", _CONSUMER, str(path)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["hash"] == s.content_hash() and out["engine"] == []
    assert out["grid"] == [
        query(s, m, b.start_minute, 15).deliverable_kw for m in (1, 7) for b in s.bands
    ]


# ------------------------------------------------------------- the certificate is monotone


def test_the_frontier_is_monotone_in_kw_and_duration() -> None:
    """D-7 (1): a longer hold at a larger kW is never certified when a smaller one fails, and a
    longer hold never certifies more kW."""
    cert = fx.built().certificate
    assert cert is not None
    envs = [cert.envelopes[i] for i in cert.certified]
    for name, (bounds, _) in _strategy_bounds(cert.plant, envs).items():
        kgrid = np.arange(1, 200, 1, dtype=np.int64)
        dmax, _ = _sweep(cert.plant, envs, bounds, kgrid)
        assert (np.diff(dmax, axis=1) <= 0).all(), name
        kw = cert.strategy_kw[name]
        assert (np.diff(kw, axis=1) <= 0).all(), name
        assert kw.shape[1] == MAX_DURATION_MIN + 1


def _binding_checks(variant: str) -> set[str]:
    """The checks that end some certified hold on a fixture variant (any strategy)."""
    cert = fx.built(variant).certificate
    assert cert is not None
    envs = [cert.envelopes[i] for i in cert.certified]
    kgrid = np.arange(1, 300, 1, dtype=np.int64)
    seen: set[str] = set()
    for bounds, _ in _strategy_bounds(cert.plant, envs).values():
        _, why = _sweep(cert.plant, envs, bounds, kgrid)
        seen |= {_REASONS[i] for i in np.unique(why) if i >= 0}
    return seen


def test_every_certificate_check_binds_on_some_fixture() -> None:
    """The mutation checks are meaningful only where a check ends a hold: the base fixture binds
    the plant floor and the hall band, the tight one the rate, loop band and recovery."""
    base, tight = _binding_checks("base"), _binding_checks("tight")
    assert {"plant_floor", "hall_band"} <= base
    assert {"rate", "loop_band", "recovery"} <= tight
    assert base | tight == set(_REASONS)
    assert fx.built("tight").surface.record_class == "synthetic_fixture"


def test_the_staircase_keeps_the_corners_and_never_overstates() -> None:
    curve = np.zeros(MAX_DURATION_MIN + 1, dtype=np.int64)
    curve[:61] = 50
    curve[61:241] = 30
    curve[241:601] = 10
    pts = staircase(curve)
    assert pts == [(60, 50), (240, 30), (600, 10)]
    for d, k in pts:
        assert curve[d] >= k


# ------------------------------------------------------------- provenance and unknowns


def test_stand_ins_on_a_real_site_id_give_a_synthetic_surface_and_a_data_request() -> None:
    res = build_surface(fx.plant(site_id="harwell_campus_k16w304527"), fx.drivers(), fx.request())
    assert res.surface.record_class == "synthetic_fixture"
    assert res.surface.site_id.startswith("SYNTHETIC-")
    assert res.surface.site_id != "harwell_campus_k16w304527"
    assert res.data_request is not None
    assert set(res.data_request.needed) >= {"chiller_count", "drivers"}
    assert "stand-ins" in res.surface.evidence.model  # type: ignore[union-attr]


def test_an_unknown_parameter_gives_unknown_cells_and_asks_for_it() -> None:
    inputs = fx.plant(unit_capacity_kwth=unknown("not in the dossier"))
    assert isinstance(resolve(inputs), Unresolved)
    res = build_surface(inputs, fx.drivers(), fx.request())
    assert all(isinstance(c.availability, Unknown) for c in res.surface.cells)
    assert isinstance(res.surface.cooling_electrical_capacity_kw, Unknown)
    assert res.data_request is not None and "unit_capacity_kwth" in res.data_request.needed
    assert res.certificate is None


def test_a_missing_driving_temperature_gives_unknown_cells() -> None:
    drv = replace(fx.drivers(), temperature_lo_c=None, temperature_hi_c=None)
    res = build_surface(fx.plant(), drv, fx.request())
    assert all(isinstance(c.availability, Unknown) for c in res.surface.cells)


def test_a_wet_bulb_plant_refuses_a_dry_bulb_series() -> None:
    with pytest.raises(ValueError, match="wet_bulb"):
        build_surface(fx.plant(evaporative_heat_rejection=True), fx.drivers(), fx.request())


def test_too_few_days_in_a_month_gives_unknown_cells() -> None:
    drv = fx.drivers()
    lo = drv.heat_lo_kwth.copy()
    lo[: 20 * 48] = np.nan  # most of January 2025 missing: fewer than 20 days per slot
    res = build_surface(fx.plant(), replace(drv, heat_lo_kwth=lo), fx.request())
    winter = [c for c in res.surface.cells if c.season_id == "winter"]
    summer = [c for c in res.surface.cells if c.season_id == "summer"]
    assert all(isinstance(c.availability, Unknown) for c in winter)
    assert any(isinstance(c.availability, Flex) for c in summer)


def test_an_unavailable_window_is_carried_as_unavailable() -> None:
    window = UnavailableWindow(
        months=(7,),
        start_minute=600,
        end_minute=720,
        reason_code=UnavailableReason.MAINTENANCE,
        detail="synthetic maintenance window",
    )
    res = build_surface(fx.plant(), fx.drivers(), fx.request(unavailable=(window,)))
    assert query(res.surface, 7, 630, 15).status is AvailabilityStatus.UNAVAILABLE
    assert query(res.surface, 1, 630, 15).status is AvailabilityStatus.FLEX


def test_a_pinned_band_grid_is_used_as_given() -> None:
    bands = (
        TimeBand(band_id="night", start_minute=0, end_minute=420),
        TimeBand(band_id="day", start_minute=420, end_minute=1440),
    )
    res = build_surface(fx.plant(), fx.drivers(), fx.request(bands=bands))
    assert [(b.start_minute, b.end_minute) for b in res.surface.bands] == [(0, 420), (420, 1440)]
    # A coarser band can only lose kW: its cell is the minimum over its slots.
    fine = _surface()
    for month in (1, 7):
        for minute in (0, 600, 1200):
            coarse_kw = query(res.surface, month, minute, 15).deliverable_kw
            fine_kw = query(fine, month, minute, 15).deliverable_kw
            assert isinstance(coarse_kw, int) and isinstance(fine_kw, int)
            assert coarse_kw <= fine_kw


def test_an_unusable_power_curve_gives_unknown_not_flex() -> None:
    from gridq.thermal.inputs import CoefficientSet

    bad = tuple(
        CoefficientSet(kind="coefficients", values=(c[0], -0.5, c[2], c[3]), provenance=fx.SYN)
        for c in fx.RAW["coeffs"]
    )
    res = build_surface(fx.plant(regimes=bad), fx.drivers(), fx.request())
    assert all(isinstance(c.availability, Unknown) for c in res.surface.cells)
    assert all("power curve" in c.availability.reason for c in res.surface.cells)  # type: ignore[union-attr]


def test_a_too_small_plant_declares_zero_flex_with_a_reason() -> None:
    res = build_surface(
        fx.plant(min_part_load_ratio=_syn(0.95), standby_running=_syn(0)),
        fx.drivers(),
        fx.request(),
    )
    for c in res.surface.cells:
        assert isinstance(c.availability, ZeroFlex)
        assert "minimum part load" in c.availability.reason


def _syn(v: float) -> object:
    from gridq.thermal.inputs import stated

    return stated(v, fx.SYN)


def test_resolve_reports_every_stand_in() -> None:
    res = resolve(fx.plant())
    assert isinstance(res, Resolved)
    assert {"chiller_count", "unit_capacity_kwth", "hall_to_loop_ua_kw_per_k"} <= set(res.stand_ins)


# ------------------------------------------------------------- cited presets and psychrometrics


def test_the_a1_presets_carry_the_2011_table_figures_and_their_source() -> None:
    r, a = ASHRAE_A1_RECOMMENDED, ASHRAE_A1_ALLOWABLE
    assert (r.min_tenths_c, r.max_tenths_c) == (180, 270)
    assert (a.min_tenths_c, a.max_tenths_c) == (150, 320)
    assert r.rate_tenths_c_per_hour("disk") == 200 and r.rate_tenths_c_per_hour("tape") == 50
    for src in (ASHRAE_2011, WMO_JCOMM_TR63):
        assert src.url.startswith("https://") and len(src.sha256) == 64
    lim = r.ic11("disk")
    assert lim.ashrae_class is AshraeClass.A1 and lim.temperature.point is MeasurementPoint.IT_INLET
    assert isinstance(a.humidity(), Unknown), "a minimum RH cannot be carried in IC-11 v1.1.0"


def _limit(cls: AshraeClass, lo: int, hi: int) -> AshraeLimit:
    base = ASHRAE_A1_RECOMMENDED.ic11("disk")
    band = TemperatureBand(point=MeasurementPoint.IT_INLET, min_tenths_c=lo, max_tenths_c=hi)
    return base.model_copy(update={"ashrae_class": cls, "temperature": band})


def test_a_declared_class_band_is_compared_with_the_preset() -> None:
    assert compare_to_preset(_limit(AshraeClass.A1, 180, 270)) == "matches"
    assert compare_to_preset(_limit(AshraeClass.A1, 190, 260)) == "tighter"
    assert compare_to_preset(_limit(AshraeClass.A1, 170, 270)) == "looser"
    assert compare_to_preset(_limit(AshraeClass.H1, 180, 220)) == "not_comparable"


def test_magnus_values_and_inverses() -> None:
    assert (MAGNUS.a_hpa, MAGNUS.b, MAGNUS.c_c) == (6.112, 17.62, 243.12)
    assert saturation_vapour_pressure_hpa(0.0) == pytest.approx(6.112)
    # 6.112 exp(17.62 x 20 / 263.12), evaluated by hand: 23.33 hPa.
    assert saturation_vapour_pressure_hpa(20.0) == pytest.approx(23.33, abs=0.005)
    assert dew_point_c(saturation_vapour_pressure_hpa(12.5)) == pytest.approx(12.5)
    assert relative_humidity_percent(20.0, 20.0) == pytest.approx(100.0)
    t = floor_for_max_rh(15.0, 60.0)
    assert relative_humidity_percent(t, 15.0) == pytest.approx(60.0)
    t = ceiling_for_min_rh(5.5, 8.0)
    assert relative_humidity_percent(t, 5.5) == pytest.approx(8.0)


@pytest.mark.parametrize("t", [-45.1, 60.1])
def test_magnus_is_refused_outside_its_validity_range(t: float) -> None:
    with pytest.raises(ValueError, match="validity range"):
        saturation_vapour_pressure_hpa(t)


# ------------------------------------------------------------- OPS-01 dossier mapping


@pytest.mark.parametrize("member", list(CoolingPlant))
def test_the_dossier_topology_mapping_is_total(member: CoolingPlant) -> None:
    out = assess_topology(
        CoolingTopologyValue(kind="cooling_topology", plant=(member,), description="synthetic")
    )
    assert out.status in ("supported", "needs_evidence", "out_of_demo_scope", "no_plant")


def test_a_supported_dossier_topology_maps_onto_ic11() -> None:
    plant = tuple(sorted((CoolingPlant.AIR_COOLED_CHILLERS, CoolingPlant.CHILLED_WATER_CRAH)))
    out = assess_topology(
        CoolingTopologyValue(kind="cooling_topology", plant=plant, description="synthetic")
    )
    assert out.status == "supported"


def test_every_real_candidate_site_gets_a_data_request_not_a_surface(tmp_path: Path) -> None:
    dossier = build_dossier(
        default_register(tmp_path / "none.json"), as_of=date(2026, 11, 26), evidence_root=tmp_path
    )
    assert dossier.sites
    for site in dossier.sites:
        out = thermal_plant_model(site)
        assert isinstance(out, PlantDataRequest)
        assert set(out.needed) == set(ALL_KEYS)
        own = {a.request for a in out.asks}
        assert {r for r, *_ in PARAMETER_ASKS} <= own
        if out.topology.status == "unknown":
            prereq = {p for a in out.asks for p in a.prerequisites}
            assert prereq, "the topology asks come first"
        assert out == request_for_site(site)


def test_a_plant_with_stand_ins_never_becomes_a_registered_site_surface(tmp_path: Path) -> None:
    dossier = build_dossier(
        default_register(tmp_path / "none.json"), as_of=date(2026, 11, 26), evidence_root=tmp_path
    )
    site = dossier.sites[0]
    out = thermal_plant_model(site, (fx.plant(site_id=site.site_id), fx.drivers(), fx.request()))
    assert isinstance(out, PlantDataRequest)
    with pytest.raises(ValueError, match="another site"):
        thermal_plant_model(site, (fx.plant(), fx.drivers(), fx.request()))


# ------------------------------------------------------------- clean room


def test_the_package_imports_no_thermaflex_code() -> None:
    pkg = SRC / "gridq" / "thermal"
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert "thermaflex" not in n.lower(), (path.name, n)
                assert n.split(".")[0] in {
                    "gridq",
                    "numpy",
                    "pydantic",
                    "math",
                    "hashlib",
                    "json",
                    "dataclasses",
                    "datetime",
                    "typing",
                    "zoneinfo",
                    "collections",
                    "__future__",
                    "functools",
                    "itertools",
                    "enum",
                }, (path.name, n)
