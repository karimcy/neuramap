"""`site_eligibility_engine` (WP-UW-02, spec A6-02): per-site eligibility for seven streams.

Design: D133 to D142 in ``docs/decisions.md`` (Fable 5.2, under the owner's
delegation).

`build_eligibility(dossier, as_of=..., atlas_root=..., evidence_admission=...)` lists every site of
an OPS-01 dossier against every stream of the rule table (`gridq.underwriting.eligibility_rules`)
as eligible, ineligible or undetermined, with the reason.

**How the register and the segment rule are obtained (acceptance criterion 5).** Once per run the
engine starts a fresh interpreter (``sys.executable -I -B``: isolated, and writing no bytecode into
Atlas) on `eligibility_bridge.py`. That process imports the Atlas loader by file path, calls
``load_rule(..., as_of=<the run's as_of>)``, evaluates each site with the Atlas evaluator, and asks
``require_verified(row, as_of=<the run's as_of>)`` for every row the table cites. A new process
re-reads the register file by construction: nothing cached by an earlier run can survive. A missing
Atlas tree, an import error, a refusal, any other exception, a non-zero exit, a timeout, unparsable
output, a reported ``as_of`` other than the run's, or a file changed during the read: each one
makes every check that depends on it undetermined, with the reason. None is ever a pass.

**Provisional is never exempt (criterion 4).** While the loaded rule's status is not ``final`` the
engine reports any ``eligible`` from the evaluator as undetermined, whatever the evaluator said.

**Adopted loaders (D134).** Under ``registered_only`` the engine relies on the Atlas files
only when the loader and the register module match a pair in ``ADOPTED_LOADERS``.

Run from the command line: ``python -m gridq.underwriting.eligibility --as-of YYYY-MM-DD``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from gridq.contracts._base import NotApplicable, Unknown, content_hash
from gridq.contracts.connection import ConnectionArchetype, CurtailmentLimit
from gridq.contracts.evidence import EvidenceObject, EvidenceStatus, SiteScope
from gridq.ops.dossier_model import (
    AskHolder,
    CandidateSiteDossier,
    DossierAsk,
    Evidenced,
    Ic4Evidence,
    SiteDossier,
    UnknownField,
)
from gridq.underwriting.basis import SYNTHETIC_LABEL
from gridq.underwriting.eligibility_model import (
    CONSUMPTION_STATEMENT,
    ELIGIBILITY_SCHEMA,
    ELIGIBILITY_SCHEMA_VERSION,
    AtlasFile,
    CheckResult,
    ControlledAssetsDeclaration,
    EligibilityFacts,
    EvidenceAdmission,
    FactInput,
    FactState,
    LoaderRecord,
    OwnerAction,
    RowVerdict,
    SiteEligibility,
    SiteEligibilityTable,
    StreamResult,
    ThresholdUse,
    combine,
)
from gridq.underwriting.eligibility_rules import (
    ADOPTED_LOADERS,
    BRIDGE_TIMEOUT_S,
    CHECKS,
    FACT_ASKS,
    FACT_EVIDENCE,
    OWNER_ACTIONS,
    RULE_TABLE_HASH,
    RULE_TABLE_VERSION,
    STREAM_QUESTIONS,
    STREAMS,
    CheckRule,
    FactField,
    cited_rows,
)

__all__ = [
    "ATLAS_ROOT_ENV",
    "BRIDGE",
    "EligibilityInputError",
    "build_eligibility",
    "main",
    "resolve_atlas_root",
    "run_bridge",
]

BRIDGE: Final = Path(__file__).with_name("eligibility_bridge.py")
ATLAS_ROOT_ENV: Final = "GRIDQ_ATLAS_ROOT"
_ATLAS_PLACEHOLDER: Final = "<atlas>"


class EligibilityInputError(ValueError):
    """The inputs do not describe one run: dates differ, or a fixture reached a registered run."""


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------------
# The bridge: one fresh process per run
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeOutcome:
    """The bridge's JSON document, or why there is none."""

    document: dict[str, Any] | None
    failure: str | None


def run_bridge(
    atlas_root: Path | None,
    *,
    as_of: date,
    rows: Sequence[str],
    sites: Sequence[Mapping[str, Any]],
    timeout_s: float | None = None,
) -> BridgeOutcome:
    """Run `eligibility_bridge.py` in a new interpreter. Never raises; a failure is a reason."""
    timeout_s = BRIDGE_TIMEOUT_S if timeout_s is None else timeout_s
    if atlas_root is None:
        return BridgeOutcome(None, "no Atlas root was given, so the register was not read")
    if not (atlas_root / "roadmap" / "governance" / "regulatory").is_dir():
        return BridgeOutcome(
            None, "the Atlas root has no roadmap/governance/regulatory tree, so nothing was read"
        )
    request = {
        "atlas_root": str(atlas_root),
        "as_of": as_of.isoformat(),
        "rows": list(rows),
        "sites": list(sites),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(BRIDGE)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BridgeOutcome(None, f"the Atlas bridge did not finish within {timeout_s} s")
    except OSError as exc:
        return BridgeOutcome(None, f"the Atlas bridge could not start: {type(exc).__name__}")
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-1:]) or "no message"
        return BridgeOutcome(None, f"the Atlas bridge exited with status {proc.returncode}: {tail}")
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return BridgeOutcome(None, f"the Atlas bridge's output is not JSON: {exc.msg}")
    if not isinstance(doc, dict) or doc.get("bridge") != "gridq.underwriting.eligibility_bridge":
        return BridgeOutcome(None, "the Atlas bridge's output is not a bridge document")
    return BridgeOutcome(doc, None)


def _clean(text: Any, atlas_root: Path | None) -> str:
    """One line, with the machine's Atlas path replaced (paths stay out of the hash)."""
    s = " ".join(str(text).split())
    if atlas_root is not None:
        s = s.replace(str(atlas_root), _ATLAS_PLACEHOLDER)
    return s or "no message"


# --------------------------------------------------------------------------------------------
# Loader record and row verdicts
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Run:
    loader: LoaderRecord
    rows: dict[str, RowVerdict]
    row_views: dict[str, dict[str, Any]]
    evaluations: dict[str, dict[str, Any]]
    #: Why the Atlas results cannot be relied on at all (bridge failure, not adopted ...).
    blocked: str | None
    #: Why the segment rule cannot be relied on (also set when ``blocked`` is).
    rule_blocked: str | None


def _adoption(files: Mapping[str, str], admission: EvidenceAdmission) -> str:
    pair = (files.get("loader"), files.get("register_module"))
    if any(pair == (a.loader_sha256, a.register_module_sha256) for a in ADOPTED_LOADERS):
        return "adopted"
    return "not_adopted_synthetic_mode" if admission != "registered_only" else "not_adopted"


def _interpret(
    outcome: BridgeOutcome,
    *,
    as_of: date,
    admission: EvidenceAdmission,
    atlas_root: Path | None,
) -> _Run:
    rows = cited_rows()
    if outcome.document is None:
        why = _clean(outcome.failure, atlas_root)
        record = LoaderRecord(
            bridge="fresh_subprocess",
            outcome="not_run" if atlas_root is None else "error",
            reason=why,
            rule_id=Unknown(kind="unknown", reason=why),
            rule_version=Unknown(kind="unknown", reason=why),
            rule_status=Unknown(kind="unknown", reason=why),
            reported_as_of=Unknown(kind="unknown", reason=why),
            rests_on=(),
            adoption="not_run",
            files=(),
            files_changed_during_read=(),
        )
        not_read = {r: RowVerdict(row=r, verdict="not_read", message=why) for r in rows}
        return _Run(record, not_read, {}, {}, why, why)

    doc = outcome.document
    files = {str(k): str(v) for k, v in (doc.get("files") or {}).items()}
    changed = tuple(sorted(str(x) for x in doc.get("files_changed_during_read") or []))
    adoption = _adoption(files, admission)
    blocked: str | None = None
    if changed:
        blocked = f"Atlas files changed while they were read ({', '.join(changed)}); re-run"
    elif adoption == "not_adopted":
        blocked = (
            "the Atlas loader and register module are not an adopted pair "
            "(gridq.underwriting.eligibility_rules.ADOPTED_LOADERS), so a registered run does not "
            "rely on them"
        )

    lr = doc.get("load_rule") or {}
    imp = doc.get("loader_import") or {}
    rule_blocked = blocked
    if lr.get("outcome") == "loaded":
        reported = date.fromisoformat(str(lr["reported_as_of"]))
        status_text = str(lr.get("status"))
        rec_outcome = "loaded"
        reason = (
            f"load_rule loaded {lr.get('rule_id')} {lr.get('version')} ({status_text}) as of "
            f"{reported.isoformat()}"
        )
        if reported != as_of:
            rule_blocked = rule_blocked or (
                f"load_rule reported as_of {reported.isoformat()}, "
                f"not the run's {as_of.isoformat()}"
            )
        rule_fields: dict[str, Any] = {
            "rule_id": str(lr.get("rule_id")),
            "rule_version": str(lr.get("version")),
            "rule_status": status_text,
            "reported_as_of": reported,
            "rests_on": tuple(str(r) for r in lr.get("rests_on") or []),
        }
    else:
        err = lr.get("error") or imp.get("error") or {}
        cls = str(err.get("class") or "")
        msg = _clean(err.get("message") or lr.get("why") or "the loader did not run", atlas_root)
        rec_outcome = "refused" if cls == "RuleRefused" else "error"
        reason = f"load_rule as of {as_of.isoformat()}: {cls or 'not called'}: {msg}"
        rule_blocked = rule_blocked or reason
        unknown = Unknown(kind="unknown", reason=reason)
        rule_fields = {
            "rule_id": unknown,
            "rule_version": unknown,
            "rule_status": unknown,
            "reported_as_of": unknown,
            "rests_on": (),
        }

    record = LoaderRecord(
        bridge="fresh_subprocess",
        outcome=rec_outcome,  # type: ignore[arg-type]
        reason=_clean(reason, atlas_root),
        adoption=adoption,  # type: ignore[arg-type]
        files=tuple(
            AtlasFile(name=k, sha256=v)  # type: ignore[arg-type]
            for k, v in sorted(files.items())
        ),
        files_changed_during_read=changed,
        **rule_fields,
    )

    verdicts: dict[str, RowVerdict] = {}
    views: dict[str, dict[str, Any]] = {}
    raw_rows = doc.get("rows") or {}
    for r in rows:
        v = raw_rows.get(r) or {"verdict": "not_read", "message": "the bridge did not report it"}
        verdict, message = str(v.get("verdict")), _clean(v.get("message"), atlas_root)
        if blocked is not None:
            verdict, message = "not_relied_on", f"{blocked} (the gate said {verdict}: {message})"
        verdicts[r] = RowVerdict(row=r, verdict=verdict, message=message)
        if verdict == "passes":
            views[r] = v
    evaluations = {str(k): v for k, v in (doc.get("evaluations") or {}).items()}
    return _Run(record, verdicts, views, evaluations, blocked, rule_blocked)


# --------------------------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Fact:
    state: str  # evidenced | declared | unknown
    value: Any
    evidence: tuple[str, ...]
    note: str
    asks: tuple[DossierAsk, ...]


def _fact_ask(field: FactField, site_id: str) -> DossierAsk:
    request, holder, closes = FACT_ASKS[field]
    return DossierAsk.make(
        request=request, holder=AskHolder(holder), scope=f"site:{site_id}", closes_with=closes
    )


def _admit_fact(
    fact: FactInput,
    *,
    site_id: str,
    registry: Mapping[str, EvidenceObject],
    as_of: date,
    admission: EvidenceAdmission,
) -> tuple[bool, str]:
    """Whether every cited document meets the fact's bar (the dossier's: D107), and why not."""
    synthetic_ok = admission == "synthetic_fixtures_test_only" and site_id.startswith("SYNTHETIC-")
    for ref in fact.evidence:
        obj = registry.get(ref.evidence_hash)
        short = ref.evidence_hash.removeprefix("sha256:")[:12]
        if obj is None:
            return False, f"evidence {short} is not in the registry"
        if obj.record_class == "synthetic_fixture" and not synthetic_ok:
            return False, f"evidence {short} is a synthetic fixture; this run admits none"
        if obj.is_self_issued:
            return False, f"evidence {short} is self-issued; a site fact needs a third party"
        allowed = FACT_EVIDENCE[fact.field].get(obj.evidence_type)
        type_ok = allowed is not None and obj.issuer.role in allowed
        if not type_ok and not (synthetic_ok and not FACT_EVIDENCE[fact.field]):
            return False, (
                f"a {obj.evidence_type.value} from a {obj.issuer.role.value} does not evidence "
                f"{fact.field.value}"
            )
        if not (isinstance(obj.scope, SiteScope) and obj.scope.site_id == site_id):
            return False, f"evidence {short} is not scoped to this site"
        if obj.status_at(as_of) is not EvidenceStatus.VALID:
            return False, f"evidence {short} is {obj.status_at(as_of).value} as of {as_of}"
        if not obj.is_verified:
            return False, f"evidence {short} has not been verified with its issuer"
    return True, "admitted"


def _facts_for(
    site_id: str,
    facts: EligibilityFacts | None,
    *,
    registry: Mapping[str, EvidenceObject],
    as_of: date,
    admission: EvidenceAdmission,
) -> dict[FactField, _Fact]:
    given = {f.field: f for f in facts.facts} if facts is not None else {}
    out: dict[FactField, _Fact] = {}
    for field in FactField:
        ask = (_fact_ask(field, site_id),)
        f = given.get(field)
        if f is None:
            out[field] = _Fact("unknown", None, (), f"{field.value} was not supplied", ask)
        elif f.basis == "declaration":
            out[field] = _Fact(
                "declared", f.value, (), f"declared by {f.declared_by}, not evidenced", ask
            )
        else:
            ok, why = _admit_fact(
                f, site_id=site_id, registry=registry, as_of=as_of, admission=admission
            )
            if ok:
                out[field] = _Fact(
                    "evidenced",
                    f.value,
                    tuple(sorted(r.evidence_hash for r in f.evidence)),
                    "admitted",
                    (),
                )
            else:
                out[field] = _Fact("unknown", f.value, (), f"not admitted: {why}", ask)
    return out


def _dossier_state(site: SiteDossier, name: str) -> tuple[Any, FactState, tuple[DossierAsk, ...]]:
    f = getattr(site, name)
    if isinstance(f, Evidenced):
        hashes = tuple(
            sorted({i.ref.evidence_hash for i in f.evidence if isinstance(i, Ic4Evidence)})
        )
        return (
            f.value,
            FactState(
                source="dossier",
                field=name,
                state="evidenced",
                evidence=hashes,
                note="evidenced in the OPS-01 dossier",
            ),
            (),
        )
    assert isinstance(f, UnknownField)
    return (
        None,
        FactState(
            source="dossier", field=name, state="unknown", evidence=(), note=f"unknown: {f.reason}"
        ),
        tuple(f.asks),
    )


def _fact_state(field: FactField, fact: _Fact) -> FactState:
    return FactState(
        source="facts",
        field=field.value,
        state=fact.state,  # type: ignore[arg-type]
        evidence=fact.evidence,  # type: ignore[arg-type]
        note=fact.note,
    )


# --------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------


@dataclass
class _Draft:
    status: str
    reason: str
    read: list[FactState]
    missing: list[str]
    thresholds: list[ThresholdUse]
    asks: list[DossierAsk]
    classification: str | None = None
    unblocked_extra: tuple[str, ...] = ()


def _threshold(run: _Run, ref: str) -> tuple[ThresholdUse | None, str]:
    """A threshold from a gate-passed row, verified against the row's own quotes (R3)."""
    row, name = ref.split(":", 1)
    view = run.row_views.get(row)
    if view is None:
        return None, f"{row} did not pass the register gate"
    table = view.get("thresholds")
    if not isinstance(table, dict) or name not in table:
        return None, f"{row} carries no machine-readable threshold {name!r}"
    t = table[name]
    try:
        value = Decimal(str(t["value"]))
        unit, comparator, quote = str(t["unit"]), str(t["comparator"]), str(t["quote"])
        idx = int(t["evidence_index"])
        quotes = [str(q) for q in view["evidence_quotes"][idx]]
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
        return None, f"{row} threshold {name!r} is malformed"
    if unit not in ("MW", "kW") or comparator not in ("gt", "ge"):
        return None, f"{row} threshold {name!r} has an unknown unit or comparator"
    if not any(quote in q for q in quotes):
        return None, f"{row} threshold {name!r}: its quote is not in the row's evidence quotes"
    figure = format(value.normalize(), "f")
    if figure not in quote or unit not in quote:
        return None, f"{row} threshold {name!r}: its quote does not carry {figure} {unit}"
    return (
        ThresholdUse(
            row=row,
            name=name,
            value=figure,
            unit=unit,  # type: ignore[arg-type]
            comparator=comparator,  # type: ignore[arg-type]
            quote=quote,
        ),
        "",
    )


def _exceeds(x_kw: Decimal, t: ThresholdUse) -> bool:
    limit_kw = Decimal(t.value) * (Decimal(1000) if t.unit == "MW" else Decimal(1))
    return x_kw > limit_kw if t.comparator == "gt" else x_kw >= limit_kw


def _check_met_route(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    value, state, asks = _dossier_state(site, "metering_class")
    d = _Draft("undetermined", "", [state], [], [], list(asks))
    if value is None:
        d.missing.append("dossier:metering_class")
        d.reason = "The settlement metering class is unknown in the OPS-01 dossier."
        return d
    row = "REG-04" if value.settlement == "sva" else "REG-38"
    route = "sva_secondary_bmu" if value.settlement == "sva" else "cva_primary_bmu_via_supplier"
    v = run.rows[row]
    if v.verdict != "passes":
        d.reason = f"The {value.settlement.upper()} route rests on {row}: {v.verdict}: {v.message}"
        d.unblocked_extra = (row,)
        return d
    d.status, d.classification = "eligible", route
    d.reason = f"{value.settlement.upper()} settlement: route {route} ({row} passes the gate)."
    return d


def _check_met_gsp(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    value, state, asks = _dossier_state(site, "metering_class")
    fact = facts[FactField.GSP_GROUPS]
    d = _Draft("undetermined", "", [state, _fact_state(FactField.GSP_GROUPS, fact)], [], [], [])
    if value is not None and value.settlement == "cva":
        d.status, d.reason = "not_applicable", "A CVA-settled site is not on the SBMU route."
        return d
    if value is None:
        d.missing.append("dossier:metering_class")
        d.asks.extend(asks)
    if fact.state != "evidenced":
        d.missing.append(f"facts:{FactField.GSP_GROUPS.value}")
        d.asks.extend(fact.asks)
    v = run.rows["REG-04"]
    if v.verdict != "passes":
        d.reason = f"REG-04: {v.verdict}: {v.message}"
        return d
    if d.missing:
        d.reason = "Missing: " + ", ".join(d.missing) + "."
        return d
    groups = sorted(set(fact.value.groups))
    if len(groups) == 1:
        d.status, d.reason = "eligible", f"All metering lies in GSP group {groups[0]} (REG-04)."
    else:
        d.status = "ineligible"
        d.reason = f"The metering spans GSP groups {', '.join(groups)}; REG-04 requires one."
    return d


def _check_p511_route(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    v = run.rows["REG-02"]
    if v.verdict != "passes":
        return _Draft("undetermined", f"REG-02: {v.verdict}: {v.message}", [], [], [], [])
    return _Draft("eligible", "The P415 route is live (REG-02 passes the gate).", [], [], [], [])


def _check_p511_band(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    fact = facts[FactField.MSID_EXPORT]
    _, net_state, _ = _dossier_state(site, "net_export")
    net_state = net_state.model_copy(
        update={"note": net_state.note + "; net export cannot place a P511 band (gross per pair)"}
    )
    d = _Draft(
        "undetermined", "", [_fact_state(FactField.MSID_EXPORT, fact), net_state], [], [], []
    )
    problems: list[str] = []
    v = run.rows["REG-01"]
    if v.verdict != "passes":
        problems.append(f"REG-01: {v.verdict}: {v.message}")
    found: dict[str, ThresholdUse] = {}
    for ref in (
        "REG-01:lower_trading_eligibility_threshold",
        "REG-01:upper_trading_eligibility_threshold",
    ):
        t, why = _threshold(run, ref)
        if t is None:
            if v.verdict == "passes":
                problems.append(why)
                d.unblocked_extra += (f"threshold {ref}",)
        else:
            found[ref.split(":", 1)[1]] = t
    d.thresholds = sorted(found.values(), key=lambda t: t.name)
    if fact.state != "evidenced":
        d.missing.append(f"facts:{FactField.MSID_EXPORT.value}")
        d.asks.extend(fact.asks)
    elif fact.value.msid_pairs != 1:
        d.missing.append("facts:msid_export per MSID pair")
        d.asks.extend(_fact_ask(FactField.MSID_EXPORT, site.site_id) for _ in (0,))
    if problems or d.missing:
        d.reason = "; ".join(problems + (["Missing: " + ", ".join(d.missing)] if d.missing else []))
        return d
    x = Decimal(str(fact.value.largest_export_kw))
    upper = found["upper_trading_eligibility_threshold"]
    lower = found["lower_trading_eligibility_threshold"]
    if _exceeds(x, upper):
        d.status, d.classification = "ineligible", "above_upper"
        d.reason = f'Largest export {x} kW is beyond REG-01\'s upper threshold: "{upper.quote}".'
    elif _exceeds(x, lower):
        d.classification = "between"
        d.missing.append("thirty-day adjusted net import and export per MSID pair")
        d.asks.append(_fact_ask(FactField.MSID_EXPORT, site.site_id))
        d.reason = (
            f"Largest export {x} kW is between REG-01's thresholds; the thirty-day net-export test "
            "decides and its data is not held."
        )
    else:
        d.status, d.classification = "eligible", "below_lower"
        d.reason = f'Largest export {x} kW is within REG-01\'s lower threshold: "{lower.quote}".'
    return d


def _check_anm(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    value, state, asks = _dossier_state(site, "connection_terms")
    d = _Draft("undetermined", "", [state], [], [], list(asks))
    v = run.rows["REG-19"]
    if value is None:
        d.missing.append("dossier:connection_terms")
    agreements = [e.agreement for e in value.agreements] if value is not None else []
    if agreements and all(a.archetype is ConnectionArchetype.FIRM for a in agreements):
        cls, placed = "firm", True
    elif agreements:
        anm = any(
            not isinstance(a.anm_response_time_s, Unknown | NotApplicable)
            or not isinstance(a.signal_protocol, Unknown | NotApplicable)
            for a in agreements
        )
        cls = "anm_managed" if anm else "non_firm_curtailable"
        placed = all(
            a.archetype is ConnectionArchetype.FIRM
            or isinstance(a.curtailment_limit, CurtailmentLimit)
            for a in agreements
        )
        if not placed:
            d.missing.append("IC-1 curtailment_limit")
    else:
        cls, placed = "", False
    if v.verdict != "passes":
        d.reason = f"REG-19: {v.verdict}: {v.message}"
        return d
    if not placed:
        d.reason = "Missing: " + ", ".join(d.missing) + "."
        return d
    d.status, d.classification = "eligible", cls
    d.reason = (
        f"The connection is {cls.replace('_', ' ')} with its curtailment terms stated (REG-19)."
    )
    return d


def _check_cm(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    fact = facts[FactField.CM_UNIT_TYPE]
    d = _Draft("undetermined", "", [_fact_state(FactField.CM_UNIT_TYPE, fact)], [], [], [])
    if fact.state != "evidenced":
        d.missing.append(f"facts:{FactField.CM_UNIT_TYPE.value}")
        d.asks.extend(fact.asks)
    refused = [
        f"{r}: {run.rows[r].verdict}"
        for r in ("REG-16", "REG-15")
        if run.rows[r].verdict != "passes"
    ]
    d.reason = (
        "The split between demand-side-response and behind-the-meter storage units is proposed, "
        "not decided (REG-16), so the class is undetermined"
        + (f"; {'; '.join(refused)}" if refused else "")
        + (f"; missing: {', '.join(d.missing)}" if d.missing else "")
        + "."
    )
    d.unblocked_extra = ("a decision on the proposed split (REG-16)",)
    return d


def _check_dfs_service(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    refused = [run.rows[r] for r in ("REG-13", "REG-41") if run.rows[r].verdict != "passes"]
    if refused:
        why = "; ".join(f"{v.row}: {v.verdict}: {v.message}" for v in refused)
        return _Draft("undetermined", why, [], [], [], [])
    return _Draft("eligible", "REG-13 and REG-41 pass the register gate.", [], [], [], [])


def _check_dfs_route(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    fact = facts[FactField.DFS_ROUTE]
    d = _Draft("undetermined", "", [_fact_state(FactField.DFS_ROUTE, fact)], [], [], [])
    if fact.state != "evidenced":
        d.missing.append(f"facts:{FactField.DFS_ROUTE.value}")
        d.asks.extend(fact.asks)
    v = run.rows["REG-13"]
    if v.verdict != "passes":
        d.reason = f"REG-13: {v.verdict}: {v.message}"
        return d
    if d.missing:
        d.reason = "Missing: " + ", ".join(d.missing) + "."
        return d
    d.status, d.classification = "eligible", f"{fact.value.route}:{fact.value.counterparty}"
    d.reason = f"The site participates through a {fact.value.route} (REG-13)."
    return d


def _check_flex_zone(site: SiteDossier, run: _Run, facts: dict[FactField, _Fact]) -> _Draft:
    fact = facts[FactField.DNO_FLEX_ZONE]
    _, dno_state, _ = _dossier_state(site, "dno")
    d = _Draft(
        "undetermined", "", [_fact_state(FactField.DNO_FLEX_ZONE, fact), dno_state], [], [], []
    )
    if fact.state != "evidenced":
        d.missing.append(f"facts:{FactField.DNO_FLEX_ZONE.value}")
        d.asks.extend(fact.asks)
    refused = [run.rows[r] for r in ("REG-17", "REG-07") if run.rows[r].verdict != "passes"]
    if refused:
        d.reason = "; ".join(f"{v.row}: {v.verdict}: {v.message}" for v in refused)
        return d
    if d.missing:
        d.reason = "Missing: " + ", ".join(d.missing) + "."
        return d
    d.status, d.classification = "eligible", fact.value.zone
    d.reason = f"A DNO document places the site in flexibility zone {fact.value.zone}."
    return d


def lcl_site_input(
    site_id: str,
    facts: Mapping[FactField, _Fact],
    assets: ControlledAssetsDeclaration | None,
) -> dict[str, Any]:
    """The Atlas evaluator's site input. Unknown stays absent, never an empty list (R10.8)."""
    out: dict[str, Any] = {"site_id": site_id}
    cc = facts[FactField.CUSTOMER_CONSUMER_CLASS]
    if cc.state in ("evidenced", "declared"):
        customer: dict[str, Any] = {"consumer_class": cc.value.consumer_class}
        if cc.state == "evidenced":
            customer["evidence_ref"] = cc.evidence[0]
        out["customer"] = customer
    others = facts[FactField.CONTRACTING_ENTITY_CUSTOMERS]
    if others.state in ("evidenced", "declared"):
        out["contracting_entity_customers"] = [
            {
                "consumer_class": c,
                **({"evidence_ref": others.evidence[0]} if others.state == "evidenced" else {}),
            }
            for c in others.value.consumer_classes
        ]
    out["assets"] = list(assets.asset_classes) if assets is not None else []
    return out


def _check_lcl(
    site: SiteDossier,
    run: _Run,
    facts: dict[FactField, _Fact],
    *,
    assets: ControlledAssetsDeclaration | None,
    rule: CheckRule,
) -> tuple[_Draft, str | None]:
    read = [
        FactState(
            source="atlas",
            field="load_rule",
            state="loaded" if run.loader.outcome == "loaded" else "not_loaded",
            evidence=(),
            note=run.loader.reason,
        ),
        _fact_state(FactField.CUSTOMER_CONSUMER_CLASS, facts[FactField.CUSTOMER_CONSUMER_CLASS]),
        _fact_state(
            FactField.CONTRACTING_ENTITY_CUSTOMERS, facts[FactField.CONTRACTING_ENTITY_CUSTOMERS]
        ),
        FactState(
            source="declaration",
            field="controlled_assets",
            state="declared" if assets is not None else "unknown",
            evidence=(),
            note=(
                f"{assets.product}: {', '.join(assets.asset_classes)} (declared by "
                f"{assets.declared_by})"
                if assets is not None
                else "no controlled-assets declaration was supplied"
            ),
        ),
    ]
    d = _Draft("undetermined", "", read, [], [], [])
    for field in (FactField.CUSTOMER_CONSUMER_CLASS, FactField.CONTRACTING_ENTITY_CUSTOMERS):
        if facts[field].state != "evidenced":
            d.missing.append(f"facts:{field.value}")
            d.asks.extend(facts[field].asks)
    if assets is None:
        d.missing.append("declaration:controlled_assets")
    if run.rule_blocked is not None:
        cc = facts[FactField.CUSTOMER_CONSUMER_CLASS]
        note = (
            f" Declared consumer class {cc.value.consumer_class!r}: the consumer guard was not "
            "evaluated because the rule did not load."
            if cc.state != "unknown" and cc.value is not None
            else ""
        )
        d.reason = f"{run.rule_blocked}.{note}"
        d.unblocked_extra = ("a segment rule that loads as of the run's date",)
        return d, None
    missing_rows = sorted(set(run.loader.rests_on) ^ set(rule.rows))
    if missing_rows:
        d.reason = (
            f"The loaded rule rests on {', '.join(run.loader.rests_on)}, but this table cites "
            f"{', '.join(rule.rows)}; a new table version must cite the rule's rows."
        )
        return d, None
    ev = run.evaluations.get(site.site_id)
    if not ev or ev.get("outcome") != "evaluated":
        err = (ev or {}).get("error") or {}
        d.reason = (
            f"The Atlas evaluator failed for this site: {err.get('class', 'no result')}: "
            f"{err.get('message', 'not evaluated')}"
        )
        return d, None
    result = ev["result"]
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    reasons = (
        " | ".join(f"{r.get('code')}: {r.get('text')}" for r in result.get("reasons") or [])
        or "the evaluator gave no reason"
    )
    status = result.get("status")
    final = run.loader.rule_status == "final"
    if status == "ineligible":
        d.status = "ineligible"
        consumer = result.get("consumer") or {}
        d.classification = f"refused:{consumer.get('consumer_class', 'assets')}"
        d.reason = reasons
    elif status == "eligible" and final:
        d.status = "eligible"
        d.classification = f"admitted under final rule {run.loader.rule_version}: " + ", ".join(
            result.get("admitted_for_controlled_dispatch") or []
        )
        d.reason = reasons
    elif status == "eligible":
        d.reason = (
            f"PROVISIONAL RULE, NEVER EXEMPT: the evaluator returned eligible, but rule "
            f"{run.loader.rule_id} {run.loader.rule_version} is {run.loader.rule_status}; "
            f"the final rule comes from WP-GRC-07. {reasons}"
        )
    elif not final:
        d.reason = (
            f"Rule {run.loader.rule_id} {run.loader.rule_version} is {run.loader.rule_status}, "
            "not final; the final rule comes from WP-GRC-07. A site that passes a provisional "
            f"rule is undetermined, never exempt. {reasons}"
        )
    else:
        d.reason = reasons
    d.unblocked_extra = ("the final segment rule (WP-GRC-07)",) if not final else ()
    return d, canonical


def _result(rule: CheckRule, draft: _Draft, run: _Run) -> CheckResult:
    rows = tuple(run.rows[r] for r in rule.rows)
    refused = tuple(r.row for r in rows if r.verdict != "passes") if rule.engine_gates_rows else ()
    gate_only = (
        draft.status == "undetermined"
        and not draft.missing
        and not rule.proposed_only
        and bool(refused)
        and not any(x.startswith("threshold ") for x in draft.unblocked_extra)
    )
    unblocked = (
        tuple(
            dict.fromkeys(
                [
                    *(f"sign off or re-verify {r}" for r in refused),
                    *draft.missing,
                    *draft.unblocked_extra,
                ]
            )
        )
        if draft.status == "undetermined"
        else ()
    )
    reason = draft.reason or "no reason recorded"
    return CheckResult(
        check_id=rule.check_id,
        status=draft.status,  # type: ignore[arg-type]
        reason=reason,
        rows=rows,
        read=tuple(draft.read),
        missing_fields=tuple(dict.fromkeys(draft.missing)),
        thresholds=tuple(draft.thresholds),
        blocked_only_by_gate=gate_only,
        unblocked_by=unblocked,
    )


_CHECK_FNS: Final = {
    "MET-ROUTE": _check_met_route,
    "MET-GSP": _check_met_gsp,
    "P511-ROUTE": _check_p511_route,
    "P511-BAND": _check_p511_band,
    "ANM-TERMS": _check_anm,
    "CM-CLASS": _check_cm,
    "DFS-SERVICE": _check_dfs_service,
    "DFS-ROUTE": _check_dfs_route,
    "FLEX-ZONE": _check_flex_zone,
}


def _site(
    site: SiteDossier,
    run: _Run,
    facts: dict[FactField, _Fact],
    facts_record: EligibilityFacts | None,
    assets: ControlledAssetsDeclaration | None,
) -> SiteEligibility:
    streams: list[StreamResult] = []
    asks: dict[str, DossierAsk] = {}
    for stream in STREAMS:
        results: list[CheckResult] = []
        classification: str | None = None
        evaluation: str | None = None
        for rule in (c for c in CHECKS if c.stream is stream):
            if rule.check_id == "LCL-RULE":
                draft, evaluation = _check_lcl(site, run, facts, assets=assets, rule=rule)
            else:
                draft = _CHECK_FNS[rule.check_id](site, run, facts)
            if rule.proposed_only:
                draft.status, draft.classification = "undetermined", None
            if (
                run.blocked is not None
                and draft.status != "undetermined"
                and (draft.status != "not_applicable")
            ):
                draft.status, draft.classification = "undetermined", None
                draft.reason = f"{run.blocked}. {draft.reason}"
            results.append(_result(rule, draft, run))
            for a in draft.asks:
                asks[a.ask_id] = a
            if draft.classification and classification is None:
                classification = draft.classification
        status = combine(tuple(r.status for r in results))
        streams.append(
            StreamResult(
                stream=stream,
                question=STREAM_QUESTIONS[stream],
                status=status,
                classification=(
                    classification
                    if classification is not None and status != "undetermined"
                    else Unknown(
                        kind="unknown",
                        reason="; ".join(r.reason for r in results if r.status == "undetermined")
                        or "no check placed the site",
                    )
                ),
                checks=tuple(results),
                atlas_evaluation=(
                    evaluation
                    if evaluation is not None
                    else NotApplicable(kind="not_applicable", reason="no Atlas evaluation")
                ),
            )
        )
    return SiteEligibility(
        site_id=site.site_id,
        dossier_site_hash=content_hash(site),
        facts_hash=(
            facts_record.content_hash()
            if facts_record is not None
            else NotApplicable(kind="not_applicable", reason="no eligibility facts supplied")
        ),
        streams=tuple(streams),
        data_requests=tuple(asks[k] for k in sorted(asks)),
    )


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def build_eligibility(
    dossier: CandidateSiteDossier,
    *,
    as_of: date,
    atlas_root: Path | None,
    evidence_admission: EvidenceAdmission,
    facts: Iterable[EligibilityFacts] = (),
    controlled_assets: ControlledAssetsDeclaration | None = None,
    evidence_registry: Iterable[EvidenceObject] = (),
) -> SiteEligibilityTable:
    """The eligibility table for every site of ``dossier`` as of ``as_of`` (no clock is read).

    ``atlas_root`` is the Atlas checkout (`resolve_atlas_root` finds it for the CLI). ``None``
    makes every register-dependent check undetermined. Raises `EligibilityInputError` only when the
    inputs do not describe one run (R9): the dossier's or a facts record's date is not ``as_of``,
    or a synthetic input reaches a ``registered_only`` run.
    """
    if dossier.as_of != as_of:
        raise EligibilityInputError(
            f"the dossier is as of {dossier.as_of}, the run is as of {as_of}: rebuild the dossier "
            "at the run's date"
        )
    by_site = {f.site_id: f for f in facts}
    site_ids = {s.site_id for s in dossier.sites}
    for f in by_site.values():
        if f.as_of != as_of:
            raise EligibilityInputError(f"facts for {f.site_id} are as of {f.as_of}, not {as_of}")
        if f.site_id not in site_ids:
            raise EligibilityInputError(f"facts name {f.site_id}, which the dossier does not hold")
    synthetic = evidence_admission == "synthetic_fixtures_test_only"
    if not synthetic:
        if any(s.site_id.startswith("SYNTHETIC-") for s in dossier.sites):
            raise EligibilityInputError("a registered_only run cannot hold a SYNTHETIC- site")
        if any(f.record_class == "synthetic_fixture" for f in by_site.values()) or (
            controlled_assets is not None and controlled_assets.record_class != "registered"
        ):
            raise EligibilityInputError("a registered_only run admits no synthetic input record")
    registry = {o.content_hash(): o for o in evidence_registry}
    fact_states = {
        s.site_id: _facts_for(
            s.site_id,
            by_site.get(s.site_id),
            registry=registry,
            as_of=as_of,
            admission=evidence_admission,
        )
        for s in dossier.sites
    }
    payload = [
        lcl_site_input(s.site_id, fact_states[s.site_id], controlled_assets) for s in dossier.sites
    ]
    outcome = run_bridge(atlas_root, as_of=as_of, rows=cited_rows(), sites=payload)
    run = _interpret(outcome, as_of=as_of, admission=evidence_admission, atlas_root=atlas_root)
    sites = tuple(
        _site(s, run, fact_states[s.site_id], by_site.get(s.site_id), controlled_assets)
        for s in dossier.sites
    )
    actions = [
        OwnerAction(action_id=a.action_id, owner=a.owner, action=a.action) for a in OWNER_ACTIONS
    ]
    refused = sorted(r.row for r in run.rows.values() if r.verdict != "passes")
    if refused:
        actions.append(
            OwnerAction(
                action_id="REGISTER-ROWS-REFUSED",
                owner="compliance/ISMS lead (spec §14.2)",
                action="The register gate refused these cited rows as of "
                f"{as_of.isoformat()}: {', '.join(refused)}. Each refusal is on its check.",
            )
        )
    if run.loader.adoption == "not_adopted":
        actions.append(
            OwnerAction(
                action_id="ADOPT-LOADER",
                owner="Olly (GridQ)",
                action="The Atlas loader files are not an adopted pair; review the Atlas change "
                "and add a new ADOPTED_LOADERS entry with its decision record, or restore the "
                "adopted files.",
            )
        )
    return SiteEligibilityTable(
        schema_name=ELIGIBILITY_SCHEMA,
        schema_version=ELIGIBILITY_SCHEMA_VERSION,
        work_package="WP-UW-02",
        as_of=as_of,
        evidence_admission=evidence_admission,
        labels=(SYNTHETIC_LABEL,) if synthetic else (),
        rule_table_version=RULE_TABLE_VERSION,
        rule_table_hash=RULE_TABLE_HASH,
        bridge_sha256=_sha256_file(BRIDGE),
        dossier_hash=dossier.content_hash(),
        controlled_assets_hash=(
            controlled_assets.content_hash()
            if controlled_assets is not None
            else NotApplicable(kind="not_applicable", reason="no controlled-assets declaration")
        ),
        loader=run.loader,
        row_verdicts=tuple(run.rows[r] for r in sorted(run.rows)),
        sites=sites,
        owner_actions=tuple(actions),
        consumption=CONSUMPTION_STATEMENT,  # type: ignore[arg-type]
    )


def resolve_atlas_root(explicit: Path | None = None) -> tuple[Path | None, str]:
    """The CLI's Atlas root and where it came from: --atlas-root, $GRIDQ_ATLAS_ROOT, ../atlas."""
    if explicit is not None:
        return explicit, "--atlas-root"
    env = os.environ.get(ATLAS_ROOT_ENV)
    if env:
        return Path(env), ATLAS_ROOT_ENV
    from gridq.governance.dpa_guard import repo_root

    sibling = repo_root().parent / "atlas"
    return (sibling, "sibling ../atlas") if sibling.is_dir() else (None, "not found")


def main(argv: Sequence[str] | None = None) -> int:
    from gridq.governance.dpa_guard import evidence_dir, load_evidence_registry, repo_root
    from gridq.ops.dossier import DossierWriteRefused, _require_git_ignored, build_dossier
    from gridq.ops.dossier_inputs import DossierInputError, default_register

    parser = argparse.ArgumentParser(
        prog="python -m gridq.underwriting.eligibility",
        description="Build the site eligibility table (WP-UW-02) into a git-ignored directory.",
    )
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--atlas-root", type=Path, default=None)
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--register", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=repo_root() / "dossiers" / "eligibility")
    args = parser.parse_args(argv)
    root, source = resolve_atlas_root(args.atlas_root)
    ev_root = args.evidence_dir if args.evidence_dir is not None else evidence_dir()
    try:
        dossier = build_dossier(
            default_register(args.register), as_of=args.as_of, evidence_root=ev_root
        )
        facts = [
            EligibilityFacts.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted((ev_root / "eligibility_facts").glob("*.json"))
        ]
        decl_path = ev_root / "controlled_assets.json"
        assets = (
            ControlledAssetsDeclaration.model_validate_json(decl_path.read_text(encoding="utf-8"))
            if decl_path.is_file()
            else None
        )
        table = build_eligibility(
            dossier,
            as_of=args.as_of,
            atlas_root=root,
            evidence_admission="registered_only",
            facts=facts,
            controlled_assets=assets,
            evidence_registry=load_evidence_registry(ev_root),
        )
        out = args.out.resolve() / f"site_eligibility_{args.as_of.isoformat()}.json"
        _require_git_ignored(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table.model_dump_json(indent=2), encoding="utf-8")
    except DossierWriteRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (DossierInputError, EligibilityInputError, ValueError) as exc:
        print(f"input error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for s in table.sites:
        for st in s.streams:
            counts[st.status] = counts.get(st.status, 0) + 1
    print(f"atlas_root: {source}")
    print(f"loader: {table.loader.outcome}")
    print(f"sites: {len(table.sites)}")
    for k in ("eligible", "ineligible", "undetermined"):
        print(f"streams_{k}: {counts.get(k, 0)}")
    print(f"content_hash: {table.content_hash()}")
    print(f"json: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
