"""Confidentiality state for a site's data (M1).

The System Brief's non-functional floor: *half-hourly data held under recorded NDA state per
site; access logged; sole-trader sites treated as personal data*. This records the NDA state and
an optional personal-data override; whether a site is personal data is otherwise derived from the
counterparty's legal entity type (see ``SiteModel.is_personal_data``). The access log itself lives
in ``gridq.ingestion.access_log``.
"""

from __future__ import annotations

from gridq.schemas.enums import NdaStatus
from gridq.schemas.provenance import GridQModel


class Confidentiality(GridQModel):
    """How a site's confidential data is held."""

    nda_status: NdaStatus = NdaStatus.UNKNOWN
    #: Reference to the recorded NDA (e.g. a document id / system ref). The state, not the doc.
    nda_reference: str | None = None
    #: Explicit personal-data flag. ``None`` (default) means "derive from the entity type"
    #: (sole-trader → personal data); set ``True``/``False`` to record a deliberate override.
    personal_data_override: bool | None = None
