"""The grid-code texts the screen is judged against, and the numbers it takes from them.

Data rule (WP-SITE-06; D-NEW-SITE06-5): every limit, threshold and study parameter comes from
a retrieved public document, held here as a `ClauseConstant` with its clause id. Only numbers
and clause ids are transcribed: no clause wording is reproduced, because each text carries a
copyright notice. The PDFs are cited and hashed, **not** committed (the `gridq.thermal.sources`
precedent).

Sources, each retrieved on 2026-11-26 by direct download:

- ``G99_ISSUE_2``: ENA EREC G99 Issue 2 (10 March 2025), from the Distribution Code site.
- ``P28_ISSUE_2``: ENA EREC P28 Issue 2 (2018). ENA sells it; the copy used is the full text NESO
  publishes on its own site. Its amendments table lists only Issue 2 (2018).
- ``P28_DCRP_REPORT``: DCRP/MP/18/01 report to the Authority (17 May 2018), which quotes P28
  Table 4. Corroborating only; ``P28_ISSUE_2`` is primary.
- ``GRID_CODE_I6R33``: NESO Serviced Grid Code Issue 6 Revision 33 (11 June 2025), the European
  Connection Conditions revision (ECC.6.3.15 fault ride through, ECC.6.3.16 fast fault current).

**Currency is unconfirmed.** Whether a later G99 amendment, P28 issue or Grid Code revision
changes a cited clause was not established from the documents themselves, so every source is
``currency="unconfirmed"`` and every verdict says so. `PERSON_ONLY_ASKS` names who closes each
gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Final, Literal

from gridq.ops.dossier_model import AskHolder, DossierAsk
from gridq.thermal.sources import Source

__all__ = [
    "CONSTANTS",
    "G99_ISSUE_2",
    "GRID_CODE_I6R33",
    "OWNER_NAME",
    "P28_DCRP_REPORT",
    "P28_ISSUE_2",
    "PERSON_ONLY_ASKS",
    "SOURCES",
    "ClauseConstant",
    "Currency",
    "SourceId",
    "constant",
]

SourceId = Literal["G99_ISSUE_2", "P28_ISSUE_2", "P28_DCRP_REPORT", "GRID_CODE_I6R33"]
Currency = Literal["unconfirmed", "confirmed"]

#: The named person who closes the person-only asks (the owner, the sole contributor).
OWNER_NAME: Final = "Olly"

_COPYRIGHT = (
    "The file carries an ENA copyright notice (no reproduction without consent): it is cited and "
    "hashed, not committed, and only numbers and clause ids are transcribed."
)

G99_ISSUE_2: Final = Source(
    source_id="G99_ISSUE_2",
    reference=(
        "Energy Networks Association (2025). Engineering Recommendation G99 Issue 2, 10 March "
        "2025. Requirements for the connection of generation equipment in parallel with public "
        "distribution networks on or after 27 April 2019."
    ),
    url="https://dcode.org.uk/assets/250307ena-erec-g99-issue-2-(2025).pdf",
    retrieved_on="2026-11-26",
    sha256="244c1d5ff4d9dfdce42d7d685ba3c8fd4cf805751c198420ae9e4a713317f5a4",
    access_note="Free download from the Distribution Code site (dcode.org.uk). " + _COPYRIGHT,
)

P28_ISSUE_2: Final = Source(
    source_id="P28_ISSUE_2",
    reference=(
        "Energy Networks Association (2018). Engineering Recommendation P28 Issue 2. Voltage "
        "fluctuations and the connection of disturbing equipment to transmission systems and "
        "distribution networks in the United Kingdom."
    ),
    url="https://www.neso.energy/document/127521/download",
    retrieved_on="2026-11-26",
    sha256="ea9efc13ec22192c6e406efa9b02677eca078b84b47d1b291ef3c69f1c603470",
    access_note=(
        "ENA sells EREC P28; this is the full Issue 2 text that NESO publishes on its own site. "
        "Its amendments table lists only 'Issue 2, 2018, major technical revision'. " + _COPYRIGHT
    ),
)

P28_DCRP_REPORT: Final = Source(
    source_id="P28_DCRP_REPORT",
    reference=(
        "Distribution Code Review Panel (2018). DCRP/MP/18/01 Report to Authority: Revision to "
        "Engineering Recommendation P28, 17 May 2018."
    ),
    url="https://www.neso.energy/document/124071/download",
    retrieved_on="2026-11-26",
    sha256="d27462909e2ced66966eee2442e18de14d0d4264e90a51fe303f7a626d247055",
    access_note=(
        "Public modification report that quotes P28 Issue 2 Table 4. Corroborating source only; "
        "P28_ISSUE_2 is primary."
    ),
)

GRID_CODE_I6R33: Final = Source(
    source_id="GRID_CODE_I6R33",
    reference=(
        "National Energy System Operator (2025). The Serviced Grid Code, Issue 6 Revision 33, "
        "11 June 2025: European Connection Conditions (ECC.6.3.15 Fault Ride Through; "
        "ECC.6.3.16 Fast Fault Current Injection)."
    ),
    url="https://www.neso.energy/document/362676/download",
    retrieved_on="2026-11-26",
    sha256="ead42d0d60643ebfa8974128b2c1a8b2252e8a00f7984683efd326da0516e3e8",
    access_note=(
        "Public revision package on NESO's site. Later revisions may exist; the clauses are cited "
        "by reference only, and no Grid Code number is transcribed."
    ),
)

SOURCES: Final[dict[str, Source]] = {
    s.source_id: s for s in (G99_ISSUE_2, P28_ISSUE_2, P28_DCRP_REPORT, GRID_CODE_I6R33)
}

#: Every source is unconfirmed until the person-only asks below are closed.
CURRENCY: Final[dict[str, Currency]] = {sid: "unconfirmed" for sid in SOURCES}


@dataclass(frozen=True)
class ClauseConstant:
    """One number transcribed from a retrieved document, with the clause it was read from."""

    key: str
    value: Fraction
    unit: str
    source_id: SourceId
    clause_id: str


def _c(
    key: str, value: Fraction | int | str, unit: str, source_id: SourceId, clause: str
) -> ClauseConstant:
    return ClauseConstant(key, Fraction(value), unit, source_id, clause)


_G99: Final = "G99_ISSUE_2"
_P28: Final = "P28_ISSUE_2"

#: The closed table of transcribed numbers. ``tests/test_ramp_ridethrough_sources.py`` pins
#: every (key, clause, value); a silent edit fails it.
CONSTANTS: Final[dict[str, ClauseConstant]] = {
    c.key: c
    for c in (
        # ----- G99 Section 4, Terms and definitions: Power Generating Module types
        _c("g99.type_a.min_capacity_w", 800, "W", _G99, "4 (Type A)"),
        _c("g99.type_b.min_capacity_w", 1_000_000, "W", _G99, "4 (Type B)"),
        _c("g99.type_c.min_capacity_w", 10_000_000, "W", _G99, "4 (Type C)"),
        _c("g99.type_d.min_capacity_w", 50_000_000, "W", _G99, "4 (Type D)"),
        _c("g99.type_d.min_connection_kv", 110, "kV", _G99, "4 (Type D)"),
        # ----- G99 6.1.3.1: the Grid Code 'Large' thresholds and Embedded Medium Power Station
        _c("g99.grid_code_large.north_scotland_w", 10_000_000, "W", _G99, "6.1.3.1"),
        _c("g99.grid_code_large.south_scotland_w", 30_000_000, "W", _G99, "6.1.3.1"),
        _c("g99.grid_code_large.england_wales_w", 100_000_000, "W", _G99, "6.1.3.1"),
        _c("g99.embedded_medium.england_wales_w", 50_000_000, "W", _G99, "6.1.3.1"),
        # ----- G99 Table 12.2 (12.3.1.6): Type B Power Park Modules, voltage against time
        _c("g99.t12_2.u_ret_pu", "0.1", "pu", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.u_clear_pu", "0.10", "pu", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.u_rec1_pu", "0.10", "pu", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.u_rec2_pu", "0.85", "pu", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.t_clear_s", "0.14", "s", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.t_rec1_s", "0.14", "s", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.t_rec2_s", "0.14", "s", _G99, "12.3.1.6 Table 12.2"),
        _c("g99.t12_2.t_rec3_s", "2.2", "s", _G99, "12.3.1.6 Table 12.2"),
        # ----- G99 Table 13.5 (13.3.1.8): Type C and D PPMs connected below 110 kV
        _c("g99.t13_5.u_ret_pu", "0.1", "pu", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.u_clear_pu", "0.10", "pu", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.u_rec1_pu", "0.10", "pu", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.u_rec2_pu", "0.85", "pu", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.t_clear_s", "0.14", "s", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.t_rec1_s", "0.14", "s", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.t_rec2_s", "0.14", "s", _G99, "13.3.1.8 Table 13.5"),
        _c("g99.t13_5.t_rec3_s", "2.2", "s", _G99, "13.3.1.8 Table 13.5"),
        # ----- G99 Table 13.6 (13.3.1.10): Type D PPMs connected at or above 110 kV
        _c("g99.t13_6.u_ret_pu", 0, "pu", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.u_clear_pu", 0, "pu", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.u_rec1_pu", 0, "pu", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.u_rec2_pu", "0.85", "pu", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.t_clear_s", "0.14", "s", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.t_rec1_s", "0.14", "s", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.t_rec2_s", "0.14", "s", _G99, "13.3.1.10 Table 13.6"),
        _c("g99.t13_6.t_rec3_s", "2.2", "s", _G99, "13.3.1.10 Table 13.6"),
        # ----- G99 12.3.1.7(e) / 13.3.1.11(e): post-fault active power recovery
        _c("g99.frt.fault_duration_s", "0.14", "s", _G99, "12.3.1 / 13.3.1"),
        _c("g99.frt.recovery_fraction", "0.9", "fraction", _G99, "12.3.1.7(e)"),
        _c("g99.frt.recovery_within_s", "0.5", "s", _G99, "12.3.1.7(e)"),
        # ----- G99 simulation study cases (Annex B.4.4 Type B; Annex C.7.5 Type C and D)
        _c("g99.b4_1.ppm_retained_voltage", "0.10", "pu", _G99, "B.4.4.2 Table B.4.1"),
        _c("g99.c7_1.ppm_below_110kv_retained_voltage", "0.10", "pu", _G99, "C.7.5.2 Table C.7.1"),
        _c("g99.c7_1.ppm_d_above_110kv_retained_voltage", 0, "pu", _G99, "C.7.5.2 Table C.7.1"),
        _c("g99.study.generic_min_short_circuit_va", 50_000_000, "VA", _G99, "B.4.4.3 / C.7.5.3"),
        # ----- P28 Table 4 (5.3.2), 6.2 and footnote 14: rapid voltage change planning levels
        _c("p28.steady_state_change_limit_pct", 3, "%", _P28, "5.3.2 Table 4; 6.2; footnote 14"),
        _c("p28.cat1.vmax_100ms_pct", 6, "%", _P28, "5.3.2 Table 4 NOTE 1"),
        _c("p28.cat2.vmax_decrease_pct", 10, "%", _P28, "5.3.2 Table 4 NOTE 3"),
        _c("p28.cat2.vmax_increase_pct", 6, "%", _P28, "5.3.2 Table 4 NOTE 4"),
        _c("p28.cat2.max_events_per_month", 4, "events", _P28, "5.3.2 Table 4"),
        _c("p28.cat3.vmax_decrease_pct", 12, "%", _P28, "5.3.2 Table 4 NOTE 5"),
        _c("p28.cat3.vmax_increase_pct", 6, "%", _P28, "5.3.2 Table 4 NOTE 6"),
        _c("p28.cat3.max_events_per_3_months", 1, "events", _P28, "5.3.2 Table 4"),
        _c("p28.cat23.min_separation_s", 600, "s", _P28, "5.3.2"),
        _c("p28.rvc.remanent_flux_pu", "0.5", "pu", _P28, "6.4.1(b); footnote 35"),
    )
}


def constant(key: str) -> ClauseConstant:
    """The transcribed constant ``key`` (``KeyError`` when it is not in the table)."""
    return CONSTANTS[key]


def _owner_ask(request: str) -> DossierAsk:
    return DossierAsk.make(
        request=request,
        holder=AskHolder.NEURA_OWNER,
        holder_name=OWNER_NAME,
        scope="dossier",
    )


#: Person-only asks: things only a person can close (no network fetch, no purchase made here).
PERSON_ONLY_ASKS: Final[tuple[DossierAsk, ...]] = (
    _owner_ask(
        "Confirm through the ENA document catalogue that EREC P28 Issue 2 (2018) is the current "
        "issue, and that the copy NESO publishes "
        "(https://www.neso.energy/document/127521/download, sha256 ea9efc13...a470) may be "
        "relied on in place of a purchased copy."
    ),
    _owner_ask(
        "Confirm that no GB amendment of EREC G99 after Issue 2 (10 March 2025) changes clauses "
        "4 (Type definitions), 6.1.3.1, 10.3.4, 12.3, 12.6, 13.3, 13.6, B.4.4 or C.7.5."
    ),
    _owner_ask(
        "Confirm the current Grid Code revision against Issue 6 Revision 33 (11 June 2025), and "
        "that ECC.6.3.15 and ECC.6.3.16 are unchanged."
    ),
    _owner_ask(
        "Obtain ENA's written consent before any G99 or P28 clause text or table is reproduced in "
        "a customer-facing report (the screen holds numbers and clause ids only)."
    ),
)
