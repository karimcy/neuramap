"""The RVC screen: the PCC voltage change caused by BESS step events (spec A3-03; D-NEW-SITE06-1).

**Three tiers, exact rationals, no square root on the decision path.**

1. *Bound on the fault level alone.* P28 6.3.5 Equation 3 gives
   ``dV/V = (S / Sbase)(cos(phi) R + sin(phi) X)``. Since ``cos(phi) R + sin(phi) X <= Z``, the
   change is at most ``S / S"k`` for any power factor and any X/R (the form of Equation 4). The
   event passes when ``(100 S)^2 <= (L S"k)^2``, with ``S^2 = P^2 + Q^2``.
2. *Equation 3 with the network's X/R.* Asked for only when the bound fails. With X/R = ``a/b``
   and ``Sbase = S"k``, ``dV/V = (P b + Q a) / (S"k sqrt(a^2 + b^2))``; the event passes when
   ``(100 (P b + Q a))^2 <= (L S"k)^2 (a^2 + b^2)``. P and Q are magnitudes, so the estimate is
   the worst over the declared reactive range (conservative: both signs taken at their worst).
3. Otherwise the event **exceeds** the limit and is referred to PSAL.

**The limit** is P28's 3 % change between steady states (Table 4; 6.2 "as a first step";
footnote 14), measured at the PCC. A quasi-static estimate is never judged against the 6, 10 or
12 % time-envelope limits for the maximum change: those fall back to 3 % within 2 s, and only a
time-domain study can show the envelope is met. The estimate is at nominal pre-event voltage:
P28 6.4.1(c) asks for assessment at the statutory limits, which are a network-operator input.

**Data.** ``S"k`` is the *minimum* short-circuit power under normal operating conditions (P28
6.4.1(a)), evidence-backed only. G99's generic 50 MVA study minimum is never used here, and a
maximum (Long Term Development Statement) fault level is not the right quantity.

**Not screened**, each handed on explicitly: transformer energisation and sympathetic inrush
(PSAL EMT), flicker, conformance to the Figure 5 to 7 time envelopes, and interaction with the
network's voltage control.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from gridq.adequacy._common import ExactRational
from gridq.contracts._base import ContractModel, NonEmptyStr, Unknown
from gridq.ops.dossier_model import AskHolder, DossierAsk
from gridq.ramp_ridethrough._common import (
    Cite,
    bess_ask,
    ceil_sqrt,
    cite,
    cite_constant,
    fault_level_ask,
    rational,
    site_ask,
    sorted_asks,
    x_over_r_ask,
)
from gridq.ramp_ridethrough.inputs import BessDeclaration, EvidencedQuantity, StudyInputs
from gridq.ramp_ridethrough.sources import CONSTANTS

__all__ = [
    "CategoryObligation",
    "EventNotComputed",
    "EventResult",
    "NotScreened",
    "RvcScreen",
    "screen_rvc",
]

EventName = Literal["trip_at_full_export", "trip_at_full_import", "unramped_full_reversal"]


class EventResult(ContractModel):
    kind: Literal["event_result"]
    event: EventName
    p_w: int
    q_var: int
    outcome: Literal["pass_bound_sk", "pass_eq3_xr", "exceeds_steady_state_limit"]
    #: The estimate of |dV/V| in %, rounded up to 0.0001 % (display: the verdict is exact).
    estimate_pct_ceil: ExactRational
    limit_pct: ExactRational
    referred_to: Literal["none", "psal_rms"]
    basis: tuple[Cite, ...]


class EventNotComputed(ContractModel):
    kind: Literal["event_not_computed"]
    event: EventName
    reason: NonEmptyStr
    #: The tier-1 estimate when the bound failed and X/R is needed; otherwise unknown.
    bound_estimate_pct_ceil: ExactRational | Unknown
    requests: tuple[DossierAsk, ...]


class CategoryObligation(ContractModel):
    category: Literal["category_2", "category_3"]
    max_events: int
    per: Literal["calendar_month", "three_calendar_months"]
    min_separation_s: int
    basis: tuple[Cite, ...]


class NotScreened(ContractModel):
    item: Literal[
        "transformer_energisation",
        "flicker",
        "time_envelope_conformance",
        "network_voltage_control_interaction",
    ]
    reason: NonEmptyStr
    handed_to: Literal["psal_emt", "psal_rms", "network_operator"]
    basis: tuple[Cite, ...]


class RvcScreen(ContractModel):
    kind: Literal["rvc_screen"]
    v0_basis: Literal["nominal_pre_event_voltage"]
    point_of_evaluation: Literal["pcc"]
    events: tuple[EventResult | EventNotComputed, ...]
    category: Literal["category_2", "category_3"] | Unknown
    obligations: tuple[CategoryObligation, ...]
    not_screened: tuple[NotScreened, ...]
    basis: tuple[Cite, ...]
    requests: tuple[DossierAsk, ...]


def _pct(squared_ratio: Fraction) -> Fraction:
    """ceil(100 sqrt(squared_ratio)) to 4 places."""
    return ceil_sqrt(squared_ratio * 10_000)


def _obligation(cat: Literal["category_2", "category_3"]) -> CategoryObligation:
    if cat == "category_2":
        key, per = "p28.cat2.max_events_per_month", "calendar_month"
    else:
        key, per = "p28.cat3.max_events_per_3_months", "three_calendar_months"
    return CategoryObligation(
        category=cat,
        max_events=int(CONSTANTS[key].value),
        per=per,  # type: ignore[arg-type]
        min_separation_s=int(CONSTANTS["p28.cat23.min_separation_s"].value),
        basis=(cite_constant(key), cite_constant("p28.cat23.min_separation_s")),
    )


_NOT_SCREENED = (
    NotScreened(
        item="transformer_energisation",
        reason=(
            "Magnetising inrush needs transformer saturation data, remanence and point-on-wave "
            "switching: a time-domain study, not a quasi-static estimate"
        ),
        handed_to="psal_emt",
        basis=(cite("P28_ISSUE_2", "6.4.1(b)-(e); 6.4.2"), cite("G99_ISSUE_2", "9.3.3; 9.3.4")),
    ),
    NotScreened(
        item="flicker",
        reason="A BESS under PPC control is not a continuously fluctuating installation; flicker "
        "(Pst/Plt) needs the operating profile and is assessed by study if the DNO asks",
        handed_to="psal_rms",
        basis=(cite("P28_ISSUE_2", "6.3"), cite("G99_ISSUE_2", "9.4.2")),
    ),
    NotScreened(
        item="time_envelope_conformance",
        reason="The maximum-change limits are time envelopes (Figures 5 to 7); a quasi-static "
        "estimate cannot show the voltage stays inside them",
        handed_to="psal_rms",
        basis=(cite("P28_ISSUE_2", "5.3.2 Table 4; Figures 5-7"),),
    ),
    NotScreened(
        item="network_voltage_control_interaction",
        reason="Tap-changer and automatic voltage control response depends on the network "
        "operator's settings",
        handed_to="network_operator",
        basis=(cite("G99_ISSUE_2", "6.3.4"),),
    ),
)


def _events(bess: BessDeclaration) -> tuple[list[tuple[EventName, int, int]], list[DossierAsk]]:
    """(event, P magnitude W, Q magnitude var) for each event that can be formed."""
    asks: list[DossierAsk] = []
    cap, imp, rr = bess.registered_capacity_w, bess.rated_import_power_w, bess.reactive_range
    for value, field in (
        (cap, "registered_capacity_w"),
        (imp, "rated_import_power_w"),
        (rr, "reactive_range"),
    ):
        if isinstance(value, Unknown):
            asks.append(bess_ask(bess, field))
    if asks:
        return [], asks
    assert isinstance(cap, int) and isinstance(imp, int) and not isinstance(rr, Unknown)
    q_trip = max(rr.absorb_var, rr.inject_var)
    return [
        ("trip_at_full_export", cap, q_trip),
        ("trip_at_full_import", imp, q_trip),
        ("unramped_full_reversal", cap + imp, rr.absorb_var + rr.inject_var),
    ], []


def screen_rvc(bess: BessDeclaration, study: StudyInputs) -> RvcScreen:
    limit = CONSTANTS["p28.steady_state_change_limit_pct"].value
    limit_cite = cite_constant("p28.steady_state_change_limit_pct")
    eq_cite = cite("P28_ISSUE_2", "6.3.5 Equations 3 and 4")
    sk_cite = cite("P28_ISSUE_2", "6.4.1(a); 6.1.5")
    site = bess.site_id
    events, asks = _events(bess)
    sk_q, xr_q = study.min_fault_level_va, study.x_over_r
    results: list[EventResult | EventNotComputed] = []
    sk_ask = fault_level_ask(site)
    xr_ask = x_over_r_ask(site)
    for event, p, q in events:
        if not isinstance(sk_q, EvidencedQuantity):
            results.append(
                EventNotComputed(
                    kind="event_not_computed",
                    event=event,
                    reason="minimum fault level at the PCC unknown",
                    bound_estimate_pct_ceil=Unknown(kind="unknown", reason="no fault level"),
                    requests=(sk_ask,),
                )
            )
            asks.append(sk_ask)
            continue
        sk = sk_q.value.as_fraction()
        s2 = Fraction(p * p + q * q)
        bound_sq = s2 / (sk * sk)
        if (100 * 100) * s2 <= (limit * sk) ** 2:
            results.append(
                EventResult(
                    kind="event_result",
                    event=event,
                    p_w=p,
                    q_var=q,
                    outcome="pass_bound_sk",
                    estimate_pct_ceil=rational(_pct(bound_sq)),
                    limit_pct=rational(limit),
                    referred_to="none",
                    basis=(eq_cite, limit_cite, sk_cite),
                )
            )
            continue
        if not isinstance(xr_q, EvidencedQuantity):
            results.append(
                EventNotComputed(
                    kind="event_not_computed",
                    event=event,
                    reason="the fault-level bound exceeds the limit; X/R decides the outcome",
                    bound_estimate_pct_ceil=rational(_pct(bound_sq)),
                    requests=(xr_ask,),
                )
            )
            asks.append(xr_ask)
            continue
        k = xr_q.value.as_fraction()
        a, b = k.numerator, k.denominator
        num = Fraction(p * b + q * a)
        eq3_sq = num * num / (sk * sk * (a * a + b * b))
        ok = (100 * 100) * num * num <= (limit * sk) ** 2 * (a * a + b * b)
        results.append(
            EventResult(
                kind="event_result",
                event=event,
                p_w=p,
                q_var=q,
                outcome="pass_eq3_xr" if ok else "exceeds_steady_state_limit",
                estimate_pct_ceil=rational(_pct(eq3_sq)),
                limit_pct=rational(limit),
                referred_to="none" if ok else "psal_rms",
                basis=(eq_cite, limit_cite, sk_cite, cite("P28_ISSUE_2", "6.1.5")),
            )
        )
    category = bess.rvc_category
    obligations: tuple[CategoryObligation, ...]
    if isinstance(category, Unknown):
        asks.append(
            site_ask(
                "State the P28 Table 4 category (2: up to 4 events a month; 3: 1 event in 3 "
                "months) the customer expects BESS trips to fall in",
                site_id=site,
                holder=AskHolder.CUSTOMER,
            )
        )
        obligations = (_obligation("category_2"), _obligation("category_3"))
    else:
        obligations = (_obligation(category),)
    return RvcScreen(
        kind="rvc_screen",
        v0_basis="nominal_pre_event_voltage",
        point_of_evaluation="pcc",
        events=tuple(results),
        category=category,
        obligations=obligations,
        not_screened=_NOT_SCREENED,
        basis=(
            eq_cite,
            limit_cite,
            sk_cite,
            cite("P28_ISSUE_2", "6.1.2"),
            cite("G99_ISSUE_2", "9.3.2; 9.3.5"),
        ),
        requests=sorted_asks(asks),
    )
