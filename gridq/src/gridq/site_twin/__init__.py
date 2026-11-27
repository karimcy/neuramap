"""The site twin (WP-TWIN-01, spec B7-01): one SYNTHETIC site's electrical, battery and cooling
state, driven by the design-time models and served over Modbus TCP.

- `electrical`: the battery, by replaying the adequacy kernel (`gridq.adequacy.kernel`) one
  segment at a time (D197);
- `thermal`: the cooling plant, by the thermal model's exact node step (`gridq.thermal.nodes`)
  under IC-11 instructions (D198);
- `registers`: Neura's SYNTHETIC register map and the commissioning set a gateway is handed;
- `modbus_server`: real Modbus TCP on 127.0.0.1;
- `twin`: the clock, the load, the meter and the wire (D196).

The twin never seals telemetry and never imports the gateway (`gridq.edge_southbound`); a gateway
reaches it the way it reaches any Modbus server.
"""

from __future__ import annotations

from gridq.site_twin.electrical import BessSecond, BessSpec, BessTwin
from gridq.site_twin.thermal import FlexVerdict, ThermalSecond, ThermalSetup, ThermalTwin
from gridq.site_twin.twin import SiteTwin, TwinConfig, TwinSecond

__all__ = [
    "BessSecond",
    "BessSpec",
    "BessTwin",
    "FlexVerdict",
    "SiteTwin",
    "ThermalSecond",
    "ThermalSetup",
    "ThermalTwin",
    "TwinConfig",
    "TwinSecond",
]
