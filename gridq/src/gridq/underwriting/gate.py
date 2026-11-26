"""The evidence gate of `underwriting_basis_v1`, and the registry decorator that applies it.

`UnderwritingGate.verdict` answers one question per (stream, site): may this stream enter the priced
floor on the evidence the site references? It reads only the rule table, the basis (``as_of`` and
the admission mode), the site's ``evidence_refs`` and connection capacity, and the evidence objects
handed to the engine. Nothing else can move it, and every one of those is in ``input_hash``.

`EvidenceGatedStream` wraps each stream of a `StreamRegistry` so its ``check_gates`` returns the
**stricter** of the stream's own gate and the evidence verdict (D90). It can only tighten:
an inner CLOSED gate is never opened, and an inner OPEN gate resting on the D57 assumed-approval
register is overridden when the evidence is not there. Because `MilpEngine` reads gates through its
registry, the same verdict binds every sizing candidate and the final valuation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from gridq.assumptions import AssumptionLibrary
from gridq.contracts.evidence import CONTRACT_VERSION as IC4_VERSION
from gridq.contracts.evidence import (
    EvidenceNotFound,
    EvidenceObject,
    EvidenceRefused,
    EvidenceStatus,
    EvidenceType,
    SelfIssuedEvidenceRefused,
    StreamScope,
    SyntheticEvidenceRefused,
    Verified,
    assess,
    resolve_ref,
    scope_covers,
)
from gridq.schemas import (
    PRICED_FLOOR_FIRMNESS,
    Firmness,
    MarketStream,
    SiteModel,
    ValueStreamResult,
)
from gridq.underwriting.basis import SYNTHETIC_LABEL, UnderwritingBasis
from gridq.underwriting.rules import (
    STACKING_RULES,
    STREAM_RULES,
    Requirement,
    StackingRule,
    StreamRule,
)
from gridq.underwriting.sources import SOURCES
from gridq.value_stack.gates import connection_bounded_response_mw
from gridq.value_stack.interface import (
    DispatchConstraintDescriptor,
    DispatchSchedule,
    GateContext,
    GateResult,
    GateStatus,
    RequiredInput,
    Scenario,
    ValueStream,
)
from gridq.value_stack.registry import StreamRegistry, default_registry

__all__ = [
    "DEFAULT_NOTES_DELIMITER",
    "EvidenceGatedStream",
    "StreamVerdict",
    "UnderwritingGate",
    "demote_result",
    "tighten",
    "underwriting_registry",
]

#: Every evidence-gated stream's record leads with the basis verdict; the stream's own (default
#: basis) gate notes follow verbatim under this line, so a D57 "assumed approved" sentence is kept
#: as provenance and visibly marked as not adopted (D101). No text is matched or removed.
DEFAULT_NOTES_DELIMITER: Final = "DEFAULT-BASIS GATE NOTES, NOT ADOPTED ON underwriting_basis_v1:"

_D57_NOT_ADOPTED: Final = (
    "The D57 assumed-approval register is not adopted on underwriting_basis_v1: no consent, award, "
    "registration or stacking permission is assumed."
)

Outcome = Literal["admitted", "conditional", "unknown", "not_gated"]

_GATE_RANK: Final[dict[GateStatus, int]] = {
    GateStatus.OPEN: 0,
    GateStatus.CONDITIONAL: 1,
    GateStatus.UNKNOWN: 2,
    GateStatus.CLOSED: 3,
}


@dataclass(frozen=True)
class StreamVerdict:
    """What the evidence says about one stream at one site, as of the basis date."""

    stream: MarketStream
    outcome: Outcome
    #: What was checked and what was found, one sentence each.
    findings: tuple[str, ...]
    #: Short tokens for `GateResult.missing_requirements`.
    missing: tuple[str, ...]
    #: What would admit the stream, one clause each.
    unlock: tuple[str, ...]
    #: Questions only a counterparty or CORE can answer; never estimated.
    data_asks: tuple[str, ...]
    #: The evidence objects the admission rests on, ordered by content hash.
    relied_on: tuple[EvidenceObject, ...]

    @property
    def admits(self) -> bool:
        return self.outcome in ("admitted", "not_gated")

    @property
    def synthetic(self) -> bool:
        return any(o.record_class == "synthetic_fixture" for o in self.relied_on)

    @property
    def gate_status(self) -> GateStatus:
        if self.outcome == "unknown":
            return GateStatus.UNKNOWN
        if self.outcome == "conditional":
            return GateStatus.CONDITIONAL
        return GateStatus.OPEN

    @property
    def unlock_condition(self) -> str | None:
        if not self.unlock:
            return None
        return "underwriting_basis_v1: " + "; ".join(self.unlock)

    def lead(self) -> str:
        """The sentence that leads this stream's record on the underwriting basis."""
        name = self.stream.value
        if self.outcome == "not_gated":
            return (
                f"UNDERWRITING BASIS (underwriting_basis_v1): {name} is bill-side and NOT "
                "evidence-gated; it is priced as the default gate prices it."
            )
        if self.outcome == "admitted":
            held = ", ".join(_describe(o) for o in self.relied_on) or "no object"
            text = (
                f"UNDERWRITING BASIS (underwriting_basis_v1): {name} is ADMITTED on evidence: "
                f"{held}. {_D57_NOT_ADOPTED}"
            )
            return f"{SYNTHETIC_LABEL}: {text}" if self.synthetic else text
        held_out = (
            "EXCLUDED AT GBP 0"
            if self.outcome == "unknown"
            else "HELD OUT OF THE PRICED FLOOR AS CONDITIONAL UPSIDE"
        )
        return (
            f"UNDERWRITING BASIS (underwriting_basis_v1): {name} is {held_out}: "
            f"{'; '.join(self.unlock) or 'see findings'}. {_D57_NOT_ADOPTED}"
        )

    def notes(self) -> list[str]:
        """The lead sentence, then each finding and data ask (labelled)."""
        return [
            self.lead(),
            *(f"UNDERWRITING EVIDENCE CHECK: {f}" for f in self.findings),
            *self.data_asks,
        ]


def _short(h: str) -> str:
    return h.removeprefix("sha256:")[:12]


def _describe(obj: EvidenceObject) -> str:
    role = obj.issuer.role.value
    v = obj.verification
    verified = (
        f"verified by {v.verifier} ({v.verifier_role.value}, {v.method.value}, {v.verified_on})"
        if isinstance(v, Verified)
        else "UNVERIFIED"
    )
    tag = " [SYNTHETIC FIXTURE]" if obj.record_class == "synthetic_fixture" else ""
    return (
        f"{obj.evidence_type.value} {_short(obj.content_hash())} issued by {obj.issuer.name} "
        f"({role}), {verified}{tag}"
    )


def _cite(citation: str, *, quote: bool = False) -> str:
    src = SOURCES.get(citation)
    if src is None:
        return citation
    head = f"{src.publisher}, {src.title} ({src.version}), {src.locator}"
    return f'{head}: "{src.quote}"' if quote else head


def _req_label(req: Requirement) -> str:
    kind = req.evidence_type.value if req.evidence_type is not None else "no IC-4 type"
    return f"{req.purpose.value} ({kind}; {req.row_basis.value}: {_cite(req.citation)})"


class UnderwritingGate:
    """The evidence verdict for every stream, from one basis and one in-hand evidence set.

    ``evidence`` is the set of IC-4 objects the caller holds. Only those the site names by content
    hash in ``SiteModel.evidence_refs`` are ever assessed (D96), so an object outside the
    run's identity cannot move a number; a referenced object that is not in hand is ABSENT.
    """

    def __init__(self, basis: UnderwritingBasis, evidence: Iterable[EvidenceObject] = ()) -> None:
        self.basis = basis
        by_hash: dict[str, EvidenceObject] = {}
        for obj in evidence:
            if type(obj) is not EvidenceObject:
                raise TypeError(
                    f"only IC-4 EvidenceObjects can be held as evidence, not {type(obj).__name__}"
                )
            by_hash[obj.content_hash()] = obj
        self._by_hash = by_hash
        self._cache: dict[tuple[object, ...], StreamVerdict] = {}

    # ------------------------------------------------------------------ what the site references

    def held(
        self, site: SiteModel
    ) -> tuple[tuple[EvidenceObject, ...], tuple[tuple[str, str], ...]]:
        """The referenced objects that may be assessed, and ``(supports, reason)`` per set-aside."""
        admissible: dict[str, EvidenceObject] = {}
        set_aside: list[tuple[str, str]] = []
        for link in site.evidence_refs or ():
            ref = link.evidence
            h = ref.evidence_hash
            candidate = self._by_hash.get(h)
            try:
                obj = resolve_ref(
                    ref,
                    () if candidate is None else (candidate,),
                    include_synthetic=self.basis.admits_synthetic,
                )
            except EvidenceNotFound:
                reason = "is referenced by the site but not in hand, so it is treated as ABSENT"
            except SyntheticEvidenceRefused:
                reason = (
                    "is a synthetic schema-test fixture, refused on an evidence_admission "
                    "'registered_only' basis"
                )
            except SelfIssuedEvidenceRefused:
                reason = "is self-issued by Neura, and third-party evidence is required"
            except EvidenceRefused as exc:
                reason = f"was refused: {exc}"
            else:
                admissible[h] = obj
                continue
            set_aside.append((link.supports, f"evidence {_short(h)} ({link.supports}) {reason}."))
        return tuple(admissible[h] for h in sorted(admissible)), tuple(set_aside)

    # ------------------------------------------------------------------ the verdict

    def verdict(self, stream: MarketStream, site: SiteModel) -> StreamVerdict:
        """The evidence verdict for ``stream`` at ``site``. Deterministic; cached per input."""
        return self._verdict(stream, site, depth=0)

    def _verdict(self, stream: MarketStream, site: SiteModel, *, depth: int) -> StreamVerdict:
        if depth > len(STACKING_RULES):
            raise RuntimeError("stacking rules form a cycle; the rule table is malformed")
        bound = connection_bounded_response_mw(site)
        key = (
            stream,
            site.site_id,
            bound,
            tuple(
                (link.supports, link.evidence.evidence_hash, link.evidence.contract_version)
                for link in site.evidence_refs or ()
            ),
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        rule = STREAM_RULES.get(stream)
        if rule is None:
            result = StreamVerdict(
                stream=stream,
                outcome="unknown",
                findings=(
                    f"{stream.value} has no row in the underwriting rule table, and a stream the "
                    "table does not name is never silently open.",
                ),
                missing=(f"{stream.value}: no underwriting rule",),
                unlock=(f"add a {stream.value} row to the rule table (a new table version)",),
                data_asks=(),
                relied_on=(),
            )
        elif not rule.requirements:
            result = StreamVerdict(stream, "not_gated", (rule.note,), (), (), (), ())
        else:
            result = self._assess_row(rule, site, bound, depth=depth)
        self._cache[key] = result
        return result

    def _assess_row(
        self, rule: StreamRule, site: SiteModel, bound: float | None, *, depth: int
    ) -> StreamVerdict:
        stream = rule.stream
        objects, set_aside = self.held(site)
        scope = StreamScope(kind="stream", stream=stream.value, site_id=site.site_id)
        as_of = self.basis.as_of
        findings: list[str] = [
            reason
            for supports, reason in set_aside
            if supports == stream.value or supports not in _STREAM_VALUES
        ]
        missing: list[str] = []
        unlock: list[str] = []
        asks: list[str] = []
        relied: dict[str, EvidenceObject] = {}
        unknown = False

        # 1. Each required evidence object (gates, consents, route to market, export permission).
        for req in rule.requirements:
            if req.evidence_type is None:
                unknown = True
                findings.append(
                    f"{_req_label(req)}: IC-4 {IC4_VERSION} has no evidence type for this "
                    "document, so it cannot be evidenced (gap G6)."
                )
                missing.append(f"{req.purpose.value}: no IC-4 evidence type (G6)")
                unlock.append(f"IC-4 gains a type for {stream.value} {req.purpose.value}")
                if req.data_ask:
                    asks.append(req.data_ask)
                continue
            met, used, clause, ask = self._requirement(objects, req, scope)
            if met:
                relied.update((o.content_hash(), o) for o in used)
                findings.append(
                    f"{_req_label(req)}: held, VALID on {as_of}, verified: "
                    + ", ".join(_describe(o) for o in used)
                    + "."
                )
            else:
                findings.append(f"{_req_label(req)}: {clause}.")
                missing.append(f"{req.purpose.value}: {req.evidence_type.value}")
                unlock.append(clause)
                if ask:
                    asks.append(ask)

        # 2. The market's minimum unit size (D94).
        size = rule.size
        if size is not None:
            if size.min_mw is None:
                findings.append(
                    f"size: no published minimum unit size is armed for {stream.value}; size "
                    "eligibility is UNASSERTED and does not block (the counterparty's executed "
                    "contract evidences acceptance of the unit)."
                )
                if size.data_ask:
                    asks.append(size.data_ask)
            elif bound is None:
                clause = (
                    "record the site's MIC or MEC: with no connection capacity the "
                    f"{size.min_mw:g} MW minimum cannot be checked"
                )
                findings.append(f"size: {clause}.")
                missing.append("size: connection capacity")
                unlock.append(clause)
            else:
                unit = int(bound * 10) / 10 if size.rounding == "dfs_round_down" else bound
                assert size.source_id is not None
                if unit >= size.min_mw:
                    findings.append(
                        f"size: connection-bounded {bound:,.3f} MW meets the {size.min_mw:g} MW "
                        f"minimum ({_cite(size.source_id)})."
                    )
                else:
                    route = self._aggregation(objects, scope)
                    if route:
                        relied.update((o.content_hash(), o) for o in route)
                        findings.append(
                            f"size: connection-bounded {bound:,.3f} MW is below the "
                            f"{size.min_mw:g} MW minimum ({_cite(size.source_id, quote=True)}), "
                            f"and is admitted through {size.aggregation_route}, evidenced by "
                            + ", ".join(_describe(o) for o in route)
                            + "."
                        )
                    else:
                        clause = (
                            f"the connection-bounded {bound:,.3f} MW is below the "
                            f"{size.min_mw:g} MW minimum, so provide a VALID, verified "
                            f"aggregator_agreement scoped to {stream.value} itself (a whole-site "
                            f"agreement is not a route) evidencing {size.aggregation_route}"
                        )
                        findings.append(
                            f"size: {clause} ({_cite(size.source_id, quote=True)}; route: "
                            f"{_cite(size.aggregation_source_id or size.source_id)})."
                        )
                        missing.append("size: below the market minimum, no aggregation route")
                        unlock.append(clause)
                        findings.extend(self._contradictions(objects, rule, scope))

        # 3. Published stacking rules that bind this stream (D95).
        for stack in STACKING_RULES:
            if stack.subject is not stream:
                continue
            fires, why = self._fires(stack, objects, site, depth=depth)
            if not fires:
                continue
            cited = "; ".join(_cite(s) for s in stack.source_ids)
            if stack.demotes:
                clause = (
                    f"stacking rule {stack.rule_id}: {stack.statement} ({cited}). "
                    f"{stream.value} is held out because {why}"
                )
                findings.append(f"{clause}.")
                missing.append(f"stacking: {stack.rule_id}")
                unlock.append(f"resolve stacking rule {stack.rule_id} with {stack.partner.value}")
                if stack.data_ask:
                    asks.append(stack.data_ask)
            else:
                findings.append(
                    f"stacking rule {stack.rule_id} ({stack.status}): {stack.statement} ({cited}); "
                    f"applies because {why}."
                )

        outcome: Outcome = "unknown" if unknown else ("conditional" if unlock else "admitted")
        return StreamVerdict(
            stream=stream,
            outcome=outcome,
            findings=tuple(findings),
            missing=tuple(missing),
            unlock=tuple(unlock),
            data_asks=tuple(dict.fromkeys(asks)),
            relied_on=tuple(relied[h] for h in sorted(relied)) if outcome == "admitted" else (),
        )

    def _requirement(
        self, objects: tuple[EvidenceObject, ...], req: Requirement, scope: StreamScope
    ) -> tuple[bool, tuple[EvidenceObject, ...], str, str | None]:
        """``(met, objects relied on, unlock clause, data ask)`` for one required object."""
        assert req.evidence_type is not None
        t = req.evidence_type
        as_of = self.basis.as_of
        check = assess(
            objects,
            evidence_type=t,
            scope=scope,
            as_of=as_of,
            include_synthetic=self.basis.admits_synthetic,
        )
        if check.status is EvidenceStatus.VALID and check.best_is_verified:
            used = tuple(
                o
                for o in objects
                if o.evidence_type is t
                and scope_covers(o.scope, scope)
                and o.status_at(as_of) is EvidenceStatus.VALID
                and o.is_verified
            )
            return True, used, "", None
        where = f"{scope.stream} at site {scope.site_id}"
        if check.status is EvidenceStatus.VALID:
            return (
                False,
                (),
                f"verify the {t.value} against its issuer (held, VALID, unverified)",
                None,
            )
        if check.status is EvidenceStatus.VALIDITY_UNKNOWN:
            return (
                False,
                (),
                f"state the {t.value}'s effective-from and expiry dates (held, validity unknown)",
                f"DATA ASK: the effective-from and expiry dates of the {t.value} held for {where}; "
                "an unknown date is not a satisfied date.",
            )
        if check.status is EvidenceStatus.NOT_YET_EFFECTIVE:
            return False, (), f"the {t.value} held is not yet effective on {as_of}", None
        if check.status is EvidenceStatus.EXPIRED:
            return False, (), f"renew the {t.value} (held, expired on {as_of})", None
        return False, (), f"provide an IC-4 {t.value} covering {where} (none held)", None

    def _aggregation(
        self, objects: tuple[EvidenceObject, ...], scope: StreamScope
    ) -> tuple[EvidenceObject, ...]:
        """VALID, verified aggregator agreements scoped to the stream itself (never the site)."""
        return tuple(
            o
            for o in objects
            if o.evidence_type is EvidenceType.AGGREGATOR_AGREEMENT
            and isinstance(o.scope, StreamScope)
            and scope_covers(o.scope, scope)
            and o.status_at(self.basis.as_of) is EvidenceStatus.VALID
            and o.is_verified
        )

    def _contradictions(
        self, objects: tuple[EvidenceObject, ...], rule: StreamRule, scope: StreamScope
    ) -> list[str]:
        out: list[str] = []
        for req in rule.requirements:
            if req.purpose.value != "registration" or req.evidence_type is None:
                continue
            check = assess(
                objects,
                evidence_type=req.evidence_type,
                scope=scope,
                as_of=self.basis.as_of,
                include_synthetic=self.basis.admits_synthetic,
            )
            if check.status is EvidenceStatus.VALID:
                out.append(
                    f"CONTRADICTION: a VALID {req.evidence_type.value} is held for {scope.stream} "
                    "although the connection-bounded MW is below the market minimum. A "
                    "registration does not substitute for the size check; check which unit the "
                    "registration covers."
                )
        return out

    def _fires(
        self,
        stack: StackingRule,
        objects: tuple[EvidenceObject, ...],
        site: SiteModel,
        *,
        depth: int,
    ) -> tuple[bool, str]:
        partner = stack.partner.value
        if stack.trigger == "partner_registration_held":
            assert stack.trigger_evidence is not None
            check = assess(
                objects,
                evidence_type=stack.trigger_evidence,
                scope=StreamScope(kind="stream", stream=partner, site_id=site.site_id),
                as_of=self.basis.as_of,
                include_synthetic=self.basis.admits_synthetic,
            )
            return (
                check.status is EvidenceStatus.VALID,
                f"a VALID {stack.trigger_evidence.value} is held for {partner} at this site",
            )
        partner_verdict = self._verdict(stack.partner, site, depth=depth + 1)
        return (
            partner_verdict.outcome == "admitted",
            f"{partner} is admitted to the priced floor on evidence",
        )


_STREAM_VALUES: Final = frozenset(m.value for m in MarketStream)


# ---------------------------------------------------------------------- combining with a gate


def tighten(inner: GateResult, verdict: StreamVerdict) -> GateResult:
    """The stricter of the stream's own gate and the evidence verdict. Never loosens."""
    if verdict.outcome == "not_gated":
        return inner.model_copy(update={"warnings": [*inner.warnings, verdict.lead()]})
    lead = verdict.notes()
    block = [DEFAULT_NOTES_DELIMITER, *inner.warnings] if inner.warnings else []
    warnings = [*lead, *block]
    missing = [*inner.missing_requirements, *verdict.missing]
    if _GATE_RANK[verdict.gate_status] <= _GATE_RANK[inner.gate_status]:
        # The inner gate is at least as strict: it stands. A shared CONDITIONAL names both unlocks.
        unlock = inner.unlock_condition
        if verdict.unlock_condition and inner.gate_status is GateStatus.CONDITIONAL:
            unlock = "; ".join(u for u in (inner.unlock_condition, verdict.unlock_condition) if u)
        return GateResult.model_validate(
            {
                **dict(inner),
                "missing_requirements": missing,
                "unlock_condition": unlock,
                "warnings": warnings,
            }
        )
    status = verdict.gate_status
    return GateResult(
        stream=inner.stream,
        gate_status=status,
        can_be_priced_now=False,
        firmness=Firmness.CONDITIONAL if status is GateStatus.CONDITIONAL else Firmness.EXCLUDED,
        missing_requirements=missing,
        unlock_condition="; ".join(
            u for u in (verdict.unlock_condition, inner.unlock_condition) if u
        )
        or None,
        warnings=warnings,
        consent_status=inner.consent_status,
    )


def demote_result(v: ValueStreamResult, verdict: StreamVerdict) -> ValueStreamResult:
    """Hold a priced stream out of the floor per ``verdict`` (the engine-side guard).

    A conditional verdict keeps the GBP as upside; an unknown one is excluded at GBP 0
    (D91).
    A stream the verdict admits, or one already held out, is returned unchanged.
    """
    if verdict.admits:
        return v
    if verdict.outcome == "conditional" and v.firmness not in PRICED_FLOOR_FIRMNESS:
        return v
    if verdict.outcome == "unknown" and v.firmness is Firmness.EXCLUDED:
        return v
    unknown = verdict.outcome == "unknown"
    return ValueStreamResult.model_validate(
        {
            **dict(v),
            "firmness": Firmness.EXCLUDED if unknown else Firmness.CONDITIONAL,
            "gate_status": verdict.gate_status.value,
            "annual_revenue_gbp": 0.0 if unknown else v.annual_revenue_gbp,
            "annual_cost_gbp": 0.0 if unknown else v.annual_cost_gbp,
            "unlock_condition": verdict.unlock_condition,
            "warnings": [
                *verdict.notes(),
                *([DEFAULT_NOTES_DELIMITER, *v.warnings] if v.warnings else []),
            ],
        }
    )


# ---------------------------------------------------------------------- the registry decorator


class EvidenceGatedStream:
    """A `ValueStream` whose gate is the stricter of its own and the evidence verdict."""

    def __init__(self, inner: ValueStream, gate: UnderwritingGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def inner(self) -> ValueStream:
        return self._inner

    @property
    def stream_id(self) -> MarketStream:
        return self._inner.stream_id

    @property
    def display_name(self) -> str:
        return self._inner.display_name

    def required_inputs(self) -> list[RequiredInput]:
        return self._inner.required_inputs()

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        own = self._inner.check_gates(site_model, assumptions, context)
        return tighten(own, self._gate.verdict(self.stream_id, site_model))

    def value_function(
        self,
        dispatch_schedule: DispatchSchedule,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None = None,
    ) -> ValueStreamResult:
        own = self._inner.value_function(
            dispatch_schedule, site_model, assumptions, scenario, context
        )
        return demote_result(own, self._gate.verdict(self.stream_id, site_model))

    def constraints(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        *,
        candidate_power_kw: float | None = None,
    ) -> list[DispatchConstraintDescriptor]:
        return self._inner.constraints(
            site_model, assumptions, candidate_power_kw=candidate_power_kw
        )


def underwriting_registry(
    gate: UnderwritingGate, base: StreamRegistry | None = None
) -> StreamRegistry:
    """``base`` (the default GB stack when ``None``) with every stream evidence-gated."""
    reg = base if base is not None else default_registry()
    return StreamRegistry(EvidenceGatedStream(s, gate) for s in reg.all())
