"""Opt-in presets: the only numbers this package carries, each quoted from a retrieved open source.

A preset is a named constructor. Nothing calls one implicitly; an input built from one carries its
`Citation`, and the synthesis records every preset it consumed (on the output, in the manifest, and
as the ``sources`` of the derived-assumption record), so a series built on a preset is
distinguishable from one built on the user's own figures.

Two presets exist, both from S1 (Shehabi et al. 2024, LBNL-2001637), and both are deliberately
narrow in what they claim:

- `ups_efficiency_shehabi_2024`: the UPS efficiency *range* LBNL assumed for a US space type
  (Table 4.3). The report gives a bare range; the uniform shape put on it here is this package's
  choice, and is labelled so. It is a constant efficiency, so the redundancy configuration then has
  no electrical effect, and the synthesis says so.
- `sd_fraction_shehabi_2024`: a measured AI workload's per-node power SD over its average power
  (Table 3.1). It is node-level evidence; a facility aggregates many nodes, so as a facility-level
  one-minute SD it is an upper bound, and is labelled ``node_level_upper_bound``. The correlation
  time stays a required input: Table 3.1 gives none.

Nothing else has a preset, because no open source held gives it (decision D81): cooling
coefficients and duty points, ``T*``, distribution loss, auxiliaries, utilisation shapes, fill and
slip periods are the user's, and each is listed as a data request in `DATA_REQUESTS`.
"""

from __future__ import annotations

from typing import Final, Literal

from gridq.datacentre.distributions import Fixed, Stated, Uniform
from gridq.datacentre.inputs import UpsConstant
from gridq.datacentre.sources import Citation

__all__ = [
    "DATA_REQUESTS",
    "NODE_LEVEL_UPPER_BOUND",
    "S1_TABLE_3_1",
    "S1_TABLE_4_3_UPS",
    "sd_fraction_shehabi_2024",
    "ups_efficiency_shehabi_2024",
]

#: The label every preset from Table 3.1 carries.
NODE_LEVEL_UPPER_BOUND: Final = "node_level_upper_bound"

SpaceType = Literal[
    "commercial_edge", "comms_sp_internal_colocation", "hyperscale_ai_air", "ai_liquid"
]

#: S1 Table 4.3 (report p. 43), the "UPS Efficiencies" column, by row group. Each entry: the space
#: types the row lists, the low and high efficiency, and the row as printed.
S1_TABLE_4_3_UPS: Final[dict[str, tuple[str, float, float, str]]] = {
    "commercial_edge": (
        "Commercial Edge, Enterprise Branch, SMB, Telco Edge",
        0.77,
        0.85,
        "• Commercial Edge 77–85%",
    ),
    "comms_sp_internal_colocation": (
        "Comms SPs, Internal, Colocation - Sm/Med Scale, Colocation - Large Scale",
        0.80,
        0.94,
        "• Comms SPs 80–94%",
    ),
    "hyperscale_ai_air": (
        "Hyperscale, AI (IT Air Cooling)",
        0.90,
        0.99,
        "• Hyperscale 90–99%",
    ),
    "ai_liquid": (
        "AI (IT Liquid Cooling)",
        0.90,
        0.99,
        "• AI (IT Liquid Cooling) 90–99%",
    ),
}

#: S1 Table 3.1 (report p. 25): per-node average power and SD, kW, for each measured workload, as
#: printed. All systems are NVIDIA H100-SXM5-80GB, 8 GPUs per node (the table caption).
S1_TABLE_3_1: Final[dict[str, tuple[float, float]]] = {
    "SMC - GPT3-175B (64)": (7.67, 0.73),
    "SMC - Llama-70B (64)": (5.92, 1.5),
    "SMC - Llama-70B (8)": (7.73, 1.11),
    "SMC - Llama-70B (1)": (6.78, 1.05),
    "BNL - Llama-13B (1)": (7.79, 0.61),
    "SMC - ResNet 1 (8)": (6.36, 1.66),
    "SMC - ResNet 2 (1)": (6.76, 0.29),
    "BNL - Resnet 1 (1)": (4.6, 0.34),
    "BNL - Resnet 2 (1)": (5.76, 0.11),
}

_TABLE_3_1_CAPTION = (
    "For each workload, average power (Pavg), maximum power (Pmax), and standard deviation (SD) "
    "are reported in kilowatts (kW). Power measurements are per-node"
)


def ups_efficiency_shehabi_2024(space_type: SpaceType) -> UpsConstant:
    """A constant UPS efficiency, uniform on S1 Table 4.3's range for ``space_type``.

    US-derived: the ranges are LBNL's simulation assumptions for the US data-centre stock. They are
    not a UK figure and not a datasheet for any plant; an OEM part-load curve replaces this preset.
    """
    rows, low, high, printed = S1_TABLE_4_3_UPS[space_type]
    citation = Citation(
        source_id="S1",
        locator=f"Table 4.3 (p. 43), UPS Efficiencies column, row group {rows}",
        quote=printed,
        caveat=(
            "US-derived simulation assumption range, not a UK figure or a plant datasheet. The "
            "report gives a bare range; the uniform distribution on it is this package's choice. "
            "A constant efficiency: the redundancy configuration has no electrical effect under it."
        ),
    )
    return UpsConstant(
        efficiency=Stated(distribution=Uniform(low=low, high=high), citation=citation)
    )


def sd_fraction_shehabi_2024(workload: str) -> Stated:
    """SD / Pavg for a measured workload row of S1 Table 3.1, as a fixed one-minute SD fraction.

    Labelled `NODE_LEVEL_UPPER_BOUND`: the measurement is of one node's power, and the facility
    sums many nodes, so the facility's relative variability is at most this (equal only if every
    node moves in lock step). The table states no sampling interval or correlation time.
    """
    if workload not in S1_TABLE_3_1:
        raise KeyError(f"S1 Table 3.1 has no workload row {workload!r}; rows: {list(S1_TABLE_3_1)}")
    pavg, sd = S1_TABLE_3_1[workload]
    citation = Citation(
        source_id="S1",
        locator=f"Table 3.1 (p. 25), caption and row '{workload}', columns Pavg (kW) and SD (kW)",
        quote=f"{_TABLE_3_1_CAPTION} … {workload}: Pavg {pavg}, SD {sd}",
        caveat=(
            f"{NODE_LEVEL_UPPER_BOUND}: per-node evidence from one workload on H100 nodes, used as "
            "an upper bound on facility-level relative variability. SD/Pavg is this package's "
            "ratio of the two printed figures. No sampling interval or correlation time is given."
        ),
    )
    return Stated(distribution=Fixed(value=sd / pavg), citation=citation)


#: What would retire each user input, in the words of a data request. The synthesis lists the ones
#: its inputs leave open as the derived record's confirmation ask.
DATA_REQUESTS: Final[dict[str, str]] = {
    "utilisation": (
        "Within-day IT utilisation shapes (weekday, Saturday, Sunday) for the planned workload mix "
        "(training and inference), from the operator or a comparable live hall's metering."
    ),
    "ramp": (
        "The IT ramp schedule: racks and rated kW per rack per phase, energisation dates, fill "
        "periods, and the operator's own view of slip per phase."
    ),
    "ups": (
        "The UPS OEM part-load efficiency curve (at least 25/50/75/100% load) for the selected "
        "modules, the module rating, the module count per phase and the redundancy configuration."
    ),
    "distribution_loss": (
        "The distribution loss (transformers, switchgear, PDUs and busway) as a fraction of IT "
        "load "
        "or UPS input, from the electrical design."
    ),
    "auxiliaries": (
        "Auxiliary loads (lighting, BMS, security, offices, generator heaters) in kW or as a "
        "fraction of capacity, with their schedules and whether each sits in the cooled space."
    ),
    "cooling": (
        "Cooling plant duty points or regression coefficients per regime (design schedules from "
        "the "
        "mechanical designer), the free-cooling switchover temperature T*, the cooling electrical "
        "capacity, and the plant's response time to a load change."
    ),
    "variability": (
        "Facility-level one-minute IT power for a comparable live hall, to set the "
        "within-half-hour "
        "SD, correlation time and any synchronous dip pattern."
    ),
    "ambient": (
        "The site's ambient dry-bulb (and wet-bulb where the topology needs it) series over the "
        "horizon: the Atlas ERA5 work package supplies this."
    ),
}
