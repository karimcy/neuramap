"""The derivation ledger: every number an adapter emits, traced to the facts it came from.

Decision D159 (Fable 5.2). IC-9 is frozen, so the trace cannot live inside the
declaration; it is a sibling record bound to it by hash (`gridq.datacentre.orchestrators.outcome`).

An adapter never writes a bare integer into its draft. It asks the `LedgerBuilder` for a `Traced`
value, stating the rule, the exact quantity (a `Fraction`), the state and policy fields it read
(`SourceRef`, by RFC 6901 JSON pointer), and the direction IC-9 rounds it to the safe side:

- ``down`` for kW, basis points and hold minutes (an offer is never larger than the facts allow);
- ``up`` for notice and ramp seconds (an offer is never faster than the facts allow);
- ``none`` for a quantity that is exact by rule (it must already be an integer).

The rounding happens here, once, so no adapter can round the wrong way. `seal` then walks the draft,
replaces every `Traced` by its integer and records the JSON pointer it landed at. A bare integer
left in the draft is an untraced number: the base adapter refuses the draft
(``UNTRACED_VALUE``), so a number with no stated origin can never reach a declaration.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr

__all__ = [
    "DerivationLedger",
    "LedgerBuilder",
    "LedgerEntry",
    "Rounding",
    "SourceRef",
    "Traced",
    "UntracedValue",
    "pointer",
    "seal",
]

Rounding = Literal["down", "up", "none"]
Scalar = str | int | float | bool


def _escape(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer(*tokens: str | int) -> str:
    """An RFC 6901 JSON pointer from its reference tokens (``/`` and ``~`` escaped)."""
    return "".join("/" + _escape(t) for t in tokens)


class SourceRef(ContractModel):
    """One fact a number was derived from: a field of the state or the policy document, or a
    constant the adapter's rule fixes (``rule``: the pointer is empty, the rule id says which)."""

    doc: Literal["state", "policy", "rule"]
    json_pointer: str
    value: Scalar


class LedgerEntry(ContractModel):
    """How one emitted integer was derived."""

    target_pointer: str
    rule_id: NonEmptyStr
    inputs: tuple[SourceRef, ...] = Field(min_length=1)
    #: The exact quantity before rounding, as ``"n"`` or ``"n/d"`` (`Fraction`'s ``str``).
    exact: NonEmptyStr
    emitted: int
    rounding: Rounding


class DerivationLedger(ContractModel):
    """Every emitted integer of one declaration, sorted by its pointer into the declaration."""

    kind: Literal["derivation_ledger"]
    entries: tuple[LedgerEntry, ...]

    @model_validator(mode="after")
    def _sorted(self) -> DerivationLedger:
        targets = [e.target_pointer for e in self.entries]
        if targets != sorted(set(targets)):
            raise ValueError("ledger entries must be sorted by target_pointer with no repeats")
        return self

    def at(self, target: str) -> LedgerEntry:
        for e in self.entries:
            if e.target_pointer == target:
                return e
        raise KeyError(target)


@dataclass(frozen=True)
class Traced:
    """A rounded integer with its derivation, waiting for `seal` to place it."""

    value: int
    rule_id: str
    inputs: tuple[SourceRef, ...]
    exact: Fraction
    rounding: Rounding


class UntracedValue(ValueError):
    """The draft holds a number that did not come from the ledger."""

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


def _round(exact: Fraction, rounding: Rounding) -> int:
    if rounding == "down":
        return math.floor(exact)
    if rounding == "up":
        return math.ceil(exact)
    if exact.denominator != 1:
        raise ValueError(f"an exact ('none') quantity must be an integer, not {exact}")
    return int(exact)


class LedgerBuilder:
    """Hands an adapter its traced integers. One builder per `declare` call."""

    def derive(
        self,
        rule_id: str,
        exact: Fraction | int,
        rounding: Rounding,
        inputs: Sequence[SourceRef],
    ) -> Traced:
        """The integer IC-9 emits for ``exact``, rounded in the stated direction."""
        if not inputs:
            raise ValueError(f"rule {rule_id}: a derived number names at least one source")
        if isinstance(exact, bool) or not isinstance(exact, Fraction | int):
            raise TypeError(f"rule {rule_id}: exact must be a Fraction or an int")
        q = Fraction(exact)
        return Traced(
            value=_round(q, rounding),
            rule_id=rule_id,
            inputs=tuple(inputs),
            exact=q,
            rounding=rounding,
        )


def seal(draft: Mapping[str, Any]) -> tuple[dict[str, Any], DerivationLedger]:
    """Replace every `Traced` in ``draft`` by its integer and record where it landed. Raises
    `UntracedValue` at the first bare number (int or float, not bool)."""
    entries: list[LedgerEntry] = []

    def walk(node: Any, tokens: tuple[str | int, ...]) -> Any:
        if isinstance(node, Traced):
            target = pointer(*tokens)
            entries.append(
                LedgerEntry(
                    target_pointer=target,
                    rule_id=node.rule_id,
                    inputs=node.inputs,
                    exact=str(node.exact),
                    emitted=node.value,
                    rounding=node.rounding,
                )
            )
            return node.value
        if isinstance(node, Mapping):
            return {k: walk(v, (*tokens, k)) for k, v in node.items()}
        if isinstance(node, list | tuple):
            return [walk(v, (*tokens, i)) for i, v in enumerate(node)]
        if isinstance(node, bool) or node is None or isinstance(node, str):
            return node
        if isinstance(node, int | float):
            raise UntracedValue(pointer(*tokens))
        raise TypeError(f"{pointer(*tokens)}: {type(node).__name__} is not JSON")

    sealed = walk(draft, ())
    entries.sort(key=lambda e: e.target_pointer)
    return sealed, DerivationLedger(kind="derivation_ledger", entries=tuple(entries))
