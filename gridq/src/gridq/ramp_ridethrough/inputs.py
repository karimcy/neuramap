"""The declared and evidenced inputs of WP-SITE-06.

Nothing here has a default for any number (the no-invented-data rule). Every element is a stated
value or `Unknown` with its reason, and every `Unknown` that a screen needs becomes a typed data
ask.

- `BessDeclaration` (``gridq.ramp_ridethrough.bess_declaration`` 1.0.0): what the customer or the
  OEM declares about the BESS design: G99 Registered Capacity, Rated Import Power, reactive range,
  PPC ramp-limit settings, the facility's registered capacity and the GB region.
- `StudyInputs` (``gridq.ramp_ridethrough.study_inputs`` 1.0.0): the site's electrical data and the
  OEM's models. **Site electrical data is evidence-backed or Unknown, never declared and never a
  stand-in**: a fault level or X/R ratio is an `EvidencedQuantity` that points at an IC-4 object
  (the network operator's letter or study), and a document input (transformer data, an OEM model) is
  an IC-4 `EvidenceRef`.
- `NetImportSeries` (``gridq.ramp_ridethrough.net_import_series`` 1.0.0): an optional
  minute-resolution site net-import series for the ramp series check.

Integer watts and exact rationals only: no float enters a hashed record (the D171 rule of
``gridq.adequacy``).
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.adequacy._common import ExactRational, NonNegInt, PosInt, RecordClass
from gridq.contracts._base import ContractModel, NonEmptyStr, Unknown
from gridq.contracts.evidence import EvidenceRef

__all__ = [
    "BESS_SCHEMA",
    "INPUTS_SCHEMA",
    "SERIES_SCHEMA",
    "BessDeclaration",
    "Declarant",
    "EvidencedQuantity",
    "GbRegion",
    "NetImportSeries",
    "ReactiveRange",
    "RvcCategory",
    "StudyInputs",
]

BESS_SCHEMA: Final = "gridq.ramp_ridethrough.bess_declaration"
INPUTS_SCHEMA: Final = "gridq.ramp_ridethrough.study_inputs"
SERIES_SCHEMA: Final = "gridq.ramp_ridethrough.net_import_series"

GbRegion = Literal["england_wales", "south_scotland", "north_scotland"]
#: The P28 Table 4 occurrence category the customer expects PCS trips to fall in.
RvcCategory = Literal["category_2", "category_3"]


class Declarant(ContractModel):
    """Who declared the design values. A declaration is an input, never evidence (IC-4)."""

    name: NonEmptyStr
    role: Literal["customer", "host", "oem"]
    reference: NonEmptyStr | Unknown


class ReactiveRange(ContractModel):
    """The declared reactive capability at Registered Capacity, as magnitudes in var."""

    kind: Literal["reactive_range"]
    absorb_var: NonNegInt
    inject_var: NonNegInt


class BessDeclaration(ContractModel):
    schema_name: Literal["gridq.ramp_ridethrough.bess_declaration"] = BESS_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    declared_by: Declarant
    #: G99 Registered Capacity of the BESS Power Park Module (based on the inverter rating).
    registered_capacity_w: PosInt | Unknown
    #: G99 Section 4 "Rated Import Power": the most it draws when replenishing its store.
    rated_import_power_w: PosInt | Unknown
    reactive_range: ReactiveRange | Unknown = Field(discriminator="kind")
    #: PPC active-power ramp limit towards more charge or less discharge (site import rising).
    ppc_ramp_towards_charge_w_per_min: PosInt | Unknown
    #: PPC active-power ramp limit towards more discharge or less charge (site import falling).
    ppc_ramp_towards_discharge_w_per_min: PosInt | Unknown
    #: Registered Capacity of every Power Generating Module at the facility (G99 6.1.3.1).
    facility_registered_capacity_w: PosInt | Unknown
    gb_region: GbRegion | Unknown
    rvc_category: RvcCategory | Unknown

    @model_validator(mode="after")
    def _facility_covers_bess(self) -> BessDeclaration:
        cap, fac = self.registered_capacity_w, self.facility_registered_capacity_w
        if isinstance(cap, int) and isinstance(fac, int) and fac < cap:
            raise ValueError("the facility's registered capacity includes the BESS's")
        if self.record_class == "synthetic_fixture" and not self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("a synthetic fixture carries a SYNTHETIC- site id")
        return self


class EvidencedQuantity(ContractModel):
    """A site electrical figure transcribed from a registered IC-4 object."""

    kind: Literal["evidenced_quantity"]
    value: ExactRational
    unit: Literal["VA", "ratio"]
    evidence: EvidenceRef

    @model_validator(mode="after")
    def _positive(self) -> EvidencedQuantity:
        if self.value.as_fraction() <= 0:
            raise ValueError("an evidenced electrical quantity is positive")
        return self


Quantity = Annotated[EvidencedQuantity | Unknown, Field(discriminator="kind")]
Document = Annotated[EvidenceRef | Unknown, Field(discriminator="kind")]


class StudyInputs(ContractModel):
    """Site electrical data and OEM documents. Each is evidence or `Unknown`, never declared."""

    schema_name: Literal["gridq.ramp_ridethrough.study_inputs"] = INPUTS_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    # ----- the network at the PCC (network operator)
    #: Minimum short-circuit power at the PCC under normal operating conditions (P28 6.4.1(a);
    #: G99 B.4.4.3 / C.7.5.3 "fault level at minimum as notified by the DNO").
    min_fault_level_va: Quantity
    #: X/R at the PCC for the minimum fault level (P28 6.1.5: R"k and X"k).
    x_over_r: Quantity
    #: Pre- and post-fault short-circuit capacity at the PCC (G99 12.3.1.7(c)).
    pre_post_fault_short_circuit: Document
    #: Equivalent network model and additional network data (G99 6.3.3; 12.3.1.7(c)).
    network_equivalent: Document
    #: Declared voltage and statutory limits at the PCC (P28 6.4.1(c)).
    statutory_voltage_limits: Document
    # ----- the customer's installation (customer or host)
    #: Transformer data per DDRC Schedule 5c (G99 6.4.3.3).
    transformer_data: Document
    #: Transformer saturation and inrush data for energisation (P28 6.4.1, 6.4.2; G99 9.3.4).
    transformer_saturation: Document
    #: Cable, busbar and site single-line data (G99 6.4.2, DDRC Schedule 5a).
    site_network_data: Document
    #: Equivalents of the data-centre installation (UPS, motor and chiller plant): G99 B.4.1.3,
    #: the first of the two clauses so numbered.
    customer_installation_equivalent: Document
    # ----- the BESS (OEM)
    #: Validated RMS simulation model of the PCS and PPC (G99 6.3.7, 6.3.8.1, 6.3.8.4).
    oem_rms_model: Document
    #: Validated EMT simulation model of the PCS and PPC (spec A3-04).
    oem_emt_model: Document
    #: PPC control settings: ramp limits, P/Q/V control modes (G99 B.4.1.2; 10.3.4).
    ppc_settings: Document
    #: Interface and PCS protection settings (G99 12.3.1.7(d), 10.6.7).
    protection_settings: Document
    #: Manufacturers' Information on FRT and fast fault current (G99 B.4.4.5 / C.7.5.5).
    frt_manufacturer_information: Document
    # ----- the ride-through evidence (bound by reference, never found by search)
    #: The fault ride-through and fast fault current study of the BESS (PSAL, the OEM or an
    #: independent engineer): a technical report or conformity certificate (G99 B.4.4, C.7.5).
    frt_study_report: Document
    #: The network operator's acceptance: a G99 approval or commissioning record.
    dno_frt_acceptance: Document

    @model_validator(mode="after")
    def _units(self) -> StudyInputs:
        for name, unit in (("min_fault_level_va", "VA"), ("x_over_r", "ratio")):
            v = getattr(self, name)
            if isinstance(v, EvidencedQuantity) and v.unit != unit:
                raise ValueError(f"{name} is stated in {unit}")
        if self.record_class == "synthetic_fixture" and not self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("a synthetic fixture carries a SYNTHETIC- site id")
        return self


class NetImportSeries(ContractModel):
    """Site net import (W, import positive), one value per minute, for the ramp series check."""

    schema_name: Literal["gridq.ramp_ridethrough.net_import_series"] = SERIES_SCHEMA
    schema_version: Literal["1.0.0"] = "1.0.0"
    site_id: NonEmptyStr
    record_class: RecordClass
    basis: Literal["metered", "modelled"]
    source: EvidenceRef | NonEmptyStr
    start_utc: NonEmptyStr
    import_w: tuple[int, ...] = Field(min_length=2)
