"""The ``data_centre`` load archetype: a `ReferenceProfile` that refuses to be a shape.

Decision D84 (Fable 5.2). CORE's archetype registry holds `ReferenceProfile` items, and
CORE's ``derive_half_hourly`` scales a profile's day shape to a site's measured totals. A
pre-operational data centre has neither a measured total nor a typical shape: its load is what its
ramp, plant and utilisation make it. So this archetype registers under the chassis registry like
any other, carries all-zero shape tuples, and its `day_shape` raises `SynthesisError`. The CORE
route therefore refuses it whatever the site states (with no total it refuses first anyway), and
the series is built by its own hook, `DataCentreArchetype.synthesise`, reached through
`gridq.datacentre.derive.derive_data_centre_half_hourly`.

The month factors are the neutral values CORE's dataclass needs to exist; they are never applied,
because `day_shape` refuses before any weight is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from gridq.datacentre.inputs import HALF_HOURS_PER_DAY, DcLoadInputs
from gridq.datacentre.synthesiser import METHOD_VERSION, Synthesis, synthesise
from gridq.datacentre.workload_flex import NO_ENVELOPE_DECLARED, WorkloadFlex
from gridq.ingestion.hh_synthesis import ProfileSource, ReferenceProfile, SynthesisError

__all__ = ["ARCHETYPE_ID", "DATA_CENTRE", "DataCentreArchetype"]

ARCHETYPE_ID: Final = "data_centre"

_ZEROS: Final = (0.0,) * HALF_HOURS_PER_DAY


@dataclass(frozen=True)
class DataCentreArchetype(ReferenceProfile):
    """A component-model archetype: no shape to scale, a synthesiser instead (the D73 hook)."""

    def day_shape(self, weekday_index: int) -> tuple[float, ...]:
        raise SynthesisError(
            f"'{self.profile_id}' has no reference day shape to scale to a measured total: a "
            "pre-operational data centre's load comes from its ramp, plant and utilisation. Use "
            "gridq.datacentre.derive.derive_data_centre_half_hourly with the site's declared "
            "inputs."
        )

    def synthesise(
        self,
        inputs: DcLoadInputs,
        *,
        seed: int,
        draws: int,
        workload_flex: WorkloadFlex = NO_ENVELOPE_DECLARED,
    ) -> Synthesis:
        return synthesise(inputs, seed=seed, draws=draws, workload_flex=workload_flex)


DATA_CENTRE: Final = DataCentreArchetype(
    profile_id=ARCHETYPE_ID,
    label="Data centre (pre-operational component model)",
    source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
    source_ref=(
        f"gridq.datacentre {METHOD_VERSION}: facility load = IT + UPS loss + distribution + "
        "auxiliaries + cooling (WP-THERM-04 forms) from site-declared inputs, with a seeded Latin "
        "hypercube for bands. Pending the spec 16.2 back-test against metered halls."
    ),
    weekday=_ZEROS,
    saturday=_ZEROS,
    sunday=_ZEROS,
    month_factors=(1.0,) * 12,
    notes=(
        "Not a shape. day_shape() refuses; the series is synthesised from declared inputs by "
        "DataCentreArchetype.synthesise and stamped MODELLED."
    ),
)
