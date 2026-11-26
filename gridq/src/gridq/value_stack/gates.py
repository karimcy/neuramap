"""Deterministic availability-gate logic — generic and market-agnostic.

These are pure predicates over a ``SiteModel`` and individual ``Assumption`` records, plus
one builder that assembles a consistent :class:`GateResult`. They contain **no GB content**
and **no market-name branches**: they read site fields (tariff structure, DUoS rates,
connection limits, observed peak, export consents) and assumption *status*, and report what
is present or missing. The GB streams in ``gb_streams.py`` wire these predicates to the
right SiteModel fields and assumption sections.

Hard rules honoured here (see SKILLS.md / parallel_development_plan §5):

- **No invented data.** A predicate that lacks its input returns ``False`` (→ a missing
  requirement), never a guessed ``True``.
- **Gates decide priceability, not money.** They never produce a revenue number.
- **Only an OPEN gate is priceable.** ``build_gate_result`` enforces the
  ``OPEN → firm/contracted`` · ``CONDITIONAL → conditional`` · ``CLOSED/UNKNOWN → excluded``
  mapping so a stream can't report an inconsistent (status, firmness) pair.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from gridq.assumptions import Assumption
from gridq.schemas import ConsentStatus, DerivedAssumption, Firmness, MarketStream, SiteModel
from gridq.value_stack.interface import GateResult, GateStatus, RequiredInput

# ---------------------------------------------------------------------------
# Site-data predicates (import-side floor)
# ---------------------------------------------------------------------------


def has_usable_tariff_arbitrage_data(site: SiteModel) -> bool:
    """True if the import tariff has a price *spread* to arbitrage.

    A single flat rate has nothing to arbitrage; arbitrage needs at least two distinct
    import price points (e.g. a time-of-use / wholesale-shaped structure). Reads only the
    site's own tariff data — a missing or flat tariff is reported, never invented.
    """
    rates = site.tariff_stack.import_rates
    distinct = {round(v, 10) for v in rates.values()}
    return len(distinct) >= 2


def has_duos_rate_data(site: SiteModel) -> bool:
    """True if DUoS band rate data is present (at least the red band)."""
    t = site.tariff_stack
    return t.duos_red_rate is not None


def has_connection_limits(site: SiteModel) -> bool:
    """True if a binding connection capacity (MIC or ASC) is recorded."""
    c = site.connection
    if c.mic_kw is not None and c.mic_kw > 0:
        return True
    return c.asc_kw is not None and c.asc_kw > 0


def connection_bounded_response_mw(site: SiteModel) -> float | None:
    """The most generous MW this site's CONNECTION could ever present to a market, or ``None``.

    A behind-the-meter unit cannot deliver more response than its connection physically passes:
    an injection-direction response is bounded by the export capacity, a demand-turn-down response
    by the import capacity. This returns the **larger** of the two (``max(mic, mec)``) precisely so
    it is an upper bound that over-states what the site could do — a size gate may then safely
    *disprove* eligibility with it (if even this bound is below a market's minimum, no unit on this
    connection can clear it) while never wrongly blocking a site that could qualify.

    It is deliberately **not** the sized battery's power, which is a sizing *output* the gate layer
    cannot see; it is the site's own recorded data, so nothing is invented. ``None`` when no
    capacity is recorded.
    """
    c = site.connection
    candidates = [v for v in (c.mic_kw, c.mec_kw) if v is not None and v > 0]
    if not candidates:
        return None
    return max(candidates) / 1000.0


class CapacityEvidence(NamedTuple):
    """A connection capacity this site actually EVIDENCES, and how strongly (D45).

    ``basis`` is ``'recorded'`` when the site's own connection record carries the figure, or
    ``'declared'`` when it comes from a D45 ``derived_input`` assumption — a value derived
    offline, declared with its basis, and reported as "assumed, confirmation required". The two
    are never merged: a declared capacity is a structural input and caps the confidence band at
    **low** (`schemas.derived.STRUCTURAL_KEYS`), so the distinction has to survive to the note.
    ``field`` names the dotted input the figure came from, so the working is traceable.
    """

    mw: float
    basis: str
    field: str


def _declared_capacity_mw(site: SiteModel, fields: tuple[str, ...]) -> CapacityEvidence | None:
    """A D45-**declared** capacity (MW) for any of ``fields``, or ``None`` — never a guess.

    D45's contract: where an input is absent it may be derived offline and recorded as a
    ``derived_input`` assumption, and the run then reads the recorded value. This reads exactly
    that record and nothing else. A ``pending_permission`` record is **never** capacity evidence:
    it carries no value by construction (a permission is not a fact), and reading one as capacity
    would be the "consent manufactures eligibility" defect wearing a different hat.

    Units follow the target field, which is ``kW`` for every connection capacity, so a record
    whose ``unit`` contradicts that is refused rather than converted on a guess (``kVA`` is
    accepted at unity power factor — the same convention `schemas.tariff` already records).
    """
    for rec in site.assumptions_requiring_confirmation:
        if rec.kind != "derived_input" or rec.key not in fields:
            continue
        value = rec.value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            continue
        unit = (rec.unit or "kw").strip().lower()
        if unit not in {"kw", "kva"}:
            continue
        return CapacityEvidence(float(value) / 1000.0, "declared", rec.key)
    return None


def evidenced_export_capacity_mw(site: SiteModel) -> CapacityEvidence | None:
    """The export capacity (MW) this site EVIDENCES, or ``None`` when none is evidenced at all.

    The injection-direction bound: a unit can only push power out through the export capacity its
    connection actually has. ``None`` is the honest answer when the site records no export
    capacity — it is **not** "unlimited" and must never be read as one, because absence of an
    export right is the reason a unit cannot be a generating/exporting participant at all.
    """
    mec = site.connection.mec_kw
    if mec is not None and mec > 0:
        return CapacityEvidence(mec / 1000.0, "recorded", "connection.mec_kw")
    return _declared_capacity_mw(site, ("connection.mec_kw",))


def evidenced_import_capacity_mw(site: SiteModel) -> CapacityEvidence | None:
    """The import capacity (MW) this site EVIDENCES, or ``None`` when none is evidenced at all.

    The demand-direction bound: a demand-reduction service can never exceed the import capacity
    the connection is contracted for. MIC and ASC are both import-side records of that same
    capacity, so the larger is the honest upper bound; ``mic_kw = 0`` is the schema's way of
    saying "not declared" (`ConnectionDetails.mic_kw` is non-nullable), so it is treated as
    absent rather than as a real zero.
    """
    c = site.connection
    recorded = [
        (v, f)
        for v, f in ((c.mic_kw, "connection.mic_kw"), (c.asc_kw, "connection.asc_kw"))
        if v is not None and v > 0
    ]
    if recorded:
        value, field = max(recorded, key=lambda pair: pair[0])
        return CapacityEvidence(value / 1000.0, "recorded", field)
    return _declared_capacity_mw(site, ("connection.mic_kw", "connection.asc_kw"))


def has_observed_peak(site: SiteModel) -> bool:
    """True if an observed peak import is available (measured peak or HH coverage).

    Annual consumption alone does not give a peak, so a T0 (annual-only) site returns
    ``False`` — the connection-binding question cannot be answered without a peak.
    """
    load = site.load_data
    if load.max_import_kw is not None and load.max_import_kw > 0:
        return True
    return load.has_hh_coverage


def has_load_data(site: SiteModel) -> bool:
    """True if any usable load signal is present (HH coverage, peak, or annual kWh)."""
    load = site.load_data
    if load.has_hh_coverage:
        return True
    if load.max_import_kw is not None and load.max_import_kw > 0:
        return True
    annual = site.annual_consumption_kwh or load.annual_kwh
    return annual is not None and annual > 0


# ---------------------------------------------------------------------------
# Consent / export predicates
# ---------------------------------------------------------------------------


def has_export_capability(site: SiteModel) -> bool:
    """True if the connection can export (MEC > 0). Export streams need this."""
    mec = site.connection.mec_kw
    return mec is not None and mec > 0


def has_export_consent(site: SiteModel) -> bool:
    """True if export is permitted: MEC > 0 and the export consents (G99/G100) approved."""
    if not has_export_capability(site):
        return False
    c = site.consents
    return c.g99 == ConsentStatus.APPROVED and c.g100 == ConsentStatus.APPROVED


def export_dispatch_enabled(site: SiteModel) -> bool:
    """True if the site's declared consents cohere for wholesale EXPORT dispatch (issue 018, A2/A3).

    The strictest intersection (design doc R6): the export consents approved AND export capability
    (``has_export_consent`` — G99 ∧ G100 approved ∧ MEC>0) AND an export MPAN on record. The
    engine reads this DECLARED state to decide whether to run the consent-gated export overlay
    (`MilpEngine._export_upside`); it never invents a consent, and a missing one simply leaves the
    overlay off (byte-identical to no-export). Consent state is a first-class frontend-declared
    input (owner steer, 018 §9) — this predicate reflects it, it does not hard-block the run.
    """
    if not has_export_consent(site):
        return False
    return bool(site.consents.export_mpan)


# ---------------------------------------------------------------------------
# Per-stream customer consent (Revenue-Data Drive / D43)
# ---------------------------------------------------------------------------


def stream_consented(site: SiteModel, stream: MarketStream) -> bool:
    """True iff the customer explicitly consented, at intake, to GridQ pursuing ``stream``.

    Reads the site's own ``stream_consents`` directly (the same read-off-SiteModel pattern as
    :func:`has_export_consent`), never a guess: a site with no per-stream consents returns
    ``False`` for every stream. Consent is *authorisation*, not value — it never on its own
    creates a £ or a firmness; it lets a gate reach the contracted case and stamps the "consent
    given" label.
    """
    return stream in site.consented_streams


def agreed_rate(site: SiteModel, stream: MarketStream) -> float | None:
    """The commercially agreed rate recorded for ``stream``, or ``None`` (never guessed).

    Precedence: the generic per-stream carrier (``SiteModel.stream_consents[…].agreed_rate_gbp``)
    wins; else the stream's dedicated rate home on ``ExportArrangement`` — the export tariff
    (£/kWh) for wholesale/P415, the local-flex agreed rate (£/MWh) for local flex. Any other
    stream has no dedicated home, so it falls to ``None`` when no generic rate was captured. The
    unit is therefore stream-dependent (as its rate home is); the caller knows which stream it
    asked for. A missing rate is ``None`` — it is never invented.
    """
    generic = site.agreed_rate_for(stream)
    if generic is not None:
        return generic
    arrangement = getattr(site, "export_arrangement", None)
    if arrangement is not None:
        if stream == MarketStream.WHOLESALE_P415:
            return arrangement.export_tariff_gbp_per_kwh
        if stream == MarketStream.LOCAL_FLEX_PICLO:
            return arrangement.local_flex_agreed_rate_gbp_per_mwh
    return None


# ---------------------------------------------------------------------------
# Deployment / portfolio / assumption-status predicates
# ---------------------------------------------------------------------------


def route_to_market_available(rtm_assumption: Assumption | None) -> bool:
    """True only if a route-to-market value is *confirmed* and positive.

    The gated-upside streams (wholesale/P415, BM, ancillary) need a real route-to-market
    partner. In the placeholder library their value is zero with ``needs_review`` status,
    so this returns ``False`` and the stream stays conditional upside — exactly the
    behaviour the brief requires until the RTM partner's real numbers land (BLOCKERS B002).
    """
    if rtm_assumption is None:
        return False
    if rtm_assumption.status.value != "confirmed":
        return False
    value = rtm_assumption.value
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def meets_portfolio_threshold(portfolio_mw: float | None, threshold_mw: float | None) -> bool:
    """True if an aggregated portfolio of ``portfolio_mw`` meets ``threshold_mw``.

    A single-site underwriting run carries no portfolio aggregate (``portfolio_mw`` is
    ``None``), so portfolio-gated streams (ancillary, BM) stay conditional upside.
    """
    if portfolio_mw is None or threshold_mw is None:
        return False
    return portfolio_mw >= threshold_mw


class EligibilityVerdict(NamedTuple):
    """Whether a unit may physically ENTER a market, and on what route (D-92).

    ``eligible`` answers only "can this unit be registered into the market at all" — never
    "is it worth anything". ``route`` names how: ``'standalone'`` (the unit itself clears the
    minimum), ``'aggregated'`` (it clears only inside an aggregated portfolio),
    ``'not_armed'`` (no threshold was supplied, so no size gate applies — eligibility is
    **unasserted**, not confirmed), ``'below_threshold'`` (proven too small) or ``'unproven'``
    (no size evidence at all). ``sellable_mw`` is the quantity actually offerable after any
    whole-MW order granularity; ``reasons`` carries the human-readable working.
    """

    eligible: bool
    route: str
    sellable_mw: float | None
    reasons: tuple[str, ...]


def sellable_whole_mw(unit_mw: float | None) -> float | None:
    """``unit_mw`` truncated to whole MW, or ``None`` when unknown (never invented).

    Some markets accept sell orders only in **integer** MW, so any fractional remainder above an
    integer boundary is simply not offerable — a 0.9 MW unit can offer nothing at all and a
    1.6 MW unit can offer 1 MW. Truncation (not rounding) is the conservative direction: rounding
    up would offer capacity the asset does not have. Generic: the *rule* lives here, the market's
    decision to apply it is content (see ``gb_streams``).
    """
    if unit_mw is None or unit_mw < 0:
        return None
    return float(int(unit_mw))


#: How the tested quantity is described in a verdict's reasons when the caller says nothing. The
#: predicate is fed different quantities by different callers (a sized unit, a connection bound, a
#: CMU's evidenced capacity) and the reason line must name the one it actually tested — a reader
#: told "the unit's 2.000 MW" under a rule written on CONNECTION capacity has been misinformed
#: about which fact the verdict rests on, even when the arithmetic is right.
DEFAULT_QUANTITY_LABEL = "the unit's"


def market_entry_eligibility(
    *,
    unit_mw: float | None,
    portfolio_mw: float | None,
    threshold_mw: float | None,
    whole_mw_orders: bool = False,
    quantity_label: str = DEFAULT_QUANTITY_LABEL,
) -> EligibilityVerdict:
    """Can a unit of ``unit_mw`` enter a market with a ``threshold_mw`` minimum? (D-92)

    The generic predicate behind the fix for "consent alone opens the market gates": **consent
    authorises pursuit, it can never manufacture eligibility**. Two routes clear a minimum-size
    threshold — the unit on its own, or an aggregated portfolio that contains it — and the caller
    supplies both, plus the threshold as market *content* (no number is defined in this
    market-agnostic module).

    Honest tri-state, following this module's no-invented-data rule:

    - ``threshold_mw is None`` ⇒ no size gate is armed. Returns ``eligible=True`` with route
      ``'not_armed'`` and a reason saying eligibility was **not asserted** — used where the
      threshold is not yet sourced, so the gate must not silently invent one *or* silently block.
    - a portfolio clearing the threshold ⇒ ``'aggregated'``. Fractional unit MW is offerable
      inside the aggregate (that is what an aggregator is for), so ``whole_mw_orders`` does not
      truncate on this route.
    - a unit clearing it on its own ⇒ ``'standalone'``, truncated to whole MW when the market
      only accepts integer sell orders.
    - a unit proven below it ⇒ ``'below_threshold'``, ``eligible=False``.
    - no size evidence at all ⇒ ``'unproven'``, ``eligible=False`` (absence of evidence is never
      taken as eligibility).

    ``quantity_label`` names the quantity ``unit_mw`` actually is, in the reasons the verdict
    reports. It is a **traceability** control, not a behavioural one: the arithmetic is identical
    whatever it says. The default describes the sized unit, so a caller feeding the predicate
    something else (a connection bound, a CMU's evidenced connection/DSR capacity) must relabel it
    rather than let the reason line assert a fact about the unit that was never tested.
    """
    if threshold_mw is None:
        return EligibilityVerdict(
            True,
            "not_armed",
            unit_mw,
            (
                "no minimum-size threshold supplied for this market, so no eligibility gate was "
                "applied — eligibility is UNASSERTED, not confirmed.",
            ),
        )
    if portfolio_mw is not None and portfolio_mw >= threshold_mw:
        own = (
            "The unit's own"
            if quantity_label == DEFAULT_QUANTITY_LABEL
            else quantity_label[0].upper() + quantity_label[1:]
        )
        return EligibilityVerdict(
            True,
            "aggregated",
            unit_mw,
            (
                f"eligible via AGGREGATION: the portfolio's {portfolio_mw:,.3f} MW clears the "
                f"{threshold_mw:,.3f} MW minimum. {own} "
                f"{'unknown' if unit_mw is None else format(unit_mw, ',.3f')} MW is offerable "
                "inside the aggregate.",
            ),
        )
    if unit_mw is None:
        return EligibilityVerdict(
            False,
            "unproven",
            None,
            (
                f"eligibility UNPROVEN: this market requires at least {threshold_mw:,.3f} MW and "
                "no unit size or aggregated portfolio MW was supplied. No size evidence is not "
                "evidence of size.",
            ),
        )
    if unit_mw < threshold_mw:
        return EligibilityVerdict(
            False,
            "below_threshold",
            0.0,
            (
                f"INELIGIBLE: {quantity_label} {unit_mw:,.3f} MW is below this market's "
                f"{threshold_mw:,.3f} MW minimum, and no aggregated portfolio clears it either.",
            ),
        )
    sellable = sellable_whole_mw(unit_mw) if whole_mw_orders else unit_mw
    reasons = [
        f"eligible STANDALONE: {quantity_label} {unit_mw:,.3f} MW clears the "
        f"{threshold_mw:,.3f} MW minimum.",
    ]
    if whole_mw_orders and sellable is not None and sellable != unit_mw:
        reasons.append(
            f"sell orders are accepted only in WHOLE MW, so {sellable:,.0f} MW of the unit's "
            f"{unit_mw:,.3f} MW is offerable; the {unit_mw - sellable:,.3f} MW remainder is not "
            "sellable into this market."
        )
    return EligibilityVerdict(True, "standalone", sellable, tuple(reasons))


def deliverability_confirmed(deliverability: bool | None) -> bool:
    """True only if degraded-capability deliverability is explicitly confirmed.

    The SiteModel carries no deliverability field in v1, so this is ``None`` → ``False``:
    Capacity Market stays conditional (never firm) until deliverability to the final
    contract year at degraded capability is verified.
    """
    return deliverability is True


def local_flex_tender_open(tender_open: bool | None) -> bool:
    """True only if an open local-flex tender in the site's area is explicitly known."""
    return tender_open is True


# ---------------------------------------------------------------------------
# Gate-result assembly (the single place the status→firmness invariant lives)
# ---------------------------------------------------------------------------

# Firmness implied by a gate, given the stream's firmness when its gate is OPEN.
_NON_OPEN_FIRMNESS: dict[GateStatus, Firmness] = {
    GateStatus.CONDITIONAL: Firmness.CONDITIONAL,
    GateStatus.CLOSED: Firmness.EXCLUDED,
    GateStatus.UNKNOWN: Firmness.EXCLUDED,
}


def build_gate_result(
    stream: MarketStream,
    status: GateStatus,
    *,
    open_firmness: Firmness = Firmness.FIRM,
    missing_requirements: list[str] | None = None,
    unlock_condition: str | None = None,
    warnings: list[str] | None = None,
    consent_status: str | None = None,
) -> GateResult:
    """Assemble a consistent :class:`GateResult` from a gate status.

    Derives ``can_be_priced_now`` and ``firmness`` from ``status`` so a stream can never
    report an inconsistent pair: only ``OPEN`` is priceable (and takes the stream's
    ``open_firmness`` — ``FIRM`` for the floor, ``CONTRACTED`` for contracted streams);
    ``CONDITIONAL`` → conditional; ``CLOSED``/``UNKNOWN`` → excluded.

    ``consent_status`` is the orthogonal "consent given" label (Revenue-Data Drive / D43): it
    records that the customer explicitly consented to this stream at intake, independently of
    whether the gate opened. It never influences ``firmness`` or ``can_be_priced_now``.
    """
    if status == GateStatus.OPEN:
        firmness = open_firmness
        can_price = True
    else:
        firmness = _NON_OPEN_FIRMNESS[status]
        can_price = False
    return GateResult(
        stream=stream,
        gate_status=status,
        can_be_priced_now=can_price,
        firmness=firmness,
        missing_requirements=list(missing_requirements or []),
        unlock_condition=unlock_condition,
        warnings=list(warnings or []),
        consent_status=consent_status,
    )


# ---------------------------------------------------------------------------
# The D45 null-input guard — a declaration-driven invariant over every gate
# ---------------------------------------------------------------------------

#: The machine-detectable stamp every null-input report carries. Downstream consumers (and tests)
#: match on this prefix, so the report can never degrade into indistinguishable prose. Deliberately
#: a *prefix* of the whole sentence: `ValueStreamResult.warnings` is a flat list of strings and a
#: reader filtering it needs one stable token, not a regex over hand-written gate text.
D45_NULL_INPUT_LABEL = "NULL INPUT ON A DECLARED PRICING INPUT (D45)"

#: What a declared input reads as on a site. Three states, not two — the third is the honest one.
INPUT_PRESENT = "present"
INPUT_ABSENT = "absent"
INPUT_UNRESOLVABLE = "unresolvable"

_MISSING = object()


def _resolve_path(site: SiteModel, path: str) -> object:
    """Walk a dotted attribute path on the site; ``_MISSING`` if no such path exists.

    Attribute access, not dictionary access: every declared key names a model field (or a model
    property such as ``load_data.has_hh_coverage``), and reading properties is deliberate — a
    derived "do we have this at all" flag is exactly what a disjunctive requirement needs.
    """
    current: object = site
    for part in path.split("."):
        current = getattr(current, part, _MISSING)
        if current is _MISSING:
            return _MISSING
    return current


def input_presence(site: SiteModel, path: str) -> str:
    """Whether ``path`` reads as present, absent, or is not a SiteModel path at all.

    The three-way split is the whole point, and each arm is a decision:

    - **``INPUT_UNRESOLVABLE``** — the key names something the SiteModel does not carry
      (``deliverability.degraded_capability`` comes from ``GateContext``; ``dfs.zone`` and
      ``route_to_market.partner`` are not site fields). The guard cannot judge those and must not
      pretend to, so it stays silent and the stream's own gate stays the authority. **This is the
      real limit**, stated rather than hidden: a declared key that resolves nowhere is invisible to
      it, which is why `tests/test_value_stack_null_input_guard.py` pins that every *floor* stream's
      blocking declarations are readable.
    - **``INPUT_ABSENT``** — the field exists and reads ``None``, an empty container, or ``False``.
      A ``False`` boolean is absence because the only booleans declared here are "do we have it"
      flags (``load_data.has_hh_coverage``), where ``False`` means *no*.
    - **``INPUT_PRESENT``** — anything else, **including a declared ``0.0``**. A recorded zero is
      evidence: it says the site faces no charge on that line. Conflating it with ``None`` is the
      other half of the same defect (`optimisation.milp_solver.fixed_line_absence_warnings` names
      the ``or 0.0`` idiom that swallowed exactly this), and it would fire the guard on sites whose
      data is complete.
    """
    value = _resolve_path(site, path)
    if value is _MISSING:
        return INPUT_UNRESOLVABLE
    if value is None:
        return INPUT_ABSENT
    if isinstance(value, bool):
        return INPUT_PRESENT if value else INPUT_ABSENT
    if isinstance(value, (dict, list, tuple, set, str)) and len(value) == 0:
        return INPUT_ABSENT
    return INPUT_PRESENT


def requirement_presence(site: SiteModel, required: RequiredInput) -> str:
    """Presence of one declared requirement, honouring its ``alternatives`` disjunction.

    Satisfied by ``key`` **or** any alternative. If every candidate is unresolvable the requirement
    is unresolvable (the guard abstains); if any resolves but none is present, it is absent.
    """
    states = [input_presence(site, p) for p in (required.key, *required.alternatives)]
    if INPUT_PRESENT in states:
        return INPUT_PRESENT
    return INPUT_ABSENT if INPUT_ABSENT in states else INPUT_UNRESOLVABLE


def evidenced_absences(site: SiteModel) -> dict[str, DerivedAssumption]:
    """The declared keys this site's register says **do not apply**, keyed by dotted field.

    The guard can see that a field reads ``None``. It cannot see *why*, and the two reasons want
    opposite sentences: *"nobody has sourced this yet, send us the rate"* against *"this component
    does not exist for this site, there is no rate to send"*. Colnbrook and Tanhouse are the live
    case, and they are the reason the sentence matters rather than a hypothetical: both are 100%
    private wire, so no DUoS unit rate applies to either and the non-commodity cost they do pay is
    already in the stack as a back-solved residual. The guard nonetheless printed
    ``tariff_stack.duos_red_rate (declared, reads NULL)`` as a data ask into their packs, which asks
    those customers for a distribution rate no supplier could ever issue them.

    `schemas.derived.DerivedAssumption` grew the `not_applicable` class to say the second thing, and
    this is what makes the class reach a reader on the gate path. Three deliberate narrownesses:

    - **Only `not_applicable`.** A `derived_input` whose value was never written back onto the
      ``SiteModel`` leaves the engine reading the same raw ``None``, so softening its ask would put
      back the silent £0 the guard exists to prevent. Colnbrook and Tanhouse each carried exactly
      that record (a valueless `derived_input` on ``tariff_stack.duos_red_rate``) until
      2026-08-17,
      when the refutation of R1 established that **no** persisted site record carried a
      `not_applicable` entry at all, so this read matched nothing on any real run. Both files now
      record all three DUoS bands in the right class
      (`tests/test_private_wire_duos_absence_on_real_sites.py` is the acceptance, on the shipped
      records rather than a fixture); no other site record does, and none should.
    - **Exact key match**, never a stem or a prefix. A record on ``tariff_stack.duos_red_rate``
      says nothing the guard may read about the amber and green bands; inferring the rest of the
      stack from one record would be a derivation, and this guard derives nothing (D44/D45).
    - **It changes no gate and no £.** A blocking null still closes the gate, the stream is still
      held out of the priced floor, and the figure is identical either way. What changes is the
      class the reader is told and the ask they are given.
    """
    return {
        rec.key: rec
        for rec in site.assumptions_requiring_confirmation
        if rec.kind == "not_applicable"
    }


def null_input_report(site: SiteModel, required: Sequence[RequiredInput]) -> tuple[str, ...]:
    """The declared inputs of one stream that read NULL on this site, as dotted keys.

    Blocking and non-blocking alike, in declaration order. Pure and market-agnostic: it reads the
    stream's own declaration and the site's own fields, and derives nothing.
    """
    return tuple(r.key for r in required if requirement_presence(site, r) == INPUT_ABSENT)


def enforce_declared_inputs(
    stream: MarketStream,
    required: Sequence[RequiredInput],
    site: SiteModel,
    gate: GateResult,
) -> GateResult:
    """**The D45 guard.** Refuse to let a gate stand OPEN on a declared input that reads NULL.

    Why this exists, in the words of the rule it implements: *"A missing ``duos_red_rate`` does not
    yield 'unknown': it prices ``DUOS_RED_BAND_AVOIDANCE`` at £0, one of only two priced-floor
    streams, and that zero then flows into P50, P90 and the DSCR indistinguishable from a measured
    result."* Until now the only thing standing between a null and that £0 was each stream's
    hand-written ``check_gates`` remembering to test the field it declared. Convention, reviewed by
    humans. This makes it an invariant, evaluated from the declaration, on every gate.

    Two effects, and the split is the D45 split:

    - **A blocking input that reads null forces the gate CLOSED.** Closed ⇒ ``EXCLUDED`` firmness ⇒
      outside ``finance.cashflows._PRICED_FIRMNESS`` ⇒ outside the priced floor, P50, P90 and the
      DSCR. The stream is *visibly* unpriced with its data ask, which is D45's own permitted
      outcome: *"refuse the £, or derive it, never both-and-neither."*
    - **A non-blocking input that reads null is reported, and nothing moves.** The declared band
      rates ``duos_amber_rate``/``duos_green_rate`` are this case: absent, they are read as zero,
      which does not zero the stream — it *widens* the modelled band spread and OVERSTATES the
      avoidance. Closing the gate for that would refuse a stream the site genuinely earns, so the
      guard states the error and its direction instead.

    **It only ever tightens.** It never opens a gate, never re-opens one a stream closed, and never
    supplies a value. It cannot: it has no derivation step, by design (D44 — values are derived
    offline, recorded, and written back onto the model; the pipeline only reads).

    **It never blocks the run** (D45's other half). Nothing raises; the run completes with a
    labelled, unpriced stream and a named ask.

    **Absence is judged on the FIELD, never on the register.** A ``DerivedAssumption`` whose value
    was not written back onto the ``SiteModel`` leaves the engine reading the raw ``None``
    (``optimisation.tariff_calendar`` reads ``tariff_stack.duos_red_rate`` directly), so honouring
    the record would produce exactly the silent £0 D45 forbids — with a gate vouching for it.

    Idempotent: re-applying it to an already-guarded gate changes nothing.
    """
    nulls = null_input_report(site, required)
    if not nulls or any(w.startswith(D45_NULL_INPUT_LABEL) for w in gate.warnings):
        return gate

    blocking = tuple(
        r.key
        for r in required
        if r.blocks_pricing and requirement_presence(site, r) == INPUT_ABSENT
    )
    advisory = tuple(k for k in nulls if k not in blocking)

    # Split each list by what the register says about it. A site with no `not_applicable` record
    # has `absences == {}`, so both `*_absent` tuples are empty, every branch below reduces to the
    # sentence it had before, and the note is byte-identical for every site in the portfolio today.
    absences = evidenced_absences(site)
    blocking_unsourced = tuple(k for k in blocking if k not in absences)
    blocking_absent = tuple(k for k in blocking if k in absences)
    advisory_unsourced = tuple(k for k in advisory if k not in absences)
    advisory_absent = tuple(k for k in advisory if k in absences)

    parts = [f"{D45_NULL_INPUT_LABEL} — {stream.value}:"]
    if blocking_unsourced:
        parts.append(
            f"{', '.join(blocking_unsourced)} read(s) as NULL on this site. A null is not a "
            "measured zero: priced through, it would report £0 for this stream and that £0 would "
            "enter P50, P90 and the DSCR indistinguishable from a measured result. The stream is "
            "therefore held OUT of the priced floor rather than priced at zero."
        )
    if blocking_absent:
        parts.append(
            f"{', '.join(blocking_absent)} read(s) as NULL, and this site's own assumption "
            "register records that as an EVIDENCED ABSENCE rather than an unfilled input: the "
            "component does not exist for this site, so there is no rate to supply and none is "
            "asked for. The stream is still held OUT of the priced floor rather than priced at "
            "zero, and the £ is identical either way, because a component this site does not face "
            "cannot be avoided and earns nothing. What the register changes is the disclosure, "
            "not the number."
        )
    if advisory_unsourced:
        parts.append(
            f"{', '.join(advisory_unsourced)} also read(s) as NULL. These do not block pricing, "
            "but an absent rate is consumed as zero downstream, which WIDENS the modelled spread "
            "against the declared bands and OVERSTATES this stream — it does not zero it."
        )
    if advisory_absent:
        parts.append(
            f"{', '.join(advisory_absent)} also read(s) as NULL and are recorded as EVIDENCED "
            "ABSENCES. They do not block pricing and there is nothing to source: a band this site "
            "does not face is correctly consumed as zero, so no spread is widened by it."
        )
    if blocking_unsourced or advisory_unsourced:
        parts.append(
            "Resolve it OFFLINE and record the value back onto the SiteModel with its provenance "
            "(D44/D45): deriving a published GB value — a DNO CDCM tariff, a NESO de-rating "
            "factor, an ENA formula — is SOURCING, not invention. Nothing is derived inside this "
            "guard, and no value is invented here. If the component genuinely does not apply to "
            "this site, record the evidenced 0.0 with the reason; the guard reads a declared zero "
            "as the fact it is."
        )
    else:
        parts.append(
            "Every null above is an EVIDENCED ABSENCE, so there is nothing to source and no rate "
            "to ask for. The ask against each key is the register's own confirmation ask, which "
            "asks the customer to CONFIRM the component does not apply. Nothing is derived inside "
            "this guard and no value is invented here: the register was written offline with its "
            "evidence (D44/D45), and this reads it."
        )
    note = " ".join(parts)

    # The gate decision is untouched by the register: a blocking null closes an OPEN gate whether
    # the absence is evidenced or unsourced. The stream is out of the priced floor either way and
    # the £ is the same, so nothing here can move a number by relabelling a record.
    if gate.gate_status != GateStatus.OPEN or not blocking:
        # Report-only: a non-open gate is already outside the floor, and a null that blocks nothing
        # must not change a price. Rebuilt rather than mutated so the GateResult validators run.
        return build_gate_result(
            stream,
            gate.gate_status,
            open_firmness=gate.firmness,
            missing_requirements=[*gate.missing_requirements, *_asks(blocking, absences)],
            unlock_condition=gate.unlock_condition,
            warnings=[*gate.warnings, note],
            consent_status=gate.consent_status,
        )

    return build_gate_result(
        stream,
        GateStatus.CLOSED,
        missing_requirements=[*gate.missing_requirements, *_asks(blocking, absences)],
        unlock_condition=gate.unlock_condition,
        warnings=[*gate.warnings, note],
        consent_status=gate.consent_status,
    )


def _asks(keys: Sequence[str], absences: dict[str, DerivedAssumption]) -> list[str]:
    """Data-ask lines for null blocking inputs — the dotted key, so the ask is unambiguous.

    A key the register records as an EVIDENCED ABSENCE gets the register's **own** confirmation
    ask instead of the generic "this reads NULL" line. That is the whole reader-facing point of the
    third class: the private-wire sites were being asked for a DUoS rate, and the record that says
    no such rate exists already carries the question that would actually settle it (supply the
    private-wire supply contract and one full invoice). Asking for the confirmation is right; asking
    for the rate is not answerable at any price.
    """
    lines = []
    for k in keys:
        record = absences.get(k)
        if record is None:
            lines.append(f"{k} (declared, reads NULL — D45: a null is not a measured zero)")
        else:
            lines.append(
                f"{k} (does not apply to this site, evidenced — D45: CONFIRM the absence, there "
                f"is no rate to supply. {record.confirmation_ask})"
            )
    return lines
