"""WP-UW-07 acceptance 2 — every energy band priced as itself; no mean of bands anywhere.

D105. A stack that quotes some bands but not others, or quotes labels with no time
windows, used to be priced at the **mean of the quoted bands** on every half-hour it could not
place: a price the site never quoted, and a spread the dispatch could harvest. The envelope
replaces it. A resolved half-hour carries its own band's rate on both sides; an unresolved one
charges at the highest quoted rate and credits discharge at the lowest, so no cycle earns spread
from a band nobody priced, and the bill there is a stated lower bound.

These tests hold that from the calendar, through the declared records, to a solved MILP.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.dispatch.contract import DispatchProblem
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.tariff_calendar import (
    IMPORT_RATE_WINDOWS_KEY,
    duos_band_labels,
    energy_objective_coefficients,
    energy_price_envelope,
    import_rate_assumptions,
)
from gridq.schemas import BandWindows, ClockWindow, TariffStack


def _week() -> pd.DatetimeIndex:
    return pd.date_range("2025-01-06 00:00", periods=7 * 48, freq="30min")


def _stack(import_rates: dict[str, float], **extra: object) -> TariffStack:
    return TariffStack(
        basis=SYNTHETIC_BASIS,
        provenance=SYNTHETIC_PROVENANCE,
        import_rates=import_rates,
        **extra,  # type: ignore[arg-type]
    )


def test_a_missing_band_is_unresolved_and_never_priced_at_the_mean() -> None:
    ts = _week()
    envelope, note = energy_price_envelope({"red": 0.40, "green": 0.10}, ts)
    assert envelope is not None
    labels = duos_band_labels(ts)
    red, amber, green = (labels == b for b in ("red", "amber", "green"))
    assert envelope.missing_bands == ("amber",)
    assert np.array_equal(envelope.unresolved, amber)
    # Each quoted band is priced as itself, on both sides.
    assert set(envelope.sell[red]) == set(envelope.buy[red]) == {0.40}
    assert set(envelope.sell[green]) == set(envelope.buy[green]) == {0.10}
    # The unpriced band charges at the highest quoted rate and credits at the lowest.
    assert set(envelope.buy[amber]) == {0.40}
    assert set(envelope.sell[amber]) == {0.10}
    mean = (0.40 + 0.10) / 2
    assert not np.isclose(envelope.sell, mean).any() and not np.isclose(envelope.buy, mean).any()
    assert note is not None and "['amber']" in note and "LOWER BOUND" in note
    sell, buy = energy_objective_coefficients(envelope)
    assert buy is not None and np.array_equal(sell, envelope.sell)


def test_a_full_red_amber_green_stack_is_spread_neutral_and_keeps_the_single_price_term() -> None:
    envelope, note = energy_price_envelope({"red": 0.4, "amber": 0.2, "green": 0.1}, _week())
    assert envelope is not None and envelope.spread_neutral and note is None
    assert np.array_equal(envelope.buy, envelope.sell)
    _sell, buy = energy_objective_coefficients(envelope)
    assert buy is None, "a fully resolved stack must build the historic single-price objective"


def test_peak_offpeak_with_no_windows_is_unresolved_not_mapped_onto_the_duos_red_band() -> None:
    """Inventing the timing invents the spread as surely as inventing the price."""
    envelope, note = energy_price_envelope({"peak": 0.4, "offpeak": 0.1}, _week())
    assert envelope is not None and envelope.window_source == "none"
    assert envelope.unresolved.all()
    assert set(envelope.sell) == {0.1} and set(envelope.buy) == {0.4}
    assert note is not None and "no time windows" in note


def test_the_stacks_own_windows_price_each_band_as_itself() -> None:
    ts = _week()
    windows = {
        "peak": BandWindows(weekday_windows=(ClockWindow(start="07:00", end="19:00"),)),
        "offpeak": BandWindows(),  # the residual: all other times
    }
    stack = _stack({"peak": 0.4, "offpeak": 0.1}, import_rate_windows=windows)
    envelope, note = energy_price_envelope(
        stack.import_rates, ts, import_rate_windows=stack.import_rate_windows
    )
    assert envelope is not None and envelope.window_source == "stack"
    assert envelope.spread_neutral and note is None
    peak = (ts.dayofweek < 5) & (ts.hour >= 7) & (ts.hour < 19)
    assert set(envelope.sell[peak]) == {0.4} and set(envelope.sell[~peak]) == {0.1}
    assert import_rate_assumptions(stack) == [], "own windows need no declared record"


def test_own_windows_must_cover_exactly_the_quoted_labels_without_overlap() -> None:
    with pytest.raises(ValueError, match="must equal the import_rates labels"):
        _stack({"peak": 0.4, "offpeak": 0.1}, import_rate_windows={"peak": BandWindows()})
    overlapping = {
        "a": BandWindows(weekday_windows=(ClockWindow(start="07:00", end="12:00"),)),
        "b": BandWindows(weekday_windows=(ClockWindow(start="11:30", end="19:00"),)),
    }
    with pytest.raises(ValueError, match="overlap"):
        _stack({"a": 0.4, "b": 0.1}, import_rate_windows=overlapping)


@pytest.mark.parametrize(
    ("rates", "keys"),
    [
        ({"red": 0.4, "green": 0.1}, ["tariff_stack.import_rates.amber"]),
        ({"day": 0.3, "night": 0.1}, [IMPORT_RATE_WINDOWS_KEY]),
        ({"day": 0.3}, []),  # flat: nothing to time
        ({"red": 0.4, "amber": 0.2, "green": 0.1}, []),  # timed by the area's sourced calendar
        ({"peak": 0.4, "offpeak": 0.1}, [IMPORT_RATE_WINDOWS_KEY]),
    ],
)
def test_every_unpriced_band_or_undeclared_timing_is_a_declared_record(
    rates: dict[str, float], keys: list[str]
) -> None:
    records = import_rate_assumptions(_stack(rates), release="gb_v0_1")
    assert [r.key for r in records] == keys
    for record in records:
        assert record.derived_on_release == "gb_v0_1"
        assert record.confirmation_ask
        assert "mean of the quoted bands" not in record.value


# ------------------------------------------------------------------ the solved MILP


@pytest.fixture(scope="module")
def solved_missing_band(tmp_path_factory: pytest.TempPathFactory):
    path: Path = tmp_path_factory.mktemp("envelope") / "hh.csv"
    rows, _ = write_hh_csv(path)
    site = multi_rate_site(path, rows=rows)
    site = site.model_copy(
        update={
            "tariff_stack": site.tariff_stack.model_copy(
                update={"import_rates": {"red": 0.40, "green": 0.10}}
            )
        }
    )
    library = load_assumptions("gb_v0_1")
    solver = MilpDispatchSolver()
    result = solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=library,
            power_kw=200.0,
            energy_kwh=400.0,
            duration_hours=2.0,
            constraints=[],
        )
    )
    return result, solver.last_schedule()


def test_the_milp_objective_carries_no_mean_of_bands(solved_missing_band) -> None:
    _result, schedule = solved_missing_band
    assert schedule is not None
    sell = schedule.energy_price_gbp_per_kwh
    buy = schedule.energy_buy_price_gbp_per_kwh
    assert sell is not None and buy is not None, "a missing band must reach the objective"
    assert set(np.round(sell, 10)) <= {0.40, 0.10}
    assert set(np.round(buy, 10)) <= {0.40, 0.10}
    assert (buy >= sell).all()


def test_the_reported_floor_is_still_the_objectives_own_term(solved_missing_band) -> None:
    """F-02 holds with the envelope: arbitrage + DUoS == the priced floor the MILP maximised."""
    _result, schedule = solved_missing_band
    total = schedule.arbitrage_value_gbp + schedule.duos_avoidance_value_gbp
    assert total == pytest.approx(schedule.priced_floor_terms_gbp, abs=0.011)


def test_no_cycle_earns_spread_from_the_unpriced_band(solved_missing_band) -> None:
    """The mutation this pins: price amber at the mean (25p) and the optimiser discharges there.

    A discharge in amber credited at the 25p mean beats a 10p green charge even after losses, so a
    mean-of-bands objective sends a second daily cycle into the band nobody priced. Under the
    envelope a discharge there is credited at the lowest quoted rate (10p) and a charge there pays
    the highest (40p), so neither ever pays for its own losses and the unpriced band stays idle.
    """
    _result, schedule = solved_missing_band
    unresolved = schedule.energy_buy_price_gbp_per_kwh > schedule.energy_price_gbp_per_kwh
    assert unresolved.any()
    assert not (schedule.discharge_kw[unresolved] > 1e-6).any()
    assert not (schedule.charge_kw[unresolved] > 1e-6).any()
