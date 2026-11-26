"""The openly published sources behind the zone and conduit design and its SL2 review.

Decision D157.

IEC 62443-3-3 is a paid, copyrighted standard. Nothing here quotes its requirement text. What this
module holds was retrieved from openly published documents, each cited by URL with the retrieval
date and the SHA-256 of the bytes retrieved (the documents themselves are not committed):

- `ISA_3_3_TOC`: ISA's own published table of contents of ISA-62443-3-3 (99.03.03)-2013. It gives
  the 51 system requirement (SR) identifiers and titles and the seven foundational requirement (FR)
  names as clause headings. It does **not** give the requirement text, the requirement
  enhancements (REs), or which REs apply at security level 2 (Annex B): those are paid text.
- `ISAGCA_QSG`: the ISA Global Cybersecurity Alliance Quick Start Guide. It defines a zone and a
  conduit, lists the FRs, defines security levels 1 to 4 by the attacker's means, resources, skills
  and motivation, and names SL-T, SL-C and SL-A. It says Part 3-3 uses the security level to
  discriminate between requirement enhancements, which is why the SL2 selection cannot be read
  from public material.
- `NIST_SP_800_82R3`: NIST's OT security guide. §5.2.3.1 uses the Purdue model and a DMZ, says
  firewall rules should only permit connections between adjacent levels, and offers a
  unidirectional gateway (data diode) as an alternative; Appendix E.1.2 says a data diode is
  hardware-incapable of the reverse direction.
- `NIST_CSF_1_1`: the NIST Cybersecurity Framework 1.1 Framework Core, whose informative
  references cite "ISA 62443-3-3:2013 SR x.y" for each subcategory. `CSF_1_1_BY_SR` is that
  mapping inverted (47 of the 51 SRs are cited; SR 3.6, 3.7, 7.5 and 7.7 are not). It is the
  review's second, independent witness.

Each record states what it provides and what it lacks, so no reader takes a title-level mapping
for a review against the requirement text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Final, Literal

from pydantic import Field

from gridq.contracts._base import ContractModel, IsoDate, NonEmptyStr

__all__ = [
    "CSF_1_1_BY_SR",
    "FR_NAMES",
    "ISAGCA_QSG",
    "ISA_3_3_TOC",
    "NIST_CSF_1_1",
    "NIST_SP_800_82R3",
    "PUBLIC_SOURCES",
    "SR_CATALOGUE",
    "PublicSource",
    "SrCatalogueEntry",
    "fr_of",
]

_HEX64 = r"^[0-9a-f]{64}$"
SourceId = Literal[
    "isa_62443_3_3_toc", "isagca_quick_start_guide", "nist_sp_800_82r3", "nist_csf_1_1"
]


class PublicSource(ContractModel):
    """One openly published document, as retrieved: where, when, and the bytes' SHA-256."""

    source_id: SourceId
    title: NonEmptyStr
    publisher: NonEmptyStr
    url: Annotated[str, Field(pattern=r"^https://\S+$")]
    retrieved_on: IsoDate
    sha256: Annotated[str, Field(pattern=_HEX64)]
    byte_length: Annotated[int, Field(ge=1)]
    provides: tuple[NonEmptyStr, ...] = Field(min_length=1)
    lacks: tuple[NonEmptyStr, ...]


def _source(**kw: object) -> PublicSource:
    return PublicSource.model_validate(kw, strict=False)


ISA_3_3_TOC: Final = _source(
    source_id="isa_62443_3_3_toc",
    title="ISA-62443-3-3 (99.03.03)-2013, table of contents (pages 7 to 9)",
    publisher="International Society of Automation (ISA)",
    url="https://www.isa.org/getmedia/4379f3a1-2687-4a0a-9d0f-20d743233c1b/ISA-62443-3-3_TOC.pdf",
    retrieved_on="2026-11-26",
    sha256="539f645080156de68b65cc3383daf66a0148730c10021fedf08b885f5780ac8b",
    byte_length=213083,
    provides=(
        "the 51 SR identifiers and titles, as clause headings",
        "the seven FR names, as clause headings",
    ),
    lacks=(
        "the requirement text of every SR",
        "the requirement enhancements (REs)",
        "which SRs and REs apply at SL 2 (Annex B, Table B.1)",
    ),
)

ISAGCA_QSG: Final = _source(
    source_id="isagca_quick_start_guide",
    title=(
        "Quick Start Guide: An Overview of ISA/IEC 62443 Standards "
        "(ISA Global Cybersecurity Alliance)"
    ),
    publisher="International Society of Automation (ISA), ISA Global Cybersecurity Alliance",
    url=(
        "https://isasecure.org/hubfs/2023%20ISA%20Website%20Redesigns/ISAGCA/PDFs/"
        "ISAGCA%20Quick%20Start%20Guide%20FINAL.pdf"
    ),
    retrieved_on="2026-11-26",
    sha256="06c9d5e440eaf2fe03d3e22dc348c9e0842ad3ed445ea2ba4e97df179c2d81c4",
    byte_length=798435,
    provides=(
        "the definitions of a zone and a conduit",
        "the seven FR names and abbreviations",
        "security levels 1 to 4 by the attacker's means, resources, skills and motivation",
        "SL-T (target), SL-C (capability) and SL-A (achieved)",
        "that Part 3-3 uses the security level to discriminate between requirement enhancements",
    ),
    lacks=("any SR or RE text", "the SL 2 selection of requirement enhancements"),
)

NIST_SP_800_82R3: Final = _source(
    source_id="nist_sp_800_82r3",
    title="NIST SP 800-82r3, Guide to Operational Technology (OT) Security (September 2023)",
    publisher="National Institute of Standards and Technology (NIST)",
    url="https://doi.org/10.6028/NIST.SP.800-82r3",
    retrieved_on="2026-11-26",
    sha256="608f554514d381853e24e0b33123b7080fbd757826f460032690aade63394616",
    byte_length=8584345,
    provides=(
        "5.2.3.1: the Purdue model as a segmentation model, a DMZ as an enforcement boundary, "
        "firewall rules only between adjacent levels, and a unidirectional gateway as an "
        "alternative",
        "Appendix E.1.2: a data diode is hardware-incapable of the reverse direction",
    ),
    lacks=("any IEC 62443-3-3 SR or RE text",),
)

NIST_CSF_1_1: Final = _source(
    source_id="nist_csf_1_1",
    title=(
        "Framework for Improving Critical Infrastructure Cybersecurity, Version 1.1 "
        "(April 16, 2018)"
    ),
    publisher="National Institute of Standards and Technology (NIST)",
    url="https://doi.org/10.6028/NIST.CSWP.04162018",
    retrieved_on="2026-11-26",
    sha256="0f3ca796610ab024cbc3484cbd6799e19b4d9159d3634972a2a76af83f15fb92",
    byte_length=1062822,
    provides=(
        "Framework Core informative references citing ISA 62443-3-3:2013 SR identifiers per "
        "subcategory (47 of the 51 SRs)",
    ),
    lacks=("any SR or RE text", "any security-level selection"),
)

#: Every source, by id.
PUBLIC_SOURCES: Final[Mapping[str, PublicSource]] = {
    s.source_id: s for s in (ISA_3_3_TOC, ISAGCA_QSG, NIST_SP_800_82R3, NIST_CSF_1_1)
}

#: The seven foundational requirements (ISA TOC clauses 5 to 11; ISAGCA guide).
FR_NAMES: Final[Mapping[int, str]] = {
    1: "Identification and authentication control",
    2: "Use control",
    3: "System integrity",
    4: "Data confidentiality",
    5: "Restricted data flow",
    6: "Timely response to events",
    7: "Resource availability",
}


class SrCatalogueEntry(ContractModel):
    """One system requirement as the ISA table of contents names it: identifier and title only."""

    sr_id: Annotated[str, Field(pattern=r"^SR [1-7]\.[1-9][0-9]?$")]
    fr: Annotated[int, Field(ge=1, le=7)]
    title: NonEmptyStr
    source_id: Literal["isa_62443_3_3_toc"]


def fr_of(sr_id: str) -> int:
    """The FR an SR belongs to (the number before the dot)."""
    return int(sr_id.split(" ")[1].split(".")[0])


_SR_TITLES: Final = (
    ("SR 1.1", "Human user identification and authentication"),
    ("SR 1.2", "Software process and device identification and authentication"),
    ("SR 1.3", "Account management"),
    ("SR 1.4", "Identifier management"),
    ("SR 1.5", "Authenticator management"),
    ("SR 1.6", "Wireless access management"),
    ("SR 1.7", "Strength of password-based authentication"),
    ("SR 1.8", "Public key infrastructure (PKI) certificates"),
    ("SR 1.9", "Strength of public key authentication"),
    ("SR 1.10", "Authenticator feedback"),
    ("SR 1.11", "Unsuccessful login attempts"),
    ("SR 1.12", "System use notification"),
    ("SR 1.13", "Access via untrusted networks"),
    ("SR 2.1", "Authorization enforcement"),
    ("SR 2.2", "Wireless use control"),
    ("SR 2.3", "Use control for portable and mobile devices"),
    ("SR 2.4", "Mobile code"),
    ("SR 2.5", "Session lock"),
    ("SR 2.6", "Remote session termination"),
    ("SR 2.7", "Concurrent session control"),
    ("SR 2.8", "Auditable events"),
    ("SR 2.9", "Audit storage capacity"),
    ("SR 2.10", "Response to audit processing failures"),
    ("SR 2.11", "Timestamps"),
    ("SR 2.12", "Non-repudiation"),
    ("SR 3.1", "Communication integrity"),
    ("SR 3.2", "Malicious code protection"),
    ("SR 3.3", "Security functionality verification"),
    ("SR 3.4", "Software and information integrity"),
    ("SR 3.5", "Input validation"),
    ("SR 3.6", "Deterministic output"),
    ("SR 3.7", "Error handling"),
    ("SR 3.8", "Session integrity"),
    ("SR 3.9", "Protection of audit information"),
    ("SR 4.1", "Information confidentiality"),
    ("SR 4.2", "Information persistence"),
    ("SR 4.3", "Use of cryptography"),
    ("SR 5.1", "Network segmentation"),
    ("SR 5.2", "Zone boundary protection"),
    ("SR 5.3", "General purpose person-to-person communication restrictions"),
    ("SR 5.4", "Application partitioning"),
    ("SR 6.1", "Audit log accessibility"),
    ("SR 6.2", "Continuous monitoring"),
    ("SR 7.1", "Denial of service protection"),
    ("SR 7.2", "Resource management"),
    ("SR 7.3", "Control system backup"),
    ("SR 7.4", "Control system recovery and reconstitution"),
    ("SR 7.5", "Emergency power"),
    ("SR 7.6", "Network and security configuration settings"),
    ("SR 7.7", "Least functionality"),
    ("SR 7.8", "Control system component inventory"),
)

#: The 51 SRs of ISA-62443-3-3 (99.03.03)-2013, as its published table of contents lists them.
SR_CATALOGUE: Final[tuple[SrCatalogueEntry, ...]] = tuple(
    SrCatalogueEntry(sr_id=i, fr=fr_of(i), title=t, source_id="isa_62443_3_3_toc")
    for i, t in _SR_TITLES
)

#: NIST CSF 1.1 Framework Core informative references, inverted: SR -> the subcategories that cite
#: it. Parsed from the retrieved PDF (``NIST_CSF_1_1``) and frozen here. SR 3.6, 3.7, 7.5 and 7.7
#: are cited by no subcategory.
CSF_1_1_BY_SR: Final[Mapping[str, tuple[str, ...]]] = {
    "SR 1.1": ("PR.AC-1", "PR.AC-6", "PR.AC-7", "PR.PT-3"),
    "SR 1.2": ("PR.AC-1", "PR.AC-6", "PR.AC-7", "PR.PT-3"),
    "SR 1.3": ("PR.AC-1", "PR.PT-3"),
    "SR 1.4": ("PR.AC-1", "PR.AC-6", "PR.PT-3"),
    "SR 1.5": ("PR.AC-1", "PR.AC-6", "PR.AC-7", "PR.PT-3"),
    "SR 1.6": ("PR.PT-3",),
    "SR 1.7": ("PR.AC-1", "PR.AC-7", "PR.PT-3"),
    "SR 1.8": ("PR.AC-1", "PR.AC-7", "PR.PT-3"),
    "SR 1.9": ("PR.AC-1", "PR.AC-6", "PR.AC-7", "PR.PT-3"),
    "SR 1.10": ("PR.AC-7", "PR.PT-3"),
    "SR 1.11": ("PR.PT-3",),
    "SR 1.12": ("PR.PT-3",),
    "SR 1.13": ("PR.AC-3", "PR.PT-3"),
    "SR 2.1": ("PR.AC-4", "PR.AC-6", "PR.PT-3"),
    "SR 2.2": ("PR.PT-3",),
    "SR 2.3": ("PR.PT-2", "PR.PT-3"),
    "SR 2.4": ("DE.CM-5", "PR.PT-3"),
    "SR 2.5": ("PR.PT-3",),
    "SR 2.6": ("PR.AC-3", "PR.PT-3"),
    "SR 2.7": ("PR.PT-3",),
    "SR 2.8": ("DE.AE-2", "ID.SC-5", "PR.PT-1", "RS.AN-3"),
    "SR 2.9": ("DE.AE-2", "PR.PT-1", "RS.AN-3"),
    "SR 2.10": ("DE.AE-2", "PR.PT-1", "RS.AN-3"),
    "SR 2.11": ("DE.AE-2", "PR.PT-1", "RS.AN-3"),
    "SR 2.12": ("DE.AE-2", "PR.PT-1", "RS.AN-3"),
    "SR 3.1": ("PR.AC-5", "PR.DS-2", "PR.DS-6", "PR.PT-4"),
    "SR 3.2": ("DE.CM-4",),
    "SR 3.3": ("DE.DP-3", "ID.SC-5", "PR.DS-6"),
    "SR 3.4": ("PR.DS-1", "PR.DS-6"),
    "SR 3.5": ("PR.PT-4",),
    "SR 3.8": ("PR.AC-5", "PR.DS-2", "PR.DS-6", "PR.PT-4"),
    "SR 3.9": ("DE.AE-2", "RS.AN-3"),
    "SR 4.1": ("PR.DS-1", "PR.DS-2", "PR.PT-4"),
    "SR 4.2": ("PR.DS-2", "PR.DS-3", "PR.IP-6"),
    "SR 4.3": ("PR.PT-4",),
    "SR 5.1": ("PR.PT-4", "RS.MI-1"),
    "SR 5.2": ("PR.DS-5", "PR.PT-4", "RS.MI-1"),
    "SR 5.3": ("PR.PT-4",),
    "SR 5.4": ("RS.MI-1",),
    "SR 6.1": ("DE.AE-2", "DE.AE-3", "DE.DP-4", "ID.SC-4", "ID.SC-5", "RS.AN-1", "RS.AN-3"),
    "SR 6.2": ("DE.AE-2", "DE.CM-1", "DE.CM-3"),
    "SR 7.1": ("PR.DS-4", "PR.PT-4", "PR.PT-5"),
    "SR 7.2": ("PR.DS-4", "PR.PT-5"),
    "SR 7.3": ("ID.SC-5", "PR.IP-4"),
    "SR 7.4": ("ID.SC-5", "PR.IP-4"),
    "SR 7.6": ("PR.IP-1", "PR.IP-3", "PR.PT-4"),
    "SR 7.8": ("ID.AM-1", "ID.AM-2"),
}
