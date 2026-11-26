"""Market value streams — the per-stream result the optimiser reports."""

from __future__ import annotations

from pydantic import Field, model_validator

from gridq.schemas.enums import Firmness, MarketStream
from gridq.schemas.provenance import GridQModel

# Streams that require a route-to-market partner / further consents — gated upside.
# They can never be marked FIRM in the priced case.
GATED_UPSIDE_STREAMS: frozenset[MarketStream] = frozenset(
    {
        MarketStream.WHOLESALE_P415,
        MarketStream.BALANCING_MECHANISM,
        MarketStream.ANCILLARY_DC_DM_DR,
    }
)

#: The firmness classes whose revenue is IN the priced floor. Everything else — ``CONDITIONAL``
#: (unlockable upside, including all gated upside) and ``EXCLUDED`` (explicitly priced out) — is
#: held out of the floor and out of anything debt is sized against.
#:
#: Declared here, beside `ValueStreamResult`, because two layers now have to agree on it and a
#: second copy of the set is a second definition of the priced floor. `finance.cashflows._is_priced`
#: delegates to :func:`is_priced_floor`, and `sizing.sizer._grid_point` uses it to stamp
#: ``SizingGridStreamRevenue.in_priced_floor``, so a per-candidate floor breakdown on the sizing
#: grid is provably composed of the same streams the finance model summed into
#: ``CashflowModel.total_annual_revenue_gbp``.
PRICED_FLOOR_FIRMNESS: frozenset[Firmness] = frozenset({Firmness.FIRM, Firmness.CONTRACTED})


class ValueStreamResult(GridQModel):
    """The annual revenue/cost and firmness of one market stream for a site."""

    stream: MarketStream
    annual_revenue_gbp: float = Field(default=0.0, ge=0)
    annual_cost_gbp: float = Field(default=0.0, ge=0)
    firmness: Firmness
    gate_status: str | None = None
    unlock_condition: str | None = None
    assumptions_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    consent_status: str | None = Field(
        default=None,
        description="The orthogonal 'consent given' label (Revenue-Data Drive / D43): 'given' "
        "when the customer explicitly consented to this stream at intake, else None. Additive "
        "and optional — carried through from the gate; it never affects the firmness bar (a "
        "gated-upside stream still cannot be FIRM) and drives the proposal's 'consent given' "
        "line.",
    )

    @model_validator(mode="after")
    def _gated_cannot_be_firm(self) -> ValueStreamResult:
        if self.stream in GATED_UPSIDE_STREAMS and self.firmness == Firmness.FIRM:
            raise ValueError(
                f"gated-upside stream {self.stream.value!r} cannot be marked firm "
                f"(allowed: contracted/conditional/excluded)."
            )
        return self


def is_priced_floor(stream_result: ValueStreamResult) -> bool:
    """Whether this stream's revenue is IN the priced floor (firmness ∈ FIRM/CONTRACTED).

    The single definition of floor membership, shared by finance and by the sizing grid — see
    :data:`PRICED_FLOOR_FIRMNESS`. It reads the recorded firmness and nothing else: no £ is
    inspected, so a floor stream that earned exactly ``0.0`` stays a floor stream (a priced result
    of zero), and a held-out stream carrying a real ``annual_revenue_gbp`` stays held out (its £ is
    upside, not floor). Conflating either direction is the D45 zero-provenance defect.
    """
    return stream_result.firmness in PRICED_FLOOR_FIRMNESS
