"""The design's review against IEC 62443-3-3 at security level 2, with its gaps (D157).

What this is: Neura's own design review of the reference zone and conduit design against the 51
system requirements (SRs) of IEC 62443-3-3, **identified only by their public identifiers and
titles** (ISA's published table of contents, `public_sources.ISA_3_3_TOC`). The requirement text,
the requirement enhancements and which of them apply at SL 2 are in the purchased standard, which
was not available. Every SR therefore carries the global gap ``GAP-62443-00`` (text unavailable),
and the SL 2 applicability of its enhancements is `Unknown`.

What this is not: a certification, a conformity assessment, or an achieved-security-level claim.
`ReviewStatus` makes the first two ``Literal[False]`` and the achieved level `Unknown`, so no
instance can say otherwise; the generated document prints `DISCLAIMER` at the top and above every
FR section.

The claim ceiling, enforced on construction:

- ``title_level_mapped``: the design has elements that address the SR as its title names it, and
  a test proves each; only the global gap remains.
- ``partially_mapped``: elements and a test exist, and at least one named gap remains.
- ``deferred_to_wp``: the design leaves it to a later work package, which owns a named gap.
- ``host_responsibility``: it depends on the facility, recorded as a data ask and a gap.

A second, independent witness: for every mapped SR that NIST CSF 1.1 cites, the CSF subcategories
of the design elements named must meet NIST's own subcategories for that SR (`csf_witness`). The
notes are Neura's words about its design. No field holds or paraphrases requirement text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, Unknown, unknown
from gridq.edge_design import reference
from gridq.edge_design.model import CONTROLS, ControlKind
from gridq.edge_design.public_sources import CSF_1_1_BY_SR, SR_CATALOGUE

__all__ = [
    "DISCLAIMER",
    "GAPS",
    "INVARIANT_CSF",
    "REVIEW",
    "Gap",
    "ReviewStatus",
    "Sl2Review",
    "SrReview",
    "build_review",
    "csf_witness",
]

#: Printed at the top of the review and above each FR section of the design document.
DISCLAIMER: Final = (
    "Neura's own design review against the public SR identifiers and titles of IEC 62443-3-3. "
    "It is not certified and is not a conformity assessment. It was not checked against the "
    "purchased requirement text or its SL 2 requirement enhancements, and claims no achieved "
    "security level."
)

GLOBAL_GAP: Final = "GAP-62443-00"
PERSON_ONLY: Final = "person_only"

Disposition = Literal[
    "title_level_mapped", "partially_mapped", "deferred_to_wp", "host_responsibility"
]
GapKind = Literal["design", "evidence_pending", "site_fact_unknown", "text_unavailable"]

GapId = Annotated[str, Field(pattern=r"^GAP-62443-[0-9]{2}$")]
WpRef = Annotated[str, Field(pattern=r"^WP-[A-Z]+-[0-9]{2}$")]
#: A test that proves an element: ``tests/<path>.py::<test function>``.
TestRef = Annotated[str, Field(pattern=r"^tests/[a-z0-9_/]+\.py::test_[a-z0-9_]+$")]
#: A design element: ``zone:``, ``conduit:``, ``control:``, ``invariant:`` or a data ``ask:``.
ElementRef = Annotated[str, Field(pattern=r"^(zone|conduit|control|invariant|ask):[A-Za-z0-9_]+$")]

_TEST_RE = re.compile(r"^tests/")


class ReviewStatus(ContractModel):
    """What the review is allowed to claim. Nothing here can be set to a stronger claim."""

    certified: Literal[False]
    conformity_assessment: Literal[False]
    sl_target: Literal[2]
    sl_achieved: Unknown
    basis: Literal["public_sr_id_and_title"]
    #: The review against the purchased text: not done (a person-only item).
    full_text_review: Unknown


class Gap(ContractModel):
    gap_id: GapId
    sr_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    kind: GapKind
    #: The work package that closes it, or ``person_only`` for something only a person can do.
    owner: Annotated[str, Field(pattern=r"^(WP-[A-Z]+-[0-9]{2}|person_only)$")]
    statement: NonEmptyStr
    closes_when: NonEmptyStr


class SrReview(ContractModel):
    sr_id: NonEmptyStr
    fr: Annotated[int, Field(ge=1, le=7)]
    #: The public title, from ISA's table of contents (never the requirement text).
    title: NonEmptyStr
    disposition: Disposition
    design_elements: tuple[ElementRef, ...]
    evidence: tuple[TestRef | WpRef, ...]
    #: NIST CSF 1.1 subcategories that cite this SR (empty when NIST cites none).
    csf_1_1: tuple[NonEmptyStr, ...]
    gap_ids: tuple[GapId, ...] = Field(min_length=1)
    sl2_re_applicability: Unknown
    #: Neura's own words on what its design does about this SR.
    note: NonEmptyStr

    @property
    def tests(self) -> tuple[str, ...]:
        return tuple(e for e in self.evidence if _TEST_RE.match(e))

    @property
    def wps(self) -> tuple[str, ...]:
        return tuple(e for e in self.evidence if not _TEST_RE.match(e))


#: The CSF 1.1 subcategories each invariant serves (Neura's judgement), for the second witness.
INVARIANT_CSF: Final[Mapping[str, tuple[str, ...]]] = {
    "no_wireless_conduit": ("PR.PT-3",),
    "no_person_to_person_channel": ("PR.PT-4",),
    "code_enters_only_signed": ("DE.CM-5", "PR.DS-6"),
    "boundary_outbound_only": ("PR.AC-3", "PR.AC-5"),
    "purdue_adjacent": ("PR.AC-5",),
    "enterprise_reaches_only_dmz": ("PR.AC-5", "PR.PT-4"),
    "off_site_reaches_control_only_through_a_break": ("PR.AC-5", "PR.PT-4"),
    "humans_use_mfa": ("PR.AC-7",),
    "writes_only_into_writable_zones": ("PR.AC-4",),
    "authority_matches_controls": ("PR.AC-4", "PR.PT-1"),
    "loss_triggers_cover_ic5": ("PR.PT-5",),
    "bms_mode_is_what_it_says": ("PR.DS-5", "PR.PT-4"),
}


def element_csf(element: str) -> frozenset[str]:
    kind, _, name = element.partition(":")
    if kind == "control":
        return frozenset(CONTROLS[ControlKind(name)].csf)
    if kind == "invariant":
        return frozenset(INVARIANT_CSF.get(name, ()))
    return frozenset()


def csf_witness(sr: SrReview) -> bool | None:
    """Whether NIST's CSF 1.1 subcategories for the SR meet those of the elements named. None
    when the witness does not apply (an unmapped disposition, or an SR NIST does not cite)."""
    if sr.disposition not in ("title_level_mapped", "partially_mapped") or not sr.csf_1_1:
        return None
    tags = frozenset().union(*(element_csf(e) for e in sr.design_elements))
    return bool(tags & set(sr.csf_1_1))


class Sl2Review(ContractModel):
    status: ReviewStatus
    disclaimer: Literal[
        "Neura's own design review against the public SR identifiers and titles of IEC 62443-3-3. "
        "It is not certified and is not a conformity assessment. It was not checked against the "
        "purchased requirement text or its SL 2 requirement enhancements, and claims no achieved "
        "security level."
    ]
    srs: tuple[SrReview, ...]
    gaps: tuple[Gap, ...]

    @model_validator(mode="after")
    def _ceiling(self) -> Sl2Review:
        _check_catalogue(self.srs)
        gaps = {g.gap_id: g for g in self.gaps}
        if len(gaps) != len(self.gaps) or [g.gap_id for g in self.gaps] != sorted(gaps):
            raise ValueError("gap ids are unique and sorted")
        if GLOBAL_GAP not in gaps or gaps[GLOBAL_GAP].kind != "text_unavailable":
            raise ValueError(f"{GLOBAL_GAP} is the global text-unavailable gap")
        if gaps[GLOBAL_GAP].owner != PERSON_ONLY:
            raise ValueError(f"{GLOBAL_GAP} is closed only by a person with the purchased text")
        for sr in self.srs:
            for problem in _sr_problems(sr, gaps):
                raise ValueError(f"{sr.sr_id}: {problem}")
        cited = {(g, s.sr_id) for s in self.srs for g in s.gap_ids}
        listed = {(g.gap_id, s) for g in self.gaps for s in g.sr_ids}
        if cited != listed:
            raise ValueError(f"gaps and SRs disagree: {sorted(cited ^ listed)[:4]}")
        return self


def _check_catalogue(srs: tuple[SrReview, ...]) -> None:
    got = [(s.sr_id, s.fr, s.title) for s in srs]
    want = [(e.sr_id, e.fr, e.title) for e in SR_CATALOGUE]
    if got != want:
        raise ValueError("the review covers exactly the 51 SRs of the ISA catalogue, in order")
    for s in srs:
        if s.csf_1_1 != CSF_1_1_BY_SR.get(s.sr_id, ()):
            raise ValueError(f"{s.sr_id}: csf_1_1 must be NIST's own subcategories")


def _sr_problems(sr: SrReview, gaps: Mapping[str, Gap]) -> Iterable[str]:
    if GLOBAL_GAP not in sr.gap_ids:
        yield f"every SR carries {GLOBAL_GAP}"
    if list(sr.gap_ids) != sorted(set(sr.gap_ids)):
        yield "gap_ids are unique and sorted"
    unknown_gaps = [g for g in sr.gap_ids if g not in gaps]
    if unknown_gaps:
        yield f"names undeclared gaps {unknown_gaps}"
        return
    named = [gaps[g] for g in sr.gap_ids if g != GLOBAL_GAP]
    d = sr.disposition
    if d in ("title_level_mapped", "partially_mapped"):
        if not any(
            e.split(":")[0] in ("zone", "conduit", "control", "invariant")
            for e in sr.design_elements
        ):
            yield f"{d} needs a design element"
        if not sr.tests:
            yield f"{d} needs a test"
        if csf_witness(sr) is False:
            yield "the elements' CSF subcategories miss all of NIST's for this SR"
    if d == "title_level_mapped" and named:
        yield "title_level_mapped carries only the global gap; with another gap it is partial"
    if d != "title_level_mapped" and not named:
        yield f"{d} names at least one gap besides {GLOBAL_GAP}"
    if d == "deferred_to_wp":
        if not sr.wps:
            yield "deferred_to_wp names the work package"
        if not any(g.owner in sr.wps for g in named):
            yield "deferred_to_wp: a named gap is owned by a work package it names"
    if d == "host_responsibility":
        if not any(e.startswith("ask:") for e in sr.design_elements):
            yield "host_responsibility names the data ask"
        if not any(g.kind == "site_fact_unknown" for g in named):
            yield "host_responsibility names a site_fact_unknown gap"


# --------------------------------------------------------------------------------- the gaps

_G = (
    (
        "GAP-62443-00",
        "text_unavailable",
        PERSON_ONLY,
        "This review uses only the public SR identifiers and titles. Nobody has checked it "
        "against the purchased requirement text or the SL 2 requirement enhancements.",
        "A person holding the purchased IEC 62443-3-3 and 3-2 checks every SR and its SL 2 "
        "enhancements against this design and records the result.",
    ),
    (
        "GAP-62443-01",
        "design",
        "WP-PLAT-06",
        "Human accounts, identifiers, authenticators, login handling and session handling for "
        "the remote-access broker are not designed here.",
        "WP-PLAT-06's identity service covers the broker's users, with tests.",
    ),
    (
        "GAP-62443-02",
        "design",
        "WP-EDGE-03",
        "Local maintenance access at the site (a console or service port on the gateway or the "
        "DMZ equipment) is not designed; the reference design gives a person no path but C11.",
        "WP-EDGE-03 either proves the gateway has no local login or designs and tests one.",
    ),
    (
        "GAP-62443-03",
        "design",
        "WP-EDGE-03",
        "The southbound fieldbus protocols as commonly deployed carry no device authentication; "
        "device binding pins addresses and ports only.",
        "WP-EDGE-03 records, per adapter and site, whether a secure protocol variant is "
        "available and used, and tests the binding.",
    ),
    (
        "GAP-62443-04",
        "design",
        "WP-EDGE-03",
        "Certificate issuance, rotation and revocation, and key storage, for mutual TLS and the "
        "partner tunnel are not designed here.",
        "WP-EDGE-03 designs and tests the gateway's key and certificate lifecycle.",
    ),
    (
        "GAP-62443-05",
        "design",
        "WP-EDGE-04",
        "The gateway's local audit buffer: its capacity across a link outage and what the edge "
        "does when it cannot record an audit event.",
        "WP-EDGE-04 sizes the buffer with the 7-day store-and-forward and tests audit failure.",
    ),
    (
        "GAP-62443-06",
        "evidence_pending",
        "WP-PLAT-06",
        "Every command is an IC-13 audit event, but protection of and access to the audit store "
        "belong to the audit log service.",
        "WP-PLAT-06's audit_log is released with access control and tamper evidence, tested.",
    ),
    (
        "GAP-62443-07",
        "design",
        "WP-EDGE-03",
        "The edge's time source and its protection are not chosen; the design only makes an "
        "untrusted clock a fail-safe trigger.",
        "WP-EDGE-03 chooses and tests the time source and its loss and drift handling.",
    ),
    (
        "GAP-62443-08",
        "design",
        "WP-EDGE-03",
        "IC-5 1.0.0 frames carry no per-frame signature: integrity rests on mutual TLS and the "
        "hash chain northbound, and on the protocol and the boundary southbound.",
        "WP-EDGE-03 decides whether per-frame signatures are needed and, if so, an IC-5 minor "
        "version adds them.",
    ),
    (
        "GAP-62443-09",
        "design",
        "WP-EDGE-06",
        "Only the update path is designed (signed images). Protection against malicious code "
        "already on the gateway or brought to it is not.",
        "WP-EDGE-06 releases the signed update channel with execution control on the gateway.",
    ),
    (
        "GAP-62443-10",
        "evidence_pending",
        "WP-GRC-10",
        "Nothing yet verifies the security functions on real equipment (firewall rules, diode, "
        "allow-list, outbound-only).",
        "WP-GRC-10's OT penetration test exercises each conduit's controls.",
    ),
    (
        "GAP-62443-11",
        "design",
        "WP-TEL-05",
        "Confidentiality of data at rest on the gateway (the telemetry buffer) and its purge at "
        "decommissioning are not designed.",
        "WP-TEL-05's retention and segregation policy covers the edge buffer.",
    ),
    (
        "GAP-62443-12",
        "site_fact_unknown",
        PERSON_ONLY,
        "The design segments a generic site. Whether a real facility's network can host these "
        "zones and conduits is unknown until the host answers the network asks.",
        "The host answers the facility network and segmentation asks for the site.",
    ),
    (
        "GAP-62443-13",
        "design",
        "WP-EDGE-03",
        "The gateway runs adapters, the IC-5 client and the policy engine in one box; how they "
        "are isolated from each other is not designed.",
        "WP-EDGE-03 designs and tests process isolation on the gateway.",
    ),
    (
        "GAP-62443-14",
        "evidence_pending",
        "WP-OPS-04",
        "Flows are logged at every boundary, but nothing yet watches the logs or alarms on them.",
        "WP-OPS-04 raises alarms from the boundary logs and the IC-5 events.",
    ),
    (
        "GAP-62443-15",
        "design",
        "WP-EDGE-03",
        "Conduit rate limits are designed; resource limits inside the gateway are not.",
        "WP-EDGE-03 sets and tests per-process resource limits.",
    ),
    (
        "GAP-62443-16",
        "design",
        "WP-EDGE-06",
        "Backup of the gateway's image and configuration is not designed.",
        "WP-EDGE-06 keeps the previous signed image and configuration for rollback.",
    ),
    (
        "GAP-62443-17",
        "design",
        "WP-EDGE-04",
        "Telemetry recovery after an outage is designed (store and forward); rebuilding a failed "
        "gateway to a known state is not.",
        "WP-EDGE-04 releases the gateway image with a tested restore procedure.",
    ),
    (
        "GAP-62443-18",
        "site_fact_unknown",
        PERSON_ONLY,
        "Whether the gateway, DMZ equipment and diode can sit on the facility's backed-up supply "
        "is a site fact.",
        "The host answers the emergency power ask for the site.",
    ),
    (
        "GAP-62443-19",
        "evidence_pending",
        "WP-EDGE-07",
        "The design pins a baseline and lists asset classes; each site's configuration and "
        "inventory come from commissioning.",
        "WP-EDGE-07's commissioning record carries the site's configuration and inventory.",
    ),
    (
        "GAP-62443-20",
        "design",
        "WP-GRC-10",
        "In the brokered_bidirectional BMS mode the boundary in front of the BMS is software (a "
        "firewall and a protocol break), not hardware.",
        "WP-GRC-10 tests that boundary at any site using the mode, and the site records why the "
        "diode modes were refused.",
    ),
    (
        "GAP-62443-21",
        "design",
        "WP-EDGE-07",
        "Portable devices brought to site (commissioning laptops, removable media) are not "
        "covered by the design.",
        "WP-EDGE-07 sets the commissioning device and media rules.",
    ),
    (
        "GAP-62443-22",
        "evidence_pending",
        "WP-EDGE-03",
        "The design fixes the point classes that may be written; the registers behind each class "
        "(the allow-list itself) are not yet built.",
        "WP-EDGE-03 releases the write allow-list and tests it against this design's classes.",
    ),
)

_GAP_TEXT: Final = {g[0]: g[1:] for g in _G}

# ------------------------------------------------------------------- the review, SR by SR

_T_MODEL = "tests/edge_design/test_edge_design_model.py::"
_T_INV = "tests/edge_design/test_edge_design_invariants.py::"
_T_IC5 = "tests/edge_design/test_edge_design_ic5.py::"
_T_REL = "tests/edge_design/test_edge_design_release.py::"

#: (SR, disposition, non-control elements, evidence, gaps besides the global one, note). The
#: controls mapped to each SR are added from `CONTROLS`, so the two tables cannot drift.
_SRS: Final[tuple[tuple[str, str, str, str, str, str], ...]] = (
    (
        "SR 1.1",
        "partially_mapped",
        "conduit:C11 invariant:humans_use_mfa",
        _T_INV + "test_humans_use_mfa",
        "01 02",
        "A person reaches the site only by a brokered, recorded session with MFA that ends at the "
        "DMZ. The broker's account system is WP-PLAT-06's.",
    ),
    (
        "SR 1.2",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "03",
        "Northbound, both ends present certificates. Southbound, each conduit is bound to the "
        "commissioned device addresses; the fieldbus protocols authenticate nothing themselves.",
    ),
    (
        "SR 1.3",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Accounts exist only at the broker; the design creates none on site.",
    ),
    (
        "SR 1.4",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Identifiers belong to the broker's identity service.",
    ),
    (
        "SR 1.5",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Authenticators belong to the broker's identity service.",
    ),
    (
        "SR 1.6",
        "title_level_mapped",
        "invariant:no_wireless_conduit",
        _T_INV + "test_no_wireless_conduit",
        "",
        "The design has no wireless conduit; the protocol vocabulary has no wireless member, and "
        "adding one breaks an invariant.",
    ),
    (
        "SR 1.7",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Passwords exist only at the broker; the broker requires MFA.",
    ),
    (
        "SR 1.8",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "04",
        "Mutual TLS on every IP conduit leaving the edge zone; the certificate lifecycle is open.",
    ),
    (
        "SR 1.9",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "04",
        "Public-key authentication on the northbound conduits; key storage is open.",
    ),
    ("SR 1.10", "deferred_to_wp", "", "WP-PLAT-06", "01", "Login feedback is the broker's."),
    (
        "SR 1.11",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Failed logins are handled at the broker.",
    ),
    (
        "SR 1.12",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Any use notification is shown by the broker.",
    ),
    (
        "SR 1.13",
        "title_level_mapped",
        "conduit:C1 conduit:C10 conduit:C11 invariant:boundary_outbound_only",
        _T_INV + "test_boundary_outbound_only " + _T_INV + "test_humans_use_mfa",
        "",
        "Every path across an untrusted network is opened from site, inside mutual TLS or an "
        "IPsec tunnel; a person crosses only with MFA through the broker.",
    ),
    (
        "SR 2.1",
        "partially_mapped",
        "invariant:writes_only_into_writable_zones invariant:authority_matches_controls",
        _T_INV
        + "test_writes_only_into_writable_zones "
        + _T_IC5
        + "test_write_bindings_follow_stage_and_mode",
        "22",
        "Each conduit says who may write which point class from which IC-5 stage; read-only "
        "conduits cannot express a write. The registers are WP-EDGE-03's allow-list.",
    ),
    (
        "SR 2.2",
        "title_level_mapped",
        "invariant:no_wireless_conduit",
        _T_INV + "test_no_wireless_conduit",
        "",
        "No wireless conduit exists, so none is used.",
    ),
    (
        "SR 2.3",
        "deferred_to_wp",
        "",
        "WP-EDGE-07",
        "21",
        "Portable devices at site belong to commissioning.",
    ),
    (
        "SR 2.4",
        "title_level_mapped",
        "conduit:C1 conduit:C2 invariant:code_enters_only_signed",
        _T_INV + "test_code_enters_only_signed",
        "",
        "Executable content travels only as a signed image, only on C1 and C2, and only into a "
        "zone that admits code through the signed update channel alone.",
    ),
    ("SR 2.5", "deferred_to_wp", "", "WP-PLAT-06", "01", "Session lock is the broker's."),
    (
        "SR 2.6",
        "deferred_to_wp",
        "conduit:C11",
        "WP-PLAT-06",
        "01",
        "The broker can end a recorded session; its rules are WP-PLAT-06's.",
    ),
    (
        "SR 2.7",
        "deferred_to_wp",
        "",
        "WP-PLAT-06",
        "01",
        "Concurrent sessions are limited at the broker.",
    ),
    (
        "SR 2.8",
        "title_level_mapped",
        "invariant:authority_matches_controls",
        _T_INV + "test_authority_matches_controls",
        "",
        "Every write conduit carries the IC-13 audit; every human session is recorded.",
    ),
    (
        "SR 2.9",
        "deferred_to_wp",
        "",
        "WP-EDGE-04",
        "05",
        "Audit storage on the gateway is sized with the 7-day buffer.",
    ),
    (
        "SR 2.10",
        "deferred_to_wp",
        "",
        "WP-EDGE-04",
        "05",
        "The edge's behaviour on an audit failure is WP-EDGE-04's.",
    ),
    (
        "SR 2.11",
        "partially_mapped",
        "zone:edge_control",
        _T_IC5 + "test_clock_and_chain_triggers_are_edge_zone_controls",
        "07",
        "An untrusted clock is an IC-5 fail-safe trigger; the time source is open.",
    ),
    (
        "SR 2.12",
        "partially_mapped",
        "",
        _T_INV + "test_authority_matches_controls",
        "08",
        "Frames are hash-chained and commands are IC-13 events with the issuing identity; frames "
        "are not signed.",
    ),
    (
        "SR 3.1",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "08",
        "Mutual TLS and the hash chain northbound; southbound integrity rests on the protocol and "
        "the boundary.",
    ),
    (
        "SR 3.2",
        "deferred_to_wp",
        "zone:edge_control",
        "WP-EDGE-06",
        "09",
        "Code enters the edge only as a signed image; protection on the box is WP-EDGE-06's.",
    ),
    (
        "SR 3.3",
        "deferred_to_wp",
        "",
        "WP-GRC-10",
        "10",
        "The security functions are verified by WP-GRC-10's OT penetration test.",
    ),
    (
        "SR 3.4",
        "title_level_mapped",
        "conduit:C1 invariant:code_enters_only_signed",
        _T_INV + "test_code_enters_only_signed",
        "",
        "Images are accepted only when signed, into a zone that takes no other software.",
    ),
    (
        "SR 3.5",
        "title_level_mapped",
        "zone:edge_control",
        _T_IC5 + "test_a_chiller_band_is_outside_write_scope_without_an_l3_mode",
        "",
        "IC-5 checks every schedule and refuses with typed reasons; fieldbus conduits pass only "
        "well-formed messages of allowed functions.",
    ),
    (
        "SR 3.6",
        "title_level_mapped",
        "zone:edge_control",
        _T_IC5 + "test_fail_safe_writes_match_the_design",
        "",
        "On any trigger the edge holds the IC-5 fail-safe target without a cloud message.",
    ),
    (
        "SR 3.7",
        "title_level_mapped",
        "zone:edge_control",
        _T_IC5 + "test_a_chiller_band_is_outside_write_scope_without_an_l3_mode",
        "",
        "Every refusal carries a typed reason.",
    ),
    (
        "SR 3.8",
        "title_level_mapped",
        "conduit:C1 conduit:C2",
        _T_MODEL + "test_controls_cover_required_frs",
        "",
        "Northbound sessions are mutual TLS and each frame names its predecessor by hash, so "
        "replay, loss and forks are seen.",
    ),
    (
        "SR 3.9",
        "partially_mapped",
        "",
        _T_INV + "test_authority_matches_controls",
        "06",
        "Audit events are hash-chained; the audit store is WP-PLAT-06's.",
    ),
    (
        "SR 4.1",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "11",
        "Confidential in transit off site (mutual TLS, IPsec); at rest on the gateway is open.",
    ),
    (
        "SR 4.2",
        "deferred_to_wp",
        "",
        "WP-TEL-05",
        "11",
        "Purging the gateway's buffer is WP-TEL-05's.",
    ),
    (
        "SR 4.3",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "04",
        "Cryptography is used on every off-site conduit; its key management is open.",
    ),
    (
        "SR 5.1",
        "partially_mapped",
        "invariant:purdue_adjacent invariant:enterprise_reaches_only_dmz",
        _T_INV + "test_purdue_adjacent " + _T_INV + "test_enterprise_reaches_only_dmz",
        "12",
        "Twelve zones by owner and Purdue level; conduits join only adjacent levels and the "
        "enterprise reaches only the DMZ. A real site's network is a data ask.",
    ),
    (
        "SR 5.2",
        "partially_mapped",
        "zone:site_dmz invariant:off_site_reaches_control_only_through_a_break "
        "invariant:bms_mode_is_what_it_says",
        _T_INV
        + "test_off_site_reaches_control_only_through_a_break "
        + _T_INV
        + "test_bms_mode_is_what_it_says",
        "12 20",
        "Every boundary has an allow-list and flow logging; off-site traffic is terminated at the "
        "DMZ; the BMS is behind a data diode by default.",
    ),
    (
        "SR 5.3",
        "title_level_mapped",
        "invariant:no_person_to_person_channel",
        _T_INV + "test_no_person_to_person_channel",
        "",
        "No conduit carries mail, chat or file sharing.",
    ),
    (
        "SR 5.4",
        "partially_mapped",
        "invariant:off_site_reaches_control_only_through_a_break",
        _T_INV + "test_off_site_reaches_control_only_through_a_break",
        "13",
        "The DMZ, the edge and the ThermaFlex adapter each terminate and re-originate sessions; "
        "isolation inside the gateway is open.",
    ),
    (
        "SR 6.1",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "06",
        "Every conduit logs its flows and every command is an audit event.",
    ),
    (
        "SR 6.2",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "14",
        "Flows are logged at every boundary; nothing watches them yet.",
    ),
    (
        "SR 7.1",
        "title_level_mapped",
        "invariant:loss_triggers_cover_ic5",
        _T_IC5 + "test_every_comms_loss_trigger_maps_to_a_conduit",
        "",
        "Off-site conduits are rate-limited, and losing any conduit that matters drives a named "
        "IC-5 fail-safe the edge holds locally.",
    ),
    (
        "SR 7.2",
        "partially_mapped",
        "",
        _T_MODEL + "test_controls_cover_required_frs",
        "15",
        "Message rate and size are bounded per conduit; limits inside the gateway are open.",
    ),
    (
        "SR 7.3",
        "deferred_to_wp",
        "",
        "WP-EDGE-06",
        "16",
        "Image and configuration backup come with the update channel.",
    ),
    (
        "SR 7.4",
        "partially_mapped",
        "conduit:C1",
        _T_IC5 + "test_every_comms_loss_trigger_maps_to_a_conduit",
        "17",
        "Telemetry is buffered through an outage and forwarded after it.",
    ),
    (
        "SR 7.5",
        "host_responsibility",
        "ask:gateway_emergency_power",
        "WP-EDGE-07",
        "18",
        "Backed-up supply for the edge equipment is the facility's to provide; asked of the host.",
    ),
    (
        "SR 7.6",
        "partially_mapped",
        "zone:site_dmz zone:edge_control",
        _T_REL + "test_design_hash_is_pinned",
        "19",
        "The network configuration of the Neura zones derives from this hashed design release.",
    ),
    (
        "SR 7.7",
        "title_level_mapped",
        "invariant:no_wireless_conduit invariant:authority_matches_controls",
        _T_INV + "test_authority_matches_controls " + _T_INV + "test_no_wireless_conduit",
        "",
        "Read-only conduits cannot express a write, nothing on site listens for an inbound "
        "session, and only the listed protocols and payloads exist.",
    ),
    (
        "SR 7.8",
        "partially_mapped",
        "",
        _T_MODEL + "test_every_zone_keeps_an_asset_inventory",
        "19",
        "Every zone lists its asset classes; the site inventory comes from commissioning.",
    ),
)


def used_controls() -> frozenset[ControlKind]:
    """Every control the reference design uses, in any BMS mode."""
    out: set[ControlKind] = set()
    for z in reference.ZONES:
        out |= set(z.controls)
    for c in (*reference.BASE_CONDUITS, *(c for v in reference.BMS_VARIANTS for c in v.conduits)):
        out |= set(c.controls)
    return frozenset(out)


def _controls_for(sr_id: str, used: frozenset[ControlKind]) -> tuple[str, ...]:
    return tuple(
        f"control:{k.value}" for k in ControlKind if k in used and sr_id in CONTROLS[k].sr_ids
    )


def build_review() -> Sl2Review:
    used = used_controls()
    titles = {e.sr_id: e for e in SR_CATALOGUE}
    srs = []
    for sr_id, disposition, elements, evidence, gap_suffixes, note in _SRS:
        entry = titles[sr_id]
        extra = tuple(elements.split())
        gap_ids = tuple(sorted({GLOBAL_GAP, *(f"GAP-62443-{g}" for g in gap_suffixes.split())}))
        srs.append(
            SrReview.model_validate(
                {
                    "sr_id": sr_id,
                    "fr": entry.fr,
                    "title": entry.title,
                    "disposition": disposition,
                    "design_elements": _controls_for(sr_id, used) + extra,
                    "evidence": tuple(evidence.split()),
                    "csf_1_1": CSF_1_1_BY_SR.get(sr_id, ()),
                    "gap_ids": gap_ids,
                    "sl2_re_applicability": unknown(
                        "not determinable from public sources: which requirement enhancements "
                        "apply at SL 2 is in the purchased text"
                    ),
                    "note": note,
                }
            )
        )
    by_gap: dict[str, list[str]] = {}
    for s in srs:
        for g in s.gap_ids:
            by_gap.setdefault(g, []).append(s.sr_id)
    gaps = tuple(
        Gap(
            gap_id=gid,
            sr_ids=tuple(by_gap.get(gid, ())),
            kind=kind,  # type: ignore[arg-type]
            owner=owner,
            statement=statement,
            closes_when=closes,
        )
        for gid, (kind, owner, statement, closes) in sorted(_GAP_TEXT.items())
    )
    return Sl2Review(
        status=ReviewStatus(
            certified=False,
            conformity_assessment=False,
            sl_target=2,
            sl_achieved=unknown(
                "no assessment has been made: this is a design review, not an assessment"
            ),
            basis="public_sr_id_and_title",
            full_text_review=unknown(
                "the purchased IEC 62443-3-3 and 3-2 text is not available (person-only item)"
            ),
        ),
        disclaimer=DISCLAIMER,
        srs=tuple(srs),
        gaps=gaps,
    )


REVIEW: Final = build_review()
GAPS: Final = REVIEW.gaps
