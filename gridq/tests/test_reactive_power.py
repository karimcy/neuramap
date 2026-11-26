"""WP-UW-07 acceptance 4 — reactive power is a modelled quantity with a variable and a charge.

D106. When the site's half-hourly file meters both reactive channels, the dispatch carries
``c_t``, the kVAr the inverter supplies against the site's own reactive, inside the inverter's
inscribed P-Q octagon; the connection is held to ``min(MIC, ASC)`` in kVA as an inscribed polygon;
and, with a published CDCM excess reactive rate on the stack, the chargeable kVArh enters the
objective as a cost. The unity-power-factor warning is retired on exactly those runs. Without the
channels nothing is built and the run is what it was, warning included.

The reactive series here is CONSTRUCTED (a test input, like every load series in these tests); the
rate is ENWL's own published 2026/27 CDCM excess reactive charge, read from the fixture.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tests.test_hv_tariff_model as published
from tests._milp_support import multi_rate_site, write_hh_csv

from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.ingestion import load_half_hourly_csv
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.schemas import SiteModel, TariffProvenance
from gridq.tariffs.hv_tariff_model import chargeable_reactive_kvarh

_TAN_PF_085 = math.tan(math.acos(0.85))  # kVAr per kW of a constructed 0.85-lagging site
_UNITY = "UNITY POWER FACTOR ASSUMED"
_MODELLED = "REACTIVE MODELLED"


def _write(path: Path, *, channels: tuple[str, ...] = ("import", "export"), nan_at=None) -> int:
    rows, ts = write_hh_csv(path)
    df = pd.read_csv(path)
    kw = df["import_kw"].to_numpy(dtype=float)
    if "import" in channels:
        ri = kw * _TAN_PF_085 * 0.5  # kVArh per half-hour
        if nan_at is not None:
            ri = ri.copy()
            ri[nan_at] = np.nan
        df["Reactive_Import_kVArh"] = ri  # an alias, with the case a meter export uses
    if "export" in channels:
        df["kvarh_export"] = 0.0
    df.to_csv(path, index=False)
    return rows


def _site(path: Path, rows: int, *, rate: bool = True, mic_kw: float = 500.0) -> SiteModel:
    site = multi_rate_site(path, rows=rows, mic_kw=mic_kw)
    stack = site.tariff_stack
    if rate:
        tariff = published._schedule_tariff(*published._SITE_SPECIFIC[0])
        stack = stack.model_copy(
            update={
                "duos_red_rate": None,
                "network_tariff": tariff,
                "provenance": TariffProvenance(
                    commodity="assumed", network="published_for_site", levies="assumed"
                ),
            }
        )
    return site.model_copy(update={"tariff_stack": stack})


def _solve(site: SiteModel, library=None, *, power: float = 200.0):
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=library or load_assumptions("gb_v0_1"),
            power_kw=power,
            energy_kwh=power * 2.0,
            duration_hours=2.0,
            constraints=[],
        )
    )
    return result, solver.last_schedule(), solver.last_customer_bill()


@pytest.fixture(scope="module")
def modelled(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("reactive") / "hh.csv"
    rows = _write(path)
    return _solve(_site(path, rows))


def test_the_loader_reads_both_reactive_channels_under_their_aliases(tmp_path: Path) -> None:
    path = tmp_path / "hh.csv"
    _write(path)
    df = load_half_hourly_csv(path)
    assert {"reactive_import_kvarh", "reactive_export_kvarh"} <= set(df.columns)


def test_reactive_has_a_variable_and_a_charge_and_the_unity_warning_is_retired(modelled) -> None:
    result, schedule, bill = modelled
    assert schedule is not None and bill is not None
    assert schedule.site_reactive_kvar is not None
    assert schedule.reactive_compensation_kvar is not None, "the dispatch variable c_t"
    assert schedule.reactive_rate_gbp_per_kvarh is not None, "the published rate prices it"
    assert any(_MODELLED in w for w in result.warnings)
    assert not any(_UNITY in w for w in result.warnings)
    assert "unity power factor" not in bill.basis
    assert bill.reactive_before_gbp is not None and bill.reactive_after_gbp is not None
    assert "Excess reactive power is billed" in bill.basis


def test_the_published_rate_is_the_cdcm_rows_own(modelled) -> None:
    _result, schedule, _bill = modelled
    tariff = published._schedule_tariff(*published._SITE_SPECIFIC[0])
    assert tariff.reactive_p_per_kvarh is not None
    assert schedule.reactive_rate_gbp_per_kvarh == float(tariff.reactive_p_per_kvarh) / 100.0


def test_the_bill_line_is_the_exact_lc14_rule_hand_recomputed(modelled) -> None:
    _result, s, bill = modelled
    dt = s.interval_hours
    before_kvarh = chargeable_reactive_kvarh(s.site_load_kw * dt, s.site_reactive_kvar * dt, None)
    expected = round(float(before_kvarh.sum()) * s.annual_factor * s.reactive_rate_gbp_per_kvarh, 2)
    assert bill.reactive_before_gbp == expected
    # Before: kVArh above 0.33 x kWh, at 0.85 PF (0.62 kVArh/kWh) every importing half-hour.
    assert expected > 0.0


def test_the_inverter_compensates_and_the_charge_falls(modelled) -> None:
    _result, s, bill = modelled
    assert float(s.reactive_compensation_kvar.max()) > 0.0
    assert bill.reactive_after_gbp < bill.reactive_before_gbp
    assert s.reactive_charge_avoidance_gbp == pytest.approx(
        bill.reactive_before_gbp - bill.reactive_after_gbp, abs=0.011
    )
    # The saving the bill reports includes the reactive line, once, on both sides.
    parts_before = (
        bill.energy_before_gbp
        + bill.duos_before_gbp
        + bill.levies_before_gbp
        + bill.standing_gbp
        + bill.capacity_gbp
        + bill.reactive_before_gbp
    )
    assert bill.annual_bill_before_gbp == pytest.approx(parts_before, abs=0.05)


def test_the_inverter_never_leaves_its_rated_circle(modelled) -> None:
    _result, s, _bill = modelled
    apparent = np.hypot(s.charge_kw + s.discharge_kw, s.reactive_compensation_kvar)
    assert float(apparent.max()) <= s.power_kw + 1e-6
    assert (s.reactive_compensation_kvar <= s.site_reactive_kvar + 1e-6).all()


def test_charging_never_breaches_the_connection_in_kva(tmp_path: Path) -> None:
    """A site near its MIC in kVA: no charging half-hour may lift √(AI² + r²) above the limit."""
    path = tmp_path / "hh.csv"
    rows = _write(path)
    mic = 380.0
    _result, s, _bill = _solve(_site(path, rows, mic_kw=mic))
    active = s.site_load_kw + s.charge_kw - s.discharge_kw
    residual = s.site_reactive_kvar - s.reactive_compensation_kvar
    charging = s.charge_kw > 1e-6
    assert charging.any()
    assert float(np.hypot(active, residual)[charging].max()) <= mic + 1e-6


def test_a_series_without_a_rate_models_kva_prices_nothing_and_says_so(tmp_path: Path) -> None:
    path = tmp_path / "hh.csv"
    rows = _write(path)
    result, s, bill = _solve(_site(path, rows, rate=False))
    assert s.site_reactive_kvar is not None and s.reactive_rate_gbp_per_kvarh is None
    assert not any(_UNITY in w for w in result.warnings)
    assert any("no reactive unit rate is declared" in w for w in result.warnings)
    assert bill.reactive_before_gbp is None and bill.reactive_after_gbp is None
    assert "reactive_before_gbp" not in bill.model_dump(), "an unmodelled line is absent, not £0"


def test_without_the_channels_nothing_is_built_and_the_warning_stands(tmp_path: Path) -> None:
    """Byte-identity of the no-series path: one channel only is NOT modelled, and the solve is the
    same solve as with no reactive column at all (same schedule ref, same unity warning)."""
    plain = tmp_path / "plain.csv"
    rows, _ = write_hh_csv(plain)
    one = tmp_path / "one.csv"
    _write(one, channels=("import",))
    r_plain, s_plain, b_plain = _solve(_site(plain, rows))
    r_one, s_one, b_one = _solve(_site(one, rows))
    for result, s, bill in ((r_plain, s_plain, b_plain), (r_one, s_one, b_one)):
        assert s.site_reactive_kvar is None and s.reactive_compensation_kvar is None
        assert any(_UNITY in w for w in result.warnings)
        assert "reactive_before_gbp" not in bill.model_dump()
    assert s_plain.ref() == s_one.ref()
    assert any("one reactive channel only" in w for w in r_one.warnings)


def test_a_partial_reactive_series_is_reported_and_not_filled(tmp_path: Path) -> None:
    path = tmp_path / "hh.csv"
    rows = _write(path, nan_at=[5, 6])
    result, s, _bill = _solve(_site(path, rows))
    assert s.site_reactive_kvar is None
    assert any("reactive channel is missing for 2 of" in w for w in result.warnings)
    assert any(_UNITY in w for w in result.warnings)


def test_a_released_power_factor_is_not_applied_on_top_of_a_metered_series(tmp_path: Path) -> None:
    from tests.test_milp_dispatch_physics import _assumption, _with_keys

    path = tmp_path / "hh.csv"
    rows = _write(path)
    library = _with_keys(
        load_assumptions("gb_v0_1"),
        "mic_asc_headroom",
        assumed_power_factor=_assumption(0.9, "fraction"),
    )
    result, s, _bill = _solve(_site(path, rows), library)
    assert s.site_reactive_kvar is not None
    assert any("is NOT applied: the site's metered reactive" in w for w in result.warnings)
    assert not any("de-rated to kVA at power factor" in w for w in result.warnings)
