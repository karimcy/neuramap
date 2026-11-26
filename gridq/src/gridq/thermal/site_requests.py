"""Data requests for the plant figures a real site's surface needs (D168 S-a).

The OPS-01 dossier holds a site's cooling topology (once evidenced) and no plant figure. So for a
real candidate site the model's answer is a `PlantDataRequest`: the asks, content-addressed with
the dossier's own `ask_id_for`, scoped ``site:<id>``, each needing the dossier's cooling-topology
asks first. The output of `thermal_plant_model` is ``ThermalFlexAvailability | PlantDataRequest``.

The dossier's plant enum maps totally onto IC-11 topologies and this model's scope:

==============================  =============================================================
dossier ``CoolingPlant``        here
==============================  =============================================================
air_cooled_chillers             ``air_cooled_chillers``
water_cooled_chillers           ``water_cooled_chillers_with_towers`` when ``evaporative`` is
                                also evidenced (towers); otherwise needs evidence
chilled_water_crah              ``crah_crac``
dx_crac                         ``crah_crac`` in IC-11, outside this model (WP-THERM-10)
free_cooling                    ``free_cooling``
direct_liquid                   ``direct_liquid_cooling`` in IC-11, outside (WP-THERM-10)
evaporative                     towers with water-cooled chillers; alone, outside the demo scope
process_refrigeration, other    outside the demo scope
no_significant_cooling_plant    no plant: nothing to model
==============================  =============================================================
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from gridq.contracts.evidence import EvidenceType
from gridq.contracts.thermal_flex import CoolingTopology
from gridq.ops.dossier_model import (
    AskHolder,
    CoolingPlant,
    CoolingTopologyValue,
    DossierAsk,
    Evidenced,
    SiteDossier,
    UnknownField,
)
from gridq.thermal.inputs import SUPPORTED_TOPOLOGIES

__all__ = [
    "PARAMETER_ASKS",
    "PlantDataRequest",
    "TopologyAssessment",
    "assess_topology",
    "asks_for",
    "plant_data_request",
    "request_for_site",
]

_ODR, _TR, _CR = (
    EvidenceType.OPERATIONAL_DATA_RECORD,
    EvidenceType.TECHNICAL_REPORT,
    EvidenceType.COMMISSIONING_RECORD,
)

#: One ask per group of figures (request text, holder, evidence that closes it) and the
#: `PlantInputs` keys it covers.
PARAMETER_ASKS: Final[
    tuple[tuple[str, AskHolder, tuple[EvidenceType, ...], frozenset[str]], ...]
] = (
    (
        "Chiller schedule: number of chillers, rated cooling capacity of each (kWth), minimum "
        "part-load ratio, how many run as standby, and the controller's minimum run and minimum "
        "off times",
        AskHolder.CUSTOMER,
        (_ODR, _TR, _CR),
        frozenset(
            {
                "chiller_count",
                "unit_capacity_kwth",
                "min_part_load_ratio",
                "standby_running",
                "min_run_min",
                "min_off_min",
            }
        ),
    ),
    (
        "Cooling plant electrical power against cooling load and the driving ambient (BMS or "
        "sub-metered history, half-hourly or finer, at least a year), or the manufacturer's "
        "part-load performance data, to fit the WP-THERM-04 C-form power curve and, with free "
        "cooling, its switch temperature",
        AskHolder.CUSTOMER,
        (EvidenceType.METERED_DATA_RECORD, _ODR, _TR),
        frozenset({"regimes[0]", "regimes[1]", "switch_temperature_c"}),
    ),
    (
        "Thermal mass: chilled-water loop volume (or a stated loop heat capacity), the data "
        "hall's heat capacity as the IT inlet sees it, and the CRAH coil hall-to-loop "
        "conductance (a mechanical design report, or a BMS record of a chiller trip or setpoint "
        "step)",
        AskHolder.CUSTOMER,
        (_TR, _ODR, _CR),
        frozenset(
            {
                "loop_capacitance",
                "loop_capacitance.volume_m3",
                "loop_capacitance.rho_cp_kj_per_m3_k",
                "hall_capacitance",
                "hall_capacitance.volume_m3",
                "hall_capacitance.rho_cp_kj_per_m3_k",
                "hall_to_loop_ua_kw_per_k",
            }
        ),
    ),
    (
        "Setpoints: chilled-water supply setpoint and its allowed band, the IT-inlet "
        "temperature range held in normal operation, and the operating dew-point range (BMS "
        "history)",
        AskHolder.CUSTOMER,
        (_ODR,),
        frozenset(
            {
                "chw_setpoint_c",
                "loop_band_min_c",
                "loop_band_max_c",
                "inlet_baseline_min_c",
                "inlet_baseline_max_c",
                "dew_point_min_c",
                "dew_point_max_c",
            }
        ),
    ),
    (
        "IT-safety limits: the ASHRAE class and envelope the site operates to (and whether tape "
        "media are present), and the customer SLA's IT-inlet temperature and humidity terms",
        AskHolder.CUSTOMER,
        (EvidenceType.SERVICE_LEVEL_AGREEMENT, _TR),
        frozenset({"limits", "limits.ashrae.max_rate_of_change"}),
    ),
    (
        "Driver series: half-hourly bounds on the heat the chillers extract (IT and in-hall "
        "losses) and the site's dry-bulb (and, with cooling towers, wet-bulb) temperature, "
        "covering every month",
        AskHolder.CUSTOMER,
        (EvidenceType.METERED_DATA_RECORD, _ODR),
        frozenset({"drivers"}),
    ),
    (
        "Response terms: the notice the BMS needs before a cooling reduction and the minutes "
        "from start to full reduction",
        AskHolder.CUSTOMER,
        (_ODR,),
        frozenset({"response"}),
    ),
)

#: Every key a real surface needs (all asks), in the order `PARAMETER_ASKS` lists them.
ALL_KEYS: Final = frozenset().union(*(keys for *_, keys in PARAMETER_ASKS))


class TopologyAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["supported", "needs_evidence", "out_of_demo_scope", "no_plant", "unknown"]
    #: The IC-11 topologies, where the dossier's plant maps onto them.
    topology: tuple[CoolingTopology, ...]
    detail: str = Field(min_length=1)


class PlantDataRequest(BaseModel):
    """What the model needs before it can declare a surface for a site. No surface, no figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["plant_data_request"]
    site_id: str = Field(min_length=1)
    topology: TopologyAssessment
    #: Parameter keys still needed (unknown, or held only as stand-ins).
    needed: tuple[str, ...]
    asks: tuple[DossierAsk, ...]


_DIRECT: Final = {
    CoolingPlant.AIR_COOLED_CHILLERS: CoolingTopology.AIR_COOLED_CHILLERS,
    CoolingPlant.CHILLED_WATER_CRAH: CoolingTopology.CRAH_CRAC,
    CoolingPlant.DX_CRAC: CoolingTopology.CRAH_CRAC,
    CoolingPlant.FREE_COOLING: CoolingTopology.FREE_COOLING,
    CoolingPlant.DIRECT_LIQUID: CoolingTopology.DIRECT_LIQUID_COOLING,
}


def assess_topology(value: CoolingTopologyValue) -> TopologyAssessment:
    """Map an evidenced dossier topology onto IC-11 and this model's scope (total)."""
    plant = set(value.plant)
    if CoolingPlant.NO_SIGNIFICANT_COOLING_PLANT in plant:
        return TopologyAssessment(
            status="no_plant", topology=(), detail="the site has no significant cooling plant"
        )
    out: set[CoolingTopology] = set()
    outside: list[str] = []
    needs: list[str] = []
    for p in sorted(plant):
        if p in _DIRECT:
            out.add(_DIRECT[p])
            if p in (CoolingPlant.DX_CRAC, CoolingPlant.DIRECT_LIQUID):
                outside.append(f"{p.value} (WP-THERM-10)")
        elif p is CoolingPlant.WATER_COOLED_CHILLERS:
            if CoolingPlant.EVAPORATIVE in plant:
                out.add(CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS)
            else:
                needs.append("water-cooled chillers without evidenced heat rejection (towers)")
        elif p is CoolingPlant.EVAPORATIVE:
            if CoolingPlant.WATER_COOLED_CHILLERS not in plant:
                outside.append("evaporative cooling without water-cooled chillers")
        else:  # process_refrigeration, other
            outside.append(p.value)
    topo = tuple(sorted(out))
    if outside:
        return TopologyAssessment(
            status="out_of_demo_scope",
            topology=topo,
            detail="outside the demo-scope chilled-water model: " + "; ".join(outside),
        )
    if needs:
        return TopologyAssessment(status="needs_evidence", topology=topo, detail="; ".join(needs))
    chillers = {
        CoolingTopology.AIR_COOLED_CHILLERS,
        CoolingTopology.WATER_COOLED_CHILLERS_WITH_TOWERS,
    }
    if (
        len(chillers & out) != 1
        or CoolingTopology.CRAH_CRAC not in out
        or not out <= set(SUPPORTED_TOPOLOGIES)
    ):
        return TopologyAssessment(
            status="out_of_demo_scope",
            topology=topo,
            detail="the demo model needs exactly one chiller type serving CRAH units",
        )
    return TopologyAssessment(status="supported", topology=topo, detail=value.description)


def asks_for(
    site_id: str, keys: frozenset[str], prerequisites: tuple[str, ...]
) -> tuple[DossierAsk, ...]:
    """The asks covering ``keys`` (unknown or stand-in parameters), scoped to the site."""
    out = []
    for request, holder, closes, covered in PARAMETER_ASKS:
        if covered & keys:
            out.append(
                DossierAsk.make(
                    request=request,
                    holder=holder,
                    scope=f"site:{site_id}",
                    closes_with=closes,
                    prerequisites=prerequisites,
                )
            )
    return tuple(sorted(out, key=lambda a: a.ask_id))


def plant_data_request(
    site_id: str,
    keys: frozenset[str],
    topology: TopologyAssessment,
    prerequisites: tuple[str, ...] = (),
) -> PlantDataRequest:
    return PlantDataRequest(
        kind="plant_data_request",
        site_id=site_id,
        topology=topology,
        needed=tuple(sorted(keys)),
        asks=asks_for(site_id, keys, prerequisites),
    )


def request_for_site(site: SiteDossier) -> PlantDataRequest:
    """The request for a dossier site. The dossier holds no plant figure, so every parameter
    is asked for; an unknown topology's own asks (with their DPA prerequisite) come first."""
    field = site.cooling_topology
    if isinstance(field, UnknownField):
        prereq = tuple(sorted(a.ask_id for a in field.asks))
        topo = TopologyAssessment(status="unknown", topology=(), detail=field.reason)
        req = plant_data_request(site.site_id, ALL_KEYS, topo, prereq)
        asks = tuple(sorted((*field.asks, *req.asks), key=lambda a: a.ask_id))
        return PlantDataRequest(
            kind="plant_data_request",
            site_id=req.site_id,
            topology=topo,
            needed=req.needed,
            asks=asks,
        )
    assert isinstance(field, Evidenced)
    topo = assess_topology(field.value)
    if topo.status in ("no_plant", "out_of_demo_scope"):
        return plant_data_request(site.site_id, frozenset(), topo)
    return plant_data_request(site.site_id, ALL_KEYS, topo)
