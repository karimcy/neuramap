"""Tariff stack — the site's import/export pricing and levies.

IC-12 3.0.0 (WP-UW-07) adds four things, each decided under the owner's delegation
(``docs/decisions.md`` D103 to D105):

- `TariffBasis`, **required**: whether the rates include VAT, and whether levies and network
  charges are itemised or sit inside the unit rates. A stack without a basis is refused, because a
  rate whose basis is unknown cannot be compared with any other rate.
- `TariffProvenance`, **required**: where each component (commodity, network, levies) came from.
  The report's "priced on its own tariff stack" sentence is rendered from it and from nothing else
  (`gridq.optimisation.tariff_calendar.tariff_provenance_phrase`).
- `BandWindows` on ``import_rate_windows``, optional: the stack's own time windows for its own
  labels, so every band is priced as itself and never as a mean of the others.
- `CdcmTariff` / `EdcmTariff` on ``network_tariff``, optional: a published DNO tariff row in the
  statement's own units (pence, `Decimal`), read by `gridq.tariffs.hv_tariff_model`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from gridq.schemas.enums import Confidence
from gridq.schemas.provenance import GridQModel
from gridq.schemas.references import EvidenceLink, canonical_evidence_links, omit_unset

_MINUTES_PER_DAY = 24 * 60


def parse_clock_minute(raw: object) -> int | None:
    """Parse a published ``"HH:MM"`` clock time to minutes after midnight (``"24:00"`` → 1440).

    ``None`` for anything malformed. **The one parser** for the ``"HH:MM"`` convention every GB
    charging statement quotes its windows in: `gridq.optimisation.tariff_calendar` delegates its
    own release-YAML parsing to this function, so a site-schema window and a release-carried DUoS
    band window can never disagree about what ``"16:30"`` means. Lenient (returns ``None``) rather
    than raising, because the release path parses untrusted YAML and falls back loudly; the schema
    path below turns the ``None`` into a validation error.
    """
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    minutes = int(parts[0]) * 60 + int(parts[1])
    if not 0 <= minutes <= _MINUTES_PER_DAY or int(parts[1]) >= 60:
        return None
    return minutes


def _check_months(value: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not value or any(not 1 <= m <= 12 for m in value):
        raise ValueError(
            f"months must be a non-empty list of 1-12 (or null for all year); got {list(value)}"
        )
    return tuple(sorted(set(value)))


def _check_dates(value: tuple[str, ...]) -> tuple[str, ...]:
    for raw in value:
        parts = raw.split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts) or len(parts[0]) != 4:
            raise ValueError(f"excluded_dates must be ISO 'YYYY-MM-DD'; got {raw!r}")
    return tuple(sorted(set(value)))


class ClockWindow(GridQModel):
    """A half-open local-clock window ``[start, end)``, quoted as the statements quote it.

    Same shape and same conventions as the per-licence-area DUoS band windows a release carries in
    ``duos.band_windows_by_dno`` (``{start: "16:00", end: "19:00"}``): period-start local clock,
    Europe/London, half-open so 19:00 is out of a 16:00–19:00 window. Deliberately NOT a pair of
    integers — real published windows land on half-hours (16:30, 19:30) that hour pairs cannot say.
    """

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _clock(cls, value: str) -> str:
        if parse_clock_minute(value) is None:
            raise ValueError(f"clock time must be 'HH:MM' between 00:00 and 24:00; got {value!r}")
        return value

    @property
    def start_minute(self) -> int:
        """Minutes after local midnight the window opens (inclusive)."""
        minute = parse_clock_minute(self.start)
        assert minute is not None  # noqa: S101 - guaranteed by the validator
        return minute

    @property
    def end_minute(self) -> int:
        """Minutes after local midnight the window closes (exclusive)."""
        minute = parse_clock_minute(self.end)
        assert minute is not None  # noqa: S101 - guaranteed by the validator
        return minute


class WindowedLevy(GridQModel):
    """A £/kWh levy line charged **only inside a stated time window**, not on every half-hour.

    **Why the type exists.** ``TariffStack.levies`` is a flat ``{label: £/kWh}`` map and every
    consumer sums it and applies the total to all 17,520 half-hours. That is right for CCL, RO,
    CfD, BSUoS, AAHEDC and Nuclear RAB — all of which bite on boundary import across the whole
    year — and **wrong for the Capacity Market supplier obligation**, which GB suppliers charge on
    peak-window consumption only ("Obligation Costs … are charged on consumption during the winter
    peak period; from 16:00 and 19:00 on working days between November & February", npower Business
    Solutions). Put a 30 p/kWh Capacity Market line in the flat map and the objective charges it on
    every charged kWh at 03:00 in July, which would wreck the dispatch. So a windowed levy is its
    own field with its own calendar, and the flat map keeps meaning exactly what it meant.

    **The window is expressed the way the DUoS band windows are** (`ClockWindow`, period-start
    Europe/London clock, half-open, weekday and weekend as separate day types, an optional month
    restriction), and `gridq.optimisation.tariff_calendar` bands it with the same machinery it
    bands DUoS with — one set of conventions, not two.

    ``excluded_dates`` carries the **non-working days** inside the window's months. It is required
    for honesty rather than precision: the Capacity Market window is "working days", GB bank
    holidays fall Monday–Friday, and ``dayofweek < 5`` alone would charge Christmas Day as a
    working day and **overstate** the avoided cost by roughly one working day in 28. The dates are
    sourced (gov.uk's published bank-holiday list), never derived from a rule — leaving it empty is
    permitted and is disclosed as an overstatement, never silently assumed harmless.
    """

    rate_gbp_per_kwh: float = Field(ge=0)
    #: Calendar months the levy applies in (1–12). ``None`` = all year.
    months: tuple[int, ...] | None = None
    #: Windows charged on weekdays (``dayofweek < 5``), and on weekends. Both default to empty:
    #: a levy with no window anywhere charges nothing and says so, never "all hours".
    weekday_windows: tuple[ClockWindow, ...] = ()
    weekend_windows: tuple[ClockWindow, ...] = ()
    #: ISO ``YYYY-MM-DD`` dates excluded from the window (non-working days). Sourced, not derived.
    excluded_dates: tuple[str, ...] = ()
    basis: str | None = None
    source: str | None = None
    confidence: Confidence | None = None

    @field_validator("months")
    @classmethod
    def _months(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        return _check_months(value)

    @field_validator("excluded_dates")
    @classmethod
    def _dates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _check_dates(value)


class BandWindows(GridQModel):
    """When one import-rate band applies, on the same conventions as `WindowedLevy`.

    Period-start Europe/London clock, half-open `ClockWindow` s, weekday and weekend as separate
    day types, an optional month restriction and sourced non-working ``excluded_dates`` (which fall
    to the weekend row). A `BandWindows` with **no window at all** is the residual band, "all other
    times"; a stack may carry at most one (`TariffStack._import_rate_windows_consistent`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    weekday_windows: tuple[ClockWindow, ...] = ()
    weekend_windows: tuple[ClockWindow, ...] = ()
    months: tuple[int, ...] | None = None
    excluded_dates: tuple[str, ...] = ()

    @field_validator("months")
    @classmethod
    def _months(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        return _check_months(value)

    @field_validator("excluded_dates")
    @classmethod
    def _dates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _check_dates(value)

    @property
    def is_residual(self) -> bool:
        """True for the "all other times" band: no weekday and no weekend window."""
        return not self.weekday_windows and not self.weekend_windows


def _minute_mask(windows: tuple[ClockWindow, ...]) -> list[bool]:
    mask = [False] * _MINUTES_PER_DAY
    for w in windows:
        start, end = w.start_minute, w.end_minute
        if start < end:
            minutes: list[int] = list(range(start, end))
        elif start > end:  # wraps midnight, e.g. 23:00-07:00
            minutes = [*range(start, _MINUTES_PER_DAY), *range(0, end)]
        else:
            minutes = []
        for m in minutes:
            mask[m] = True
    return mask


VatTreatment = Literal["excluded", "included"]
VatAttestation = Literal["stated_by_record", "derived"]
LevyBasis = Literal["itemised", "in_unit_rates"]
NetworkBasis = Literal["itemised", "in_unit_rates", "not_charged"]
ComponentProvenance = Literal[
    "own_invoice", "published_for_site", "by_analogy", "assumed", "not_charged"
]

#: The provenance values under which a component may be called the site's own.
OWN_COMPONENT_PROVENANCE: frozenset[str] = frozenset(
    {"own_invoice", "published_for_site", "not_charged"}
)


class TariffBasis(GridQModel):
    """What the stack's rates include (D103). Required on every `TariffStack`.

    ``vat`` says whether the £ figures include VAT; ``vat_rate_fraction`` is the rate that was
    included (required when ``vat="included"``, refused when ``"excluded"``) and is read from the
    record, never from a release key or from memory. ``vat_attestation`` says how the VAT basis is
    known: ``stated_by_record`` (the invoice or the record's author states it) or ``derived``
    (inferred under H-01, which then needs a `DerivedAssumption` on ``tariff_stack.basis.vat`` on
    the same site, enforced by `SiteModel`). ``levies`` and ``network_charges`` say whether those
    lines are itemised in this stack or already sit inside the unit rates, or (network only) are
    not charged at all, as on a private-wire supply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    vat: VatTreatment
    vat_rate_fraction: float | None = Field(default=None, gt=0, lt=1)
    vat_attestation: VatAttestation
    levies: LevyBasis
    network_charges: NetworkBasis

    @model_validator(mode="after")
    def _vat_rate_matches_treatment(self) -> TariffBasis:
        if self.vat == "included" and self.vat_rate_fraction is None:
            raise ValueError(
                "basis.vat is 'included' but no vat_rate_fraction is given: the rate that was "
                "included must come from the record, so the engine can state the net figure."
            )
        if self.vat == "excluded" and self.vat_rate_fraction is not None:
            raise ValueError(
                "basis.vat is 'excluded' but a vat_rate_fraction is given: an excluded rate has no "
                "VAT in it to state."
            )
        return self

    @property
    def net_of_vat_divisor(self) -> float:
        """What a stated £ figure is divided by to give it net of VAT (1.0 when excluded)."""
        return 1.0 + (self.vat_rate_fraction or 0.0)


class TariffProvenance(GridQModel):
    """Where each component of the stack came from (D104). Required on every stack.

    ``own_invoice``: the site's own invoiced rate. ``published_for_site``: the DNO's published rate
    for the site's own licence area, voltage and band. ``by_analogy``: another site's or another
    tariff's rate. ``assumed``: constructed or assumed. ``not_charged``: the component is not
    charged to this site (private wire). Only the first two and the last are the site's own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    commodity: ComponentProvenance
    network: ComponentProvenance
    levies: ComponentProvenance

    @property
    def is_own(self) -> bool:
        """True only when every component is the site's own (or is not charged to it)."""
        return all(
            value in OWN_COMPONENT_PROVENANCE
            for value in (self.commodity, self.network, self.levies)
        )


class _PublishedNetworkTariff(GridQModel):
    """One published DNO tariff row, in the statement's own units (pence, `Decimal`).

    Pence and `Decimal` are deliberate: the published example in
    ``tests/fixtures/dno_charging`` reproduces to the penny only if the rate is the statement's own
    number, converted to pounds at the boundary (`gridq.tariffs.hv_tariff_model`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dno: str = Field(min_length=1)
    voltage: Literal["LV", "HV", "EHV"]
    llfc: str | None = None
    charging_year: str = Field(min_length=1)
    tcr_band: str | None = None
    #: The document the row was read from (title, URL, retrieval date, SHA-256).
    source: str = Field(min_length=1)
    fixed_p_per_day: Decimal = Field(ge=0)
    capacity_p_per_kva_day: Decimal = Field(ge=0)
    exceeded_capacity_p_per_kva_day: Decimal = Field(ge=0)


class CdcmTariff(_PublishedNetworkTariff):
    """A CDCM (LV/HV) tariff: three time bands, fixed, capacity, exceeded capacity, reactive."""

    methodology: Literal["CDCM"] = "CDCM"
    red_p_per_kwh: Decimal = Field(ge=0)
    amber_p_per_kwh: Decimal = Field(ge=0)
    green_p_per_kwh: Decimal = Field(ge=0)
    #: Excess reactive power charge (p/kVArh). ``None`` = not stated by the record.
    reactive_p_per_kvarh: Decimal | None = Field(default=None, ge=0)


class EdcmTariff(_PublishedNetworkTariff):
    """An EDCM (designated EHV) import tariff: super-red unit charge in its own window.

    EDCM has no reactive charge, so there is no field for one.
    """

    methodology: Literal["EDCM"] = "EDCM"
    super_red_p_per_kwh: Decimal = Field(ge=0)
    super_red_window: BandWindows

    @field_validator("super_red_window")
    @classmethod
    def _window_present(cls, value: BandWindows) -> BandWindows:
        if value.is_residual:
            raise ValueError("an EDCM super red window must state its weekday or weekend window")
        return value


class TariffStack(GridQModel):
    """Import/export rates, DUoS bands and levy lines for a site.

    ``import_rates`` / ``export_rates`` / ``levies`` map a label (e.g. ``"red"``, ``"RO"``)
    to a £/kWh value. Rates are non-negative (£/kWh).
    """

    supplier: str | None = None
    tariff_name: str | None = None
    standing_charge_per_day_gbp: float | None = Field(default=None, ge=0)
    import_rates: dict[str, float] = Field(default_factory=dict)
    export_rates: dict[str, float] = Field(default_factory=dict)
    duos_red_rate: float | None = Field(default=None, ge=0)
    duos_amber_rate: float | None = Field(default=None, ge=0)
    duos_green_rate: float | None = Field(default=None, ge=0)
    levies: dict[str, float] = Field(default_factory=dict)
    #: £/kWh levy lines charged only inside a stated time window (see `WindowedLevy`). Keyed by
    #: label, exactly like ``levies`` — the two maps never share a key, because a levy is either
    #: charged on every half-hour or only inside its window, never both.
    windowed_levies: dict[str, WindowedLevy] = Field(default_factory=dict)
    #: Availability / capacity charge (£ per kVA of agreed supply capacity per day) — the UK C&I
    #: "capacity charge" line billed on the site's ASC (additive, 2026-07-07: the Robert Thomas
    #: Farms invoice bills 8.76 p/kVA/day on 250 kVA; the bill model was blind to it before).
    #: The kVA basis is the connection's ASC (``connection.asc_kw``, kVA≈kW at unity PF —
    #: disclosed where used).
    capacity_charge_gbp_per_kva_day: float | None = Field(default=None, ge=0)
    source: str | None = None
    confidence: Confidence | None = None
    #: IC-12 3.0.0, required (D103): VAT, levy and network basis of every rate above.
    basis: TariffBasis
    #: IC-12 3.0.0, required (D104): where each component came from.
    provenance: TariffProvenance
    #: IC-12 3.0.0, optional: IC-4 evidence for the provenance claims. Absent from dumps while
    #: unset; ordered and de-duplicated like `SiteModel.evidence_refs`.
    evidence: tuple[EvidenceLink, ...] | None = None
    #: IC-12 3.0.0, optional (D105): the stack's own windows for its own import labels.
    import_rate_windows: dict[str, BandWindows] | None = None
    #: IC-12 3.0.0, optional (D105): the published DNO tariff row for this site.
    network_tariff: CdcmTariff | EdcmTariff | None = Field(
        default=None, discriminator="methodology"
    )

    @model_serializer(mode="wrap")
    def _dump_without_unset_growth(self, handler: SerializerFunctionWrapHandler) -> Any:
        return omit_unset(handler(self), ("evidence", "import_rate_windows", "network_tariff"))

    @model_validator(mode="after")
    def _canonicalise_evidence(self) -> TariffStack:
        self.evidence = canonical_evidence_links(self.evidence)
        return self

    @model_validator(mode="after")
    def _basis_matches_lines(self) -> TariffStack:
        """Refuse a stack whose stated basis contradicts the lines it carries.

        ``levies="in_unit_rates"`` with an itemised levy line would charge that levy twice. Network
        charges ``in_unit_rates`` or ``not_charged`` with a DUoS rate or capacity charge would
        charge (or invent) the network twice. Provenance ``network="not_charged"`` must agree with
        the basis, in both directions.
        """
        basis = self.basis
        if basis.levies == "in_unit_rates" and (self.levies or self.windowed_levies):
            raise ValueError(
                "basis.levies is 'in_unit_rates' but the stack itemises levy lines; a levy is "
                "either inside the unit rates or itemised, never both."
            )
        if basis.network_charges in ("in_unit_rates", "not_charged"):
            set_lines = [
                name
                for name in (
                    "duos_red_rate",
                    "duos_amber_rate",
                    "duos_green_rate",
                    "capacity_charge_gbp_per_kva_day",
                )
                if getattr(self, name) is not None
            ]
            if set_lines or self.network_tariff is not None:
                raise ValueError(
                    f"basis.network_charges is {basis.network_charges!r} but the stack sets "
                    f"{set_lines or ['network_tariff']}; itemised network lines need "
                    "network_charges='itemised'."
                )
        if (basis.network_charges == "not_charged") != (self.provenance.network == "not_charged"):
            raise ValueError(
                "basis.network_charges and provenance.network disagree about whether network "
                "charges apply to this site; 'not_charged' must be stated in both or neither."
            )
        return self

    @model_validator(mode="after")
    def _import_rate_windows_consistent(self) -> TariffStack:
        """Own windows must cover exactly the stack's labels, without overlap, one residual."""
        windows = self.import_rate_windows
        if windows is None:
            return self
        if set(windows) != set(self.import_rates):
            raise ValueError(
                f"import_rate_windows labels {sorted(windows)} must equal the import_rates labels "
                f"{sorted(self.import_rates)}: every band is priced as itself in its own window."
            )
        residual = [label for label, w in windows.items() if w.is_residual]
        if len(residual) > 1:
            raise ValueError(
                f"import_rate_windows has more than one band with no window ({residual}); at most "
                "one band may be the residual 'all other times'."
            )
        timed = [(label, w) for label, w in windows.items() if not w.is_residual]
        for i, (label_a, a) in enumerate(timed):
            for label_b, b in timed[i + 1 :]:
                months_a = set(a.months or range(1, 13))
                if not months_a & set(b.months or range(1, 13)):
                    continue
                for day in ("weekday_windows", "weekend_windows"):
                    mask_a = _minute_mask(getattr(a, day))
                    mask_b = _minute_mask(getattr(b, day))
                    if any(x and y for x, y in zip(mask_a, mask_b, strict=True)):
                        raise ValueError(
                            f"import_rate_windows {label_a!r} and {label_b!r} overlap in "
                            f"{day}; a half-hour belongs to one band only."
                        )
        return self

    @model_validator(mode="after")
    def _network_tariff_agrees_with_flat_lines(self) -> TariffStack:
        """A published row and the legacy flat £ lines may coexist only if they agree.

        An EDCM row has no red/amber/green unit rates at all (its one unit charge is super red), so
        a flat ``duos_*`` line beside it would price a band the published tariff does not have.
        """
        nt = self.network_tariff
        if nt is None:
            return self
        if isinstance(nt, EdcmTariff):
            flat_bands = [
                name
                for name in ("duos_red_rate", "duos_amber_rate", "duos_green_rate")
                if getattr(self, name) is not None
            ]
            if flat_bands:
                raise ValueError(
                    f"network_tariff is an EDCM row but the stack also sets {flat_bands}; EDCM has "
                    "no red/amber/green unit charge, only super red, so those lines would price a "
                    "band the published tariff does not have."
                )
            pairs: tuple[tuple[str, Decimal], ...] = (
                ("capacity_charge_gbp_per_kva_day", nt.capacity_p_per_kva_day),
            )
        else:
            pairs = (
                ("duos_red_rate", nt.red_p_per_kwh),
                ("duos_amber_rate", nt.amber_p_per_kwh),
                ("duos_green_rate", nt.green_p_per_kwh),
                ("capacity_charge_gbp_per_kva_day", nt.capacity_p_per_kva_day),
            )
        for name, pence in pairs:
            flat = getattr(self, name)
            if flat is not None and abs(flat * 100.0 - float(pence)) > 1e-9:
                raise ValueError(
                    f"{name} = £{flat} disagrees with network_tariff ({pence} p); a rate is "
                    "stated once, or twice with one value."
                )
        return self

    @field_validator("import_rates", "export_rates", "levies")
    @classmethod
    def _no_negative_rates(cls, value: dict[str, float]) -> dict[str, float]:
        negatives = {k: v for k, v in value.items() if v < 0}
        if negatives:
            raise ValueError(f"rates/levies must be non-negative (£/kWh); got {negatives}")
        return value

    @field_validator("windowed_levies")
    @classmethod
    def _windows_present(cls, value: dict[str, WindowedLevy]) -> dict[str, WindowedLevy]:
        for label, levy in value.items():
            if not levy.weekday_windows and not levy.weekend_windows:
                raise ValueError(
                    f"windowed levy {label!r} declares no weekday or weekend window, so it would "
                    "charge nothing. Record it in `levies` if it is a flat line, or give it its "
                    "window."
                )
        return value

    @model_validator(mode="after")
    def _no_levy_label_collision(self) -> TariffStack:
        """A label may not sit in both levy maps — that charges one £ twice, half of it invisibly.

        The flat map is summed and applied to every half-hour; the windowed map is applied inside
        its window. A label present in both is therefore charged flat AND again in-window, and a
        reader auditing ``windowed_levies`` sees only the second copy.
        """
        clash = sorted(set(self.levies) & set(self.windowed_levies))
        if clash:
            raise ValueError(
                f"levy label(s) {clash} appear in BOTH `levies` (flat, every half-hour) and "
                "`windowed_levies` (in-window only); a levy is one or the other, never both — "
                "carrying it twice charges the same £ twice."
            )
        return self
