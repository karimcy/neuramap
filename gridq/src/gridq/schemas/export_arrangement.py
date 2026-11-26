"""Export / route-to-market arrangement for a site (issue 018 / WS-A4).

Additive, optional sub-model of `SiteModel`. It records **how** a site's exported or flexible
energy reaches a market: the route, the counterparty, the aggregator / route-to-market cut on
gross revenue, an optional declared export tariff, and free-text notes.

It is a **data contract only** — no pricing or optimisation logic lives here. A later slice
(WS-C) reads ``aggregator_cut_pct`` to net local-flex revenue; the assumption library carries
the default cut (``local_flex_piclo.aggregator_cut_pct``) and this per-site value overrides it
when set. Every field is optional with a sensible default, so an all-default instance is a
valid, meaningful "route unknown, nothing declared" record.
"""

from __future__ import annotations

from pydantic import Field

from gridq.schemas.enums import ExportRoute
from gridq.schemas.provenance import GridQModel


class ExportArrangement(GridQModel):
    """A site's declared export / route-to-market arrangement.

    ``route`` defaults to ``ExportRoute.UNKNOWN`` — the honest state until the arrangement is
    evidenced. Nothing here is estimated by the engine; a missing value stays ``None`` and
    becomes a ranked data-ask upstream, never an invented number.
    """

    route: ExportRoute = ExportRoute.UNKNOWN
    counterparty: str | None = Field(
        default=None,
        description="Who the export / route-to-market arrangement is with (a PPA offtaker or a "
        "flexibility aggregator); None when unknown or not applicable.",
    )
    aggregator_cut_pct: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="The aggregator / route-to-market take on gross flexibility/export revenue, "
        "as a fraction in [0, 1]. A per-site override of the assumption default "
        "(local_flex_piclo.aggregator_cut_pct); None = fall back to the assumption default.",
    )
    export_tariff_gbp_per_kwh: float | None = Field(
        default=None,
        ge=0,
        description="Declared export tariff (£/kWh) under a PPA / export arrangement; None when "
        "unknown or not applicable.",
    )
    local_flex_agreed_rate_gbp_per_mwh: float | None = Field(
        default=None,
        ge=0,
        description="Declared agreed local-flexibility rate (£/MWh) under a flexibility-aggregator "
        "arrangement, when one has been agreed; None when unknown or not applicable. Additive, "
        "None default (issue 018 / D8) - recorded verbatim, priced by a later WS-C slice; nothing "
        "here is estimated by the engine.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-text notes on the arrangement (verbatim).",
    )
