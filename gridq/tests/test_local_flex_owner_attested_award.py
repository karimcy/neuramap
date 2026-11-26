"""D54 — the owner-attested local-flexibility award, from the site record to the priced floor.

The subsystem this exercises was **built, tested and never called**. `AvailabilityCommitment` →
constraint seam → `produce_gate_context` → `GateContext.local_flex_tender_open` →
`LocalFlexPicloStream` OPEN → `Firmness.CONTRACTED` → `finance.cashflows.PRICED_FLOOR_FIRMNESS` has
existed since W7-1/W7-4, and the only way to put a commitment on the seam was
``enumerate_sizing(extra_constraints=…)``, whose sole caller in the whole repo was a test. So every
assertion below is written against **`dispatch_constraints_for`** — the one function `sizer`,
`evaluator`, `underwrite`, `two_stage`, `MilpEngine` and `demo` all call — and never against the
test-only seam, because a green test on the seam is exactly what shipped the defect eleven times.

The tests deliberately assert the DISCLOSURE as hard as they assert the money. Moving a stream into
the priced floor on an owner's word is only defensible while the record says whose word it was, and
a warning nobody tests is a warning that quietly disappears.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import time
from pathlib import Path

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.dispatch import dispatch_constraints_for
from gridq.finance.cashflows import _NO_P90_IN_TERM
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import _local_flex_value
from gridq.quotes.assembly import _canonical_site_dump
from gridq.schemas.market import PRICED_FLOOR_FIRMNESS
from gridq.sizing.deliverability import d57_portfolio_aggregate_mw, produce_gate_context
from gridq.value_stack.gb_streams import (
    LOCAL_FLEX_ATTESTATION_REF_PREFIX,
    LocalFlexPicloStream,
    local_flex_award_constraints,
)
from gridq.value_stack.interface import GateStatus
from gridq.value_stack.registry import default_registry

LIB = load_assumptions("gb_v1_11")
REPO = Path(__file__).resolve().parents[1]
CAMPAIGN_SITES = sorted((REPO / "examples/real/sites").glob("*.json"))

#: The published Port Ham service window (NGED CDP, trade opportunity T10A 2026/27 SAOU_DA):
#: 10:00–16:00, all days, April–September.
PORT_HAM_WINDOW = s.AvailabilityWindow(
    label="Port Ham SAOU_DA capacity requirement window (published)",
    months=[4, 5, 6, 7, 8, 9],
    start_time=time(10),
    end_time=time(16),
)


def _award(*, committed_kw: float = 5.0, **overrides: object) -> s.AvailabilityCommitment:
    payload: dict[str, object] = {
        "commitment_id": "attested_award",
        "stream": s.MarketStream.LOCAL_FLEX_PICLO,
        "committed_power_kw": committed_kw,
        "windows": [PORT_HAM_WINDOW],
        "tenor_years": 1.0,
        "counterparty": "NGED (West Midlands) — CMZ_T10A_WM_0029_G 'Port Ham Generation'",
        "reference": f"{LOCAL_FLEX_ATTESTATION_REF_PREFIX}-2026-08-17",
        "confirmed_by": "Olly (site owner), 2026-08-17",
        "basis": "owner attestation, on good authority (decision D54)",
    }
    payload.update(overrides)
    return s.AvailabilityCommitment.model_validate(payload)


def _site_with_award(make_site: Callable[..., s.SiteModel], **award_kwargs: object) -> s.SiteModel:
    site = make_site()
    return site.model_copy(
        update={
            "connection": site.connection.model_copy(update={"dno_region": "NGED_WM"}),
            "local_flex_awards": [_award(**award_kwargs)],
        }
    )


# ---------------------------------------------------------------------------
# 1. Reachability — the bridge sits on the production chokepoint, not a test seam.
# ---------------------------------------------------------------------------


def test_award_reaches_the_seam_through_dispatch_constraints_for(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """`dispatch_constraints_for` is what every production entry point calls (sizer.py:540,
    evaluator.py:124, underwrite.py:68, two_stage.py:354, engine.py:1138/1290/1323/1528/1730). If
    the award is not on ITS output it is not in a production run, whatever a seam test says."""
    reg = default_registry()
    site = _site_with_award(make_site)

    descriptors = dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg)

    typed = [d for d in descriptors if d.parameters is not None]
    assert len(typed) == 1, "the attested award must ride the production constraint seam"
    assert typed[0].kind == "availability_window"  # the kind the MILP compiles
    assert typed[0].parameters is not None
    assert typed[0].parameters.stream == s.MarketStream.LOCAL_FLEX_PICLO
    # And the pre-D54 behaviour is untouched: a site with no award adds no typed payload.
    bare = dispatch_constraints_for(reg.stream_ids(), make_site(), LIB, registry=reg)
    assert [d for d in bare if d.parameters is not None] == []


def test_award_opens_the_gate_to_contracted_through_the_real_producer(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The whole chain, with no hand-built GateContext: seam → producer → gate → firmness.

    CONTRACTED is inside `schemas.market.PRICED_FLOOR_FIRMNESS`, which is what puts the £ into
    P50, EBITDA and the IRRs — so this assertion IS the money assertion.
    """
    reg = default_registry()
    site = _site_with_award(make_site)
    descriptors = dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg)

    context, _ = produce_gate_context(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        constraints=descriptors,
        assumptions=LIB,
    )

    assert context is not None and context.local_flex_tender_open is True
    gate = LocalFlexPicloStream().check_gates(site, LIB, context)
    assert gate.gate_status is GateStatus.OPEN
    assert gate.firmness in PRICED_FLOOR_FIRMNESS


def test_without_an_award_the_stream_stays_conditional_and_out_of_the_floor(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The negative control. Nothing about D54 opens a gate for a site that declares no award."""
    reg = default_registry()
    site = make_site()
    site = site.model_copy(
        update={"connection": site.connection.model_copy(update={"dno_region": "NGED_WM"})}
    )
    descriptors = dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg)
    context, _ = produce_gate_context(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        constraints=descriptors,
        assumptions=LIB,
    )
    # D57 item 1 (commit 7405d4b): `produce_gate_context` now ALWAYS returns a context, because the
    # assumed-approved aggregated portfolio MW is asserted on every run. What it must NOT do is
    # invent a local-flex fact: `local_flex_tender_open` stays None on the no-commitment branch, so
    # this negative control still holds. Every site the owner actually supplies carries an award
    # under D54, so this path is not reachable on a campaign run.
    assert context is not None
    assert context.local_flex_tender_open is None
    assert context.deliverability_confirmed is None
    assert context.portfolio_mw == pytest.approx(d57_portfolio_aggregate_mw())
    gate = LocalFlexPicloStream().check_gates(site, LIB, context)
    assert gate.gate_status is GateStatus.CONDITIONAL
    assert gate.firmness not in PRICED_FLOOR_FIRMNESS


# ---------------------------------------------------------------------------
# 2. Provenance — an attestation must never be recorded as a sighted contract.
# ---------------------------------------------------------------------------


def test_evidence_names_the_owner_and_never_claims_a_contract_record(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Before D54 every local-flex commitment was stamped ``confirmed_by='contract-record'`` with
    the basis "awarded tender / contracted commitment reference supplied on the constraint seam" —
    a claim about the outside world the producer never checked. A lender reading that is entitled
    to believe a contract was seen."""
    reg = default_registry()
    site = _site_with_award(make_site)
    descriptors = dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg)

    context, _ = produce_gate_context(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        constraints=descriptors,
        assumptions=LIB,
    )

    assert context is not None
    evidence = [e for e in context.evidence if e.stream == s.MarketStream.LOCAL_FLEX_PICLO]
    assert len(evidence) == 1
    assert evidence[0].confirmed_by == "Olly (site owner), 2026-08-17"
    assert "owner attestation" in evidence[0].basis
    assert "contract-record" not in evidence[0].confirmed_by
    assert evidence[0].reference.startswith(LOCAL_FLEX_ATTESTATION_REF_PREFIX)


def test_a_commitment_naming_no_confirmer_keeps_the_pre_d54_wording(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The additive fields must not move an existing record: absent ⇒ the old sentence, verbatim."""
    reg = default_registry()
    site = _site_with_award(make_site, confirmed_by=None, basis=None)
    descriptors = dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg)
    context, _ = produce_gate_context(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        constraints=descriptors,
        assumptions=LIB,
    )
    assert context is not None
    evidence = [e for e in context.evidence if e.stream == s.MarketStream.LOCAL_FLEX_PICLO][0]
    assert evidence.confirmed_by == "contract-record"
    assert evidence.basis == (
        "awarded tender / contracted commitment reference supplied on the constraint seam."
    )


def test_the_open_gate_carries_the_attestation_disclosure(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The labelling is the mitigation, so it is asserted as hard as the firmness.

    Every clause here answers a specific way an OPEN local-flex gate can now mislead: whose word
    it rests on, that no register was consulted, what is still missing, which price is actually
    being used, which leg is still £0, and that P90 does not move.
    """
    reg = default_registry()
    site = _site_with_award(make_site)
    descriptors = dispatch_constraints_for(reg.stream_ids(), site, LIB, registry=reg)
    context, _ = produce_gate_context(
        power_kw=100.0,
        energy_kwh=200.0,
        duration_hours=2.0,
        constraints=descriptors,
        assumptions=LIB,
    )

    text = " ".join(LocalFlexPicloStream().check_gates(site, LIB, context).warnings)

    assert "OWNER ATTESTATION, NOT A SIGHTED CONTRACT" in text
    assert "DID NOT CONSULT ANY DNO REGISTER" in text
    assert "OUTSTANDING CONFIRMATION ASK" in text
    assert "GB-WIDE MEDIAN" in text  # the priced rate is not this zone's clear
    assert "AVAILABILITY LEG OF THIS AWARD IS STILL £0" in text
    assert "P90 AND THE LENDER-BASIS DSCR DO NOT MOVE" in text
    # …and the published clears for the site's own licence area, as context and labelled as such.
    assert "AWARDED CLEARS PUBLISHED FOR THIS LICENCE AREA" in text
    assert "79.75" in text and "1.33" in text


def test_p90_is_structurally_zero_for_this_stream_even_inside_the_award_term() -> None:
    """States the bound the disclosure promises, so the promise cannot rot silently."""
    assert s.MarketStream.LOCAL_FLEX_PICLO.value in _NO_P90_IN_TERM


# ---------------------------------------------------------------------------
# 3. The clamp — a revenue assumption must not delete sizing candidates.
# ---------------------------------------------------------------------------


def test_the_award_is_bounded_by_deliverable_capability(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The seam is built once for the whole grid, before any candidate exists. An availability
    commitment above a candidate's deliverable envelope makes that candidate INFEASIBLE (the MILP
    "returns INFEASIBLE rather than silently relaxing a contract"), so an unbounded award would
    delete the small end of the search for a reason unrelated to its economics. Measured: an
    unbounded award made 138 of 139 candidates infeasible on a real Bishops Cleeve run."""
    site = _site_with_award(make_site, committed_kw=400.0)  # ≈ the site's whole 420 kW peak

    descriptors, notes = local_flex_award_constraints(site, LIB)

    assert descriptors[0].parameters is not None
    modelled = descriptors[0].parameters.committed_power_kw
    # The smallest searchable system (0.05 × min(mic 300, peak 420) = 15 kW, via
    # `two_stage.power_search_floor_kw`), net of the library's 5 kW/MW parasitic draw and of one
    # year of its 2.5%/yr fade — the level the W7-3 end-of-tenor check actually tests against.
    assert modelled == pytest.approx(15.0 * 0.995 * 0.975)
    assert modelled < 400.0
    assert any("MODELLED AT" in n and "UNDERSTATED" in n for n in notes)
    # The declared record itself is untouched — the bound is a modelling limit, not an edit.
    assert site.local_flex_awards is not None
    assert site.local_flex_awards[0].committed_power_kw == 400.0


def test_the_bound_survives_the_end_of_tenor_check_at_the_smallest_candidate(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The second door the award could delete a candidate through, and it is not the solver.

    A tenored commitment is also assessed by `assess_end_of_life_deliverability`, and that verdict
    is not advisory: `sizing/objective.py:82` REJECTS any candidate whose capability at year N —
    ``power × (1 − fade)^tenor`` for a non-CM commitment — is below the committed kW. A bound that
    cleared the dispatch envelope but not the fade would still kill the smallest rung of the grid,
    for exactly the forbidden reason: a revenue assumption narrowing the search.
    """
    from gridq.optimisation.two_stage import (
        connection_power_limit_kw,
        load_relevant_capacity_kw,
        power_search_floor_kw,
    )
    from gridq.sizing.deliverability import assess_end_of_life_deliverability

    site = _site_with_award(make_site, committed_kw=400.0)
    descriptors, _ = local_flex_award_constraints(site, LIB)
    anchor = min(
        float(connection_power_limit_kw(site) or 0.0),
        float(site.load_data.max_import_kw or 0.0),
    )
    floor_kw = power_search_floor_kw(load_relevant_capacity_kw(site, anchor))

    verdict, _ = assess_end_of_life_deliverability(
        power_kw=floor_kw,
        energy_kwh=floor_kw * 0.5,
        duration_hours=0.5,
        constraints=descriptors,
        assumptions=LIB,
    )

    assert verdict is True, "the smallest grid candidate must still clear the award's tenor"


def test_a_zero_load_service_window_no_longer_refuses_the_award() -> None:
    """D57 item 5: the award is bounded by the BATTERY'S capability, not by in-window metered
    import.

    **What this test used to assert, and why the inversion is authorised AND a bug fix.** It
    required
    ``descriptors == []`` on Bishops Cleeve MRF, because the site imports **zero** for 16% of the
    published 10:00-16:00 April-September Port Ham window and the old bound was
    ``min(declared, min in-window metered import, end-of-tenor capability)``. Two things were wrong
    with that, and only the first is about D57:

    1. The metered-import cap existed only because a response had to be served behind the meter,
       which is true only while export consent is absent. D57 assumes export consent, the export
       MPAN and G99/G100 APPROVED, so a response is deliverable as net injection past the meter.
    2. **The SIGN was wrong, independently of D57.** Port Ham's awarded product is Demand Turn Up /
       Generation Turn Down: it pays the asset to CHARGE. Turning demand up consumes import
       HEADROOM, which is LARGEST exactly where metered import is SMALLEST, so the old bound was
       tightest precisely where the real product was easiest to deliver.

    It dies if the metered-import term is put back into
    `gb_streams.local_flex_award_constraints`, and it dies if the measured minimum import stops
    being REPORTED - D57 removes the restriction, never the candour.
    """
    site = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/bishops_cleeve_mrf.json").read_text())
    )

    descriptors, notes = local_flex_award_constraints(site, LIB)

    assert descriptors, "the award must now reserve dispatch headroom (D57 item 5)"
    # The measured figure that USED to refuse the award is still measured and still reported.
    assert any("MINIMUM metered import" in n and "no longer binds" in n for n in notes)
    assert any("DEMAND TURN UP" in n for n in notes)
    # …and the gate says an award IS recorded — never the pre-D54 "no award is recorded" (item 8).
    gate = LocalFlexPicloStream().check_gates(site, LIB, None)
    text = " ".join(gate.warnings)
    assert "NO AWARDED LOCAL-FLEX COMMITMENT IS RECORDED" not in text


def test_a_modelled_award_without_a_gate_context_is_not_reported_as_unmodellable(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The mirror defect, found by reading a real run's output rather than a test.

    The headline `OptimisationResult` of an unviable site carries no solved dispatch, so no
    `GateContext` is produced and every gate falls back to its unevidenced state — even where the
    award modelled fine on all 139 sizing-grid rows. The first version of the item-8 rewrite told
    that reader "the award was NOT modelled", which is false in the opposite direction from the
    sentence it replaced.
    """
    site = _site_with_award(make_site)  # 5 kW award, well inside this site's envelope
    descriptors, _ = local_flex_award_constraints(site, LIB)
    assert descriptors, "precondition: this award models"

    text = " ".join(LocalFlexPicloStream().check_gates(site, LIB, None).warnings)

    assert "an award IS recorded and WAS modelled" in text
    assert "was NOT modelled" not in text


def test_a_site_with_no_award_keeps_the_pre_d54_conditional_wording(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The mirror: the original sentence must survive verbatim for a site that records nothing."""
    site = make_site()
    site = site.model_copy(
        update={"connection": site.connection.model_copy(update={"dno_region": "NGED_WM"})}
    )
    text = " ".join(LocalFlexPicloStream().check_gates(site, LIB, None).warnings)
    assert "NO AWARDED LOCAL-FLEX COMMITMENT IS RECORDED ON THIS SITE" in text
    assert "DESPITE AN AWARD BEING RECORDED" not in text


def test_an_award_within_the_search_floor_passes_through_unclamped(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _site_with_award(make_site, committed_kw=5.0)
    descriptors, notes = local_flex_award_constraints(site, LIB)
    assert descriptors[0].parameters is not None
    assert descriptors[0].parameters.committed_power_kw == pytest.approx(5.0)
    assert not [n for n in notes if "MODELLED AT" in n]


# ---------------------------------------------------------------------------
# 4. Run identity — a set award is a real input; an absent one moves nothing.
# ---------------------------------------------------------------------------


def test_award_enters_the_canonical_hash_input_and_absence_does_not(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """D42 hash policy. Two runs on different awards must never share a ``run_id``; every pre-D54
    site's ``input_hash`` must stay byte-identical."""
    bare = make_site()
    assert "local_flex_awards" not in _canonical_site_dump(bare)
    assert "local_flex_awards" not in _canonical_site_dump(
        bare.model_copy(update={"local_flex_awards": []})
    )
    with_award = _canonical_site_dump(_site_with_award(make_site))
    assert with_award["local_flex_awards"]


# ---------------------------------------------------------------------------
# 5. The campaign sites carry the decision, and carry it honestly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CAMPAIGN_SITES, ids=lambda p: p.stem)
def test_every_campaign_site_records_the_attested_award_with_its_ask(path: Path) -> None:
    """D54 instructs that an award be assumed for every site the owner supplies. What makes that
    admissible rather than a fabrication is the record: an attestation reference (never a DNO
    trade id), a `pending_permission` register entry that cannot carry a value and therefore can
    never read as evidence in hand, and a LOW confidence ceiling."""
    site = s.SiteModel.model_validate(json.loads(path.read_text()))

    assert site.local_flex_awards, f"{path.stem} carries no D54 award"
    award = site.local_flex_awards[0]
    assert award.stream is s.MarketStream.LOCAL_FLEX_PICLO
    assert award.reference is not None
    assert award.reference.startswith(LOCAL_FLEX_ATTESTATION_REF_PREFIX)
    assert award.confirmed_by is not None and "Olly" in award.confirmed_by
    assert award.basis is not None and "OWNER ATTESTATION" in award.basis
    assert "NOT verified against any DNO register" in award.basis

    records = [
        r for r in (site.derived_assumptions or []) if r.key == "local_flex_awards.reference"
    ]
    assert len(records) == 1, f"{path.stem} must state the outstanding reference ask exactly once"
    ask = records[0]
    assert ask.kind == "pending_permission"  # valueless by construction — never "consent granted"
    assert ask.value is None
    assert ask.confidence_ceiling is s.ConfidenceBand.LOW
    assert ask.sources, "the register entry must cite the published source it was checked against"


@pytest.mark.parametrize(
    "stem",
    ["harwell", "knowl_hill", "knowl_hill_invoice_anchored", "colnbrook", "tanhouse"],
)
def test_the_five_zoneless_sites_declare_the_cross_zone_substitution(stem: str) -> None:
    """These sites are in NO tendered zone — an evidenced negative across six published SSEN SEPD
    postcode lists. Their award terms are borrowed from a different zone, which D30 forbids and
    D54 overrides for these sites only. The record must SAY so and NAME the donor: a donor zone's
    price presented as the site's own clear is the failure this test exists to prevent."""
    site = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites" / f"{stem}.json").read_text())
    )
    assert site.local_flex_awards is not None
    basis = site.local_flex_awards[0].basis or ""
    assert "CROSS-ZONE SUBSTITUTION, DECLARED" in basis
    assert "Wallingford" in basis  # the donor, named
    assert "D54 OVERRIDES D30" in basis
    assert "SITS IN NO TENDERED ZONE" in basis


@pytest.mark.parametrize(
    "stem", ["bishops_cleeve_mrf", "bishops_cleeve_ash_plant", "ewelme", "ewelme_invoice_anchored"]
)
def test_the_four_own_zone_sites_claim_no_substitution(stem: str) -> None:
    """The mirror of the test above: a site in its own zone must not be labelled a substitution,
    or the label stops carrying information."""
    site = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites" / f"{stem}.json").read_text())
    )
    assert site.local_flex_awards is not None
    basis = site.local_flex_awards[0].basis or ""
    assert "CROSS-ZONE SUBSTITUTION" not in basis
    assert "PUBLISHED STATUS OF THIS ZONE" in basis
    # The contradicting evidence stays on the record — that is the whole mitigation.
    assert "DO NOT AGREE" in basis


# ---------------------------------------------------------------------------
# 6. LOCALFLEX-3 — the availability-leg sentence must not deny the award it was shown.
# ---------------------------------------------------------------------------

#: The pre-LOCALFLEX-3 sentence, verbatim. A site with no award must keep it BYTE-IDENTICALLY, so
#: hardcode it here rather than compute it — computing it from the helper would test it against
#: itself and would not notice the wording drifting.
_NO_AWARD_SENTENCE = (
    "WHY THIS IS CONTEXT AND NOT REVENUE: the availability leg is priced at £0 because no tender "
    "has been AWARDED to this site. An availability price is asserted only from an awarded tender "
    "(decision D30, recorded on `availability.availability_price_gbp_per_mw_h`, which is null by "
    "design in every release)."
)


def _gate_warning_text(site: s.SiteModel) -> str:
    """The stream's OWN gate warnings, through `check_gates` — the production path.

    Deliberately not a direct call to `local_flex_published_price_context`: the sentence is only a
    defect because a READER sees it, and `check_gates` is what puts it on the record. A test that
    called the helper would still pass if the emission were deleted.
    """
    return " ".join(LocalFlexPicloStream().check_gates(site, LIB, None).warnings)


def test_the_availability_leg_sentence_stops_denying_an_attested_award(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """LOCALFLEX-3: "no tender has been AWARDED to this site" is FALSE once one is attested.

    This is LOCALFLEX-1 one layer along — a claim about the outside world the function never
    checked — except that here the record it was handed REFUTES it, and the same run's other
    sentence says "an award IS recorded". Two contradictory accounts of the same fact on one
    record is precisely what D54's labelling mitigation cannot survive.

    **Updated for D57 item 5b (2026-08-18): the wiring gap this sentence described is CLOSED.**
    `valuation._local_flex_value` now reads the award's own committed kW, derives its contracted
    window hours from the service windows the award declares, and prices them at the AWARDED CLEAR
    for the site's own licence area. So the sentence no longer explains a £0 - it records that the
    leg is priced, and names the wiring gap in the past tense so a reader of an older pack can tell
    the two apart. The published ceilings/averages quoted alongside stay CONTEXT and are still not
    what the £ is built from.
    """
    text = _gate_warning_text(_site_with_award(make_site))

    assert "no tender has been AWARDED to this site" not in text
    assert "1 ATTESTED LOCAL-FLEX AWARD(S) (D54)" in text
    # The leg is priced, from the award's own terms, at the awarded clear.
    assert "THE AVAILABILITY LEG IS PRICED FROM THEM" in text
    assert "awarded_zone_clears" in text
    # The old £0 cause is named in the PAST tense, and the context prices stay context.
    assert "contracted_mw=None" in text
    assert "a WIRING gap, never a fact about the site" in text
    assert "they are not what the £ is built from" in text


def test_a_site_with_no_award_keeps_the_pre_localflex3_sentence_verbatim(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The other half of the guard: no pre-D54 record may move.

    A site that records no award has nothing to contradict, so the original sentence is still the
    honest one and must survive to the byte — otherwise this "fix" silently rewords every quote
    that was already correct.
    """
    site = make_site()
    bare = site.model_copy(
        update={"connection": site.connection.model_copy(update={"dno_region": "NGED_WM"})}
    )
    assert bare.local_flex_awards is None

    assert _NO_AWARD_SENTENCE in _gate_warning_text(bare)


# ---------------------------------------------------------------------------
# D57 item 5b — the availability leg is PRICED, and the utilisation leg is priced
# in the direction the AWARDED PRODUCT is actually delivered in.
# ---------------------------------------------------------------------------


def _flex_schedule(*, charge_mwh: float, discharge_mwh: float) -> HalfHourlyDispatchSchedule:
    """A minimal solved schedule carrying both in-window tender volumes."""
    n = 48
    return HalfHourlyDispatchSchedule(
        power_kw=250.0,
        energy_kwh=500.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=np.zeros(n),
        discharge_kw=np.zeros(n),
        soe_kwh=np.zeros(n),
        site_load_kw=np.full(n, 50.0),
        duos_rate_gbp_per_kwh=np.zeros(n),
        red_band_mask=np.zeros(n, dtype=bool),
        tender_window_charge_mwh=charge_mwh,
        tender_window_discharge_mwh=discharge_mwh,
    )


def test_the_availability_leg_is_priced_from_the_awards_own_mw_and_window_hours() -> None:
    """D57 item 5b: the leg that was structurally £0 on every run now carries a £.

    ``_local_flex_value`` passed ``contracted_mw=None, window_hours=None`` to
    `local_flex_availability_value`, which therefore took its missing-input branch and returned £0 —
    on a site whose attested award records both. For SAOU/VAOU products availability is the LARGER
    half of the payment, so that was a missing leg, not a conservative rounding.

    It dies if the call site stops passing ``contracted_mw=``, stops passing ``window_hours=``, or
    stops passing ``awarded_rate_gbp_per_mw_h=`` — D30's null on
    ``availability.availability_price_gbp_per_mw_h`` is untouched, so without the awarded clear the
    helper still refuses and the leg returns to £0.
    """
    lib = load_assumptions("gb_v1_12")
    site = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/bishops_cleeve_ash_plant.json").read_text())
    )
    revenue, used, notes = _local_flex_value(
        _flex_schedule(charge_mwh=30.0, discharge_mwh=5.0), lib, site
    )
    text = " ".join(notes)
    assert revenue > 0.0, "the local-flex stream must carry a £ under D57"
    # The award's own terms: 46.32 kW over 6 h/day x 183 days = 1,098 h/yr, which is exactly the
    # figure NGED publishes for this zone's window (1,098 h/MW/yr) — derived, never defaulted.
    assert "1,098.0 contracted window hours/yr" in text
    # The count now comes from the awarded row's own published figure; the derivation from the
    # award's declared window (6 h/day x 183 delivery dates) gives exactly the same 1,098 h, so the
    # two agree at this site and the ladder is visible in the note either way.
    assert "PUBLISHED BY THE AWARDED SERVICE ITSELF" in text
    # The rate is this site's OWN zone's awarded clear, cited, and labelled for what it is.
    assert "AWARDED CLEAR USED IN FULL (D57 limb 5b)" in text
    assert any("awarded_zone_clears.nged_wm_port_ham" in k for k in used)
    assert "£1.33/MW/h" in text
    # THE INVOICED MW IS THE MW THE DISPATCH RESERVES (2026-08-19). The award DECLARES 46.32 kW -
    # this site's whole metered peak import - and the dispatch reserves 2.247 kW, the smallest
    # candidate on the sizing grid at its end-of-tenor capability. The leg used to be billed on the
    # declared figure while the reservation held the clamped one, a 20.6x mismatch on the campaign's
    # Harwell run (GBP 504.14/kW/yr of a GBP 631.04/kW/yr total against a Modo GB grid-scale
    # index of GBP 60-80/kW/yr for an entire revenue-stacked megawatt battery).
    #
    # THIS ASSERTION IS THE MUTATION TEST for that fix: revert `_local_flex_value` to read
    # `site.local_flex_awards` instead of `gb_streams.clamped_local_flex_awards` and the leg returns
    # to 1.33 x 0.04632 x 1,098 = GBP 67.64/yr and this line fails.
    #
    # 1.33 £/MW/h x 0.0022468 MW x 1,098 h = £3.28/yr gross availability.
    assert "= £3.28/yr" in text
    assert "0.002 MW contracted" in text
    assert "the award declares 0.046 MW" in text
    assert "THE INVOICED MW IS THE MW IN THE DISPATCH RESERVATION" in text


def test_the_availability_hours_come_from_the_awarded_services_published_count() -> None:
    """The source row's own availability-hour count governs over a re-derivation from clock times.

    SSEN's Wallingford VAOU row publishes ``136 availability h``; re-deriving from the award's
    declared 17:00-19:00 over December, January and February gives 2 h/day x 90 days = 180 h, a
    1.32x overstatement. 136 h is also the count that RECONCILES to the tender's own budget, which
    the same row states: the largest award, 0.29 MW at GBP 183.08 over 136 h, is 90 pct of SSEN's
    published GBP 8,200 tranche budget. 180 h does not reconcile to it.

    It dies if `_local_flex_value` stops passing the resolved zone row to
    `local_flex_award_window_hours`, and it dies if the library row stops publishing the count -
    the derivation would then return and the leg would read 180 h again.
    """
    lib = load_assumptions("gb_v1_12")
    ssen = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/harwell.json").read_text())
    )
    _rev, used, notes = _local_flex_value(
        _flex_schedule(charge_mwh=5.0, discharge_mwh=30.0), lib, ssen
    )
    text = " ".join(notes)
    assert "136.0 contracted window hours/yr" in text
    assert "180.0 contracted window hours/yr" not in text
    # The LABEL follows the ladder. It read "DERIVED FROM THE AWARD'S OWN SERVICE WINDOWS"
    # unconditionally, which became false the moment the published count took precedence: the run
    # then claimed a derivation in the same sentence whose parenthesis said PUBLISHED. That is the
    # contradictory-note-pair defect class, and a note stating a figure must state THAT figure's
    # basis.
    assert "136.0 contracted window hours/yr, PUBLISHED BY THE AWARDED SERVICE ITSELF" in text
    assert "DERIVED FROM THE AWARD'S OWN SERVICE WINDOWS" not in text
    # 174.99 GBP/MW/h x 0.0103716 MW (the clamped reservation) x 136 h = GBP 246.83/yr gross.
    assert "= £246.83/yr" in text
    assert any(k.endswith("availability_h_per_year") for k in used), (
        "the key that priced the hours must reach the assumptions register"
    )

    # NGED's Port Ham row publishes 1,098 h and the derivation gives exactly 1,098, so the change
    # is a no-op there. Pinned, because a silent disagreement between the two would be invisible.
    nged = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/bishops_cleeve_ash_plant.json").read_text())
    )
    _rev2, _used2, notes2 = _local_flex_value(
        _flex_schedule(charge_mwh=30.0, discharge_mwh=5.0), lib, nged
    )
    text2 = " ".join(notes2)
    assert "1,098.0 contracted window hours/yr, PUBLISHED BY THE AWARDED SERVICE ITSELF" in text2
    assert "DERIVED FROM THE AWARD'S OWN SERVICE WINDOWS" not in text2


def test_the_utilisation_leg_follows_the_awarded_products_direction() -> None:
    """A Demand-Turn-Up award is delivered by CHARGING; a Turn-Down award by DISCHARGING.

    Both awarded rows name BOTH directions ("Demand Turn Up / Generation Turn Down" and "Demand Turn
    Down / Generation Turn Up"), so the test that decides has to read the *"the asset is paid
    to ..."* clause. A substring match on "TURN UP" matches both and silently prices SSEN's
    discharge product off the charge series — which is the bug this test exists to keep fixed.

    It dies if the direction branch is removed (both sites would then read the same series) and it
    dies if the ``paid to`` clause parse is replaced by a "TURN UP" substring test.
    """
    lib = load_assumptions("gb_v1_12")
    # NGED_WM Port Ham: paid to CHARGE. Volume must come from the CHARGE series.
    nged = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/bishops_cleeve_ash_plant.json").read_text())
    )
    _rev, _used, notes = _local_flex_value(
        _flex_schedule(charge_mwh=30.0, discharge_mwh=5.0), lib, nged
    )
    text = " ".join(notes)
    assert "DEMAND TURN UP / GENERATION TURN DOWN" in text
    assert "in-window CHARGE the dispatch actually raised" in text
    assert "30.0 MWh delivered in the awarded windows" in text

    # SSEN_SEPD Wallingford: paid to DISCHARGE. Volume must come from the DISCHARGE series.
    ssen = s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/harwell.json").read_text())
    )
    _rev2, _used2, notes2 = _local_flex_value(
        _flex_schedule(charge_mwh=30.0, discharge_mwh=5.0), lib, ssen
    )
    text2 = " ".join(notes2)
    assert "delivered by DISCHARGING" in text2
    assert "5.0 MWh delivered in the awarded windows" in text2
    assert "in-window CHARGE the dispatch actually raised" not in text2


# ---------------------------------------------------------------------------
# D61 (2026-11-22) — the dispatch reservation is PER CANDIDATE. Appended; the
# tests above, including #7 and #8, are unedited.
# ---------------------------------------------------------------------------

# Imported here, not at the top, so the D61 block is purely appended (#7 and #8 stay unedited).
from gridq.assumptions import AssumptionLibrary  # noqa: E402
from gridq.value_stack.interface import DispatchConstraintDescriptor  # noqa: E402


def _harwell() -> s.SiteModel:
    return s.SiteModel.model_validate(
        json.loads((REPO / "examples/real/sites/harwell.json").read_text())
    )


def _expected_reserved_kw(
    declared_kw: float, power_kw: float, tenor_years: float, lib: AssumptionLibrary
) -> float:
    """The D61 clamp, recomputed in the test from the library's own values.

    reserved kW = min(declared kW, P x (1 - standby_kw_per_mw / 1000) x (1 - fade% / 100)^tenor)
    """
    standby = float(lib.get("parasitic_load", "standby_kw_per_mw").value)
    fade = float(lib.get("degradation", "annual_capacity_fade_pct").value)
    capability = power_kw * (1.0 - standby / 1000.0) * (1.0 - fade / 100.0) ** tenor_years
    return min(declared_kw, capability)


def _award_descriptor(
    descriptors: list[DispatchConstraintDescriptor],
) -> s.AvailabilityCommitment:
    """The single local-flex award descriptor's typed payload."""
    awards = [d for d in descriptors if d.constraint_id.startswith("local_flex_award_")]
    assert len(awards) == 1, [d.constraint_id for d in descriptors]
    assert awards[0].parameters is not None
    return awards[0].parameters


def test_seam_reserves_the_candidates_own_capability() -> None:
    """T1 (D61): `dispatch_constraints_for(..., candidate_power_kw=P)` reserves P's own capability.

    Harwell, gb_v1_12: declared 213.81 kW, tenor 1 yr, standby 5 kW/MW, fade 2.5 %/yr, so the
    capability factor is 0.995 x 0.975 = 0.970125. At 100 kW the reservation is 97.013 kW (below
    the declared figure); at 500 kW it is capped at the declared 213.81 kW. With no candidate it is
    the pre-candidate default, the search floor 10.691 kW x 0.970125 = 10.372 kW.

    Dies if any link of the pass-through is removed: the stream's ``constraints`` keyword, the
    builder's keyword, or `dispatch_constraints_for` forwarding it (the reservation would fall
    back to the floor figure at every size).
    """
    lib = load_assumptions("gb_v1_12")
    site = _harwell()
    reg = default_registry()
    assert site.local_flex_awards is not None
    award = site.local_flex_awards[0]
    declared = float(award.committed_power_kw)
    tenor = float(award.tenor_years or 0.0)

    for power_kw in (100.0, 500.0):
        expected = _expected_reserved_kw(declared, power_kw, tenor, lib)
        descriptor = _award_descriptor(
            dispatch_constraints_for(
                reg.stream_ids(), site, lib, registry=reg, candidate_power_kw=power_kw
            )
        )
        assert descriptor.committed_power_kw == pytest.approx(expected, abs=1e-9)
    assert _expected_reserved_kw(declared, 100.0, tenor, lib) < declared  # below the award
    assert _expected_reserved_kw(declared, 500.0, tenor, lib) == declared  # the award binds

    # No candidate: the pre-candidate default, bounded by the site's search floor.
    from gridq.optimisation.two_stage import (
        connection_power_limit_kw,
        load_relevant_capacity_kw,
        power_search_floor_kw,
    )

    limit = connection_power_limit_kw(site)
    peak = site.load_data.max_import_kw
    anchor = min(float(v) for v in (limit, peak) if v is not None and float(v) > 0)
    floor_kw = power_search_floor_kw(load_relevant_capacity_kw(site, anchor))
    default = _award_descriptor(dispatch_constraints_for(reg.stream_ids(), site, lib, registry=reg))
    assert default.committed_power_kw == pytest.approx(
        _expected_reserved_kw(declared, floor_kw, tenor, lib), abs=1e-9
    )
    assert default.committed_power_kw == pytest.approx(10.372, abs=1e-3)


def test_candidate_dispatch_constraints_replaces_only_the_award() -> None:
    """T2 (D61): the per-candidate helper swaps the award and nothing else.

    Order is preserved, a caller-supplied extra descriptor (an id the streams do not produce) is
    returned as the same object, and the award descriptor is replaced by its per-candidate form.
    """
    from gridq.dispatch import candidate_dispatch_constraints

    lib = load_assumptions("gb_v1_12")
    site = _harwell()
    reg = default_registry()
    extra = DispatchConstraintDescriptor(
        constraint_id="caller_supplied_extra",
        description="an extra descriptor the streams do not produce",
        kind="availability_window",
        parameter_source="test",
    )
    base = [*dispatch_constraints_for(reg.stream_ids(), site, lib, registry=reg), extra]

    out = candidate_dispatch_constraints(
        base, reg.stream_ids(), site, lib, candidate_power_kw=100.0, registry=reg
    )

    assert [d.constraint_id for d in out] == [d.constraint_id for d in base]
    assert out[-1] is extra
    before = _award_descriptor(base)
    after = _award_descriptor(out)
    assert after is not before
    assert after.committed_power_kw == pytest.approx(
        _expected_reserved_kw(213.81, 100.0, 1.0, lib), abs=1e-9
    )
    assert before.committed_power_kw == pytest.approx(10.372, abs=1e-3)
    # Every other descriptor is unchanged in value.
    for b, a in zip(base, out, strict=True):
        if not b.constraint_id.startswith("local_flex_award_"):
            assert a == b


# ---------------------------------------------------------------------------
# D61 (2026-11-22): the availability leg bills the reservation the SOLVE recorded.
# ---------------------------------------------------------------------------

from dataclasses import replace as _replace  # noqa: E402

from tests._milp_support import multi_rate_site, write_hh_csv  # noqa: E402

from gridq.dispatch import DispatchProblem  # noqa: E402
from gridq.optimisation.milp_solver import MilpDispatchSolver  # noqa: E402
from gridq.optimisation.valuation import _aggregator_cut  # noqa: E402
from gridq.value_stack.gb_streams import local_flex_awarded_zone_clear  # noqa: E402


def _availability_net_gbp(schedule: HalfHourlyDispatchSchedule, lib, site) -> float:
    """The availability leg's NET £, isolated by difference.

    The utilisation leg reads only the in-window volumes, which are identical on both calls; the
    second call carries an EMPTY reservation record, so its availability leg is not billed (T9).
    """
    full, _u, _n = _local_flex_value(schedule, lib, site)
    unreserved, _u2, _n2 = _local_flex_value(
        _replace(schedule, local_flex_reserved_kw=()), lib, site
    )
    return full - unreserved


def test_invoice_is_the_recorded_reservation_at_every_size(tmp_path: Path) -> None:
    """T7 (D61): real solves at four sizes; each invoice is that solve's own recorded reservation.

    A 7-day synthetic January site carrying Harwell's own award (declared 213.81 kW, tenor 1 yr,
    window 17:00-19:00 in Dec/Jan/Feb) in Harwell's licence area (SSEN_SEPD), on gb_v1_12.
    Recomputed by hand from the release:

        reserved kW  = min(213.81, P x (1 - 5/1000) x (1 - 2.5/100)^1) = min(213.81, 0.970125 P)
        availability = 174.99 GBP/MW/h x reserved kW / 1000 x 136 h x (1 - 0.20)

    At the floor, 100 kW, 300 kW and 400 kW the reservation is 0.970125 x floor, 97.013 kW, then
    213.81 kW twice: the invoice rises strictly with size until the declared kW binds, then holds.

    It dies if `_local_flex_value` stops reading `schedule.local_flex_reserved_kw` (every size would
    be billed the seam's floor reservation, so sizes 2-4 fail), and if the solver stops reserving
    per candidate (the recorded kW would no longer match the formula).
    """
    lib = load_assumptions("gb_v1_12")
    harwell = _harwell()
    hh = tmp_path / "january.csv"
    rows, _ts = write_hh_csv(hh, days=7)
    base = multi_rate_site(hh, rows=rows)
    site = base.model_copy(
        update={
            "connection": base.connection.model_copy(update={"dno_region": "SSEN_SEPD"}),
            "local_flex_awards": harwell.local_flex_awards,
        }
    )
    assert site.local_flex_awards is not None
    award = site.local_flex_awards[0]
    declared = float(award.committed_power_kw)
    tenor = float(award.tenor_years or 0.0)
    reg = default_registry()

    # Every factor from the source the valuation itself reads.
    row, _row_key, _row_notes = local_flex_awarded_zone_clear(site, lib)
    assert row is not None
    rate = float(row["availability_gbp_per_mw_h"])  # type: ignore[arg-type]
    hours = float(row["availability_h_per_year"])  # type: ignore[arg-type]
    cut, _src, _cut_used = _aggregator_cut(site, lib)
    assert (rate, hours, cut) == (174.99, 136.0, 0.20)

    from gridq.optimisation.two_stage import (
        connection_power_limit_kw,
        load_relevant_capacity_kw,
        power_search_floor_kw,
    )

    limit = connection_power_limit_kw(site)
    peak = site.load_data.max_import_kw
    anchor = min(float(v) for v in (limit, peak) if v is not None and float(v) > 0)
    floor_kw = power_search_floor_kw(load_relevant_capacity_kw(site, anchor))

    invoices: list[float] = []
    for power_kw in (floor_kw, 100.0, 300.0, 400.0):
        solver = MilpDispatchSolver()
        result = solver.solve(
            DispatchProblem(
                site_model=site,
                assumptions=lib,
                power_kw=power_kw,
                energy_kwh=2.0 * power_kw,
                duration_hours=2.0,
                constraints=dispatch_constraints_for(
                    reg.stream_ids(), site, lib, registry=reg, candidate_power_kw=power_kw
                ),
            )
        )
        assert result.solver_status.value == "optimal", (power_kw, result.warnings)
        schedule = solver.last_schedule()
        assert schedule is not None
        expected_kw = _expected_reserved_kw(declared, power_kw, tenor, lib)
        assert schedule.local_flex_reserved_kw == (
            (award.commitment_id, pytest.approx(expected_kw, abs=1e-9)),
        )
        net = _availability_net_gbp(schedule, lib, site)
        assert net == pytest.approx(rate * expected_kw / 1000.0 * hours * (1.0 - cut), abs=1e-6)
        _rev, _used, notes = _local_flex_value(schedule, lib, site)
        text = " ".join(notes)
        assert f"{expected_kw / 1000.0:,.3f} MW contracted" in text
        if expected_kw < declared:
            assert "read from the solved schedule's own reservation record" in text
            assert "THE INVOICED MW IS THE MW IN THE DISPATCH RESERVATION" in text
        invoices.append(net)

    assert invoices[0] < invoices[1] < invoices[2], invoices
    assert invoices[3] == pytest.approx(invoices[2], abs=1e-9)  # the declared kW binds
    # 174.99 x 0.21381 x 136 x 0.8 = GBP 4,070.71/yr net at and above 300 kW.
    assert invoices[2] == pytest.approx(4070.71, abs=0.01)


def test_cm_reservation_does_not_set_the_local_flex_invoice() -> None:
    """T8 (D61): the Capacity Market's reservation is not the local-flex reservation.

    The leg used to re-derive its bound from ``schedule.deliverable_capacity_mw``, which is what
    the CAPACITY MARKET reserved. Here the CM reserved 0.05 MW (which would bill 48.506 kW,
    0.049 MW) and the local-flex record says 97.0125 kW (Harwell's award at a 100 kW candidate):
    the leg bills 0.097 MW, the record. It dies if the old `deliverable_capacity_mw` read returns.
    """
    lib = load_assumptions("gb_v1_12")
    site = _harwell()
    assert site.local_flex_awards is not None
    cid = site.local_flex_awards[0].commitment_id
    reserved_kw = 97.0125
    schedule = _replace(
        _flex_schedule(charge_mwh=5.0, discharge_mwh=30.0),
        deliverable_capacity_mw=0.05,
        local_flex_reserved_kw=((cid, reserved_kw),),
    )
    _rev, _used, notes = _local_flex_value(schedule, lib, site)
    text = " ".join(notes)
    assert f"{reserved_kw / 1000.0:,.3f} MW contracted" in text
    assert "0.049 MW contracted" not in text
    # 174.99 GBP/MW/h x 0.0970125 MW x 136 h = GBP 2,308.77/yr gross.
    assert "= £2,308.77/yr" in text


def test_unreserved_award_is_not_billed() -> None:
    """T9 (D61): an award the solved dispatch did not reserve earns no availability, and says so.

    An EMPTY record (a solved schedule whose dispatch compiled no reservation for this award) is
    not the absent record (``None``, billed the seam default). It dies if the ``in by_id`` filter
    is removed (a KeyError) or if the not-reserved award is billed anyway.
    """
    lib = load_assumptions("gb_v1_12")
    site = _harwell()
    schedule = _replace(
        _flex_schedule(charge_mwh=0.0, discharge_mwh=0.0), local_flex_reserved_kw=()
    )
    revenue, _used, notes = _local_flex_value(schedule, lib, site)
    text = " ".join(notes)
    assert revenue == 0.0
    assert "WAS NOT RESERVED IN THIS DISPATCH" in text
    assert "AVAILABILITY LEG FOR AWARD" not in text
    # The award IS on file, so no data ask for its contracted MW may appear.
    assert "the award's contracted MW" not in text


# ---------------------------------------------------------------------------
# D61 (2026-11-22): every sizing evaluation reserves ITS OWN candidate's capability.
# ---------------------------------------------------------------------------

import sys  # noqa: E402

from gridq.dispatch import DispatchResult  # noqa: E402
from gridq.finance import InsufficientInputsError  # noqa: E402
from gridq.optimisation import two_stage as _two_stage  # noqa: E402
from gridq.sizing import evaluator as _evaluator  # noqa: E402
from gridq.sizing import sizer as _sizer  # noqa: E402
from gridq.sizing import underwrite as _underwrite  # noqa: E402
from gridq.sizing.objective import SizingObjective  # noqa: E402
from gridq.sizing.search_space import SearchSpace, SizingCandidate  # noqa: E402


class _RecordingSolver:
    """Records each problem's constraints; returns a minimal BASELINE result (no dispatch run)."""

    MODEL_VERSION = "recording-solver"

    def __init__(self) -> None:
        self.constraints: list[list[DispatchConstraintDescriptor]] = []

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        self.constraints.append(list(problem.constraints))
        return DispatchResult(
            power_kw=problem.power_kw,
            energy_kwh=problem.energy_kwh,
            duration_hours=problem.duration_hours,
            implementation_level=s.ImplementationLevel.BASELINE,
            bankable_status=s.BankableStatus.NOT_BANKABLE,
            solver_status=s.SolverStatus.BASELINE,
        )


def _no_finance(*_args: object, **_kwargs: object):
    raise InsufficientInputsError("finance is not exercised by this test")


def test_evaluate_spec_reserves_per_candidate() -> None:
    """T3 (D61): `_evaluate_spec` rebinds the award at THIS candidate before it solves.

    It is handed the search's candidate-less constraint list (Harwell's award at the floor,
    10.372 kW) and a 100 kW candidate; the problem the solver receives must carry 100 x 0.970125 =
    97.013 kW. It dies if the first statement of `_evaluate_spec` (the
    `candidate_dispatch_constraints` rebind) is deleted.
    """
    lib = load_assumptions("gb_v1_12")
    site = _harwell()
    reg = default_registry()
    floor_constraints = dispatch_constraints_for(reg.stream_ids(), site, lib, registry=reg)
    assert _award_descriptor(floor_constraints).committed_power_kw == pytest.approx(
        10.372, abs=1e-3
    )

    solver = _RecordingSolver()
    evaluation = _sizer._evaluate_spec(
        site,
        lib,
        SizingCandidate(power_kw=100.0, duration_hours=2.0),
        dispatch_solver=solver,
        streams=reg.stream_ids(),
        constraints=floor_constraints,
        debt_terms=None,
        horizon_years=None,
        scenario=None,
        finance_runner=_no_finance,
        model_version=solver.MODEL_VERSION,
        registry=reg,
    )
    assert evaluation.priceable is False  # the refusing finance runner was reached and recorded
    assert len(solver.constraints) == 1
    reserved = _award_descriptor(solver.constraints[0]).committed_power_kw
    assert reserved == pytest.approx(_expected_reserved_kw(213.81, 100.0, 1.0, lib), abs=1e-9)
    assert reserved == pytest.approx(97.0125, abs=1e-9)


def test_every_evaluate_spec_caller_passes_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4 (D61): all seven `_evaluate_spec` call sites pass ``registry=``.

    `enumerate_sizing`, `underwrite_spec` and the continuous evaluator are run against the REAL
    `_evaluate_spec` behind a recorder (so a dropped ``registry=`` is also a ``TypeError``); the
    two-stage search is run on a 14-day series (so its typical-day proxy, confirmation, polish and
    no-viable cover stages all fire) against a recorder returning a non-viable evaluation. Every
    two-stage call site must be reached, located by its own line in the module source, so deleting
    any one ``registry=reg`` turns this red.
    """
    lib = load_assumptions("gb_v1_12")
    hh = tmp_path / "fortnight.csv"
    rows, _ts = write_hh_csv(hh, days=14)
    site = multi_rate_site(hh, rows=rows)
    objective = SizingObjective(min_p90_dscr_covenant=1.3)
    real = _sizer._evaluate_spec
    calls: list[tuple[str, int, dict[str, object]]] = []

    def _recorder(tag: str, *, delegate: bool):
        def _record(site, assumptions, candidate, **kwargs):
            calls.append((tag, sys._getframe(1).f_lineno, dict(kwargs)))
            if delegate:
                return real(site, assumptions, candidate, **kwargs)
            return _sizer.CandidateEvaluation(
                candidate=candidate,
                optimisation_result=s.OptimisationResult(
                    result_id="res_stub",
                    request_id="req_stub",
                    implementation_level=s.ImplementationLevel.PRODUCTION,
                    bankable_status=s.BankableStatus.NOT_BANKABLE,
                    selected_power_kw=candidate.power_kw,
                    selected_energy_kwh=candidate.energy_kwh,
                    duration_hours=candidate.duration_hours,
                    value_streams=[],
                    solver_status=s.SolverStatus.OPTIMAL,
                ),
                priceable=True,
                bankable=False,
                min_p90_dscr=None,
                covenant=1.3,
                passes_covenant=None,
                levered_return_pct=None,
                project_irr_pct=-2.0,
                financial=None,
            )

        return _record

    monkeypatch.setattr(_sizer, "_evaluate_spec", _recorder("enumerate", delegate=True))
    monkeypatch.setattr(_underwrite, "_evaluate_spec", _recorder("underwrite", delegate=True))
    monkeypatch.setattr(_evaluator, "_evaluate_spec", _recorder("evaluator", delegate=True))
    monkeypatch.setattr(_two_stage, "_evaluate_spec", _recorder("two_stage", delegate=False))

    _sizer.enumerate_sizing(
        site,
        lib,
        SearchSpace(power_kw_candidates=[50.0], duration_hour_candidates=[2.0]),
        dispatch_solver=_RecordingSolver(),
        objective=objective,
        finance_runner=_no_finance,
    )
    _underwrite.underwrite_spec(
        site,
        lib,
        SizingCandidate(power_kw=50.0, duration_hours=2.0),
        dispatch_solver=_RecordingSolver(),
        objective=objective,
        finance_runner=_no_finance,
    )
    _evaluator.ContinuousSizingEvaluator(
        site,
        lib,
        dispatch_solver=_RecordingSolver(),
        objective=objective,
        finance_runner=_no_finance,
    ).evaluate(50.0, 100.0)
    _two_stage.two_stage_sizing(
        site,
        lib,
        base_solver=MilpDispatchSolver(),
        objective=objective,
        binding_capacity_kw=500.0,
        seed=0,
    )

    assert {tag for tag, _line, _kw in calls} == {
        "enumerate",
        "underwrite",
        "evaluator",
        "two_stage",
    }
    missing = [(tag, line) for tag, line, kwargs in calls if "registry" not in kwargs]
    assert not missing, f"_evaluate_spec called without registry= from {sorted(set(missing))}"
    assert all(kwargs["registry"] is not None for _t, _l, kwargs in calls)

    # Every two-stage call site was reached: the recorded caller lines are exactly the lines on
    # which `two_stage.py` calls `_evaluate_spec(`.
    source = Path(_two_stage.__file__).read_text().splitlines()
    sites = {i for i, text in enumerate(source, start=1) if text.strip() == "_evaluate_spec("}
    assert len(sites) == 4, sites
    reached = {line for tag, line, _kw in calls if tag == "two_stage"}
    assert reached == sites, (reached, sites)
