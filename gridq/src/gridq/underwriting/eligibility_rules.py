"""The site eligibility rule table (WP-UW-02, spec A6-02): versioned, content-hashed, row-cited.

Design: D133 to D142 in ``docs/decisions.md`` (Fable 5.2, under the owner's
delegation).

Every check cites at least one regulatory-register row (acceptance criterion 1). Each row lives in
the Atlas register (``atlas/roadmap/governance/regulatory``). The engine asks the register gate
(``register.require_verified(row, as_of=<the run's as_of>)``) about every cited row on every run.
A check whose row the gate refuses is undetermined, with the refusal as its reason (D136).

**No regulatory figure is written here.** A numeric check names the register threshold it needs
(``thresholds``) and reads its value, unit and comparator from the row the gate passed, checked
against the row's own quoted evidence (D135). The conditions below are in GridQ's words,
carry no figure, and say which register claim they rest on. A test enforces the no-figure rule.

A check may report ``ineligible`` only where ``exclusion_stated`` is true: the cited row states an
exclusion. A row that states none can only make a check eligible or undetermined. A check marked
``proposed_only`` rests on a proposal and is undetermined whatever the facts say.

This table is separate from ``gridq.underwriting.rules`` (WP-UW-01), whose hash it does not move.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Final

from gridq.contracts.evidence import EvidenceType, IssuerRole

__all__ = [
    "ADOPTED_LOADERS",
    "BRIDGE_TIMEOUT_S",
    "CHECKS",
    "FACT_EVIDENCE",
    "FACT_FIELDS",
    "OWNER_ACTIONS",
    "RULE_TABLE_HASH",
    "RULE_TABLE_VERSION",
    "STREAMS",
    "STREAM_QUESTIONS",
    "AdoptedLoader",
    "CheckRule",
    "FactField",
    "OwnerActionRule",
    "Stream",
    "cited_rows",
    "rule_table_payload",
]

RULE_TABLE_VERSION: Final = "1.0.0"

#: The bridge subprocess's wall-clock limit (D142). A fixed constant in the table,
#: so a run's identity never depends on a per-run setting. A timeout is undetermined, never a pass.
BRIDGE_TIMEOUT_S: Final = 60


class Stream(StrEnum):
    """The seven streams of spec A6-02, in the order the table lists them."""

    METERING_CLASS_AND_GSP_GROUP = "metering_class_and_gsp_group"
    P511_EXPORT_BAND = "p511_export_band"
    ANM_ZONE = "anm_zone"
    CM_CLASS = "cm_class"
    DFS = "dfs"
    DNO_FLEX_ZONE = "dno_flex_zone"
    LOAD_CONTROL_EXEMPTION_CLASS = "load_control_exemption_class"


STREAMS: Final = tuple(Stream)

#: What each stream's status answers (D137).
STREAM_QUESTIONS: Final[dict[Stream, str]] = {
    Stream.METERING_CLASS_AND_GSP_GROUP: (
        "Does the site's settlement metering give it a balancing-mechanism registration route, and "
        "does that route's GSP-group condition hold?"
    ),
    Stream.P511_EXPORT_BAND: (
        "Is the site's MSID pair excluded from the P415 wholesale route by BSC P511's export bands?"
    ),
    Stream.ANM_ZONE: (
        "Is the connection firm, or curtailable or ANM-managed, and if so are its curtailment "
        "terms stated?"
    ),
    Stream.CM_CLASS: "Which Capacity Market unit class would the site prequalify as?",
    Stream.DFS: "Is there a Demand Flexibility Service participation route for the site?",
    Stream.DNO_FLEX_ZONE: "Is the site inside a zone where its DNO procures flexibility?",
    Stream.LOAD_CONTROL_EXEMPTION_CLASS: (
        "What does the load-control customer-segment rule (WP-GRC-16, and WP-GRC-07's final rule "
        "when released) return for the site?"
    ),
}


class FactField(StrEnum):
    """Site facts the OPS-01 dossier has no field for (D139): evidenced or unknown."""

    CUSTOMER_CONSUMER_CLASS = "customer_consumer_class"
    CONTRACTING_ENTITY_CUSTOMERS = "contracting_entity_customers"
    GSP_GROUPS = "gsp_groups"
    MSID_EXPORT = "msid_export"
    CM_UNIT_TYPE = "cm_unit_type"
    DFS_ROUTE = "dfs_route"
    DNO_FLEX_ZONE = "dno_flex_zone"


FACT_FIELDS: Final = tuple(FactField)

_MPAN_ISSUERS: Final = frozenset(
    {
        IssuerRole.ELEXON,
        IssuerRole.SUPPLIER,
        IssuerRole.METER_OPERATOR,
        IssuerRole.DNO,
        IssuerRole.IDNO,
    }
)

#: fact -> {admissible IC-4 type -> admissible issuer roles}. An empty map: no IC-4 type can
#: evidence the fact today (IC-4 has no type for a customer's consumer class; owner action
#: IC4-CONSUMER-CLASS). Only a declaration reaches the rule then, and a declared class the rule
#: refuses is refused, while a declared class it would pass is held (the rule needs evidence).
FACT_EVIDENCE: Final[dict[FactField, dict[EvidenceType, frozenset[IssuerRole]]]] = {
    FactField.CUSTOMER_CONSUMER_CLASS: {},
    FactField.CONTRACTING_ENTITY_CUSTOMERS: {},
    FactField.GSP_GROUPS: {EvidenceType.MPAN_REGISTRATION: _MPAN_ISSUERS},
    FactField.MSID_EXPORT: {
        EvidenceType.METERED_DATA_RECORD: frozenset({IssuerRole.METER_OPERATOR}),
    },
    FactField.CM_UNIT_TYPE: {
        EvidenceType.CMU_PREQUALIFICATION: frozenset({IssuerRole.EMR_SETTLEMENT, IssuerRole.NESO}),
        EvidenceType.CAPACITY_AGREEMENT: frozenset({IssuerRole.EMR_SETTLEMENT}),
    },
    FactField.DFS_ROUTE: {
        EvidenceType.AGGREGATOR_AGREEMENT: frozenset({IssuerRole.AGGREGATOR, IssuerRole.SUPPLIER}),
    },
    FactField.DNO_FLEX_ZONE: {
        EvidenceType.DNO_FLEXIBILITY_CONTRACT: frozenset({IssuerRole.DNO, IssuerRole.IDNO}),
    },
}

#: The data request for each fact when it is unknown: (request, holder, document that closes it).
FACT_ASKS: Final[dict[FactField, tuple[str, str, tuple[EvidenceType, ...]]]] = {
    FactField.CUSTOMER_CONSUMER_CLASS: (
        "State the site customer's consumer class (domestic, small business, or neither) with the "
        "supply contract or supplier record that shows it",
        "customer",
        (),
    ),
    FactField.CONTRACTING_ENTITY_CUSTOMERS: (
        "List the consumer class of every customer of the entity that contracts for controlled "
        "dispatch, in every product, with the record that shows each",
        "neura_owner",
        (),
    ),
    FactField.GSP_GROUPS: (
        "Provide the MPAN registration of each import and export MPAN at the site, showing its GSP "
        "group",
        "meter_operator",
        (EvidenceType.MPAN_REGISTRATION,),
    ),
    FactField.MSID_EXPORT: (
        "Provide the meter operator's half-hourly export data for each MSID pair over the last "
        "year, and the preceding thirty days' adjusted net import and export, so P511's bands can "
        "be read per MSID pair",
        "meter_operator",
        (EvidenceType.METERED_DATA_RECORD,),
    ),
    FactField.CM_UNIT_TYPE: (
        "Provide the CMU prequalification or capacity agreement naming the unit's class",
        "partner",
        (EvidenceType.CMU_PREQUALIFICATION, EvidenceType.CAPACITY_AGREEMENT),
    ),
    FactField.DFS_ROUTE: (
        "Provide the supplier's or aggregator's agreement giving the site a DFS "
        "participation route",
        "partner",
        (EvidenceType.AGGREGATOR_AGREEMENT,),
    ),
    FactField.DNO_FLEX_ZONE: (
        "Provide the DNO flexibility contract, or the DNO's zone record, naming the flexibility "
        "zone the site's connection point lies in",
        "network_operator",
        (EvidenceType.DNO_FLEXIBILITY_CONTRACT,),
    ),
}


@dataclass(frozen=True)
class CheckRule:
    """One check of one stream. ``reads`` entries are ``<source>:<field>``."""

    check_id: str
    stream: Stream
    rows: tuple[str, ...]
    reads: tuple[str, ...]
    condition: str
    exclusion_stated: bool
    #: ``<row>:<threshold name>``: thresholds read from the row the gate passed.
    thresholds: tuple[str, ...] = ()
    proposed_only: bool = False
    #: True when the engine applies the register gate to every cited row itself. False for the
    #: load-control rule, whose loader gates its own rows (provisional reliance, WP-GRC-16).
    engine_gates_rows: bool = True


CHECKS: Final[tuple[CheckRule, ...]] = (
    CheckRule(
        check_id="MET-ROUTE",
        stream=Stream.METERING_CLASS_AND_GSP_GROUP,
        rows=("REG-04", "REG-38"),
        reads=("dossier:metering_class",),
        condition=(
            "An SVA-settled site has the secondary BM unit route through a virtual lead party "
            "(REG-04); a CVA-settled site has the primary BM unit route through a supplier "
            "(REG-38). Only the row of the site's own route must pass the register gate."
        ),
        exclusion_stated=False,
    ),
    CheckRule(
        check_id="MET-GSP",
        stream=Stream.METERING_CLASS_AND_GSP_GROUP,
        rows=("REG-04",),
        reads=("dossier:metering_class", "facts:gsp_groups"),
        condition=(
            "On the secondary BM unit route the site's metering lies within a single GSP group "
            "(REG-04 states the single-GSP-group condition). Not applicable to a CVA-settled site."
        ),
        exclusion_stated=True,
    ),
    CheckRule(
        check_id="P511-ROUTE",
        stream=Stream.P511_EXPORT_BAND,
        rows=("REG-02",),
        reads=(),
        condition="The P415 virtual lead party wholesale route is live (REG-02).",
        exclusion_stated=False,
    ),
    CheckRule(
        check_id="P511-BAND",
        stream=Stream.P511_EXPORT_BAND,
        rows=("REG-01",),
        reads=("facts:msid_export", "dossier:net_export"),
        condition=(
            "BSC P511 bands each MSID pair by its largest half-hourly export over the lookback "
            "(REG-01). Above the upper threshold the pair is excluded from P415; between the lower "
            "and upper thresholds the thirty-day adjusted net-export test decides; at or below the "
            "lower threshold the band does not exclude it. Both thresholds, their units and their "
            "comparators are read from REG-01 itself. The dossier's net export is net, not the "
            "largest gross export per MSID pair, so it cannot place the band."
        ),
        exclusion_stated=True,
        thresholds=(
            "REG-01:lower_trading_eligibility_threshold",
            "REG-01:upper_trading_eligibility_threshold",
        ),
    ),
    CheckRule(
        check_id="ANM-TERMS",
        stream=Stream.ANM_ZONE,
        rows=("REG-19",),
        reads=("dossier:connection_terms",),
        condition=(
            "A firm connection is not curtailed. A non-firm or hybrid connection is curtailable "
            "under the Access SCR's curtailment limits (REG-19); it is placed only when its IC-1 "
            "terms state the curtailment limit. REG-19 states no exclusion, so this stream is "
            "never ineligible."
        ),
        exclusion_stated=False,
    ),
    CheckRule(
        check_id="CM-CLASS",
        stream=Stream.CM_CLASS,
        rows=("REG-16", "REG-15"),
        reads=("facts:cm_unit_type",),
        condition=(
            "The split between demand-side-response and behind-the-meter storage units is a "
            "proposal (REG-16), and the prequalification window is in the register (REG-15). A "
            "class resting on a proposal is not decided, so the class is undetermined."
        ),
        exclusion_stated=False,
        proposed_only=True,
    ),
    CheckRule(
        check_id="DFS-SERVICE",
        stream=Stream.DFS,
        rows=("REG-13", "REG-41"),
        reads=(),
        condition=(
            "The Demand Flexibility Service runs year-round and two-way (REG-13) under a "
            "derogation whose term is in the register (REG-41). Its expiry is the register "
            "gate's to judge."
        ),
        exclusion_stated=False,
    ),
    CheckRule(
        check_id="DFS-ROUTE",
        stream=Stream.DFS,
        rows=("REG-13",),
        reads=("facts:dfs_route",),
        condition=(
            "The site participates through a supplier or aggregator (REG-13); the route is "
            "evidenced by that party's agreement."
        ),
        exclusion_stated=False,
    ),
    CheckRule(
        check_id="FLEX-ZONE",
        stream=Stream.DNO_FLEX_ZONE,
        rows=("REG-17", "REG-07"),
        reads=("facts:dno_flex_zone", "dossier:dno"),
        condition=(
            "DNO flexibility is contracted on the ENA standard agreement (REG-17) and its assets "
            "are recorded in the flexibility market asset register (REG-07). The site is placed "
            "only by a DNO document naming its zone. The rows state no exclusion."
        ),
        exclusion_stated=False,
    ),
    CheckRule(
        check_id="LCL-RULE",
        stream=Stream.LOAD_CONTROL_EXEMPTION_CLASS,
        rows=("REG-26", "REG-27", "REG-42"),
        reads=(
            "atlas:load_rule",
            "facts:customer_consumer_class",
            "facts:contracting_entity_customers",
            "declaration:controlled_assets",
        ),
        condition=(
            "The segment rule is obtained only through the Atlas loader with the run's as_of, in a "
            "fresh process, and evaluated by the Atlas evaluator. A domestic or small-business "
            "customer anywhere in the contracting entity's portfolio fails the proposed Class B "
            "condition (REG-27). While the rule is provisional nothing is reported eligible. Any "
            "loader exception is undetermined with its reason."
        ),
        exclusion_stated=True,
        engine_gates_rows=False,
    ),
)


@dataclass(frozen=True)
class AdoptedLoader:
    """A pair of Atlas loader files GridQ has adopted by hash (D134).

    Under ``registered_only`` the engine relies on the Atlas register module and load-control
    loader only when both files' sha256 match one adopted pair. Adopting WP-GRC-07's loader is a new
    entry here with its own decision record; an entry is never replaced.
    """

    released_by: str
    atlas_commit: str
    loader_sha256: str
    register_module_sha256: str


ADOPTED_LOADERS: Final[tuple[AdoptedLoader, ...]] = (
    AdoptedLoader(
        released_by="WP-GRC-16",
        atlas_commit="0cc045be6971e449e70cf387094d62487a5f9800",
        loader_sha256="sha256:e169a51262e10d31d59c000d4ae0ffde936a582719d2fc1e99ac642f00f8feb3",
        register_module_sha256=(
            "sha256:ee30ac641c27ac1f8ef11311bab75f9c897651362dc465a21daa937296adb434"
        ),
    ),
)


@dataclass(frozen=True)
class OwnerActionRule:
    action_id: str
    owner: str
    action: str


#: Actions only a person or another repository can take; listed on every table (R10.4).
OWNER_ACTIONS: Final[tuple[OwnerActionRule, ...]] = (
    OwnerActionRule(
        action_id="ATLAS-THRESHOLDS",
        owner="Atlas register owner (WP-GRC-03)",
        action=(
            "Add a machine-readable `thresholds` mapping to register rows that state a figure "
            "(REG-01 first): {name: {value, unit, comparator, evidence_index, quote}}, with a "
            "build check in build_register.py that the quote is verbatim in that evidence's "
            "quotes and carries the figure and unit. Until then every numeric check is "
            "undetermined."
        ),
    ),
    OwnerActionRule(
        action_id="IC4-CONSUMER-CLASS",
        owner="CORE (IC-4, WP-BO-02)",
        action=(
            "Add an IC-4 evidence type for a customer's consumer class (a supplier's record of a "
            "domestic or small-business classification). Until then a passing consumer class "
            "cannot be evidenced and the load-control stream stays undetermined."
        ),
    ),
    OwnerActionRule(
        action_id="REGISTER-SIGN-OFF",
        owner="compliance/ISMS lead (spec §14.2)",
        action=(
            "Sign off each register row this table cites after checking it against its primary "
            "source; require_verified refuses every row without a person's sign-off."
        ),
    ),
)


def cited_rows() -> tuple[str, ...]:
    """Every register row the table cites, sorted."""
    return tuple(sorted({r for c in CHECKS for r in c.rows}))


def rule_table_payload() -> dict[str, object]:
    """The canonical content of the table: what ``RULE_TABLE_HASH`` hashes."""
    return {
        "table": "gridq.underwriting.eligibility_rules",
        "version": RULE_TABLE_VERSION,
        "bridge_timeout_s": BRIDGE_TIMEOUT_S,
        "streams": [s.value for s in STREAMS],
        "questions": {s.value: q for s, q in STREAM_QUESTIONS.items()},
        "checks": [{**asdict(c), "stream": c.stream.value, "rows": list(c.rows)} for c in CHECKS],
        "fact_evidence": {
            f.value: {t.value: sorted(r.value for r in roles) for t, roles in m.items()}
            for f, m in FACT_EVIDENCE.items()
        },
        "fact_asks": {
            f.value: [req, holder, [t.value for t in types]]
            for f, (req, holder, types) in FACT_ASKS.items()
        },
        "adopted_loaders": [asdict(a) for a in ADOPTED_LOADERS],
        "owner_actions": [asdict(a) for a in OWNER_ACTIONS],
    }


def _hash() -> str:
    text = json.dumps(
        rule_table_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


RULE_TABLE_HASH: Final = _hash()
