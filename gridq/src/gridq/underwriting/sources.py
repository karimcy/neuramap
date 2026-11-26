"""The published rules `underwriting_basis_v1` enforces, each retrieved and quoted (WP-UW-01).

Every size minimum and market rule in the rule table (`gridq.underwriting.rules`) cites one entry
here. Each entry is a verbatim quotation from the published document, with the place in that
document it comes from and the date it was retrieved. Nothing here is a paraphrase, an estimate or a
value Neura would have to ask a counterparty for: those stay "unknown" in the rule table, each with
its data ask.

Retrieved 2026-11-26 by the WP-UW-01 session. The PDFs were fetched from the URLs below and their
text extracted with ``pdftotext -layout``; the legislation text was read from legislation.gov.uk
("Latest available (Revised)").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

__all__ = ["SOURCES", "SourceQuote"]

_RETRIEVED = date(2026, 11, 26)


@dataclass(frozen=True)
class SourceQuote:
    """One quoted passage of one published document."""

    source_id: str
    publisher: str
    title: str
    version: str
    url: str
    locator: str
    quote: str
    retrieved_on: date = _RETRIEVED


SOURCES: Final[dict[str, SourceQuote]] = {
    s.source_id: s
    for s in (
        SourceQuote(
            source_id="neso_wider_access_bm_min_unit",
            publisher="NESO",
            title="Wider Access to the Balancing Mechanism: Onboarding and Registration Guidance "
            "for Suppliers and Virtual Lead Parties",
            version="v0.8, 19/11/26",
            url="https://www.neso.energy/document/150276/download",
            locator="section 2.3 (Virtual Lead Party route); the same sentence ends section 2.2",
            quote="Under this route aggregation is permitted but is limited to Grid Supply Point "
            "Groups (Distribution Network Owner areas). The minimum size for a Balancing "
            "Mechanism Unit is 1MW.",
        ),
        SourceQuote(
            source_id="neso_wider_access_vlp_registration",
            publisher="NESO",
            title="Wider Access to the Balancing Mechanism: Onboarding and Registration Guidance "
            "for Suppliers and Virtual Lead Parties",
            version="v0.8, 19/11/26",
            url="https://www.neso.energy/document/150276/download",
            locator="section 2.3, Virtual Lead Party (VLP) Route to the Balancing Mechanism",
            quote="The Virtual Lead Party (VLP) route requires registration through the NESO "
            "Single Markets Platform (SMP). Parties will register Secondary BMUs with both NESO "
            "and Elexon.",
        ),
        SourceQuote(
            source_id="neso_dynamic_response_min_unit",
            publisher="NESO",
            title="Dynamic Response Services Provider Guidance",
            version="v.14, June 2026",
            url="https://www.neso.energy/document/276606/download",
            locator="Overview of the services (page 5)",
            quote="A minimum response capacity of 1 MW is required per response unit. [...] Sell "
            "orders may only be submitted in whole MWs. [...] Aggregation is possible when assets "
            "sit behind same GSP Group (subject to technical assurance being carried out on the "
            "risks associated with moving to GSP Group).",
        ),
        SourceQuote(
            source_id="neso_dfr_stacking_principles",
            publisher="National Grid ESO (now NESO)",
            title="Unlocking Stacking of BOAs with Frequency Response Services",
            version="undated; PDF created 15 Feb 2022",
            url="https://www.neso.energy/document/184466/download",
            locator="section 1, Service stacking principles",
            quote="It is not permitted to stack two different DFR services together - i.e. DC+DM, "
            "DM+DR, etc [...] Stacking of a single DFR with the BM is permitted",
        ),
        SourceQuote(
            source_id="neso_dfs_rules_unit_capacity",
            publisher="NESO",
            title="Demand Flexibility Service Procurement Rules",
            version="Version 5.0, effective from 18 May 2026",
            url="https://www.neso.energy/document/379391/download",
            locator="paragraph 4.2",
            quote="Each DFS Unit shall have a Registered Unit Capacity which shall be an amount "
            "(if not an integer, rounded to one decimal place) from and including zero point one "
            "(0.1) MW to and including one hundred (100) MW (unless otherwise specified by NESO).",
        ),
        SourceQuote(
            source_id="neso_dfs_rules_zone_aggregation",
            publisher="NESO",
            title="Demand Flexibility Service Procurement Rules",
            version="Version 5.0, effective from 18 May 2026",
            url="https://www.neso.energy/document/379391/download",
            locator="paragraph 4.3.2(k)",
            quote="the DFS Unit to which it is allocated [...] provided always that a DFS Unit "
            "shall only have allocated to it Unit Meter Points located in the same DFS Zone",
        ),
        SourceQuote(
            source_id="neso_dfs_rules_occupier_agreement",
            publisher="NESO",
            title="Demand Flexibility Service Procurement Rules",
            version="Version 5.0, effective from 18 May 2026",
            url="https://www.neso.energy/document/379391/download",
            locator="paragraph 4.3.2(j)",
            quote="a timestamp (in format HH:MM:SS) and date on which the Registered Service "
            "Provider obtained agreement from the owner and/or occupier of the Premises to its "
            "participation in the Demand Flexibility Service",
        ),
        SourceQuote(
            source_id="neso_dfs_rules_no_bm_unit",
            publisher="NESO",
            title="Demand Flexibility Service Procurement Rules",
            version="Version 5.0, effective from 18 May 2026",
            url="https://www.neso.energy/document/379391/download",
            locator="paragraph 4.7.6",
            quote="[Each of the Unit Meter Points ...] shall not form part of a BM Unit except a "
            "Supplier Base BM Unit;",
        ),
        SourceQuote(
            source_id="neso_dfs_rules_cm_permitted",
            publisher="NESO",
            title="Demand Flexibility Service Procurement Rules",
            version="Version 5.0, effective from 18 May 2026",
            url="https://www.neso.energy/document/379391/download",
            locator="paragraph 4.7.8(a)",
            quote="may:- (a) comprise (wholly or partly, including by virtue of being a Sub-Meter "
            "associated with a Boundary Meter which is) a CMU which is the subject of a Capacity "
            "Agreement unless otherwise stated (or to the extent and upon such conditions as NESO "
            "may stipulate) in the DFS Stacking List",
        ),
        SourceQuote(
            source_id="neso_dfs_rules_stacking_list_bar",
            publisher="NESO",
            title="Demand Flexibility Service Procurement Rules",
            version="Version 5.0, effective from 18 May 2026",
            url="https://www.neso.energy/document/379391/download",
            locator="paragraph 4.7.9",
            quote="shall not be registered with NESO or with any Public Distribution System "
            "Operator (or any other third party) to provide (whether solely, partly, or in "
            "conjunction with any other Plant or Apparatus and including where providing via an "
            "aggregator) any Balancing Service to NESO or any DNO Flexibility Service to that "
            "Public Distribution System Operator (or similar service to another entity) including "
            "participation in an Active Network Management Scheme where so prohibited by the DFS "
            "Stacking List.",
        ),
        SourceQuote(
            source_id="neso_dfs_stacking_list_v2",
            publisher="NESO",
            title="Demand Flexibility Service (DFS) Stacking List",
            version="v.2 (April 2026)",
            url="https://www.neso.energy/document/380211/download",
            locator="Table 1, Permitted stacking with DFS",
            quote="Y means stacking is available; N means it is not possible; and SD indicates "
            "stacking is only possible when providing a service in the same direction [...] "
            "CM: Jumping N/A, Splitting Y-SD, Co-delivery Y-SD. DSO services (PR, SU, OU, SA+OU, "
            "VA OU (2&15), VA OU (DA&WA)): Jumping Y, Splitting Y-SD, Co-delivery Y-SD. LCM: "
            "Jumping Y, Splitting Y-SD, Co-delivery N. [The table names no Dynamic Containment, "
            "Moderation or Regulation service, no Balancing Mechanism and no wholesale trading.]",
        ),
        SourceQuote(
            source_id="si_2014_2043_reg_15_4",
            publisher="UK Government (legislation.gov.uk)",
            title="The Electricity Capacity Regulations 2014 (SI 2014/2043)",
            version="regulation 15, latest available (revised); '1MW' substituted 4.7.2020 by SI "
            "2020/697",
            url="https://www.legislation.gov.uk/uksi/2014/2043/regulation/15",
            locator="regulation 15(4)",
            quote="(a) in the case of a generating CMU or an interconnector CMU, that the "
            "connection capacity of the CMU is equal to or greater than 1MW (the “minimum "
            "capacity threshold”); or (b) in the case of a demand side response CMU, that the "
            "DSR capacity of the CMU is equal to or greater than the minimum capacity threshold.",
        ),
    )
}
