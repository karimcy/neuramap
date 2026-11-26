"""`StudyBasis` — the named FEASIBILITY-STUDY basis that prices export into the floor (**D46**).

**Authority: Olly, 2026-08-13 (D46).** Two bases now exist and every consumer must state which it
is on.

* The **underwriting basis** is unchanged and remains the default in every sense that matters:
  wholesale/P415 is gated upside and **never** in the priced floor (`CLAUDE.md` §6, D20/B002/D43).
* The **study basis** recorded here may price wholesale export into the **priced floor**, using a
  **derived, sourced** export tariff, so an in-house feasibility study can ask "what does this site
  look like if export is contractible?" and get a priced answer instead of a refusal.

D46 also records the risk this creates, plainly: **a study-basis number is one an unlabelled reader
could mistake for an underwritten one, and the mitigation is entirely in the labelling.** So the
labelling here is load-bearing rather than decorative, and it is built into the schema rather than
left to prose:

1. **There is no default and no flag.** ``basis_id`` has no default, the acknowledgement sentence
   must be quoted verbatim, and both the export limit and the export tariff must be supplied *with*
   their `DerivedAssumption` records. A truthy argument cannot enable this; a person has to write
   down what they are doing. (Same shape as
   `gridq.optimisation.engine.ExportFeasibilityOverride`, which already solved this problem.)
2. **The tariff may not be invented.** ``tariff_record`` must be a `derived_input` on the export
   tariff key, carrying the same value as ``export_tariff_gbp_per_kwh``, and it must cite at least
   one source. D45's no-null amendment applies to an export tariff exactly as to any other input:
   a £/kWh nobody can point at a document for is refused here, at construction, rather than
   travelling into five reports.
3. **Consent is not moved, and cannot be.** ``consent_record`` is a `pending_permission` — a class
   the `DerivedAssumption` validators make structurally unable to carry a value or to read as
   consent granted. Where revenue *sits* is a modelling choice the owner may make; asserting that a
   customer has *agreed* an export arrangement is a factual claim about a third party and stays
   forbidden (D45). A study run therefore prices export in the floor **and** states "requires
   customer consent" against it.
4. **The band is capped at LOW.** An export limit is a structural feasibility input under the D45
   ceiling policy, so ``confidence_ceiling`` is validated against `ceiling_for_key` and a producer
   cannot raise its own ceiling. ``not_bankable`` is untouched — it is the "confirmation required"
   statement, not a restriction to remove.

**Run identity.** The basis is carried on the run and enters ``input_hash``
(`gridq.quotes.assembly._canonical_run_input`, pop-if-None per C033/D42), because two bases must
never share a ``run_id``. `SOLVER_SUFFIX` additionally lands the basis in
``SolverMetadata.model_version``, so the record says which basis produced it in a machine-readable
field and not only in prose.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal, Protocol, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

import gridq
from gridq.extensions import UnknownExtension, study_bases
from gridq.schemas.derived import DerivedAssumption, ceiling_for_key
from gridq.schemas.enums import ConfidenceBand
from gridq.schemas.provenance import GridQModel

__all__ = [
    "STUDY_BASIS_CLASSES",
    "STUDY_BASIS_LABELS",
    "AllStreamsEnabledStudyBasis",
    "NamedBasis",
    "StudyBasis",
    "basis_class_problems",
    "study_basis_label",
]


class StudyBasis(GridQModel):
    """The named study basis that prices wholesale export into the priced floor. See module doc."""

    # Frozen for the same reason `DerivedAssumption` is: this record enters ``input_hash``, so the
    # basis a run was priced on must not be reassignable after the validators have passed it.
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The verbatim sentence ``acknowledgement`` must carry. Prose deliberately: a boolean cannot be
    #: misread, but it also cannot be *quoted*, and this string ends up in the run record.
    ACKNOWLEDGEMENT: ClassVar[str] = (
        "study basis, not an underwriting output; export is priced in the priced floor on this "
        "basis and still requires customer consent"
    )
    #: The one basis this schema knows how to price. A second study basis gets its own literal
    #: rather than a boolean on this one.
    BASIS_ID: ClassVar[str] = "export_in_priced_floor_study_v1"
    #: The banner stated on the run, on every affected figure, and searchable by any consumer.
    LABEL: ClassVar[str] = (
        "STUDY BASIS (D46) — EXPORT PRICED IN THE PRICED FLOOR — NOT AN UNDERWRITING OUTPUT"
    )
    #: Appended to ``SolverMetadata.model_version`` so the BASIS is machine-readable on the record.
    SOLVER_SUFFIX: ClassVar[str] = "-study-export-floor"
    #: The register keys the three records stand on. The tariff key is unrecognised by the D45
    #: ceiling policy, which caps an unclassified input at LOW — the correct answer here.
    TARIFF_KEY: ClassVar[str] = "export_arrangement.export_tariff_gbp_per_kwh"
    EXPORT_LIMIT_KEY: ClassVar[str] = "export_arrangement.declared_export_limit_kw"
    CONSENT_KEY: ClassVar[str] = "stream_consents.WHOLESALE_P415"

    #: Names the basis. No default: a study basis is always chosen, never inherited.
    basis_id: Literal["export_in_priced_floor_study_v1"]
    #: Who directed this study basis (a person, in the record — never "the system").
    authorised_by: str = Field(min_length=1)
    #: Why the study is being run on this basis, in the words of whoever authorised it.
    justification: str = Field(min_length=1)
    #: Must equal `ACKNOWLEDGEMENT` exactly.
    acknowledgement: str
    #: The export capacity the study permits (kW). Comes from HERE, never from a guessed site field.
    export_limit_kw: float = Field(gt=0)
    #: The derived, sourced export tariff the floor entry is priced at (£/kWh).
    export_tariff_gbp_per_kwh: float = Field(gt=0)
    #: The `derived_input` record for the tariff: source, vintage, direction of effect, ask.
    tariff_record: DerivedAssumption
    #: The `derived_input` record for the export limit.
    export_limit_record: DerivedAssumption
    #: The `pending_permission` record for export consent. Never readable as consent granted.
    consent_record: DerivedAssumption
    #: The band cap this basis imposes. LOW, and never raisable above the D45 policy ceiling.
    confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

    @model_validator(mode="after")
    def _enforce_named_basis(self) -> StudyBasis:
        if self.acknowledgement != self.ACKNOWLEDGEMENT:
            raise ValueError(
                "StudyBasis.acknowledgement must be the verbatim sentence "
                f"{self.ACKNOWLEDGEMENT!r}. It exists so a second pricing basis cannot be enabled "
                "by a truthy flag: whoever enables it states, in the run record, that this is a "
                "study output and not an underwriting one, and that consent is still outstanding."
            )
        if not self.authorised_by.strip() or not self.justification.strip():
            raise ValueError(
                "StudyBasis requires a non-blank authorised_by and justification: an "
                "unattributed, unexplained second pricing basis is exactly the silent default this "
                "input is shaped to make impossible."
            )
        for field, value in (
            ("export_limit_kw", self.export_limit_kw),
            ("export_tariff_gbp_per_kwh", self.export_tariff_gbp_per_kwh),
        ):
            if not math.isfinite(value):
                raise ValueError(f"StudyBasis.{field} must be finite, got {value!r}.")
        self._check_derived_input(
            self.tariff_record, self.TARIFF_KEY, self.export_tariff_gbp_per_kwh, "tariff_record"
        )
        self._check_derived_input(
            self.export_limit_record,
            self.EXPORT_LIMIT_KEY,
            self.export_limit_kw,
            "export_limit_record",
        )
        # An uncited tariff is the fabrication D45's amendment forbids outright. The limit may
        # legitimately be derived from the site's own evidenced connection (no external citation
        # exists for it), so the source requirement binds the PRICE only — the number that
        # multiplies into every £ on the floor.
        if not self.tariff_record.sources:
            raise ValueError(
                "StudyBasis.tariff_record cites no source. D46 requires the export tariff to be "
                "DERIVED AND SOURCED, and D45's no-null amendment applies to it exactly as to any "
                "other input: GB wholesale prices are public and dated, so an uncited £/kWh is an "
                "invented one. Record the series, its vintage and its access date, or refuse the "
                "study basis and say what is missing."
            )
        if self.consent_record.kind != "pending_permission":
            raise ValueError(
                "StudyBasis.consent_record must be a pending_permission record: D46 moves where "
                "export revenue SITS and moves nothing about what the customer has agreed. A "
                "derived_input here would be a fabricated consent wearing the label of a derived "
                "fact (`gridq.schemas.derived`)."
            )
        if self.consent_record.key != self.CONSENT_KEY:
            raise ValueError(
                f"StudyBasis.consent_record.key must be {self.CONSENT_KEY!r}, got "
                f"{self.consent_record.key!r} — the outstanding permission a study basis prices "
                "against is export consent, and the record has to name it."
            )
        policy = min(
            (ceiling_for_key(self.TARIFF_KEY), ceiling_for_key(self.EXPORT_LIMIT_KEY)),
            key=lambda band: band.rank,
        )
        if self.confidence_ceiling.rank > policy.rank:
            raise ValueError(
                f"StudyBasis declares a {self.confidence_ceiling.value} confidence ceiling, but "
                f"the D45 ceiling policy caps this basis at {policy.value}: an export limit is a "
                "STRUCTURAL FEASIBILITY input and a derived export tariff is unclassified, which "
                "also caps at LOW (`gridq.schemas.derived.ceiling_for_key`). A producer may not "
                "raise its own ceiling."
            )
        return self

    @staticmethod
    def _check_derived_input(
        record: DerivedAssumption, key: str, value: float, field_name: str
    ) -> None:
        """A supplied record must be a `derived_input` on ``key`` carrying exactly ``value``."""
        if record.kind != "derived_input":
            raise ValueError(
                f"StudyBasis.{field_name} must be a derived_input record, got {record.kind!r}."
            )
        if record.key != key:
            raise ValueError(
                f"StudyBasis.{field_name}.key must be {key!r}, got {record.key!r} — the record has "
                "to name the field it stands in for, or the assumption register describes a "
                "different number from the one the model consumed."
            )
        recorded = record.value
        if not isinstance(recorded, (int, float)) or not math.isclose(
            float(recorded), value, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError(
                f"StudyBasis.{field_name}.value ({recorded!r}) must equal the value the model "
                f"consumes ({value!r}). A register entry that disagrees with the priced number is "
                "worse than no register entry: it reads as corroboration."
            )

    @property
    def solver_basis_label(self) -> str:
        """The short basis label handed to the dispatch solver's export limit."""
        return (
            f"{self.LABEL}; export limit supplied by the named study basis "
            f"{self.basis_id!r} authorised by {self.authorised_by.strip()} — NOT a declared MEC "
            "and NOT customer consent"
        )

    def note(self) -> str:
        """The disclosure stated on every artifact carrying a number produced on this basis."""
        return (
            f"{self.LABEL}. This run was priced on the D46 FEASIBILITY-STUDY BASIS, not the "
            "underwriting basis. Wholesale export IS priced into the priced floor here and "
            "therefore into P50, P90, the DSCR and both IRRs — which the underwriting basis "
            "forbids (CLAUDE.md §6, D20/B002/D43) and which D46 leaves unchanged for underwriting. "
            f"Basis chosen by {self.authorised_by.strip()}: {self.justification.strip()} "
            f"Acknowledged as: “{self.ACKNOWLEDGEMENT}”. "
            f"Export is priced at a DERIVED, SOURCED tariff of "
            f"£{self.export_tariff_gbp_per_kwh:,.6f}/kWh up to an export limit of "
            f"{self.export_limit_kw:,.3f} kW, both recorded as assumptions requiring confirmation. "
            "CONSENT IS UNCHANGED AND OUTSTANDING: no consent, MEC, export MPAN or G99/G100 record "
            "has been written to the site, `export_dispatch_enabled` is untouched, and export at "
            "this site REQUIRES CUSTOMER CONSENT — it is modelled, not granted. Per the D45 "
            "ceiling policy an export limit is a STRUCTURAL FEASIBILITY input, so this run's "
            f"confidence band is capped at {self.confidence_ceiling.value.upper()} and the result "
            "stays not_bankable. A FIGURE FROM THIS RUN MAY NEVER BE PRESENTED AS AN UNDERWRITING "
            "OUTPUT; re-run on the underwriting basis for anything lender-facing. "
            + " ".join(self.statements())
        )

    def floor_entry_note(self) -> str:
        """The shorter sentence stated beside the export figure itself, on every artifact."""
        return (
            f"{self.LABEL}: this export revenue is in the priced floor ONLY because the run "
            "declared the D46 study basis. On the underwriting basis the same export value is "
            "GATED UPSIDE and contributes nothing to P50, P90, the DSCR or either IRR. It is "
            "priced at a derived, sourced tariff of "
            f"£{self.export_tariff_gbp_per_kwh:,.6f}/kWh and REQUIRES CUSTOMER CONSENT plus a "
            "contracted route to market before any of it is realisable — a commercial ask, not a "
            "modelling change."
        )

    def statements(self) -> list[str]:
        """The three register statements, in the D45 report language for each record's class."""
        return [
            self.tariff_record.statement(),
            self.export_limit_record.statement(),
            self.consent_record.statement(),
        ]

    def assumption_keys(self) -> list[str]:
        """The register keys this basis consumes — carried onto the priced entry (traceability)."""
        return [self.TARIFF_KEY, self.EXPORT_LIMIT_KEY, self.CONSENT_KEY]


class AllStreamsEnabledStudyBasis(StudyBasis):
    """The named study basis on which EVERY revenue stream is priced into the floor (**D55**).

    **Authority: Olly, 2026-08-18 (D55), extending D46.** The owner's instruction is verbatim: an
    aggregator route and ancillary services must each be *"a revenue stream to pick from"* and *"a
    revenue stream the MILPs can access"*, and *"you assume for every revenue stream that an award
    has been given to trade in that stream"*. This class is that instruction implemented **inside**
    the D46 mechanism rather than beside it, because D46 already solved the hard half of the
    problem: how to price normally-gated value without corrupting an underwriting run.

    **Why it is a subclass and not a flag.** ``export_in_priced_floor_study_v1`` prices exactly one
    normally-gated stream into the floor. This basis is a strict **superset**: it keeps the export
    tariff, the export limit and the export-consent record unchanged — so
    `gridq.optimisation.engine._StudyBasisDispatchSolver` prices export on this basis exactly as it
    does on D46's, with no change to the engine — and adds the remaining streams, their sourced
    prices, their assumed awards and the published exclusivity rules that stop the optimiser adding
    up what a battery cannot physically deliver. Subclassing means every existing D46 consumer
    (`quotes.assembly._canonical_run_input`, `MilpEngine`, the report writers) accepts it untouched,
    and every D46 guarantee still holds:

    1. **No default and no flag.** ``basis_id`` is a distinct `Literal`, so this basis cannot be
       reached by mutating a boolean, and the acknowledgement sentence is its own and must be quoted
       verbatim.
    2. **Nothing may be invented.** Every enabled stream must carry a sourced ``£/kW/yr`` **and** a
       `DerivedAssumption`; the aggregator cut must carry its own `derived_input` record.
    3. **Awards and consents are not moved, and cannot be.** *This is the load-bearing line.* The
       owner's "assume an award has been given" is honoured as a **modelling basis** — the stream is
       priced, its value is shown — and **never** as a factual claim that a third party has agreed.
       Every ``stream_award_records`` entry is a ``pending_permission``, the one `DerivedAssumption`
       class structurally unable to carry a value or read as consent granted (D45). This basis
       therefore prices the Balancing Mechanism into the floor **and** states "requires an award and
       customer consent" against it, in the same sentence.
    4. **The band is capped at LOW** and ``not_bankable`` is untouched.

    **Run identity.** Inherited from D46 and unchanged in mechanism: the basis is dumped whole into
    ``_canonical_run_input`` (`gridq/quotes/assembly.py:221`) and therefore into ``input_hash``, so
    an all-streams study run can never share a ``run_id`` with an export-only study run or with an
    underwriting run of the same site. `tests/test_all_streams_study_basis.py` pins this and fails
    if the ``study_basis`` key is dropped from the canonical dict.

    **KNOWN LIMITATION, stated rather than glossed.** ``SOLVER_SUFFIX`` cannot distinguish this
    basis on ``SolverMetadata.model_version``: `engine.py:338` computes
    ``_StudyBasisDispatchSolver.MODEL_VERSION`` from ``StudyBasis.SOLVER_SUFFIX`` at
    **class-creation time**, so a subclass's suffix never reaches the solver metadata.
    Machine-readable basis identity here therefore rests on ``input_hash`` and on the dumped
    ``basis_id``, not on ``model_version``. Closing it means editing `engine.py` to read the suffix
    off the instance.

    **WHAT IS PRICED AT RUNTIME TODAY, AND WHAT IS NOT.** Stated because it is the difference
    between a delivered stream and a declared one:

    * **Priced at runtime now:** wholesale export, through the inherited export tariff and export
      limit (`engine.py:306` ``_StudyBasisDispatchSolver``, reaching every sizing candidate).
    * **Declared, sourced, priced in the library, awaiting one dispatch hook:** the Balancing
      Mechanism, DC/DM/DR response, the Capacity Market, DFS and the aggregator cut. Their prices
      and half-hourly shapes are in ``assumptions/gb_v1_12.yaml``; this class carries the option
      set, the assumed awards and the exclusivity rules; the remaining work is to read
      ``option_set()`` from the dispatch objective in `gridq/optimisation/milp_solver.py` and
      `gridq/value_stack/gb_streams.py`. Until that hook exists **no figure from those five streams
      reaches a reported £**, and this docstring is the record of that fact rather than a claim to
      the contrary.
    """

    #: This basis's own acknowledgement. Longer than D46's on purpose: it has more to acknowledge.
    ACKNOWLEDGEMENT: ClassVar[str] = (
        "study basis, not an underwriting output; every revenue stream is priced in the priced "
        "floor on this basis, every award and consent is ASSUMED rather than held, and all of them "
        "still require an award, a route to market and customer consent"
    )
    BASIS_ID: ClassVar[str] = "all_streams_enabled_study_v1"
    LABEL: ClassVar[str] = (
        "STUDY BASIS (D55) — EVERY REVENUE STREAM PRICED IN THE PRICED FLOOR, EVERY AWARD ASSUMED "
        "— NOT AN UNDERWRITING OUTPUT"
    )
    #: Inherited value, deliberately: see the KNOWN LIMITATION above. Overriding it here would be
    #: worse than leaving it — it would read as machine-readable when it is not.
    SOLVER_SUFFIX: ClassVar[str] = StudyBasis.SOLVER_SUFFIX
    #: The register key the aggregator cut stands on.
    AGGREGATOR_CUT_KEY: ClassVar[str] = "balancing_mechanism.study_basis_aggregator_route"
    #: The prefix every per-stream assumed-award record must key on.
    AWARD_KEY_PREFIX: ClassVar[str] = "stream_consents."
    #: Streams whose value reaches the site only through a route-to-market partner, so the
    #: aggregator cut applies to them. Bill-avoidance streams are deliberately absent: that value is
    #: the site's own avoided cost and no third party stands between the battery and it.
    ROUTED_STREAMS: ClassVar[frozenset[str]] = frozenset(
        {
            "WHOLESALE_P415",
            "BALANCING_MECHANISM",
            "ANCILLARY_DC_DM_DR",
            "CAPACITY_MARKET",
            "DFS",
            "LOCAL_FLEX_PICLO",
        }
    )

    basis_id: Literal["all_streams_enabled_study_v1"]  # type: ignore[assignment]
    #: Every stream this basis prices into the floor, by `MarketStream` value. The OPTION SET the
    #: optimiser chooses from — never a set of streams to be summed.
    enabled_streams: list[str] = Field(min_length=1)
    #: The sourced £/kW/yr for each enabled stream, GROSS of the aggregator cut. Keys must match
    #: ``enabled_streams`` exactly: no stream may be enabled without a price, and no price may be
    #: carried for a stream that is not enabled.
    stream_price_gbp_per_kw_yr: dict[str, float]
    #: One ``pending_permission`` per enabled stream: the award/consent this basis ASSUMES. Never a
    #: ``derived_input`` — see point 3 above.
    stream_award_records: list[DerivedAssumption] = Field(min_length=1)
    #: The share of every ROUTED stream's revenue the aggregator takes (0–1).
    aggregator_cut_pct: float = Field(ge=0.0, lt=1.0)
    #: The ``derived_input`` record for the cut. Its value must equal ``aggregator_cut_pct``.
    aggregator_cut_record: DerivedAssumption
    #: Families of mutually exclusive streams, from the published rules. Each inner list is a set of
    #: streams at most ONE of which may be committed in a commitment period.
    exclusive_families: list[list[str]] = Field(default_factory=list)
    #: The single annual discharge budget every stream competes for, kWh per kW of rated power.
    warranty_throughput_kwh_per_kw_yr: float = Field(gt=0)

    @model_validator(mode="after")
    def _enforce_all_streams_basis(self) -> AllStreamsEnabledStudyBasis:
        enabled = list(self.enabled_streams)
        if len(set(enabled)) != len(enabled):
            raise ValueError(
                f"AllStreamsEnabledStudyBasis.enabled_streams contains duplicates: {enabled!r}. "
                "A stream enabled twice is a stream whose price could be summed twice."
            )
        priced = set(self.stream_price_gbp_per_kw_yr)
        if priced != set(enabled):
            raise ValueError(
                "AllStreamsEnabledStudyBasis.stream_price_gbp_per_kw_yr must name exactly the "
                f"enabled streams. Enabled with no price: {sorted(set(enabled) - priced)!r}; "
                f"priced but not enabled: {sorted(priced - set(enabled))!r}. A stream with no "
                "price prices at ZERO on a floor basis, which is the null-on-the-pricing-path "
                "defect D45 forbids outright; a price with no stream is an orphaned number."
            )
        for stream, value in self.stream_price_gbp_per_kw_yr.items():
            if not math.isfinite(value):
                raise ValueError(
                    f"AllStreamsEnabledStudyBasis price for {stream!r} must be finite, got "
                    f"{value!r}."
                )
        awarded: dict[str, DerivedAssumption] = {}
        for record in self.stream_award_records:
            if record.kind != "pending_permission":
                raise ValueError(
                    f"AllStreamsEnabledStudyBasis.stream_award_records entry on {record.key!r} is "
                    f"a {record.kind!r}. Every assumed award MUST be a pending_permission: the "
                    "owner's instruction moves where revenue SITS and cannot make a factual claim "
                    "that a third party has awarded or consented to anything (D45). A "
                    "derived_input here would be a fabricated award wearing the label of a derived "
                    "fact (`gridq.schemas.derived`)."
                )
            if not record.key.startswith(self.AWARD_KEY_PREFIX):
                raise ValueError(
                    f"AllStreamsEnabledStudyBasis award key {record.key!r} must start with "
                    f"{self.AWARD_KEY_PREFIX!r} so the record names the permission it stands for."
                )
            awarded[record.key[len(self.AWARD_KEY_PREFIX) :]] = record
        missing = sorted(set(enabled) - set(awarded))
        if missing:
            raise ValueError(
                "AllStreamsEnabledStudyBasis prices these streams into the floor with NO "
                f"assumed-award record: {missing!r}. Every priced stream states on the record that "
                "its award and consent are outstanding — that statement is the whole safeguard, "
                "and a priced stream without it reads as a stream the customer has agreed."
            )
        extra = sorted(set(awarded) - set(enabled))
        if extra:
            raise ValueError(
                "AllStreamsEnabledStudyBasis carries award records for streams it does not price: "
                f"{extra!r}. Remove them, or enable and price the stream."
            )
        for family in self.exclusive_families:
            if len(family) < 2:
                raise ValueError(
                    f"AllStreamsEnabledStudyBasis.exclusive_families entry {family!r} names fewer "
                    "than two streams, so it constrains nothing."
                )
            unknown = sorted(set(family) - set(enabled))
            if unknown:
                raise ValueError(
                    "AllStreamsEnabledStudyBasis.exclusive_families names streams that are not "
                    f"enabled: {unknown!r}. An exclusivity rule over a stream nobody prices is a "
                    "rule that cannot bind."
                )
        self._check_derived_input(
            self.aggregator_cut_record,
            self.AGGREGATOR_CUT_KEY,
            self.aggregator_cut_pct,
            "aggregator_cut_record",
        )
        if not self.aggregator_cut_record.sources:
            raise ValueError(
                "AllStreamsEnabledStudyBasis.aggregator_cut_record cites no source. The cut "
                "multiplies into EVERY routed stream, so an uncited one silently scales the whole "
                "grid-market stack. No GB aggregator publishes a revenue share (recorded NOT "
                "FOUND, three searches), so the record must cite the library placeholder it "
                "stands on and the searches that failed — not nothing."
            )
        if not math.isfinite(self.warranty_throughput_kwh_per_kw_yr):
            raise ValueError(
                "AllStreamsEnabledStudyBasis.warranty_throughput_kwh_per_kw_yr must be finite."
            )
        return self

    # -- the option set ---------------------------------------------------------------------------

    def routed_streams(self) -> list[str]:
        """The enabled streams the aggregator cut applies to, in declaration order."""
        return [s for s in self.enabled_streams if s in self.ROUTED_STREAMS]

    def net_price_gbp_per_kw_yr(self, stream: str) -> float:
        """The £/kW/yr for ``stream`` after the aggregator cut, where the cut applies to it.

        The cut is applied HERE, once, so no consumer has to remember which streams it touches.
        """
        if stream not in self.stream_price_gbp_per_kw_yr:
            raise KeyError(
                f"{stream!r} is not priced on this basis; enabled streams are "
                f"{self.enabled_streams!r}."
            )
        gross = self.stream_price_gbp_per_kw_yr[stream]
        if stream in self.ROUTED_STREAMS:
            return gross * (1.0 - self.aggregator_cut_pct)
        return gross

    def option_set(self) -> list[dict[str, object]]:
        """The per-stream option the optimiser chooses from: price, routing, award, exclusivity.

        This is the hand-off surface for the dispatch objective. It is a **list of options**, never
        a total: summing ``net_gbp_per_kw_yr`` across it would breach both the exclusivity rules and
        the single warranty throughput budget, and would describe a battery that delivers the same
        kW to several markets at once.
        """
        return [
            {
                "stream": stream,
                "gross_gbp_per_kw_yr": self.stream_price_gbp_per_kw_yr[stream],
                "net_gbp_per_kw_yr": self.net_price_gbp_per_kw_yr(stream),
                "aggregator_routed": stream in self.ROUTED_STREAMS,
                "award_assumed_not_held": True,
                "exclusive_with": sorted(
                    {other for fam in self.exclusive_families if stream in fam for other in fam}
                    - {stream}
                ),
            }
            for stream in self.enabled_streams
        ]

    def exclusivity_violations(self, selection: list[str]) -> list[list[str]]:
        """Which exclusive families ``selection`` breaches — empty when the selection is legal.

        The check the optimiser's binary constraints implement, expressed once here so a consumer
        can verify a selection without re-deriving the rules from the published documents.
        """
        chosen = set(selection)
        return [
            sorted(family)
            for family in self.exclusive_families
            if len(chosen.intersection(family)) > 1
        ]

    def throughput_headroom_kwh_per_kw_yr(self, committed_kwh_per_kw_yr: float) -> float:
        """The annual discharge budget left after ``committed_kwh_per_kw_yr`` is spent.

        Negative when a selection is over-committed — which is the normal answer for any naive sum
        of this basis's streams, and the reason it is worth computing.
        """
        return self.warranty_throughput_kwh_per_kw_yr - committed_kwh_per_kw_yr

    # -- disclosure -------------------------------------------------------------------------------

    def assumption_keys(self) -> list[str]:
        """The register keys this basis consumes: D46's three, the aggregator cut, every award."""
        return [
            *super().assumption_keys(),
            self.AGGREGATOR_CUT_KEY,
            *(record.key for record in self.stream_award_records),
        ]

    def statements(self) -> list[str]:
        """D46's three statements, plus the aggregator cut and every assumed-award statement."""
        return [
            *super().statements(),
            self.aggregator_cut_record.statement(),
            *(record.statement() for record in self.stream_award_records),
        ]

    def note(self) -> str:
        """The disclosure stated on every artifact carrying a number produced on this basis."""
        options = ", ".join(
            f"{stream} £{self.net_price_gbp_per_kw_yr(stream):,.2f}/kW/yr net"
            for stream in self.enabled_streams
        )
        families = (
            "; ".join(" XOR ".join(sorted(f)) for f in self.exclusive_families)
            if self.exclusive_families
            else "none declared"
        )
        return (
            f"{self.LABEL}. This run was priced on the D55 ALL-STREAMS-ENABLED FEASIBILITY-STUDY "
            "BASIS, not the underwriting basis. EVERY revenue stream is priced into the priced "
            "floor here and therefore into P50, P90, the DSCR and both IRRs — which the "
            "underwriting basis forbids for wholesale/P415, the Balancing Mechanism and DC/DM/DR "
            "(CLAUDE.md §6, D20/B002/D43) and which D55 leaves unchanged for underwriting. "
            f"Basis chosen by {self.authorised_by.strip()}: {self.justification.strip()} "
            f"Acknowledged as: “{self.ACKNOWLEDGEMENT}”. "
            f"THE OPTION SET, net of a {self.aggregator_cut_pct:.1%} aggregator revenue share on "
            f"every routed stream: {options}. THESE FIGURES ARE OPTIONS, NOT A TOTAL — the "
            f"optimiser chooses per half-hour subject to the published exclusivity rules "
            f"({families}), to one kW being sellable once per half-hour, and to a single annual "
            f"discharge budget of {self.warranty_throughput_kwh_per_kw_yr:,.1f} kWh/kW/yr. Summing "
            "them describes a battery that does not exist. "
            "EVERY AWARD AND CONSENT ON THIS BASIS IS ASSUMED, NOT HELD: no award, tender result, "
            "registered BM Unit, Response Unit, CMU, DFS registration, export MPAN, G99/G100 "
            "approval or per-stream consent has been written to the site, and each stream carries "
            "a 'requires an award and customer consent' record. Per the D45 ceiling policy this "
            f"run's confidence band is capped at {self.confidence_ceiling.value.upper()} and the "
            "result stays not_bankable. A FIGURE FROM THIS RUN MAY NEVER BE PRESENTED AS AN "
            "UNDERWRITING OUTPUT; re-run on the underwriting basis for anything lender-facing. "
            + " ".join(self.statements())
        )

    def floor_entry_note(self) -> str:
        """The shorter sentence stated beside every priced figure on this basis, on any artifact."""
        return (
            f"{self.LABEL}: this revenue is in the priced floor ONLY because the run declared the "
            "D55 all-streams study basis. On the underwriting basis wholesale/P415, the Balancing "
            "Mechanism and DC/DM/DR are GATED UPSIDE and the Capacity Market, DFS and local "
            "flexibility are CONDITIONAL, so all six contribute nothing to P50, P90, the DSCR or "
            f"either IRR. Every figure here is net of a {self.aggregator_cut_pct:.1%} aggregator "
            "revenue share that NO GB AGGREGATOR PUBLISHES (recorded NOT FOUND after three "
            "searches), and every stream REQUIRES AN AWARD, A ROUTE TO MARKET AND CUSTOMER CONSENT "
            "before any of it is realisable — commercial asks, not modelling changes."
        )


#: Every named study basis, keyed by the ``basis_id`` persisted on `QuoteRecord.study_basis_id`.
#:
#: The point of this map is that a consumer holding a finished record can recover the basis's OWN
#: banner from a `Literal` id instead of parsing a disclosure sentence. Before it existed the
#: renderers detected the basis by matching a literal prefix against
#: ``optimisation_result.warnings``, which is an identity resting on wording: the measured
#: 2026-08-18 defect was a D55 run whose banner disappeared from all four artifacts because the
#: detector still
#: read ``"STUDY BASIS (D46)"``, after which the customer proposal described a study run as an
#: underwriting run. An id cannot be reworded.
#:
#: Registering a new basis is deliberately a two-line edit HERE as well as a new subclass, so a
#: basis that no consumer can label cannot reach a report: `test_study_basis_marker_and_band` fails
#: if a `StudyBasis` subclass is missing from this map.
STUDY_BASIS_CLASSES: dict[str, type[StudyBasis]] = {
    StudyBasis.BASIS_ID: StudyBasis,
    AllStreamsEnabledStudyBasis.BASIS_ID: AllStreamsEnabledStudyBasis,
}

#: ``basis_id`` → the basis's own banner (`StudyBasis.LABEL`). Read this rather than a constant of
#: your own: the label states WHICH basis priced the figures, and a generic "STUDY BASIS" chip
#: understates a D55 run's priced floor by five streams.
STUDY_BASIS_LABELS: dict[str, str] = {
    basis_id: cls.LABEL for basis_id, cls in STUDY_BASIS_CLASSES.items()
}


def study_basis_label(basis_id: str | None) -> str | None:
    """The banner for a persisted ``basis_id``, or ``None`` when the record names no study basis.

    ``None`` in, ``None`` out — an UNDERWRITING run carries no banner. An UNRECOGNISED id also
    returns ``None`` rather than raising: a consumer rendering an old or forward-dated record must
    not crash, and the caller's own "is this a study run" branch (``study_basis_id is not None``) is
    what governs whether a banner is required. A caller that needs the stricter reading looks the id
    up in `STUDY_BASIS_LABELS` directly and handles the `KeyError`.
    """
    if basis_id is None:
        return None
    label = STUDY_BASIS_LABELS.get(basis_id)
    if label is not None:
        return label
    # IC-12 2.0.0: a basis another workstream registered from its own package labels itself here,
    # so every renderer that already calls this function shows its banner with no edit.
    try:
        return study_bases.get(basis_id).LABEL
    except UnknownExtension:
        return None


class NamedBasis(Protocol):
    """What the chassis reads off a basis: the contract a registered basis class must meet.

    `StudyBasis` and `AllStreamsEnabledStudyBasis` meet it, and so must a basis another
    workstream registers through `gridq.extensions.study_bases` (``underwriting_basis_v1``,
    WP-UW-01). It is exactly what `quotes.pipeline` and `quotes.assembly` consume: the id that is
    persisted on the record, the ceiling that caps the band, the note that leads the warnings,
    and the dump that enters ``input_hash``. `basis_class_problems` checks the class-level half.
    """

    BASIS_ID: ClassVar[str]
    LABEL: ClassVar[str]

    @property
    def basis_id(self) -> str: ...

    @property
    def confidence_ceiling(self) -> ConfidenceBand: ...

    def note(self) -> str: ...

    def model_dump(self, *, mode: str = ...) -> dict[str, Any]: ...


def basis_class_problems(cls: object, *, expected_id: str) -> list[str]:
    """Why ``cls`` may not be registered as the basis ``expected_id``; empty when it may.

    The rules are the ones the two built-in bases already keep, stated once so a basis from
    another package cannot be weaker than they are:

    - a frozen pydantic model that refuses unknown fields (the instance enters ``input_hash``);
    - ``BASIS_ID`` equal to the registered id, and a non-empty ``LABEL`` (its banner);
    - ``basis_id`` a **required** field typed as the one-value ``Literal`` of that id, so a basis
      is always chosen by name and can never arrive as a default or a flag;
    - a ``confidence_ceiling`` field and a ``note()`` method, which the pipeline reads.
    """
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        return ["it is not a pydantic model class"]
    problems: list[str] = []
    if cls.model_config.get("extra") != "forbid":
        problems.append("it must refuse unknown fields (extra='forbid')")
    if not cls.model_config.get("frozen"):
        problems.append("it must be frozen: the instance enters input_hash")
    if getattr(cls, "BASIS_ID", None) != expected_id:
        problems.append(f"BASIS_ID must equal the registered id {expected_id!r}")
    label = getattr(cls, "LABEL", None)
    if not isinstance(label, str) or not label.strip():
        problems.append("LABEL must be a non-empty banner")
    field = cls.model_fields.get("basis_id")
    if field is None or not field.is_required():
        problems.append("basis_id must be a required field: a basis is chosen, never a default")
    elif get_origin(field.annotation) is not Literal or get_args(field.annotation) != (
        expected_id,
    ):
        problems.append(f"basis_id must be typed Literal[{expected_id!r}]")
    if "confidence_ceiling" not in cls.model_fields:
        problems.append("it must carry a confidence_ceiling field")
    if not callable(getattr(cls, "note", None)):
        problems.append("it must define note()")
    return problems


# The built-in bases are the first two entries of the chassis study-basis registry. A third basis
# is registered from its owner's package (`gridq.extensions`), not by editing this file.
for _basis_id, _basis_class in STUDY_BASIS_CLASSES.items():
    study_bases.register_builtin(_basis_id, _basis_class, version=gridq.__version__)
