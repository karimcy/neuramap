"""WP-THERM-03 ``thermal_plant_model``: a clean-room cooling-plant model with IT-safety limits
(spec A4-01 to A4-03), producing usable flex as an IC-11 v1.1.0 surface.

Clean room (ADR-D7 option (c); WP-THERM-01 no-go): no ThermaFlex code, document or parameter is
used or asked for. Every figure is a site figure with IC-4 evidence, a cited public figure, or
a labelled stand-in; the only published figures held are in `gridq.thermal.sources` (ASHRAE TC 9.9
2011 Table 4 class A1, WMO No-8 psychrometrics).

Modules: `sources` (cited presets), `psychro` (dew point and RH), `inputs` (typed inputs with
provenance, no defaults), `plant` (power curve, thermal nodes, limits as numbers), `certificate`
(the firmness certificate per start slot), `surface` (bands, curves, the IC-11 surface, and the
`FlexProducer` seam an interface-level adapter could implement), `site_requests` (dossier mapping
and `PlantDataRequest`). Decisions D163 to D169 (docs/decisions.md).

WP-THERM-05 ``thermaflex_dc_controller`` (spec TF-02): the objective mode "flexibility within
IT-safety limits" beside the comfort/cost mode, with the thermal limits as hard constraints.
Modules: `comfort_cost` (the comfort/cost mode seam and the clean-room reference policy),
`safety_filter` (the run-time filter that is the controller's only safety argument), `controller`
(`DcController`: admission of IC-11 instructions, the minute, mode transitions). Decisions
D-NEW-THERM05-1 to -5 (docs/decisions.md).
"""

from __future__ import annotations

from gridq.contracts.thermal_flex import ThermalFlexAvailability
from gridq.ops.dossier_model import SiteDossier
from gridq.thermal.comfort_cost import ComfortCostPolicy, SetpointHoldingPolicy
from gridq.thermal.controller import DcController, ObjectiveMode
from gridq.thermal.inputs import DriverSeries, PlantInputs, SurfaceRequest
from gridq.thermal.site_requests import PlantDataRequest, request_for_site
from gridq.thermal.surface import SurfaceResult, build_surface

__all__ = [
    "ComfortCostPolicy",
    "DcController",
    "ObjectiveMode",
    "PlantDataRequest",
    "SetpointHoldingPolicy",
    "SurfaceResult",
    "build_surface",
    "thermaflex_dc_controller",
    "thermal_plant_model",
]


def thermal_plant_model(
    site: SiteDossier,
    plant: tuple[PlantInputs, DriverSeries, SurfaceRequest] | None = None,
) -> ThermalFlexAvailability | PlantDataRequest:
    """A candidate site's IC-11 surface, or the data request that stands in its place.

    The OPS-01 dossier holds no plant figure, so without ``plant`` the answer is the site's
    `PlantDataRequest`. With ``plant``, a surface is returned only when it is registered: every
    figure is the site's own or cited, none a stand-in. Otherwise the data request comes back.
    """
    if plant is None:
        return request_for_site(site)
    inputs, drivers, request = plant
    if inputs.site_id != site.site_id:
        raise ValueError("the plant inputs name another site")
    result = build_surface(inputs, drivers, request)
    if result.data_request is not None or result.surface.record_class != "registered":
        return result.data_request or request_for_site(site)
    return result.surface


def thermaflex_dc_controller(
    result: SurfaceResult,
    controller_id: str,
    policy: ComfortCostPolicy | None = None,
    mode: ObjectiveMode = ObjectiveMode.COMFORT_COST,
    authority_granted: bool = False,
) -> DcController:
    """The site's controller over one WP-THERM-03 build: the comfort/cost mode is ``policy``
    (the clean-room `SetpointHoldingPolicy` when none is given), and the IT-safety objective mode
    flexes only within the limits the build's certificate states. Starts in comfort/cost mode
    without control authority unless told otherwise."""
    if result.certificate is None:
        raise ValueError("no certificate: the build has no flex to control")
    return DcController(
        surface=result.surface,
        certificate=result.certificate,
        policy=policy or SetpointHoldingPolicy(result.certificate.plant),
        controller_id=controller_id,
        mode=mode,
        authority_granted=authority_granted,
    )
