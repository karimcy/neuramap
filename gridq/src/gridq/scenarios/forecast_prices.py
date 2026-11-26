"""Per-scenario forecast price-series loader — the seam that feeds the dispatch engine.

A scenario backed by a hash-pinned :class:`~gridq.scenarios.forecast_reference.ForecastReference`
exposes its price *series* (the actual £/MWh curve for that band) to the dispatch engine
**without** the scenario engine ever reading a price to fabricate a multiplier. The forecast
values are loaded from the pinned file, the content hash is re-checked against the reference (so
drift is caught, not ignored), and the points are filtered to the requested Low/Central/High band.

Nothing is invented: a band the forecast does not cover yields an **empty** series + a warning,
never a guessed curve; a file that has drifted from its recorded ``content_hash`` yields an empty,
``verified=False`` series + a loud re-pin warning. The forecast *values* never become GridQ
assumptions and never set a ``price_multiplier`` — multipliers stay explicit, sourced input
(see ``docs/scenario_engine.md`` §3/§4, decision D20).

The future half-hourly dispatch MILP consumes a :class:`ScenarioPriceSeries` resolved
from ``Scenario.forecast_ref``; the concrete on-``DispatchProblem`` field shape stays deferred to
that engine (``dispatch/contract.py``; D12/D17/D20). The (pandas-backed) ingestion loaders are
imported lazily inside the functions so they stay off the scenarios import path.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from gridq.scenarios.forecast_reference import ForecastReference, _load_forecast_file
from gridq.scenarios.models import ScenarioName
from gridq.schemas.provenance import GridQModel

if TYPE_CHECKING:
    from gridq.ingestion.market_forecasts import MarketForecastPoint

# Minimal, conservative label → band map. Deliberately NOT a guessed P10/P50/P90 (or
# bull/bear) mapping: the real Modo scenario labels are unconfirmed (OPEN_COMMENTS C012), so an
# unrecognised label is surfaced as a warning rather than silently mapped to a band.
_BAND_ALIASES: dict[ScenarioName, frozenset[str]] = {
    ScenarioName.LOW: frozenset({"low"}),
    ScenarioName.CENTRAL: frozenset({"central", "base"}),
    ScenarioName.HIGH: frozenset({"high"}),
}
_KNOWN_LABELS: frozenset[str] = frozenset().union(*_BAND_ALIASES.values())


class ForecastPricePoint(GridQModel):
    """One forecast price point for a single band (timestamp + £/MWh; price may be missing)."""

    timestamp: datetime
    price_gbp_per_mwh: float | None = None


class ScenarioPriceSeries(GridQModel):
    """The price *series* for one scenario band, resolved from a hash-pinned forecast.

    This is the input-side object the dispatch engine consumes per band; it is **never** baked
    onto the frozen ``DispatchResult`` / ``OptimisationResult``. ``verified`` is True only when the
    series was read from a file whose content hash still matches ``forecast_ref.content_hash`` and
    the band actually has points — an empty band or a drifted file is ``verified=False``.
    """

    scenario_name: ScenarioName
    forecast_ref: ForecastReference
    price_unit: str
    points: list[ForecastPricePoint] = Field(default_factory=list)
    verified: bool = False
    warnings: list[str] = Field(default_factory=list)

    @property
    def priced_point_count(self) -> int:
        """Number of points carrying a non-null price."""
        return sum(1 for p in self.points if p.price_gbp_per_mwh is not None)


def _norm(label: str | None) -> str:
    return str(label).strip().lower()


def _points_for_band(
    points: list[MarketForecastPoint], scenario_name: ScenarioName
) -> tuple[list[MarketForecastPoint], list[str]]:
    """Filter loaded forecast points to one band; warn (never fabricate) when a band is absent.

    - No scenario labels in the file at all → the single unlabelled curve IS Central; Low/High
      have no distinct series (empty + warning).
    - Labelled file → match the band's aliases; unrecognised labels are reported, never guessed.
    """
    labels_present = {_norm(p.scenario) for p in points if p.scenario is not None}

    if not labels_present:
        if scenario_name == ScenarioName.CENTRAL:
            return list(points), []
        return [], [
            f"forecast carries no scenario labels — only a single (Central) price curve is "
            f"available; the {scenario_name.value!r} band has no distinct series (not fabricated)."
        ]

    aliases = _BAND_ALIASES.get(scenario_name, frozenset())
    matched = [p for p in points if p.scenario is not None and _norm(p.scenario) in aliases]
    if matched:
        return matched, []

    unrecognised = sorted(labels_present - _KNOWN_LABELS)
    msg = f"forecast pins no points for the {scenario_name.value!r} band"
    if unrecognised:
        msg += (
            f"; unrecognised scenario label(s) {unrecognised} were not mapped to Low/Central/High "
            f"(no guessed mapping — confirm the Modo labels, OPEN_COMMENTS C012)"
        )
    return [], [msg + " — its price series is empty and is NOT fabricated."]


def _empty_series(
    ref: ForecastReference, scenario_name: ScenarioName, warnings: list[str]
) -> ScenarioPriceSeries:
    return ScenarioPriceSeries(
        scenario_name=scenario_name,
        forecast_ref=ref,
        price_unit=ref.price_unit,
        points=[],
        verified=False,
        warnings=warnings,
    )


def scenario_price_series(
    ref: ForecastReference,
    *,
    scenario_name: ScenarioName,
    file_path: str | Path | None = None,
    verify: bool = True,
) -> ScenarioPriceSeries:
    """Resolve one band's price series from the hash-pinned forecast the reference points at.

    Loads the forecast from ``file_path`` (or ``ref.file_ref``), optionally re-checks the content
    hash against ``ref.content_hash`` (drift detection), filters the points to ``scenario_name``
    and returns them in canonical timestamp order. Returns an **empty**, ``verified=False`` series
    with an explanatory warning — never a guessed curve — when the file is missing/unreadable, has
    drifted, or does not cover the requested band.
    """
    from gridq.ingestion.market_forecasts import MarketForecastError, hash_market_forecast

    path = Path(file_path) if file_path is not None else Path(ref.file_ref)

    if not path.is_file():
        return _empty_series(
            ref,
            scenario_name,
            [
                f"forecast file {path} is missing; cannot resolve the {scenario_name.value!r} "
                f"price series for {ref.forecast_version!r} from {ref.source!r} — re-pin and "
                f"reprice (no curve fabricated)."
            ],
        )

    try:
        forecast = _load_forecast_file(path)
    except (MarketForecastError, ValueError) as exc:
        return _empty_series(
            ref,
            scenario_name,
            [
                f"forecast file {path} could not be re-read as a forecast ({exc}); cannot resolve "
                f"the {scenario_name.value!r} price series (no curve fabricated)."
            ],
        )

    if verify and hash_market_forecast(forecast) != ref.content_hash:
        return _empty_series(
            ref,
            scenario_name,
            [
                f"forecast file {path} content hash no longer matches the recorded "
                f"{ref.content_hash} — the referenced forecast changed; re-pin and reprice "
                f"before reading its {scenario_name.value!r} series (not served unverified)."
            ],
        )

    band_points, band_warnings = _points_for_band(forecast.points, scenario_name)
    points = sorted(
        (
            ForecastPricePoint(timestamp=p.timestamp, price_gbp_per_mwh=p.price_gbp_per_mwh)
            for p in band_points
        ),
        key=lambda fp: fp.timestamp,
    )
    return ScenarioPriceSeries(
        scenario_name=scenario_name,
        forecast_ref=ref,
        price_unit=ref.price_unit,
        points=points,
        verified=verify and bool(points),
        warnings=band_warnings,
    )


def price_series_for_set(
    scenario_set: object, *, file_path: str | Path | None = None, verify: bool = True
) -> dict[ScenarioName, ScenarioPriceSeries]:
    """Resolve the per-band price series for every forecast-backed scenario in a set.

    This is what the future dispatch MILP consumes per band. Scenarios with no ``forecast_ref``
    are skipped (no reference ⇒ no series); a band whose forecast lacks distinct points appears
    with an empty series + warning. Keyed by :class:`ScenarioName` (the band identity).
    """
    out: dict[ScenarioName, ScenarioPriceSeries] = {}
    for scenario in getattr(scenario_set, "scenarios", []):
        ref = getattr(scenario, "forecast_ref", None)
        if ref is None:
            continue
        out[scenario.name] = scenario_price_series(
            ref, scenario_name=scenario.name, file_path=file_path, verify=verify
        )
    return out
