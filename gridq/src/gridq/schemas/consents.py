"""Grid and supplier consent state for a site."""

from __future__ import annotations

from gridq.schemas.enums import ConsentStatus
from gridq.schemas.provenance import GridQModel


class ConsentState(GridQModel):
    """The bundle of consents/permissions relevant to connecting and operating."""

    g98: ConsentStatus = ConsentStatus.UNKNOWN
    g99: ConsentStatus = ConsentStatus.UNKNOWN
    g100: ConsentStatus = ConsentStatus.UNKNOWN
    export_mpan: str | None = None
    dno_application_state: ConsentStatus = ConsentStatus.NOT_STARTED
    supplier_consent_state: ConsentStatus = ConsentStatus.NOT_STARTED
    notes: str | None = None
