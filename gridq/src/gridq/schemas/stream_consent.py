"""Per-stream customer consent captured at intake (Revenue-Data Drive / D43).

This records that the customer explicitly consented, at intake, to GridQ pursuing one
revenue stream on their behalf — with a commercially agreed rate where the stream needs one
that is not already held elsewhere on the `SiteModel` (e.g. a PPA £/kWh floor, a Capacity
Market aggregator fee). It is the *authorisation spine* the wider drive hangs off: in a later
slice, a consented stream (with any rate it needs) is what flips the proposal from
"conditional" to "consent given" for that stream and lets its gate open to a contracted
firmness.

It is a **data contract only** — no pricing, gating or optimisation logic lives here, and
nothing here invents money. Consent is a *necessary authorisation*, not a value: a stream
still needs its data/rate to produce a number.

Note: rates that already have a dedicated home on the `SiteModel` are NOT duplicated here —
the export tariff (£/kWh), aggregator cut (%) and local-flex agreed rate (£/MWh) live on
`ExportArrangement`. `agreed_rate_gbp` is the generic carrier for the streams whose agreed
rate has no such dedicated field.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from gridq.schemas.enums import MarketStream
from gridq.schemas.provenance import GridQModel


class RateUnit(StrEnum):
    """The unit a commercially agreed rate is expressed in.

    Exists because `agreed_rate_gbp` was a bare float that different downstream code paths read
    as four incompatible units (hostile review F-17). £/kWh and £/MW/yr differ by six orders of
    magnitude, so an unlabelled number is not a rate — it is a guess waiting to be made.
    """

    GBP_PER_KWH = "gbp_per_kwh"
    GBP_PER_MWH = "gbp_per_mwh"
    GBP_PER_KW_YEAR = "gbp_per_kw_year"
    GBP_PER_MW_YEAR = "gbp_per_mw_year"
    GBP_PER_YEAR = "gbp_per_year"


class RevenueStreamConsent(GridQModel):
    """Explicit customer authorisation to pursue ONE revenue stream, captured at intake."""

    stream: MarketStream
    consented: bool = Field(
        default=False,
        description="True iff the customer explicitly consented, at intake, to GridQ pursuing "
        "this revenue stream on their behalf.",
    )
    agreed_rate_gbp: float | None = Field(
        default=None,
        ge=0,
        description="An optional commercially agreed rate, verbatim, for streams whose agreed "
        "rate is not already held elsewhere on the SiteModel. MUST be accompanied by "
        "`agreed_rate_unit` — the number alone is meaningless. None when the stream needs no "
        "such rate or none was supplied. The export tariff / aggregator cut / local-flex rate "
        "live on ExportArrangement and are not duplicated here.",
    )
    agreed_rate_unit: RateUnit | None = Field(
        default=None,
        description="The unit `agreed_rate_gbp` is expressed in. REQUIRED whenever a rate is "
        "given (hostile review F-17): this field was previously a bare, unit-less float that "
        "downstream code read as £/kWh, £/MWh, £/kW/yr AND £/MW/yr depending on the stream. A "
        "single mis-united intake entry therefore produced contracted floor revenue wrong by up "
        "to three orders of magnitude, and the record still validated cleanly.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-text notes on the consent / agreed rate (verbatim).",
    )

    @model_validator(mode="after")
    def _rate_requires_its_unit(self) -> RevenueStreamConsent:
        """A rate without a unit is not admissible (F-17).

        Rejected at the door rather than defaulted: guessing the unit is precisely the failure
        this field exists to prevent, and the intake form is where the answer is actually known.
        """
        if self.agreed_rate_gbp is not None and self.agreed_rate_unit is None:
            raise ValueError(
                f"agreed_rate_gbp={self.agreed_rate_gbp} was given for stream "
                f"{self.stream.value!r} with no agreed_rate_unit. An unlabelled rate is "
                "unusable: £/kWh and £/MW/yr differ by six orders of magnitude. Set "
                f"agreed_rate_unit to one of {[u.value for u in RateUnit]}."
            )
        return self
