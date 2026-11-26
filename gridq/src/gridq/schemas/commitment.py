"""Availability commitments (W7-1) — the market-agnostic firming-contract shape.

The common substrate of every firming product (north_star Wave 7): a Capacity Market
agreement, a DSO flexibility window and a customer resilience reserve all commit the battery
to HOLD capability in defined windows for a contract term. This module holds that shape as
pure, validated data — no market branch, and **no fabricated numbers**: commitments are
produced from contract/tender records (the W7-4 GateContext producer), never defaulted into
existence.

The consumer side is the W7-2 constraint compiler: a `DispatchConstraintDescriptor` whose
``kind`` is ``'availability_window'`` / ``'held_state_of_energy'`` carries an
`AvailabilityCommitment` in its typed ``parameters`` payload, and the solver compiles it into
reserved net-injection headroom and held state-of-energy constraints over the window
half-hours. Additive to the contract layer (D23/D25 precedent — `SiteModel` /
`OptimisationResult` untouched, `SCHEMA_VERSION` unchanged); the seam design is flagged for
Olly as OPEN_COMMENTS **C026**.
"""

from __future__ import annotations

from datetime import time

from pydantic import Field, field_validator, model_validator

from gridq.schemas.enums import MarketStream
from gridq.schemas.provenance import GridQModel


class AvailabilityWindow(GridQModel):
    """One recurring window in which committed capability must be available.

    Market-agnostic calendar shape: optional month and day-of-week filters (``None`` means
    every month / every day) plus a same-day ``[start_time, end_time)`` span. An overnight
    window (e.g. 23:00–01:00) is expressed as **two** windows — 23:00 to midnight and
    midnight to 01:00 — where ``end_time = 00:00`` means the exclusive END of the day
    (24:00), so the pre-midnight half is representable (adoption review W7-5). The compiler
    maps times onto half-hour indices deterministically (GB local clock, mirroring the DUoS
    bands), so the schema itself never needs timezone or wrap logic.
    """

    label: str | None = Field(
        default=None, description="Optional human-readable name, e.g. 'winter evening peak'."
    )
    months: list[int] | None = Field(
        default=None,
        description="Months (1–12) the window applies to; None = all year.",
    )
    days_of_week: list[int] | None = Field(
        default=None,
        description="Days (0=Monday … 6=Sunday) the window applies to; None = every day.",
    )
    start_time: time = Field(description="Window start (inclusive).")
    end_time: time = Field(
        description="Window end (exclusive); after start_time, or 00:00 meaning end-of-day."
    )

    @field_validator("months")
    @classmethod
    def _months_valid(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("months must be non-empty when provided (None = all year)")
        bad = [m for m in v if not 1 <= m <= 12]
        if bad:
            raise ValueError(f"months must be in 1..12, got {bad}")
        if len(set(v)) != len(v):
            raise ValueError(f"months must not repeat, got {v}")
        return sorted(v)

    @field_validator("days_of_week")
    @classmethod
    def _days_valid(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("days_of_week must be non-empty when provided (None = every day)")
        bad = [d for d in v if not 0 <= d <= 6]
        if bad:
            raise ValueError(f"days_of_week must be in 0..6 (0=Monday), got {bad}")
        if len(set(v)) != len(v):
            raise ValueError(f"days_of_week must not repeat, got {v}")
        return sorted(v)

    @model_validator(mode="after")
    def _span_valid(self) -> AvailabilityWindow:
        # end_time == 00:00 is the exclusive end of the day (24:00) — the only way the
        # pre-midnight half of an overnight window is representable (adoption review W7-5).
        if self.end_time != time(0, 0) and self.end_time <= self.start_time:
            raise ValueError(
                "end_time must be after start_time, or 00:00 meaning end-of-day (express an "
                "overnight window as two windows)"
            )
        return self


class AvailabilityCommitment(GridQModel):
    """A contracted availability commitment — capability the battery must HOLD, not may earn.

    ``committed_power_kw`` is the net-injection capability that must be deliverable on call
    throughout every window; ``held_energy_kwh`` is the explicit state-of-energy floor to hold
    during windows — ``None`` means no explicit energy hold is contracted, and the consumer
    must never invent one. ``tenor_years`` is the contract length from day one; it drives the
    closed-form end-of-tenor deliverability check (W7-3: capability at degraded year-N ≥ the
    commitment). ``counterparty``/``reference`` carry the contract's provenance — a commitment
    with no reference is a modelling input, not evidence, and gates must treat it accordingly.
    """

    commitment_id: str = Field(min_length=1, description="Stable id for traceability.")
    stream: MarketStream = Field(description="The value stream this commitment belongs to.")
    committed_power_kw: float = Field(
        gt=0, description="Committed deliverable power (kW) throughout every window."
    )
    held_energy_kwh: float | None = Field(
        default=None,
        ge=0,
        description="Explicit state-of-energy floor (kWh) to hold during windows; None = no "
        "explicit hold contracted (never defaulted).",
    )
    windows: list[AvailabilityWindow] = Field(
        min_length=1, description="The recurring windows the commitment covers."
    )
    tenor_years: float | None = Field(
        default=None,
        gt=0,
        description="Contract length in years from day one; drives the end-of-tenor "
        "deliverability check (W7-3).",
    )
    counterparty: str | None = Field(
        default=None, description="Who the commitment is to (DSO, EMR body, customer)."
    )
    reference: str | None = Field(
        default=None,
        description="Contract / tender / agreement id — the commitment's provenance.",
    )
    #: WHO asserts this commitment exists, and on WHAT. Additive and defaulted ``None`` (D54): a
    #: commitment supplied without them reads exactly as before, so no existing record moves.
    #:
    #: They exist because the consumer's default sentence is a factual claim it cannot check. Until
    #: D54 every local-flex commitment reaching `sizing.deliverability.produce_gate_context` was
    #: written into the evidence record as ``confirmed_by='contract-record'`` with the basis
    #: "awarded tender / contracted commitment reference supplied on the constraint seam" — true
    #: for a commitment transcribed from a DNO award, FALSE for one the site's OWNER attests. The
    #: two must never share report language: a lender reading "contract-record" is entitled to
    #: believe a contract was seen. So the asserter travels WITH the assertion.
    confirmed_by: str | None = Field(
        default=None,
        description="Who asserts this commitment exists — a named person for an attestation, a "
        "system/record for a transcribed contract. None ⇒ the consumer's own default label.",
    )
    basis: str | None = Field(
        default=None,
        description="What the assertion rests on, verbatim (e.g. 'owner attestation, on good "
        "authority'). None ⇒ the consumer's own default sentence.",
    )


class CommitmentEvidence(GridQModel):
    """The provenance of one firming gate fact — who confirmed what, against which contract.

    Replaces bare booleans as the carrier of gate evidence (W7-4): every asserted gate fact
    names its confirmer, its basis and the contract it rests on. ``reference`` is **required**
    — a fact without a contract reference is a modelling input, not evidence, and a producer
    must not emit it. An engine-computed verdict says so in ``confirmed_by``/``basis``
    (arithmetic on library values is labelled arithmetic, never dressed up as a third-party
    confirmation).
    """

    commitment_id: str = Field(min_length=1)
    stream: MarketStream
    verdict: bool = Field(description="The confirmed fact (deliverable / tender contracted).")
    confirmed_by: str = Field(
        min_length=1,
        description="Who/what confirmed it — a named person, system, or "
        "'engine:<check>' for a computed verdict.",
    )
    basis: str = Field(
        min_length=1, description="What the confirmation rests on (the working, verbatim)."
    )
    reference: str = Field(
        min_length=1, description="Contract / tender / agreement id — required provenance."
    )
    final_contract_year: float | None = Field(
        default=None, gt=0, description="Years from day one the fact is verified to."
    )
    verified_capacity_kw: float | None = Field(
        default=None, ge=0, description="The deliverable kW the fact was verified at."
    )
