"""``dc_load_model`` (WP-SITE-01): pre-operational data-centre load and PUE synthesis.

A WS-SITE plug-in package, outside every CORE-owned path. It registers the ``data_centre`` load
archetype through the IC-12 chassis (`gridq.extensions`) when the chassis loads
``gridq.datacentre.plugin``; **importing this package registers nothing**. Call `ensure_registered`
(or `derive_data_centre_half_hourly`, which calls it) to load the plug-in.

What it produces, from site-declared inputs (`DcLoadInputs`) and a seed:

- a half-hourly facility-power ensemble over the horizon, with an *expected* (mean) series,
  pointwise P05/P10/P50/P90/P95 bands and each edge's simulation-error interval;
- one-minute series for any window, an exactly conditioned disaggregation of the half-hourly one;
- aggregates (energy by year and month, peak half-hourly and one-minute kW), a ranked variance
  attribution (the data ask), and a series hash that the same inputs and seed reproduce.

An operator's IC-9 compute turn-down declaration (WP-SITE-07) is an optional input
(``workload_flex=``, default `NO_ENVELOPE_DECLARED`). It is bound beside the ensemble and never
changes it; what it could deliver is computed on request by `turndown_overlay`, labelled
conditional (`gridq.datacentre.workload_flex`, `gridq.datacentre.turndown`).

The data rule: every number is a user input with no default, or an opt-in preset quoted from a
retrieved open source (`gridq.datacentre.presets`, `gridq.datacentre.sources`). Decisions
D81 to D84 and D126 in ``docs/decisions.md`` record the method choices.
"""

from __future__ import annotations

from gridq.datacentre.archetype import ARCHETYPE_ID, DATA_CENTRE, DataCentreArchetype
from gridq.datacentre.derive import DerivedDataCentre, derive_data_centre_half_hourly
from gridq.datacentre.distributions import (
    Discrete,
    Fixed,
    Normal,
    Quantiles,
    Stated,
    Triangular,
    Uniform,
)
from gridq.datacentre.inputs import (
    AmbientSeries,
    Auxiliary,
    CoolingPlant,
    CoolingRegime,
    CoolingResponse,
    DcLoadInputs,
    Dips,
    DistributionLoss,
    DutyPoint,
    RampPhase,
    Ups,
    UpsConstant,
    UpsCurve,
    UpsPoint,
    Utilisation,
    Variability,
)
from gridq.datacentre.plugin import PLUGIN_MODULE
from gridq.datacentre.synthesiser import MinuteWindow, Synthesis, synthesise
from gridq.datacentre.turndown import (
    MinuteTurndown,
    TurndownOverlay,
    turndown_overlay,
    turndown_overlay_minutes,
)
from gridq.datacentre.workload_flex import (
    NO_ENVELOPE_DECLARED,
    NoEnvelopeDeclared,
    WorkloadFlex,
    WorkloadFlexBinding,
)
from gridq.extensions import LoadedPlugin, load_plugin

__all__ = [
    "ARCHETYPE_ID",
    "DATA_CENTRE",
    "NO_ENVELOPE_DECLARED",
    "PLUGIN_MODULE",
    "AmbientSeries",
    "Auxiliary",
    "CoolingPlant",
    "CoolingRegime",
    "CoolingResponse",
    "DataCentreArchetype",
    "DcLoadInputs",
    "DerivedDataCentre",
    "Dips",
    "Discrete",
    "DistributionLoss",
    "DutyPoint",
    "Fixed",
    "MinuteTurndown",
    "MinuteWindow",
    "NoEnvelopeDeclared",
    "Normal",
    "Quantiles",
    "RampPhase",
    "Stated",
    "Synthesis",
    "Triangular",
    "TurndownOverlay",
    "Uniform",
    "Ups",
    "UpsConstant",
    "UpsCurve",
    "UpsPoint",
    "Utilisation",
    "Variability",
    "WorkloadFlex",
    "WorkloadFlexBinding",
    "derive_data_centre_half_hourly",
    "ensure_registered",
    "synthesise",
    "turndown_overlay",
    "turndown_overlay_minutes",
]


def ensure_registered() -> LoadedPlugin:
    """Load the plug-in through the chassis (idempotent) and return its load record."""
    return load_plugin(PLUGIN_MODULE)
