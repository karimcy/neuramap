"""Load for the adequacy simulator: one-minute facility and IT power, every draw, as integer watts.

Two sources meet one protocol (`LoadSource`):

- `SynthesisLoad` wraps a `gridq.datacentre.Synthesis` (WP-SITE-01) and reads its one-minute
  windows, which the load model generates on their own (D82);
- `MinuteLoadArray` holds explicit arrays, for labelled synthetic fixtures and for metered data a
  later producer may supply.

Power is quantised once, at this boundary, to integer watts (``floor(kW x 1000 + 0.5)``), so the
kernel is exact integer arithmetic (D171, D174).

**Time base (D170).** The load model's clock is naive local time with 1,440 minutes in
every day. A scenario step at UTC instant ``u`` reads the load minute at ``u``'s Europe/London wall
time, shifted by the scenario's `LoadAnchor` (whole days). So on the spring-forward day the
missing local hour is never read, and on the autumn day the repeated hour is read twice; both are
counted. A replay scenario in another year is anchored to a load date by a stated `LoadAnchor`;
without one, the first horizon date with the same month and day is used and recorded. Nothing is
tiled or wrapped: a mapping outside the horizon is a data ask.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import model_validator

from gridq.adequacy._common import PosInt, RecordClass
from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    NotApplicable,
    Sha256,
    not_applicable,
)
from gridq.datacentre.synthesiser import Synthesis

__all__ = [
    "CLOCK",
    "LoadAnchor",
    "LoadIdentity",
    "LoadMapping",
    "LoadSource",
    "MinuteLoadArray",
    "SynthesisLoad",
    "map_scenario_minutes",
    "quantise_w",
]

CLOCK: Final = "Europe/London"
_TZ: Final = ZoneInfo(CLOCK)
MINUTES_PER_DAY: Final = 1440


def quantise_w(kw: np.ndarray) -> np.ndarray:
    """kW floats to integer watts, round half up. Elementwise IEEE operations only."""
    return np.floor(np.asarray(kw, dtype=np.float64) * 1000.0 + 0.5).astype(np.int64)


class LoadIdentity(ContractModel):
    """What load a run read, by hash. ``series_digest`` identifies the values themselves."""

    kind: Literal["datacentre_synthesis", "minute_array"]
    site_id: NonEmptyStr
    record_class: RecordClass
    draws: PosInt
    horizon_start: IsoDate
    horizon_end: IsoDate
    load_inputs_hash: Sha256 | NotApplicable
    seed: int | NotApplicable
    series_digest: Sha256


@runtime_checkable
class LoadSource(Protocol):
    """One-minute load over a naive-local horizon of whole days."""

    @property
    def identity(self) -> LoadIdentity: ...

    def minutes(self, m0: int, m1: int) -> tuple[np.ndarray, np.ndarray]:
        """Facility and IT watts, int64 ``(draws, m1 - m0)``, for horizon minutes ``[m0, m1)``."""
        ...


@dataclass(frozen=True)
class SynthesisLoad:
    """A data-centre load ensemble (WP-SITE-01) read at one minute. The record class is stated by
    the caller: the load model's inputs do not carry one."""

    synthesis: Synthesis
    record_class: RecordClass

    @property
    def identity(self) -> LoadIdentity:
        s = self.synthesis
        return LoadIdentity(
            kind="datacentre_synthesis",
            site_id=s.inputs.site_id,
            record_class=self.record_class,
            draws=s.draws,
            horizon_start=s.inputs.horizon_start,
            horizon_end=s.inputs.horizon_end,
            load_inputs_hash=s.inputs_hash,
            seed=s.seed,
            series_digest=s.series_hash,
        )

    def minutes(self, m0: int, m1: int) -> tuple[np.ndarray, np.ndarray]:
        h0 = datetime.combine(self.synthesis.inputs.horizon_start, datetime.min.time())
        w = self.synthesis.one_minute(
            h0 + timedelta(minutes=m0), h0 + timedelta(minutes=m1), jensen_gap_flag_kw=None
        )
        return quantise_w(w.members_kw), quantise_w(w.it_members_kw)


@dataclass(frozen=True)
class MinuteLoadArray:
    """Explicit one-minute load in integer watts, ``(draws, days x 1440)``. For labelled synthetic
    fixtures (site ids starting ``SYNTHETIC-``) and for a later metered producer."""

    site_id: str
    record_class: RecordClass
    horizon_start: date
    facility_w: np.ndarray
    it_w: np.ndarray

    def __post_init__(self) -> None:
        f, i = self.facility_w, self.it_w
        if f.ndim != 2 or f.shape != i.shape:
            raise ValueError("facility_w and it_w are (draws, minutes) arrays of one shape")
        if f.shape[1] % MINUTES_PER_DAY:
            raise ValueError("the horizon is whole days of 1,440 minutes")
        if f.dtype != np.int64 or i.dtype != np.int64:
            raise ValueError("load is integer watts (int64)")
        if (f < 0).any() or (i < 0).any() or (i > f).any():
            raise ValueError("load is non-negative and IT load never exceeds facility load")
        if (self.record_class == "synthetic_fixture") != self.site_id.startswith("SYNTHETIC-"):
            raise ValueError("site_id starts with SYNTHETIC- exactly when the record is synthetic")

    @property
    def identity(self) -> LoadIdentity:
        h = hashlib.sha256()
        h.update(f"{self.facility_w.shape}".encode())
        h.update(self.facility_w.astype("<i8").tobytes())
        h.update(self.it_w.astype("<i8").tobytes())
        days = self.facility_w.shape[1] // MINUTES_PER_DAY
        return LoadIdentity(
            kind="minute_array",
            site_id=self.site_id,
            record_class=self.record_class,
            draws=self.facility_w.shape[0],
            horizon_start=self.horizon_start,
            horizon_end=self.horizon_start + timedelta(days=days),
            load_inputs_hash=not_applicable("explicit arrays: the values are the input"),
            seed=not_applicable("explicit arrays: nothing is drawn"),
            series_digest="sha256:" + h.hexdigest(),
        )

    def minutes(self, m0: int, m1: int) -> tuple[np.ndarray, np.ndarray]:
        return self.facility_w[:, m0:m1], self.it_w[:, m0:m1]


# --------------------------------------------------------------------------- time base


class LoadAnchor(ContractModel):
    """Maps a scenario's first local date to a load-model date (a whole-day shift)."""

    scenario_id: NonEmptyStr
    load_anchor_date: IsoDate


class LoadMapping(ContractModel):
    """How one scenario's minutes were mapped onto the load horizon (reported on the result)."""

    scenario_id: NonEmptyStr
    scenario_local_start: IsoDate
    load_anchor_date: IsoDate
    anchor_basis: Literal["stated", "same_month_day_in_horizon"]
    dst_skipped_minutes: int
    dst_repeated_minutes: int

    @model_validator(mode="after")
    def _non_negative(self) -> LoadMapping:
        if self.dst_skipped_minutes < 0 or self.dst_repeated_minutes < 0:
            raise ValueError("counts are non-negative")
        return self


def local_start_date(period_start: datetime) -> date:
    return period_start.astimezone(_TZ).date()


def resolve_anchor(
    scenario_id: str,
    period_start: datetime,
    anchors: tuple[LoadAnchor, ...],
    horizon: tuple[date, date],
) -> tuple[date, Literal["stated", "same_month_day_in_horizon"]] | None:
    """The load date the scenario's first local date maps to, or None when none can be found."""
    for a in anchors:
        if a.scenario_id == scenario_id:
            return a.load_anchor_date, "stated"
    start = local_start_date(period_start)
    d = horizon[0]
    while d < horizon[1]:
        if (d.month, d.day) == (start.month, start.day):
            return d, "same_month_day_in_horizon"
        d += timedelta(days=1)
    return None


def map_scenario_minutes(
    period_start: datetime, n_minutes: int, *, horizon_start: date, shift_days: int
) -> tuple[np.ndarray, int, int]:
    """Load-horizon minute index for each scenario minute, and the DST skipped/repeated counts.

    ``idx[k]`` is the naive local minute of ``period_start + k`` minutes on `CLOCK`, shifted by
    ``shift_days``, counted from ``horizon_start`` 00:00. The UTC offset is read per half-hour
    (IC-2 periods start on the half-hour, and GB clock changes fall on the hour).
    """
    ps = period_start.astimezone(UTC)
    h0 = datetime.combine(horizon_start, datetime.min.time(), tzinfo=UTC)
    base = int((ps - h0).total_seconds() // 60) + shift_days * MINUTES_PER_DAY
    n_blocks = -(-n_minutes // 30)
    offsets = np.empty(n_blocks, dtype=np.int64)
    for b in range(n_blocks):
        off = (ps + timedelta(minutes=30 * b)).astimezone(_TZ).utcoffset()
        assert off is not None
        offsets[b] = int(off.total_seconds() // 60)
    k = np.arange(n_minutes, dtype=np.int64)
    idx = base + k + offsets[k // 30]
    steps = np.diff(idx)
    skipped = int(np.sum(steps[steps > 1] - 1))
    repeated = int(np.sum(1 - steps[steps < 1]))
    return idx, skipped, repeated
