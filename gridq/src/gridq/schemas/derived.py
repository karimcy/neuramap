"""Recorded derived assumptions — how a run continues when an input is missing (**D44**).

**Authority: Olly, 2026-08-12 (D44).** No missing input may block a validation run. Where a fact
about the site is absent, the most reasonable assumption is derived, **recorded**, and stated in the
result and the report as requiring confirmation; the run completes. This supersedes the
hostile-review-era gate policy that refused a run for a data reason.

## Three classes, and why the distinction lives in the schema

A missing *fact about the site*, a missing *permission from the customer*, and a component that
**does not apply to this site at all** are three different things and **must never share report
language**:

- **`derived_input`** — data that may reasonably be derived: connection capacity, load shape, a
  tariff component. Reported as *"assumed, confirmation required"*.
- **`pending_permission`** — a permission that cannot be derived at all: revenue-stream consent, an
  export MPAN, G99 approval. The stream is modelled and its value shown, labelled *"requires
  customer consent"*.
- **`not_applicable`** — the input is empty because the component **does not exist for this site**,
  and the emptiness is *evidenced* rather than unfilled. Reported as
  *"does not apply to this site, evidenced"*.

**A derived record is never written as consent granted.** That is enforced here, not merely
documented: a `pending_permission` record cannot carry a value, cannot claim a derivation method
other than ``not_derivable``, and must name the ask. Encoding the distinction in the schema is what
stops a later change fabricating a consent field.

### Why the third class exists: an evidenced absence is not an unfilled input

The D45 null-input guard could not tell *"null because nobody sourced it"* from *"null because the
component does not apply"*, because the schema had no way to say the second thing
(`tests/test_value_stack_null_input_guard.py::test_the_guard_cannot_tell_not_applicable_from_not_sourced`
pinned that limit and named a third class here as the remedy; the guard now reads this class, and
`tests/test_evidenced_absence_reaches_the_data_ask.py` is the acceptance). It is live, not
theoretical: Colnbrook
and Tanhouse are 100% private-wire, so **no** DUoS unit rate applies to either and the non-commodity
cost they do pay is already in the stack as a back-solved residual — adding a rate would
double-count it. Both site records say exactly that in their own `basis` prose, and both were
nevertheless forced into `derived_input`, so the register rendered an evidenced absence under
*"assumed, confirmation required"* and the report asked those sites for a rate they should never
supply.

`not_applicable` is a **disclosure** class, not a pricing one. It carries no value (there is none),
its ceiling policy is the same as every other class, and `gridq.quotes.confidence` folds it into the
band exactly as before, so introducing it moves **no number and no band**. What it changes is that
the reader is told which register entries are assumptions and which are evidenced absences, and the
confirmation ask becomes *"confirm this component does not apply"* rather than *"send us the rate"*.

**The class only earns its keep once a reader receives it, so two consumers read it.**
`gridq.quotes.confidence._register_composition` counts it out of the assumption count, and
`gridq.value_stack.gates.evidenced_absences` is read by the D45 null-input guard on every gate:
a null the register calls an evidenced absence keeps its gate CLOSED, its stream out of the priced
floor and its £ unchanged, but its note says the component does not exist for this site and its ask
becomes this record's own `confirmation_ask` instead of *"this reads NULL, send us the rate"*. The
match there is on `kind` **and** an exact `key`: a `derived_input` whose value was never written
back onto the `SiteModel` leaves the engine reading the same raw `None`, so it must keep the hard
ask, and one record may never vouch for a neighbouring key.

### The class is not the producer's free choice

Splitting the classes is only half the guard. `kind` was a free field, so a producer could write
a **consent-shaped key as a `derived_input`** — `key="stream_consents.CAPACITY_MARKET"`,
`value="granted"` — and it validated, then rendered under *"assumed, confirmation required"*: a
fabricated consent wearing the label of a derived fact. So the **key binds the class**: any key that
names a permission (`stream_consents.*`, an export MPAN, G98/G99/G100, the DNO or supplier consent
state — `is_permission_key`) must be recorded as `pending_permission`, and is therefore valueless by
the rule above. A permission is not a fact whichever field it is written into. It is not
`not_applicable` either: *"this consent does not apply"* is still an assertion about a third
party's decision, so a permission-shaped key is refused in that class too and can only be
`pending_permission`.

**The match is case-insensitive, and that is load-bearing rather than tidiness.** The classifier
compared raw strings, so the binding was defeated by capitalisation alone: `key="Consents.G99"`,
`value="granted"` validated as a `derived_input` and rendered *"G99 approval is missing — assumed,
confirmation required. used granted."* — the fabrication route above, reopened by a shift key, in
a report section whose purpose is to be the audit trail. The classifier is deliberately
**fail-closed** (see `PERMISSION_KEY_PREFIXES`): a false positive costs a producer one honest
redirection to `pending_permission`, a false negative puts an invented consent before a lender.

### The record is immutable

A validator only guards **construction**. `DerivedAssumption` is therefore ``frozen``: the record a
run consumed is the record that entered ``input_hash``, and no later assignment can turn a
constructed `derived_input` into the consent record the validator refuses to build. The freeze is on
this model alone rather than on `GridQModel` — see `gridq.schemas.provenance` for why the base class
cannot carry it.

## Ceilings

Every record caps the confidence band, and **no derived record may ever declare `HIGH`** — a value
that is assumed rather than confirmed cannot be the strongest evidence in the case, whatever else is
true. The policy per target field is written once in `ceiling_for_key` and enforced by a validator,
so a producer cannot quietly flatter its own record:

- **structural feasibility** inputs (connection capacity, export limit, the load series itself, the
  energy scale) cap at **LOW** — they decide whether the case is physically possible at all;
- **shape-like** inputs (load archetype, within-day shape, a tariff line) cap at **MEDIUM**,
  matching the `data/sites/hh/manifest.json` convention for a modelled shape;
- an **unrecognised** key caps at **LOW**. An unclassified missing input is not assumed harmless.

The ceiling policy binds **all three** classes. It once returned early for `pending_permission`, so
the claim "no producer may raise its own ceiling" held for `derived_input` only and a permission
record on `connection.mic_kw` could declare MEDIUM where the structural policy says LOW.
`ceiling_for_key` is deliberately **not** case-normalised, unlike `is_permission_key`: an
unrecognised key already falls to LOW, so a miscased shape key fails in the safe direction, and
normalising it would *raise* a ceiling from LOW to MEDIUM on a typo.

No LLM runs on the pricing path. Values reach a record in a **separate, offline derivation step**
that writes provenance; the pipeline only ever *reads* recorded records (`docs/decisions.md` D44).
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from gridq.schemas.enums import ConfidenceBand
from gridq.schemas.provenance import GridQModel

__all__ = [
    "PERMISSION_KEY_PREFIXES",
    "SHAPE_DISCLOSURE_KEYS",
    "SHAPE_LIKE_KEYS",
    "STRUCTURAL_KEYS",
    "DerivedAssumption",
    "ceiling_for_key",
    "is_permission_key",
]

#: Target fields whose absence is a **structural feasibility** question: they decide whether the
#: recommended system is physically possible, or set the energy scale every revenue line is derived
#: from. A derived value here caps the band at LOW.
STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {
        "connection.mic_kw",
        "connection.asc_kw",
        "connection.mec_kw",
        "connection.voltage_level",
        "export_arrangement.route",
        "export_arrangement.declared_export_limit_kw",
        "load_data.half_hourly_file_ref",
        "load_data.annual_kwh",
        "load_data.max_import_kw",
        "load_data.max_export_kw",
        "annual_consumption_kwh",
    }
)

#: Target fields that describe the **shape** of a known quantity rather than its existence or size:
#: which archetype, how it sits within the day, which line of a known tariff. A derived value here
#: caps the band at MEDIUM — the same ceiling a modelled half-hourly series carries.
SHAPE_LIKE_KEYS: frozenset[str] = frozenset(
    {
        "load_data.half_hourly_basis",
        "load_data.half_hourly_reference_profile",
        "load_data.half_hourly_scaled_to",
        "load_data.synthesis_year",
        "tariff_stack.duos_red_rate",
        "tariff_stack.duos_amber_rate",
        "tariff_stack.duos_green_rate",
        "tariff_stack.levies",
        "tariff_stack.import_rates",
        # WP-UW-07 (D103/D105): which band an unquoted rate or an unstated window belongs to
        # is the shape of a known tariff, not its existence; and the VAT basis of a stated rate
        # is likewise a line of a known tariff.
        "tariff_stack.import_rates.red",
        "tariff_stack.import_rates.amber",
        "tariff_stack.import_rates.green",
        "tariff_stack.import_rates.day",
        "tariff_stack.import_rates.night",
        "tariff_stack.import_rates.peak",
        "tariff_stack.import_rates.offpeak",
        "tariff_stack.import_rate_windows",
        "tariff_stack.basis.vat",
        "counterparty.sector",
    }
)


#: The keys that count as **disclosing a modelled load shape**. A site whose `half_hourly_basis` is
#: MODELLED has had its entire within-day load shape assumed, so its register must carry a record on
#: one of these keys or the register is silent about the largest assumption in the run. Read by
#: `gridq.quotes.confidence` to make "no input had to be assumed" unreachable for such a site.
SHAPE_DISCLOSURE_KEYS: frozenset[str] = frozenset(
    {
        "load_data.half_hourly_basis",
        "load_data.half_hourly_reference_profile",
        "load_data.half_hourly_scaled_to",
        "load_data.half_hourly_file_ref",
        "load_data.synthesis_year",
    }
)

#: Key stems that name a **permission**, not a fact about the site: a customer authorisation, a DNO
#: connection approval, or an issued export MPAN. None of these is derivable from data at any
#: confidence, so a record on one of them is a `pending_permission` and nothing else. Matching is by
#: stem, so ``consents.g99``, ``consents.g99_approved`` and ``consents.g99.reference`` all bind.
PERMISSION_KEY_PREFIXES: tuple[str, ...] = (
    # Per-stream customer authorisation (D43 spine, D44/D45 register).
    "stream_consents",
    # DNO connection approvals. Granted by the DNO, evidenced by a reference — never inferred.
    "consents.g98",
    "consents.g99",
    "consents.g100",
    "g98",
    "g99",
    "g100",
    # An export MPAN is issued by a supplier/DNO. A site either has one or does not.
    "consents.export_mpan",
    "export_mpan",
    # Application/consent states: a process status held by a third party.
    "consents.dno_application_state",
    "consents.supplier_consent_state",
)

#: The same stems, case-folded once at import. Derived from `PERMISSION_KEY_PREFIXES` rather than
#: written out again, so a stem added above can never be added in a form the matcher does not see.
_PERMISSION_KEY_STEMS: tuple[str, ...] = tuple(p.casefold() for p in PERMISSION_KEY_PREFIXES)


def is_permission_key(key: str) -> bool:
    """Whether ``key`` names a permission rather than a fact, and so binds `pending_permission`.

    The consent-fabrication route this closes: `kind` was the producer's free choice, so a
    `derived_input` record keyed ``stream_consents.CAPACITY_MARKET`` with ``value="granted"``
    validated and then rendered under *"assumed, confirmation required"* — a consent invented in the
    one place the reader is told assumptions live. Binding the key to the class makes that record
    unconstructible, and because a `pending_permission` may not carry a value, the fabricated
    ``"granted"`` has nowhere left to go.

    Matched on the stem so a field-suffixed or dotted variant of the same permission binds too,
    and **case-insensitively**, because the rule above was defeated by capitalisation alone:
    ``Consents.G99`` with ``value="granted"`` validated as a `derived_input` and rendered under
    *"assumed, confirmation required"*. The whole route this function exists to close was open to
    anyone who typed a key with a capital letter, which is not a hostile act and needs no intent.
    """
    stem = key.strip().casefold()
    return any(
        stem == prefix or stem.startswith(f"{prefix}.") or stem.startswith(f"{prefix}_")
        for prefix in _PERMISSION_KEY_STEMS
    )


def ceiling_for_key(key: str) -> ConfidenceBand:
    """The best confidence band a derived value for ``key`` can support (**the policy, once**).

    Structural feasibility inputs cap at LOW, shape-like inputs at MEDIUM, and anything
    unrecognised at LOW — an unclassified missing input is never assumed harmless. HIGH is
    unreachable by construction: a derived value is by definition unconfirmed.
    """
    if key in SHAPE_LIKE_KEYS:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


class DerivedAssumption(GridQModel):
    """One recorded stand-in for a missing input, and everything needed to judge and confirm it.

    Carried on `SiteModel.derived_assumptions` when the value was derived **offline** and actually
    consumed by the run (it enters ``input_hash``, so two runs on different derived capacities can
    never share a ``run_id``), and emitted transiently by the ingestion gates when a run continued
    past a missing input without a recorded value (those reach the quote's warnings and the
    confidence factors, never the site — a gate observation is not an input and must not move any
    existing run identity).

    **Immutable (``frozen``).** The record is part of the run's identity, so it must not be able to
    change after the validator has passed it: ``rec.kind = "pending_permission"`` on a record that
    already carries a value would flip `report_label` to *"requires customer consent"* and produce
    exactly the record `_enforce_class_and_ceiling` refuses to build. Only this model is frozen —
    `GridQModel` cannot be (`gridq.schemas.provenance`).
    """

    # Frozen rather than `validate_assignment`: re-validating on assignment would still permit a
    # legal in-place edit (a different `value` on a `derived_input`) to a record already hashed into
    # `input_hash`. `extra="forbid"` is restated because pydantic merges a subclass's `model_config`
    # with the base's, and stating it keeps this model's contract readable on its own.
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: `derived_input` = a fact that may reasonably be derived. `pending_permission` = a permission
    #: that cannot be derived at all. `not_applicable` = the component does not exist for this site
    #: and the emptiness is evidenced. The three never share report language — see `report_label`.
    #: **Not the producer's free choice:** a permission-shaped `key` forces `pending_permission`.
    #: Only `derived_input` may carry a value; the other two are valueless by construction.
    kind: Literal["derived_input", "pending_permission", "not_applicable"]
    #: Dotted target field, e.g. ``"connection.mic_kw"``. Drives the ceiling policy.
    key: str = Field(min_length=1)
    #: What was absent, in plain words a customer would recognise.
    missing_input: str = Field(min_length=1)
    #: The value used. **Always None for `pending_permission` and `not_applicable`** — a permission
    #: is not a value and a record must never be readable as consent granted, and a component that
    #: does not exist for this site has no value to record.
    value: float | str | None = None
    unit: str | None = None
    #: The reasoning that justifies this value. Never "reasonable" on its own: what it was derived
    #: from, and what it assumes.
    basis: str = Field(min_length=1)
    #: Citations. **Empty is allowed and must stay visible** — an uncited derivation is a weaker
    #: record than a cited one and the reader is entitled to see which it is.
    sources: list[str] = Field(default_factory=list)
    #: How the value was produced. ``archetype_pending_source`` is the honest label for a documented
    #: engineering archetype that no published curve backs yet
    #: (`gridq.ingestion.hh_synthesis.ProfileSource.ARCHETYPE_PENDING_SOURCE`): it is neither a
    #: ``sourced_default`` — there is no source, and `sources` stays visibly empty — nor a
    #: ``site_analogue``, because no peer site's data is behind it.
    derivation_method: Literal[
        "fable_advisor",
        "sourced_default",
        "archetype_pending_source",
        "site_analogue",
        "not_derivable",
    ]
    #: Who or what derived it: a model identity (``"claude-fable-5"``) for an advisor-derived value,
    #: the module for a deterministic one (``"gridq.ingestion.hh_mandate"``).
    derived_by: str = Field(min_length=1)
    #: The assumption release in force when the value was derived (e.g. ``"gb_v1_6"``). ``None``
    #: when the record was made outside a library-scoped run — honest absence, never a placeholder.
    derived_on_release: str | None = None
    #: The band cap this record imposes. Never HIGH; never better than `ceiling_for_key`.
    confidence_ceiling: ConfidenceBand
    #: Exactly what to ask the customer to retire this record.
    confirmation_ask: str = Field(min_length=1)

    @model_validator(mode="after")
    def _enforce_class_and_ceiling(self) -> DerivedAssumption:
        if self.confidence_ceiling is ConfidenceBand.HIGH:
            raise ValueError(
                "a DerivedAssumption may never declare a HIGH confidence ceiling — an assumed "
                "value cannot be the strongest evidence in a case, however sound the reasoning."
            )
        # Branch on the class EXPLICITLY, never `if permission: ... else: <derived_input rules>`.
        # That shape silently applied the derived_input rules to whatever class was added next, so a
        # third kind would have inherited "may carry a value" without anyone writing it down.
        if self.kind == "pending_permission":
            # The teeth of the class split: a permission record cannot be written as though the
            # permission were held. No value, no derivation claim, and it must name the ask.
            if self.value is not None or self.unit is not None:
                raise ValueError(
                    "a pending_permission record must not carry a value or unit: a permission is "
                    "not a derivable fact and the record must never be readable as consent given. "
                    "Model the stream, show its value on the result, and label it "
                    "'requires customer consent'."
                )
            if self.derivation_method != "not_derivable":
                raise ValueError(
                    f"pending_permission requires derivation_method='not_derivable', got "
                    f"{self.derivation_method!r} — a permission cannot be derived from data."
                )
        elif self.kind == "not_applicable":
            # An evidenced absence. There is no value because the component does not exist for this
            # site, so a value here would be exactly the fabrication the class was added to avoid:
            # a rate asserted on a site that faces no such charge.
            if self.value is not None or self.unit is not None:
                raise ValueError(
                    "a not_applicable record must not carry a value or unit: the class means the "
                    "component does not exist for this site, so there is nothing to value. Record "
                    "the evidence in `basis` and the ask in `confirmation_ask`; if the site DOES "
                    "face this charge and it genuinely measures zero, that is a measured 0.0 on "
                    "the field itself, which is a fact and needs no register entry."
                )
            if self.derivation_method != "not_derivable":
                raise ValueError(
                    f"not_applicable requires derivation_method='not_derivable', got "
                    f"{self.derivation_method!r} — no value was produced, so no method produced "
                    "one. The reasoning that establishes non-applicability belongs in `basis`."
                )
            if is_permission_key(self.key):
                raise ValueError(
                    f"{self.key!r} names a PERMISSION, so it cannot be recorded as not_applicable: "
                    "'this consent does not apply' is still an assertion about a third party's "
                    "decision, and it is not ours to make. A permission-shaped key "
                    "(`gridq.schemas.derived.is_permission_key`) binds kind='pending_permission', "
                    "which states the ask without asserting the answer either way."
                )
        elif self.kind == "derived_input":
            # The key binds the class. Without this, `kind` was the producer's free choice and a
            # consent could be fabricated in the register itself: key
            # "stream_consents.CAPACITY_MARKET", value "granted", rendered to the reader as
            # "assumed, confirmation required". A permission is not a fact in any field.
            if is_permission_key(self.key):
                raise ValueError(
                    f"{self.key!r} names a PERMISSION, so it cannot be recorded as a "
                    "derived_input: consent-shaped keys (stream_consents.*, an export MPAN, "
                    "G98/G99/G100, the DNO/supplier consent state — "
                    "`gridq.schemas.derived.is_permission_key`) bind kind='pending_permission', "
                    "which carries no value and can never be read as consent granted. A "
                    "permission cannot be derived from data, and a derived record labelled "
                    "'assumed, confirmation required' would present a fabricated consent as an "
                    "ordinary modelling assumption."
                )
            if self.derivation_method == "not_derivable" and self.value is not None:
                raise ValueError(
                    "derivation_method='not_derivable' cannot carry a value — record the absence "
                    "and the ask, or record a value and the method that produced it."
                )
        else:  # pragma: no cover - unreachable while `kind` is the Literal above
            raise ValueError(
                f"unhandled DerivedAssumption kind {self.kind!r}. A new class must state its own "
                "rules here: whether it may carry a value, which derivation methods it may claim, "
                "and whether a permission-shaped key may enter it. Falling through to another "
                "class's rules is how a valueless class would silently gain the right to a value."
            )
        # The ceiling policy binds ALL THREE classes. It used to be reached only by `derived_input`
        # (the permission branch returned early), so a pending_permission on `connection.mic_kw`
        # could declare MEDIUM where the structural policy says LOW — a producer raising its own
        # ceiling, which is the one thing writing the policy down once was meant to prevent.
        policy = ceiling_for_key(self.key)
        if self.confidence_ceiling.rank > policy.rank:
            raise ValueError(
                f"derived value for {self.key!r} declares a {self.confidence_ceiling.value} "
                f"ceiling but the D44 ceiling policy allows at most {policy.value} "
                f"(`gridq.schemas.derived.ceiling_for_key`). A strong input never compensates for "
                "a fatal one, so a producer may not raise its own ceiling."
            )
        return self

    @property
    def report_label(self) -> str:
        """The report language for this record's class. The three classes never share a sentence.

        Read into the reader-facing text at `gridq.quotes.confidence._derived_factor` and into every
        register warning via `statement`, so the label is the whole reason the classes are separate:
        an evidenced absence rendered as *"assumed, confirmation required"* tells a client to go and
        find a number that does not exist.
        """
        if self.kind == "pending_permission":
            return "requires customer consent"
        if self.kind == "not_applicable":
            return "does not apply to this site, evidenced"
        return "assumed, confirmation required"

    def statement(self) -> str:
        """One reader-facing line: what was missing, what was used, and what to confirm."""
        if self.kind == "pending_permission":
            used = "no value used"
        elif self.kind == "not_applicable":
            used = "no value applies"
        elif self.value is None:
            used = "no value derived"
        else:
            unit = f" {self.unit}" if self.unit else ""
            used = f"used {self.value}{unit}"
        # "is missing" is false for an evidenced absence, and it is the exact sentence that sent a
        # data ask to a private-wire site for a distribution rate no supplier could ever issue it.
        absence = (
            "is EMPTY BY EVIDENCE rather than unfilled"
            if self.kind == "not_applicable"
            else "is missing"
        )
        cited = "; ".join(self.sources) if self.sources else "no citation recorded"
        return (
            f"{self.key}: {self.missing_input} {absence} — {self.report_label}. {used}. "
            f"Basis: {self.basis} (sources: {cited}). Confidence capped at "
            f"{self.confidence_ceiling.value.upper()}. Confirm: {self.confirmation_ask}"
        )
