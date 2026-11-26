"""Grid connection details for a site.

NOTE on units: MIC/MEC/ASC are conventionally apparent power (kVA). Per the v1 schema
spec these fields are named ``*_kw`` and treated as kW; the kW-vs-kVA convention is
flagged for reconciliation with docs/03_data_contracts.md (see OPEN_COMMENTS).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from gridq.schemas.enums import VoltageLevel
from gridq.schemas.provenance import GridQModel
from gridq.schemas.references import ConnectionAgreementRef, omit_unset


class ConnectionDetails(GridQModel):
    """The connection's capacities, voltage, DNO region and headroom."""

    mic_kw: float = Field(ge=0, description="Maximum import capacity (kW in v1; see module note).")
    mec_kw: float | None = Field(default=None, ge=0, description="Maximum export capacity.")
    asc_kw: float | None = Field(default=None, ge=0, description="Available supply capacity.")
    voltage_level: VoltageLevel | None = None
    dno_region: str | None = None
    # Headroom is a signed margin (negative = over-subscribed) — a documented exception
    # to the no-negative-kW rule.
    import_headroom_kw: float | None = Field(
        default=None, description="MIC minus peak import; may be negative if over-subscribed."
    )
    export_headroom_kw: float | None = Field(
        default=None, description="MEC minus peak export; may be negative if over-subscribed."
    )
    #: Commentary only since IC-12 2.0.0. It is shown and quoted verbatim, and it still enters
    #: ``input_hash`` because it always did. No consumer may read a connection term out of it:
    #: terms come from the IC-1 agreement `agreement` points at
    #: (`tests/contracts/test_ic12_chassis.py` pins the readers of this field).
    anm_or_curtailment_notes: str | None = None
    #: IC-12 2.0.0: the IC-1 `ConnectionAgreement` this connection's terms come from, by content
    #: hash. The typed carrier of firm and non-firm capacity, profiles, curtailment limits and
    #: ANM terms. ``None`` = no agreement has been registered for the site, which is every site
    #: before WP-CONN-06; it is then absent from every dump, so no existing identity moves.
    agreement: ConnectionAgreementRef | None = None

    @model_serializer(mode="wrap")
    def _dump_without_unset_growth(self, handler: SerializerFunctionWrapHandler) -> Any:
        return omit_unset(handler(self), ("agreement",))
