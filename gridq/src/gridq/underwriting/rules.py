"""The evidence-requirement map of `underwriting_basis_v1`: a versioned, content-hashed rule table.

IC-4's inventory leaves "which evidence type each stream requires" to WP-UW-01 (gap G6). This module
is that map, held as data rather than branches so it can be hashed: `RULE_TABLE_HASH` enters every
run's `input_hash` through `UnderwritingBasis.rule_table_hash`, so changing one row is a new run
identity and never a silent change of meaning (D92).

Every row says where it comes from (`RowBasis`), so a policy choice is never presented as a market
rule:

- ``published_rule`` cites a quoted passage in `gridq.underwriting.sources.SOURCES`;
- ``spec_route_table`` cites the route-to-market table of the Neura UK product spec (section 12.1);
- ``underwriting_policy`` is a choice this basis makes, stated as such, with its reason.

A requirement whose evidence has no IC-4 type yet carries ``evidence_type=None`` and a data ask. A
size minimum that no retrieved source gives is "not armed" with a data ask; nothing here was
estimated or substituted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Final, Literal

from gridq.contracts.evidence import CONTRACT_VERSION as IC4_VERSION
from gridq.contracts.evidence import EvidenceType
from gridq.provenance.hashing import hash_json
from gridq.schemas import MarketStream
from gridq.underwriting.sources import SOURCES

__all__ = [
    "ADMISSION_POLICY",
    "RULE_TABLE_HASH",
    "RULE_TABLE_VERSION",
    "STACKING_RULES",
    "STREAM_RULES",
    "Purpose",
    "Requirement",
    "RowBasis",
    "SizeMinimum",
    "StackingRule",
    "StreamRule",
    "rule_table_document",
]

RULE_TABLE_VERSION: Final = "1.0.0"


class RowBasis(StrEnum):
    """Where a row of the table comes from."""

    PUBLISHED_RULE = "published_rule"
    SPEC_ROUTE_TABLE = "spec_route_table"
    UNDERWRITING_POLICY = "underwriting_policy"


class Purpose(StrEnum):
    """What a required evidence object evidences."""

    REGISTRATION = "registration"
    CONTRACT = "contract"
    ROUTE_TO_MARKET = "route_to_market"
    CUSTOMER_CONSENT = "customer_consent"
    EXPORT_PERMISSION = "export_permission"
    EXPORT_METERING = "export_metering"
    CONNECTION = "connection"
    SERVICE_AGREEMENT = "service_agreement"


@dataclass(frozen=True)
class Requirement:
    """One evidence object a stream needs before it may enter the priced floor."""

    purpose: Purpose
    #: ``None`` when IC-4 has no type for the document (gap G6). The stream is then UNKNOWN.
    evidence_type: EvidenceType | None
    row_basis: RowBasis
    #: A `SOURCES` id for a published rule; otherwise the spec reference or the policy reason.
    citation: str
    #: Required when ``evidence_type`` is ``None``: what to ask for, and of whom.
    data_ask: str | None = None


@dataclass(frozen=True)
class SizeMinimum:
    """A market's minimum unit size, compared with the site's connection-bounded MW.

    ``min_mw=None`` means no retrieved source gives the minimum: the gate is "not armed", does not
    block, and the data ask is stated on the stream.
    """

    min_mw: float | None
    source_id: str | None
    #: The aggregation the quoted rule permits below the minimum, evidenced by a VALID, verified
    #: ``aggregator_agreement`` scoped to the stream itself.
    aggregation_route: str | None
    aggregation_source_id: str | None
    #: ``dfs_round_down``: NESO's "rounded to one decimal place", read as truncation so a
    #: registered capacity never exceeds the asset (`gb_streams.dfs_registered_unit_capacity_mw`).
    rounding: Literal["none", "dfs_round_down"] = "none"
    data_ask: str | None = None


@dataclass(frozen=True)
class StreamRule:
    """Everything `underwriting_basis_v1` requires of one stream."""

    stream: MarketStream
    requirements: tuple[Requirement, ...]
    size: SizeMinimum | None
    #: Why the row is shaped as it is, in one or two sentences.
    note: str


@dataclass(frozen=True)
class StackingRule:
    """One published (or unresolved) rule on holding two streams at one meter point.

    The rule is about ``subject``: an exclusive rule demotes the subject when it fires, because the
    published rule binds the subject's market (D95: rule-directed, never the lower-GBP
    stream). A permitting rule demotes nothing and adds its caveat to the subject's record.
    """

    rule_id: str
    subject: MarketStream
    partner: MarketStream
    status: Literal[
        "exclusive", "exclusive_until_answered", "permitted", "permitted_same_direction"
    ]
    #: When the rule fires. ``partner_registration_held``: a VALID object of ``trigger_evidence``
    #: covers the partner at the site, whether or not the partner is priced (DFS 4.7.6 bars being
    #: PART of a BM Unit, which the registration shows). ``partner_admitted``: the partner's own
    #: evidence verdict admits it to the priced floor.
    trigger: Literal["partner_registration_held", "partner_admitted"]
    trigger_evidence: EvidenceType | None
    source_ids: tuple[str, ...]
    statement: str
    data_ask: str | None = None

    @property
    def demotes(self) -> bool:
        return self.status in ("exclusive", "exclusive_until_answered")


# ------------------------------------------------------------------ admission (D93)

#: How an evidence status is treated. In the table, so tightening it is a visible version bump.
ADMISSION_POLICY: Final[dict[str, str]] = {
    "valid_verified": "admitted to the priced floor (any IC-4 verifier role; the role is printed)",
    "valid_unverified": "conditional: unlock by verifying the object against its issuer",
    "validity_unknown": "conditional: data ask for the missing effective-from or expiry date",
    "not_yet_effective": "conditional: not effective on the basis as_of date",
    "expired": "conditional: expired on the basis as_of date",
    "absent": "conditional: no object of the type covers the stream at this site",
    "no_ic4_type": "unknown: excluded at GBP 0 until IC-4 gains the type (G6 data ask)",
    "self_issued": "never admitted (IC-4 README: eligibility admits no self-issued record)",
    "synthetic_fixture": "refused unless the basis states evidence_admission "
    "'synthetic_fixtures_test_only'",
}

# ------------------------------------------------------------------ shared rows

_POLICY_CONSENT = (
    "D70 (ADR-D3): the underwriting basis enforces consent evidence. That a customer has agreed "
    "to a third party dispatching its asset is a factual claim about a third party, so it is "
    "admitted only on the customer's own document."
)
_POLICY_EXPORT = (
    "D92: priced delivery that exports needs the DNO's G99 approval. No G99 source was "
    "retrieved for WP-UW-01, so this row is policy, not a quoted rule."
)


def _consent(
    *, row_basis: RowBasis = RowBasis.UNDERWRITING_POLICY, citation: str = ""
) -> Requirement:
    return Requirement(
        Purpose.CUSTOMER_CONSENT,
        EvidenceType.CUSTOMER_CONSENT,
        row_basis,
        citation or _POLICY_CONSENT,
    )


_EXPORT = Requirement(
    Purpose.EXPORT_PERMISSION,
    EvidenceType.G99_APPROVAL,
    RowBasis.UNDERWRITING_POLICY,
    _POLICY_EXPORT,
)


def _route(spec_row: str) -> Requirement:
    return Requirement(
        Purpose.ROUTE_TO_MARKET,
        EvidenceType.AGGREGATOR_AGREEMENT,
        RowBasis.SPEC_ROUTE_TABLE,
        f"Neura UK product spec section 12.1, route for Neura: {spec_row}",
    )


_G6_ASK = (
    "DATA ASK (IC-4 gap G6): IC-4 " + IC4_VERSION + " has no evidence type for {what}, so this "
    "requirement cannot "
    "be evidenced and the stream is excluded at GBP 0 on this basis. Request to CORE: a types-only "
    "IC-4 minor adding the type, with its GB document name verified against the issuer ({issuer}). "
    "Request to the route-to-market partner: the registration document itself."
)

_NOT_ARMED_ASK = (
    "DATA ASK: no retrieved published source gives a minimum unit size for {market}, so no size "
    "gate is armed and size eligibility is UNASSERTED (not confirmed). It was not estimated. "
    "Source: {whom}."
)

_BILL_SIDE_NOTE = (
    "Bill-side value: the site's own avoided cost, with no counterparty between the battery and "
    "the pound, so no third-party evidence gate applies (D92; D55's ROUTED_STREAMS "
    "likewise routes none of them through an aggregator). The asset-level connection and "
    "G99/G100 gate is WP-UW-02's."
)

# ------------------------------------------------------------------ the map (D92)

_ROWS: tuple[StreamRule, ...] = (
    StreamRule(
        MarketStream.BALANCING_MECHANISM,
        (
            Requirement(
                Purpose.REGISTRATION,
                EvidenceType.BMU_REGISTRATION,
                RowBasis.PUBLISHED_RULE,
                "neso_wider_access_vlp_registration",
            ),
            _route("Partner VLP; SBMU >=1 MW, SVA-metered only, single GSP group"),
            _consent(),
            _EXPORT,
        ),
        SizeMinimum(
            1.0,
            "neso_wider_access_bm_min_unit",
            "a Secondary BM Unit aggregated within one Grid Supply Point Group",
            "neso_wider_access_bm_min_unit",
        ),
        "BM is entered through a registered BM Unit, via a partner VLP.",
    ),
    StreamRule(
        MarketStream.ANCILLARY_DC_DM_DR,
        (
            Requirement(
                Purpose.REGISTRATION,
                None,
                RowBasis.PUBLISHED_RULE,
                "neso_dynamic_response_min_unit",
                _G6_ASK.format(
                    what="a Dynamic Response (DC/DM/DR) response-unit registration",
                    issuer="NESO",
                ),
            ),
            _route("Partner"),
            _consent(),
            _EXPORT,
        ),
        SizeMinimum(
            1.0,
            "neso_dynamic_response_min_unit",
            "a response unit aggregating assets behind the same GSP Group (NESO technical "
            "assurance applies)",
            "neso_dynamic_response_min_unit",
        ),
        "GridQ prices DC, DM and DR as one stream, so the two-DFR stacking bar is within the "
        "stream.",
    ),
    StreamRule(
        MarketStream.DFS,
        (
            Requirement(
                Purpose.REGISTRATION,
                None,
                RowBasis.PUBLISHED_RULE,
                "neso_dfs_rules_unit_capacity",
                _G6_ASK.format(what="a DFS Unit registration", issuer="NESO"),
            ),
            _route("Via supplier/aggregator"),
            _consent(
                row_basis=RowBasis.PUBLISHED_RULE, citation="neso_dfs_rules_occupier_agreement"
            ),
        ),
        SizeMinimum(
            0.1,
            "neso_dfs_rules_unit_capacity",
            "a DFS Unit of Unit Meter Points in the same DFS Zone",
            "neso_dfs_rules_zone_aggregation",
            rounding="dfs_round_down",
        ),
        "DFS registers a DFS Unit through a supplier or aggregator; NESO's own rules require the "
        "occupier's agreement.",
    ),
    StreamRule(
        MarketStream.WHOLESALE_P415,
        (
            _route("Via the aggregator's or supplier's desk (P415 Virtual Trading Party via VLP)"),
            _consent(),
            Requirement(
                Purpose.EXPORT_METERING,
                EvidenceType.MPAN_REGISTRATION,
                RowBasis.UNDERWRITING_POLICY,
                "D92: the export leg settles on a registered export MPAN.",
            ),
            _EXPORT,
        ),
        SizeMinimum(
            None,
            None,
            None,
            None,
            data_ask=_NOT_ARMED_ASK.format(
                market="P415 wholesale entry via a VLP",
                whom="the route-to-market partner or Elexon (P511's >10 MW export ceiling is a "
                "maximum and belongs to WP-UW-02)",
            ),
        ),
        "Wholesale is gated upside on every underwriting run unless a route, consent, export MPAN "
        "and export approval are all evidenced.",
    ),
    StreamRule(
        MarketStream.CAPACITY_MARKET,
        (
            Requirement(
                Purpose.CONTRACT,
                EvidenceType.CAPACITY_AGREEMENT,
                RowBasis.SPEC_ROUTE_TABLE,
                "Neura UK product spec section 12.1, route for Neura: DSR or storage CMU via "
                "aggregator. A cmu_prequalification alone does not admit the stream: prequalified "
                "is not awarded, so it stays conditional.",
            ),
            _consent(),
        ),
        SizeMinimum(
            1.0,
            "si_2014_2043_reg_15_4",
            "a demand side response CMU whose DSR capacity is at least the minimum capacity "
            "threshold (regulation 15(4)(b))",
            "si_2014_2043_reg_15_4",
        ),
        "The CM floor is the Capacity Agreement; the agreement holder may be the site's "
        "aggregator.",
    ),
    StreamRule(
        MarketStream.LOCAL_FLEX_PICLO,
        (
            Requirement(
                Purpose.CONTRACT,
                EvidenceType.DNO_FLEXIBILITY_CONTRACT,
                RowBasis.SPEC_ROUTE_TABLE,
                "Neura UK product spec section 12.1, DNO flexibility: 'tier = conditional until "
                "contracted'.",
            ),
            _consent(),
        ),
        SizeMinimum(
            None,
            None,
            None,
            None,
            data_ask=_NOT_ARMED_ASK.format(
                market="DNO flexibility services (DNO-specific)",
                whom="the DNO's flexibility tender documents for the site's zone",
            ),
        ),
        "An executed DNO flexibility contract is the counterparty's acceptance of the unit; the "
        "unknown minimum does not block once it is held (D94).",
    ),
    StreamRule(
        MarketStream.LOAD_SERVICE,
        (
            Requirement(
                Purpose.SERVICE_AGREEMENT,
                EvidenceType.SERVICE_LEVEL_AGREEMENT,
                RowBasis.UNDERWRITING_POLICY,
                "D92: a load service is paid by the host under a service level agreement.",
            ),
        ),
        None,
        "The host's payment for a load service needs the host's agreement.",
    ),
    StreamRule(
        MarketStream.AGREED_CAPACITY_REDUCTION,
        (
            Requirement(
                Purpose.CONNECTION,
                EvidenceType.CONNECTION_AGREEMENT,
                RowBasis.UNDERWRITING_POLICY,
                "D92: a reduced agreed capacity is the DNO's agreement, held as the "
                "connection agreement that states it.",
            ),
        ),
        None,
        "The avoided agreed-capacity charge needs the DNO to agree the reduced capacity.",
    ),
    StreamRule(MarketStream.TARIFF_ARBITRAGE, (), None, _BILL_SIDE_NOTE),
    StreamRule(MarketStream.DUOS_RED_BAND_AVOIDANCE, (), None, _BILL_SIDE_NOTE),
    StreamRule(
        MarketStream.MIC_ASC_HEADROOM,
        (),
        None,
        _BILL_SIDE_NOTE + " Its GBP is carried by AGREED_CAPACITY_REDUCTION (accounting identity).",
    ),
    StreamRule(
        MarketStream.PPA_OR_BILL_SAVINGS,
        (),
        None,
        _BILL_SIDE_NOTE + " Its GBP is itemised across arbitrage and DUoS (accounting identity).",
    ),
)

#: The map, keyed by stream. A stream with no row is UNKNOWN on this basis (never silently open).
STREAM_RULES: Final[dict[MarketStream, StreamRule]] = {row.stream: row for row in _ROWS}

# ------------------------------------------------------------------ stacking (D95)

STACKING_RULES: Final[tuple[StackingRule, ...]] = (
    StackingRule(
        "dfs_x_bm",
        MarketStream.DFS,
        MarketStream.BALANCING_MECHANISM,
        "exclusive",
        "partner_registration_held",
        EvidenceType.BMU_REGISTRATION,
        ("neso_dfs_rules_no_bm_unit",),
        "A DFS Unit Meter Point 'shall not form part of a BM Unit except a Supplier Base BM Unit'. "
        "The rule binds DFS, so DFS is the stream demoted, whatever either is worth.",
        "DATA ASK: IC-4 bmu_registration records no BM Unit type, so a registered unit is treated "
        "as NOT a Supplier Base BM Unit. If it is one, the registration document must say so.",
    ),
    StackingRule(
        "dfs_x_dc_dm_dr",
        MarketStream.DFS,
        MarketStream.ANCILLARY_DC_DM_DR,
        "exclusive_until_answered",
        "partner_admitted",
        None,
        ("neso_dfs_rules_stacking_list_bar", "neso_dfs_stacking_list_v2"),
        "Rule 4.7.9 bars other Balancing Services 'where so prohibited by the DFS Stacking List', "
        "and Stacking List v.2 does not name Dynamic Containment, Moderation or Regulation. A "
        "silent permitted-stacking list is not a permission, so the pair is held exclusive.",
        "DATA ASK to the NESO DFS team (demandflexibility@neso.energy): may one Unit Meter Point "
        "hold a DFS Unit and a Dynamic Response unit at once, and in which stacking types?",
    ),
    StackingRule(
        "dfs_x_cm",
        MarketStream.DFS,
        MarketStream.CAPACITY_MARKET,
        "permitted_same_direction",
        "partner_admitted",
        None,
        ("neso_dfs_rules_cm_permitted", "neso_dfs_stacking_list_v2"),
        "DFS may sit on a CMU under a Capacity Agreement; splitting and co-delivery only in the "
        "same direction (Stacking List v.2: CM Y-SD).",
    ),
    StackingRule(
        "dfs_x_dno_flex",
        MarketStream.DFS,
        MarketStream.LOCAL_FLEX_PICLO,
        "permitted_same_direction",
        "partner_admitted",
        None,
        ("neso_dfs_stacking_list_v2",),
        "DFS with DSO services: jumping permitted; splitting and co-delivery only in the same "
        "direction (Stacking List v.2: DSO services Y / Y-SD).",
    ),
    StackingRule(
        "dc_dm_dr_x_bm",
        MarketStream.ANCILLARY_DC_DM_DR,
        MarketStream.BALANCING_MECHANISM,
        "permitted",
        "partner_admitted",
        None,
        ("neso_dfr_stacking_principles",),
        "'Stacking of a single DFR with the BM is permitted'.",
    ),
)

# ------------------------------------------------------------------ identity


def _plain(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(_plain(k)): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def rule_table_document() -> dict[str, Any]:
    """The whole table as plain JSON: rows, stacking rules, admission policy and every quote."""
    cited = sorted(
        {r.citation for row in _ROWS for r in row.requirements if r.citation in SOURCES}
        | {
            sid
            for row in _ROWS
            if row.size is not None
            for sid in (row.size.source_id, row.size.aggregation_source_id)
            if sid is not None
        }
        | {sid for rule in STACKING_RULES for sid in rule.source_ids}
    )
    return {
        "version": RULE_TABLE_VERSION,
        "admission": dict(ADMISSION_POLICY),
        "streams": [_plain(asdict(row)) for row in sorted(_ROWS, key=lambda r: r.stream.value)],
        "stacking": [_plain(asdict(rule)) for rule in STACKING_RULES],
        "sources": [
            _plain({**asdict(SOURCES[sid]), "retrieved_on": SOURCES[sid].retrieved_on.isoformat()})
            for sid in cited
        ],
    }


#: ``sha256:<hex>`` of `rule_table_document`. The basis states it, so it enters ``input_hash``.
RULE_TABLE_HASH: Final[str] = hash_json(rule_table_document())
