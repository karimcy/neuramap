"""Assumption-move / reprice-tolerance check (X.1 · consistency contract #3).

The System Brief: *a quote is valid only against the assumption version that produced it;
material updates trigger repricing*, and for the underwritten state, *if the assumption library
moves beyond tolerance before signature, the quote reprices — nothing is reconstructed after the
fact*.

This module makes that falsifiable. It compares the assumption set a quote was priced against
with the current library, leaf by leaf, and decides — against the library's own
``quote_validity.reprice_tolerance_pct`` (no private constant) — whether any move is *material*
(beyond tolerance). A numeric leaf moving by more than the tolerance, or any structural change
(an assumption added, removed, or a non-numeric value changed), is a breach that forces a reprice.

The tolerance is a relative percentage sourced from the library, so the check is deterministic
and carries no hardcoded number of its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from gridq.assumptions.loader import AssumptionError, load_assumptions
from gridq.assumptions.models import SECTION_NAMES, AssumptionLibrary

if TYPE_CHECKING:
    from gridq.schemas import QuoteRecord

#: Where the reprice tolerance lives in the library (a versioned source, not a private constant).
TOLERANCE_SECTION = "quote_validity"
TOLERANCE_NAME = "reprice_tolerance_pct"

#: Sentinel for "this leaf is present on one side only" (a structural add/drop).
_ABSENT = object()


@dataclass(frozen=True)
class AssumptionMove:
    """One leaf that differs between the priced-against set and the current library.

    ``change_pct`` is the relative percentage move for a numeric leaf (``math.inf`` if it moved
    away from zero); ``None`` for a structural change (added/removed, or a non-numeric value
    changed), which is always material.
    """

    key: str
    priced: Any
    current: Any
    change_pct: float | None
    beyond_tolerance: bool


@dataclass(frozen=True)
class RepriceCheck:
    """Verdict: must this quote reprice because the library moved beyond tolerance?"""

    tolerance_pct: float
    moves: list[AssumptionMove] = field(default_factory=list)

    @property
    def breaches(self) -> list[AssumptionMove]:
        """The subset of moves that are beyond tolerance (the ones that force the reprice)."""
        return [m for m in self.moves if m.beyond_tolerance]

    @property
    def needs_reprice(self) -> bool:
        """True iff any move is beyond tolerance — derived from `breaches`, never stored."""
        return bool(self.breaches)

    def summary(self) -> str:
        """A one-line, human-readable reason — empty string when no reprice is needed."""
        if not self.needs_reprice:
            return ""
        parts = []
        for m in self.breaches:
            if m.change_pct is None:
                parts.append(f"{m.key}: {m.priced!r} → {m.current!r}")
            else:
                pct = "∞" if math.isinf(m.change_pct) else f"{m.change_pct:.1f}%"
                parts.append(f"{m.key}: {m.priced} → {m.current} ({pct})")
        return (
            f"assumption update beyond {self.tolerance_pct:g}% tolerance — reprice required: "
            + "; ".join(parts)
        )


def reprice_tolerance_pct(library: AssumptionLibrary) -> float:
    """Read the relative reprice tolerance (%) from the library; never hardcoded.

    Raises `AssumptionError` if the library carries no tolerance, since the check cannot be made
    honestly without it.
    """
    try:
        assumption = library.get(TOLERANCE_SECTION, TOLERANCE_NAME)
    except KeyError:
        raise AssumptionError(
            f"library {library.metadata.version!r} has no "
            f"{TOLERANCE_SECTION}.{TOLERANCE_NAME}; cannot assess reprice tolerance."
        ) from None
    return float(assumption.value)


def _is_number(value: Any) -> bool:
    """True for real numbers (``bool`` excluded — booleans compare by identity, not magnitude)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a (possibly structured) assumption value to ``leaf-path -> scalar``."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, sub in value.items():
            out.update(_flatten(sub, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            out.update(_flatten(sub, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def _leaf_move(key: str, priced: Any, current: Any, tolerance_pct: float) -> AssumptionMove | None:
    """Compare one leaf; return an `AssumptionMove` if it differs, else ``None``."""
    if priced is _ABSENT or current is _ABSENT:
        return AssumptionMove(key, priced, current, change_pct=None, beyond_tolerance=True)
    if _is_number(priced) and _is_number(current):
        if priced == current:
            return None
        if priced == 0:
            change_pct = math.inf
        else:
            change_pct = abs(current - priced) / abs(priced) * 100.0
        return AssumptionMove(
            key, priced, current, change_pct, beyond_tolerance=change_pct > tolerance_pct
        )
    # Non-numeric leaf: any inequality is a structural/material change.
    if priced != current:
        return AssumptionMove(key, priced, current, change_pct=None, beyond_tolerance=True)
    return None


def assess_reprice(priced_against: AssumptionLibrary, current: AssumptionLibrary) -> RepriceCheck:
    """Compare two libraries assumption by assumption and decide whether a reprice is required.

    The tolerance is read from ``current`` (the policy in force at the check). A breach is any
    of: an assumption added or removed; its ``unit`` changed (a redenomination is material even
    at the same number); or a numeric value leaf that moved beyond the relative tolerance. Only
    the assumptions' ``value``/``unit`` are compared — library ``metadata`` (version, currency,
    release date) is out of scope, so a version bump alone never forces a reprice.
    """
    tolerance_pct = reprice_tolerance_pct(current)
    moves: list[AssumptionMove] = []
    for section in SECTION_NAMES:
        priced_section: dict[str, Any] = getattr(priced_against, section)
        current_section: dict[str, Any] = getattr(current, section)
        for name in sorted(set(priced_section) | set(current_section)):
            key = f"{section}.{name}"
            in_priced, in_current = name in priced_section, name in current_section
            if in_priced != in_current:
                # The whole assumption was added or removed — always material, regardless of
                # what its value flattens to (an empty mapping/list still counts).
                moves.append(
                    AssumptionMove(
                        key,
                        priced_section[name].value if in_priced else _ABSENT,
                        current_section[name].value if in_current else _ABSENT,
                        change_pct=None,
                        beyond_tolerance=True,
                    )
                )
                continue
            priced, present = priced_section[name], current_section[name]
            if priced.unit != present.unit:
                moves.append(
                    AssumptionMove(
                        f"{key} [unit]", priced.unit, present.unit, None, beyond_tolerance=True
                    )
                )
            priced_leaves = _flatten(priced.value)
            current_leaves = _flatten(present.value)
            for leaf in sorted(set(priced_leaves) | set(current_leaves)):
                move = _leaf_move(
                    key + (f".{leaf}" if leaf else ""),
                    priced_leaves.get(leaf, _ABSENT),
                    current_leaves.get(leaf, _ABSENT),
                    tolerance_pct,
                )
                if move is not None:
                    moves.append(move)
    return RepriceCheck(tolerance_pct=tolerance_pct, moves=moves)


def assess_quote_reprice(quote: QuoteRecord, current: AssumptionLibrary) -> RepriceCheck:
    """Assess whether ``quote`` must reprice against the ``current`` library.

    Loads the assumption set the quote was priced against (by its ``assumption_version``) and
    compares it to ``current``. Raises `AssumptionError` if the quote carries no version.
    """
    if quote.assumption_version is None:
        raise AssumptionError("quote has no assumption_version; cannot assess reprice.")
    priced_against = load_assumptions(quote.assumption_version)
    return assess_reprice(priced_against, current)
