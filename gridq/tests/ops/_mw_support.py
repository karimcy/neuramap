"""Synthetic fillings and environments for the demonstrator-MW tests (WP-OPS-11).

Every filling here is a ``synthetic_fixture`` and every series a TEST FIXTURE. Nothing here is an
agreed filling, a real site or a real reading.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from gridq.contracts._base import NotApplicable
from gridq.ops.mw_formula import (
    Env,
    Filling,
    NumberFill,
    Q,
    Reading,
    SiteEnv,
    TermValue,
    utc_half_hours,
)

SYNTHETIC_NA = NotApplicable(kind="not_applicable", reason="TEST FIXTURE: synthetic filling")

#: The shared slots filled the plainest way; each test overrides what it exercises.
BASE: dict[str, str] = {
    "authority_fail": "exclude_interval",
    "coincidence": "coincident",
    "gap_policy": "unknown_if_any_gap",
    "quality_fail": "exclude_interval",
    "resolution": "s1800",
    "statistic": "mean",
    "window": "demonstrator_days_all_day",
}

#: Four half-hours on the first day of the demonstrator window (GMT, so local = UTC).
START = datetime(2028, 11, 1, 0, 0, tzinfo=UTC)
IVS = utc_half_hours(START, 4)


def fill(
    choices: Mapping[str, str], numbers: Mapping[str, Fraction | int] | None = None
) -> Filling:
    return Filling(
        record_class="synthetic_fixture",
        agreement=SYNTHETIC_NA,
        choices=tuple(sorted(choices.items())),
        numbers=tuple(
            NumberFill(slot_id=k, value=Q.of(v)) for k, v in sorted((numbers or {}).items())
        ),
    )


def series(*values: Any, quality: str = "good") -> tuple[Reading | None, ...]:
    """A series; ``None`` is a gap; ``(v, q)`` gives one reading its own quality."""
    out: list[Reading | None] = []
    for v in values:
        if v is None:
            out.append(None)
        elif isinstance(v, tuple):
            out.append(Reading(v[0] if isinstance(v[0], bool) else Fraction(v[0]), v[1]))
        else:
            out.append(Reading(v if isinstance(v, bool) else Fraction(v), quality))  # type: ignore[arg-type]
    return tuple(out)


def flags(*values: bool | None) -> tuple[Reading | None, ...]:
    return tuple(None if v is None else Reading(v) for v in values)


def authorised(n: int = 4) -> dict[str, TermValue]:
    """The shared condition terms, all true: signed off, authority held, envelope in force."""
    return {
        "control_signoff": True,
        "control_authority": flags(*([True] * n)),
        "envelope_in_force": flags(*([True] * n)),
    }


def site(site_id: str, **terms: TermValue) -> SiteEnv:
    return SiteEnv(site_id=site_id, terms={**authorised(), **terms})


def env(*sites: SiteEnv, intervals: tuple[datetime, ...] = IVS, resolution_s: int = 1800) -> Env:
    return Env(resolution_s=resolution_s, intervals=intervals, sites=sites)
