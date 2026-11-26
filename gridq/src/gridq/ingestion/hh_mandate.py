"""The half-hourly data basis (validation Gate 2) — **record and continue**, never block.

Policy, stated once and enforced here (**D44, Olly 2026-08-12**):

**No missing input blocks a validation run.** Where the half-hourly basis is absent, the gate
records what is missing, what would stand in for it, the confidence ceiling that follows and the
exact confirmation ask — and the run continues. It never raises for missing data.

- **Metered** data is used whenever it exists, and is never displaced, blended away or preferred
  against in favour of a generated shape.
- Where metered data does not exist, **generating a shape is standard practice, not a
  degradation** — a sourced reference profile for the business type, scaled to the site's own
  reconciled totals (`gridq.ingestion.hh_synthesis`).
- Where no shape has been generated yet, the run still completes. It completes *honestly*: with no
  series there is no dispatch to price, so the engine reports insufficient data, the band is capped
  at LOW, and the recorded `DerivedAssumption` names the archetype and totals that would produce a
  shape. A refusal and an honest empty result are not the same thing — the second is auditable.
- The basis is **recorded on the run** and travels onto every artifact, so a reader never has to
  ask which sites were metered.

**An absent basis is never read as METERED** (N9, route 2). A generated series is a file on disk
exactly like a metered one, so inferring "metered" from a path silently promoted every modelled site
to the top confidence band. An attached series whose provenance was never declared is admitted —
the run must price the data it has — but its basis is honestly *unrecorded* (`basis is None`), its
ceiling is MEDIUM rather than HIGH, and a `DerivedAssumption` states the open question.

The one thing still refused here is a **corrupt** basis: a `half_hourly_basis` string that is not a
recognised basis is a caller error, not a missing input, and is raised rather than guessed. Gate 1
(MILP or nothing) is untouched by D44 — it is an engine-quality gate, not a data gate.

This module holds the gate and the basis vocabulary. It deliberately does NOT generate profiles —
generation lives in `gridq.ingestion.hh_synthesis`, so the gate stays a pure, testable policy
function with no dependency on how a shape is produced. See `docs/validation_requirements.md` §1
Gate 2 and `docs/decisions.md` D44.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gridq.schemas import ConfidenceBand, DerivedAssumption, HalfHourlyBasis, SiteModel

__all__ = [
    "HalfHourlyAdmission",
    "HalfHourlyBasis",
    "HalfHourlyMandateError",
    "admit_half_hourly",
]

# `HalfHourlyBasis` now lives in `gridq.schemas.enums` and is re-exported here unchanged. It moved
# because the basis has to be PERSISTED on the QuoteRecord and read by the lifecycle gate (N21):
# while it lived in this module the ceiling below could only be interpolated into a warning string,
# so nothing downstream could branch on it and a modelled run passed every gate a metered run did.

#: Identity stamped on the records this gate writes. A deterministic, code-derived record names the
#: module that made it; an advisor-derived value names the model (`gridq.schemas.derived`).
_DERIVED_BY = "gridq.ingestion.hh_mandate"


class HalfHourlyMandateError(ValueError):
    """The recorded half-hourly basis is not a recognised basis.

    **Not raised for missing data** (D44): an absent series or an undeclared basis is recorded and
    the run continues. This is reserved for a `load_data.half_hourly_basis` value that is corrupt —
    a typo or an unknown vocabulary — which is a caller error and must not be guessed at.
    """


@dataclass(frozen=True)
class HalfHourlyAdmission:
    """The gate's verdict: which basis underwriting proceeds on, and what it rests on."""

    #: The recorded basis, or ``None`` when no basis is established — either because no series is
    #: attached at all, or because an attached series never declared its provenance. ``None`` is
    #: never a synonym for METERED (N9).
    basis: HalfHourlyBasis | None
    file_ref: str
    #: What the shape was scaled to, when modelled (e.g. "12 monthly invoice totals, 2025").
    scaled_to: str | None = None
    #: The reference profile and its source, when modelled. Required for a MODELLED basis.
    reference_profile: str | None = None
    notes: tuple[str, ...] = ()
    #: Records for every input this admission had to stand in for (D44). Transient: they belong to
    #: the RUN, not to the site, so the caller carries them onto the quote and the confidence
    #: assessment and never writes them back onto `SiteModel` (that would move the site's identity).
    derived: tuple[DerivedAssumption, ...] = field(default_factory=tuple)

    @property
    def has_series(self) -> bool:
        """Whether a half-hourly series is actually attached for the engine to price."""
        return bool(self.file_ref)

    @property
    def is_metered(self) -> bool:
        return self.basis is HalfHourlyBasis.METERED

    @property
    def confidence_ceiling(self) -> ConfidenceBand:
        """The best confidence band this basis can support (`docs/validation_requirements.md` §2).

        - **METERED** → HIGH. The site's own evidence about itself.
        - **MODELLED / MIXED** → MEDIUM. A legitimate input basis, but evidence about a *typical*
          site of this type rather than about *this* site, so it cannot carry the top band however
          good the rest of the case is.
        - **basis unrecorded, series attached** → MEDIUM. The data is priced as attached, but its
          provenance is an open question, and an open question is not top evidence (N9).
        - **no series at all** → LOW. Structural: with no load series there is nothing to dispatch
          against, so nothing downstream can be strongly evidenced.

        Returns the typed `ConfidenceBand` rather than a bare string (N21) so the ceiling can be
        persisted on the `QuoteRecord` and **compared** by the lifecycle gate. As a `StrEnum` it
        still formats and compares equal to the old ``"high"``/``"medium"`` literals, so callers
        that only interpolate it are unaffected.
        """
        if self.basis is HalfHourlyBasis.METERED:
            return ConfidenceBand.HIGH
        if self.basis is not None:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.MEDIUM if self.has_series else ConfidenceBand.LOW


def _undeclared_basis_record(release: str | None) -> DerivedAssumption:
    """A series is attached but never said where it came from (N9, route 2)."""
    return DerivedAssumption(
        kind="derived_input",
        key="load_data.half_hourly_basis",
        missing_input="the provenance of the attached half-hourly series (metered or modelled)",
        # Deliberately no value: the gate cannot tell a meter export from a generated CSV, and
        # guessing "metered" is the exact defect N9 filed. The series is priced as attached.
        value=None,
        unit=None,
        basis="A half-hourly series is attached and priced as supplied, but `half_hourly_basis` "
        "was never declared, so it is not established whether this is the site's own meter export "
        "or a modelled shape. An undeclared basis is NOT read as metered: the top confidence band "
        "is withheld until the provenance is stated.",
        sources=[],
        derivation_method="not_derivable",
        derived_by=_DERIVED_BY,
        derived_on_release=release,
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask="Confirm whether the attached half-hourly file is the site's own metered "
        "export (and from which MPAN and period) or a modelled shape, and record it in "
        "`load_data.half_hourly_basis`.",
    )


def _no_series_record(site: SiteModel, release: str | None) -> DerivedAssumption:
    """No half-hourly series at all — the run continues, with the shape named as the ask."""
    # Lazy import: `hh_synthesis` imports this module's vocabulary, so a module-level import here
    # would be circular. The gate reads only the default profile's identity, never its curves.
    from gridq.ingestion.hh_synthesis import DEFAULT_SYNTHESIS_YEAR, default_reference_profile

    profile = default_reference_profile()
    annual = site.load_data.annual_kwh or site.annual_consumption_kwh
    if annual:
        basis = (
            f"No half-hourly series is attached, so there is no shape to dispatch against and the "
            f"engine prices nothing. This site does have {annual:,.0f} kWh/yr of stated "
            f"consumption, which is enough to MODEL a defensible shape: reference profile "
            f"'{profile.profile_id}' ({profile.source.value}) scaled to that total over "
            f"{DEFAULT_SYNTHESIS_YEAR}, via `gridq.ingestion.hh_synthesis.derive_half_hourly`. "
            "Generating that shape is standard practice here, not a degradation. Until it is "
            "generated and attached, the run completes with no priced dispatch."
        )
        method: str = "sourced_default"
        value: str | None = profile.profile_id
    else:
        basis = (
            "No half-hourly series is attached and there is no annual or monthly consumption total "
            "to scale a modelled shape to, so no shape can be derived without inventing the site's "
            "energy. The run completes with no priced dispatch rather than with a guessed one."
        )
        method = "not_derivable"
        value = None
    return DerivedAssumption(
        kind="derived_input",
        key="load_data.half_hourly_file_ref",
        missing_input="the site's half-hourly load series",
        value=value,
        unit=None,
        basis=basis,
        # The archetype library is documented engineering shape, not a published curve
        # (`ProfileSource.ARCHETYPE_PENDING_SOURCE`). An empty citation list is the honest
        # record and must stay visible — see `gridq.schemas.derived`.
        sources=[],
        derivation_method=method,  # type: ignore[arg-type]  # narrowed literal by construction
        derived_by=_DERIVED_BY,
        derived_on_release=release,
        # Structural: with no series there is nothing to dispatch against (`ceiling_for_key`).
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="Supply the site's half-hourly meter export (preferred) or its monthly "
        "consumption totals and operating pattern, so a shape can be modelled and scaled to the "
        "site's own measured energy.",
    )


def admit_half_hourly(site: SiteModel, *, release: str | None = None) -> HalfHourlyAdmission:
    """Gate 2. Return the basis underwriting proceeds on, recording whatever was missing.

    **This never raises for missing data** (D44). A site whose `load_data.half_hourly_basis` is
    recorded is admitted on that basis. A site with a series but no declared basis is admitted with
    ``basis=None`` — priced as supplied, ceiling MEDIUM, provenance recorded as an open ask. A site
    with no series at all is admitted with ``basis=None`` and no ``file_ref``: the run continues and
    reports honestly (nothing to dispatch ⇒ no priced spec), carrying a record that names the
    reference profile and totals a shape would be modelled from.

    Only a **corrupt** basis string raises `HalfHourlyMandateError` — that is bad data, not absent
    data, and must never be guessed at.

    ``release`` is the assumption release in force, stamped onto the records for provenance; a
    caller without a library in scope leaves it ``None`` (honest absence, never a placeholder).
    """
    ref = site.load_data.half_hourly_file_ref
    if ref:
        # The basis is read from the record, never inferred from the presence of a file. A
        # generated series is a file on disk exactly like a metered one, so inferring "metered"
        # from a path would silently promote every modelled site to the top confidence band.
        declared = (site.load_data.half_hourly_basis or "").strip().lower()
        if declared in {b.value for b in HalfHourlyBasis}:
            return HalfHourlyAdmission(
                basis=HalfHourlyBasis(declared),
                file_ref=ref,
                scaled_to=site.load_data.half_hourly_scaled_to,
                reference_profile=site.load_data.half_hourly_reference_profile,
            )
        if declared:
            raise HalfHourlyMandateError(
                f"load_data.half_hourly_basis={declared!r} is not a recognised basis "
                f"(expected one of {[b.value for b in HalfHourlyBasis]}). This is corrupt data, "
                "not missing data: a basis that cannot be read is never guessed at."
            )
        # Undeclared: admitted and priced, but never promoted to METERED (N9, route 2).
        record = _undeclared_basis_record(release)
        return HalfHourlyAdmission(
            basis=None,
            file_ref=ref,
            scaled_to=site.load_data.half_hourly_scaled_to,
            reference_profile=site.load_data.half_hourly_reference_profile,
            notes=(record.statement(),),
            derived=(record,),
        )

    record = _no_series_record(site, release)
    return HalfHourlyAdmission(
        basis=None,
        file_ref="",
        notes=(record.statement(),),
        derived=(record,),
    )
