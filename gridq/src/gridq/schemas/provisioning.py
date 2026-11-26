"""Power-provisioning requirements (issue 017 stage 1) — the battery as load-serving capacity.

A site whose desired demand exceeds the connection's deliverable import in a window (e.g.
18:00–19:00) declares a `ProvisioningRequirement`: the battery must discharge in the window to
serve the shortfall, recharging **only through the same connection in the gaps**. The declared
part is deliberately minimal and auditable — a flat additional kW per window and/or "cover the
metered shortfall" — because the unmet series itself is **derived, never declared**:
``unmet_t = max(0, load_t + aux_t + additional − import_cap_t)`` (the issue-017 stage-2
derivation layer). No fabricated numbers: the only £ field is the customer's declared
counterfactual (a DNO reinforcement quote, a genset, lost production), which requires its
basis verbatim and is otherwise absent — pricing gates additionally require ``reference``
before any FIRM treatment (W7-4 evidence discipline).

Additive to the contract layer (`SCHEMA_VERSION` unchanged, D23/D25 precedent).
`SiteModel.provisioning_requirements` is wired under **D42** (owner-authorized C033) with
pop-if-None hash stability in `quotes.assembly._canonical_site_dump`; the manual 48-slot
daily curve is the D42 "enter a site curve from the outside" path. Calendar shape reused
verbatim from the W7-1 commitment substrate (`AvailabilityWindow`) so every firming product
shares one window semantics.
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from gridq.schemas.commitment import AvailabilityWindow
from gridq.schemas.provenance import GridQModel


class ProvisioningRequirement(GridQModel):
    """A declared obligation to serve load the connection cannot deliver in a window.

    ``additional_load_kw`` is NEW demand the customer states the site needs inside the
    windows (demand the meter has never seen — a declared fact with the requirement as its
    provenance). ``serve_metered_shortfall`` covers the case the meter *does* show: observed
    load riding at/over the import cap (clipped demand). At least one must be active or the
    requirement demands nothing. When both apply the shortfall is physically joint — the
    battery covers ``max(0, total − cap)``; it cannot serve the addition while leaving the
    metered excess unserved.

    ``import_cap_kw`` is the window-effective deliverable import; ``None`` means the site's
    own ``max_import_kw`` applies (the consumer resolves it — never defaulted here).
    ``declared_counterfactual_gbp_per_year`` is the customer's stated cost of the alternative
    and the ONLY £ input this product introduces; ``counterfactual_basis`` (what the figure
    is, verbatim) is mandatory alongside it. A requirement with no ``reference`` is a
    modelling input, not evidence — gates must treat it accordingly (W7-4).
    """

    requirement_id: str = Field(min_length=1, description="Stable id for traceability.")
    windows: list[AvailabilityWindow] = Field(
        min_length=1, description="The recurring windows the obligation covers."
    )
    additional_load_kw: float | None = Field(
        default=None,
        gt=0,
        description="Declared NEW demand (kW, flat within windows) the connection cannot "
        "deliver; None = no new load declared.",
    )
    additional_load_profile_daily_kw: list[float] | None = Field(
        default=None,
        description="Declared NEW demand as a manual half-hourly daily curve entered from "
        "the outside (48 kW values, slot 0 = 00:00 local; applied inside windows on the "
        "days/months the windows cover). Mutually exclusive with additional_load_kw (D42). "
        "A full declared series file ref is the v2 shape.",
    )
    serve_metered_shortfall: bool = Field(
        default=False,
        description="Also cover the metered load's own excess over the import cap "
        "(clipped-demand case).",
    )
    import_cap_kw: float | None = Field(
        default=None,
        gt=0,
        description="Window-effective deliverable import (kW); None = the site's "
        "max_import_kw applies (resolved by the consumer, never defaulted here).",
    )
    tenor_years: float | None = Field(
        default=None,
        gt=0,
        description="Obligation length in years from day one; drives the end-of-tenor "
        "floors (W7-3 closed form).",
    )
    counterparty: str | None = Field(
        default=None, description="Who the obligation is to (normally the site customer)."
    )
    reference: str | None = Field(
        default=None,
        description="Contract / agreement id — the obligation's provenance; required "
        "before any FIRM treatment.",
    )
    declared_counterfactual_gbp_per_year: float | None = Field(
        default=None,
        gt=0,
        description="Customer-declared annual cost of the alternative (DNO reinforcement, "
        "genset, lost production). Never estimated by the engine; absent = the stream "
        "prices £0 and only the engine-measured opportunity cost is reported.",
    )
    counterfactual_basis: str | None = Field(
        default=None,
        description="What the declared counterfactual is, verbatim (e.g. 'NGED reinforcement "
        "quote ref …, annualised over …'). Mandatory alongside the £ figure.",
    )

    @field_validator("additional_load_profile_daily_kw")
    @classmethod
    def _profile_valid(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        if len(v) != 48:
            raise ValueError(
                f"additional_load_profile_daily_kw must have exactly 48 half-hourly slots "
                f"(slot 0 = 00:00 local), got {len(v)}"
            )
        if any(x < 0 for x in v):
            raise ValueError("additional_load_profile_daily_kw values must be >= 0 kW")
        if not any(x > 0 for x in v):
            raise ValueError(
                "additional_load_profile_daily_kw is all-zero — a curve that demands "
                "nothing; omit it instead"
            )
        return v

    @model_validator(mode="after")
    def _one_addition_shape(self) -> ProvisioningRequirement:
        if (
            self.additional_load_kw is not None
            and self.additional_load_profile_daily_kw is not None
        ):
            raise ValueError(
                "declare the addition as EITHER a flat additional_load_kw OR a daily curve "
                "(additional_load_profile_daily_kw), not both (D42)"
            )
        return self

    @model_validator(mode="after")
    def _demands_something(self) -> ProvisioningRequirement:
        if (
            self.additional_load_kw is None
            and self.additional_load_profile_daily_kw is None
            and not self.serve_metered_shortfall
        ):
            raise ValueError(
                "a ProvisioningRequirement must declare additional_load_kw, a daily curve, "
                "and/or set serve_metered_shortfall — otherwise it demands nothing"
            )
        return self

    @model_validator(mode="after")
    def _counterfactual_has_basis(self) -> ProvisioningRequirement:
        if (
            self.declared_counterfactual_gbp_per_year is not None
            and not (self.counterfactual_basis or "").strip()
        ):
            raise ValueError(
                "declared_counterfactual_gbp_per_year requires counterfactual_basis — a £ "
                "figure with no stated basis is an invented number"
            )
        if self.declared_counterfactual_gbp_per_year is None and self.counterfactual_basis:
            raise ValueError(
                "counterfactual_basis without declared_counterfactual_gbp_per_year — the "
                "basis describes a figure that is not there"
            )
        return self
