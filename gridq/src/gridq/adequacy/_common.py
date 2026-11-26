"""Shared types for ``gridq.adequacy`` (WP-SITE-04): exact rationals, record classes and data asks.

Every record in this package is a frozen, strict, None-free `ContractModel` with a canonical
content hash (`gridq.contracts._base`). None is a registered frozen contract: each carries its own
``schema_name`` and ``schema_version``, as the WP-OPS-01 dossier does (D107).

Hashed payloads hold no floats (D171): power is integer watts, energy integer joules
(W x s) or kWh, durations integer seconds, probabilities `ExactRational`. Floats exist only as
display properties.
"""

from __future__ import annotations

from fractions import Fraction
from math import ceil, gcd
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel
from gridq.contracts.evidence import EvidenceType
from gridq.ops.dossier_model import AskHolder, DossierAsk

__all__ = [
    "SNAP",
    "J_PER_KWH",
    "ExactRational",
    "NonNegInt",
    "PosInt",
    "RecordClass",
    "ask",
    "ceil_snapped",
    "combine_record_class",
]

#: Joules (W x s) in one kWh. Every energy in the kernel is an integer number of joules.
J_PER_KWH: Final = 3_600_000
#: IC-3's rounding obligation (D86): snap by 1e-6 before rounding to the safe side.
SNAP: Final = Fraction(1, 10**6)

PosInt = Annotated[int, Field(ge=1)]
NonNegInt = Annotated[int, Field(ge=0)]
RecordClass = Literal["registered", "synthetic_fixture"]


class ExactRational(ContractModel):
    """An exact rational: ``numerator / denominator`` in lowest terms, sign on the numerator, zero
    written ``0/1``. IC-2's `Rational` is limited to (0, 1], so outputs use this instead."""

    numerator: int
    denominator: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _lowest_terms(self) -> ExactRational:
        if gcd(self.numerator, self.denominator) != 1 and self.numerator != 0:
            raise ValueError("an exact rational is written in lowest terms")
        if self.numerator == 0 and self.denominator != 1:
            raise ValueError("zero is written 0/1")
        return self

    @classmethod
    def of(cls, value: Fraction | int) -> ExactRational:
        f = Fraction(value)
        return cls(numerator=f.numerator, denominator=f.denominator)

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    @property
    def as_float(self) -> float:
        """Display only; never hashed."""
        return self.numerator / self.denominator


def ceil_snapped(value: Fraction) -> int:
    """IC-3's safe-side rounding up (D86): snap by 1e-6, then ceil. Exact rational arithmetic."""
    return ceil(value - SNAP)


def combine_record_class(*classes: str) -> RecordClass:
    """``synthetic_fixture`` if any input is, else ``registered``."""
    return "synthetic_fixture" if "synthetic_fixture" in classes else "registered"


def ask(
    request: str,
    *,
    site_id: str,
    holder: AskHolder = AskHolder.CUSTOMER,
    closes_with: tuple[EvidenceType, ...] = (),
    prerequisites: tuple[str, ...] = (),
) -> DossierAsk:
    """A typed, content-addressed data ask for one site (the D108 shape, reused)."""
    return DossierAsk.make(
        request=request,
        holder=holder,
        scope=f"site:{site_id}",
        closes_with=closes_with,
        prerequisites=prerequisites,
    )
