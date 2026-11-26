"""The customer bill, before vs after the battery (C017 / W7-5) — the firming headline's data.

Every figure is computed from the site's OWN metered half-hourly load, the site's OWN tariff
(time-of-use energy + DUoS unit rates + £/kWh levy lines) and the SOLVED dispatch schedule —
the exact series the MILP optimised, so the proposal's "bill before/after" is the run record,
never prose. Round-trip losses are bought at the price faced (the battery increases total kWh
imported while shifting expensive→cheap), so a negative saving is representable and honest.

**Fixed lines (2026-07-07):** the standing charge and the capacity/availability charge (billed
on the site's agreed supply capacity, not the realised peak) are battery-invariant, so they are
carried identically on BOTH sides — they never move the saving, but their inclusion makes the
totals a customer's real annual bill rather than a partial figure (the Robert Thomas Farms
red-team run showed a "bill" that was blind to £17k/yr of real fixed lines). The TNUoS residual
remains excluded and ``basis`` says so. A tariff with no time-mappable energy rate produces no
bill at all (the producer returns ``None``) — the proposal keeps its honest pending seam rather
than a partial figure.

**BEFORE-ONLY bills (2026-08-15).** ``annual_bill_before_gbp`` is DISPATCH-INVARIANT by
construction: the site's own load against its own tariff stack with no battery in it. It needs a
half-hourly series and a tariff, not a viable battery. Gate 3b (bill-vs-invoice reconciliation)
deliberately reads that BEFORE side precisely because it tests the TARIFF STACK rather than the
dispatch — and a wrong tariff stack is a leading candidate for *why* a site returns "no viable
configuration" in the first place. So a run that recommends no system still produces a bill, and
this model carries it in a **before-only** mode:

* the six AFTER-side figures are ``None`` **together, or populated together** — never mixed. A
  mixed state is a validation error, so a half-built bill cannot exist;
* ``annual_saving_gbp`` is ``None`` in that mode, never ``0.0``. With no dispatch there is no
  after-battery bill, and a zero saving would render as a *computed* result ("this battery saves
  you nothing") rather than as the absence of a battery. The distinction is the whole point;
* ``peak_import_after_kw`` must also be ``None`` (there is no schedule to measure it on);
* ``basis`` states that no system was recommended and that no after-battery bill exists.

Consumers branch on `CustomerBill.after_side_priced` (or on any AFTER field being ``None``) — a
machine-readable discriminator, not a prose label. The renderers already treat a ``None`` after
figure as the labelled-pending seam.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from gridq.schemas.provenance import GridQModel
from gridq.schemas.references import omit_unset

_TOL_GBP = 0.05  # rounding tolerance across independently-rounded components

#: The AFTER-side figures that stand or fall together. All present ⇒ a solved dispatch was
#: priced; all ``None`` ⇒ the before-only mode (no dispatch exists). Any mix is a defect and is
#: rejected — that all-or-nothing rule is what makes the absence structurally unmistakable rather
#: than a zero a reader can mistake for a computed saving. ``peak_import_after_kw`` is
#: deliberately NOT in this set: it is informational physics that has always been optional on a
#: solved record (see below), so it is constrained separately.
_AFTER_FIELDS: tuple[str, ...] = (
    "annual_bill_after_gbp",
    "annual_saving_gbp",
    "energy_after_gbp",
    "duos_after_gbp",
    "levies_after_gbp",
    "import_after_kwh",
)


class CustomerBill(GridQModel):
    """Annual customer bill decomposition, before vs after the solved dispatch.

    In the **before-only** mode (no dispatch — see the module docstring) every AFTER-side field is
    ``None`` and only the BEFORE side and the battery-invariant fixed lines carry figures.
    """

    annual_bill_before_gbp: float = Field(ge=0)
    #: ``None`` ⇔ before-only (no dispatch). Required, never defaulted: a caller must state which
    #: mode it is producing rather than fall into one.
    annual_bill_after_gbp: float | None = Field(ge=0)
    annual_saving_gbp: float | None = Field(
        description="before − after; negative is representable (losses cost real money). "
        "``None`` in the before-only mode — with no dispatch there is no saving, and 0.0 would "
        "read as a computed one."
    )
    energy_before_gbp: float = Field(ge=0)
    energy_after_gbp: float | None = Field(ge=0)
    duos_before_gbp: float = Field(ge=0)
    duos_after_gbp: float | None = Field(ge=0)
    levies_before_gbp: float = Field(ge=0)
    levies_after_gbp: float | None = Field(ge=0)
    #: Battery-invariant fixed lines (identical before and after; additive 2026-07-07, default 0
    #: so historical records load unchanged): the supplier standing charge and the capacity /
    #: availability charge (£/kVA/day × the agreed supply capacity — billed on the ASC, not the
    #: realised peak, so the battery cannot move it; kVA≈kW at unity PF, disclosed in basis).
    #: Battery-invariant means they are carried in the before-only mode too.
    standing_gbp: float = Field(default=0.0, ge=0)
    capacity_gbp: float = Field(default=0.0, ge=0)
    import_before_kwh: float = Field(ge=0)
    import_after_kwh: float | None = Field(ge=0)
    levy_rate_gbp_per_kwh: float = Field(ge=0)
    # W9 (C029): INFORMATIONAL peak-import physics off the solved schedule. The dispatch is
    # bill-optimised with an epsilon peak-minimising tie-break (D38), NOT peak-priced — the
    # after-peak can still legitimately EXCEED the before-peak (charging off-red-band), so no
    # ASC/capacity £ is ever priced from these; they exist so a human can see the measured
    # effect while the objective-level capacity term awaits C029.
    peak_import_before_kw: float | None = Field(default=None, ge=0)
    peak_import_after_kw: float | None = Field(default=None, ge=0)
    #: WP-UW-07 (D106): the excess reactive power charge, before and after, on the exact
    #: LC14 rule at the published rate. ``None`` when the site has no metered reactive channel or
    #: the tariff states no reactive rate: an unmodelled line, never a measured £0. Absent from
    #: dumps while ``None``, so every existing bill (and every hash over one) is unchanged.
    reactive_before_gbp: float | None = Field(default=None, ge=0)
    reactive_after_gbp: float | None = Field(default=None, ge=0)
    basis: str = Field(
        min_length=1,
        description="What the bill includes AND excludes — the honesty label the proposal "
        "must carry alongside the headline.",
    )

    @model_serializer(mode="wrap")
    def _dump_without_unset_reactive(self, handler: SerializerFunctionWrapHandler) -> Any:
        return omit_unset(handler(self), ("reactive_before_gbp", "reactive_after_gbp"))

    @property
    def after_side_priced(self) -> bool:
        """True iff a solved dispatch produced the AFTER side (the machine-readable discriminator).

        ``False`` is the before-only mode: the BEFORE side is real and dispatch-invariant, and
        there is no after-battery bill to compare it against.
        """
        return self.annual_bill_after_gbp is not None

    @model_validator(mode="after")
    def _consistent(self) -> CustomerBill:
        absent = [name for name in _AFTER_FIELDS if getattr(self, name) is None]
        if absent and len(absent) != len(_AFTER_FIELDS):
            # A half-populated after side is the exact failure this model exists to prevent: a
            # reader cannot tell which figures were computed and which defaulted.
            raise ValueError(
                "the AFTER side must be wholly present or wholly absent; missing "
                f"{sorted(absent)} while the rest are populated."
            )
        fixed = self.standing_gbp + self.capacity_gbp
        before_parts = (
            self.energy_before_gbp
            + self.duos_before_gbp
            + self.levies_before_gbp
            + fixed
            + (self.reactive_before_gbp or 0.0)
        )
        if abs(self.annual_bill_before_gbp - before_parts) > _TOL_GBP:
            raise ValueError(
                f"bill_before components must sum to the total "
                f"(total {self.annual_bill_before_gbp}, components {before_parts})."
            )
        if (self.reactive_before_gbp is None) != (self.reactive_after_gbp is None) and not absent:
            raise ValueError(
                "reactive_before_gbp and reactive_after_gbp are priced together or not at all; one "
                "side alone would move the saving by a line the other side does not carry."
            )
        if absent:
            # Before-only: nothing measured off a schedule may carry a figure, or the record would
            # imply a dispatch that does not exist.
            if self.reactive_after_gbp is not None:
                raise ValueError(
                    "reactive_after_gbp is measured off the solved schedule, so it must be None on "
                    "a before-only bill."
                )
            if self.peak_import_after_kw is not None:
                raise ValueError(
                    "peak_import_after_kw is measured off the solved schedule, so it must be None "
                    "on a before-only bill (no dispatch exists to measure it on)."
                )
            return self
        # Every AFTER field is populated here (the all-or-nothing check above returned otherwise);
        # the locals exist to narrow the Optionals without an `or 0.0`, which is the idiom that
        # turns a genuine absence into a silent zero everywhere else in this codebase.
        after_total = self.annual_bill_after_gbp
        energy_after = self.energy_after_gbp
        duos_after = self.duos_after_gbp
        levies_after = self.levies_after_gbp
        saving = self.annual_saving_gbp
        if (
            after_total is None
            or energy_after is None
            or duos_after is None
            or levies_after is None
            or saving is None
        ):  # pragma: no cover — unreachable: the all-or-nothing check above returned already
            raise ValueError("the AFTER side must be wholly present or wholly absent.")
        after_parts = (
            energy_after + duos_after + levies_after + fixed + (self.reactive_after_gbp or 0.0)
        )
        if abs(after_total - after_parts) > _TOL_GBP:
            raise ValueError(
                f"bill_after components must sum to the total "
                f"(total {after_total}, components {after_parts})."
            )
        expected_saving = self.annual_bill_before_gbp - after_total
        if abs(saving - expected_saving) > _TOL_GBP:
            raise ValueError(
                f"annual_saving_gbp must equal before − after ({saving} vs {expected_saving})."
            )
        return self
