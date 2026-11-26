"""GB value streams — the GB **content** module (the only place GB specifics live).

The generic core (``interface.py``, ``gates.py``, ``streams.py``) never names a market.
This module wires the generic gate predicates to the GB SiteModel fields and the GB
assumption sections, and implements each stream's **value function** (£/yr over a dispatch
schedule, reading numbers from the assumption library). A new market would add its own
``<market>_streams.py`` here — it would **not** add a ``market == "GB"`` branch to the core.

The GB stack, how each stream gates, and what its value function prices over:

| Stream                    | Gate (data / context)          | Priced case · value (gb_v0_1)    |
|---------------------------|--------------------------------|----------------------------------|
| PPA / bill savings        | usable tariff + load           | floor (firm) · itemised by parts |
| Tariff arbitrage          | usable multi-rate tariff       | floor (firm) · £/MW/yr × power    |
| DUoS red-band avoidance   | DUoS rate data                 | floor (firm) · £/MW/yr × power    |
| MIC / ASC headroom        | connection limit + peak        | floor (where binds) · £/MW/yr×pw  |
| Capacity Market           | de-rated deliverability (ctx)  | contracted · £/kW/yr × derated kW |
| Local flexibility (Piclo) | open tender in DNO area (ctx)  | contracted · £/MWh × tender MWh   |
| Wholesale + BM via P415   | RTM partner + export consents  | gated upside · £0 placeholder     |
| Ancillary DC/DM/DR        | RTM partner + min portfolio MW | gated upside (decay) · £0         |

Nothing here invents revenue or data: a number is always an assumption-library value ×
a dispatched quantity, carries the assumption's (placeholder/needs_review) status as a
not-bankable warning, and is computed only over a supplied dispatch schedule. A missing
input becomes a missing requirement on the gate; gated upside is always conditional, never
firm; the deliverability/tender/portfolio facts the SiteModel cannot hold arrive via the
generic ``GateContext`` (decision D12), never a SiteModel field or a market branch.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus
from gridq.governance.dpa_guard import DpaEvidenceMissing
from gridq.schemas import AvailabilityCommitment, MarketStream, SiteModel
from gridq.value_stack import gates
from gridq.value_stack.dispatch import DispatchSummary
from gridq.value_stack.interface import (
    DispatchConstraintDescriptor,
    GateContext,
    GateResult,
    GateStatus,
    RequiredInput,
    Scenario,
)
from gridq.value_stack.streams import ContractedStream, FloorStream, GatedUpsideStream, PricedValue

# ---------------------------------------------------------------------------
# Shared dispatch-constraint descriptors (mechanic-specific; values come from the
# SiteModel/assumptions at solve time — never fabricated here).
# ---------------------------------------------------------------------------

_CONNECTION_CHARGE_LIMIT = DispatchConstraintDescriptor(
    constraint_id="connection_charge_limit",
    description="Battery charge load counts against the import connection; charge kW is "
    "capped at the binding connection limit (min of MIC and ASC).",
    kind="connection_limit",
    parameter_source="connection.mic_kw / connection.asc_kw",
)
_WARRANTY_THROUGHPUT_CAP = DispatchConstraintDescriptor(
    constraint_id="warranty_throughput_cap",
    description="Annual throughput (full-cycle-equivalents) is hard-capped by the warranty; "
    "overcycling is not available to the optimiser.",
    kind="throughput_cap",
    parameter_source="warranty_cycle_limits.max_cycles_per_year_by_duration_h",
)
_DUOS_RED_WINDOW = DispatchConstraintDescriptor(
    constraint_id="duos_red_band_window",
    description="DUoS red-band avoidance only accrues by discharging across the red-band "
    "time windows (post-TCR treatment; no Triad story).",
    kind="availability_window",
    parameter_source="tariff_stack.duos_red_rate (band schedule)",
)
_CM_DELIVERABILITY = DispatchConstraintDescriptor(
    constraint_id="cm_deliverability",
    description="The asset must hold its de-rated capacity in every contract year at "
    "degraded capability (sized day-one for end-of-life; no augmentation).",
    kind="deliverability",
    parameter_source="capacity_market.de_rating_factor_by_duration_h + degradation",
)
_CM_HELD_SOE = DispatchConstraintDescriptor(
    constraint_id="cm_held_state_of_energy",
    description="A state-of-energy band must be held available to deliver the de-rated "
    "capacity across a stress event for the contract duration.",
    kind="held_state_of_energy",
    parameter_source="capacity_market.de_rating_factor_by_duration_h (duration) + dispatch SoE",
)
_TENDER_WINDOW = DispatchConstraintDescriptor(
    constraint_id="local_flex_tender_window",
    description="Local-flex value accrues only by delivering within the awarded tender's "
    "availability/utilisation windows.",
    kind="availability_window",
    parameter_source="local_flex_piclo (tender schedule)",
)
_EXPORT_LIMIT = DispatchConstraintDescriptor(
    constraint_id="export_limit",
    description="Export-side dispatch is bounded by the export connection capacity (MEC).",
    kind="export_limit",
    parameter_source="connection.mec_kw",
)
_DFS_SERVICE_WINDOW = DispatchConstraintDescriptor(
    constraint_id="dfs_service_window",
    description="DFS pays only over the contracted settlement periods of a published Service "
    "Requirement, measured against a baseline; delivery outside a called period earns nothing.",
    kind="availability_window",
    parameter_source="dfs (Service Requirement periods + baseline methodology)",
)
_ANCILLARY_AVAILABILITY = DispatchConstraintDescriptor(
    constraint_id="ancillary_availability_headroom",
    description="DC/DM/DR requires response headroom (reserved power + a held state-of-energy "
    "band) available across contracted windows, with capability decay over life.",
    kind="availability_window",
    parameter_source="ancillary_services (response spec) + degradation",
)


# ---------------------------------------------------------------------------
# Market-entry eligibility thresholds — GB CONTENT (D-92).
#
# Consent authorises pursuit; it can NEVER manufacture eligibility. Before this, a consented
# stream shortcut straight to CONTRACTED with no size check, so a 300 kW site could carry
# contracted Balancing Mechanism revenue into the P90 series the debt is sized on, for a market
# it cannot physically enter. Every number below is sourced; the generic predicates that consume
# them live in `gates.py`, which holds no market numbers.
# ---------------------------------------------------------------------------

#: Minimum BM Unit size. `docs/bess_constants_register.md` row "Minimum BM Unit size and
#: aggregation scope (Wider Access)": "1 MW minimum BM Unit size; aggregation permitted only within
#: Grid Supply Point Groups (DNO areas)". The Balancing Reserve row ("Balancing Reserve eligibility
#: and payment structure": "Must be a BM Unit; minimum 1 MW contracted capacity") is the same bound.
_MIN_BM_UNIT_MW = 1.0

#: Minimum response-unit size for the six dynamic response products. Register row "Dynamic Response
#: (DC/DM/DR) minimum unit size": "A minimum response capacity of 1 MW is required per response
#: unit", maximum 100 MW, applying to both BM and non-BM registered assets — "the gate that excludes
#: sub-1 MW BTM assets from all six dynamic response products". Aggregation is permitted only within
#: the same GSP Group (register row "Response service aggregation geographic restriction").
_MIN_DYNAMIC_RESPONSE_UNIT_MW = 1.0

#: Markets whose sell orders are accepted only in WHOLE MW. Register row "EAC / Dynamic Response
#: order granularity": "Sell orders may only be submitted in whole MWs"; EAC child orders accept
#: "any integer volume"; and "For BM, MW values must be declared as integers." Consequence recorded
#: in the same row: "Combined with the 1 MW floor this makes the ancillary-sellable quantity an
#: integer in {0, 1, 2, ...}. Fractional nameplate above an integer boundary is unsellable
#: into EAC."
WHOLE_MW_ORDER_STREAMS: frozenset[MarketStream] = frozenset(
    {MarketStream.BALANCING_MECHANISM, MarketStream.ANCILLARY_DC_DM_DR}
)

#: Minimum Capacity Market prequalification capacity. `docs/bess_constants_register.md` row
#: "Minimum capacity threshold for prequalification": **1 MW**, in "MW of connection capacity
#: (generating/interconnector CMU) or DSR capacity (DSR CMU)", from The Electricity Capacity
#: Regulations 2014 (SI 2014/2043) regulation 15, "all CMU types". The register's own note is the
#: consequence this gate enforces: "Directly excludes standalone BTM batteries below 1 MW from the
#: CM unless aggregated into a multi-site DSR CMU."
#:
#: **Basis caveat — the threshold is on CONNECTION (or DSR) capacity, NOT de-rated capacity.** The
#: register row says so explicitly. So the predicate is fed the connection bound at gate time and
#: the SOLVED power at engine time, and must never be tested against ``deliverable_capacity_mw``
#: (nameplate × de-rating), which for a 2 h battery is ~0.22x the connection capacity and would bar
#: eligible units.
_MIN_CM_PREQUALIFICATION_MW = 1.0

#: The armed minimum-size gate per stream. A stream ABSENT from this map has **no armed gate** —
#: `gates.market_entry_eligibility` then returns route ``'not_armed'`` and states that eligibility
#: was not asserted, rather than inventing a threshold or silently blocking.
#:
#: Every entry is a sourced register row, never a plausible-looking number: arming a gate that is
#: not in the register would delete real revenue, and leaving a sourced one unarmed books revenue
#: the asset is legally barred from earning. Both are defects; only the second is silent.
_MIN_UNIT_MW_BY_STREAM: dict[MarketStream, float] = {
    MarketStream.BALANCING_MECHANISM: _MIN_BM_UNIT_MW,
    MarketStream.ANCILLARY_DC_DM_DR: _MIN_DYNAMIC_RESPONSE_UNIT_MW,
    MarketStream.CAPACITY_MARKET: _MIN_CM_PREQUALIFICATION_MW,
}

#: Streams whose aggregation route is a **multi-site DSR CMU**, not response/BM aggregation within
#: one GSP Group. The two routes have different rules, so the caveats must not be swapped.
_DSR_CMU_AGGREGATION_STREAMS: frozenset[MarketStream] = frozenset({MarketStream.CAPACITY_MARKET})

#: The labelled data ask for a stream whose size gate cannot be armed from the register.
ELIGIBILITY_DATA_ASK = (
    "DATA ASK — no minimum-capacity threshold for this market is recorded in "
    "docs/bess_constants_register.md, so NO size-eligibility gate was applied and eligibility is "
    "UNASSERTED. It was not invented: source the market's minimum capacity into a new assumption "
    "release before relying on this stream's eligibility."
)


def eligibility_threshold_mw(stream: MarketStream) -> float | None:
    """The armed minimum-size threshold (MW) for ``stream``, or ``None`` when none is sourced."""
    return _MIN_UNIT_MW_BY_STREAM.get(stream)


def market_entry_verdict(
    stream: MarketStream,
    *,
    unit_mw: float | None,
    portfolio_mw: float | None,
) -> gates.EligibilityVerdict:
    """Can ``stream`` be entered at ``unit_mw`` (or via a ``portfolio_mw`` aggregate)? (D-92)

    The single eligibility entry point for BOTH pricing paths: the proxy gate calls it with the
    site's connection-bounded upper bound (`gates.connection_bounded_response_mw`), and the engine
    path calls it with the SOLVED unit power. Same predicate, same sourced thresholds, so the two
    paths cannot disagree about **who may enter** a market.

    **They can still disagree about money, and today they do** — the claim that they "cannot
    disagree" was true only of entry and was read as covering the £. The two paths price BM and
    DC/DM/DR off different volume bases (:func:`market_sellable_volume_mw` is this file's single
    sourced definition; `optimisation.valuation._derated_deliverable_mw` applies a Capacity Market
    duration de-rating to the same quantity) and apply the capability-decay and scenario-price
    factors differently. Neither divergence is a difference of opinion about eligibility, and
    neither is fixed by this function.

    **Capacity Market callers must not use this function on a sizing output.** Its threshold is a
    property of the CMU's *evidenced* connection or DSR capacity, so the CM has its own entry
    point, :func:`capacity_market_entry_verdict`, which refuses to read eligibility out of a sized
    power the connection record does not support.
    """
    return gates.market_entry_eligibility(
        unit_mw=unit_mw,
        portfolio_mw=portfolio_mw,
        threshold_mw=eligibility_threshold_mw(stream),
        whole_mw_orders=stream in WHOLE_MW_ORDER_STREAMS,
    )


# ---------------------------------------------------------------------------
# The sellable volume — N-2 / N-3.
#
# Each market's £ is a rate × a VOLUME, and the volume basis is market content, not a modelling
# convenience. Two facts about the two BTM response markets, both sourced:
#
# - They contract in **plain MW of deliverable power**, in whole-MW sell orders. Neither has a
#   Capacity Market duration de-rating: the CM factor is a scaled EFC used for capacity-adequacy
#   accounting in the CM alone (`docs/bess_constants_register.md`, "Capex thresholds determining
#   agreement length" and D41's de-rating rows), and no register row applies it to a BM offer or an
#   EAC response order. Applying it to them de-rates a volume that was never de-rateable and then
#   truncates the result to whole MW, which turns the wrong basis into a cliff: a genuinely eligible
#   4 MW unit books £0 because 4 × 0.2094 = 0.8376 truncates to zero (measured).
# - The de-ratings that DO apply to DC/DM/DR are the availability factor F and the performance
#   factor K, applied separately and explicitly by :func:`eac_response_value` (D-158). Charging a CM
#   duration factor as well would be a third, unsourced haircut on the same MW.
#
# `EligibilityVerdict.sellable_mw` is the single definition — "the quantity actually offerable after
# any whole-MW order granularity" — and it already honours the route distinction the aggregated case
# needs (a fractional unit IS offerable inside an aggregate, so that route is not truncated).
# ---------------------------------------------------------------------------

#: The sourced volume basis for each market that sells a MW. Emitted with the £ so a reader can see
#: which quantity was priced, and on what authority, rather than inferring it from the number.
_VOLUME_BASIS_NOTE: dict[MarketStream, str] = {
    MarketStream.BALANCING_MECHANISM: (
        "VOLUME BASIS: the unit's deliverable power in MW, truncated to whole MW. Register rows "
        "'Minimum BM Unit size and aggregation scope (Wider Access)' ('minimum 1 MW contracted "
        "capacity') and 'EAC / Dynamic Response order granularity' ('For BM, MW values must be "
        "declared as integers'). NOT de-rated: the Capacity Market duration de-rating is a scaled "
        "EFC for capacity-adequacy accounting and no sourced row applies it to a BM offer."
    ),
    MarketStream.ANCILLARY_DC_DM_DR: (
        "VOLUME BASIS: the unit's response capability in MW, truncated to whole MW. Register row "
        "'EAC / Dynamic Response order granularity' ('Sell orders may only be submitted in whole "
        "MWs'). The de-ratings that apply to this product are the availability factor F and the "
        "performance factor K, applied separately (D-158) — NOT the Capacity Market duration "
        "de-rating, which would be a third unsourced haircut on the same MW."
    ),
}


def sellable_volume_mw(
    stream: MarketStream,
    *,
    unit_mw: float | None,
    portfolio_mw: float | None,
) -> tuple[float | None, list[str]]:
    """The MW ``stream`` may actually SELL at ``unit_mw`` — ``None`` ⇒ nothing is offerable.

    **The single volume definition for both pricing paths**, in the shape the local-flex call-rate
    fix established (one function here, imported by `gridq.optimisation.valuation`, so the two
    cannot drift). It is deliberately the same object the eligibility verdict already computes:
    entry and volume are one question asked once, not two answers that can disagree.

    Returns ``(mw, notes)``. ``None`` when the market cannot be entered at this size, or when the
    offerable quantity truncates away entirely — the caller prices **£0**, not a smaller number,
    because a market the asset cannot sell into is worth nothing rather than worth less.
    """
    verdict = market_entry_verdict(stream, unit_mw=unit_mw, portfolio_mw=portfolio_mw)
    notes = list(verdict.reasons) + aggregation_route_notes(verdict, stream)
    if verdict.route == "not_armed":
        notes.append(ELIGIBILITY_DATA_ASK)
    basis = _VOLUME_BASIS_NOTE.get(stream)
    if basis is not None:
        notes.append(basis)
    if not verdict.eligible or verdict.sellable_mw is None or verdict.sellable_mw <= 0:
        return None, notes
    return verdict.sellable_mw, notes


def aggregation_route_notes(verdict: gates.EligibilityVerdict, stream: MarketStream) -> list[str]:
    """Extra caveats owed by the aggregation route — which differs by market, so ``stream`` binds.

    Two different GB aggregation routes clear a 1 MW minimum, with different rules, and their
    caveats must never be swapped:

    - **Capacity Market**: a multi-site **DSR CMU**. The register's row for the 1 MW threshold says
      a sub-1 MW BTM battery is excluded "unless aggregated into a multi-site DSR CMU", and the DSR
      route carries its own obligations (Proven vs Unproven DSR, credit cover, DSR testing) rather
      than a GSP-group co-location rule.
    - **Balancing Mechanism / DC-DM-DR**: aggregation permitted only within one **GSP Group**.
    """
    if verdict.route != "aggregated":
        return []
    if stream in _DSR_CMU_AGGREGATION_STREAMS:
        return [
            "AGGREGATION ASSUMED INTO A MULTI-SITE DSR CMU: a sub-1 MW behind-the-meter battery "
            "clears the Capacity Market's 1 MW prequalification threshold only inside an "
            "aggregated DSR CMU (register row 'Minimum capacity threshold for prequalification': "
            "'Directly excludes standalone BTM batteries below 1 MW from the CM unless aggregated "
            "into a multi-site DSR CMU'). The supplied portfolio MW carries no evidence that such "
            "a CMU exists, so this is an unverified assumption, not a confirmed fact.",
            "THE DSR ROUTE IS NOT FREE: a DSR CMU brings its own obligations the de-rating factor "
            "does not capture — Proven DSR requires three evidenced dispatch periods with a DNO "
            "Line Loss Factor letter (register: 'effectively impossible for a greenfield BTM "
            "battery'), forcing the Unproven DSR route with credit cover, and the DSR de-rating "
            "factor (AABS) differs from the storage EFC curve used to size this figure. Neither "
            "adjustment is applied here, so an aggregated CM figure is GROSS of both, and gross of "
            "the aggregator's own cut.",
        ]
    return [
        "AGGREGATION ASSUMED WITHIN ONE GSP GROUP: GB permits response/BM aggregation only where "
        "the assets sit behind the same Grid Supply Point Group (register rows 'Response service "
        "aggregation geographic restriction' and 'Minimum BM Unit size and aggregation scope "
        "(Wider Access)'). The supplied portfolio MW carries no GSP-group evidence, so this is an "
        "unverified assumption, not a confirmed fact.",
        "AGGREGATOR TAKE NOT DEDUCTED: revenue reached via an aggregated portfolio is earned "
        "through a route-to-market partner that takes a share, and no sourced aggregator-cut key "
        "exists for this market (local_flex_piclo.aggregator_cut_pct is local-flex-scoped and is "
        "NOT borrowed here). This figure is therefore GROSS of the aggregator's cut.",
    ]


# ---------------------------------------------------------------------------
# Capacity Market prequalification capacity — N-1.
#
# `SI 2014/2043` reg 15(4) puts the 1 MW minimum on **"the connection capacity of the CMU"**
# (limb (a): generating and interconnector CMUs) or **"the DSR capacity of the CMU"** (limb (b)).
# `docs/bess_constants_register.md` row "Minimum capacity threshold for prequalification" carries
# the same basis and its own explicit caveat: "Applies to connection/DSR capacity, not de-rated
# capacity."
#
# That quantity is a property of the CMU, evidenced by the site's connection record. It is NOT the
# sized battery's rated power — and reading it off a sizing output produced the inverse of D-92's
# own principle: a site recording NO connection capacity and NO export capacity, therefore with no
# evidenced connection at all and no right to export a single kW, was judged "eligible STANDALONE"
# off a 2 MW sizing outcome and booked contracted Capacity Market revenue into the priced floor the
# DSCR covenant is measured on. D45's no-null amendment is explicit about the direction that must
# never be silent: "a null on the pricing path is a defect to fix, not a state to report", and a
# null that ADDS revenue is the worst case of it.
# ---------------------------------------------------------------------------

#: How a CM verdict describes the quantity it tested. The threshold is neither the host site's MIC
#: nor the battery's rating, so the reason line must not present either as if it were the tested
#: fact (`gates.DEFAULT_QUANTITY_LABEL` is the sized-unit wording and would misreport it).
_CM_QUANTITY_LABEL = "the CMU's evidenced connection/DSR capacity of"

#: Verdict route for "no connection or DSR capacity is evidenced or declared at all". Distinct from
#: `'unproven'` (the generic no-size-evidence route) because it is a **site-data** absence with a
#: named data ask, not an unsupplied argument, and distinct from `'below_threshold'` because
#: nothing was disproven — the input needed to answer the question is simply missing.
CM_UNEVIDENCED_ROUTE = "unevidenced_capacity"

#: The data ask owed when a site evidences no connection capacity on either CM route.
CM_CAPACITY_DATA_ASK = (
    "DATA ASK — record the site's connection capacity (connection.mic_kw or connection.asc_kw for "
    "the DSR route) and its export capacity (connection.mec_kw for the generating route), or "
    "declare one offline as a D45 `derived_input` with its basis and confirmation ask. Until then "
    "the Capacity Market question cannot be answered for this site, and it is REFUSED rather than "
    "answered favourably: an absent input may never resolve to eligible, least of all on a stream "
    "that would add revenue to the priced floor (D45 no-null amendment)."
)


class CmuCapacity(NamedTuple):
    """The Capacity Market capacity a CMU on this site can EVIDENCE, and by which statutory route.

    ``mw`` is ``None`` when the site evidences neither route — the honest refusal, never 0.0 and
    never the sized power. ``route`` is ``'generating'`` (reg 15(4)(a) connection capacity, bounded
    by the export capacity a generating CMU must have to export at all) or ``'dsr'``
    (reg 15(4)(b) DSR capacity, bounded by the import capacity a demand reduction is measured
    against). ``notes`` carries the working, including whether the capacity is recorded or D45
    declared.
    """

    mw: float | None
    route: str | None
    notes: tuple[str, ...]


def capacity_market_cmu_capacity(
    site_model: SiteModel, *, unit_mw: float | None = None
) -> CmuCapacity:
    """The CMU's evidenced connection/DSR capacity (MW) — the quantity reg 15(4) actually tests.

    Two statutory routes, each bounded by the connection evidence its own limb depends on:

    - **Generating CMU** (reg 15(4)(a), "the connection capacity of the CMU"): a generating CMU
      *exports*, so its connection capacity cannot exceed the site's export capacity (MEC). A site
      with no export capacity has no generating-CMU route at all — not a small one.
    - **DSR CMU** (reg 15(4)(b), "the DSR capacity of the CMU"): a demand reduction is measured
      against demand taken through the connection, so it cannot exceed the import capacity
      (MIC/ASC).

    ``unit_mw`` is the sized power when the caller knows it (the money side); ``None`` is the gate
    side, where no sizing output exists and none is wanted. Either way the evidence gates the
    route: with neither capacity recorded nor declared the answer is ``None`` however large the
    unit is, which is the N-1 fix — a sizing output can never *supply* the missing evidence.

    The larger of the two routes wins, because prequalification needs only one. Nothing here is
    de-rated: the register row bars that explicitly.

    **Asymmetry between the two routes, deliberate and bounded.** The generating route is capped by
    the export capacity, because a generating CMU physically exports through it — no export
    capacity, no generating CMU, and a 5 MW unit behind a 0.5 MW export connection has 0.5 MW of
    connection capacity, not 5. The DSR route is **existence-gated but not capped** by the recorded
    import capacity: a DSR capacity is not a connection rating but a demand reduction, derived from
    evidenced DSR volumes against a Baseline Demand (register row "Proven DSR Capacity derivation"),
    and the SiteModel records no DSR capacity to read. Where the unit exceeds the recorded import
    capacity the verdict on that route is therefore an explicit UPPER BOUND, said so in the notes,
    rather than a confirmation.
    """
    export = gates.evidenced_export_capacity_mw(site_model)
    demand = gates.evidenced_import_capacity_mw(site_model)
    if export is None and demand is None:
        return CmuCapacity(
            None,
            None,
            (
                "Capacity Market eligibility UNEVIDENCED: this site records neither a connection "
                "capacity (MIC/ASC) nor an export capacity (MEC), so there is no evidence of the "
                "quantity SI 2014/2043 reg 15(4) tests — 'the connection capacity of the CMU' "
                "(limb (a), generating) or 'the DSR capacity of the CMU' (limb (b)). With no "
                "export capacity the site cannot be a generating CMU at all, and with no import "
                "capacity there is nothing to evidence a DSR capacity against.",
                "The sized battery's power is NOT this quantity and cannot stand in for it: the "
                "register row is explicit that the threshold applies to connection/DSR capacity, "
                "and a sizing output is not evidence about the connection. Consent, deliverability "
                "and absence alike can never manufacture eligibility (D-92) — this is that same "
                "principle read in the direction that ADDS revenue.",
                CM_CAPACITY_DATA_ASK,
            ),
        )
    routes: list[tuple[float, str]] = []
    notes: list[str] = []
    if export is not None:
        # reg 15(4)(a): the connection capacity of a generating CMU, which it exports through.
        generating_mw = export.mw if unit_mw is None else min(unit_mw, export.mw)
        routes.append((generating_mw, "generating"))
        capped = (
            ""
            if unit_mw is None or unit_mw <= export.mw
            else f" — capped from the {unit_mw:,.3f} MW unit, which cannot export more than the "
            "connection passes"
        )
        notes.append(
            f"GENERATING route (SI 2014/2043 reg 15(4)(a), 'the connection capacity of the CMU'): "
            f"{generating_mw:,.3f} MW, from the export capacity {export.mw:,.3f} MW "
            f"({export.basis} on {export.field}){capped}."
        )
        notes.extend(_declared_capacity_notes(export))
    if demand is not None:
        # reg 15(4)(b): the DSR capacity of the CMU — a demand reduction, not a connection rating.
        dsr_mw = demand.mw if unit_mw is None else unit_mw
        routes.append((dsr_mw, "dsr"))
        notes.append(
            f"DSR route (SI 2014/2043 reg 15(4)(b), 'the DSR capacity of the CMU'): "
            f"{dsr_mw:,.3f} MW, on a connection whose recorded import capacity is "
            f"{demand.mw:,.3f} MW ({demand.basis} on {demand.field})."
        )
        notes.extend(_declared_capacity_notes(demand))
        if unit_mw is not None and unit_mw > demand.mw:
            notes.append(
                f"UPPER BOUND on the DSR route: the {unit_mw:,.3f} MW tested is the sized unit and "
                f"EXCEEDS the site's recorded import capacity of {demand.mw:,.3f} MW, which bounds "
                "any demand reduction measured at this connection. No DSR capacity is recorded for "
                "this site, and a real one is derived from evidenced DSR volumes against a "
                "Baseline Demand (register row 'Proven DSR Capacity derivation' — 'effectively "
                "impossible for a greenfield BTM battery', forcing the Unproven DSR route with "
                "credit cover). This route's verdict is therefore an upper bound, not a "
                "confirmation."
            )
    best = max(routes, key=lambda r: r[0])
    return CmuCapacity(best[0], best[1], tuple(notes))


def _declared_capacity_notes(evidence: gates.CapacityEvidence) -> list[str]:
    """The D45 disclosure a DECLARED (rather than recorded) capacity owes its reader."""
    if evidence.basis != "declared":
        return []
    return [
        f"{evidence.field} is a D45 DECLARED value, not a measured one — reported as 'assumed, "
        "confirmation required', never as evidence. It is a structural feasibility input, so it "
        "caps the confidence band at LOW (docs/decisions.md D45; schemas.derived.STRUCTURAL_KEYS)."
    ]


def capacity_market_entry_verdict(
    site_model: SiteModel,
    *,
    unit_mw: float | None,
    portfolio_mw: float | None,
) -> gates.EligibilityVerdict:
    """Can a CMU on this site PREQUALIFY for the Capacity Market? (N-1, reg 15(4))

    The CM's own entry point, replacing :func:`market_entry_verdict` for this stream on both the
    gate and the money side. The difference is the quantity tested: the CMU's **evidenced**
    connection or DSR capacity (:func:`capacity_market_cmu_capacity`), never a sizing output.

    An unevidenced site is ``eligible=False`` on route :data:`CM_UNEVIDENCED_ROUTE`, and an
    aggregated ``portfolio_mw`` does **not** rescue it: a multi-site DSR CMU aggregates evidenced
    capacity from its members, and a member whose own capacity is unevidenced contributes an
    unknown, not a clearing one. Refusing here keeps the failure a labelled data ask instead of a
    revenue line.
    """
    threshold = eligibility_threshold_mw(MarketStream.CAPACITY_MARKET)
    cmu = capacity_market_cmu_capacity(site_model, unit_mw=unit_mw)
    if cmu.mw is None:
        return gates.EligibilityVerdict(False, CM_UNEVIDENCED_ROUTE, None, cmu.notes)
    verdict = gates.market_entry_eligibility(
        unit_mw=cmu.mw,
        portfolio_mw=portfolio_mw,
        threshold_mw=threshold,
        whole_mw_orders=MarketStream.CAPACITY_MARKET in WHOLE_MW_ORDER_STREAMS,
        quantity_label=_CM_QUANTITY_LABEL,
    )
    return verdict._replace(reasons=cmu.notes + verdict.reasons)


def _maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    """Fetch an assumption if present, else ``None`` (never fabricates one)."""
    try:
        return assumptions.get(section, name)
    except KeyError:
        return None


def _consent_status(site_model: SiteModel, stream: MarketStream) -> str | None:
    """The orthogonal "consent given" label for ``stream`` (Revenue-Data Drive / D43).

    ``"given"`` iff the customer explicitly consented to this stream at intake, else ``None``.
    Stamped onto every GateResult regardless of the gate status — consent is authorisation, not
    value, so this label is orthogonal to whether the stream priced or opened.
    """
    return "given" if gates.stream_consented(site_model, stream) else None


def _consent_agreed_rate(site_model: SiteModel, stream: MarketStream) -> float | None:
    """The customer-agreed rate for ``stream`` when consented, else None (Revenue-Data Drive / D43).

    Consent is authorisation, not value: a captured agreed rate is honoured only when the customer
    explicitly consented. Mirrors the engine path (``valuation._consent_agreed_rate``) so the proxy
    fallback prices consent identically. Never invents a rate.
    """
    if not gates.stream_consented(site_model, stream):
        return None
    return gates.agreed_rate(site_model, stream)


# ---------------------------------------------------------------------------
# Value-function helpers (numbers from the assumption library × dispatched quantities).
# ---------------------------------------------------------------------------


def _num(a: Assumption | None) -> float | None:
    """The numeric value of an assumption, or ``None`` (booleans/structured values excluded)."""
    if a is None:
        return None
    v = a.value
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def _placeholder_note(section: str, name: str, a: Assumption) -> str:
    """A loud label so a placeholder-derived £ can never be mistaken for a bankable number."""
    return (
        f"indicative only — NOT bankable: {section}.{name} = {a.value} {a.unit} is status "
        f"'{a.status.value}' (confidence {a.confidence.value}); source: {a.source}."
    )


def _price_multiplier(scenario: Scenario) -> float:
    """Read ``scenario.price_multiplier`` (duck-typed, like finance); 1.0 if absent/invalid."""
    m = getattr(scenario, "price_multiplier", None)
    if isinstance(m, bool):
        return 1.0
    if isinstance(m, (int, float)) and m > 0:
        return float(m)
    return 1.0


def _derate_for_duration(derate_map: object, duration_h: float) -> tuple[float | None, str | None]:
    """CM de-rating factor for ``duration_h`` from the per-duration table.

    Picks the factor for the largest tabulated duration <= ``duration_h``. Between tabulated
    points that IS conservative (longer storage de-rates more favourably, so flooring onto a
    lower bin understates), and no value is ever interpolated.

    **Below the smallest tabulated point the floor inverts and is NOT conservative**, which the
    warning text below used to claim it was. Handing a 0.5 h unit the 1 h bin credits it with an
    hour unit's de-rated capacity: on the published 2029-30 T-4 curve that is 0.1099 against a
    published 0.0557, i.e. nearly double, in the direction that ADDS Capacity Market revenue. The
    live fix is data, not code: ``gb_v1_10`` extends
    ``capacity_market.de_rating_factor_by_duration_h`` to the full published 0.5 h to 12 h curve,
    so on the newest release no duration the sizing search can produce (0.5 h to 8 h,
    ``optimisation.two_stage.DURATION_BOUNDS_HOURS``) reaches this branch at all. The branch
    survives for sub-0.5 h durations and for runs pinned to a superseded release.

    Two things a reader of this function must know, both recorded rather than silently relied on:

    *   ``gridq.optimisation._assumptions.value_for_duration`` is this function's twin and carries
        the SAME below-range flaw, with the same "conservatively" wording. That twin is the one on
        the production pricing path; this one is not (see below). It is not corrected here because
        it is not this module's to change.
    *   This function is **not reachable from a quote**. It is called only by
        :meth:`CapacityMarketStream._price`, which is called only by
        ``streams.ValueStream.value_function``, which has no caller in ``src/`` at all. The MILP
        path reprices every stream in ``optimisation/valuation.py`` and takes only ``check_gates``
        from this module.

    Returns ``(factor, warning|None)``.
    """
    if not isinstance(derate_map, dict) or not derate_map:
        return None, "capacity_market.de_rating_factor_by_duration_h is missing or empty"
    try:
        pairs = sorted((float(k), float(v)) for k, v in derate_map.items())
    except (TypeError, ValueError):
        return None, "capacity_market.de_rating_factor_by_duration_h is malformed"
    chosen: float | None = None
    for d, f in pairs:
        if d <= duration_h:
            chosen = f
    if chosen is not None:
        return chosen, None
    return pairs[0][1], (
        f"duration {duration_h}h is below the smallest tabulated CM de-rating point "
        f"({pairs[0][0]}h), so that point's factor {pairs[0][1]} is used. THIS OVERSTATES the "
        f"de-rated capacity of a {duration_h}h unit and therefore its Capacity Market revenue: it "
        f"credits it with a {pairs[0][0]}h unit's de-rating. Read this figure as an upper bound, "
        "not a conservative one, and source the published bin for this duration"
    )


def _priced_per_mw_yr(
    section: str,
    name: str,
    dispatch: DispatchSummary,
    assumptions: AssumptionLibrary,
    scenario: Scenario,
    *,
    extra_note: str | None = None,
    agreed_rate: float | None = None,
    volume_mw: float | None = None,
    extra_notes: Sequence[str] = (),
) -> PricedValue:
    """Value a £/MW/yr stream: ``rate × volume MW × scenario multiplier``.

    Rate ladder (D43): a customer-agreed £/MW/yr rate (``agreed_rate``) wins over the assumption
    value when consented; else the assumption value; else £0. An agreed rate is still labelled
    not-bankable (a commercially-agreed rate, not a market-cleared price).

    ``volume_mw`` overrides the sized power as the priced volume — used by the size-gated markets
    (D-92) to price the **sellable** MW (whole-MW order granularity) rather than the nameplate.
    """
    a = _maybe(assumptions, section, name)
    key = f"{section}.{name}"
    val = _num(a)
    if agreed_rate is not None:
        rate = agreed_rate
        notes = [
            f"priced at the customer-agreed rate £{rate:,.2f}/MW/yr (consent given), ahead of the "
            f"assumption {key} (estimation ladder, D43); not bankable — a commercially-agreed "
            "rate, not a market-cleared price."
        ]
        used: tuple[str, ...] = ()
    elif a is None or val is None:
        return PricedValue(0.0, 0.0, (f"not priced: {key} missing or non-numeric.",), ())
    else:
        rate = val
        notes = [_placeholder_note(section, name, a)]
        used = (key,)
    mult = _price_multiplier(scenario)
    mw = dispatch.power_mw if volume_mw is None else volume_mw
    revenue = rate * mw * mult
    if extra_note:
        notes.append(extra_note)
    if mult != 1.0:
        notes.append(f"scenario price multiplier {mult} applied.")
    notes.extend(extra_notes)
    return PricedValue(revenue, 0.0, tuple(notes), used)


# ---------------------------------------------------------------------------
# Import-side floor (no external partner needed)
# ---------------------------------------------------------------------------


class PpaOrBillSavingsStream(FloorStream):
    """Customer-facing import-side floor: the bill savings a PPA is priced against."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.PPA_OR_BILL_SAVINGS,
            display_name="PPA / bill savings (import floor)",
            required_inputs=[
                RequiredInput(key="tariff_stack.import_rates", description="Import tariff rates."),
                RequiredInput(
                    key="load_data",
                    description="Half-hourly or annual load to value the bill before/after.",
                ),
            ],
            constraints=[_CONNECTION_CHARGE_LIMIT, _WARRANTY_THROUGHPUT_CAP],
            assumption_keys=["tariff_arbitrage", "duos", "mic_asc_headroom"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        missing: list[str] = []
        if not site_model.tariff_stack.import_rates:
            missing.append("import tariff rates (tariff_stack.import_rates)")
        if not gates.has_load_data(site_model):
            missing.append("load data (half-hourly coverage or annual consumption)")
        if missing:
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=missing,
                warnings=[f"PPA/bill savings cannot be assessed: missing {', '.join(missing)}."],
                consent_status=consent_status,
            )
        return self._gate(GateStatus.OPEN, consent_status=consent_status)

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        # Not separately priced: the import-side floor value is itemised across the
        # tariff-arbitrage + DUoS + MIC/ASC streams, so summing it here would double-count.
        # The customer PPA £/kWh itself is set by the finance pricing layer, not the stack.
        return PricedValue(
            0.0,
            0.0,
            (
                "PPA / bill savings is the customer-facing aggregate of the import-side floor; "
                "its value is itemised across the tariff_arbitrage, DUoS red-band and MIC/ASC "
                "headroom streams (summing here would double-count). The PPA £/kWh is set by "
                "the finance pricing layer.",
            ),
            (),
        )


#: Stable prefix on the warning that explains an OPEN tariff-arbitrage gate on a FLAT import
#: tariff (TARIFF-1). Deliberately a module constant: the reported floor equalling the MILP
#: objective depends on this gate opening, and `tests/test_flat_tariff_gate_carries_cost.py` pins
#: the text so the disclosure cannot be silently dropped while the behaviour stays.
_FLAT_TARIFF_NO_SPREAD_NOTE = (
    "FLAT IMPORT TARIFF — NO PRICE SPREAD TO ARBITRAGE, so the arbitrage REVENUE leg is £0. "
    "That £0 is a measured economic fact, not a missing input. The gate is nonetheless OPEN "
    "because charge energy is still bought at the flat rate: the MILP objective carries "
    "flat_rate × (Σdischarge − Σcharge) × Δt, which is NEGATIVE by exactly the cost of the "
    "round-trip losses, and that cost belongs in the reported floor."
)

#: The machine-detectable stamp that a stream's GBP is carried under a NAMED OTHER STREAM rather
#: than
#: being a gated zero. D57's acceptance rule is verbatim: "the acceptance test per stream per site
#: is
#: either a non-zero GBP, or an explicit statement on the record that the GBP is carried under a
#: named
#: other stream", so this token is what makes the second limb machine-checkable instead of prose a
#: reader has to find. Consumers (and `scripts/d57_stream_matrix.py`) match on the prefix.
CARRIED_BY_LABEL = "GBP CARRIED BY ANOTHER STREAM (D57 accounting identity)"

#: The carrier statement for TARIFF_ARBITRAGE on a site whose import tariff is a SINGLE FLAT RATE.
#: This is arithmetic and not eligibility: max(rate) − min(rate) IS zero, so there is no spread to
#: arbitrage at any battery size, under any consent, on any basis. D57 opens gates; it cannot create
#: a
#: price spread in a supply contract that has one rate, and inventing a time-of-use tariff the
#: customer
#: does not hold would be a factual claim about their supply contract (D45).
_FLAT_TARIFF_CARRIER_NOTE = (
    f"{CARRIED_BY_LABEL}: on a single-rate import tariff the import-side bill-avoidance value this "
    "stream would carry is earned entirely on the DUoS time bands, and it is reported in full "
    "under "
    "DUOS_RED_BAND_AVOIDANCE. The same discharge earns it; the same kWh is not counted twice. This "
    "GBP 0 is therefore the SAME KIND of figure as PPA_OR_BILL_SAVINGS' GBP 0 - an accounting "
    "identity with a named carrier - and NOT a gated, refused or suppressed stream. It is not "
    "something D57 can open: max(rate) − min(rate) is zero on this site's evidenced tariff, so "
    "there "
    "is no spread to arbitrage at any battery size, under any consent, on any basis. The stream's "
    "COST leg is still real and is still carried (see the flat-tariff note above), which is why "
    "the "
    "gate stays OPEN. CONFIRMATION ASK: whether the customer could move to a time-of-use import "
    "contract, and on what day/night rates - that is a commercial question about their supply "
    "agreement, not a modelling input, and nothing here assumes an answer."
)


class TariffArbitrageStream(FloorStream):
    """Supply-rate arbitrage on wholesale-shaped / time-of-use tariffs."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.TARIFF_ARBITRAGE,
            display_name="Tariff arbitrage",
            required_inputs=[
                RequiredInput(
                    key="tariff_stack.import_rates",
                    description="A multi-rate / time-of-use import tariff (≥2 distinct prices).",
                ),
                RequiredInput(key="load_data", description="Load to arbitrage against."),
            ],
            constraints=[_WARRANTY_THROUGHPUT_CAP],
            assumption_keys=["tariff_arbitrage", "round_trip_efficiency"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        if not site_model.tariff_stack.import_rates:
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=["an import tariff (tariff_stack.import_rates)"],
                warnings=[
                    "tariff arbitrage gate closed: tariff_stack.import_rates is empty, so the "
                    "site's energy price is genuinely UNKNOWN — not merely flat. Nothing can be "
                    "priced against an unknown price, and the MILP has no energy term either "
                    "(`optimisation.tariff_calendar.energy_price_series` returns None for an "
                    "empty rate map), so the closed gate and the objective agree at £0."
                ],
                consent_status=consent_status,
            )
        if not gates.has_load_data(site_model):
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=["load data (half-hourly coverage or annual consumption)"],
                warnings=["tariff arbitrage gate closed: no load data to value arbitrage against."],
                consent_status=consent_status,
            )
        if not gates.has_usable_tariff_arbitrage_data(site_model):
            # TARIFF-1 (Olly, 2026-08-17). A FLAT tariff used to close this gate, and closing it
            # DELETED A REAL COST from the reported floor. `energy_price_series` (the F-01 fix)
            # deliberately returns a CONSTANT price array rather than None for a single rate, so
            # the MILP objective carries `flat_rate x (Σdischarge − Σcharge) x Δt`, which is
            # NEGATIVE by exactly the cost of the round-trip losses. But a CLOSED gate is EXCLUDED
            # firmness, `valuation._priceable` skips the branch, and `annual_cost_gbp` is summed
            # only over FIRM/CONTRACTED streams (`finance/cashflows.py:869`) — so the negative
            # energy term never reached the floor and the REPORTED FLOOR SAT ABOVE THE MILP'S OWN
            # OBJECTIVE. That is the F-02 failure mode arriving through the gate instead of a clamp.
            #
            # The gate therefore OPENS (⇒ FIRM), which is what carries the cost. "No spread to
            # arbitrage" is a £0-REVENUE FACT, not an absent input: the revenue leg is genuinely
            # zero and `_split_signed` puts the whole signed component on the cost channel.
            # DIRECTION OF EFFECT: the reported floor moves DOWN — the conservative direction, and
            # the only class-C fix that does.
            return self._gate(
                GateStatus.OPEN,
                warnings=[
                    _FLAT_TARIFF_NO_SPREAD_NOTE,
                    _FLAT_TARIFF_CARRIER_NOTE,
                    "The gate is OPEN rather than closed BECAUSE the flat rate is a real cost: "
                    "closing it excluded the stream from the priced floor, and `annual_cost_gbp` "
                    "is summed only over FIRM/CONTRACTED streams, so the negative energy term was "
                    "dropped and the reported floor sat ABOVE the MILP objective. Opening the "
                    "gate carries the cost and moves the reported floor DOWN.",
                ],
                consent_status=consent_status,
            )
        return self._gate(GateStatus.OPEN, consent_status=consent_status)

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        # ENGINE/PROXY PARITY (TARIFF-1, Olly 2026-08-17). Opening the gate on a flat tariff is what
        # carries the round-trip energy COST into the reported floor — but it must not turn on the
        # per-MW/yr proxy REVENUE, which prices a spread the site does not have. The engine path
        # reads `schedule.arbitrage_value_gbp` and correctly yields £0 revenue there; without this
        # guard the proxy would read the library's indicative £/MW/yr instead and publish a
        # spread-derived revenue on a site with no spread — a fresh engine/proxy divergence in the
        # UP direction, created by the very fix that closed a DOWN-direction one.
        if not gates.has_usable_tariff_arbitrage_data(site_model):
            return PricedValue(
                0.0, 0.0, (_FLAT_TARIFF_NO_SPREAD_NOTE, _FLAT_TARIFF_CARRIER_NOTE), ()
            )
        return _priced_per_mw_yr(
            "tariff_arbitrage",
            "indicative_value_gbp_per_mw_yr",
            dispatch,
            assumptions,
            scenario,
            extra_note="indicative £/MW/yr × sized power; the site-derived half-hourly "
            "arbitrage value (load+tariff spread, round-trip losses) lands with the dispatch "
            "engine (M3).",
        )


class DuosRedBandAvoidanceStream(FloorStream):
    """DUoS red-band avoidance (post-TCR treatment; no Triad story)."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.DUOS_RED_BAND_AVOIDANCE,
            display_name="DUoS red-band avoidance",
            required_inputs=[
                RequiredInput(
                    key="tariff_stack.duos_red_rate",
                    description="DUoS band rate data (at least the red band).",
                ),
                # The off-peak bands are declared because the £ DEPENDS on them, not because the
                # stream needs them to exist: absent, they are consumed as zero, which widens the
                # modelled red-to-green spread beyond the one the site faces and OVERSTATES this
                # stream (the missing off-peak charging cost never nets off). That is the opposite
                # error from the red band's, so it must not close the gate — closing it would
                # refuse a stream the site genuinely earns. Declared non-blocking, the D45 guard
                # reports the overstatement and its direction and moves no number. The run-level
                # twin is `optimisation.milp_solver.duos_absence_warning`; this is the per-stream
                # one, on the record a floor reader actually reads.
                RequiredInput(
                    key="tariff_stack.duos_amber_rate",
                    description="DUoS amber band rate — absent, it is read as zero and widens the "
                    "modelled band spread.",
                    blocks_pricing=False,
                ),
                RequiredInput(
                    key="tariff_stack.duos_green_rate",
                    description="DUoS green band rate — absent, it is read as zero and widens the "
                    "modelled band spread.",
                    blocks_pricing=False,
                ),
                RequiredInput(key="load_data", description="Load across the red-band windows."),
            ],
            constraints=[_DUOS_RED_WINDOW],
            assumption_keys=["duos"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        if not gates.has_duos_rate_data(site_model):
            # A STRUCTURAL zero and a MISSING-DATA zero are the same £ and completely different
            # statements, and the gate used to publish only the second. Where the site's own
            # register records `tariff_stack.duos_red_rate` as `not_applicable` — the live case is
            # a 100% private-wire site, behind which the GB DNOs' Common Distribution Charging
            # Methodology red/amber/green unit rates simply do not apply — the £0 is CORRECT and
            # PERMANENT, and asking that customer for a distribution rate no supplier could ever
            # issue them is the defect. Deriving a published DNO red rate for such a meter would
            # be an INVENTED CHARGE, not sourcing, so the zero must stay.
            record = gates.evidenced_absences(site_model).get("tariff_stack.duos_red_rate")
            if record is not None:
                return self._gate(
                    GateStatus.CLOSED,
                    missing_requirements=[],
                    warnings=[
                        "DUoS red-band avoidance is £0 for a STRUCTURAL reason, not a missing "
                        "input: this site's register records tariff_stack.duos_red_rate as NOT "
                        f"APPLICABLE — {record.basis} There is no band spread to monetise, so "
                        "£0 is the right number and it is PERMANENT. NO DATA ASK IS MADE: "
                        "deriving a published DNO red rate for this meter point would invent a "
                        "charge the site does not pay, which is the opposite of sourcing."
                    ],
                    consent_status=consent_status,
                )
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=["DUoS band rates (tariff_stack.duos_red_rate)"],
                warnings=["DUoS red-band avoidance gate closed: no DUoS rate data on the tariff."],
                consent_status=consent_status,
            )
        if not gates.has_load_data(site_model):
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=["load data (half-hourly coverage or annual consumption)"],
                warnings=["DUoS red-band avoidance gate closed: no load data."],
                consent_status=consent_status,
            )
        return self._gate(GateStatus.OPEN, consent_status=consent_status)

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        return _priced_per_mw_yr(
            "duos",
            "red_band_avoidance_gbp_per_mw_yr",
            dispatch,
            assumptions,
            scenario,
            extra_note="red-band avoidance £/MW/yr × sized power; the site-derived value "
            "(red-band-window discharge) lands with the dispatch engine (M3).",
        )


#: Stable prefix on the MIC/ASC unpriced-capacity-leg disclosure (MICASC-1/2). Pinned by
#: ``tests/test_mic_asc_capacity_leg_disclosure.py``.
MIC_ASC_UNPRICED_LEG_LABEL = "UNPRICED LEG — AVOIDED AGREED CAPACITY AND TCR BAND"

# ---------------------------------------------------------------------------
# Avoided agreed capacity + TCR band (MICASC-3, Olly, 2026-08-17)
# ---------------------------------------------------------------------------
#
# The £/kVA/day agreed-capacity charge and the TCR fixed residual are the two bill lines that fall
# when the site's PEAK falls. Neither was modelled: both were computed off ``asc_kw or mic_kw``, a
# battery-invariant constant, so peak shaving could never reduce them (milp_solver ~line 731,
# engine ~line 2648). This block prices them.
#
# THE CONVENTION THAT MAKES THE NUMBER HONEST — the counterfactual. A site whose agreed capacity
# sits far above its metered peak can release that capacity TODAY, with no battery, by asking the
# DNO. That saving is real and it is LARGE (£15,039/yr at Bishops Cleeve MRF, £18,632/yr at Knowl
# Hill on the invoice-anchored basis), and it is NOT battery money. Crediting it to the battery
# would be the single biggest overstatement available on this whole stream. So the counterfactual
# is not "the capacity the customer agreed years ago"; it is **the tightest capacity the site's own
# metered peak would already support without a battery**. The battery is credited only with the
# INCREMENT below that line.
#
#: Both TCR-banded fixed lines, per licence area, keyed by the DUoS charging-statement table name.
_TNUOS_RESIDUAL_KEY = ("tnuos", "demand_residual_bands_gbp_per_day")
_TCR_THRESHOLDS_KEY = ("duos", "tcr_band_thresholds_kva")
_DUOS_BY_DNO_KEY = ("duos", "unit_rates_p_per_kwh_by_dno")

#: The DUoS/TNUoS charging table this model applies. LV is DERIVED, not assumed blind: every real
#: site's own evidenced ``capacity_charge_gbp_per_kva_day`` (0.1066 NGED_WM, 0.1063 SSEN_SEPD)
#: reproduces the published ``lv_site_specific`` rate exactly, and each site's
#: ``standing_charge_per_day_gbp`` reconstructs EXACTLY as TNUoS LV-band residual + DUoS LV-band
#: fixed. A site whose voltage is evidenced as HV must not use this table.
_LV_TABLE = "lv_site_specific"

#: Security margin held between the new agreed capacity and the worst modelled net import, as a
#: FRACTION of the battery's rated power. A capacity reduction is only real if the new figure is
#: never exceeded: one breach and the customer pays excess-capacity charges (equal to the capacity
#: rate in every area fetched for gb_v1_11), which would turn a saving into a penalty.
#:
#: 0.10 is a MODELLING CONVENTION, not a published rate, and it is labelled as one wherever it is
#: reported. There is no published GB margin for this and none is invented. Its direction is DOWN
#: (it reduces the claim). It is expressed against rated power because the risk it covers is the
#: battery being unavailable or empty at the moment the site peaks, which scales with how much of
#: the peak the battery is carrying — a fixed kW margin would be far too loose on a large system
#: and needlessly tight on a small one.
_CAPACITY_SECURITY_MARGIN_FRACTION = 0.10

#: How far ``annual_factor`` may exceed 1.0 before the metered horizon stops counting as a whole
#: year. The real sites land at 1.0001 (17,518 of 17,520 half-hours — two missing intervals), which
#: is a rounding artefact, not a missing season. 1.01 is ~3.6 days of gap; beyond that the annual
#: maximum is genuinely unobserved and the claim is refused rather than extrapolated.
_FULL_YEAR_COVERAGE_TOLERANCE = 1.01

#: Prefix on every reported agreed-capacity figure. Pinned by the tests.
AGREED_CAPACITY_ACTION_LABEL = "REQUIRES THE CUSTOMER TO RE-NEGOTIATE AGREED CAPACITY WITH THE DNO"


class TcrBandCharge(NamedTuple):
    """The two TCR-banded fixed lines at one agreed-capacity band, in £/day."""

    band: str
    tnuos_residual_gbp_per_day: float
    duos_fixed_gbp_per_day: float

    @property
    def total_gbp_per_day(self) -> float:
        return self.tnuos_residual_gbp_per_day + self.duos_fixed_gbp_per_day


def tcr_band_for_kva(kva: float, assumptions: AssumptionLibrary) -> str | None:
    """The TCR residual band an agreed capacity of ``kva`` falls in (LV table), else ``None``.

    Boundary rule is the charging statement's own and is **upper-inclusive**: ``Lower < x <=
    Upper`` (``duos.tcr_band_thresholds_kva.boundary_rule``, verbatim). Getting this backwards
    moves a site exactly on a threshold into the wrong band, which on the LV4/LV3 step is £8,692/yr.
    """
    entry = _maybe(assumptions, *_TCR_THRESHOLDS_KEY)
    if entry is None or not isinstance(entry.value, dict):
        return None
    table = entry.value.get(_LV_TABLE)
    if not isinstance(table, dict):
        return None
    for band, bounds in table.items():
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        low, high = bounds
        if kva > float(low) and (high is None or kva <= float(high)):
            return str(band)
    return None


def tcr_band_charge(
    kva: float, dno_region: str | None, assumptions: AssumptionLibrary
) -> TcrBandCharge | None:
    """Both TCR-banded fixed £/day lines at agreed capacity ``kva``, or ``None`` if underivable.

    TWO components, and omitting the first is the error the MICASC audit made — it costed the band
    step at the DUoS fixed line alone (£329/yr at Bishops Cleeve MRF) and missed the **TNUoS
    non-locational demand residual**, which is banded on exactly the same agreed capacity and is
    ~6x larger (£2,087/yr on the same step). The audit's band figure is therefore understated 7.3x.

    Both are sourced and pinned in the release:
      * ``tnuos.demand_residual_bands_gbp_per_day`` — NESO January 2026 Final TNUoS Tariffs for
        2026/27, Table 10 (LV1 5.797398 / LV2 11.511840 / LV3 14.381374 / LV4 38.180103 £/day);
      * ``duos.unit_rates_p_per_kwh_by_dno.<AREA>.fixed_p_per_mpan_day`` — the area's own 2026/27
        Use of System Charging Statement (URL + sha256 in the release).

    A **0.00 DUoS fixed line is a FACT, not a gap** (SSEN_SEPD publishes 0.00 at every LV band and
    recovers its residual through band-varying unit rates instead), so it is read as zero and the
    TNUoS leg still applies. An area absent from the table returns the TNUoS leg with the DUoS leg
    at zero and the caller states the omission — never a proxy from another area, which
    gb_v1_11 records as a defect the release exists to have fixed.
    """
    band = tcr_band_for_kva(kva, assumptions)
    if band is None:
        return None
    tnuos_entry = _maybe(assumptions, *_TNUOS_RESIDUAL_KEY)
    if tnuos_entry is None or not isinstance(tnuos_entry.value, dict):
        return None
    bands = tnuos_entry.value.get("bands")
    if not isinstance(bands, dict):
        return None
    # "band_3" -> "LV3": the TNUoS table keys the same TCR bands by voltage + ordinal.
    ordinal = band.rsplit("_", 1)[-1]
    residual = bands.get("LV" + ordinal)
    if residual is None:
        return None

    duos_fixed = 0.0
    area_entry = _maybe(assumptions, *_DUOS_BY_DNO_KEY)
    if dno_region and area_entry is not None and isinstance(area_entry.value, dict):
        area = area_entry.value.get(dno_region)
        if isinstance(area, dict):
            fixed = area.get("fixed_p_per_mpan_day")
            if isinstance(fixed, dict):
                sub = fixed.get(_LV_TABLE)
                if isinstance(sub, dict) and sub.get(band) is not None:
                    duos_fixed = float(sub[band]) / 100.0
    return TcrBandCharge(band, float(residual), duos_fixed)


class AgreedCapacityBasis(NamedTuple):
    """Everything needed to price a peak reduction on one site, all site-or-release sourced."""

    agreed_kva: float
    """The capacity the site is billed on today (``asc_kw`` else ``mic_kw``)."""
    metered_peak_kw: float
    """The site's own measured peak import — the no-battery counterfactual's binding number."""
    capacity_rate_gbp_per_kva_day: float
    dno_region: str | None
    rate_basis: str
    assumption_keys: tuple[str, ...]


def agreed_capacity_basis(
    site_model: SiteModel, assumptions: AssumptionLibrary
) -> AgreedCapacityBasis | None:
    """Resolve the site's agreed-capacity pricing basis, or ``None`` when it cannot be sourced.

    The £/kVA/day rate is the site's OWN evidenced ``tariff_stack.capacity_charge_gbp_per_kva_day``
    wherever it exists. Where it does not (Tanhouse, Colnbrook), it is DERIVED from the release's
    pinned per-area table via ``connection.dno_region`` — which is sourcing under D45, not
    invention: the figure is a published DNO charging-statement rate for the site's own licence
    area, carried with its URL and sha256. The derivation is labelled in ``rate_basis`` and the
    caller states it. **No cross-area proxy is ever used**: an absent area returns ``None`` and the
    stream reports the data ask instead of a number.
    """
    connection = site_model.connection
    agreed = connection.asc_kw or connection.mic_kw
    peak = site_model.load_data.max_import_kw
    if not agreed or agreed <= 0 or peak is None or peak <= 0:
        return None
    dno = getattr(connection, "dno_region", None)
    keys: list[str] = [".".join(_TCR_THRESHOLDS_KEY), ".".join(_TNUOS_RESIDUAL_KEY)]

    rate = site_model.tariff_stack.capacity_charge_gbp_per_kva_day
    if rate is not None:
        basis = (
            f"the site's own evidenced tariff line (£{float(rate):.4f}/kVA/day, "
            "tariff_stack.capacity_charge_gbp_per_kva_day)"
        )
    else:
        entry = _maybe(assumptions, *_DUOS_BY_DNO_KEY)
        area = entry.value.get(dno) if (entry and isinstance(entry.value, dict) and dno) else None
        table = area.get("capacity_charge_p_per_kva_day") if isinstance(area, dict) else None
        p_per_kva_day = table.get(_LV_TABLE) if isinstance(table, dict) else None
        if p_per_kva_day is None:
            return None
        rate = float(p_per_kva_day) / 100.0
        keys.append(".".join(_DUOS_BY_DNO_KEY))
        basis = (
            f"DERIVED under D45 from the release's pinned 2026/27 charging statement for this "
            f"site's own licence area ({dno} {_LV_TABLE} {p_per_kva_day} p/kVA/day, "
            f"{'.'.join(_DUOS_BY_DNO_KEY)}) because the site carries no capacity line of its own "
            "— sourcing a published GB rate, never a cross-area proxy"
        )
    return AgreedCapacityBasis(
        agreed_kva=float(agreed),
        metered_peak_kw=float(peak),
        capacity_rate_gbp_per_kva_day=float(rate),
        dno_region=dno,
        rate_basis=basis,
        assumption_keys=tuple(keys),
    )


class AgreedCapacityValue(NamedTuple):
    """The priced (and unpriced) result of one agreed-capacity reduction assessment.

    **``annual_value_gbp`` is NET and the two legs are GROSS — they are not meant to add up.**
    The legs are the bill arithmetic (what the two charges fall by); the headline additionally
    subtracts the FIRM revenue the overlay dispatch gave up to earn them, which the caller measures
    on both schedules. ``capacity_leg_gbp + band_leg_gbp >= annual_value_gbp`` always, and the
    difference is the sacrificed floor revenue, stated in the notes. Reporting the legs net would
    hide which charge moved; reporting the headline gross would double-count the floor.
    """

    annual_value_gbp: float
    """Battery-ATTRIBUTABLE £/yr, NET of sacrificed floor revenue: the increment below what
    renegotiation alone already achieves, minus what the overlay dispatch paid to get there."""
    counterfactual_kva: float
    """The capacity the metered peak already supports with NO battery — the zero point."""
    proposed_kva: float
    """The capacity the modelled dispatch supports, security margin included."""
    security_margin_kw: float
    free_of_battery_gbp: float
    """The renegotiation-only saving. NOT battery money; reported so it can never be double-sold."""
    capacity_leg_gbp: float
    band_leg_gbp: float
    notes: list[str]
    assumption_keys: tuple[str, ...]


def price_agreed_capacity_reduction(
    site_model: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    modelled_peak_kw: float,
    rated_power_kw: float,
    full_series: bool,
    coverage_factor: float = 1.0,
) -> AgreedCapacityValue | None:
    """Price the avoided agreed-capacity charge + TCR band drop from a modelled peak.

    ``modelled_peak_kw`` is the WORST net import (``load + charge − discharge``) over the whole
    modelled horizon — the max, never a mean and never a percentile. That is the only statistic a
    capacity reduction can be underwritten on: agreed capacity is a ceiling, and a single breach in
    a year is a breach. ``full_series=False`` (a typical-period proxy solve) refuses to price at
    all, because the annual worst case is not observable on a weighted subset of periods.

    ``coverage_factor`` is the schedule's ``annual_factor`` (8760 ÷ metered hours). Materially
    above 1.0 means the metered horizon is NOT a whole year, so the unmetered season's peak is
    simply unknown — and an unobserved winter peak is exactly the one that breaches a reduced
    capacity. The claim is refused there rather than extrapolated.

    Three conservatisms, all stated on the result:
      1. the security margin (see ``_CAPACITY_SECURITY_MARGIN_FRACTION``);
      2. the new capacity is rounded UP to whole kVA while the counterfactual is never rounded up
         past the capacity the site actually holds, so rounding can never manufacture a kVA of
         saving in either leg;
      3. the counterfactual is the metered peak, not the agreed capacity, so renegotiation-only
         value is excluded from the claim entirely.
    """
    basis = agreed_capacity_basis(site_model, assumptions)
    if basis is None:
        return None
    rate_per_kva_yr = basis.capacity_rate_gbp_per_kva_day * 365.25

    # The no-battery counterfactual: the tightest whole kVA the site's own metered peak supports.
    # No security margin is applied here — this is a bound on what the customer could do anyway,
    # and inflating it would inflate the battery's credit.
    # ``min`` against the capacity actually held, NOT against its ceiling: a site whose agreed
    # capacity sits just below the next whole kVA (Harwell, 213.81) must not be shown a negative
    # "free" saving for the 0.19 kVA that rounding invented.
    counterfactual = min(float(math.ceil(basis.metered_peak_kw)), basis.agreed_kva)
    margin = _CAPACITY_SECURITY_MARGIN_FRACTION * max(rated_power_kw, 0.0)
    # Never claim MORE capacity than the counterfactual already released, and never claim a
    # reduction the customer's existing agreement does not contain.
    proposed = min(float(math.ceil(modelled_peak_kw + margin)), counterfactual)

    free_of_battery = (basis.agreed_kva - counterfactual) * rate_per_kva_yr
    now = tcr_band_charge(basis.agreed_kva, basis.dno_region, assumptions)
    at_counterfactual = tcr_band_charge(counterfactual, basis.dno_region, assumptions)
    at_proposed = tcr_band_charge(proposed, basis.dno_region, assumptions)
    if now is not None and at_counterfactual is not None:
        free_of_battery += (now.total_gbp_per_day - at_counterfactual.total_gbp_per_day) * 365.25

    capacity_leg = max(0.0, counterfactual - proposed) * rate_per_kva_yr
    band_leg = 0.0
    if at_counterfactual is not None and at_proposed is not None:
        band_leg = max(
            0.0,
            (at_counterfactual.total_gbp_per_day - at_proposed.total_gbp_per_day) * 365.25,
        )

    notes: list[str] = []
    part_year = coverage_factor > _FULL_YEAR_COVERAGE_TOLERANCE
    if not full_series or part_year:
        if part_year:
            notes.append(
                f"NOT PRICED: the metered horizon is NOT a whole year (annual_factor "
                f"{coverage_factor:.4f} ⇒ about "
                f"{365.25 / max(coverage_factor, 1e-9):,.0f} days of data). A reduced agreed "
                "capacity is breached by ONE half-hour above it, and the half-hour most likely to "
                "do that is in the season this dataset does not cover. Extrapolating a peak into "
                "unmetered months would be inventing the single number this stream depends on, so "
                "the claim is REFUSED. £0 here is a refusal, not a measurement. Supply a full "
                "metered year and the stream prices."
            )
        else:
            notes.append(
                "NOT PRICED ON THIS SOLVE: the dispatch is a typical-period proxy (weighted "
                "intervals), and an agreed-capacity reduction is underwritten on the ANNUAL WORST "
                "CASE — a single half-hour above the new capacity is a breach. A weighted subset "
                "of periods cannot evidence the annual maximum, so £0 here is a REFUSAL TO CLAIM, "
                "not a measurement of no value. The full-series solve on the confirmed candidate "
                "prices it."
            )
        return AgreedCapacityValue(
            annual_value_gbp=0.0,
            counterfactual_kva=float(counterfactual),
            proposed_kva=float(counterfactual),
            security_margin_kw=round(margin, 3),
            free_of_battery_gbp=round(free_of_battery, 2),
            capacity_leg_gbp=0.0,
            band_leg_gbp=0.0,
            notes=notes,
            assumption_keys=basis.assumption_keys,
        )

    total = capacity_leg + band_leg
    notes.append(
        f"{AGREED_CAPACITY_ACTION_LABEL}. This is a CUSTOMER ACTION and a DNO decision, not an "
        "automatic bill mechanic: the charge falls only once the agreed capacity on the connection "
        "agreement is actually changed, and the TCR band follows the DCUSA Schedule 32 re-banding "
        "criteria on a 24-month average MIC basis (duos.tcr_band_thresholds_kva.allocation_basis), "
        "so the band leg is not available in year 1 even once the application succeeds. The stream "
        "is therefore CONDITIONAL and sits OUTSIDE the priced floor, P50, P90 and the DSCR. It is "
        "never written as agreed."
    )
    notes.append(
        f"COUNTERFACTUAL, and it is the whole discipline of this number: the site is billed on "
        f"{basis.agreed_kva:,.0f} kVA against a MEASURED peak of {basis.metered_peak_kw:,.2f} kW. "
        f"Re-negotiating to {counterfactual:,.0f} kVA needs NO BATTERY and is worth "
        f"£{free_of_battery:,.0f}/yr — that saving is the CUSTOMER'S ALREADY and is EXCLUDED from "
        f"the figure above. The battery is credited only with the increment below it: "
        f"{counterfactual:,.0f} kVA → {proposed:,.0f} kVA."
    )
    notes.append(
        f"BOUNDED BY THE ANNUAL WORST CASE, not the average: the new capacity clears the highest "
        f"net import (load + charge − discharge) in EVERY settlement period of the modelled year, "
        f"plus a security margin of {margin:,.2f} kW "
        f"({_CAPACITY_SECURITY_MARGIN_FRACTION:.0%} of rated power) held back for the battery "
        "being unavailable or empty when the site peaks. That margin is a LABELLED MODELLING "
        "CONVENTION, not a published rate — no GB source specifies one and none is invented; its "
        "direction is DOWN (it reduces the claim). One breach costs excess-capacity charges at the "
        "same £/kVA rate, so the conservative side is the only defensible one."
    )
    notes.append(
        f"Capacity leg £{capacity_leg:,.0f}/yr at £{rate_per_kva_yr:,.2f}/kVA/yr — "
        f"{basis.rate_basis}."
    )
    if at_counterfactual is not None and at_proposed is not None:
        if band_leg > 0:
            notes.append(
                f"TCR band leg £{band_leg:,.0f}/yr: {at_counterfactual.band} → {at_proposed.band} "
                f"(£{at_counterfactual.total_gbp_per_day:.6f} → "
                f"£{at_proposed.total_gbp_per_day:.6f}/day). BOTH banded lines are counted — the "
                "TNUoS non-locational demand residual (NESO Jan-2026 Final TNUoS 2026/27 Table 10) "
                "AND the DUoS banded fixed line — because both are banded on the same agreed "
                "capacity. Pricing the DUoS line alone understates this step ~7x."
            )
        else:
            lower = _next_band_floor(at_counterfactual.band, assumptions)
            if lower is not None:
                gap = counterfactual - lower
                notes.append(
                    f"TCR band leg £0: the modelled peak stays inside {at_counterfactual.band}. "
                    f"The next band down needs the peak at or below {lower:,.0f} kVA — a further "
                    f"{gap:,.0f} kW of shave from the counterfactual. £0 here is a MEASUREMENT, "
                    "not a missing input."
                )
            else:
                notes.append(
                    f"TCR band leg £0: {at_counterfactual.band} is the lowest band — no further "
                    "band drop exists on this site. PERMANENT, and not a data ask."
                )
    else:
        notes.append(
            "TCR band leg NOT ASSESSED: the site's TCR band could not be resolved from "
            "duos.tcr_band_thresholds_kva + tnuos.demand_residual_bands_gbp_per_day. The capacity "
            "leg above stands; the band leg is ABSENT, not zero."
        )
    notes.append(
        "DISJOINT FROM THE CONNECTION-BOUNDED ARBITRAGE (no double-count): tariff arbitrage and "
        "DUoS red-band avoidance are priced on ENERGY (£/kWh × kWh moved); these two lines are "
        "priced on CAPACITY (£/kVA/day × kVA and a £/day band step) and appear on the bill whether "
        "or not a single kWh moves. The MIC/ASC headroom stream's £0 covers the arbitrage reading "
        "of headroom and still does. The modelled peak this is priced from is also taken from an "
        "OVERLAY solve whose sacrificed floor revenue is netted off, so no £ is counted twice."
    )
    return AgreedCapacityValue(
        annual_value_gbp=round(total, 2),
        counterfactual_kva=float(counterfactual),
        proposed_kva=float(proposed),
        security_margin_kw=round(margin, 3),
        free_of_battery_gbp=round(free_of_battery, 2),
        capacity_leg_gbp=round(capacity_leg, 2),
        band_leg_gbp=round(band_leg, 2),
        notes=notes,
        assumption_keys=basis.assumption_keys,
    )


def _next_band_floor(band: str, assumptions: AssumptionLibrary) -> float | None:
    """The upper threshold of the band below ``band`` — the kVA a drop must reach. Else None."""
    entry = _maybe(assumptions, *_TCR_THRESHOLDS_KEY)
    if entry is None or not isinstance(entry.value, dict):
        return None
    table = entry.value.get(_LV_TABLE)
    if not isinstance(table, dict):
        return None
    bounds = table.get(band)
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return None
    low = float(bounds[0])
    return None if low <= 0 else low


def mic_asc_unpriced_capacity_leg_notes(site_model: SiteModel) -> list[str]:
    """State the MIC/ASC money the engine does not price, with the site's own measured numbers.

    MICASC-1/2 (Olly, 2026-08-17). ``valuation.build_value_streams`` hard-codes this stream to £0
    with the rationale "the connection-headroom value is realised through the connection-bounded
    arbitrage; pricing it separately would double-count". That rationale is sound **for the
    arbitrage reading of headroom and only for it**. There is a second, entirely separate £: the
    avoided DUoS **agreed-capacity** charge and the avoided **TCR fixed-residual band**, both of
    which fall when the peak falls — the agreed capacity is computed as
    ``capacity_charge × (asc_kw or mic_kw) × 365.25``, a **battery-invariant constant**, so peak
    shaving can never reduce THIS line and the avoided charge is structurally zero *here*.

    **SUPERSEDED IN PART (MICASC-3, Olly, 2026-08-17): the two amounts are now priced**, on their
    own stream — :class:`AgreedCapacityReductionStream` /
    ``MarketStream.AGREED_CAPACITY_REDUCTION`` — from a dedicated overlay solve that puts the
    agreed capacity in the dispatch objective (``MilpDispatchSolver.solve``'s capacity overlay).
    The claim "neither of which any code path models" was true when written and is no longer; what
    remains true, and is why this note stays, is that the MIC/ASC headroom stream's OWN £0 is the
    arbitrage reading of headroom and nothing else. Two corrections the pricing pass made to the
    original disclosure:

    * the band step must count the **TNUoS non-locational demand residual** as well as the DUoS
      banded fixed line — both are banded on the same agreed capacity, and the TNUoS leg is ~6x
      the larger. Costing the DUoS line alone understates the step ~7x;
    * the value is **incremental to renegotiation the customer could do with no battery at all**,
      which on these sites is the far bigger number (£15,039/yr at Bishops Cleeve MRF). That part
      is not battery revenue and is excluded from the priced figure.

    **DIRECTION OF EFFECT: UP, but OUTSIDE the priced floor.** The new stream is CONDITIONAL (it
    needs the customer to re-negotiate with the DNO), so it moves no IRR and no selection.
    """
    notes = [
        f"{MIC_ASC_UNPRICED_LEG_LABEL}: this stream reports £0 because connection headroom is "
        "realised through the connection-bounded arbitrage, and pricing it twice would "
        "double-count. That reasoning covers the ARBITRAGE reading of headroom ONLY. Two further "
        "amounts are real and are separate: (a) the avoided AGREED-CAPACITY charge, and (b) the "
        "avoided TCR FIXED-RESIDUAL band. Both fall when the peak falls. They are NOW PRICED, on "
        "their own stream (AGREED_CAPACITY_REDUCTION) — read that stream's £, not this £0."
    ]
    charge = site_model.tariff_stack.capacity_charge_gbp_per_kva_day
    agreed_kva = site_model.connection.asc_kw or site_model.connection.mic_kw
    if charge is not None and agreed_kva:
        notes.append(
            f"MEASURED ON THIS SITE: capacity charge £{charge:,.4f}/kVA/day against an agreed "
            f"{agreed_kva:,.1f} kVA = £{charge * agreed_kva * 365.25:,.0f}/yr. THIS BILL LINE is "
            "still computed battery-invariantly on both sides of the customer bill, which is "
            "correct: the bill states what the site is billed TODAY, and the agreed capacity has "
            "not been renegotiated. What changed is that the reduction the battery would make "
            "possible is now measured and reported, on AGREED_CAPACITY_REDUCTION."
        )
    notes.append(
        "PRICED SINCE MICASC-3 (2026-08-17) from an overlay solve that puts the agreed capacity "
        "in the dispatch objective. Rates: the site's own capacity line (or the release's pinned "
        "per-area duos...capacity_charge_p_per_kva_day), duos.tcr_band_thresholds_kva (LV bands "
        "0-80 / 80-150 / 150-231 / 231+ kVA, upper-inclusive) and "
        "tnuos.demand_residual_bands_gbp_per_day. DIRECTION OF EFFECT: UP — but the new stream is "
        "CONDITIONAL (it requires the customer to re-negotiate agreed capacity with the DNO), so "
        "it is upside OUTSIDE the priced floor and moves no IRR. This £0 is still the right "
        "number for the headroom-as-arbitrage reading."
    )
    return notes


class AgreedCapacityReductionStream(ContractedStream):
    """Avoided agreed-capacity charge + TCR band drop — CONDITIONAL, never in the priced floor.

    **The firmness argument, made explicitly rather than picked silently (MICASC-3).**

    FIRM was considered and rejected. The tempting case for FIRM is that £/kVA/day × agreed
    capacity is a pure bill mechanic: once the agreed capacity is lower, the saving is automatic
    and permanent, and DNOs routinely grant *reductions* (unlike increases, which need a network
    assessment). Three facts defeat it:

    1. **It needs an application that has not been made.** The charge falls when the connection
       agreement changes, not when the battery is commissioned. That is an act by the customer and
       a decision by the DNO — under D45 a permission, and a permission is never written as
       granted. Nothing in the site's own evidence says it has been sought.
    2. **The band leg cannot happen in year 1 even if the application succeeds.** The TCR band is
       allocated on a *24-month average MIC* and re-banded only on the DCUSA Schedule 32 criteria
       (``duos.tcr_band_thresholds_kva.allocation_basis``, verbatim in the release). A revenue line
       that structurally cannot arrive in the first two years is not firm.
    3. **The saving is bought with risk the customer carries.** A reduced agreed capacity that is
       later breached attracts excess-capacity charges — at the same £/kVA rate in every area
       fetched for gb_v1_11. The saving is therefore contingent on the battery performing at every
       annual peak for the life of the agreement. A lender does not size debt on that.

    The one genuinely FIRM variant of this money — avoided **excess-capacity** charges on a site
    already breaching its agreed capacity — needs no renegotiation at all and would belong in the
    floor. It is £0 across this whole portfolio: no site's metered peak exceeds its agreed capacity
    (Harwell is exactly equal, Tanhouse 13.65 kW under). It is not modelled because there is
    nothing to model, and that is a measurement, not an omission.

    **D57 (Olly, 2026-08-18) SUPERSEDES ALL THREE, AND THE GATE NOW OPENS.** The three facts above
    are all statements about a real-world approval: an application not yet made, a DCUSA Schedule 32
    re-banding decision, and a breach risk the customer carries. D57 assumes **every** contract,
    consent, gate and rule approved for the site and for the battery size under evaluation, and its
    status line overrides any local docstring that appears to justify a restriction — including this
    one. The DNO renegotiation D53 was waiting on an owner attestation for is **supplied by D57**,
    so the gate returns OPEN, the firmness is CONTRACTED, and the £ enters the priced floor, P50,
    P90, the DSCR, both IRRs and candidate selection.

    Nothing about the arithmetic changes and nothing is double-booked: this stream is the sole
    carrier of the avoided agreed-capacity charge and the avoided TCR fixed-residual band, and
    ``MIC_ASC_HEADROOM`` stays at £0 on that stated carrier (its own £0 note names this stream). The
    facts above are not deleted — they are the CONFIRMATION ASK the stream now publishes beside
    a priced figure, which is what D57 limb 4 requires. Direction of effect: **UP**.

    The one thing that stays forbidden (D45): the record says the renegotiation is **assumed
    approved**, which is true, never that the DNO has **granted** it, which would be a factual claim
    about a third party.
    """

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.AGREED_CAPACITY_REDUCTION,
            display_name="Avoided agreed capacity + TCR band",
            required_inputs=[
                RequiredInput(
                    key="connection.asc_kw",
                    description="Agreed supply capacity the site is billed on (ASC, or MIC).",
                    alternatives=("connection.mic_kw",),
                ),
                RequiredInput(
                    key="load_data.max_import_kw",
                    description="Measured peak import — the no-battery renegotiation "
                    "counterfactual this value is incremental to.",
                ),
                RequiredInput(
                    key="tariff_stack.capacity_charge_gbp_per_kva_day",
                    description="Agreed-capacity charge (£/kVA/day); derived from the release's "
                    "pinned per-area charging statement when the site carries none.",
                    alternatives=("connection.dno_region",),
                ),
            ],
            constraints=[_CONNECTION_CHARGE_LIMIT],
            assumption_keys=[
                "duos.tcr_band_thresholds_kva",
                "tnuos.demand_residual_bands_gbp_per_day",
            ],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        basis = agreed_capacity_basis(site_model, assumptions)
        if basis is None:
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=[
                    "an agreed capacity (connection.asc_kw or connection.mic_kw), a measured peak "
                    "(load_data.max_import_kw), and a £/kVA/day capacity charge — either the "
                    "site's own tariff line or connection.dno_region to derive the published one"
                ],
                warnings=[
                    "Avoided agreed capacity NOT ASSESSED: the site carries no capacity charge "
                    "and no licence area to derive one from, so there is no rate to price a "
                    "reduction at. £0 here is ABSENT, not measured — and no cross-area rate is "
                    "proxied in, which gb_v1_11 records as the defect that release exists to fix."
                ],
                consent_status=consent_status,
            )
        # D57 (Olly, 2026-08-18), item 6. This returned CONDITIONAL, which is outside
        # `finance.cashflows._PRICED_FIRMNESS`, so the stream moved no P50, P90, DSCR, IRR or
        # candidate selection. D53 chose that pending an owner attestation of the DNO
        # renegotiation; D57 SUPPLIES THAT ATTESTATION and assumes the renegotiation approved for
        # this site and this battery size, so the gate is OPEN and the firmness CONTRACTED.
        return self._gate(
            GateStatus.OPEN,
            unlock_condition=None,
            warnings=[
                "AVOIDED AGREED CAPACITY + TCR BAND IS IN THE PRICED FLOOR ON A D57 ASSUMPTION, "
                "NOT ON A COMPLETED RENEGOTIATION. What this figure assumes, and it is assumed "
                f"rather than evidenced: that the customer has applied to "
                f"{basis.dno_region or 'the DNO'} to reduce the agreed capacity on the connection "
                f"agreement from {basis.agreed_kva:,.0f} kVA and that the application has been "
                "APPROVED; and, for the TCR band leg, that the DCUSA Schedule 32 re-banding "
                "criteria are met on a 24-month average MIC basis, including the first two years "
                "in which a 24-month average cannot yet have moved. D53 held this stream out of "
                "the floor pending an owner attestation of exactly that renegotiation; D57 "
                "supplies the attestation on the owner's authority (2026-08-18) and the stream is "
                "priced in full. DIRECTION OF EFFECT: UP - it moves from conditional upside into "
                "the priced floor, the P50/P90 series, the DSCR, both IRRs and candidate "
                "selection. CONFIRMATION ASK: the varied connection agreement showing the reduced "
                "agreed capacity, and the DNO's TCR band allocation for the site after re-banding. "
                "NO POUND IS DOUBLE-BOOKED: this stream is the sole carrier of the avoided "
                "agreed-capacity charge and the avoided TCR band, and MIC_ASC_HEADROOM stays at "
                "GBP 0 on that stated carrier. NOT REPORTED AS AGREED (D45): the renegotiation is "
                "ASSUMED APPROVED, which is true; no DNO is asserted to have granted anything.",
                "RESIDUAL RISK THE ASSUMPTION CARRIES, STATED RATHER THAN PRICED: a reduced agreed "
                "capacity that is later breached attracts excess-capacity charges at the same "
                "GBP/kVA rate, so this saving is contingent on the battery performing at every "
                "annual peak for the life of the agreement. The one genuinely unconditional "
                "variant of this money - avoided EXCESS-capacity charges on a site already "
                "breaching its agreed capacity - is GBP 0 across this whole portfolio because no "
                "site's metered peak exceeds its agreed capacity. That is a measurement, not an "
                "omission.",
            ],
            consent_status=consent_status,
        )

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        # The value-stack read-model has no half-hourly series, so it cannot see a modelled peak;
        # the £ is the engine's, computed in `valuation.build_value_streams` from the overlay
        # solve's own worst-case net import. Reporting a per-MW proxy here would be a second,
        # disagreeing definition of the same number.
        return PricedValue(
            annual_revenue_gbp=0.0,
            warnings=(
                "Not priced from the value-stack read-model: this stream is worth the site's own "
                "modelled WORST-CASE net import against its own capacity rate, which needs the "
                "half-hourly schedule. The engine's dispatch path prices it "
                "(MilpDispatchSolver's agreed-capacity overlay); a £/MW/yr proxy here would be a "
                "second definition of the same number and the two would disagree.",
            ),
        )


class MicAscHeadroomStream(FloorStream):
    """Value of the connection (MIC/ASC) headroom — priced only where the connection binds."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.MIC_ASC_HEADROOM,
            display_name="MIC / ASC headroom value",
            required_inputs=[
                # Both requirements are DISJUNCTIONS, and saying so is load-bearing now that the
                # D45 guard reads the declaration literally: `gates.has_connection_limits` accepts
                # MIC *or* ASC and `gates.has_observed_peak` accepts a measured peak *or* HH
                # coverage, so declaring only the first path of each would close the gate on sites
                # whose requirement is genuinely met — a false refusal, not a guard.
                RequiredInput(
                    key="connection.mic_kw",
                    description="Connection limit (MIC, or ASC band) that may bind.",
                    alternatives=("connection.asc_kw",),
                ),
                RequiredInput(
                    key="load_data.max_import_kw",
                    description="Observed peak import (measured peak or half-hourly coverage).",
                    alternatives=("load_data.has_hh_coverage",),
                ),
            ],
            constraints=[_CONNECTION_CHARGE_LIMIT],
            assumption_keys=["mic_asc_headroom"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        missing: list[str] = []
        if not gates.has_connection_limits(site_model):
            missing.append("a connection limit (connection.mic_kw or connection.asc_kw)")
        if not gates.has_observed_peak(site_model):
            missing.append(
                "an observed peak import (load_data.max_import_kw or half-hourly coverage)"
            )
        if missing:
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=missing,
                warnings=[
                    "MIC/ASC headroom value cannot be assessed: missing " + ", ".join(missing) + "."
                ],
                consent_status=consent_status,
            )
        return self._gate(
            GateStatus.OPEN,
            warnings=[
                "MIC/ASC headroom value applies only where the connection (MIC/ASC) actually "
                "binds the dispatch.",
                *mic_asc_unpriced_capacity_leg_notes(site_model),
            ],
            consent_status=consent_status,
        )

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        return _priced_per_mw_yr(
            "mic_asc_headroom",
            "value_gbp_per_mw_yr",
            dispatch,
            assumptions,
            scenario,
            extra_note="£/MW/yr × sized power; accrues only where the connection (MIC/ASC) "
            "actually binds the dispatch.",
        )


# ---------------------------------------------------------------------------
# Contracted-when-it-holds (deliverability / tender gated, via GateContext)
# ---------------------------------------------------------------------------


class CapacityMarketStream(ContractedStream):
    """Capacity Market — de-rated by duration; priced only if deliverability holds."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.CAPACITY_MARKET,
            display_name="Capacity Market",
            required_inputs=[
                RequiredInput(
                    key="deliverability.degraded_capability",
                    description="Verified de-rated deliverability to the final contract year at "
                    "degraded (end-of-life) capability. Supplied via GateContext, not SiteModel.",
                ),
            ],
            constraints=[_CM_DELIVERABILITY, _CM_HELD_SOE],
            assumption_keys=["capacity_market"],
        )

    def _unevidenced_capacity_gate(
        self,
        threshold: float,
        verdict: gates.EligibilityVerdict,
        consent_status: str | None,
    ) -> GateResult:
        """UNKNOWN: the CM question cannot be answered because the capacity input is absent (N-1).

        ``UNKNOWN`` is this gate layer's own word for "could not be determined; treated
        conservatively (not priced)" (`GateStatus`), and it is the status D45's no-null amendment
        asks for when derivation has not happened: "the affected £ is **refused** rather than
        silently read as zero". It refuses on both pricing paths by construction — the engine
        prices only a priceable-or-conditional gate, and the proxy's ``value_function`` returns
        straight from the gate for CLOSED/UNKNOWN — so neither path can reach a rate, a de-rating
        factor or a sizing output to build a number out of.

        CONDITIONAL was the wrong status here even though it also leaves the priced floor: it is
        priceable-for-upside, so the £ survived as a visible "conditional" Capacity Market line
        computed from the very sizing output that was never evidence of anything.

        Consent stays RECORDED. Consent and eligibility are orthogonal and conflating them in
        *either* direction is a defect: consent cannot open a market the site cannot enter, and an
        ineligible market cannot erase the customer's authorisation.
        """
        warnings = [
            "Capacity Market REFUSED (not merely unconfirmed): this site evidences no connection "
            "capacity and no export capacity, so the quantity The Electricity Capacity Regulations "
            f"2014 (SI 2014/2043) reg 15(4) tests against the {threshold:,.3f} MW minimum — the "
            "CMU's connection capacity, or its DSR capacity — is not present in the site data at "
            "all. No £ is priced on either path, and the sized battery's power is NOT accepted "
            "as a substitute for the missing evidence.",
            "An ABSENT input must never resolve to ELIGIBLE, and this one would have ADDED "
            "revenue to the priced floor the DSCR covenant is measured on. D45's no-null "
            "amendment governs: a null on the pricing path is a defect to derive-and-declare or "
            "refuse, never a state to report favourably.",
            *verdict.reasons,
        ]
        if consent_status is not None:
            warnings.append(
                "The customer HAS consented to the Capacity Market; the consent is RECORDED and "
                "unaffected. Consent authorises pursuit, it does not evidence a connection — and "
                "an unanswerable eligibility question does not withdraw the authorisation."
            )
        return self._gate(
            GateStatus.UNKNOWN,
            missing_requirements=[
                "the CMU's connection capacity (connection.mec_kw for the generating route) or its "
                "DSR capacity (connection.mic_kw / connection.asc_kw for the DSR route) — recorded "
                "from evidence, or declared offline as a D45 derived_input"
            ],
            unlock_condition=(
                "record the site's connection capacity (MIC/ASC) and export capacity (MEC), or "
                "declare one as a D45 derived_input with its basis and confirmation ask; the "
                f"CMU then needs at least {threshold:,.3f} MW on one of the two statutory routes"
            ),
            warnings=warnings,
            consent_status=consent_status,
        )

    def _prequalification_gate(
        self,
        site_model: SiteModel,
        context: GateContext | None,
        consent_status: str | None,
    ) -> GateResult | None:
        """A non-priceable gate when this site cannot PREQUALIFY for the CM, else ``None``.

        The Capacity Market's 1 MW minimum is a **prequalification** threshold on connection (or
        DSR) capacity, not a de-rating input: below it there is no CMU to hold an agreement, so no
        clearing price applies at any de-rating factor. Tested on the capacity the site actually
        evidences (:func:`capacity_market_cmu_capacity`), which at gate time is the whole
        connection's own bound — generous by construction, so a refusal means no battery on this
        connection could prequalify standalone, never a wrongly-blocked site a bigger unit clears.

        Two distinct refusals, which must never be collapsed into one (N-1):

        - **Nothing evidenced** ⇒ ``UNKNOWN``. The site records neither connection nor export
          capacity, so the question reg 15(4) asks cannot be answered at all. UNKNOWN is not
          priceable, so **no £ is produced on either path** and the sized power can never be read
          as the missing evidence. Previously this case fell through to the deliverability gate and
          a confirmed-deliverability site went OPEN ⇒ CONTRACTED, booking CM revenue in the priced
          floor off nothing but a sizing outcome.
        - **Evidenced but short** ⇒ ``CONDITIONAL``, with the unlock condition. Something was
          disproven, and an aggregated DSR CMU is a real route out, so the stream stays visible.
        """
        threshold = eligibility_threshold_mw(self.stream_id)
        if threshold is None:  # pragma: no cover - defensive; the CM gate is armed above
            return None
        portfolio_mw = context.portfolio_mw if context is not None else None
        # No sizing output at gate time, and none is wanted: the gate tests the site's own evidence.
        verdict = capacity_market_entry_verdict(site_model, unit_mw=None, portfolio_mw=portfolio_mw)
        if verdict.route == CM_UNEVIDENCED_ROUTE:
            return self._unevidenced_capacity_gate(threshold, verdict, consent_status)
        if verdict.route != "below_threshold":
            return None
        bound_mw = capacity_market_cmu_capacity(site_model).mw or 0.0
        warnings = [
            f"Capacity Market NOT ELIGIBLE for this site: prequalification requires at least "
            f"{threshold:,.3f} MW of CONNECTION capacity and the site's whole connection presents "
            f"at most {bound_mw:,.3f} MW (the better of the generating route's export capacity and "
            "the DSR route's import capacity), so no battery on this connection can prequalify "
            "standalone. Source: The Electricity Capacity Regulations 2014 "
            "(SI 2014/2043) regulation 15 — 'Directly excludes standalone BTM batteries below 1 MW "
            "from the CM unless aggregated into a multi-site DSR CMU'.",
            "This is a PREQUALIFICATION bar, not a de-rating haircut: below the threshold there is "
            "no CMU to hold an agreement, so the clearing price applies to nothing. It is tested "
            "on connection capacity, NOT on de-rated capacity — the register row is explicit that "
            "the threshold 'applies to connection/DSR capacity, not de-rated capacity'.",
            *verdict.reasons,
        ]
        if consent_status is not None:
            warnings.append(
                "The customer HAS consented to the Capacity Market; the consent is recorded and "
                "the stream stays visible as conditional upside, but it earns £0 until an eligible "
                "route exists. Consent authorises pursuit, not participation."
            )
        return self._gate(
            GateStatus.CONDITIONAL,
            missing_requirements=[
                f"at least {threshold:,.3f} MW of connection capacity, or aggregation into a "
                "multi-site DSR CMU"
            ],
            unlock_condition=(
                f"a connection capacity of at least {threshold:,.3f} MW, or aggregation into a "
                "multi-site DSR CMU (which brings its own Proven/Unproven DSR evidence, credit "
                "cover and DSR de-rating basis)"
            ),
            warnings=warnings,
            consent_status=consent_status,
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        # CM keeps its deliverability physics: consent authorises the stream but can NEVER
        # bypass verified de-rated deliverability (locked D43 decision). Consent is stamped as
        # the orthogonal label only.
        consent_status = _consent_status(site_model, self.stream_id)
        # D-92 / N-1: prequalification eligibility is tested FIRST — before deliverability, because
        # a market the site cannot prequalify for at all is not merely unconfirmed. The threshold is
        # on the CMU's connection or DSR capacity (SI 2014/2043 reg 15(4)), so the basis is the
        # site's own EVIDENCE on the two statutory routes — the export capacity for the generating
        # route, the import capacity (MIC/ASC, or a D45-declared value) for the DSR route — and no
        # sizing output is needed or wanted. Evidencing neither refuses the stream outright
        # (UNKNOWN, not priceable on either path) rather than letting a sized power stand in for the
        # missing evidence.
        ineligible = self._prequalification_gate(site_model, context, consent_status)
        if ineligible is not None:
            return ineligible
        deliverability = context.deliverability_confirmed if context is not None else None
        if not gates.deliverability_confirmed(deliverability):
            if consent_status is not None:
                # D57 (Olly, 2026-08-18). The comment above says consent "can NEVER bypass verified
                # de-rated deliverability (locked D43 decision)". D57 supersedes D43 on stream
                # eligibility and unlocks exactly this: "every contract, consent, gate, award,
                # registration, threshold and rule is ASSUMED APPROVED for the site AND FOR THE
                # BATTERY SIZE UNDER EVALUATION". A CM agreement assumed approved at this size is an
                # agreement whose de-rated deliverability is assumed to hold, so the gate opens.
                #
                # What is NOT asserted, and the distinction is load-bearing: the end-of-tenor
                # capability check did not RUN. `context.deliverability_confirmed` stays None, so
                # `valuation._capacity_market_value` still emits its honest "end-of-tenor
                # deliverability NOT confirmed" note beside the pound. The assumption is published
                # AS an assumption; no check is claimed that was not performed (D45).
                #
                # DIRECTION OF EFFECT: UP. CAPACITY_MARKET moves from CONDITIONAL (outside the
                # priced floor, so outside P50, P90, the DSCR and both IRRs) to CONTRACTED (inside
                # all of them) on every consented site.
                return self._gate(
                    GateStatus.OPEN,
                    warnings=[
                        "CAPACITY MARKET GATE OPENED ON A D57 ASSUMPTION, NOT ON A CHECK. The "
                        "de-rated deliverability of this capacity to the final CM contract year at "
                        "degraded (end-of-life) capability is UNVERIFIED: no CM "
                        "availability-window "
                        "commitment is compiled on this site, so the end-of-tenor capability check "
                        "(W7-3) had nothing to assess and did not run. Under D57 the CM agreement "
                        "is ASSUMED APPROVED for this site and this battery size, which assumes "
                        "that deliverability, and the stream is therefore priced into the floor. "
                        "CONFIRMATION ASK: a de-rating and capability schedule to the final "
                        "contract year, sized day-one with no augmentation, plus the CM agreement "
                        "itself. DIRECTION OF EFFECT: UP - this assumption moves the stream from "
                        "conditional upside into the priced floor, the P50/P90 series and the DSCR."
                    ],
                    consent_status=consent_status,
                )
            return self._gate(
                GateStatus.CONDITIONAL,
                missing_requirements=[
                    "confirmed de-rated deliverability to the final CM contract year at "
                    "degraded (end-of-life) capability"
                ],
                unlock_condition="verify de-rated deliverability holds to the final contract "
                "year at degraded capability (sized day-one; no augmentation)",
                warnings=[
                    "Capacity Market not priced into the floor: deliverability at degraded "
                    "capability is unverified — conditional, not firm."
                ],
                consent_status=consent_status,
            )
        return self._gate(GateStatus.OPEN, consent_status=consent_status)

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        # D-92 / N-1: prequalification eligibility, checked before any rate is read — a market the
        # CMU cannot prequalify for is worth £0, not a de-rated fraction of the clearing price.
        #
        # The dispatched size is passed as a CAP on the evidenced capacity, never as the evidence:
        # reg 15(4) tests the CMU's connection or DSR capacity, so a site evidencing neither is
        # ineligible at any sized power. `check_gates` refuses that case before pricing is reached
        # at all; passing `site_model` here keeps the money side true on its own terms rather than
        # relying on the gate to have caught it.
        cm_verdict = capacity_market_entry_verdict(
            site_model,
            unit_mw=dispatch.power_mw,
            portfolio_mw=context.portfolio_mw if context is not None else None,
        )
        cm_elig_notes = list(cm_verdict.reasons) + aggregation_route_notes(
            cm_verdict, self.stream_id
        )
        if cm_verdict.route == "not_armed":  # pragma: no cover - the CM gate is armed
            cm_elig_notes.append(ELIGIBILITY_DATA_ASK)
        if not cm_verdict.eligible:
            cm_elig_notes.append(
                "£0: the Capacity Market cannot be PREQUALIFIED for at this unit's size "
                "(SI 2014/2043 reg 15, 1 MW of connection capacity), so it earns nothing at any "
                "de-rating factor or clearing price. Held out of the priced floor, the P50/P90 "
                "series and the DSCR."
            )
            return PricedValue(0.0, 0.0, tuple(cm_elig_notes), ())
        clearing_a = _maybe(assumptions, "capacity_market", "clearing_price_gbp_per_kw_yr")
        derate_a = _maybe(assumptions, "capacity_market", "de_rating_factor_by_duration_h")
        fade_a = _maybe(assumptions, "degradation", "annual_capacity_fade_pct")
        clearing = _num(clearing_a)
        if derate_a is None:
            return PricedValue(
                0.0, 0.0, ("not priced: capacity_market clearing price / de-rating missing.",), ()
            )
        # Rate ladder (D43): a customer-agreed CM rate (£/kW/yr) wins over the assumption clearing
        # price when consented; else the assumption clearing price; else £0.
        consented_rate = _consent_agreed_rate(site_model, MarketStream.CAPACITY_MARKET)
        derate, derate_warn = _derate_for_duration(derate_a.value, dispatch.duration_h)
        if derate is None:
            return PricedValue(0.0, 0.0, (f"not priced: {derate_warn}.",), ())
        if consented_rate is not None:
            clearing = consented_rate
            used = ["capacity_market.de_rating_factor_by_duration_h"]
            notes = [
                f"Capacity Market priced at the customer-agreed rate £{consented_rate:,.2f}/kW/yr "
                "(consent given), ahead of the assumption clearing price (estimation ladder, D43); "
                "not bankable — a commercially-agreed rate, not a market-cleared price."
            ]
        elif clearing_a is None or clearing is None:
            return PricedValue(
                0.0, 0.0, ("not priced: capacity_market clearing price / de-rating missing.",), ()
            )
        else:
            used = [
                "capacity_market.clearing_price_gbp_per_kw_yr",
                "capacity_market.de_rating_factor_by_duration_h",
            ]
            notes = [
                _placeholder_note("capacity_market", "clearing_price_gbp_per_kw_yr", clearing_a)
            ]
        # The deliverability profile: capability held to the final contract year at degraded
        # capability. Prefer the engine-verified figure; else de-rate the sized power and say so.
        if dispatch.deliverable_capacity_mw is not None:
            derated_mw = dispatch.deliverable_capacity_mw
            notes.append(
                f"de-rated deliverable capacity {derated_mw} MW (engine-verified end-of-life "
                f"capability, duration {dispatch.duration_h}h)."
            )
        else:
            derated_mw = dispatch.power_mw * derate
            fade = _num(fade_a)
            notes.append(
                f"de-rated capacity = sized power {dispatch.power_mw} MW × de-rating {derate} "
                f"(duration {dispatch.duration_h}h); must hold to the final contract year at "
                f"degraded capability (degradation fade {fade if fade is not None else 'n/a'}%/yr; "
                f"day-one sizing holds end-of-life — engine-verified, no augmentation)."
            )
            if fade_a is not None:
                used.append("degradation.annual_capacity_fade_pct")
        if derate_warn:
            notes.append(derate_warn + ".")
        mult = _price_multiplier(scenario)
        revenue = clearing * (derated_mw * 1000.0) * mult  # £/kW/yr × kW
        if mult != 1.0:
            notes.append(f"scenario price multiplier {mult} applied.")
        if gate.gate_status == GateStatus.CONDITIONAL:
            notes.append(
                "CONDITIONAL upside — held OUT of the priced floor until de-rated "
                "deliverability at degraded capability is confirmed."
            )
        # The eligibility working travels with the £ even when the verdict was favourable, so a
        # reader can see WHICH route (standalone or aggregated DSR CMU) this figure depends on.
        notes.extend(cm_elig_notes)
        return PricedValue(revenue, 0.0, tuple(notes), tuple(used))


#: Fraction of contracted local-flex availability that is actually instructed as a paid call.
#:
#: The default is a DNO's own published figure: NGED's revenue-estimation tool assumes **5%**
#: utilisation of available hours for Scheduled Availability / Operational Utilisation (day-ahead
#: notice), valid 23/09/2025–31/03/2026 — `docs/bess_constants_register.md` row "Assumed utilisation
#: rate for SAOU (Scheduled Availability, Operational Utilisation – Day Ahead Notice)".
#: Corroborating
#: rows in the same register: UKPN's indicative utilisation÷availability ratio across 126
#: zone-seasons has a median of 22% (min 14%, max 37%); only 19% of NGED's day-ahead constraint
#: zones received ANY dispatch in 2024/25 (SAOU_DA, 14 of 73).
#:
#: 5% is the DEFAULT because it is a DNO's own published assumption for its own product and sits at
#: the conservative end of the sourced range — understating this revenue is the safe direction for
#: underwriting. Override per tender/region via the assumption library.
#:
#: **This constant and :func:`local_flex_call_rate` are the SINGLE definition shared by both pricing
#: paths.** `gridq.optimisation.valuation` imports them (import direction: valuation → value_stack,
#: never the reverse). Before this, the engine path applied the call rate and the proxy path did
#: not, so the two disagreed by 20x on a CONTRACTED stream feeding the priced floor, P50/P90 and
#: the DSCR.
_DEFAULT_LOCAL_FLEX_CALL_RATE = 0.05
_LOCAL_FLEX_CALL_RATE_KEY = ("local_flex_piclo", "utilisation_call_rate")


def local_flex_call_rate(assumptions: AssumptionLibrary) -> tuple[float, list[str], str]:
    """The fraction of in-window delivered energy that is actually an instructed, paid call.

    Returns ``(call_rate, assumption_keys_used, note)``. A library value is honoured only when it
    is numeric and within ``[0, 1]``; otherwise the sourced default above is used and the note says
    so loudly. Shared by the engine path (`valuation._local_flex_value`) and the proxy path
    (`LocalFlexPicloStream._price`) so the two cannot drift apart again.
    """
    entry = _maybe(assumptions, *_LOCAL_FLEX_CALL_RATE_KEY)
    value = _num(entry)
    if entry is not None and value is not None and 0.0 <= value <= 1.0:
        return (
            value,
            [".".join(_LOCAL_FLEX_CALL_RATE_KEY)],
            _placeholder_note(*_LOCAL_FLEX_CALL_RATE_KEY, entry),
        )
    return (
        _DEFAULT_LOCAL_FLEX_CALL_RATE,
        [],
        f"local-flex utilisation applied at a {_DEFAULT_LOCAL_FLEX_CALL_RATE:.0%} CALL RATE — the "
        "assumption library carries no local_flex_piclo.utilisation_call_rate, so the documented "
        "sourced default is used (NGED's own revenue-estimation tool assumes 5%; UKPN's median "
        "utilisation-to-availability ratio is 22%). Availability is NOT utilisation: pricing every "
        "in-window discharge as an instructed call overstates this stream by roughly 5-20x.",
    )


# ---------------------------------------------------------------------------
# EAC dynamic-response settlement structure — D-158.
#
# DC/DM/DR used to enter the contracted floor as ONE lumped £/MW/yr with no availability factor F,
# no performance factor K, no EFA-block shape and no product split; combined with the missing 1 MW
# gate that made it the floor's most optimistic line. The structure below is sourced; the two
# de-rating factors are NOT invented (see `eac_response_value`).
# ---------------------------------------------------------------------------

#: The six Enduring Auction Capability dynamic-response products. Register row "EAC / Dynamic
#: Response order granularity": day-ahead co-optimised auctions "for DCL, DCH, DML, DMH, DRL, DRH".
#: The assumption library's own £/MW/yr derivation is the mean clearing price across exactly these
#: six ("4032 EFA-block rows"), which is why they are the correct decomposition of that number.
_EAC_RESPONSE_PRODUCTS: tuple[str, ...] = ("DCL", "DCH", "DML", "DMH", "DRL", "DRH")

#: EFA blocks are 4 hours, 6 per day. Register row "Slow Reserve minimum delivery duration":
#: "Service windows are 30 minutes; EFA blocks are 4 hours (8 windows)."
_EFA_BLOCK_HOURS = 4.0
_EFA_BLOCKS_PER_DAY = 6
_DAYS_PER_YEAR = 365.0
#: 6 blocks x 4 h x 365 d = 8760 h — the same annualisation the library's £/MW/yr key uses
#: ("mean cleared £/MW/h across DC/DM/DR x 8760 h"), so the decomposition is exact, not a re-scale.
_EFA_HOURS_PER_YEAR = _EFA_BLOCK_HOURS * _EFA_BLOCKS_PER_DAY * _DAYS_PER_YEAR

#: Assumption homes for the two settlement de-rating factors. Both are read, never defaulted to a
#: number: an absent key produces a labelled data ask and an explicit upper-bound warning.
_AVAILABILITY_FACTOR_KEY = ("availability", "expected_availability_pct")
_PERFORMANCE_FACTOR_KEY = ("ancillary_services", "performance_factor_k")


def _settlement_factor(
    assumptions: AssumptionLibrary, key: tuple[str, str], *, as_percent: bool, label: str, ask: str
) -> tuple[float, list[str], list[str]]:
    """Resolve a 0-1 settlement de-rating factor from the library, or refuse to invent one.

    Returns ``(factor, keys_used, notes)``. A missing / non-numeric / out-of-range entry yields
    ``1.0`` **plus a loud upper-bound warning naming the key and the register evidence** — the
    figure is then explicitly an over-statement rather than a silently de-rated number.
    """
    entry = _maybe(assumptions, *key)
    raw = _num(entry)
    if entry is not None and raw is not None:
        factor = raw / 100.0 if as_percent else raw
        if 0.0 <= factor <= 1.0:
            return (
                factor,
                [".".join(key)],
                [
                    f"{label} {factor:.4f} applied from {'.'.join(key)}.",
                    _placeholder_note(*key, entry),
                ],
            )
    return 1.0, [], [f"{label} NOT applied (held at 1.0 — an UPPER BOUND). {ask}"]


def eac_response_value(
    *,
    rate_gbp_per_mw_yr: float,
    sellable_mw: float,
    assumptions: AssumptionLibrary,
    toll_reference: str | None = None,
) -> tuple[float, list[str], list[str]]:
    """Value DC/DM/DR at EFA-block granularity, netted by availability F and performance K (D-158).

    Returns ``(revenue, assumption_keys_used, notes)``.

    Structure, all sourced to `docs/bess_constants_register.md`:

    - **Six products, never summed.** DCL/DCH/DML/DMH/DRL/DRH are separate products, and
      "Stacking two different DFR services together is NOT permitted (e.g. DC+DM, DM+DR)" — so at
      most ONE of them is held in any EFA block. The lumped £/MW/yr is therefore a *single-product,
      all-blocks* figure, and reporting six additive product lines would be a straight double count.
    - **EFA-block granularity.** 6 blocks x 4 h x 365 d = 8760 block-hours/yr, matching the
      library key's own derivation (mean cleared £/MW/h x 8760).
    - **Availability factor F.** "F = 0 for the entire settlement period if availability < 99.9%" —
      a brutal binary, and "K_e = min_j k_j — the MINIMUM across the block", so one degraded
      settlement period zeroes a whole block. Its EXPECTED value is a modeller input, read from
      ``availability.expected_availability_pct``.
    - **Performance factor K.** The register carries only the piecewise error formula
      ("k = 1 if error E < 0.03; k = 1 - (E - 0.03)/(0.07 - 0.03) if 0.03 <= E <= 0.07; k = 0 if
      E > 0.07"), which needs measured delivery error — there is **no sourced expected K**, so none
      is invented.

    ``toll_reference`` is the ONLY exemption: for a toll whose contract reference states that
    availability risk sits with the counterparty, the £/MW/yr shortcut is kept as-is (F and K are
    the counterparty's problem, not the asset's), and the reference is recorded on the note.

    **Boundary with H-17:** F here is the *product's* within-block availability factor. The
    fleet-level availability haircut on all revenue is finance's (H-17, `finance/cashflows.py`)
    — never both, or the same outage is charged twice.
    """
    per_mw_h = rate_gbp_per_mw_yr / _EFA_HOURS_PER_YEAR
    notes = [
        f"DC/DM/DR valued at EFA-block granularity: £{per_mw_h:,.4f}/MW/h x {sellable_mw:,.0f} MW "
        f"x {_EFA_BLOCK_HOURS:.0f} h/block x {_EFA_BLOCKS_PER_DAY} blocks/day x "
        f"{_DAYS_PER_YEAR:.0f} days = {_EFA_HOURS_PER_YEAR:,.0f} block-hours/yr.",
        "ONE product per EFA block, never six added together: the six EAC products "
        f"({', '.join(_EAC_RESPONSE_PRODUCTS)}) are alternatives, and GB forbids stacking two "
        "dynamic frequency-response services together (register: 'Stacking two different DFR "
        "services together is NOT permitted'). Which product is held in which block comes from the "
        "auction award — DATA ASK: no award record was supplied, so the whole year is attributed "
        "to a single unspecified DC/DM/DR product at the library's blended rate.",
    ]
    if toll_reference is not None:
        notes.append(
            f"TOLL EXEMPTION applied under contract reference {toll_reference!r}: availability "
            "risk "
            "sits with the counterparty, so the £/MW/yr shortcut is kept and neither the "
            "availability factor F nor the performance factor K is deducted from the asset."
        )
        return rate_gbp_per_mw_yr * sellable_mw, [], notes
    f_factor, f_used, f_notes = _settlement_factor(
        assumptions,
        _AVAILABILITY_FACTOR_KEY,
        as_percent=True,
        label="EAC availability factor F",
        ask=(
            "DATA ASK: availability.expected_availability_pct is not populated in the assumption "
            "library, and no expected value for the binary 99.9%-per-settlement-period factor is "
            "sourced in docs/bess_constants_register.md — so it was NOT invented. The register's "
            "GB BESS fleet availability row (95% average from September 2024, down from 97-98%) is "
            "the candidate basis; land it in a new assumption release. NOTE the boundary: this is "
            "the PRODUCT's within-block factor, not the fleet-level haircut on all revenue (H-17, "
            "finance/cashflows.py) — applying both would charge the same outage twice."
        ),
    )
    k_factor, k_used, k_notes = _settlement_factor(
        assumptions,
        _PERFORMANCE_FACTOR_KEY,
        as_percent=False,
        label="EAC performance factor K",
        ask=(
            "DATA ASK: ancillary_services.performance_factor_k does not exist in any assumption "
            "release, and docs/bess_constants_register.md carries only the piecewise error formula "
            "for k (k = 1 if E < 0.03; taper to 0 at E > 0.07) with NO published expected value — "
            "so no K < 1.0 was invented. Delivered performance is measured from 20 Hz data and the "
            "block factor is the MINIMUM across settlement periods, so a realistic K is below 1.0 "
            "and this figure is an upper bound."
        ),
    )
    revenue = rate_gbp_per_mw_yr * sellable_mw * f_factor * k_factor
    notes.extend(f_notes)
    notes.extend(k_notes)
    notes.append(
        f"net availability revenue = £{rate_gbp_per_mw_yr:,.2f}/MW/yr x {sellable_mw:,.0f} MW "
        f"x F {f_factor:.4f} x K {k_factor:.4f} = £{revenue:,.2f}/yr."
    )
    return revenue, f_used + k_used, notes


def _aggregator_cut(site_model: SiteModel, assumptions: AssumptionLibrary) -> tuple[float, str]:
    """Aggregator / route-to-market take on gross local-flex revenue: ``(cut_fraction, source)``.

    Precedence: a per-site agreed rate (``SiteModel.export_arrangement.aggregator_cut_pct``) wins;
    else the assumption default (``local_flex_piclo.aggregator_cut_pct``); else ``0.0`` — no cut is
    invented, so gross == net when neither is supplied (byte-identical to the pre-018 pricing, and
    to any release without the key, e.g. gb_v0_1).
    """
    arrangement = getattr(site_model, "export_arrangement", None)
    if arrangement is not None and arrangement.aggregator_cut_pct is not None:
        return float(arrangement.aggregator_cut_pct), "site export arrangement"
    assumption = _maybe(assumptions, "local_flex_piclo", "aggregator_cut_pct")
    value = _num(assumption)
    if assumption is not None and value is not None:
        return float(value), "assumption"
    return 0.0, "none"


#: The library entry holding the AWARDED CLEARS for the constraint-management zones the campaign
#: sites sit in — what the DNO actually PAID, per licence area (D54, gb_v1_11/gb_v1_12).
_AWARDED_ZONE_CLEARS_KEY = ("local_flex_piclo", "awarded_zone_clears")


def local_flex_awarded_zone_clear(
    site_model: SiteModel, assumptions: AssumptionLibrary
) -> tuple[dict[str, object] | None, str, list[str]]:
    """The site's OWN licence area's awarded local-flex clear, or ``None`` — **D57 limb 5b**.

    Returns ``(row, row_key, notes)``. The row is the entry under
    ``local_flex_piclo.awarded_zone_clears`` whose key begins with the site's
    ``connection.dno_region`` token, lower-cased — so a row from a DIFFERENT licence area is never
    substituted, which is the one substitution the run record itself flags as a defect.

    **Why this function exists, and what changed.** The entry's own note says "they are recorded as
    CONTEXT: no code prices an award from this entry, and D30's empty home on
    ``availability.availability_price_gbp_per_mw_h`` is unchanged". D57 limb 5b (Olly, 2026-08-18)
    retires exactly that refusal, verbatim on the point: where a sourced price is *"an awarded
    contract rate, a participant-only rate, a tender-specific clear, a registered-unit rate, or any
    figure that in the real world requires a contract, award, registration, consent or membership
    the
    site does not hold: it is used in full."* The award exists by assumption under limb 2, so the
    award price applies. D30's null stays where it is — it is the UNDERWRITING home for an
    availability price and is not repealed — and this function is the study-basis path around it.

    What limb 5b still requires, because it is correctness and not eligibility: the rate is real,
    cited, and LABELLED for what it is. The notes returned here carry the label and the citation.
    """
    region = getattr(getattr(site_model, "connection", None), "dno_region", None)
    entry = _maybe(assumptions, *_AWARDED_ZONE_CLEARS_KEY)
    key = ".".join(_AWARDED_ZONE_CLEARS_KEY)
    if entry is None or not isinstance(entry.value, dict):
        why = (
            f"NO AWARDED-CLEAR TABLE IN THIS RELEASE ({key} absent), so no awarded availability "
            "or utilisation rate can be applied to this site's award. Nothing is substituted."
        )
        return None, key, [why]
    if region is None:
        why = (
            "SITE RECORDS NO LICENCE AREA (connection.dno_region is null), so its own zone's "
            f"awarded clear cannot be selected from {key}. A row from another licence area is NOT "
            "substituted. DATA ASK: the site's DNO licence area."
        )
        return None, key, [why]
    token = str(region).lower()
    for row_key, row in entry.value.items():
        if row_key == "note" or not isinstance(row, dict):
            continue
        if not row_key.startswith(token):
            continue
        label = (
            f"AWARDED CLEAR USED IN FULL (D57 limb 5b): {key}.{row_key} is what {region} ACTUALLY "
            "PAID in this site's own constraint-management zone - an AWARDED TENDER CLEAR, not a "
            "guide price, not a median and not a ceiling median. It requires an award the site "
            "does not evidence holding, and under D57 limb 2 that award is ASSUMED APPROVED, so "
            "the awarded price applies. Direction of the awarded product: "
            f"{row.get('direction', 'not recorded')}. CONFIRMATION ASK: the site's own signed "
            "flexibility contract, so the rate stops being the zone's clear and becomes the "
            "site's own."
        )
        return dict(row), f"{key}.{row_key}", [label]
    why = (
        f"NO AWARDED CLEAR IS RECORDED FOR THIS SITE'S LICENCE AREA ({region}) in {key}, and a row "
        "from a DIFFERENT licence area is NOT substituted - a zone ceiling is a hard cap on that "
        "zone, so another DNO's clear can exceed anything this site could be paid. DATA ASK: the "
        "awarded availability and utilisation rates for this site's own zone."
    )
    return None, key, [why]


def local_flex_award_window_hours(
    award: object, zone_clear: dict[str, object] | None = None
) -> tuple[float | None, str]:
    """Annual contracted availability hours for an award: the PUBLISHED count, else derived.

    Returns ``(hours_per_year, basis)``, or ``(None, why)`` when neither is available. This
    is the missing input that kept the availability leg at £0: `local_flex_availability_value`
    refuses to price without contracted window hours, and its production call site passed ``None``.

    **THE LADDER, corrected 2026-08-19.** The awarded zone row that supplies the availability RATE
    also publishes that service's own annual availability-hour count, and where it does, that count
    governs. It used to be re-derived from the award's declared clock times instead, and the two
    disagree: SSEN's Wallingford VAOU row publishes ``136 availability h`` while a re-derivation
    from 17:00-19:00 over December, January and February gives 2 h/day x 90 days = 180 h, a 1.32x
    overstatement. 136 h is also the figure that RECONCILES to the tender's own budget: the row's
    cross-check reads "the largest award (Octopus 0.29 MW at GBP 183.08 over 136 h = GBP 7,220 +
    GBP 144 utilisation) against SSEN's own published GBP 8,200 budget for that tranche is 90 pct".
    180 h does not reconcile to it. DIRECTION OF EFFECT: DOWN, 1.32x, on every site in the SSEN SEPD
    licence area. NGED's Port Ham row publishes 1,098 h/MW/yr and the derivation from its declared
    window (6 h/day x 183 delivery dates) gives exactly 1,098, so the two agree there and the
    change is a no-op on every NGED site.

    Where the row publishes no count the DERIVATION from the award itself is kept - the months, days
    of week and clock times it declares - never a library default, so the figure is either the
    service's own published hours or the contract's own window and never a constant.

    Day count per month uses a fixed 365-day non-leap calendar so the figure is deterministic across
    delivery years (the award's tenor is what varies, not this shape).
    """
    published = None if zone_clear is None else zone_clear.get("availability_h_per_year")
    if isinstance(published, int | float) and float(published) > 0:
        return (
            float(published),
            "PUBLISHED BY THE AWARDED SERVICE ITSELF, not re-derived: the zone row that supplies "
            f"the availability rate publishes {float(published):,.1f} availability h/yr for this "
            "service, and that count governs over a re-derivation from the award's declared clock "
            "times",
        )
    windows = list(getattr(award, "windows", None) or [])
    if not windows:
        return None, "the award declares no service window"
    days_in_month = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    total = 0.0
    parts: list[str] = []
    for w in windows:
        start, end = getattr(w, "start_time", None), getattr(w, "end_time", None)
        if start is None or end is None:
            continue
        span_h = ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) / 60.0
        if span_h <= 0:  # an overnight window wraps midnight
            span_h += 24.0
        months = list(getattr(w, "months", None) or range(1, 13))
        dows = getattr(w, "days_of_week", None)
        days = 0.0
        for m in months:
            n = float(days_in_month[int(m) - 1])
            # A day-of-week restriction scales the month's days by the share of the week it covers.
            days += n if not dows else n * (len(set(dows)) / 7.0)
        total += span_h * days
        parts.append(
            f"{getattr(w, 'label', 'window')}: {span_h:g} h/day x {days:,.1f} delivery days"
        )
    if total <= 0:
        return None, "the award's service windows carry no usable clock times"
    return total, (
        "DERIVED FROM THE AWARD'S OWN SERVICE WINDOWS, because the zone row publishes no "
        f"availability-hour count of its own ({'; '.join(parts)})"
    )


# ---------------------------------------------------------------------------
# ENA Open Networks local-flex settlement formulas — D-116.
#
# Local flex was priced utilisation-only: no availability payment (an UNDERSTATEMENT, since SAOU
# contracts earn most of their £ there) and no Performance Multiplier or Payable-Over-Delivery cap
# (an OVERSTATEMENT per called MWh). The two errors do not cancel — they act on different legs.
# ---------------------------------------------------------------------------

#: Grace Factor: delivery within this margin of the contracted quantity is paid in full.
#: `docs/bess_constants_register.md` row "Grace Factor, Penalisation Multiplier and Payable
#: Over-Delivery by product": "NGED SAOU: utilisation grace 5%, availability grace 5%, multiplier 3,
#: POD 0%"; "UKPN (all products): 5% grace factor, 3x penalisation multiplier."
_LOCAL_FLEX_GRACE_FACTOR = 0.05
#: Performance (penalisation) Multiplier applied to the shortfall beyond the Grace Factor. Same row.
_LOCAL_FLEX_PERFORMANCE_MULTIPLIER = 3.0
#: Payable Over-Delivery: the fraction of delivery ABOVE the contracted quantity that is paid.
#: 0% for every product in the register row above — over-delivery is free to the DNO.
_LOCAL_FLEX_PAYABLE_OVER_DELIVERY = 0.0


def payable_delivery_fraction(
    delivery_ratio: float,
    *,
    grace: float = _LOCAL_FLEX_GRACE_FACTOR,
    multiplier: float = _LOCAL_FLEX_PERFORMANCE_MULTIPLIER,
    payable_over_delivery: float = _LOCAL_FLEX_PAYABLE_OVER_DELIVERY,
) -> float:
    """Fraction of the maximum payment earned at ``delivery_ratio`` (delivered ÷ contracted).

    The ENA Open Networks standardised turn-up/turn-down payment taper (D-116):

    - delivery at or above ``1 − grace`` is paid **in full** (the Grace Factor tops it up to 100%),
      with anything above the contracted quantity paid only at ``payable_over_delivery`` — 0% for
      every GB product in the register, so over-delivery is unpaid;
    - below that, payment falls at ``multiplier`` × the shortfall beyond the grace band:
      ``(1 − grace) − multiplier × ((1 − grace) − delivery_ratio)``, floored at zero.

    This is not a guessed curve. It reproduces **every** point of the register's published taper
    ("Payment taper under 5% Grace Factor and Performance Multiplier of 3") exactly — 100–95%→100%,
    94%→92%, 90%→80%, 85%→65%, 80%→50%, 76%→38% — and its stated zero crossing at 63% delivery.
    `tests/test_market_entry_eligibility.py` pins all of them against that published table.
    """
    if delivery_ratio < 0:
        return 0.0
    threshold = 1.0 - grace
    if delivery_ratio >= threshold:
        # Grace tops delivery up to full payment; over-delivery is payable only at the POD rate.
        return 1.0 + payable_over_delivery * max(0.0, delivery_ratio - 1.0)
    return max(0.0, threshold - multiplier * (threshold - delivery_ratio))


#: D-116 (WP-UW-16): the quote-level statement that the availability leg is priced GROSS. It is
#: appended to `quote.warnings` by the pipeline whenever `local_flex_gross_label` says so, so the
#: reader of the quote (not only the reader of the stream notes) is told the leg is an upper bound.
LOCAL_FLEX_GROSS_LABEL = (
    "LOCAL-FLEX AVAILABILITY LEG IS GROSS: no sourced utilisation performance ratio exists in "
    "the assumption library, so the ENA Monthly Utilisation Performance Factor is held at 1.0 "
    "and the availability payment on every award at this site is an UPPER BOUND, before any "
    "under-delivery taper (D-116). DATA ASK: the DNO's settlement statements for the award, "
    "or a published delivery ratio for the product."
)


def sourced_utilisation_performance_ratio(assumptions: AssumptionLibrary) -> float | None:
    """The library's sourced utilisation performance ratio, or ``None``. Never a default (D-116).

    Reads ``local_flex_piclo.utilisation_performance_ratio``. No release carries it today, so this
    returns ``None`` and the availability leg is priced gross and labelled so
    (:func:`local_flex_gross_label`). The day a sourced value is released, the same function feeds
    the price (`valuation._local_flex_value`) and retires the label, so the two cannot disagree.

    Only a CONFIRMED entry with a non-empty source counts (D68). A placeholder or needs-review
    ratio is exactly the unsourced number D-116 forbids: it would taper the price and silently
    retire the gross label on a value nobody has evidenced, so it is ignored and the leg stays
    gross.
    """
    entry = _maybe(assumptions, "local_flex_piclo", "utilisation_performance_ratio")
    if entry is None or entry.status is not AssumptionStatus.CONFIRMED or not entry.source.strip():
        return None
    return _num(entry)


def local_flex_gross_label(site_model: SiteModel, assumptions: AssumptionLibrary) -> str | None:
    """`LOCAL_FLEX_GROSS_LABEL` when an awarded site's availability leg is priced gross (D-116).

    ``None`` on a site with no local-flex award (there is no availability leg to label) and when a
    sourced ratio exists (the leg is then tapered, not gross).
    """
    if not site_model.local_flex_awards:
        return None
    if sourced_utilisation_performance_ratio(assumptions) is not None:
        return None
    return LOCAL_FLEX_GROSS_LABEL


def local_flex_availability_value(
    *,
    assumptions: AssumptionLibrary,
    contracted_mw: float | None,
    window_hours: float | None,
    utilisation_performance_ratio: float | None = None,
    product_type: str | None = None,
    awarded_rate_gbp_per_mw_h: float | None = None,
    awarded_rate_key: str | None = None,
) -> tuple[float, list[str], list[str]]:
    """The AVAILABILITY leg of a local-flex award: £/MW/h × contracted MW × window hours × MUPF.

    Returns ``(revenue, assumption_keys_used, notes)`` — and returns **£0 with a labelled data ask**
    unless every input is real. This is the understatement half of D-116: SAOU-type contracts earn
    most of their money on availability, not utilisation, so omitting the leg understates the
    stream.

    It is nonetheless **not defaulted into existence**, because the repo has already decided that
    (decision D30, recorded on ``availability.availability_price_gbp_per_mw_h``): "an availability
    price (the contracted half of a flex payment) is asserted only from an awarded tender — never
    defaulted." The register's own availability prices span £3.16/MW/h (NGED SAOU_DA ceiling median)
    to £109–136/MW/h (SSEN battery VAOU clears) — a 40x spread across DNOs and products that must
    not be averaged into a single default. So the leg activates when an award lands, and says
    exactly what is missing until then.

    The Monthly Utilisation Performance Factor is the availability-side clawback: missed utilisation
    instructions reduce the availability payment through the same ENA taper (availability grace 5%).
    """
    entry = _maybe(assumptions, "availability", "availability_price_gbp_per_mw_h")
    rate = _num(entry)
    # D57 limb 5b (Olly, 2026-08-18): an AWARDED CLEAR from the site's OWN zone is used in full and
    # takes precedence over D30's deliberately-empty home above. D30 is not repealed - it remains
    # the UNDERWRITING rule that an availability price is asserted only from an awarded tender - and
    # this argument IS an awarded tender's clear, supplied by the caller with its citation. `entry`
    # stays whatever the release holds so the placeholder note below still reports it honestly.
    awarded_note: str | None = None
    if awarded_rate_gbp_per_mw_h is not None and awarded_rate_gbp_per_mw_h > 0:
        rate = float(awarded_rate_gbp_per_mw_h)
        awarded_note = (
            f"availability rate GBP {rate:,.2f}/MW/h taken from the AWARDED CLEAR at "
            f"{awarded_rate_key or 'the awarded tender'} (D57 limb 5b: an awarded contract rate is "
            "used in full, the award being ASSUMED APPROVED under limb 2). This is what the DNO "
            "actually paid in this site's own zone - it is NOT a guide price, a market-wide "
            "average or a ceiling median, and no other licence area's rate is substituted. "
            "D30's null on "
            "availability.availability_price_gbp_per_mw_h is untouched and still governs an "
            "underwriting run."
        )
    missing: list[str] = []
    if rate is None:
        missing.append("availability.availability_price_gbp_per_mw_h (from the awarded tender)")
    if contracted_mw is None or contracted_mw <= 0:
        missing.append("the award's contracted MW")
    if window_hours is None or window_hours <= 0:
        missing.append("the award's contracted availability window hours per year")
    if missing:
        return (
            0.0,
            [],
            [
                "availability leg NOT priced — £0. DATA ASK: "
                + "; ".join(missing)
                + ". This UNDERSTATES local flex, because availability (not utilisation) is where "
                "SAOU-type contracts earn most of their revenue; it is left at £0 rather than "
                "defaulted because an availability price is asserted only from an awarded tender "
                "(decision D30), and the register's per-DNO availability prices span roughly 40x "
                "(NGED SAOU_DA ceiling median £3.16/MW/h to SSEN battery VAOU clears of "
                "£109-136/MW/h) — a spread that must not be averaged into one number.",
            ],
        )
    assert rate is not None and contracted_mw is not None and window_hours is not None
    gross = rate * contracted_mw * window_hours
    notes = [
        f"availability leg: £{rate:,.2f}/MW/h × {contracted_mw:,.3f} MW contracted × "
        f"{window_hours:,.1f} contracted window hours/yr = £{gross:,.2f}/yr"
        + (f" ({product_type} product)." if product_type else "."),
        awarded_note or "",
        _placeholder_note("availability", "availability_price_gbp_per_mw_h", entry)
        if entry is not None and awarded_note is None
        else "",
    ]
    used = (
        [awarded_rate_key]
        if awarded_note is not None and awarded_rate_key
        else ["availability.availability_price_gbp_per_mw_h"]
    )
    if utilisation_performance_ratio is None:
        notes.append(
            "Monthly Utilisation Performance Factor NOT applied (held at 1.0 — an UPPER BOUND). "
            "DATA ASK: the ratio of delivered to instructed utilisation per month. Missed "
            "utilisation instructions claw back the availability payment through the same ENA "
            "taper (availability grace 5%, multiplier 3), so a real MUPF is at or below 1.0."
        )
        return gross, used, [n for n in notes if n]
    mupf = payable_delivery_fraction(utilisation_performance_ratio)
    revenue = gross * mupf
    notes.append(
        f"Monthly Utilisation Performance Factor {mupf:.4f} applied from a "
        f"{utilisation_performance_ratio:.1%} utilisation performance ratio (ENA taper: grace "
        f"{_LOCAL_FLEX_GRACE_FACTOR:.0%}, multiplier {_LOCAL_FLEX_PERFORMANCE_MULTIPLIER:.0f}): "
        f"£{gross:,.2f} × {mupf:.4f} = £{revenue:,.2f}/yr."
    )
    return revenue, used, [n for n in notes if n]


def local_flex_utilisation_adjustment(
    *,
    delivered_mwh: float,
    contracted_mwh: float | None,
) -> tuple[float, list[str]]:
    """Payable utilisation MWh after the Grace Factor, Performance Multiplier and POD cap (D-116).

    Returns ``(payable_mwh, notes)``. This is the *overstatement* half of D-116: paying every
    delivered MWh in full ignores both the penalty taper for under-delivery and the
    Payable-Over-Delivery cap, which is **0%** for every GB product in the register — delivery
    beyond the contracted quantity earns nothing.

    Without the award's contracted MWh no delivery ratio exists, so no adjustment can be computed;
    the delivered volume passes through with a labelled data ask rather than an invented ratio.
    """
    if contracted_mwh is None or contracted_mwh <= 0:
        return (
            delivered_mwh,
            [
                "utilisation payment NOT adjusted for performance — the Grace Factor, Performance "
                "Multiplier and Payable-Over-Delivery cap could not be applied. DATA ASK: the "
                "award's contracted utilisation MWh, without which there is no delivery ratio. "
                "This is an UPPER BOUND on the payable volume: the POD cap is 0% for every GB "
                "product in the register, so any delivery above the contracted quantity is unpaid, "
                "and under-delivery is penalised at 3x the shortfall beyond a 5% grace band."
            ],
        )
    ratio = delivered_mwh / contracted_mwh
    fraction = payable_delivery_fraction(ratio)
    payable = contracted_mwh * fraction
    notes = [
        f"utilisation performance: {delivered_mwh:,.3f} MWh delivered against "
        f"{contracted_mwh:,.3f} MWh contracted = {ratio:.1%}; ENA taper (grace "
        f"{_LOCAL_FLEX_GRACE_FACTOR:.0%}, multiplier "
        f"{_LOCAL_FLEX_PERFORMANCE_MULTIPLIER:.0f}, payable over-delivery "
        f"{_LOCAL_FLEX_PAYABLE_OVER_DELIVERY:.0%}) pays {fraction:.4f} of the contracted quantity "
        f"= {payable:,.3f} payable MWh."
    ]
    if delivered_mwh > contracted_mwh:
        notes.append(
            f"PAYABLE OVER-DELIVERY CAP: {delivered_mwh - contracted_mwh:,.3f} MWh was delivered "
            "above the contracted quantity and is UNPAID (POD 0%)."
        )
    return payable, notes


#: Key path of the per-DNO published availability prices. LOCALFLEX-2: this table had NO CODE READER
#: anywhere in ``src/`` while :func:`local_flex_availability_value` read only the single scalar
#: ``availability.availability_price_gbp_per_mw_h``, whose value is null in every release.
_LOCAL_FLEX_AVAIL_BY_SOURCE_KEY = "local_flex_piclo.availability_price_gbp_per_mw_h_by_source"

#: Stable prefix on the per-DNO published-price context (LOCALFLEX-2). Pinned by
#: ``tests/test_local_flex_dno_price_context.py`` so the selection cannot silently revert to
#: publishing nothing.
LOCAL_FLEX_PRICE_CONTEXT_LABEL = "PUBLISHED AVAILABILITY PRICES FOR THIS SITE'S DNO"


def _local_flex_price_rows(value: object, operator: str) -> list[str]:
    """Rows of the per-DNO availability table belonging to ``operator``, rendered for a reader.

    Selection is by the operator prefix of the table's own key names (``nged_…``, ``ukpn_…``,
    ``ssen_…``), which is why the site's ``connection.dno_region`` (``NGED_WM``, ``SSEN_SEPD``, …)
    is reduced to its licensee token. A region with no matching row yields an empty list — never a
    substituted row from another DNO, which is the averaging D30 forbids wearing a different hat.
    """
    if not isinstance(value, dict):
        return []
    rows: list[str] = []
    for name, row in sorted(value.items()):
        if not name.lower().startswith(operator) or not isinstance(row, dict):
            continue
        figures = {
            k: v
            for k, v in row.items()
            if k != "basis" and isinstance(v, (int, float, dict, list)) and not isinstance(v, bool)
        }
        if not figures:
            continue
        basis = row.get("basis")
        rendered = "; ".join(f"{k} {v}" for k, v in figures.items())
        rows.append(f"{name}: {rendered}" + (f" [{basis}]" if isinstance(basis, str) else ""))
    return rows


def local_flex_published_price_context(
    assumptions: AssumptionLibrary, site_model: SiteModel
) -> list[str]:
    """The site's own DNO's PUBLISHED availability prices, as labelled context — never as a price.

    LOCALFLEX-2 (Olly, 2026-08-17). Two things were true at once and only one was a defect. The
    refusal to **average** the per-DNO table was right and stays right: gb_v1_10's own note says the
    sources "DISAGREE by 20-30x for the nominally identical product", and D30 is recorded on the key
    itself — "this entry must NEVER produce a default availability price". The refusal to **select**
    was the defect: every campaign site's DNO area is known, so the reader could always have been
    shown what their own DNO publishes, and instead got a sentence saying the spread was too wide to
    quote.

    **This function moves no money and must not.** ``LOCAL_FLEX_PICLO`` is not in
    ``GATED_UPSIDE_STREAMS``, so its revenue lands in the PRICED FLOOR; turning a published
    indicative price into a booked availability payment with no award would be the F-19 error with a
    citation attached. So the figures are published as **context in the gate warnings**, the £ stays
    £0, and the text says which it is. The confirmation ask is an awarded tender.
    """
    region = site_model.connection.dno_region
    if not region:
        return []
    operator = region.split("_")[0].lower()
    entry = _maybe(assumptions, "local_flex_piclo", "availability_price_gbp_per_mw_h_by_source")
    if entry is None:
        return [
            f"NO PUBLISHED AVAILABILITY PRICES AVAILABLE: {_LOCAL_FLEX_AVAIL_BY_SOURCE_KEY} is "
            "absent from this assumption release, so not even indicative context can be shown "
            f"for {region}."
        ]
    rows = _local_flex_price_rows(entry.value, operator)
    if not rows:
        return [
            f"NO PUBLISHED AVAILABILITY PRICE FOR {region}: "
            f"{_LOCAL_FLEX_AVAIL_BY_SOURCE_KEY} carries no row for licensee '{operator.upper()}'. "
            "A row from a DIFFERENT DNO is deliberately NOT substituted — the published figures "
            "disagree by 20-30x for the nominally identical product, so borrowing one would be the "
            "averaging decision D30 forbids. DATA ASK: this licensee's published flexibility "
            "tender parameters."
        ]
    # LOCALFLEX-3 (Olly, 2026-08-18) — D54. The leading clause below used to read "because no
    # tender has been AWARDED to this site" UNCONDITIONALLY. On a site that records an
    # owner-attested award that sentence is FALSE, and it contradicts the sibling sentence
    # `_local_flex_conditional_reason` writes onto the same record ("an award IS recorded"). It is
    # the LOCALFLEX-1 defect one layer along: a claim about the outside world this function never
    # checked, now upgraded to a claim its own input refutes. Labelling is D54's whole mitigation,
    # so a self-contradicting label defeats it.
    #
    # A site recording NO award keeps the original sentence BYTE-IDENTICALLY, so no pre-D54 record
    # moves. This changes no £: the availability leg was £0 before and is £0 after — what changes
    # is that the run now states the REAL reason it is £0 rather than a false one.
    n_awards = len(
        [
            a
            for a in (getattr(site_model, "local_flex_awards", None) or [])
            if a.stream == MarketStream.LOCAL_FLEX_PICLO
        ]
    )
    why_zero = (
        "the availability leg is priced at £0 because no tender has been AWARDED to this site"
        if not n_awards
        else (
            f"THIS SITE RECORDS {n_awards} ATTESTED LOCAL-FLEX AWARD(S) (D54), AND SINCE D57 ITEM "
            "5b (2026-08-18) THE AVAILABILITY LEG IS PRICED FROM THEM. `optimisation.valuation` "
            "reads the award's own committed kW and derives its contracted window hours from the "
            "service windows the award declares, and prices them at the AWARDED CLEAR for this "
            "site's own licence area (`local_flex_piclo.awarded_zone_clears`, D57 limb 5b: an "
            "awarded contract rate is used in full, the award being assumed approved). Until that "
            "change the call site passed `contracted_mw=None, window_hours=None` and the leg was "
            "£0 on every run - a WIRING gap, never a fact about the site, and the reason an "
            "attested award could sit in the priced floor and still pay £0. The prices quoted "
            "above remain CONTEXT: they are ceilings and market-wide averages, not this zone's "
            "clear, and they are not what the £ is built from"
        )
    )
    return [
        f"{LOCAL_FLEX_PRICE_CONTEXT_LABEL} ({region}) — PUBLISHED CONTEXT, NOT A PRICE, AND NOT IN "
        f"THE £: {' | '.join(rows)}. Source: {_LOCAL_FLEX_AVAIL_BY_SOURCE_KEY} (NGED 'DSO Revenue "
        "Estimator September 2025 V2.0' and 'C31E Supporting Data 2024-25'; UKPN 'Competition Data "
        "- Summer 2026 Tender V2'; SSEN 'Flexibility Services Contract Register v2.30', 30/07/2026)"
        " — status needs_review, confidence LOW.",
        f"WHY THIS IS CONTEXT AND NOT REVENUE: {why_zero}. An availability price is asserted only "
        "from an "
        "awarded tender (decision D30, recorded on "
        "`availability.availability_price_gbp_per_mw_h`, which is null by design in every "
        "release). NGED figures above are per-CMZ CEILINGS or market-wide averages, not clears "
        "(the previous HV round cleared 34% below ceiling); UKPN's are INDICATIVE ranges; only the "
        "SSEN rows are actual awarded battery contracts, and that sample is 14 contracts, mostly "
        "grid-scale. CONFIRMATION ASK: an awarded tender supplying the contracted MW, the "
        "availability price and the contracted window hours per year.",
        "DIRECTION OF EFFECT IF WRONGLY BOOKED: UP, and materially. Paying a full year of "
        "availability as though called is the F-19 error (an implicit 100% utilisation rate). "
        "Annualising even the availability leg needs contracted window hours, and the only "
        "published figure in the register is UKPN's median 270 h/season for HV LT-SU (~540 h/yr), "
        "across which these prices span roughly £1.71/kW/yr to £58.9/kW/yr — a 34x range. The "
        "honest statement is the range, never a point.",
    ]


#: The `AvailabilityCommitment.reference` prefix an OWNER-ATTESTED award carries (D54). It is not a
#: DNO trade id and must never be mistaken for one: the string names the attestation, so a reader
#: (or a later gate) can tell an attested award from a transcribed one by looking at the reference
#: alone, which is the field the W7-4 evidence discipline keys on.
LOCAL_FLEX_ATTESTATION_REF_PREFIX = "OWNER-ATTESTATION-D54"


def _min_in_window_load_kw(
    site_model: SiteModel, award: AvailabilityCommitment
) -> tuple[float | None, str]:
    """The site's own MEASURED minimum import (kW) inside an award's windows, or ``(None, why)``.

    This is the quantity that decides whether an availability commitment is deliverable at all,
    and nothing else on the `SiteModel` is a substitute for it. On the behind-the-meter no-export
    floor the solver's per-instant response envelope is ``min(power − parasitic, load_t)``, so the
    binding number is the **minimum** in-window load over the metered horizon — not the peak, not
    the mean, not the connection. On the campaign sites those differ by everything: Bishops Cleeve
    MRF peaks at 147 kW and imports **zero for 16% of the published 10:00–16:00 April–September
    window**; Knowl Hill for 83% of it.

    Read from the site's own declared series through the same loader the pipeline uses, cached by
    (path, mtime) so the repeated `dispatch_constraints_for` calls in one run pay for it once.
    Returns ``(None, reason)`` when the series cannot be located or read — never a guess, and the
    caller then declines to model the award rather than reserving headroom it cannot justify.
    """
    ref = site_model.load_data.half_hourly_file_ref
    if not ref:
        return None, "the site declares no half-hourly series"
    path = Path(ref)
    if not path.is_file():
        for base in (Path.cwd(), Path(__file__).resolve().parents[3]):
            if (base / ref).is_file():
                path = base / ref
                break
        else:
            return None, f"the declared series {ref!r} was not found from {Path.cwd()}"
    try:
        series = _load_min_load_series(str(path.resolve()), path.stat().st_mtime_ns)
    except DpaEvidenceMissing:
        # Customer data without DPA evidence is refused, never read as "a bad series"
        # (WP-GRC-05). The run stops; it does not price around it.
        raise
    except Exception as exc:  # noqa: BLE001 — a bad series must never break a run (D45)
        return None, f"the declared series {ref!r} could not be read ({exc})"
    if series is None:
        return None, f"the declared series {ref!r} carries no usable import column"
    hour, month, dow, load_kw = series
    mask = np.zeros(load_kw.shape, dtype=bool)
    for w in award.windows:
        # ``end_time == 00:00`` is the exclusive END of the day (the schema's overnight
        # convention), so it must read as hour 24 and not as hour 0 — otherwise the window
        # collapses to nothing and a real award is silently dropped as vacuous.
        end_h = 24 if w.end_time == time(0, 0) else w.end_time.hour
        m = (hour >= w.start_time.hour) & (hour < end_h)
        if w.months:
            m &= np.isin(month, list(w.months))
        if w.days_of_week:
            m &= np.isin(dow, list(w.days_of_week))
        mask |= m
    if not mask.any():
        return None, "the award's windows never intersect the metered horizon"
    return float(np.nanmin(load_kw[mask])), "measured from the site's own half-hourly series"


@lru_cache(maxsize=32)
def _load_min_load_series(
    path: str, _mtime_ns: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """``(hour, month, day-of-week, load kW)`` arrays for a half-hourly file, cached by path+mtime.

    Plain numpy arrays rather than a frame: the caller only needs calendar filters and a minimum,
    and the cache key is the file's identity, so two sites sharing a series pay once.
    """
    import pandas as pd

    from gridq.ingestion import load_half_hourly_csv

    df = load_half_hourly_csv(path)
    if "import_kw" in df.columns:
        kw = df["import_kw"].astype(float).to_numpy()
    elif "import_kwh" in df.columns:
        # The same conversion the solver applies (`milp_solver` load build, ``import_kwh × 60/
        # interval_min``): kWh per half hour → average kW. Getting this wrong by 2x would halve or
        # double the bound that decides whether an award is deliverable at all.
        kw = (df["import_kwh"].astype(float) * 2.0).to_numpy()
    else:
        return None
    stamps = pd.DatetimeIndex(df["timestamp"])
    return (
        np.asarray(stamps.hour),
        np.asarray(stamps.month),
        np.asarray(stamps.dayofweek),
        kw,
    )


class ClampedLocalFlexAward(NamedTuple):
    """One local-flex award with its committed kW clamped to the battery's own capability.

    ``award`` carries the CLAMPED ``committed_power_kw``; ``declared_kw`` is what the site record
    declared before the clamp. Both travel together because the two consumers need both halves: the
    dispatch reserves the clamped kW, and the availability leg must be able to say on the record
    which figure it billed and which it did not.
    """

    award: AvailabilityCommitment
    declared_kw: float


def clamped_local_flex_awards(
    site_model: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    candidate_power_kw: float | None = None,
) -> tuple[list[ClampedLocalFlexAward], list[str]]:
    """The site's attested local-flex awards, clamped to deliverable capability, + their notes.

    **ONE clamp, TWO consumers, and the second one was missing (2026-08-19).** This function is the
    single definition of the local-flex volume the model commits to. It is consumed by
    :func:`local_flex_award_constraints`, which turns each clamped award into the dispatch
    reservation, AND by :func:`gridq.optimisation.valuation._local_flex_value`, which writes the
    availability invoice. Before this split the clamp lived inside the constraint builder and
    applied to a LOCAL COPY of the award: the dispatch reserved 10.372 kW on the campaign's Harwell
    run while the availability leg was billed on the 213.810 kW the site record declared, because
    the valuation re-read ``site.local_flex_awards`` unclamped. That is a 20.6x mismatch between the
    volume the model commits and the volume it bills, inside one run, and it was worth GBP
    504/kW/yr of a GBP 631/kW/yr reported total against a Modo GB grid-scale index of GBP
    60-80/kW/yr for an entire revenue-stacked megawatt battery. The invariant this function exists
    to hold is: **the invoiced MW is the MW in the DispatchConstraintDescriptor, at every
    candidate.**

    WHICH SIDE OF D57 THE CLAMP SITS ON. D57 removes every ELIGIBILITY constraint on this stream and
    all of that stays removed: no award is needed, no open tender is needed, no zone match, no
    minimum size, and an awarded-contract rate is used in full (limb 5b). The clamp is the PHYSICS
    of the asset, which D57 explicitly preserves as arithmetic integrity, and D58 limb 1 names the
    same bound as the half that is not waived.

    This is the bridge the subsystem was missing. `AvailabilityCommitment` → the constraint seam →
    `sizing.deliverability.produce_gate_context` → `GateContext.local_flex_tender_open` → this
    stream's OPEN gate → `Firmness.CONTRACTED` → the priced floor has existed since W7-1/W7-4, and
    **nothing in production ever built the first link**: `enumerate_sizing(extra_constraints=…)`
    had zero callers outside a test, so the whole chain was reachable only from a test fixture.
    Emitting the descriptors from `constraints()` puts it on `dispatch_constraints_for`, which
    every production entry point calls (`sizer`, `evaluator`, `underwrite`, `two_stage`, the
    engine) — so the award reaches the MILP and the gate on a plain ``gridq run``.

    **The volume is bounded by measured deliverability, and that bound is the whole difficulty.**
    The MILP compiles an availability window into reserved net-injection headroom
    (``discharge − charge ≤ envelope − committed``, ``envelope = min(power − parasitic, load_t)``)
    and, per its own docstring, "an infeasible commitment is compiled anyway and warned — the solve
    honestly returns INFEASIBLE rather than silently relaxing a contract". Two facts follow.

    First, the bound must never exceed what the candidate holding the award can deliver, or the
    award silently deletes sizes from the search — a revenue assumption removing sizes for a reason
    unrelated to their economics. Since D61 (2026-11-22) the constraint builder is called PER
    CANDIDATE on the production path (``candidate_power_kw`` = that candidate's power, via
    `gridq.dispatch.candidate_dispatch_constraints` in `sizing.sizer._evaluate_spec` and every
    chosen-spec re-solve), so each candidate reserves its OWN end-of-tenor capability. The
    candidate-less form, bounded by the smallest system on the site's sizing grid, is only the
    pre-candidate default (the gate and disclosure readers, and a schedule never solved).

    Second, and much sharper: on the no-export floor the envelope collapses to the **metered load**
    whenever the load is the smaller term, so the binding quantity is the site's MINIMUM in-window
    import, not its peak. Measured on the campaign sites this is decisive — Bishops Cleeve MRF
    imports **zero for 16% of the published 10:00–16:00 April–September window** and Knowl Hill for
    83% of it — and an unbounded award made 138 of 139 candidates INFEASIBLE on a real run. Where
    that minimum is zero there is no headroom to hold at ANY positive volume, and the honest result
    is that the attested award is **not deliverable as a firm hold at this site**: no constraint is
    emitted, the gate stays CONDITIONAL, and the run says exactly why. That is a high-confidence
    negative, not a data gap — and it is a finding the owner needs, because it says the attestation
    cannot be banked at those sites without export or a partial-window product.

    Returns ``(clamped_awards, notes)``; ``([], [])`` when the site declares no award, which keeps
    every pre-D54 site byte-identical.
    """
    awards = [
        a
        for a in (getattr(site_model, "local_flex_awards", None) or [])
        if a.stream == MarketStream.LOCAL_FLEX_PICLO
    ]
    if not awards:
        return [], []
    # Call-time import: `optimisation.two_stage` imports the dispatch/value-stack layer, so a
    # module-level import here would be circular. Same precedent as `sizing.deliverability`'s
    # `value_for_duration` import — the search floor is single-sourced rather than re-derived,
    # so the bound can never drift from the grid it is protecting.
    from gridq.optimisation.two_stage import (
        connection_power_limit_kw,
        load_relevant_capacity_kw,
        power_search_floor_kw,
    )

    limit_kw = connection_power_limit_kw(site_model)
    peak_kw = getattr(site_model.load_data, "max_import_kw", None)
    anchor = min([float(v) for v in (limit_kw, peak_kw) if v is not None and float(v) > 0] or [0.0])
    floor_kw = (
        power_search_floor_kw(load_relevant_capacity_kw(site_model, anchor)) if anchor > 0 else 0.0
    )
    # The smallest candidate's own response capability, and BOTH terms are load-bearing.
    #
    # Parasitic draw, because the solver subtracts the battery's continuous auxiliary load before
    # the site-load cap applies. Degradation retention, because a tenored commitment is ALSO
    # assessed by the W7-3 end-of-tenor check (`sizing.deliverability.assess_end_of_life_
    # deliverability`), whose verdict is not advisory: ``objective.py:82`` REJECTS any candidate
    # whose capability at year N — ``power × (1 − fade)^tenor`` for a non-CM commitment — falls
    # below the committed kW. A bound that cleared the dispatch envelope but not the retention
    # would therefore still delete the smallest rung of the grid, through a different door and for
    # the same forbidden reason: a revenue assumption narrowing the search. Read from the same
    # library key the check reads, so the two can never disagree.
    standby = _num(_maybe(assumptions, "parasitic_load", "standby_kw_per_mw"))
    fade = _num(_maybe(assumptions, "degradation", "annual_capacity_fade_pct"))
    fade_pct = fade if fade is not None and 0 <= fade < 100 else 0.0
    floor_capability_kw = floor_kw * (1.0 - (standby or 0.0) / 1000.0)

    clamped: list[ClampedLocalFlexAward] = []
    notes: list[str] = []
    for award in awards:
        declared = float(award.committed_power_kw)
        # D57 (Olly, 2026-08-18): THE METERED-IMPORT BOUND IS RETIRED. The award is bounded by the
        # BATTERY'S OWN DELIVERABLE CAPABILITY and by nothing else. `_min_in_window_load_kw`
        # is still
        # measured, because the measurement is a real finding and the reader is entitled to it, but
        # it no longer reduces or refuses a volume.
        #
        # Two reasons, and the second is a defect independent of D57:
        #  1. The bound existed only because a response had to be served behind the meter, which is
        #     true only while export consent is absent. D57 assumes export consent, the export MPAN
        #     and G99/G100 APPROVED, so the reservation envelope in
        #     `optimisation.milp_solver._compile_commitments` is now the battery's capability net of
        #     parasitic draw with NO site-load cap, and the constraint is feasible without
        #     this bound.
        #  2. THE SIGN WAS WRONG for the awarded product. NGED's Port Ham clear - the only open,
        #     undersubscribed award any of these sites sits in - is DEMAND TURN UP: it pays
        #     the asset
        #     to CHARGE. Turning demand up consumes import HEADROOM (connection limit less load),
        #     which is LARGEST exactly where metered import is SMALLEST, so bounding it by minimum
        #     metered import was tightest precisely where the real product was easiest to deliver.
        #     "Nothing to turn down" was never the right test for a product that asks the site to
        #     turn something UP.
        #
        # DIRECTION OF EFFECT: UP, at five of nine sites from exactly GBP 0 (their minimum in-window
        # metered import is zero, so the award was refused outright).
        min_load_kw, why = _min_in_window_load_kw(site_model, award)
        # The smallest candidate's capability at the END of the award's tenor, which is the level
        # the W7-3 check tests against — not its day-one rating.
        retention = (1.0 - fade_pct / 100.0) ** float(award.tenor_years or 0.0)
        # ONE BOUND PER CANDIDATE (D61, 2026-11-22). Since D61 the constraint builder is called
        # PER CANDIDATE on the production path: `sizing.sizer._evaluate_spec` re-resolves the
        # seam at each candidate's own power (`dispatch.candidate_dispatch_constraints`), and every
        # chosen-spec re-solve in the engine passes the same power, so each candidate RESERVES its
        # own end-of-tenor capability and the solver records the kW it reserved on the schedule.
        # The valuation then BILLS that record, so the invoice MW is the reserved MW at every size
        # (the FIX 1 invariant, restored).
        #
        # Before D61 the reservation was built once, pre-candidate, at the SEARCH FLOOR, while the
        # invoice billed the solved candidate (339892c): at Harwell 250 kW the dispatch held 10.372
        # kW and the invoice billed 213.810 kW, so the same MW was booked twice. Billing the floor
        # instead was the size-invariance the owner rejected ("its not right that every size would
        # get the same amount for all battery size").
        #
        # The candidate-less form (``candidate_power_kw is None``) is the PRE-CANDIDATE DEFAULT
        # only: the gate and disclosure readers that run before a candidate exists, and the
        # valuation of a schedule that carries no solved reservation record.
        # D57 limb 3 is untouched: this is the asset's own physics, the one carve-out it allows, and
        # no eligibility gate, threshold, award or consent test is reintroduced anywhere here.
        capability_kw = (
            floor_capability_kw
            if candidate_power_kw is None
            else candidate_power_kw * (1.0 - (standby or 0.0) / 1000.0)
        )
        eol_capability_kw = capability_kw * retention
        bound = min(declared, eol_capability_kw) if capability_kw > 0 else declared
        if min_load_kw is not None:
            notes.append(
                f"LOCAL-FLEX AWARD '{award.commitment_id}': the site's MINIMUM metered import "
                f"inside the award's service windows is {min_load_kw:,.3f} kW ({why}). Before D57 "
                "that figure BOUNDED the award, and where it was zero the award was refused as not "
                "deliverable as a firm hold at any positive volume. It is reported here and no "
                "longer binds: D57 assumes export consent approved, so a response is deliverable "
                "as net injection past the meter and needs no site load to turn down; and the "
                "awarded product at these sites is DEMAND TURN UP, which consumes import HEADROOM "
                "rather than import volume, so metered import was the wrong-directional bound in "
                "the first place. DATA ASK: the awarded capacity and the ENA product type "
                "(SU / SAOU / VAOU / OU) on the contract, so the volume stops being derived."
            )
        else:
            notes.append(
                f"LOCAL-FLEX AWARD '{award.commitment_id}': the in-window metered import could not "
                f"be measured because {why}. Under D57 this no longer refuses the award - it is "
                "bounded by the battery's own end-of-tenor deliverable capability instead, "
                "which is "
                "a property of the asset and needs no metered series. DATA ASK: the site's "
                "half-hourly series and the awarded capacity on the contract."
            )
        if bound <= 0:
            notes.append(
                f"LOCAL-FLEX AWARD '{award.commitment_id}' NOT MODELLED: the smallest system on "
                "this site's sizing grid has no positive end-of-tenor deliverable capability, so "
                "there is no volume to reserve at any size. This is a property of the sizing grid, "
                "not of any award, consent or threshold."
            )
            continue
        if bound < declared:
            if candidate_power_kw is None:
                binding = (
                    "The binding limit is the smallest system on this "
                    f"site's sizing grid at its END-OF-TENOR capability ({eol_capability_kw:,.3f} "
                    f"kW = {floor_kw:,.3f} kW rated, less parasitic draw, less {fade_pct:g}%/yr "
                    f"fade over {award.tenor_years:g} yr). The £ is UNDERSTATED by the difference, "
                    "deliberately: an award modelled above deliverable capability makes candidates "
                    "INFEASIBLE rather than merely unattractive, and would narrow the search on a "
                    "revenue assumption. "
                )
            else:
                binding = (
                    "The binding limit is THIS CANDIDATE's own END-OF-TENOR capability "
                    f"({eol_capability_kw:,.3f} kW = {candidate_power_kw:,.3f} kW rated, less "
                    f"parasitic draw, less {fade_pct:g}%/yr fade over {award.tenor_years:g} yr). "
                )
            notes.append(
                f"LOCAL-FLEX AWARD '{award.commitment_id}' MODELLED AT {bound:,.3f} kW, NOT THE "
                f"{declared:,.3f} kW DECLARED. {binding}"
                "THIS IS THE ONE BOUND D57 KEEPS, because it is the asset's own physics and not a "
                "contract, consent, award, threshold or market rule. DATA ASK: the awarded "
                "capacity "
                "on the contract, so the volume stops being derived."
            )
            award = award.model_copy(update={"committed_power_kw": bound})
        clamped.append(ClampedLocalFlexAward(award=award, declared_kw=declared))
    return clamped, notes


def local_flex_award_constraints(
    site_model: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    candidate_power_kw: float | None = None,
) -> tuple[list[DispatchConstraintDescriptor], list[str]]:
    """The site's clamped local-flex awards as typed seam constraints (**D54**) + their notes.

    A thin wrapper over :func:`clamped_local_flex_awards`, which holds the clamp and all of its
    reasoning. The split exists so the availability INVOICE and the dispatch RESERVATION read the
    same volume; see that function's docstring for the defect the shared definition closes.

    ``candidate_power_kw`` (D61): the power of the candidate whose dispatch this reservation is
    for. Passed on the production path, so each candidate reserves its own end-of-tenor
    capability; ``None`` is the pre-candidate default (bounded by the search floor), unchanged.
    """
    clamped, notes = clamped_local_flex_awards(
        site_model, assumptions, candidate_power_kw=candidate_power_kw
    )
    return [
        DispatchConstraintDescriptor(
            constraint_id=f"local_flex_award_{entry.award.commitment_id}",
            description="An attested local-flexibility award: the battery must hold "
            f"{entry.award.committed_power_kw:,.3f} kW of deliverable response through the award's "
            "service windows (D54, owner-attested - see the gate warnings for its provenance).",
            kind="availability_window",
            parameter_source="site.local_flex_awards (AvailabilityCommitment)",
            parameters=entry.award,
        )
        for entry in clamped
    ], notes


def _awarded_zone_clear_rows(assumptions: AssumptionLibrary, site_model: SiteModel) -> list[str]:
    """The AWARDED clears published for the site's own licence area, as labelled context (D54).

    Deliberately its own renderer rather than a reuse of :func:`_local_flex_price_rows`: that one
    keeps only numeric/collection cells, which would silently drop the two fields that decide
    whether the modelled award resembles the real product at all — the **service window** and the
    **direction**. Port Ham's product pays an asset to CHARGE in the summer daytime; Wallingford's
    pays it to DISCHARGE on winter evenings. A reader shown two prices and neither direction has
    been told the least useful half.
    """
    region = site_model.connection.dno_region
    entry = _maybe(assumptions, "local_flex_piclo", "awarded_zone_clears")
    if not region or entry is None or not isinstance(entry.value, dict):
        return []
    operator = region.split("_")[0].lower()
    rows = [
        f"{name}: "
        + "; ".join(
            f"{k} {v}"
            for k, v in row.items()
            if k not in {"basis", "note"} and not isinstance(v, bool)
        )
        + (f" [{row['basis']}]" if isinstance(row.get("basis"), str) else "")
        for name, row in sorted(entry.value.items())
        if isinstance(row, dict) and name.lower().startswith(operator)
    ]
    if not rows:
        return []
    return [
        f"AWARDED CLEARS PUBLISHED FOR THIS LICENCE AREA ({region}) — CONTEXT, NOT THE PRICE USED: "
        + " | ".join(rows)
        + f". Source: local_flex_piclo.awarded_zone_clears (status {entry.status.value}, "
        "confidence low)."
    ]


def _local_flex_conditional_reason(site_model: SiteModel, assumptions: AssumptionLibrary) -> str:
    """Why local flex is CONDITIONAL — the state that was actually tested, per site (D54).

    Three distinct states used to render as one sentence, and only the first of them was true:

    * the site records **no award** — the pre-D54 wording, unchanged;
    * the site records an attested award that **could not be modelled** (its deliverable volume
      measured zero, or its series was unreadable) — an evidenced physical result, and saying "no
      award is recorded" to that owner would be plainly false;
    * the site records an award which **was** modelled, in which case the gate is OPEN and this
      function is not reached.
    """
    awards = [
        a
        for a in (getattr(site_model, "local_flex_awards", None) or [])
        if a.stream == MarketStream.LOCAL_FLEX_PICLO
    ]
    base = (
        "The DNO's open-tender register was NOT consulted and this run makes no claim about "
        "whether a tender is currently open in the site's area."
    )
    if not awards:
        return (
            "local flex not priced into the floor: NO AWARDED LOCAL-FLEX COMMITMENT IS RECORDED "
            f"ON THIS SITE. That is the whole of what was tested — {base} Conditional until "
            "contracted."
        )
    descriptors, notes = local_flex_award_constraints(site_model, assumptions)
    if descriptors:
        # The award DID model. Reaching here means this particular result carries no solved
        # dispatch to produce a GateContext from — the no-selection / no-schedule degrade, where
        # every gate falls back to its unevidenced state. Saying "the award was not modelled"
        # here would be false in the opposite direction from the sentence above it.
        return (
            "local flex not priced into the floor ON THIS RESULT, although an award IS recorded "
            f"and WAS modelled ({len(awards)} attested, D54). No gate context reached this gate, "
            "which is what happens when no candidate was selected and there is therefore no "
            "solved dispatch behind the headline result — the per-candidate rows on the sizing "
            f"grid carry the award's real firmness and £. {base}"
        )
    return (
        "local flex not priced into the floor DESPITE AN AWARD BEING RECORDED ON THIS SITE "
        f"({len(awards)} attested, D54). The award was NOT modelled, for the reason(s) that "
        "follow, so the stream cannot be contracted and its £ stays out of P50, P90 and the "
        f"DSCR — this is a modelling refusal, not a finding that the award is worthless. {base} "
        + " ".join(notes)
    )


def local_flex_award_disclosure(site_model: SiteModel, assumptions: AssumptionLibrary) -> list[str]:
    """What an OPEN local-flex gate must say when the award behind it is an ATTESTATION (D54).

    Every sentence here is the mitigation for one specific way this stream can now mislead:

    1. **Who asserted it.** The award's own ``confirmed_by``/``basis``, verbatim, and an explicit
       denial that any register was consulted. The published registers were in fact checked in the
       session this decision was taken and they do **not** agree with it for six of the nine
       campaign sites, so "verified against the DNO register" would be a false statement about the
       outside world — the exact class of defect LOCALFLEX-1 removed from the CONDITIONAL branch.
    2. **What is missing.** ``reference`` is the contract id. The commitment schema is explicit
       that "a commitment with no reference is a modelling input, not evidence"; an attestation
       reference is not the DNO's, so the ask is stated per award.
    3. **What it costs the reader.** The stream is now CONTRACTED, so it is inside
       ``finance.cashflows._PRICED_FIRMNESS`` and its £ reaches P50, EBITDA and both IRRs. Its P90
       contribution stays **zero** — ``LOCAL_FLEX_PICLO`` is in ``_NO_P90_IN_TERM`` — so the
       lender-basis DSCR does not move. Saying so stops a reader inferring a debt-sizing effect
       that is structurally impossible.
    4. **Which price is actually being used.** The utilisation leg prices off the library's
       GB-wide Piclo median, which is not the site's own zone's awarded clear.
    5. **Which leg is still £0.** The availability leg — where SAOU/VAOU contracts earn most of
       their money — is not priced on the engine path at all.

    Returns ``[]`` when the site declares no award, so an OPEN gate reached any other way is
    byte-identical to before.
    """
    awards = [
        a
        for a in (getattr(site_model, "local_flex_awards", None) or [])
        if a.stream == MarketStream.LOCAL_FLEX_PICLO
    ]
    if not awards:
        return []
    out: list[str] = []
    for a in awards:
        windows = "; ".join(
            f"{w.label or 'window'} {w.start_time.strftime('%H:%M')}-"
            f"{w.end_time.strftime('%H:%M')}" + (f" months {w.months}" if w.months else " all year")
            for w in a.windows
        )
        out.append(
            f"LOCAL FLEX IS IN THE PRICED FLOOR ON AN OWNER ATTESTATION, NOT A SIGHTED CONTRACT "
            f"(D54). Award '{a.commitment_id}': {a.committed_power_kw:,.3f} kW committed over "
            f"[{windows}]"
            + (f", tenor {a.tenor_years:g} yr" if a.tenor_years else "")
            + f", counterparty {a.counterparty or 'NOT STATED'}. Asserted by "
            f"{a.confirmed_by or 'NOT STATED'} on the basis: "
            f"{a.basis or 'NOT STATED'}. THIS RUN DID NOT CONSULT ANY DNO REGISTER, PICLO FEED OR "
            "CMZ LIST, AND MAKES NO CLAIM THAT ONE MATCHES."
        )
        out.append(
            f"OUTSTANDING CONFIRMATION ASK for award '{a.commitment_id}': the DNO/DSO contract or "
            f"trade reference. The reference carried here is "
            f"'{a.reference or 'NONE'}' — an attestation id, not a tender id. Until the real "
            "reference lands this award is a modelling input the gate has been told to treat as "
            "evidence, and the run's confidence band is capped LOW on that account."
        )
    out.extend(_awarded_zone_clear_rows(assumptions, site_model))
    util = _maybe(assumptions, "local_flex_piclo", "utilisation_price_gbp_per_mwh")
    util_v = _num(util)
    out.append(
        "PRICE BASIS FOR THIS AWARD IS NOT THE AWARD'S OWN CLEAR. The utilisation leg is priced at "
        + (
            f"£{util_v:,.2f}/MWh from local_flex_piclo.utilisation_price_gbp_per_mwh"
            if util_v is not None
            else "local_flex_piclo.utilisation_price_gbp_per_mwh (absent — the leg is £0)"
        )
        + ", which is a GB-WIDE MEDIAN of Piclo guide prices, not this zone's awarded price. A "
        "zone ceiling is a hard cap on that zone, so a GB median can exceed what the site could "
        "ever be paid. DATA ASK: the awarded utilisation £/MWh on the contract."
    )
    out.append(
        "THE AVAILABILITY LEG OF THIS AWARD IS STILL £0. The engine's local-flex valuation calls "
        "the availability helper with no contracted MW and no window hours, so only the "
        "utilisation leg is priced. For SAOU/VAOU-type awards availability is the larger half, so "
        "the figure shown UNDERSTATES the award — it is not a conservative rounding, it is a "
        "missing leg."
    )
    out.append(
        "P90 AND THE LENDER-BASIS DSCR DO NOT MOVE. LOCAL_FLEX_PICLO is in "
        "finance.cashflows._NO_P90_IN_TERM, so its P90 contribution is forced to zero even inside "
        "a live award term (D-139/D-1410). This award changes P50, EBITDA and the IRRs only."
    )
    _, clamp_notes = local_flex_award_constraints(site_model, assumptions)
    out.extend(clamp_notes)
    return out


class LocalFlexPicloStream(ContractedStream):
    """Local flexibility (Piclo et al.) — priced only when a tender is open and contracted."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.LOCAL_FLEX_PICLO,
            display_name="Local flexibility (Piclo et al.)",
            required_inputs=[
                RequiredInput(
                    key="connection.dno_region",
                    description="The site's DNO area, to match against a tender.",
                ),
                RequiredInput(
                    key="local_flex.tender_open",
                    description="An open local-flex tender in the site's area + a contracted "
                    "award. Supplied via GateContext, not SiteModel.",
                ),
            ],
            constraints=[_TENDER_WINDOW],
            assumption_keys=["local_flex_piclo"],
        )

    def constraints(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        *,
        candidate_power_kw: float | None = None,
    ) -> list[DispatchConstraintDescriptor]:
        """The generic tender-window descriptor, plus one typed award per D54 attestation.

        The base class returns the static descriptors alone. `_TENDER_WINDOW` carries **no
        payload**, so the solver logs it as "declared, not contracted" and compiles nothing — which
        is why `tender_window_discharge_mwh` was ``None`` on every production run and the
        utilisation leg was £0 whatever the gate said. An award descriptor carries the typed
        `AvailabilityCommitment`, so it compiles, the delivered in-window MWh is measured, and the
        gate fact is produced. See :func:`local_flex_award_constraints`.

        ``candidate_power_kw`` (D61) resolves the award at that candidate's own capability; it is
        passed through to the clamp unchanged, and ``None`` keeps the pre-candidate default.
        """
        awards, _ = local_flex_award_constraints(
            site_model, assumptions, candidate_power_kw=candidate_power_kw
        )
        return [*super().constraints(site_model, assumptions), *awards]

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        if site_model.connection.dno_region is None:
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=[
                    "the site's DNO area (connection.dno_region) to locate a local-flex tender"
                ],
                warnings=["local flex excluded: no DNO area recorded to match against a tender."],
                consent_status=consent_status,
            )
        tender_open = context.local_flex_tender_open if context is not None else None
        if not gates.local_flex_tender_open(tender_open):
            return self._gate(
                GateStatus.CONDITIONAL,
                missing_requirements=[
                    "an open local-flex tender in the site's DNO area, and a contracted award"
                ],
                unlock_condition="a local-flex (Piclo et al.) tender open in the site's DNO "
                "area + a contracted award",
                warnings=[
                    # LOCALFLEX-1 (Olly, 2026-08-17): this used to read "no known open tender in
                    # the site's area", which is an UNCHECKED CLAIM ABOUT THE OUTSIDE WORLD dressed
                    # as a finding. Nothing in src/ consults a tender register, a Piclo feed or a
                    # DNO CMZ list: `context.local_flex_tender_open` is set in exactly one place
                    # (`sizing/deliverability.py`) and only from a commitment the SITE ITSELF
                    # supplies. So the sentence fired on every run regardless of what any DNO was
                    # actually tendering. Re-worded to what was actually tested.
                    # D54: this sentence must now distinguish TWO different states it used to
                    # collapse into one. "No award is recorded" is true only when the site records
                    # none; a site that records an attested award the physics could not model is a
                    # different finding entirely, and telling its owner "no award is recorded"
                    # would be as false as the register claim LOCALFLEX-1 removed.
                    _local_flex_conditional_reason(site_model, assumptions),
                    *local_flex_published_price_context(assumptions, site_model),
                    "EXCLUSIVITY IS CONDITIONAL, NOT AUTOMATIC (DFS Procurement Rules 4.7.9): "
                    "registering this meter point for a DNO Flexibility Service bars it from "
                    "NESO's Demand Flexibility Service ONLY WHERE THE DFS STACKING LIST SO "
                    "PROVIDES. NESO's winter 2024/25 review records stacking WAS facilitated with "
                    "'all the core DNO Flexibility Services'. GridQ models DFS and DNO local flex "
                    "as ALTERNATIVES and never sums them pending sight of the current Stacking "
                    "List — a conservative modelling choice that UNDERSTATES the stack if the pair "
                    "is in fact permitted. Below 1 MW these are the only two contracted markets "
                    "available, so this choice is load-bearing. A Capacity Market agreement is "
                    "EXPRESSLY permitted alongside either (Rules 4.7.8(a)). DATA ASK: the current "
                    "DFS Stacking List.",
                ],
                consent_status=consent_status,
            )
        # D54: the gate is OPEN, so this stream is CONTRACTED and its £ is in the priced floor.
        # That is the ONE change in this work that moves the return itself rather than what the
        # record displays, and the labelling below is the whole of its mitigation — it must travel
        # with the number, not sit in a doc. An OPEN local-flex gate that says nothing about WHO
        # asserted the award is indistinguishable from one backed by a DNO contract.
        return self._gate(
            GateStatus.OPEN,
            warnings=local_flex_award_disclosure(site_model, assumptions),
            consent_status=consent_status,
        )

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        util_a = _maybe(assumptions, "local_flex_piclo", "utilisation_price_gbp_per_mwh")
        util = _num(util_a)
        key = "local_flex_piclo.utilisation_price_gbp_per_mwh"
        # Rate ladder (D43) — parity with the engine path (`valuation._local_flex_value`): a
        # customer-agreed local-flex £/MWh wins over the assumption utilisation price when the
        # customer consented; else the assumption price; else £0. The proxy previously read ONLY
        # the assumption key, so a site whose agreed rate lived on
        # `export_arrangement.local_flex_agreed_rate_gbp_per_mwh` was priced off the library here
        # and off the agreed rate there — a second, quieter engine/proxy divergence.
        consented_rate = _consent_agreed_rate(site_model, MarketStream.LOCAL_FLEX_PICLO)
        if consented_rate is not None:
            rate: float = consented_rate
            used = []
            notes = [
                f"local flex priced at the customer-agreed rate £{rate:,.2f}/MWh (consent given) "
                "— ahead of the assumption utilisation price (estimation ladder, D43)."
            ]
        elif util_a is None or util is None:
            return PricedValue(0.0, 0.0, (f"not priced: {key} missing or non-numeric.",), ())
        else:
            rate = util
            used = [key]
            notes = [_placeholder_note("local_flex_piclo", "utilisation_price_gbp_per_mwh", util_a)]
        mwh = dispatch.tender_window_discharge_mwh
        if mwh is None:
            notes.append(
                "no tender-window delivered energy supplied (dispatch.tender_window_discharge_mwh) "
                "— priced £0; supply delivered MWh in the awarded tender windows to value it."
            )
            return PricedValue(0.0, 0.0, tuple(notes), tuple(used))
        mult = _price_multiplier(scenario)
        # F-19 parity: availability is NOT utilisation. This path used to pay EVERY in-window
        # discharge across the whole year as an instructed DNO call (an implicit 100% call rate)
        # while the engine path applied the sourced 5% rate — the two disagreed by 20x on a
        # CONTRACTED stream that feeds the priced floor, P50/P90 and the DSCR. Same resolver,
        # same default, same note on both paths now.
        call_rate, call_used, call_note = local_flex_call_rate(assumptions)
        called_mwh = mwh * call_rate
        used += call_used
        notes.append(call_note)
        # D-116, same two legs as the engine path: the payable utilisation volume after the ENA
        # taper/POD cap, plus the availability leg (£0 + data ask until an award lands, per D30).
        payable_mwh, perf_notes = local_flex_utilisation_adjustment(
            delivered_mwh=called_mwh, contracted_mwh=None
        )
        avail_revenue, avail_used, avail_notes = local_flex_availability_value(
            assumptions=assumptions, contracted_mw=None, window_hours=None
        )
        used += avail_used
        gross = rate * payable_mwh * mult
        # Aggregator / route-to-market take (issue 018 WS-C): net = gross x (1 - cut).
        # Precedence site agreed rate -> assumption default -> 0.0 (no cut ever invented).
        cut, cut_src = _aggregator_cut(site_model, assumptions)
        revenue = (gross + avail_revenue * mult) * (1.0 - cut)
        notes.append(
            f"utilisation £/MWh × {round(payable_mwh, 3)} payable MWh "
            f"({mwh} MWh delivered in the awarded tender windows × {call_rate:.0%} call rate) "
            f"(gross £{gross:,.0f})."
        )
        notes.extend(perf_notes)
        notes.extend(avail_notes)
        if cut > 0.0:
            notes.append(
                f"aggregator cut {cut:.0%} ({cut_src}) applied: net £{revenue:,.0f} "
                f"= gross × (1 − {cut:.2f})."
            )
            if cut_src == "assumption":
                used.append("local_flex_piclo.aggregator_cut_pct")
        if mult != 1.0:
            notes.append(f"scenario price multiplier {mult} applied.")
        if gate.gate_status == GateStatus.CONDITIONAL:
            notes.append(
                "CONDITIONAL upside — out of the priced floor until a tender is open and awarded."
            )
        return PricedValue(revenue, 0.0, tuple(notes), tuple(used))


# ---------------------------------------------------------------------------
# Demand Flexibility Service — E-06.
#
# The ONLY direct NESO revenue route below 1 MW. Every other NESO product this pipeline could
# reach (BM, Balancing Reserve, all six DC/DM/DR products) imposes a 1 MW minimum unit, so once
# D-92's gates are enforced a sub-1 MW C&I site — which is all five sites in the live campaign —
# has no NESO route without DFS. Every constant below is sourced; what is not in the register
# emits a labelled data ask instead of a number.
# ---------------------------------------------------------------------------

#: Minimum / maximum Registered Unit Capacity of a DFS Unit, and its rounding rule.
#: `docs/bess_constants_register.md` rows "DFS Minimum Submission Size / minimum Registered Unit
#: Capacity" (0.1 MW) and "DFS Maximum Submission Size" (100 MW): the capacity must be "an amount
#: (if not an integer, rounded to one decimal place) from and including zero point one (0.1) MW to
#: and including one hundred (100) MW".
_DFS_MIN_UNIT_MW = 0.1
_DFS_MAX_UNIT_MW = 100.0
#: One decimal place — the register's own rounding rule for Registered Unit Capacity.
_DFS_CAPACITY_DECIMALS = 1

#: The piecewise DFS performance factor K. Register row "DFS performance factor K (piecewise)":
#: K=1.2 if RV>=120; K=0.01*RV if 50<=RV<120; K=0.01*(2*RV-50) if 25<=RV<50; K=0 if 0<=RV<25;
#: K=0.01*RV if -100<=RV<0; K=-1 if RV<-100 — where RV = 100*V/(Q*0.5) is realised delivery as a
#: percentage of the contracted half-hour energy. Note the two traps the register calls out: K is
#: NEGATIVE below zero realised delivery (the provider PAYS, to -1), and 0-25% delivery earns
#: exactly zero. Any model that clips K at zero understates the downside.
_DFS_K_CAP = 1.2


def dfs_performance_factor(realised_delivery_pct: float) -> float:
    """The DFS performance factor K for a realised delivery percentage ``RV`` (E-06).

    Verbatim from the register's piecewise definition, including the negative limb — clipping K at
    zero would delete a real liability, because a DFS Unit that moves the wrong way against its
    baseline **pays**, down to K = -1.
    """
    rv = realised_delivery_pct
    if rv >= 120.0:
        return _DFS_K_CAP
    if rv >= 50.0:
        return 0.01 * rv
    if rv >= 25.0:
        return 0.01 * (2.0 * rv - 50.0)
    if rv >= 0.0:
        return 0.0  # the dead zone: delivering 0-25% of the contracted volume earns exactly zero
    if rv >= -100.0:
        return 0.01 * rv
    return -1.0


#: Key path of the ONLY sourced DFS price in the library. `_num` cannot read it — the assumption's
#: value is a dict — so the anchor is fetched out of that structure by name. DFS-3: the gate used to
#: publish "no `dfs` assumption section exists in any release … and no register row carries a DFS
#: utilisation price", which is FALSE against gb_v1_10 and is the same key-path-mismatch class of
#: defect as a stream declaring a data ask for a value the release already carries.
_DFS_ANCHOR_SECTION = "ancillary_services"
_DFS_ANCHOR_NAME = "dfs_performance_factor_k_piecewise"
_DFS_ANCHOR_FIELD = "historic_price_anchor_gbp_per_mwh"
_DFS_ANCHOR_KEY = f"{_DFS_ANCHOR_SECTION}.{_DFS_ANCHOR_NAME}.{_DFS_ANCHOR_FIELD}"

#: Key path of the sourced DFS Zone register — 12 zones, identified by NUMBER only. DFS-3: the gate
#: used to publish "the DFS Zone register … is not recorded", also false against gb_v1_10.
_DFS_ZONE_KEY = "ancillary_services.dfs_registered_unit_capacity_mw.zone_names"

#: The machine-detectable stamp on a stream whose £ is DERIVED BUT NOT PRICED. Distinct from a
#: priced £0 and from `gates.D45_NULL_INPUT_LABEL`: it says "this stream is NOT worth zero — a
#: sourced range exists and is published below, but no engine branch prices it", where a null-input
#: label says "an input the model reads is empty". Downstream consumers and tests match on this
#: prefix, so the zero can never degrade into prose indistinguishable from "earns nothing".
REFUSED_POUND_LABEL = "£ NOT PRICED, AND NOT ZERO (D45)"

#: DFS REALISED OUTTURN — NESO's own winter 2024/25 review, published 3 July 2025
#: (https://www.neso.energy/document/363911/download). Every figure below is stated verbatim in
#: that document or is a division of two figures stated in it; see the register section "DFS
#: realised outturn" for the full rows, citations and caveats.
#:
#: **These supersede the £4,559/MWh 2022-23 anchor as an annualisation basis, and the correction is
#: large enough to be the point.** Annualising the old anchor over the 236 procured settlement
#: periods gives ~£538/kW/yr — 25.6× NESO's own full-participation average and roughly 5× the whole
#: grid-scale GB BESS revenue index. The service repriced by roughly 20× when it moved from a
#: winter-contingency enhanced action to a merit-based margin tool, so the two are not comparable.
_DFS_REALISED_SEASON = "winter 2024/25 (27 Nov 2024 - 28 Mar 2025)"
_DFS_REALISED_AVG_ACCEPTED_GBP_PER_MWH = 210.0
_DFS_REALISED_MIN_ACCEPTED_GBP_PER_MWH = 59.0
_DFS_REALISED_MAX_ACCEPTED_GBP_PER_MWH = 1290.0
_DFS_REALISED_EVENTS_PROCURED = 44
_DFS_REALISED_SP_PROCURED = 236
_DFS_REALISED_SP_PUBLISHED = 306

#: NESO's published per-1-MW seasonal revenue scenarios (pp. 17-18), as
#: ``label -> (low, average, high)`` £/MW/season. ``None`` where the source states no figure — the
#: 40-highest-priced-periods scenario publishes only the two high variants. Settlement is
#: ``S = 0.5·P·Q·K`` and therefore LINEAR in offered volume Q, so £/MW scales to any unit size.
_DFS_SCENARIO_GBP_PER_MW_SEASON: dict[str, tuple[float | None, float | None, float]] = {
    "full participation across every published settlement period "
    f"({_DFS_REALISED_SP_PUBLISHED} SPs)": (17_918.0, 21_009.0, 33_560.0),
    "one hour per Service Requirement (112 SPs)": (3_142.0, 7_558.0, 12_477.0),
    "the 40 highest-priced settlement periods": (None, None, 13_726.0),
}

#: The scenario GridQ quotes as the defensible central case, and the one it quotes as the ceiling.
#: S2 is a unit that responds to most Service Requirements without winning every settlement period;
#: S1 is full acceptance, which no single unit should be underwritten at.
_DFS_CENTRAL_GBP_PER_MW_SEASON = 7_558.0
_DFS_CEILING_GBP_PER_MW_SEASON = 21_009.0


def dfs_settlement_value_gbp(
    *, utilisation_price_gbp_per_mwh: float, service_volume_mw: float, k: float
) -> float:
    """One DFS Contracted Settlement Period's settlement value: ``S = 0.5 * P * Q * K``.

    Verbatim from the register row "DFS settlement value formula" (NESO, 'Demand Flexibility
    Service Service Terms', v4.0, effective 9 April 2026, Schedule 1). The ``0.5`` converts a
    30-minute settlement period to MWh. Payment is on the **offered** volume ``Q`` scaled by ``K``,
    so under-delivery is penalised through ``K`` rather than through a reduced ``Q``.

    **This is a per-settlement-period figure and is deliberately not annualised anywhere.**
    Annualising needs a called-event count, which no register row and no library key carries — see
    :func:`dfs_unit_economics_notes`.
    """
    return 0.5 * utilisation_price_gbp_per_mwh * service_volume_mw * k


class DfsZoneObservation(NamedTuple):
    """One DFS zone's own measured 2026/27 outturn: how often it is called, at what price, when.

    The three numbers travel TOGETHER because they move in opposite directions across the twelve
    zones: the thin zones clear highest on almost no volume and the deep zones clear lowest on
    nearly all of it, so one zone's call count on another zone's price manufactures a figure no
    zone could pay. ``window_histogram`` is that same zone's own count of accepted settlement
    periods by LOCAL START TIME, so the shape and the count are one measurement rather than two.
    """

    accepted_settlement_periods: int
    event_days: int
    utilisation_price_gbp_per_mwh: float
    window_histogram: dict[tuple[int, int], int]

    @property
    def gross_gbp_per_kw_yr(self) -> float:
        """GBP/kW/yr if the unit delivered its full rating in every observed called period.

        ``periods x 0.5 h x price``, i.e. the POWER-BOUNDED ceiling on this zone's utilisation
        leg. It is a ceiling and not a forecast: a real dispatch delivers less wherever the state
        of energy, the site's own load or a better-paid half-hour gets in the way.
        """
        return self.accepted_settlement_periods * 0.5 * self.utilisation_price_gbp_per_mwh / 1000.0

    @property
    def periods_per_event_day(self) -> float:
        """Mean accepted settlement periods per delivery day that procured any volume here."""
        return self.accepted_settlement_periods / float(self.event_days)


#: THE MEASURED 12-ZONE DFS BASIS, ON THE OBSERVED PERIOD COUNT (owner instruction, 2026-08-19:
#: "scale the DFS then to the evidence, scaling based on battery size and location to get a more
#: accurate view"). This REPLACES two things: a single modelled zone (6, the most favourable of the
#: twelve) and an ANNUALISATION of a 127-day observation by 365/127.
#:
#: SOURCE. NESO Data Portal, package `demand-flexibility` ("Demand Flexibility Service", NESO Open
#: Data Licence), resource "DFS Utilisation Report" (live 2026/27 season), resource_id
#: 3ebf77d7-05df-466e-a023-dc45a90efeea, portal
#: https://www.neso.energy/data-portal/demand-flexibility-service-dfs, bulk CSV
#: https://api.neso.energy/datastore/dump/3ebf77d7-05df-466e-a023-dc45a90efeea, fetched 2026-08-18
#: and held in this repo at ``data/fetched/neso/dfs_util_cur.csv`` (sha256
#: 655bd61601692045a9e36e87534ccace13406fb7610d66f64d0a509a58edf280). 7,369 bid rows, 70 Event IDs,
#: delivery 2026-04-14 to 2026-08-18 inclusive (127 days), 11 registered participants, 80 DFS Units,
#: all 12 zones populated.
#:
#: HOW EACH NUMBER IS DEFINED, because two of them have a near neighbour that is wrong.
#:   * ``accepted_settlement_periods``: DISTINCT ``(Delivery Date, From_Local)`` cells carrying an
#:     accepted bid in that zone. NOT the bid-row count: zone 6's 948 accepted rows collapse to 407
#:     distinct periods across 9 DFS Units, so rows would overstate the call count about 2.3x. A
#:     previous pass made exactly that slip and it was caught in audit.
#:   * ``event_days``: distinct delivery DAYS carrying one. NESO's own winter-2024/25 review counts
#:     an "event" as a delivery day, which this matches.
#:   * ``utilisation_price_gbp_per_mwh``: VOLUME-WEIGHTED accepted price, cash over energy
#:     (sum of ``0.5 x MW x price`` over sum of ``0.5 x MW``). DFS is PAY-AS-BID (Procurement Rules
#:     v5.0 para 12.2, confirmed empirically: 41.1% of accepted (event, zone, period) cells contain
#:     more than one distinct accepted price), so an accepted bid IS what the provider receives.
#:   * ``window_histogram``: the same distinct periods, counted by local start time.
#: FILTERS, each of which changes the answer: ``Status == Accepted`` (a rejected bid procured
#: nothing), ``Service Requirement Type == Live`` (a Test event is not a market outturn), and
#: ``Event Type == Downwards``. The direction filter matters and is the reason zone 3 reads 163
#: periods rather than 173: the engine measures DFS on NET DISCHARGE, which is the downwards
#: direction, and the file's 10 accepted Upwards bids (1.0 MW in total at GBP 20-45/MWh) are the
#: other direction of a bi-directional product. Mixing them would price a discharge at a charging
#: price.
#:
#: THE OBSERVED COUNT IS USED AS THE ANNUAL COUNT, AND THE DIRECTION OF EFFECT IS **DOWN**. The
#: previous basis annualised zone 6's 407 periods by 365/127 to 1,170 called half-hours a year, so
#: 65% of the modelled calls were an extrapolation of a spring/summer observation onto autumn and
#: winter. The observed count is used instead, unextrapolated: 370 periods a year for zone 10 rather
#: than 1,063 (370 x 365/127), which is 3.16x fewer paid periods at zone 10 and 2.87x fewer overall.
#: It is a MEASURED FLOOR, and it is stated as one: 370 periods were procured in zone 10 in 127
#: days, so if that event rate persists year-round the figure UNDERSTATES a full year by up to 2.9x,
#: while the extrapolation OVERSTATED it if the observed window was seasonally intense. The evidence
#: does not settle which, so the model books what was seen. CONFIRMATION ASK: a full delivery year
#: of the 2026/27 zonal service, which does not exist yet - the redesign is four months old.
#:
#: LABELLED FOR WHAT IT IS: a REALISED, PARTIAL-SEASON OUTTURN of accepted bids, not a forward
#: price, not a guaranteed clear and not a tariff. Generated by
#: ``scripts/derive_dfs_zonal_basis.py --basis``, which refuses to run if the source file's sha256
#: has moved, and pinned against the source by ``tests/test_dfs_zonal_basis.py``.
_DFS_ZONE_OBSERVED: dict[int, DfsZoneObservation] = {
    1: DfsZoneObservation(
        accepted_settlement_periods=82,
        event_days=14,
        utilisation_price_gbp_per_mwh=209.699,
        window_histogram={
            (16, 0): 1,
            (16, 30): 1,
            (17, 0): 4,
            (17, 30): 4,
            (18, 0): 8,
            (18, 30): 7,
            (19, 0): 11,
            (19, 30): 10,
            (20, 0): 9,
            (20, 30): 8,
            (21, 0): 6,
            (21, 30): 6,
            (22, 0): 3,
            (22, 30): 4,
        },
    ),
    2: DfsZoneObservation(
        accepted_settlement_periods=90,
        event_days=15,
        utilisation_price_gbp_per_mwh=222.15,
        window_histogram={
            (16, 0): 1,
            (16, 30): 1,
            (17, 0): 4,
            (17, 30): 4,
            (18, 0): 7,
            (18, 30): 6,
            (19, 0): 13,
            (19, 30): 12,
            (20, 0): 12,
            (20, 30): 11,
            (21, 0): 6,
            (21, 30): 6,
            (22, 0): 3,
            (22, 30): 4,
        },
    ),
    3: DfsZoneObservation(
        accepted_settlement_periods=163,
        event_days=25,
        utilisation_price_gbp_per_mwh=210.495,
        window_histogram={
            (16, 0): 2,
            (16, 30): 2,
            (17, 0): 8,
            (17, 30): 7,
            (18, 0): 18,
            (18, 30): 18,
            (19, 0): 21,
            (19, 30): 20,
            (20, 0): 19,
            (20, 30): 18,
            (21, 0): 11,
            (21, 30): 11,
            (22, 0): 3,
            (22, 30): 5,
        },
    ),
    4: DfsZoneObservation(
        accepted_settlement_periods=206,
        event_days=32,
        utilisation_price_gbp_per_mwh=196.525,
        window_histogram={
            (16, 0): 2,
            (16, 30): 2,
            (17, 0): 10,
            (17, 30): 10,
            (18, 0): 19,
            (18, 30): 19,
            (19, 0): 28,
            (19, 30): 26,
            (20, 0): 25,
            (20, 30): 24,
            (21, 0): 15,
            (21, 30): 16,
            (22, 0): 4,
            (22, 30): 6,
        },
    ),
    5: DfsZoneObservation(
        accepted_settlement_periods=316,
        event_days=42,
        utilisation_price_gbp_per_mwh=161.253,
        window_histogram={
            (15, 30): 1,
            (16, 0): 9,
            (16, 30): 9,
            (17, 0): 19,
            (17, 30): 18,
            (18, 0): 29,
            (18, 30): 29,
            (19, 0): 36,
            (19, 30): 35,
            (20, 0): 34,
            (20, 30): 33,
            (21, 0): 24,
            (21, 30): 25,
            (22, 0): 7,
            (22, 30): 8,
        },
    ),
    6: DfsZoneObservation(
        accepted_settlement_periods=407,
        event_days=50,
        utilisation_price_gbp_per_mwh=159.711,
        window_histogram={
            (15, 30): 1,
            (16, 0): 10,
            (16, 30): 10,
            (17, 0): 26,
            (17, 30): 25,
            (18, 0): 39,
            (18, 30): 39,
            (19, 0): 45,
            (19, 30): 45,
            (20, 0): 44,
            (20, 30): 42,
            (21, 0): 32,
            (21, 30): 32,
            (22, 0): 8,
            (22, 30): 9,
        },
    ),
    7: DfsZoneObservation(
        accepted_settlement_periods=243,
        event_days=36,
        utilisation_price_gbp_per_mwh=171.867,
        window_histogram={
            (16, 0): 5,
            (16, 30): 5,
            (17, 0): 13,
            (17, 30): 13,
            (18, 0): 23,
            (18, 30): 22,
            (19, 0): 31,
            (19, 30): 29,
            (20, 0): 30,
            (20, 30): 25,
            (21, 0): 17,
            (21, 30): 18,
            (22, 0): 5,
            (22, 30): 7,
        },
    ),
    8: DfsZoneObservation(
        accepted_settlement_periods=282,
        event_days=44,
        utilisation_price_gbp_per_mwh=185.889,
        window_histogram={
            (15, 30): 1,
            (16, 0): 8,
            (16, 30): 8,
            (17, 0): 21,
            (17, 30): 18,
            (18, 0): 28,
            (18, 30): 27,
            (19, 0): 33,
            (19, 30): 31,
            (20, 0): 31,
            (20, 30): 27,
            (21, 0): 18,
            (21, 30): 19,
            (22, 0): 5,
            (22, 30): 7,
        },
    ),
    9: DfsZoneObservation(
        accepted_settlement_periods=233,
        event_days=34,
        utilisation_price_gbp_per_mwh=194.654,
        window_histogram={
            (16, 0): 2,
            (16, 30): 2,
            (17, 0): 11,
            (17, 30): 11,
            (18, 0): 21,
            (18, 30): 22,
            (19, 0): 31,
            (19, 30): 29,
            (20, 0): 28,
            (20, 30): 27,
            (21, 0): 18,
            (21, 30): 19,
            (22, 0): 5,
            (22, 30): 7,
        },
    ),
    10: DfsZoneObservation(
        accepted_settlement_periods=370,
        event_days=51,
        utilisation_price_gbp_per_mwh=163.979,
        window_histogram={
            (15, 30): 1,
            (16, 0): 10,
            (16, 30): 10,
            (17, 0): 28,
            (17, 30): 27,
            (18, 0): 39,
            (18, 30): 39,
            (19, 0): 44,
            (19, 30): 41,
            (20, 0): 39,
            (20, 30): 33,
            (21, 0): 23,
            (21, 30): 23,
            (22, 0): 6,
            (22, 30): 7,
        },
    ),
    11: DfsZoneObservation(
        accepted_settlement_periods=379,
        event_days=50,
        utilisation_price_gbp_per_mwh=157.411,
        window_histogram={
            (15, 30): 1,
            (16, 0): 10,
            (16, 30): 10,
            (17, 0): 28,
            (17, 30): 27,
            (18, 0): 39,
            (18, 30): 39,
            (19, 0): 43,
            (19, 30): 42,
            (20, 0): 40,
            (20, 30): 37,
            (21, 0): 26,
            (21, 30): 24,
            (22, 0): 6,
            (22, 30): 7,
        },
    ),
    12: DfsZoneObservation(
        accepted_settlement_periods=366,
        event_days=47,
        utilisation_price_gbp_per_mwh=162.533,
        window_histogram={
            (15, 30): 1,
            (16, 0): 8,
            (16, 30): 8,
            (17, 0): 24,
            (17, 30): 23,
            (18, 0): 34,
            (18, 30): 34,
            (19, 0): 43,
            (19, 30): 42,
            (20, 0): 40,
            (20, 30): 37,
            (21, 0): 28,
            (21, 30): 29,
            (22, 0): 8,
            (22, 30): 7,
        },
    ),
}

#: The span the observation covers: 2026-04-14 to 2026-08-18 inclusive.
_DFS_SPAN_DAYS = 127
#: The factor the SUPERSEDED basis used to annualise that span. Kept because the notes state the
#: counterfactual: the reader is entitled to see the number that was NOT taken and its direction.
_DFS_SUPERSEDED_ANNUALISATION = 365.0 / _DFS_SPAN_DAYS

#: DERIVED FROM THE TABLE, never transcribed alongside it, so the range and the median cannot drift
#: away from the twelve values they summarise. Both are on the OBSERVED period count, so both are
#: about 2.87x below the figures the annualised basis published (GBP 24.71 to GBP 93.41, median
#: GBP 73.22 per kW/yr).
_DFS_OBSERVED_GROSS_SORTED = sorted(o.gross_gbp_per_kw_yr for o in _DFS_ZONE_OBSERVED.values())
_DFS_ZONAL_RANGE_GBP_PER_KW_YR = (
    round(_DFS_OBSERVED_GROSS_SORTED[0], 2),
    round(_DFS_OBSERVED_GROSS_SORTED[-1], 2),
)
_DFS_MEDIAN_ZONE_GROSS_GBP_PER_KW_YR = round(statistics.median(_DFS_OBSERVED_GROSS_SORTED), 2)

#: OUTWARD CODE -> DFS ZONE, derived by POINT-IN-POLYGON and not by inference from a licence area
#: (a DNO licence area and a DFS zone are different geographies and do not nest).
#:
#: METHOD, reproducible by ``scripts/derive_dfs_zonal_basis.py --zones``:
#:   1. NESO, 'DFS 12 Zones GeoJson Map', https://www.neso.energy/document/376656/download - a zip
#:      containing ``12_Zones_DFS.geojson``, CRS84 (WGS84 lon/lat), 12 features each carrying the
#:      single property {'Region': 1..12}. Fetched 2026-08-19; the zip's sha256 is
#:      ed3fa1cc89484faff47ff90fdb89c74d0fa860ff9818e729d25929b993daef88, which REPRODUCES the value
#:      the assumption library already records at
#:      ``ancillary_services.dfs_registered_unit_capacity_mw.zone_names.geojson_sha256``.
#:   2. Postcode centroids from postcodes.io (https://api.postcodes.io), the open API over the ONS
#:      Postcode Directory: ``/outcodes/{outward code}`` for the district centroid and
#:      ``/postcodes/{full postcode}`` for a full code. Retrieved 2026-08-19.
#:   3. Ray-casting point-in-polygon, holes respected, exactly one zone per point.
#:
#: EVERY CAMPAIGN SITE RESOLVES, AND THE DISTRICT CENTROID AGREES WITH THE FULL POSTCODE IN ALL NINE
#: CASES, which is what makes an outward-code key safe here: GL52 2RT and GL52 7RT (Bishops Cleeve,
#: -2.06933/51.90079 and -2.07569/51.94749) both zone 10; OX10 6PJ (Ewelme, -1.06968/51.60772) zone
#: 10; OX11 0QX (Harwell campus, -1.31450/51.57489) zone 10; RG10 9XY (Knowl Hill,
#: -0.81934/51.50975) zone 10; SL3 0EG (Colnbrook and Tanhouse, -0.50553/51.48518) zone 12. So the
#: campaign spans TWO zones and the two are genuinely different: zone 10 pays GBP 163.979/MWh over
#: 370 periods (GBP 30.34/kW/yr gross) and zone 12 GBP 162.533/MWh over 366 (GBP 29.74/kW/yr).
#:
#: A postcode is a DISTRICT centroid, so a district straddling a zone boundary would be resolved to
#: whichever zone its centroid falls in. That is stated as the residual ask rather than hidden, and
#: the full-postcode agreement above is the check that it does not bite on these nine sites.
#:
#: THE TABLE ALSO CARRIES 26 REFERENCE DISTRICTS, at least one per zone, resolved by the same script
#: in the same run, so a site outside this campaign resolves to its own zone instead of falling to
#: the substitution: IV1 (Highland) zone 1, AB10 zone 2, EH1 and G1 zone 3, NE1 / CA1 / DH1 zone 4,
#: LA1 / M1 / LL30 zone 5, LS1 / B1 / NG1 / LE17 / SK1 zone 6, HU1 zone 7, CF10 / SA1 zone 8,
#: PL1 / TR1 zone 9, BS1 / SO14 zone 10, NR1 / PE1 zone 11, SW1A / ME1 zone 12. TWO INDEPENDENT
#: CROSS-CHECKS FALL OUT OF THAT: Bristol and Southampton land in zone 10 with Gloucestershire,
#: Oxfordshire and Berkshire, and London and Medway land in zone 12 with Slough, so the campaign's
#: two zones are corroborated by six unrelated districts each side of the boundary.
_DFS_ZONE_BY_OUTWARD_CODE: dict[str, int] = {
    "AB10": 2,
    "B1": 6,
    "BS1": 10,
    "CA1": 4,
    "CF10": 8,
    "DH1": 4,
    "EH1": 3,
    "G1": 3,
    "GL52": 10,
    "HU1": 7,
    "IV1": 1,
    "LA1": 5,
    "LE17": 6,
    "LL30": 5,
    "LS1": 6,
    "M1": 5,
    "ME1": 12,
    "NE1": 4,
    "NG1": 6,
    "NR1": 11,
    "OX10": 10,
    "OX11": 10,
    "PE1": 11,
    "PL1": 9,
    "RG10": 10,
    "SA1": 8,
    "SK1": 6,
    "SL3": 12,
    "SO14": 10,
    "SW1A": 12,
    "TR1": 9,
}

#: The zone used when a site's location cannot be resolved (D57 limb 5b: substitute the
#: best-evidenced comparable and USE IT IN FULL, never zero the stream). Zone 6 is the
#: best-evidenced of the twelve on every count in the source: the most accepted bid rows (948), the
#: most distinct accepted settlement periods (407), the most delivery days (50) and by far the most
#: procured volume (6,387.8 MW of 15,499.0 MW nationally). The substitution is DECLARED on the
#: record every time it is used, with the zonal range, because a donor zone's clear is never the
#: site's own.
_DFS_SUBSTITUTE_ZONE = 6

#: THE NATIONAL BID-ACTIVITY WINDOW, and it is a DIFFERENT quantity from the per-zone histograms
#: above: this is the count of ALL Live bid ROWS, accepted AND rejected, across all 12 zones, by
#: local start time. It is kept for the one fact it evidences and the per-zone counts cannot - the
#: OUTER BOUND of the service window across the whole market: nothing before 11:00 and nothing after
#: 23:00 in four months of data, with the mass at 17:00-22:00 and the peak at 19:00-19:30. It is NOT
#: the shape the mask uses, and it must not be: a rejected bid weights a clock slot by how hard
#: providers tried, not by what NESO procured. The docstring that used to call these "Live event
#: settlement periods" was wrong on both counts, which is how a bid-row shape came to drive a
#: settlement-period model.
_DFS_NATIONAL_LIVE_BID_ROWS_BY_SLOT: dict[tuple[int, int], int] = {
    (11, 0): 27,
    (11, 30): 31,
    (12, 0): 60,
    (12, 30): 60,
    (13, 0): 60,
    (13, 30): 60,
    (14, 0): 64,
    (14, 30): 64,
    (15, 0): 50,
    (15, 30): 57,
    (16, 0): 110,
    (16, 30): 109,
    (17, 0): 338,
    (17, 30): 346,
    (18, 0): 706,
    (18, 30): 734,
    (19, 0): 905,
    (19, 30): 926,
    (20, 0): 805,
    (20, 30): 689,
    (21, 0): 408,
    (21, 30): 397,
    (22, 0): 109,
    (22, 30): 101,
}


#: THE FULL-YEAR HALF-HOURLY WHOLESALE PRICE SERIES, held in this repo (D57, 2026-08-18).
#:
#: Elexon Insights API (public, unauthenticated), dataset MID, data provider APXMIDP, 17,521
#: half-hours covering 2025-08-13 to 2026-08-12 inclusive - EXACTLY the twelve months of the
#: campaign's metered load, so the join is a clean one and nothing is interpolated or extrapolated.
#: sha256 03aee3de3e66de90b8b9006fb81b3aa86fe31b81c6030a63be3572daf2410ee5, which is the hash the
#: gb_v1_12 key ``wholesale_p415.study_basis_mid_half_hourly_price_series`` cites. Mean GBP
#: 86.968/MWh, volume-weighted mean GBP 88.728, median GBP 88.51, p10 GBP 40.75, p90 GBP 124.70,
#: min GBP -102.92 (427 negative half-hours), max GBP 560.81.
#:
#: **WHY THE SHAPE, NOT THE MEAN, IS THE WHOLE POINT.** A flat annual mean can never clear an export
#: decision: on a real campaign site the delivered cost of a charged kWh plus its marginal
#: degradation is about GBP 199/MWh, and the mean is GBP 87. Priced flat, the optimiser rationally
#: never exports and the stream reports GBP 0 - which reads as "export is worthless" when what it
#: actually says is "the average of a volatile series is below the marginal cost". Priced on the
#: SERIES, 64 half-hours a year clear that bar and 23 clear GBP 250/MWh, so the optimiser exports in
#: those and nowhere else. That is a dispatch decision on real prices instead of an artefact of
#: averaging.
#:
#: SETTLEMENT-PERIOD CONVENTION, stated because getting it wrong shifts the whole price shape by an
#: hour: **SP1 = 00:00-00:30 LOCAL**. Confirmed against the data rather than assumed - the file's
#: first row is SP3 on 2025-08-13, which is 01:00-01:30 local = 00:00Z under BST, i.e. the fetch
#: window's own start; and the dearest settlement period across the year is SP38 = 18:30-19:00
#: local,
#: which is the GB evening peak. Clock-change days carry 46 or 50 periods and are matched on their
#: own (date, period) key like any other, never re-indexed.
_MID_SERIES_PATH = "data/fetched/elexon/mid_year_2025-08-13_2026-08-12.csv"
_MID_SERIES_SHA256 = "03aee3de3e66de90b8b9006fb81b3aa86fe31b81c6030a63be3572daf2410ee5"
#: The most half-hours a GB year of clock changes can leave without a clock-derived
#: settlement-period key: 2 missing on the 46-period spring day plus 2 duplicated on the
#: 50-period autumn day. Beyond this the series is genuinely misaligned and is refused.
_MID_CLOCK_CHANGE_TOLERANCE = 4


@lru_cache(maxsize=4)
def _mid_price_lookup(path: str) -> dict[tuple[str, int], float]:
    """Parse the MID price file ONCE per process, keyed by (settlement date, settlement period).

    Cached because the sizing search calls the series builder on EVERY candidate solve - several
    hundred times per site - and re-reading and re-parsing 17,521 CSV rows each time turned a
    six-minute run into an hour of I/O. The cache is keyed on the path, so a different file is a
    different entry and nothing is shared between them.
    """
    import csv as _csv

    out: dict[tuple[str, int], float] = {}
    with Path(path).open() as fh:
        for row in _csv.DictReader(fh):
            out[(row["settlement_date"], int(row["settlement_period"]))] = (
                float(row["price_gbp_per_mwh"]) / 1000.0
            )
    return out


def mid_half_hourly_export_price_series(
    timestamps: Any, *, repo_root: Any = None
) -> tuple[np.ndarray | None, list[str]]:
    """The MID price series (GBP/kWh) aligned to ``timestamps``, or ``None`` if it cannot be.

    Returns ``(array | None, notes)``. ``None`` means the series could not be aligned and the caller
    must fall back to its own basis and say so - it never means GBP 0, and nothing is interpolated,
    extrapolated or back-filled. A partial match is refused outright rather than mixed with a flat
    fallback, because a series that is real for part of a year and constant for the rest is the kind
    of input a reader cannot reason about.

    **Vectorised, because the sizing search calls this on every candidate solve.** The lookup keys
    are
    built with pandas array operations rather than a Python loop over 17,518 timestamps, and the
    file
    is parsed once per process (:func:`_mid_price_lookup`).
    """
    import numpy as np
    import pandas as pd

    root = Path(str(repo_root)) if repo_root is not None else Path(__file__).resolve().parents[3]
    path = root / _MID_SERIES_PATH
    if not path.exists():
        return None, [
            f"HALF-HOURLY WHOLESALE SERIES NOT FOUND at {_MID_SERIES_PATH}: the export price falls "
            "back to the caller's own basis. Nothing is invented and no price is set to zero."
        ]
    by_key = _mid_price_lookup(str(path))
    idx = pd.DatetimeIndex(timestamps)
    dates = idx.strftime("%Y-%m-%d")
    periods = np.asarray(idx.hour) * 2 + (np.asarray(idx.minute) >= 30).astype(int) + 1
    prices = pd.Series([by_key.get((d, int(sp))) for d, sp in zip(dates, periods, strict=True)])
    unmatched = prices.isna()
    missing = int(unmatched.sum())
    # CLOCK-CHANGE TOLERANCE, bounded and disclosed. A GB year has two clock changes: the spring
    # short day has 46 settlement periods (SP 3 and 4 do not exist) and the autumn long day has 50
    # (the local clock repeats 01:00-02:00, so two intervals share one clock-derived SP key). A
    # clock-derived SP therefore cannot match on AT MOST FOUR intervals a year, and refusing a whole
    # year's series over four intervals out of 17,518 would throw away the entire basis for a
    # calendar artefact. Those intervals are carried forward from the previous matched half-hour and
    # the COUNT is reported; anything beyond four is a real misalignment and is still refused.
    clock_note = ""
    if 0 < missing <= _MID_CLOCK_CHANGE_TOLERANCE:
        clock_note = (
            f"CLOCK-CHANGE FILL, DISCLOSED: {missing} of {len(idx):,} half-hours "
            f"({missing / len(idx):.4%}) had no clock-derived (settlement date, settlement period) "
            "key in the price file, which is what a 46-period spring day and a 50-period autumn "
            "day "
            "do to any clock-derived period number. Each is carried forward from the previous "
            f"matched half-hour. The tolerance is {_MID_CLOCK_CHANGE_TOLERANCE} intervals a year - "
            "the most two clock changes can produce - and anything beyond it is refused as a real "
            "misalignment rather than filled."
        )
        prices = prices.ffill().bfill()
        missing = int(prices.isna().sum())
    if missing:
        return None, [
            f"HALF-HOURLY WHOLESALE SERIES NOT ALIGNED: {missing} of {len(idx)} priced half-hours "
            f"have no matching (settlement date, settlement period) row in {_MID_SERIES_PATH}, "
            "which covers 2025-08-13 to 2026-08-12 only. The series is REFUSED rather than "
            "part-filled - a price that is real for part of a year and constant for the rest is "
            "not "
            "a basis a reader can reason about. The export price falls back to the caller's own "
            "basis. DATA ASK: a MID series covering this site's metered horizon."
        ]
    return np.asarray(prices.to_numpy(), dtype=float), [
        f"EXPORT PRICED ON A REAL FULL-YEAR HALF-HOURLY SERIES, not on an annual mean: "
        f"{len(idx):,} half-hours of Elexon Market Index Data (dataset MID, provider APXMIDP) held "
        f"at {_MID_SERIES_PATH}, sha256 {_MID_SERIES_SHA256[:16]}..., covering exactly the twelve "
        "months of this campaign's metered load, joined on (settlement date, settlement period) "
        "with SP1 = 00:00-00:30 LOCAL. Mean GBP 86.968/MWh, p90 GBP 124.70, max GBP 560.81, and "
        "427 "
        "half-hours cleared NEGATIVE. WHY THE SHAPE MATTERS: the delivered cost of a charged kWh "
        "plus its marginal degradation is roughly GBP 199/MWh on these sites, so a FLAT mean of "
        "GBP "
        "87 can never clear an export decision and the optimiser would rationally never export - a "
        "GBP 0 that reads as 'export is worthless' when it only says 'the average of a volatile "
        "series is below marginal cost'. On the series, 64 half-hours a year clear that bar and 23 "
        "clear GBP 250/MWh. LABELLED FOR WHAT IT IS: a HISTORIC OUTTURN spot index, not a forward "
        "curve, and a WHOLESALE index at the transmission boundary - a behind-the-meter exporter "
        "does not receive it gross, and embedded benefits, supplier margin, imbalance exposure and "
        "the aggregator cut all sit between it and the site's bank account. CONFIRMATION ASK: an "
        "agreed export tariff or a forward curve for the delivery years.",
        *([clock_note] if clock_note else []),
    ]


#: The assumption key that carries the same 12-zone table as :data:`_DFS_ZONE_OBSERVED`, so the
#: per-stream assumptions register can CITE a library key for the largest stream in the pack instead
#: of printing a traceability gap. The library is preferred and the module constants are the
#: byte-identical fallback for a release that predates the key;
#: ``tests/test_dfs_zonal_basis.py`` pins the two copies to each other so they cannot drift.
_DFS_ZONAL_SECTION = "ancillary_services"
_DFS_ZONAL_NAME = "dfs_zonal_observed_2026_27"
_DFS_ZONAL_KEY = f"{_DFS_ZONAL_SECTION}.{_DFS_ZONAL_NAME}"


def dfs_zone_for_site(site_model: SiteModel) -> tuple[int, bool, list[str]]:
    """This site's own DFS Zone: ``(zone, resolved, notes)``.

    DFS is a ZONAL service - Procurement Rules v5.0 Schedule 1 requires that "all Unit Meter Points
    in a DFS Unit must be located in the same DFS Zone" - and the zone sets BOTH the price and the
    number of calls, in OPPOSITE directions. A national median is therefore the one figure that is
    certainly wrong for every site, and it is what this function exists to stop being used.

    Resolved from the site's own postcode district by point-in-polygon against NESO's published
    12-zone geography; :data:`_DFS_ZONE_BY_OUTWARD_CODE` carries the method, the retrieval and the
    sha256 that reproduces the value the assumption library already records. Where the postcode is
    absent, malformed or outside the derived table, D57 limb 5b applies: the best-evidenced
    comparable zone is substituted, USED IN FULL, and DECLARED. The stream is never zeroed, deferred
    or held conditional for an unresolved location.
    """
    raw = str(getattr(site_model, "postcode", "") or "").strip().upper()
    outward = raw.split()[0] if raw else ""
    zone = _DFS_ZONE_BY_OUTWARD_CODE.get(outward)
    low, high = _DFS_ZONAL_RANGE_GBP_PER_KW_YR
    if zone is not None:
        obs = _DFS_ZONE_OBSERVED[zone]
        return (
            zone,
            True,
            [
                f"DFS ZONE {zone}, THIS SITE'S OWN, resolved from postcode district {outward} by "
                "point-in-polygon against NESO's published 12-zone geography ('DFS 12 Zones "
                "GeoJson Map', https://www.neso.energy/document/376656/download, sha256 "
                "ed3fa1cc8948... which reproduces the value recorded at "
                "ancillary_services.dfs_registered_unit_capacity_mw.zone_names.geojson_sha256), "
                "with the district centroid taken from postcodes.io over the ONS Postcode "
                "Directory. Reproducible: scripts/derive_dfs_zonal_basis.py --zones. THE ZONE'S "
                f"OWN MEASURED 2026/27 OUTTURN: {obs.accepted_settlement_periods} accepted "
                f"settlement periods over {obs.event_days} delivery days in {_DFS_SPAN_DAYS} "
                "days, at a volume-weighted accepted price of GBP "
                f"{obs.utilisation_price_gbp_per_mwh:,.3f}/MWh, which is GBP "
                f"{obs.gross_gbp_per_kw_yr:,.2f}/kW/yr if the unit delivers its full rating in "
                f"every one of them. The twelve zones span GBP {low:,.2f} to GBP "
                f"{high:,.2f}/kW/yr on that basis, median GBP "
                f"{_DFS_MEDIAN_ZONE_GROSS_GBP_PER_KW_YR:,.2f}, so the zone is worth up to 3.8x "
                "either way and a national average would be wrong for every site. DATA ASK, and "
                "it is the only one left on the zone: the site's FULL postcode. What was used is "
                "the postcode DISTRICT centroid, so a district straddling a zone boundary could "
                "resolve to the wrong side; each campaign district was additionally checked "
                "against a full postcode inside it and all nine agreed.",
            ],
        )
    obs = _DFS_ZONE_OBSERVED[_DFS_SUBSTITUTE_ZONE]
    recorded = raw or "(empty)"
    return (
        _DFS_SUBSTITUTE_ZONE,
        False,
        [
            "DFS ZONE NOT RESOLVED FROM THIS SITE'S RECORD, SO A ZONE IS SUBSTITUTED AND USED IN "
            f"FULL (D57 limb 5b). The record carries postcode '{recorded}', which yields no "
            f"outward code in the derived point-in-polygon table, so zone {_DFS_SUBSTITUTE_ZONE} "
            "is used: it is the BEST-EVIDENCED of the twelve on every count in NESO's own "
            f"utilisation report (948 accepted bid rows, {obs.accepted_settlement_periods} "
            f"distinct accepted settlement periods, {obs.event_days} delivery days, and 6,387.8 "
            "MW of the 15,499.0 MW procured nationally). Its terms are GBP "
            f"{obs.utilisation_price_gbp_per_mwh:,.3f}/MWh over {obs.accepted_settlement_periods} "
            f"periods, GBP {obs.gross_gbp_per_kw_yr:,.2f}/kW/yr gross, against a zonal range of "
            f"GBP {low:,.2f} to GBP {high:,.2f}/kW/yr (median GBP "
            f"{_DFS_MEDIAN_ZONE_GROSS_GBP_PER_KW_YR:,.2f}). A DONOR ZONE'S CLEAR IS NOT THIS "
            "SITE'S OWN and is never presented as one. The stream is NOT zeroed, deferred or "
            "held conditional for an unresolved zone: D57 limb 5b forbids that expressly. DATA "
            "ASK: this site's postcode, from which the zone is derivable by point-in-polygon in "
            "one step (scripts/derive_dfs_zonal_basis.py --zones).",
        ],
    )


def dfs_zone_basis(
    assumptions: AssumptionLibrary, *, zone: int
) -> tuple[DfsZoneObservation, list[str], list[str]]:
    """One zone's measured DFS basis: ``(observation, assumption_keys_used, notes)``.

    Prefers the assumption library's own copy of the 12-zone table so the figure carries a citable
    key, and falls back to the module constants - which are the same numbers, pinned to each other
    by test - for a release cut before the key existed. The fallback is DISCLOSED and never silent,
    and it never reads as zero: an unknown zone is a ``KeyError`` from the caller's resolver, not a
    GBP 0 on the stream.
    """
    a = _maybe(assumptions, _DFS_ZONAL_SECTION, _DFS_ZONAL_NAME)
    fallback = _DFS_ZONE_OBSERVED[zone]
    by_zone = a.value.get("by_zone") if a is not None and isinstance(a.value, dict) else None
    row = None
    if isinstance(by_zone, dict):
        row = by_zone.get(zone, by_zone.get(str(zone)))
    if not isinstance(row, dict):
        return (
            fallback,
            [],
            [
                "DFS ZONAL BASIS READ FROM THE MODULE CONSTANTS, NOT THE LIBRARY: "
                f"{_DFS_ZONAL_KEY} carries no row for zone {zone} on this release (it was added "
                "in gb_v1_12), so the transcribed table in gridq.value_stack.gb_streams is used. "
                "Same numbers, same source, same sha256; what is missing is only the citable "
                "key, and this note is the citation in its place."
            ],
        )
    observation = DfsZoneObservation(
        accepted_settlement_periods=int(row["accepted_settlement_periods"]),
        event_days=int(row["event_days"]),
        utilisation_price_gbp_per_mwh=float(row["utilisation_price_gbp_per_mwh"]),
        # The library carries the COUNTS and the PRICE; the window SHAPE stays in code, because a
        # 12 x 15 histogram in YAML is a transcription hazard with no reader. Same measurement.
        window_histogram=fallback.window_histogram,
    )
    return observation, [_DFS_ZONAL_KEY], []


def dfs_service_period_mask(
    timestamps: Any, *, zone: int, assumptions: AssumptionLibrary
) -> tuple[np.ndarray, list[str]]:
    """The half-hours a DFS Unit in ``zone`` is treated as CALLED in, over a metered horizon.

    Returns ``(boolean mask aligned to timestamps, notes)``. ``numpy`` is imported at call time so
    this module stays importable without it, matching the rest of the file.

    TWO THINGS COME FROM THE ZONE'S OWN MEASUREMENT, and they are one measurement rather than two:
    the COUNT of called half-hours (its accepted settlement periods over the 127-day observation,
    used AS the annual count and NOT annualised) and the SHAPE (those same periods counted by local
    start time). Only the choice of WHICH DAYS they fall on is the model's.
    """
    import numpy as np
    import pandas as pd

    observation, _used, basis_notes = dfs_zone_basis(assumptions, zone=zone)
    histogram = observation.window_histogram
    # VECTORISED, because the sizing search calls this on every candidate solve - several hundred
    # times per site. Three Python list comprehensions over 17,518 timestamps each turned a
    # six-minute run into an hour of interpreter time, which is the same defect the MID series
    # lookup had and was found the same way: by watching a run burn wall clock without burning CPU.
    idx = pd.DatetimeIndex(timestamps)
    n = len(idx)
    mask = np.zeros(n, dtype=bool)
    hours = np.asarray(idx.hour)
    minutes = np.asarray(idx.minute)
    # The horizon's own length in years, so a part-year series is not given a full year of calls.
    horizon_years = max(n, 1) / (365.0 * 48.0)
    target = observation.accepted_settlement_periods * horizon_years
    total_weight = float(sum(histogram.values()))
    placed = 0
    for (h, m), weight in histogram.items():
        slot = np.flatnonzero((hours == h) & (minutes == m))
        if slot.size == 0:
            continue
        take = int(round(target * weight / total_weight))
        take = min(take, int(slot.size))
        if take <= 0:
            continue
        picks = slot[np.linspace(0, slot.size - 1, take).round().astype(int)]
        mask[np.unique(picks)] = True
        placed += int(np.unique(picks).size)
    if placed == 0:
        return mask, [
            *basis_notes,
            "DFS SERVICE PERIODS: this horizon contains no half-hour inside the measured DFS "
            "event window (11:00-23:00 local), so no called period is modelled and the "
            "utilisation leg is GBP 0 for a CALENDAR reason. It is not a finding that the site "
            "cannot deliver DFS.",
        ]
    peak = max(histogram, key=lambda k: histogram[k])
    window_slots = sorted(_DFS_NATIONAL_LIVE_BID_ROWS_BY_SLOT)
    first, last = window_slots[0], window_slots[-1]
    window_span = f"{first[0]:02d}:{first[1]:02d} to {last[0]:02d}:{last[1]:02d}"
    annualised = round(observation.accepted_settlement_periods * _DFS_SUPERSEDED_ANNUALISATION)
    return mask, [
        *basis_notes,
        f"DFS SERVICE PERIODS MODELLED: {int(mask.sum())} called half-hours over this horizon, "
        f"from a target of {observation.accepted_settlement_periods} per year, which is ZONE "
        f"{zone}'s OWN OBSERVED COUNT: the distinct settlement periods in which an accepted Live "
        f"Downwards bid procured volume in that zone over {_DFS_SPAN_DAYS} days (2026-04-14 to "
        f"2026-08-18), across {observation.event_days} delivery days, measured from NESO's own "
        "utilisation report held at data/fetched/neso/dfs_util_cur.csv (sha256 655bd616...). "
        "They are allocated across clock slots in proportion to that same zone's own count of "
        "those periods by local start time, so the COUNT and the SHAPE are one measurement: the "
        f"peak slot is {peak[0]:02d}:{peak[1]:02d} and nothing falls outside {window_span} local "
        "in four months of market-wide bidding. SHAPE, NOT SOURCED: which specific DAYS those "
        "half-hours fall on - the real events fell wherever the margin was tight. ASSUMED under "
        "D57: that this unit wins every period in which its zone procured anything; the measured "
        "volume fill was 3.84% of the published requirement, with 42 of 97 Live events procuring "
        "exactly zero, so the binding constraint on winning is the bid price and not the volume "
        "cap.",
        "SCALED TO THE OBSERVED PERIODS, AND THE DIRECTION OF EFFECT IS DOWN (owner instruction, "
        "2026-08-19). The previous basis annualised a 127-day observation by 365/127, which for "
        f"this zone would book {annualised} called half-hours a year instead of "
        f"{observation.accepted_settlement_periods}: "
        f"{annualised / observation.accepted_settlement_periods:,.2f}x more paid periods than "
        "were ever observed, on a spring-and-summer window extrapolated across autumn and "
        "winter. What is modelled now is what was seen. LABELLED HONESTLY IN BOTH DIRECTIONS: "
        "this is a MEASURED FLOOR, so if the event rate persists year-round it UNDERSTATES a "
        f"full year by up to {annualised / observation.accepted_settlement_periods:,.2f}x, and "
        "the superseded extrapolation OVERSTATED it if the observed window was seasonally "
        "intense. The evidence does not settle which way, and a full delivery year of the "
        "2026/27 zonal service does not exist yet - the redesign is four months old. "
        "CONFIRMATION ASK: NESO's utilisation report for a complete delivery year.",
        "THE EVENING PEAK IS WANTED BY FOUR STREAMS AT ONCE, AND CAN BE SOLD ONLY ONCE. The DFS "
        f"window peaks at {peak[0]:02d}:{peak[1]:02d}, which is also the DUoS red band, the "
        "Capacity Market supplier-levy peak window and the DC/DM/DR evening EFA blocks. That "
        "collision is PHYSICS and it is enforced where it belongs - in the dispatch: there is "
        "one discharge variable, one state of energy and one annual warranty throughput budget, "
        "every price component is a disjoint array over the same half-hours, and DFS delivery "
        "pays the same marginal degradation cost as arbitrage. The optimiser therefore SELLS "
        "EACH HALF-HOUR ONCE, to whichever stream values it most. What D57 waives is the "
        "regulatory exclusivity, never the physics.",
    ]


def dfs_utilisation_rate_gbp_per_kwh(
    assumptions: AssumptionLibrary, *, zone: int
) -> tuple[float, list[str], list[str]]:
    """The GBP/kWh the DFS objective pays per net kWh delivered in a called period in ``zone``.

    Returns ``(rate, assumption_keys_used, notes)``.

    ``S = 0.5 x P x Q x K`` per settlement period, and ``0.5 x Q`` IS the MWh delivered in that
    half-hour, so per delivered MWh the settlement is ``P x K`` and per kWh it is ``P x K / 1000``.

    ``P`` is THIS ZONE'S volume-weighted ACCEPTED utilisation price on the current 2026/27 zonal
    service, taken together with that zone's own call count because the two move in opposite
    directions across the twelve. ``K = 1.0`` is FULL DELIVERY OF THE OFFERED VOLUME. K is
    deliberately not the fleet's realised 0.3896: that statistic corrects OFFERED volume against
    SETTLED volume, and this figure is built from DISPATCH-MEASURED delivery, where the offered and
    delivered volumes are the same by construction. Applying it here would haircut a measured
    quantity for a mismatch that cannot exist in it. The published schedule's ``K = 1.2``
    over-delivery bonus is also not taken - it would assert delivery BEYOND the volume offered,
    which is a behaviour and not a gate.
    """
    observation, used, basis_notes = dfs_zone_basis(assumptions, zone=zone)
    k = 1.0
    rate = observation.utilisation_price_gbp_per_mwh * k / 1000.0
    low, high = _DFS_ZONAL_RANGE_GBP_PER_KW_YR
    return (
        rate,
        used,
        [
            *basis_notes,
            f"DFS UTILISATION RATE GBP {rate:,.6f}/kWh = P x K / 1000, with P = GBP "
            f"{observation.utilisation_price_gbp_per_mwh:,.3f}/MWh and K = {k:g}. LABELLED FOR "
            f"WHAT IT IS: P is the VOLUME-WEIGHTED ACCEPTED BID PRICE for ZONE {zone} - THIS "
            "SITE'S OWN ZONE - on the CURRENT 2026/27 zonal service, measured over the accepted "
            "Live Downwards bids in NESO's own utilisation report "
            "(data/fetched/neso/dfs_util_cur.csv, 2026-04-14 to 2026-08-18). DFS is PAY-AS-BID "
            "(Procurement Rules v5.0 s12.2), so an accepted price is what the provider actually "
            "receives. It is a REALISED price from a partial season, not a forward price and not "
            "a guaranteed clear; the marginal accepted price - the bar a new entrant must beat - "
            "was a median of GBP 155.00 and a p90 of GBP 210.17/MWh over 3,147 (event, zone, "
            "period) cells market-wide, so the acceptance curve is flat and bidding at the "
            "average wins most of the time.",
            f"K = {k:g} IS FULL DELIVERY OF THE OFFERED VOLUME, assumed under D57. It is "
            "deliberately NOT the fleet's realised 0.3896: that statistic corrects OFFERED "
            "volume against SETTLED volume, and this figure is built from DISPATCH-MEASURED "
            "delivery, where the offered and delivered volumes are the same by construction - so "
            "the mismatch it corrects for cannot exist in it, and applying it would haircut a "
            "measured quantity twice. The published schedule's K = 1.2 over-delivery bonus is "
            "also not taken: it would assert delivery BEYOND the volume offered, which is a "
            "behaviour and not a gate. CONFIRMATION ASK: this unit's own realised delivery "
            "percentage against its offered volume, which is what sets K.",
            "THE ZONAL PAIR IS TAKEN TOGETHER, WHICH IS WHY THIS IS NOT THE FAVOURABLE END OF "
            f"THE RANGE. Zone {zone}'s price and its call count are both its own: "
            f"{observation.accepted_settlement_periods} accepted settlement periods over "
            f"{observation.event_days} delivery days in {_DFS_SPAN_DAYS} days at GBP "
            f"{observation.utilisation_price_gbp_per_mwh:,.3f}/MWh, which is GBP "
            f"{observation.gross_gbp_per_kw_yr:,.2f}/kW/yr gross at full delivery. The twelve "
            f"zones span GBP {low:,.2f} to GBP {high:,.2f}/kW/yr and the MEDIAN zone is GBP "
            f"{_DFS_MEDIAN_ZONE_GROSS_GBP_PER_KW_YR:,.2f}/kW/yr. Price and call count move in "
            "OPPOSITE directions across zones - the thin zones clear highest on tiny volume - so "
            "mixing one zone's count with another's price would manufacture a figure no zone "
            "could pay, and that is expressly not done here. D56 directs the favourable end of a "
            "sourced range, and the owner's 2026-08-19 instruction directs the SITE'S OWN ZONE "
            "instead: a location is a fact about the site, not a range to choose from, so where "
            "the two conflict the site's own zone wins.",
            "NOT USED, AND DELIBERATELY: the library's own two DFS figures are both superseded "
            f"here. The GBP 4,559/MWh at {_DFS_ANCHOR_KEY} is a 2022-23 anchor from the PREVIOUS "
            "winter-contingency scheme, roughly 28x this price, and annualises to about 5x the "
            "entire grid-scale GB BESS revenue index. The winter 2024/25 per-MW scenario set "
            "(ancillary_services.study_basis_dfs_gbp_per_kw_yr, GBP 7.56/kW/yr central) predates "
            "the 9 April 2026 zonal redesign: the call count rose an order of magnitude while "
            "the price fell, and on the observed 2026/27 basis the median zone is GBP "
            f"{_DFS_MEDIAN_ZONE_GROSS_GBP_PER_KW_YR:,.2f}/kW/yr gross at full delivery.",
        ],
    )


def dfs_seasonal_value_range_gbp(
    service_volume_mw: float,
) -> tuple[float, float, float]:
    """NESO's per-1-MW DFS scenarios scaled to ``service_volume_mw``: (low, central, high).

    Settlement is ``S = 0.5·P·Q·K``, which is **linear in the offered volume Q**, so NESO's stated
    1 MW figures scale directly to a sub-MW Registered Unit Capacity. No other term is touched:
    these are the source's own numbers, not a re-derivation from P and K.
    """
    lows = [v[0] for v in _DFS_SCENARIO_GBP_PER_MW_SEASON.values() if v[0] is not None]
    highs = [v[2] for v in _DFS_SCENARIO_GBP_PER_MW_SEASON.values()]
    return (
        min(lows) * service_volume_mw,
        _DFS_CENTRAL_GBP_PER_MW_SEASON * service_volume_mw,
        max(highs) * service_volume_mw,
    )


def dfs_unit_economics_notes(
    assumptions: AssumptionLibrary, service_volume_mw: float | None
) -> tuple[list[str], list[str]]:
    """DFS's DERIVED, SOURCED value range, and the reason it is still not priced into the record.

    Returns ``(notes, assumption_keys_used)``.

    **This used to be a refusal, and the refusal was wrong** (Olly, 2026-08-17). The stream declared
    a data ask *and* priced £0.00 — D45's forbidden both-and-neither — and the first fix withheld
    the annual £ on the grounds that the called-event count and realised K were unsourceable. A
    recorded search then found them: NESO's own winter 2024/25 review publishes the event count (44
    procured events over 236 settlement periods), the realised average accepted price (£210/MWh),
    the aggregate performance withholding, **and NESO's own per-1-MW annual revenue scenarios**.
    Under D45 that is sourcing, so the honest outcome is *derive and declare*, not refuse.

    **The correction this uncovered is the important part.** The library's only DFS price was a
    £4,559/MWh 2022-23 anchor. Annualised over the 236 procured settlement periods it gives roughly
    £538/kW/yr — **25.6× NESO's own full-participation average and about 5× the entire grid-scale GB
    BESS revenue index**. Anything near that figure at a 93-750 kW site is a defect signal, not a
    result. The anchor is kept only as a superseded historic marker.

    **AND THE REASON IT USED TO GIVE IS NO LONGER TRUE (2026-08-19).** These notes told every reader
    that the record showed £0 "only because no engine branch prices this stream yet". That branch
    has existed since D57 item 4: `optimisation.valuation._dfs_value` prices DFS from the dispatch
    it measures over the called periods, on the site's own zone. Leaving the old sentence in put
    two contradictory statements about the same stream in one artifact - a measured £ and a claim
    that nothing could measure it. NESO's per-1-MW scenarios are therefore presented here for what
    they now are: a PUBLISHED CROSS-CHECK on a different (winter 2024/25, pre-zonal) basis, quoted
    beside the priced figure and never added to it, because they are two estimates of one pound.
    """
    notes: list[str] = []
    used: list[str] = []
    q = service_volume_mw if service_volume_mw is not None and service_volume_mw > 0 else None

    headline = (
        "PUBLISHED CROSS-CHECK ON A SUPERSEDED BASIS, NOT THE £: DFS has a SOURCED value range and "
        "it is NOT £0. The priced figure on this stream is the DISPATCH-MEASURED settlement over "
        "this site's own DFS zone's called settlement periods; what follows is NESO's own per-MW "
        "scenario set from the winter 2024/25 (pre-zonal) season, quoted beside it as a published "
        "cross-check and never added to it. "
    )
    if q is not None:
        low, central, high = dfs_seasonal_value_range_gbp(q)
        headline += (
            f"At a {q:,.1f} MW Registered Unit Capacity, NESO's own published scenarios give "
            f"£{low:,.0f}-£{high:,.0f} per season, with £{central:,.0f} "
            f"(£{_DFS_CENTRAL_GBP_PER_MW_SEASON / 1000.0:,.2f}/kW/yr) the defensible central case "
            f"and £{_DFS_CEILING_GBP_PER_MW_SEASON * q:,.0f} "
            f"(£{_DFS_CEILING_GBP_PER_MW_SEASON / 1000.0:,.2f}/kW/yr) the full-acceptance ceiling. "
            f"NOTE THE VOLUME BASIS: {q:,.1f} MW comes from the site's CONNECTION bound, an "
            "UPPER BOUND on the offered Service Volume — the sized battery may be smaller and the "
            "£/yr figures scale linearly down with it. The £/kW/yr figures are size-invariant; "
            "the £/yr figures are not."
        )
    else:
        headline += (
            "NESO's published per-1-MW scenarios span £3,142-£33,560 per season "
            "(£3.14-£33.56/kW/yr), central case £7,558 (£7.56/kW/yr)."
        )
    notes.append(headline)
    notes.append(
        "SCENARIO BASIS (NESO's own, not GridQ's arithmetic): a single 1 MW DFS unit over "
        f"{_DFS_REALISED_SEASON}. Full participation across all {_DFS_REALISED_SP_PUBLISHED} "
        "published settlement periods: £17,918 low / £21,009 average / £33,560 high. One hour per "
        "Service Requirement (112 SPs): £3,142 / £7,558 / £12,477. The 40 highest-priced periods: "
        "£13,726. Settlement S = 0.5 × P × Q × K is LINEAR in the offered volume Q, so the per-MW "
        "figures scale directly to a sub-MW unit. Source: NESO, 'Demand Flexibility Service "
        "(DFS)' winter 2024/25 review, published 3 July 2025, "
        "https://www.neso.energy/document/363911/download, pp. 17-18."
    )
    notes.append(
        f"REALISED OUTTURN, {_DFS_REALISED_SEASON}: 56 Service Requirements published covering "
        f"{_DFS_REALISED_SP_PUBLISHED} settlement periods, with volume actually procured on "
        f"{_DFS_REALISED_EVENTS_PROCURED} events covering {_DFS_REALISED_SP_PROCURED} settlement "
        f"periods. Average accepted bid £{_DFS_REALISED_AVG_ACCEPTED_GBP_PER_MWH:,.0f}/MWh, range "
        f"£{_DFS_REALISED_MIN_ACCEPTED_GBP_PER_MWH:,.0f}-"
        f"£{_DFS_REALISED_MAX_ACCEPTED_GBP_PER_MWH:,.0f}/MWh. Fleet-aggregate performance "
        "withholding took £943,983 settled from £1,233,312 accepted (K ≈ 0.765) — a FLEET "
        "statistic over a 90%-domestic, 99.58%-manually-instructed population, NOT this asset's "
        "expected K, and NOT to be multiplied into the scenarios above, which already embed "
        "accepted-bid pricing."
    )
    notes.append(
        f"THE £4,559/MWh ANCHOR IS SUPERSEDED AND MUST NOT BE ANNUALISED. Carried at "
        f"{_DFS_ANCHOR_KEY}, it is the average 2022-23 LIVE EVENT price under the PREVIOUS "
        "winter-contingency scheme (Cornwall Insight for National Grid Electricity Distribution, "
        "'Revenue Stacking for Flexibility', December 2023, Figure 4, "
        "https://www.flexiblepower.co.uk/downloads/1150). Annualised over the 236 procured "
        "settlement periods it gives about £538/kW/yr — 25.6× NESO's own full-participation "
        "average and roughly 5× the entire grid-scale GB BESS revenue index. The service repriced "
        "by roughly 20× when it became a merit-based margin tool, so the two are not comparable: "
        "even the single highest accepted bid of the 2024/25 season (£1,290/MWh) is 3.5× below "
        "the old anchor. A DFS figure anywhere near £538/kW/yr at this site size is a DEFECT "
        "SIGNAL, not a result."
    )
    notes.append(
        "WHY THESE SCENARIOS ARE NOT THE £, AND WHAT IS: (i) the engine prices DFS from the "
        "half-hourly dispatch it measures over the called settlement periods of THIS SITE'S OWN "
        "ZONE (valuation._dfs_value, on the 2026/27 zonal outturn), so booking the scenarios as "
        "well would count one pound twice - the one rule D57 does not relax; (ii) the 2024/25 "
        "season these scenarios describe PREDATES the 9 April 2026 zonal, bi-directional redesign, "
        "under which acceptance is per zone against a Zonal Cap that 'may be zero (0) MW', so a "
        "national figure from that season is not a forecast for a zone in this one; (iii) the "
        "DFS-vs-DNO-flex branch selector is still not built, and under D57 limb 3 the stacking "
        "exclusivity is assumed waived and DECLARED rather than enforced. CONFIDENCE CEILING: LOW. "
        "STATUS: needs_review. CONFIRMATION ASK: a full delivery year of the 2026/27 zonal service "
        "broken down by zone, which does not exist yet - the redesign is four months old."
    )
    notes.append(
        "NESO'S OWN CAVEAT, VERBATIM: the scenarios 'have been created to act as a guide and are "
        "not indicative of future year revenue as these are based off the system and market "
        "conditions from the winter 24/25 period.' They also exclude stacked revenue."
    )
    # The anchor key is still READ (it is what the supersession note is about), so it is reported
    # as used — the citation trail must show which library key the correction applies to.
    if _maybe(assumptions, _DFS_ANCHOR_SECTION, _DFS_ANCHOR_NAME) is not None:
        used.append(_DFS_ANCHOR_KEY)
    return notes, used


def dfs_registered_unit_capacity_mw(unit_mw: float | None) -> float | None:
    """``unit_mw`` as a valid DFS Registered Unit Capacity, or ``None`` if it cannot be one.

    Rounded to one decimal place per the register's rule, then bounds-checked against the 0.1-100 MW
    range. Rounding is **down** (truncation at one decimal) so the registered capacity never exceeds
    the asset's real capability.
    """
    if unit_mw is None or unit_mw <= 0:
        return None
    factor = 10.0**_DFS_CAPACITY_DECIMALS
    rounded = int(unit_mw * factor) / factor
    if rounded < _DFS_MIN_UNIT_MW or rounded > _DFS_MAX_UNIT_MW:
        return None
    return rounded


class DemandFlexibilityServiceStream(ContractedStream):
    """NESO's Demand Flexibility Service — the only direct NESO route below 1 MW (E-06).

    **The annual £ is REFUSED, not priced at zero (DFS-2, D45).** Two of the four settlement terms
    — the realised delivery percentage (hence K) and the count of called settlement periods per
    season — are unsourced, and they are exactly the terms that set the annual number. DFS is
    EVENT-DRIVEN, so annualising it from continuous availability would overstate it by orders of
    magnitude. The gate therefore publishes the per-called-period unit economics and an explicit
    refusal, so the zero can never be read as "this stream earns nothing". See
    :func:`dfs_unit_economics_notes`.

    Gates on the things DFS actually requires:

    - **Registered Unit Capacity 0.1-100 MW**, to one decimal place. Far below the 1 MW minimum that
      bars a sub-1 MW asset from the BM and from all six DC/DM/DR products.
    - **All Unit Meter Points in exactly ONE DFS Zone** — a DFS Unit may aggregate meter points only
      where they share a zone.
    - **A baseline methodology** — the settlement volume V is measured against a baseline, so the
      choice of methodology is a settlement input, not a detail.
    - **Directional products**, priced by the register's settlement formula
      ``S = 0.5 x P x Q x K``: utilisation price £/MWh x offered Service Volume MW x the piecewise
      performance factor K, with 0.5 converting a 30-minute settlement period to MWh.

    It also carries the **exclusivity** rule, which is the reason this stream and the streams D-92
    removes cannot both be earned: DFS Procurement Rules 4.7.6 bars a participating meter point from
    forming part of a BM Unit other than a Supplier Base BMU, and 4.7.9 bars its registration for
    other Balancing Services or DNO Flexibility Services.
    """

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.DFS,
            display_name="Demand Flexibility Service (NESO)",
            required_inputs=[
                RequiredInput(
                    key="dfs.registered_unit_capacity_mw",
                    description="Registered Unit Capacity of the DFS Unit: 0.1-100 MW, to one "
                    "decimal place.",
                ),
                RequiredInput(
                    key="dfs.zone",
                    description="The single DFS Zone all of the unit's meter points sit in — a DFS "
                    "Unit may aggregate meter points only within one zone.",
                ),
                RequiredInput(
                    key="dfs.baseline_methodology",
                    description="The baseline the settlement volume is measured against (e.g. the "
                    "Elexon default BL01 lookback, or a supplier/aggregator alternative).",
                ),
                RequiredInput(
                    key="dfs.utilisation_price_gbp_per_mwh",
                    description="Utilisation Price from the accepted DFS Bid (£/MWh).",
                ),
            ],
            constraints=[_DFS_SERVICE_WINDOW],
            assumption_keys=[],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        # Size eligibility uses the site's connection bound — the same sizing-independent upper
        # bound the BM/response gates use. Unlike those, DFS's floor is 0.1 MW, so a small C&I
        # connection clears it: that is exactly why this stream exists.
        bound_mw = gates.connection_bounded_response_mw(site_model)
        capacity = dfs_registered_unit_capacity_mw(bound_mw)
        if bound_mw is not None and capacity is None:
            # DFS-5 (Olly, 2026-08-17): SURFACE THE INTERPRETATION, do not bury it. The exclusion
            # below rests on reading NESO's "rounded to one decimal place" as round-DOWN
            # (`dfs_registered_unit_capacity_mw`). Nearest-rounding would take 0.093 MW to 0.1 MW
            # and CLEAR the bar. The conservative reading is kept as the default — a registered
            # capacity must never exceed the asset's real capability — but it is an INTERPRETATION
            # of the rule, not the rule, and it is the only thing excluding this site.
            nearest = round(bound_mw, _DFS_CAPACITY_DECIMALS)
            margin_pct = (
                (_DFS_MIN_UNIT_MW - bound_mw) / _DFS_MIN_UNIT_MW * 100.0
                if bound_mw < _DFS_MIN_UNIT_MW
                else 0.0
            )
            warnings = [
                f"DFS excluded: the site's connection presents {bound_mw:,.3f} MW, which "
                f"cannot round to a valid Registered Unit Capacity "
                f"({_DFS_MIN_UNIT_MW} MW to {_DFS_MAX_UNIT_MW} MW, one decimal place).",
                "THIS EXCLUSION IS AN INTERPRETATION, NOT A BARE RULE. NESO requires the "
                "Registered Unit Capacity to be 'an amount (if not an integer, rounded to one "
                "decimal place) from and including zero point one (0.1) MW' (Procurement Rules "
                "v5.0, Schedule 1 and para 4.2). GridQ reads 'rounded' as round-DOWN so the "
                f"registered capacity can never exceed real capability: {bound_mw:,.3f} MW "
                f"truncates to 0.0 MW and fails. NEAREST-rounding would give {nearest:,.1f} MW "
                "and CLEAR the bar. The conservative reading is deliberately kept — but a reader "
                "must know the exclusion turns on it.",
            ]
            if margin_pct > 0:
                warnings.append(
                    f"MARGIN TO THE BOUNDARY: {bound_mw:,.3f} MW is {margin_pct:,.1f}% below the "
                    f"{_DFS_MIN_UNIT_MW} MW floor. Any ONE of these clears it: an uplift to the "
                    "site's MIC/ASC; a second co-located meter point in the SAME DFS Zone (a DFS "
                    "Unit may aggregate meter points within one zone); or aggregation into a "
                    "larger DFS Unit via a route-to-market partner. This is a structuring "
                    "question with a live answer, not a permanent bar."
                )
            # D57 items 4 and 8 (Olly, 2026-08-18): THE 0.1 MW REGISTERED-UNIT FLOOR IS ASSUMED
            # SATISFIED. It is a product minimum-size threshold, named in D57 limb 3 among the
            # constraints that "are all ASSUMED SATISFIED and must not reduce a figure", and limb 5b
            # names being "below a statutory or product minimum size (the 1 MW registered unit, the
            # 0.1 MW DFS RUC)" as something that may not zero, reduce, defer or hold a stream
            # conditional. Every campaign site is sub-MW and two are sub-0.1 MW, so this branch was
            # CLOSING the only direct NESO route open to them. The whole finding above - including
            # the round-down interpretation and the margin to the boundary - is kept and published;
            # only the gate status changes.
            warnings.append(
                "DFS IS PRICED ON A D57 ASSUMPTION, NOT ON A CLEARED REGISTERED UNIT CAPACITY. "
                f"This site's connection presents {bound_mw:,.3f} MW, below the "
                f"{_DFS_MIN_UNIT_MW} MW Registered Unit Capacity floor on GridQ's round-DOWN "
                "reading of the rule. Under D57 limb 3 that product minimum is ASSUMED SATISFIED "
                "and limb 5b forbids it zeroing, reducing, deferring or conditioning the stream, "
                "so DFS is OPEN and priced in full. WHAT IS ASSUMED: registration of a DFS Unit "
                "that clears the floor - by aggregating this meter point with others in the SAME "
                "DFS Zone, by a route-to-market partner's larger DFS Unit, or by an MIC/ASC "
                "uplift. DIRECTION OF EFFECT: UP, from GBP 0 to a priced stream inside the floor, "
                "P50, P90, the DSCR, both IRRs and candidate selection. CONFIRMATION ASK: the DFS "
                "Unit registration, its Registered Unit Capacity and its zone. NOT ASSERTED "
                "(D45): no registration is claimed to exist and no aggregator is asserted to have "
                "agreed one."
            )
            warnings.extend(self._d57_stacking_waiver_notes())
            return self._gate(
                GateStatus.OPEN,
                warnings=warnings,
                consent_status=consent_status,
            )
        # D57: these are the things DFS requires and this site does not hold. They are no longer a
        # `missing_requirements` list on a CONDITIONAL gate - the gate is OPEN and they are the
        # CONFIRMATION ASK published on it - so they are written into the warnings instead.
        warnings = [
            "DFS REQUIREMENTS NOT EVIDENCED ON THIS SITE, EACH ASSUMED APPROVED UNDER D57 limb 2: "
            "the single DFS Zone containing every one of the unit's meter points; the baseline "
            "methodology the settlement volume is measured against; and an accepted DFS Bid with "
            "its Utilisation Price (GBP/MWh) and Service Volume (MW).",
            "DFS is the ONLY direct NESO revenue route below 1 MW: its minimum Registered Unit "
            f"Capacity is {_DFS_MIN_UNIT_MW} MW, against the 1 MW minimum that bars a sub-1 MW "
            "asset from the Balancing Mechanism, Balancing Reserve and all six DC/DM/DR products.",
            "EXCLUSIVITY — ONE HARD BAR AND ONE CONDITIONAL BAR, AND THEY MUST NOT BE CONFLATED. "
            "HARD (Procurement Rules 4.7.6): a participating meter point may NOT form part of a BM "
            "Unit other than a Supplier Base BMU. CONDITIONAL (4.7.9): registration for other "
            "Balancing Services or DNO Flexibility Services is barred ONLY WHERE THE DFS STACKING "
            "LIST PROHIBITS IT. NESO's own winter 2024/25 review records that stacking WAS "
            "facilitated with 'all the core DNO Flexibility Services in addition to the Capacity "
            "Market (CM), Local Constraints Market (LCM)', with 10 providers stacking MPANs across "
            "CM (11,936) and LCM (3,114). DATA ASK: the current DFS Stacking List — it is the "
            "GOVERNING DOCUMENT for stacking and is not held in this repo.",
            "GridQ nonetheless models DFS and DNO local flex as ALTERNATIVES and never sums them, "
            "pending that Stacking List. That is a deliberately CONSERVATIVE MODELLING CHOICE, not "
            "a statement that the rules forbid stacking — and it UNDERSTATES the stack if the "
            "current list permits the pair. Stated so a reader is never left thinking the "
            "exclusion was read off the rules.",
            "EXCLUSIVITY DOES NOT EXTEND TO THE CAPACITY MARKET: Procurement Rules 4.7.8(a) "
            "EXPRESSLY PERMITS a CM agreement alongside DFS unless the DFS Stacking List says "
            "otherwise, and the import-side floor streams (bill savings, tariff arbitrage, DUoS "
            "red-band avoidance, MIC/ASC headroom) are behind-the-meter bill mechanics that no "
            "NESO stacking rule touches. Overstating the exclusive set understates the stack.",
            "EXCLUSIVITY IS PER METER POINT, NOT PER SITE. A site with two MPANs could in "
            "principle run DFS on one and DNO flexibility on the other. DATA ASK: how many MPANs "
            "does this site have, and which DFS Zone is each in?",
            "REGISTRATION REQUIREMENT (Procurement Rules 4.7.2 / 4.7.7): an I&C Boundary Meter "
            "must be HALF-HOURLY SETTLED unless it is Profile Class 3 or 4, and metering must meet "
            "Code of Practice 11 accuracy class. DATA ASK: confirm this meter point's settlement "
            "class and metering CoP. A private-wire or sub-metered arrangement may not satisfy it, "
            "which would make DFS unavailable here for a METERING reason independent of size.",
            # DFS-3 (Olly, 2026-08-17): both asks below used to assert the library carried NOTHING.
            # That was FALSE against gb_v1_10 and is the key-path-mismatch defect class — a stream
            # declaring a data ask for a value the release already holds, because the path it looks
            # at and the path the value lives at are different. Re-pointed at the real key paths.
            f"SOURCED, NOT MISSING — the DFS price anchor lives at {_DFS_ANCHOR_KEY} (see the "
            "PRICE VINTAGE note for its value, vintage and the reason it is not a forward price). "
            "What remains a DATA ASK is a CURRENT cleared utilisation price: DFS prices come from "
            "accepted bids in a specific Service Requirement, and clearing is per zone against a "
            "Zonal Cap that 'may be zero (0) MW', so no library constant can stand in for one.",
            f"SOURCED, NOT MISSING — the DFS Zone register lives at {_DFS_ZONE_KEY}: 12 zones, "
            "identified by NUMBER ONLY (each GeoJSON feature carries the single property "
            "{'Region': 1..12}; no zone NAMES are published, so a name can never be quoted), "
            "allocated by point-in-polygon of the site's location, geojson_sha256 "
            "ed3fa1cc89484faff47ff90fdb89c74d0fa860ff9818e729d25929b993daef88 (NESO, 'DFS 12 "
            "Zones GeoJson Map', https://www.neso.energy/document/376656/download). What remains "
            "a DATA ASK is THIS SITE'S zone: allocation needs a full address or postcode, and the "
            "site record carries district-level detail only.",
            "DATA ASK — the baseline methodology. The two options ARE sourced (P376 BL01 historic: "
            "average of up to ten most recent like Working Days from a 60-day window, excluding "
            "Event Days and clock-change days, no in-day adjustment; OR the Self-Nominated "
            "Baselining Methodology, open to I&C and Generation Asset meter points only). The "
            "CHOICE must come from the DFS registration record. It is MATERIAL for a battery "
            "behind a variable C&I load: the historic baseline is contaminated by the battery's "
            "own prior dispatch, which is exactly why the self-nominated option exists.",
        ]
        if capacity is not None:
            warnings.insert(
                1,
                f"the site's connection could present a Registered Unit Capacity of "
                f"{capacity:,.1f} MW (from {bound_mw:,.3f} MW, truncated to one decimal place as "
                "the DFS rules require), which clears the DFS minimum.",
            )
        # DFS-2 (Olly, 2026-08-17): the stream used to declare these data asks AND price £0.00 into
        # the record — the one outcome D45 forbids ("refuse the £, or derive it, never
        # both-and-neither"). The annual £ is now an EXPLICIT REFUSAL carrying the per-event unit
        # economics, so the zero can no longer be read as "this stream earns nothing".
        # `_` on the keys: `GateResult` has no assumptions_used channel and `valuation._result`
        # takes that list from the pricing branch, which DFS does not have (see the DFS-1
        # follow-up). The key paths are named inline in the notes instead, so the citation still
        # reaches the reader — never stashed on `self`, which is a shared registry singleton.
        refusal_notes, _refusal_used = dfs_unit_economics_notes(assumptions, capacity)
        warnings.extend(refusal_notes)
        # D57 item 4: the gate OPENS. Every item in `missing` above is a registration, a zone
        # allocation, a baseline election or an accepted bid - each one a contract, consent,
        # registration or award that D57 limb 2 assumes APPROVED for this site and this battery
        # size. They stay on the record as the confirmation ask, which is what limb 4 requires,
        # and the engine now prices the stream from the dispatch it measures over the DFS season's
        # called settlement periods (`optimisation.milp_solver`'s DFS objective term).
        warnings.append(
            "DFS IS PRICED ON D57 ASSUMPTIONS, NOT ON A HELD REGISTRATION. ASSUMED APPROVED for "
            "this site and this battery size: DFS Unit registration; the zone allocation (every "
            "meter point in ONE of the 12 DFS Zones - this site's zone is NOT derived, the record "
            "carries a district-level postcode only); the baseline methodology election (P376 BL01 "
            "or Self-Nominated); and an ACCEPTED DFS Bid in a published Service Requirement, "
            "including that the zone's Zonal Cap - which 'may be zero (0) MW' - leaves room for "
            "it. CONFIRMATION ASK: all four, plus this site's DFS zone. DIRECTION OF EFFECT: UP, "
            "from GBP 0 to a priced stream inside the floor, P50, P90, the DSCR and both IRRs. "
            "NOT ASSERTED (D45): nothing here claims a registration exists or that NESO has "
            "accepted a bid."
        )
        warnings.extend(self._d57_stacking_waiver_notes())
        return self._gate(
            GateStatus.OPEN,
            warnings=warnings,
            consent_status=consent_status,
        )

    @staticmethod
    def _d57_stacking_waiver_notes() -> list[str]:
        """The DFS exclusivity waiver D57 requires be STATED rather than enforced.

        Separate from the shared-power-budget waiver `value_stack.capacity_budget` states, because
        they are DIFFERENT rules: that one is the physics of one inverter, this one is the DFS
        Stacking List and Procurement Rules 4.7.6 / 4.7.9. A reader told about one and not the other
        would think the stack had been checked against the rules.
        """
        return [
            "DFS STACKING EXCLUSIVITY IS ASSUMED WAIVED (D57 limb 3), NOT CHECKED. Procurement "
            "Rules 4.7.6 bars a DFS Unit Meter Point from forming part of a BM Unit other than a "
            "Supplier Base BM Unit - an UNCONDITIONAL bar - and 4.7.9 bars registration for other "
            "Balancing Services or DNO Flexibility Services WHERE THE DFS STACKING LIST SO "
            "PROVIDES. This run prices DFS ALONGSIDE the Balancing Mechanism, DC/DM/DR response "
            "and DNO local flexibility on the same meter point, which 4.7.6 forbids outright on "
            "the BM leg. D57 limb 3 assumes every such rule satisfied and forbids it reducing a "
            "figure, so the pair is priced and the waiver is stated here instead. WHAT WOULD "
            "SETTLE IT: the current DFS Stacking List, which is the governing document and is not "
            "held in this repo, plus how many MPANs this site has (exclusivity is PER METER POINT, "
            "so a two-MPAN site could genuinely run DFS on one and DNO flex on the other). "
            "DIRECTION OF EFFECT: UP - at the central DFS case this is roughly GBP 7.56/kW/yr of "
            "DFS priced alongside GBP 18.41/kW/yr of BM plus GBP 20.75-46.02/kW/yr of response, "
            "where the rules as written would make the model choose. A CAPACITY MARKET agreement "
            "needs no waiver: Rules 4.7.8(a) expressly permits it alongside DFS.",
        ]

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        """DFS settlement: ``S = 0.5 x P x Q x K`` summed over contracted settlement periods.

        THE COARSE-SWEEP RANKING PATH. The engine path (`optimisation.valuation._dfs_value`) prices
        the selected candidate from the dispatch it measures; this path exists so the coarse sweep
        does not rank every candidate as though DFS earned nothing. The two are ALTERNATIVES and are
        never summed.
        """
        rate = _consent_agreed_rate(site_model, MarketStream.DFS)
        capacity = dfs_registered_unit_capacity_mw(dispatch.power_mw)
        notes = [
            "DFS settlement value per contracted settlement period is S = 0.5 x P x Q x K "
            "(utilisation price £/MWh x offered Service Volume MW x performance factor K; the "
            "0.5 converts a 30-minute period to MWh). Payment is on the OFFERED volume Q scaled "
            "by K, so under-delivery is penalised through K rather than through a reduced Q.",
            "K is piecewise and is NOT clipped at zero: 0-25% realised delivery earns exactly "
            "zero, delivery above 120% is capped at K = 1.2, and NEGATIVE realised delivery "
            "makes K negative down to -1 - the provider PAYS. Clipping K at zero would delete a "
            "real liability.",
        ]
        if capacity is None:
            # D57 item 8: the 0.1 MW Registered Unit Capacity floor is ASSUMED SATISFIED, so a
            # sub-0.1 MW unit is priced on its OWN MW rather than refused. The gate already
            # publishes what is being assumed (registration of a DFS Unit that clears the floor, by
            # aggregating meter points in one zone or via a route-to-market partner); refusing the £
            # here would re-impose the threshold the gate just assumed away.
            notes.append(
                f"a dispatched {dispatch.power_mw:,.3f} MW cannot round to a valid Registered "
                f"Unit Capacity ({_DFS_MIN_UNIT_MW}-{_DFS_MAX_UNIT_MW} MW, one decimal place) on "
                "the round-DOWN reading of the rule. Under D57 limb 3 that product minimum is "
                "ASSUMED SATISFIED and limb 5b forbids it reducing the figure, so the stream is "
                "priced on the unit's own MW. CONFIRMATION ASK: the DFS Unit registration and "
                "its Registered Unit Capacity."
            )
        else:
            # THE ROUNDING RULE IS STATED AND NOT APPLIED TO THE VOLUME (2026-08-19). This branch
            # used to price the TRUNCATED capacity, so a 0.25 MW unit was paid on 0.2 MW and a
            # 0.15 MW unit on 0.1 MW - a 20-33% haircut delivered by a registration formality. D57
            # limb 3 forbids a statutory or product threshold reducing a figure and limb 5b says so
            # again; the truncation is reported as the registration fact it is, and the volume
            # priced below is the unit's own MW. DIRECTION OF EFFECT: UP, by
            # power / truncated capacity, on any unit above 0.1 MW that is not an exact multiple of
            # 0.1 MW; exactly zero on the campaign's sub-0.1 MW systems, which take the branch
            # above.
            notes.append(
                f"Registered Unit Capacity {capacity:,.1f} MW (dispatched "
                f"{dispatch.power_mw:,.3f} MW truncated to one decimal place per the register's "
                "rounding rule), within the 0.1-100 MW range. THE TRUNCATION IS NOT APPLIED TO "
                f"THE £: the volume priced is the unit's own {dispatch.power_mw:,.3f} MW, because "
                "D57 limb 3 forbids a product threshold or rounding rule reducing a figure. "
                "CONFIRMATION ASK: the registered capacity actually declared for this unit."
            )
        if rate is None:
            # D57 item 4: the £ no longer depends on a customer-agreed rate. What prices this stream
            # is the MEASURED zonal outturn in NESO's own 2026/27 utilisation report, which is a
            # published market result and needs no consent to be usable (limb 5b). The data ask is
            # unchanged and still published - it is now an ask, not a refusal.
            notes.append(
                "No CUSTOMER-AGREED DFS Utilisation Price is recorded, and none is needed: the £ "
                "below is the measured outturn for this site's own DFS zone in NESO's published "
                "utilisation report, not a customer rate. DATA ASK, unchanged: the accepted DFS "
                "Bid's Utilisation Price (£/MWh) and Service Volume (MW), plus the contracted "
                "settlement periods actually called, so the figure becomes this unit's own "
                "rather than its zone's."
            )
        else:
            notes.append(
                f"a customer-agreed DFS utilisation rate of £{rate:,.2f}/MWh is recorded; it is "
                "NOT what this path prices, because a customer-agreed £/MWh needs a "
                "called-period count to become an annual figure and the site record carries "
                "none. What is priced is this site's own zone's measured accepted price over its "
                "own measured called periods."
            )
        return self._proxy_priced(dispatch, site_model, assumptions, notes)

    @staticmethod
    def _proxy_priced(
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        notes: list[str],
    ) -> PricedValue:
        """The PROXY path's DFS £/yr: this zone's own periods and price, bounded by THIS asset.

        **Why this exists (D57 item 4, 2026-08-18).** This path returned £0 with a refusal, which
        was right while nothing anywhere could price DFS. It is wrong now: the ENGINE path prices
        DFS from the dispatch it measures over the called settlement periods, and the two paths are
        used at DIFFERENT STAGES of one search - the proxy ranks candidates on the coarse sweep, the
        engine prices the fine and confirm stages. A £0 here does not understate a report, it
        distorts the SEARCH: every coarse candidate is ranked as though DFS earned nothing, so the
        sizing loop can discard the size that would have won on the full stack.

        **THE SIZE FUNCTION, and why a flat £/kW/yr was the wrong shape (2026-08-19).** DFS pays on
        the OFFERED Service Volume, so what the asset can offer in a called half-hour is what it can
        earn, and that is bounded TWICE: by rated POWER within any one half-hour, and by usable
        ENERGY across the called half-hours of one evening. A flat per-kW rate captures only the
        first, so it paid a 0.5 h battery the same £/kW as a 4 h one over a window that runs five to
        eight half-hours - the very error that makes a coarse sweep prefer a duration the engine
        then prices far lower. What is computed instead, per event day, is
        ``min(periods_per_event_day x 0.5 h x rated kW, usable kWh)``, and the annual figure is that
        times the zone's own count of event days. Every term is measured: the periods, the days and
        the price come from the zone's own outturn, and the usable window from the library's own
        SoC bounds.

        **The two paths cannot double-book, by construction:** they are alternatives, never summed.
        A candidate is priced by the proxy OR by the engine, and the selected candidate is always an
        engine-priced one.
        """
        zone, _resolved, zone_notes = dfs_zone_for_site(site_model)
        observation, used, basis_notes = dfs_zone_basis(assumptions, zone=zone)
        rate_gbp_per_kwh = observation.utilisation_price_gbp_per_mwh / 1000.0
        power_kw = dispatch.power_mw * 1000.0
        nameplate_kwh = power_kw * dispatch.duration_h
        soc_min = _num(_maybe(assumptions, "warranty_cycle_limits", "soc_min_fraction"))
        soc_max = _num(_maybe(assumptions, "warranty_cycle_limits", "soc_max_fraction"))
        soc_keys: list[str] = []
        if soc_min is not None and soc_max is not None and soc_max > soc_min:
            usable_fraction = soc_max - soc_min
            soc_keys = [
                "warranty_cycle_limits.soc_min_fraction",
                "warranty_cycle_limits.soc_max_fraction",
            ]
            soc_label = (
                f"the library's own operating SoC window ({soc_min:g} to {soc_max:g}, "
                f"{usable_fraction:.0%} usable)"
            )
        else:
            # NO INVENTED HAIRCUT: with no sourced SoC window the nameplate energy is used and the
            # note says so. A missing key never becomes a silent derate any more than it becomes a
            # silent zero.
            usable_fraction = 1.0
            soc_label = (
                "NAMEPLATE energy, because this release carries no warranty_cycle_limits SoC "
                "window to bound it with (no haircut is invented in its place)"
            )
        usable_kwh = nameplate_kwh * usable_fraction
        per_day_power_kwh = observation.periods_per_event_day * 0.5 * power_kw
        per_day_kwh = min(per_day_power_kwh, usable_kwh)
        annual_kwh = per_day_kwh * observation.event_days
        revenue = round(rate_gbp_per_kwh * annual_kwh, 2)
        # DERIVED FROM THE VOLUME ACTUALLY USED, not from a re-comparison of the two bounds, so the
        # label cannot describe a bound the arithmetic did not apply.
        binding = "ENERGY" if per_day_kwh < per_day_power_kwh else "POWER"
        low, high = _DFS_ZONAL_RANGE_GBP_PER_KW_YR
        notes.extend(zone_notes)
        notes.extend(basis_notes)
        notes.append(
            f"PROXY PATH £{revenue:,.2f}/yr, AND IT IS A FUNCTION OF BOTH SIZE AND LOCATION. = "
            f"GBP {observation.utilisation_price_gbp_per_mwh:,.3f}/MWh (zone {zone}'s own "
            f"volume-weighted accepted price) x {observation.event_days} event days x "
            f"min({observation.periods_per_event_day:,.2f} periods/day x 0.5 h x {power_kw:,.3f} "
            f"kW, {usable_kwh:,.3f} kWh usable) = {annual_kwh:,.3f} kWh/yr, i.e. GBP "
            f"{revenue / power_kw if power_kw else 0:,.2f}/kW/yr. THE {binding} BOUND BINDS here: "
            f"rated power allows {per_day_power_kwh:,.3f} kWh across an average event day's "
            f"called periods and the pack holds {usable_kwh:,.3f} kWh, on {nameplate_kwh:,.3f} "
            f"kWh nameplate at {dispatch.duration_h:,.2f} h and {soc_label}. ZONE {zone}'s OWN "
            f"OUTTURN, measured: {observation.accepted_settlement_periods} accepted settlement "
            f"periods over {observation.event_days} delivery days in {_DFS_SPAN_DAYS} days "
            "(2026-04-14 to 2026-08-18), NOT annualised by 365/127 - the observed count is used "
            "as the annual count and the direction of that choice is DOWN. Zonal range GBP "
            f"{low:,.2f} to GBP {high:,.2f}/kW/yr at full delivery, median GBP "
            f"{_DFS_MEDIAN_ZONE_GROSS_GBP_PER_KW_YR:,.2f}. THIS IS THE COARSE-SWEEP RANKING PATH "
            "ONLY: the selected candidate's £ is always the ENGINE's, measured from the dispatch "
            "over the called periods, and the two are alternatives that are never summed."
        )
        return PricedValue(revenue, 0.0, tuple(notes), tuple(used + soc_keys))


class LoadServiceStream(ContractedStream):
    """Power provisioning (issue 017 / D42) — the battery as load-serving capacity.

    The obligation is compiled into the dispatch as physics (`SiteModel.
    provisioning_requirements` → the solver's forced draw), so delivery is in-model by
    construction. The VALUE is the customer's declared counterfactual (reinforcement quote /
    genset / lost production, basis verbatim) — never an engine estimate, and never priced
    as import displacement (serving load the wire could not deliver displaces nothing).
    Contracted when a requirement carries its customer contract ``reference``; a requirement
    without one is a modelling input (W7-4) and stays conditional, out of the priced floor.
    """

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.LOAD_SERVICE,
            display_name="Power provisioning (load service)",
            required_inputs=[
                RequiredInput(
                    key="provisioning_requirements",
                    description="A declared load-service obligation (windows + declared "
                    "addition or metered-shortfall flag) on the site.",
                ),
                RequiredInput(
                    key="provisioning_requirements[].reference",
                    description="The customer contract reference — a requirement without "
                    "one is a modelling input, not evidence (W7-4).",
                ),
                RequiredInput(
                    key="provisioning_requirements[].declared_counterfactual_gbp_per_year",
                    description="The customer-declared annual cost of the alternative "
                    "(with its basis verbatim) — the only £ input; never estimated.",
                ),
            ],
            constraints=[],  # compiled from the site directly (issue 017), not the seam
            assumption_keys=[],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        requirements = site_model.provisioning_requirements or []
        if not requirements:
            return self._gate(
                GateStatus.CLOSED,
                missing_requirements=["a declared load-service obligation on the site"],
                warnings=["load service excluded: the site declares no obligation."],
                consent_status=consent_status,
            )
        if not any(r.reference for r in requirements):
            return self._gate(
                GateStatus.CONDITIONAL,
                missing_requirements=[
                    "the customer contract reference on the load-service requirement"
                ],
                unlock_condition="the signed customer provisioning contract, referenced on "
                "the requirement",
                warnings=[
                    "load service not priced into the floor: the declared requirement carries "
                    "no contract reference — a modelling input, not evidence (W7-4)."
                ],
                consent_status=consent_status,
            )
        return self._gate(GateStatus.OPEN, consent_status=consent_status)

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        requirements = site_model.provisioning_requirements or []
        revenue = 0.0
        notes: list[str] = []
        for req in requirements:
            if req.declared_counterfactual_gbp_per_year is None:
                notes.append(
                    f"requirement '{req.requirement_id}': no declared counterfactual — £0 "
                    "priced; the engine-measured opportunity cost is reported separately."
                )
                continue
            revenue += req.declared_counterfactual_gbp_per_year
            notes.append(
                f"requirement '{req.requirement_id}': customer-declared counterfactual "
                f"£{req.declared_counterfactual_gbp_per_year:,.0f}/yr (basis: "
                f"{req.counterfactual_basis})."
            )
        served = dispatch.load_service_energy_mwh
        if served is not None:
            notes.append(
                f"measured forced delivery {served} MWh/yr (in-model evidence; never priced "
                "as import displacement)."
            )
        if gate.gate_status == GateStatus.CONDITIONAL:
            notes.append(
                "CONDITIONAL — out of the priced floor until the customer contract is "
                "referenced on the requirement."
            )
        return PricedValue(revenue, 0.0, tuple(notes), ())


# ---------------------------------------------------------------------------
# Gated upside — never in the priced floor; always conditional, never firm
# ---------------------------------------------------------------------------


class WholesaleP415Stream(GatedUpsideStream):
    """Wholesale + BM via P415 — gated upside; RTM partner + export consents to unlock."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.WHOLESALE_P415,
            display_name="Wholesale + BM via P415",
            required_inputs=[
                RequiredInput(
                    key="route_to_market.partner",
                    description="A confirmed route-to-market (RTM) partner / API (BLOCKERS B002).",
                ),
                RequiredInput(
                    key="consents.g99_g100",
                    description="Approved export consents (G99/G100) + export capability (MEC>0).",
                ),
            ],
            constraints=[_EXPORT_LIMIT],
            assumption_keys=["wholesale_p415"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        # Consent authorises the contracted case; wholesale export also physically needs the
        # export-dispatch consents to cohere (MEC>0 ∧ G99/G100 approved ∧ export MPAN). With
        # both, the gate opens to CONTRACTED (never FIRM). Absent consent it stays CONDITIONAL,
        # byte-identical to the pre-D43 behaviour (plus the orthogonal consent_status label).
        consent_status = _consent_status(site_model, self.stream_id)
        if consent_status is not None and gates.export_dispatch_enabled(site_model):
            return self._gate(GateStatus.OPEN, consent_status=consent_status)
        rtm = _maybe(assumptions, "wholesale_p415", "gated_upside_value_gbp_per_mw_yr")
        missing: list[str] = []
        if not gates.route_to_market_available(rtm):
            missing.append("a confirmed route-to-market (RTM) partner")
        if not gates.has_export_consent(site_model):
            missing.append("approved export consents (G99/G100) and export capability (MEC>0)")
        return self._gate(
            GateStatus.CONDITIONAL,
            missing_requirements=missing,
            unlock_condition="route-to-market partner + export consents (G99/G100)",
            warnings=[
                "wholesale/P415 is gated upside — never in the priced floor; conditional until "
                "the RTM partner and export consents are in place."
            ],
            consent_status=consent_status,
        )

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        # WHOLESALE's agreed rate is a £/kWh export tariff (not £/MW/yr): the contracted mode
        # (agreed tariff × exported MWh) needs an engine-measured export volume the proxy does not
        # carry, so the proxy stays a £0 placeholder here and the engine path prices the contracted
        # value from the export overlay (D43). Consent is still noted.
        note = (
            "GATED upside — £0 until a confirmed route-to-market partner value lands (BLOCKERS "
            "B002); never in the priced floor. Avoids double-counting tariff optimisation via "
            "deviation-volume baselining."
        )
        if gates.stream_consented(site_model, MarketStream.WHOLESALE_P415):
            note += (
                " Consent given: the contracted export-tariff value (agreed £/kWh × exported MWh) "
                "is priced only on the engine path, which measures the export volume."
            )
        return _priced_per_mw_yr(
            "wholesale_p415",
            "gated_upside_value_gbp_per_mw_yr",
            dispatch,
            assumptions,
            scenario,
            extra_note=note,
        )


class _SizeGatedUpsideStream(GatedUpsideStream):
    """A gated-upside stream whose market imposes a MINIMUM UNIT SIZE to enter (D-92).

    Holds the shared size-eligibility gate for the Balancing Mechanism and the DC/DM/DR response
    products, so the one predicate and the one set of warnings serve both. Applied *before* the
    consent shortcut in each stream's ``check_gates``: consent authorises pursuit, it can never
    manufacture eligibility.
    """

    def _eligibility_notes(self, site_model: SiteModel, context: GateContext | None) -> list[str]:
        """The eligibility working for a gate that DID open — route, caveats, and any data ask."""
        verdict = market_entry_verdict(
            self.stream_id,
            unit_mw=gates.connection_bounded_response_mw(site_model),
            portfolio_mw=context.portfolio_mw if context is not None else None,
        )
        notes = list(verdict.reasons) + aggregation_route_notes(verdict, self.stream_id)
        if verdict.route == "not_armed":
            notes.append(ELIGIBILITY_DATA_ASK)
        if verdict.route == "standalone":
            notes.append(
                "Eligibility here rests on the site's CONNECTION capacity (an upper bound on any "
                "response it could present), not on the sized battery — the engine path re-tests "
                "the same threshold against the solved power, which is the binding test."
            )
        return notes

    def _sellable_mw(
        self, dispatch: DispatchSummary, context: GateContext | None
    ) -> tuple[float | None, list[str]]:
        """The MW this stream may actually SELL at the dispatched size — ``None`` ⇒ ineligible.

        The money-side half of D-92, and the one the proxy path relies on: ``check_gates`` cannot
        see the sized battery, but ``_price`` can, so the same predicate is re-run here against
        the solved power. An ineligible unit returns ``None`` and the caller prices **£0** — not a
        smaller number, and not a conditional-but-nonzero one, because a market the asset cannot
        enter is worth nothing rather than worth less.

        Delegates to :func:`sellable_volume_mw` so the volume basis has exactly one definition,
        shared with the engine path rather than restated here (N-2).
        """
        mw, notes = sellable_volume_mw(
            self.stream_id,
            unit_mw=dispatch.power_mw,
            portfolio_mw=context.portfolio_mw if context is not None else None,
        )
        if mw is None:
            notes.append(
                f"£0: {self.display_name} priced at zero because the asset cannot enter this "
                "market at its dispatched size. CONSENT DOES NOT CREATE ELIGIBILITY."
            )
        return mw, notes

    def _ineligibility_gate(
        self,
        site_model: SiteModel,
        context: GateContext | None,
        consent_status: str | None,
    ) -> GateResult | None:
        """A CONDITIONAL gate when this site can never enter the market, else ``None`` (D-92).

        Uses the site's own generous connection bound, so a refusal means *no* unit on this
        connection could clear the market's minimum — never a wrongly-blocked site that could
        qualify with a bigger battery.
        """
        threshold = eligibility_threshold_mw(self.stream_id)
        if threshold is None:
            return None  # no armed gate; the data ask is carried on the open gate's notes
        bound_mw = gates.connection_bounded_response_mw(site_model)
        portfolio_mw = context.portfolio_mw if context is not None else None
        verdict = market_entry_verdict(self.stream_id, unit_mw=bound_mw, portfolio_mw=portfolio_mw)
        # Only a PROVEN shortfall blocks. 'unproven' (no capacity recorded at all) does not — the
        # engine path holds the authoritative size and enforces it there, so an unknown size never
        # silently deletes a stream at gate time.
        if verdict.route != "below_threshold":
            return None
        warnings = [
            f"{self.display_name} is NOT ELIGIBLE for this site: the market requires a minimum "
            f"{threshold:,.3f} MW unit and the site's whole connection presents at most "
            f"{bound_mw:,.3f} MW (max of MIC and MEC), so no battery on this connection can enter "
            "it standalone. CONSENT DOES NOT CREATE ELIGIBILITY — a customer's authorisation to "
            "pursue a market is not a right to participate in it.",
            *verdict.reasons,
        ]
        if consent_status is not None:
            warnings.append(
                "The customer HAS consented to this stream; the consent is recorded and the "
                "stream stays visible as conditional upside, but it earns £0 until an eligible "
                "route exists. Consent authorises pursuit, not participation."
            )
        return self._gate(
            GateStatus.CONDITIONAL,
            missing_requirements=[
                f"a unit of at least {threshold:,.3f} MW, or aggregation into a portfolio of at "
                f"least {threshold:,.3f} MW within the SAME GSP Group"
            ],
            unlock_condition=(
                f"a registered unit of at least {threshold:,.3f} MW, or aggregation to "
                f"{threshold:,.3f} MW within one GSP Group via a route-to-market partner "
                "(whose cut must then be deducted)"
            ),
            warnings=warnings,
            consent_status=consent_status,
        )


class BalancingMechanismStream(_SizeGatedUpsideStream):
    """Balancing Mechanism — gated upside; RTM partner + aggregated portfolio to unlock."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.BALANCING_MECHANISM,
            display_name="Balancing Mechanism",
            required_inputs=[
                RequiredInput(
                    key="route_to_market.partner",
                    description="A confirmed route-to-market (RTM) partner / API (BLOCKERS B002).",
                ),
                RequiredInput(
                    key="portfolio.aggregated_mw",
                    description="Aggregation into a portfolio meeting the minimum MW threshold. "
                    "A single-site run carries no portfolio aggregate.",
                ),
            ],
            constraints=[_EXPORT_LIMIT],
            assumption_keys=["balancing_mechanism"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        # D-92: the size-eligibility gate is applied BEFORE the consent shortcut. Consent
        # authorises pursuit; it can never manufacture eligibility for a market the unit cannot
        # physically enter. The gate layer cannot see the sized battery (a sizing output), so it
        # tests the site's own generous connection bound — if even max(MIC, MEC) is below the 1 MW
        # BM Unit minimum, no unit on this connection can clear it and no consent changes that.
        # The engine path re-runs the SAME predicate against the solved power (`valuation`), which
        # is the tighter, authoritative test.
        ineligible = self._ineligibility_gate(site_model, context, consent_status)
        if ineligible is not None:
            return ineligible
        if consent_status is not None:
            return self._gate(
                GateStatus.OPEN,
                warnings=self._eligibility_notes(site_model, context),
                consent_status=consent_status,
            )
        rtm = _maybe(assumptions, "balancing_mechanism", "gated_upside_value_gbp_per_mw_yr")
        portfolio_mw = context.portfolio_mw if context is not None else None
        threshold_mw = context.portfolio_threshold_mw if context is not None else None
        missing: list[str] = []
        if not gates.route_to_market_available(rtm):
            missing.append("a confirmed route-to-market (RTM) partner")
        if not gates.meets_portfolio_threshold(portfolio_mw, threshold_mw):
            missing.append("aggregation into a portfolio meeting the minimum MW threshold")
        return self._gate(
            GateStatus.CONDITIONAL,
            missing_requirements=missing,
            unlock_condition="route-to-market partner + minimum aggregated portfolio MW "
            "(+ export consents G99/G100)",
            warnings=[
                "Balancing Mechanism is gated upside — never in the priced floor; conditional "
                "until the RTM partner and aggregated portfolio threshold are in place."
            ],
            consent_status=consent_status,
        )

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        # D-92: the sellable MW is the whole-MW-truncated eligible quantity at the DISPATCHED size,
        # not the nameplate. An ineligible unit is priced £0.
        sellable_mw, elig_notes = self._sellable_mw(dispatch, context)
        if sellable_mw is None:
            return PricedValue(0.0, 0.0, tuple(elig_notes), ())
        return _priced_per_mw_yr(
            "balancing_mechanism",
            "gated_upside_value_gbp_per_mw_yr",
            dispatch,
            assumptions,
            scenario,
            extra_note="GATED upside — £0 until a confirmed RTM partner value + aggregated "
            "portfolio land (BLOCKERS B002); never in the priced floor.",
            agreed_rate=_consent_agreed_rate(site_model, MarketStream.BALANCING_MECHANISM),
            volume_mw=sellable_mw,
            extra_notes=elig_notes,
        )


class AncillaryDcDmDrStream(_SizeGatedUpsideStream):
    """Ancillary DC/DM/DR (with decay) — gated upside; RTM partner + portfolio to unlock."""

    def __init__(self) -> None:
        super().__init__(
            stream_id=MarketStream.ANCILLARY_DC_DM_DR,
            display_name="Ancillary services (DC/DM/DR)",
            required_inputs=[
                RequiredInput(
                    key="route_to_market.partner",
                    description="A confirmed RTM partner + a commercial forecast source for "
                    "decay-curve calibration (BLOCKERS B002).",
                ),
                RequiredInput(
                    key="portfolio.aggregated_mw",
                    description="Aggregation into a portfolio meeting the minimum MW threshold.",
                ),
            ],
            constraints=[_ANCILLARY_AVAILABILITY],
            assumption_keys=["ancillary_services"],
        )

    def check_gates(
        self,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        context: GateContext | None = None,
    ) -> GateResult:
        consent_status = _consent_status(site_model, self.stream_id)
        # D-92: size eligibility BEFORE the consent shortcut — the 1 MW-per-response-unit minimum
        # is "the gate that excludes sub-1 MW BTM assets from all six dynamic response products".
        ineligible = self._ineligibility_gate(site_model, context, consent_status)
        if ineligible is not None:
            return ineligible
        if consent_status is not None:
            return self._gate(
                GateStatus.OPEN,
                warnings=self._eligibility_notes(site_model, context),
                consent_status=consent_status,
            )
        rtm = _maybe(assumptions, "ancillary_services", "dc_dm_dr_value_gbp_per_mw_yr")
        portfolio_mw = context.portfolio_mw if context is not None else None
        threshold_mw = context.portfolio_threshold_mw if context is not None else None
        missing: list[str] = []
        if not gates.route_to_market_available(rtm):
            missing.append("a confirmed route-to-market (RTM) partner + ancillary forecast source")
        if not gates.meets_portfolio_threshold(portfolio_mw, threshold_mw):
            missing.append("aggregation into a portfolio meeting the minimum MW threshold")
        return self._gate(
            GateStatus.CONDITIONAL,
            missing_requirements=missing,
            unlock_condition="route-to-market partner + minimum aggregated portfolio MW "
            "(DC/DM/DR with capability decay)",
            warnings=[
                "Ancillary DC/DM/DR is gated upside — never in the priced floor; conditional "
                "until the RTM partner and aggregated portfolio threshold are in place."
            ],
            consent_status=consent_status,
        )

    def _price(
        self,
        dispatch: DispatchSummary,
        site_model: SiteModel,
        assumptions: AssumptionLibrary,
        scenario: Scenario,
        context: GateContext | None,
        gate: GateResult,
    ) -> PricedValue:
        a = _maybe(assumptions, "ancillary_services", "dc_dm_dr_value_gbp_per_mw_yr")
        val = _num(a)
        key = "ancillary_services.dc_dm_dr_value_gbp_per_mw_yr"
        # D-92: sellable MW at the dispatched size (whole-MW EAC order granularity); £0 if the
        # asset cannot enter the six dynamic-response products at all.
        sellable_mw, elig_notes = self._sellable_mw(dispatch, context)
        if sellable_mw is None:
            return PricedValue(0.0, 0.0, tuple(elig_notes), ())
        # Rate ladder (D43): a customer-agreed £/MW/yr rate wins over the assumption when consented;
        # else the assumption; else £0.
        consented_rate = _consent_agreed_rate(site_model, MarketStream.ANCILLARY_DC_DM_DR)
        if consented_rate is not None:
            rate: float = consented_rate
            used = []
            base_notes = [
                f"ancillary DC/DM/DR priced at the customer-agreed rate £{rate:,.2f}/MW/yr "
                "(consent given), ahead of the assumption (estimation ladder, D43); not bankable "
                "— a commercially-agreed rate, not a market-cleared price.",
            ]
        elif a is None or val is None:
            return PricedValue(0.0, 0.0, (f"not priced: {key} missing or non-numeric.",), ())
        else:
            rate = val
            used = [key]
            base_notes = [
                _placeholder_note("ancillary_services", "dc_dm_dr_value_gbp_per_mw_yr", a),
                "GATED upside (DC/DM/DR with decay) — £0 until a confirmed forecast/RTM value "
                "lands (BLOCKERS B002); never in the priced floor.",
            ]
        fade_a = _maybe(assumptions, "degradation", "annual_capacity_fade_pct")
        fade = _num(fade_a)
        # Capability-decay factor (price erosion over life): a one-year fade proxy. The full
        # decay curve calibrates against the RTM partner's forecast source (B002).
        decay = (1.0 - fade / 100.0) if fade is not None else 1.0
        mult = _price_multiplier(scenario)
        # D-158: the six EAC products at EFA-block granularity, netted by availability F and
        # performance K, on the SELLABLE whole MW — not one flat £/MW/yr on the nameplate.
        eac_revenue, eac_used, eac_notes = eac_response_value(
            rate_gbp_per_mw_yr=rate,
            sellable_mw=sellable_mw,
            assumptions=assumptions,
        )
        used += eac_used
        revenue = eac_revenue * decay * mult
        notes = base_notes + elig_notes + eac_notes
        if fade_a is not None:
            used.append("degradation.annual_capacity_fade_pct")
            notes.append(
                f"capability-decay factor {decay} from degradation fade {fade}%/yr "
                f"(price erosion over life)."
            )
        if mult != 1.0:
            notes.append(f"scenario price multiplier {mult} applied.")
        return PricedValue(revenue, 0.0, tuple(notes), tuple(used))


def gb_value_streams() -> list[FloorStream | ContractedStream | GatedUpsideStream]:
    """Build the twelve GB value streams, in registry order (matches ``MarketStream``)."""
    return [
        PpaOrBillSavingsStream(),
        TariffArbitrageStream(),
        DuosRedBandAvoidanceStream(),
        MicAscHeadroomStream(),
        CapacityMarketStream(),
        LocalFlexPicloStream(),
        LoadServiceStream(),
        WholesaleP415Stream(),
        BalancingMechanismStream(),
        AncillaryDcDmDrStream(),
        # E-06: the only direct NESO route below 1 MW. Registered last so the pre-existing registry
        # order is unchanged and only an append is visible downstream.
        DemandFlexibilityServiceStream(),
        # MICASC-3: the avoided agreed-capacity charge + TCR band. Appended, same discipline as
        # E-06 — the pre-existing registry order is untouched.
        AgreedCapacityReductionStream(),
    ]
