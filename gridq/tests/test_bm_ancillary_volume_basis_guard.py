"""The BM / ancillary VOLUME BASIS: the sourced sellable-MW definition, on both branches.

**WHAT THIS FILE USED TO BE, AND WHY IT CHANGED (2026-08-19, Olly).** It pinned a labelled
**refusal to price**: a consented BM or DC/DM/DR unit whose volume had been built by importing the
Capacity Market duration de-rating reported GBP 0, with a loud note, until a caller supplied an
attributed `CapacityMarketDeratingBasisAcknowledgement`. Twelve of this module's tests asserted that
refusal or the figures it produced, and every one of them is now the stale half.

**D57 limb 1 is what settles it, verbatim:** "Every revenue stream is OPEN and PRICED on every site.
... No stream may be suppressed, zeroed, gated, held conditional, or excluded from the priced
floor." A refusal to price is precisely that, and D45's rule the refusal was built on ("refuse the
GBP or derive it, never both-and-neither") is resolved the other way under D57: **derive it.**

The mechanism was retired rather than suppressed. `optimisation/valuation.py` replaced the
CM-de-rated FALLBACK volume with `value_stack.gb_streams.sellable_volume_mw` - the single sourced
definition the proxy path already used, which is plain deliverable power, route-aware-truncated to
whole MW, and reads no de-rating factor at all. So the wrong basis is no longer reachable from the
production call site, `_CM_DERATE_KEY` is never credited there, and there is nothing left to refuse.
The guard block itself is KEPT, unreachable, as the correct refusal the day any caller reintroduces
a CM-de-rated volume on these streams.

**WHAT STILL BITES, because a published figure needs its basis stated (D57 limb 4, D59 limb 3):**

* The volume basis reaches the reader: `VOLUME BASIS`, `truncated to whole MW`, and `NO Capacity
  Market de-rating factor is applied` all appear in the rendered underwriting memo, through the real
  `outputs.viewmodel` -> `outputs.underwriting` chain including `formatting.no_dashes`.
* The two volume branches AGREE. The documented engine-vs-proxy divergence is closed only while both
  call `sellable_volume_mw`, and a de-rating creeping back into either shows up as a credited
  `_CM_DERATE_KEY`.
* The 1 MW threshold is still read from ONE sourced place (`gb_streams.eligibility_threshold_mw`).
  D57 limb 3 assumes it SATISFIED; it does not delete it, and a second hardcoded copy is still how
  two places come to disagree about who may enter a market.
* The acknowledgement is now **INERT**, asserted as byte-identical output with and without it. That
  is a tripwire in the D57 direction: it goes RED the day the retired guard fires again.
* The Capacity Market is untouched by the volume-basis change, because for the CM the duration
  de-rating IS the correct basis. Its figure moved for a different reason (FIX 5, commit 05133e4:
  the de-rating now lands once, not twice) and is re-pinned with that mechanism reconciled.

**DIRECTION OF EFFECT: revenue UP.** GBP 0 -> GBP 120,000/yr on the 4 MW fixture, GBP 60,000 ->
GBP 300,000/yr where a 10 MW unit had been de-rated to 2 whole MW, and GBP 100,000 ->
GBP 454,959.05 on the Capacity Market case. Each is stated on its own test.

**NO GATE, THRESHOLD, MINIMUM SIZE, CONSENT TEST, AWARD REQUIREMENT OR STACKING PROHIBITION IS
ADDED OR RESTORED ANYWHERE IN THIS FILE.**
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.optimisation import valuation
from gridq.optimisation._assumptions import value_for_duration
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import (
    CM_DERATING_BASIS_ACKNOWLEDGED,
    CM_DERATING_BASIS_REFUSAL,
    CapacityMarketDeratingBasisAcknowledgement,
    build_value_streams,
)
from gridq.outputs import underwriting as uw
from gridq.outputs import viewmodel as vmod
from gridq.schemas import MarketStream, RateUnit
from gridq.sizing.deliverability import produce_gate_context
from gridq.value_stack.gb_streams import eligibility_threshold_mw

LIB = load_assumptions("gb_v1_8")
BM = MarketStream.BALANCING_MECHANISM
EAC = MarketStream.ANCILLARY_DC_DM_DR
SIZE_GATED = (BM, EAC)
AGREED_RATE = 30_000.0

#: A valid opt-in. Built once so every test uses the same attributed record.
ACK = CapacityMarketDeratingBasisAcknowledgement(
    authorised_by="Olly",
    justification="Feasibility comparison against the D43 baseline before the basis is corrected.",
    acknowledgement=CapacityMarketDeratingBasisAcknowledgement.ACKNOWLEDGEMENT,
)


def _schedule(*, power_kw: float, deliverable_mw: float | None) -> HalfHourlyDispatchSchedule:
    n = 48
    zeros = np.zeros(n, dtype=float)
    return HalfHourlyDispatchSchedule(
        power_kw=power_kw,
        energy_kwh=power_kw * 2.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=zeros,
        discharge_kw=zeros,
        soe_kwh=zeros,
        site_load_kw=np.full(n, 100.0),
        duos_rate_gbp_per_kwh=np.full(n, 0.1),
        red_band_mask=np.zeros(n, dtype=bool),
        deliverable_capacity_mw=deliverable_mw,
    )


def _site(
    make_site: Callable[..., s.SiteModel],
    *,
    consents: tuple[s.RevenueStreamConsent, ...],
    mic_kw: float,
) -> s.SiteModel:
    """A consented site whose connection can present a unit of ``mic_kw`` (D-92 gate input)."""
    base = make_site()
    return s.SiteModel.model_validate(
        {
            **base.model_dump(mode="python"),
            "stream_consents": tuple(consents),
            "connection": base.connection.model_copy(update={"mic_kw": mic_kw, "mec_kw": mic_kw}),
        }
    )


def _consent(stream: MarketStream, rate: float, unit: RateUnit) -> s.RevenueStreamConsent:
    return s.RevenueStreamConsent(
        stream=stream, consented=True, agreed_rate_gbp=rate, agreed_rate_unit=unit
    )


def _entry(groups: tuple[list[s.ValueStreamResult], ...], sid: MarketStream):
    hits = [v for group in groups for v in group if v.stream == sid]
    assert len(hits) == 1, f"expected exactly one {sid.value} entry, got {len(hits)}"
    return hits[0]


def _priced(
    make_site: Callable[..., s.SiteModel],
    stream: MarketStream,
    *,
    power_kw: float,
    deliverable_mw: float | None = None,
    ack: CapacityMarketDeratingBasisAcknowledgement | None = None,
) -> tuple[s.ValueStreamResult, list[s.ValueStreamResult], list[s.ValueStreamResult]]:
    """Run the production entry point for one consented stream; return (entry, floor, upside).

    ``deliverable_mw`` defaults to ``None``, which used to be the GUARD-ARMED configuration: with no
    solver-reserved capability the volume fell back to ``sized power x the CM duration de-rating``,
    the imported basis the retired guard existed to refuse. That fallback is gone. Both branches now
    read ``gb_streams.sellable_volume_mw`` on the unit's own deliverable power, so the argument no
    longer selects a basis and the two branches are expected to AGREE, which
    `test_the_volume_is_the_sourced_sellable_basis_and_credits_no_de_rating_key` asserts.

    NOTE, and it is load-bearing for
    `test_a_sub_1_mw_unit_is_refused_only_when_no_aggregation_context_is_supplied`: this helper
    passes **no gate context**, which no production caller does. `generate_quote` always supplies
    one from `sizing.deliverability.produce_gate_context`, carrying D57's assumed-approved portfolio
    aggregate, so a sub-1 MW unit that reads GBP 0 here prices in full on a real run.
    """
    site = _site(
        make_site,
        consents=(_consent(stream, AGREED_RATE, RateUnit.GBP_PER_MW_YEAR),),
        mic_kw=power_kw,
    )
    floor, upside, _ = build_value_streams(
        _schedule(power_kw=power_kw, deliverable_mw=deliverable_mw),
        site,
        LIB,
        cm_derating_basis_acknowledgement=ack,
    )
    return _entry((floor, upside), stream), floor, upside


def _memo_stream_notes(*groups: list[s.ValueStreamResult]) -> str:
    """The stream notes as the UNDERWRITING MEMO renders them — the layer a reader reads.

    Exercises the real production chain rather than a paraphrase of it: `_stream_row` and
    `_number_stream_notes` are what `viewmodel._split` runs (`outputs/viewmodel.py:638`) and
    `underwriting._stream_notes` is what the memo calls (`outputs/underwriting.py:225`), including
    the `formatting.no_dashes` rewrite that a dash-carrying label would not survive.
    """
    rows = [[vmod._stream_row(v) for v in group] for group in groups]
    notes, _ = vmod._number_stream_notes(*rows)
    return uw._stream_notes(notes)


# --- the basis: the production call site, asserted at the reader's layer -------


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_stream_prices_on_the_sourced_volume_basis_and_the_memo_states_it(
    stream: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    """WAS ``test_the_refusal_reaches_the_underwriting_memo_a_reader_reads``.

    It asserted `CM_DERATING_BASIS_REFUSAL in memo` and `annual_revenue_gbp == 0.0`. Under D57
    limb 1 a refusal to price is exactly what may not happen: "No stream may be suppressed, zeroed,
    gated, held conditional, or excluded from the priced floor." The refusal was retired by
    replacing the CM-de-rated FALLBACK volume with `gb_streams.sellable_volume_mw`, so the wrong
    basis is no longer reachable from this call site and there is nothing left to refuse.

    What the reader must still be told is the BASIS, and that is what this now asserts on the
    rendered memo: the volume is plain deliverable power truncated to whole MW, and NO Capacity
    Market de-rating factor is applied. A figure whose basis is unstated is the defect this file
    was always about; only the verdict changed, from withheld to published-and-labelled.

    Direction of effect: GBP 0 -> GBP 120,000/yr on this fixture (30,000/MW/yr x 4 whole MW).
    """
    entry, floor, upside = _priced(make_site, stream, power_kw=4_000.0)
    memo = _memo_stream_notes(floor, upside)

    # 1. The stream is PRICED and reaches the priced floor as CONTRACTED (D57 limb 1).
    assert entry.annual_revenue_gbp == pytest.approx(AGREED_RATE * 4.0)
    assert entry.firmness == s.Firmness.CONTRACTED
    assert any(v.stream == stream for v in floor), "a priced consented stream is a floor entry"
    # 2. No refusal, and no acknowledged-wrong-basis verdict either: neither applies now.
    assert CM_DERATING_BASIS_REFUSAL not in memo
    assert CM_DERATING_BASIS_ACKNOWLEDGED not in memo
    assert not any(w.startswith(CM_DERATING_BASIS_REFUSAL) for w in entry.warnings)
    # 3. The BASIS is stated in the memo a reader reads, and names what it is NOT.
    assert "VOLUME BASIS" in memo
    assert "truncated to whole MW" in memo
    assert "NO Capacity Market de-rating factor is applied" in memo
    assert "plain deliverable power" in memo
    assert "D-102/D57" in memo


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_sourced_threshold_is_quoted_rather_than_a_second_copy_of_it(
    stream: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    """WAS ``test_the_refusal_quotes_the_sourced_threshold_rather_than_a_second_copy_of_it``.

    The protection survives the refusal's retirement, relocated to the note that now carries the
    threshold: the eligibility verdict. The 1 MW figure is still READ from `gb_streams`, never
    restated in valuation.py, because a second hardcoded copy of a sourced threshold is how two
    places come to disagree about who may enter a market. This fails if any note grows its own
    literal.

    D57 limb 3 assumes the threshold SATISFIED; it does not delete it. The threshold still has to
    be sourced from one place, and it is still the number the record quotes.
    """
    threshold = eligibility_threshold_mw(stream)
    assert threshold is not None, "the sourced BM/ancillary minimum must stay armed"
    entry, _, _ = _priced(make_site, stream, power_kw=4_000.0)
    verdict = next(w for w in entry.warnings if w.startswith("eligible STANDALONE"))
    assert f"{threshold:,.3f} MW" in verdict
    # And no note anywhere on the entry restates the threshold as its own literal.
    assert not any("1 MW minimum" in w for w in entry.warnings), (
        "a note is restating the sourced threshold in its own words rather than formatting it"
    )


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_no_whole_mw_truncation_note_is_emitted_when_the_whole_unit_is_sellable(
    stream: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    """WAS ``test_the_refusal_pre_empts_the_misleading_whole_mw_truncation_note``.

    The old defect: a 4 MW unit's 0.879 CM-de-rated MW truncated to 0, and the record blamed
    WHOLE-MW ORDER GRANULARITY for a GBP 0 that was really the imported CM de-rating, which was
    true and misleading at once. Retiring the de-rated fallback removed the cliff at source: a
    4 MW unit is now 4 whole sellable MW, so no truncation note can be emitted at all and the
    misleading attribution is structurally impossible rather than pre-empted by a guard.

    D57 limb 1 settles the verdict half (the stream is priced, not refused); this test holds the
    reporting half, which is that a GBP 0 is never blamed on the wrong cause.
    """
    entry, _, _ = _priced(make_site, stream, power_kw=4_000.0, deliverable_mw=None)
    assert entry.annual_revenue_gbp == pytest.approx(AGREED_RATE * 4.0)
    assert not any(
        w.startswith("£0:") and "truncates to 0 MW as a sell order" in w for w in entry.warnings
    ), "an order-granularity note was emitted for a unit whose whole capability is sellable"
    assert not any("IT IS A CLIFF, NOT A HAIRCUT" in w for w in entry.warnings), (
        "the de-rated-volume cliff diagnosis survived the retirement of the de-rated volume"
    )


# --- the retired opt-in: it must now be INERT, and that is the tripwire --------


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_acknowledgement_is_now_INERT_on_the_production_path(
    stream: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    """WAS ``test_the_acknowledgement_prices_the_stream_and_labels_the_figure_understated``.

    The opt-in existed to let a caller publish a figure on the CM-de-rated basis with an attributed
    acknowledgement. There is no CM-de-rated basis on this path any more, so passing the
    acknowledgement must change NOTHING. Asserted as byte-identical warnings and an identical GBP
    with and without it, which is a tripwire in the D57 direction: the day anyone re-arms the guard
    on this call site, the two arms diverge and this goes RED.

    The acknowledgement TYPE is deliberately not deleted (its construction rules are still tested
    below, and it is the correct opt-in the day a caller reintroduces a CM-de-rated volume) - but a
    dead path that silently starts firing again is the failure this pins.
    """
    without, floor_without, _ = _priced(make_site, stream, power_kw=10_000.0)
    with_ack, floor_with, _ = _priced(make_site, stream, power_kw=10_000.0, ack=ACK)

    assert with_ack.annual_revenue_gbp == without.annual_revenue_gbp
    assert list(with_ack.warnings) == list(without.warnings), (
        "the acknowledgement changed the record, so the retired guard is firing again"
    )
    # Neither verdict label reaches the reader, because neither applies.
    memo = _memo_stream_notes(floor_with)
    assert CM_DERATING_BASIS_ACKNOWLEDGED not in memo
    assert CM_DERATING_BASIS_REFUSAL not in memo


def test_bm_prices_the_agreed_rate_times_the_whole_sellable_mw(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """WAS ``test_bm_prices_the_agreed_rate_times_the_derated_mw_when_acknowledged``.

    RE-PINNED, and the reason is the retirement of the de-rated fallback volume. On D43's basis a
    10 MW / 2 h unit de-rated to 2.198 MW and offered 2 whole MW, so the pin was
    ``AGREED_RATE x 2.0`` = GBP 60,000. The volume is now `gb_streams.sellable_volume_mw` on plain
    deliverable power, so the same unit offers 10 whole MW and the figure is GBP 300,000. That is
    a 5x increase and it is the D57 direction of effect, stated rather than absorbed.

    The literal is kept deliberately: computing the expectation from `sellable_volume_mw` would be
    tautological and a bug in that function would pass silently here.
    """
    entry, _, _ = _priced(make_site, BM, power_kw=10_000.0, deliverable_mw=None)
    assert entry.annual_revenue_gbp == pytest.approx(300_000.0)
    assert entry.annual_revenue_gbp == pytest.approx(AGREED_RATE * 10.0)


def test_the_acknowledgement_cannot_be_enabled_by_a_truthy_flag() -> None:
    """No default, no boolean: the sentence must be quoted verbatim or construction raises."""
    with pytest.raises(ValueError, match="verbatim"):
        CapacityMarketDeratingBasisAcknowledgement(
            authorised_by="Olly", justification="because", acknowledgement="yes"
        )
    with pytest.raises(ValueError, match="verbatim"):
        CapacityMarketDeratingBasisAcknowledgement(
            authorised_by="Olly",
            justification="because",
            # Near-miss: the right words, the wrong sentence. Still refused.
            acknowledgement=CapacityMarketDeratingBasisAcknowledgement.ACKNOWLEDGEMENT.upper(),
        )


@pytest.mark.parametrize(("who", "why"), [("", "because"), ("  ", "because"), ("Olly", "   ")])
def test_the_acknowledgement_must_be_attributed_and_explained(who: str, why: str) -> None:
    """An unattributed or unexplained override is the silent default this input exists to bar."""
    with pytest.raises(ValueError, match="non-blank"):
        CapacityMarketDeratingBasisAcknowledgement(
            authorised_by=who,
            justification=why,
            acknowledgement=CapacityMarketDeratingBasisAcknowledgement.ACKNOWLEDGEMENT,
        )


def test_the_acknowledgement_label_survives_the_no_dashes_render() -> None:
    """Both prefixes must be greppable in the RENDERED document, so neither may carry a dash.

    `formatting.no_dashes` rewrites em and en dashes on their way to the reader (an em dash becomes
    a comma), and stream notes pass through it. A label that only greps in the record is a label
    that does not grep where it matters.
    """
    from gridq.outputs import formatting as fmt

    for label in (CM_DERATING_BASIS_REFUSAL, CM_DERATING_BASIS_ACKNOWLEDGED):
        assert fmt.no_dashes(label) == label


# --- the corrected half: the volume is the SOURCED basis on BOTH branches -----


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_the_volume_is_the_sourced_sellable_basis_and_credits_no_de_rating_key(
    stream: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    """WAS ``test_a_solver_measured_capability_prices_with_no_acknowledgement``.

    RE-PINNED from GBP 60,000 to GBP 120,000, and the reason is a basis change, not a bigger
    number for its own sake. That test passed ``deliverable_mw=2.0`` on a 4 MW unit and pinned
    ``AGREED_RATE x 2.0``, because `_derated_deliverable_mw` returned the solver's reserved
    capability verbatim. The volume is now `gb_streams.sellable_volume_mw` on the unit's own
    deliverable power, so BOTH branches read the same single sourced definition and the reserved
    capability no longer selects the volume: 4 whole MW either way, GBP 120,000.

    THE INVARIANT THIS NOW PINS, which is stronger than the old one: the two branches AGREE. The
    documented engine-vs-proxy volume divergence is closed only while they call the same function,
    and a de-rating factor creeping back into either one would show up here as a credited
    `_CM_DERATE_KEY`.
    """
    measured, floor_m, upside_m = _priced(make_site, stream, power_kw=4_000.0, deliverable_mw=2.0)
    fallback, floor_f, _ = _priced(make_site, stream, power_kw=4_000.0, deliverable_mw=None)

    assert measured.annual_revenue_gbp == pytest.approx(120_000.0)
    assert measured.annual_revenue_gbp == pytest.approx(AGREED_RATE * 4.0)
    assert fallback.annual_revenue_gbp == measured.annual_revenue_gbp, (
        "the reserved-capability and fallback branches no longer read the same volume definition"
    )
    assert measured.firmness == s.Firmness.CONTRACTED
    assert any(v.stream == stream for v in floor_m), "it must reach the priced floor"
    assert all(v.stream != stream for v in upside_m)
    # No de-rating factor was consulted on either branch, and the record says so.
    assert valuation._CM_DERATE_KEY not in (measured.assumptions_used or [])
    assert any("NO Capacity Market de-rating factor is applied" in w for w in measured.warnings)
    # And neither retired verdict label is present, because neither applies.
    assert not any(w.startswith(CM_DERATING_BASIS_REFUSAL) for w in measured.warnings)
    assert not any(w.startswith(CM_DERATING_BASIS_ACKNOWLEDGED) for w in measured.warnings)


def test_the_guard_arms_off_the_provenance_key_so_it_cannot_silently_stop_firing() -> None:
    """The two branches are told apart by what they CREDIT, and that link is pinned here.

    The guard reads `_CM_DERATE_KEY` out of the volume's ``assumptions_used``. If that constant and
    the key `_derated_deliverable_mw` actually credits ever drifted apart, the guard would conclude
    "the de-rating was not consulted" and **publish** the very 9×-understated figure it exists to
    withhold — a silent failure in the unsafe direction, which is the one worth a test.
    """
    lib = load_assumptions("gb_v1_8")
    # Fallback branch: no reserved capability, so the CM duration factor IS read and credited.
    mw, _, used = valuation._derated_deliverable_mw(
        _schedule(power_kw=10_000.0, deliverable_mw=None), lib
    )
    assert mw == pytest.approx(2.198)
    assert used == [valuation._CM_DERATE_KEY], "the guard's key must be the one credited here"
    # Measured branch: the solver's own committed MW, verbatim, crediting NO de-rating factor.
    mw, _, used = valuation._derated_deliverable_mw(
        _schedule(power_kw=4_000.0, deliverable_mw=2.0), lib
    )
    assert mw == pytest.approx(2.0)
    assert valuation._CM_DERATE_KEY not in used


# --- what the change must NOT touch -------------------------------------------


@pytest.mark.parametrize("stream", SIZE_GATED)
def test_a_sub_1_mw_unit_is_refused_only_when_no_aggregation_context_is_supplied(
    stream: MarketStream, make_site: Callable[..., s.SiteModel]
) -> None:
    """The D-92 size refusal survives ONLY on a call with no gate context, and that matters.

    WHY THIS DOCSTRING IS LONGER THAN THE TEST. The first half below asserts a sub-1 MW unit earns
    GBP 0, which read on its own is exactly the outcome D57 limb 3 forbids: "Statutory thresholds
    (the 1 MW registered-unit minimum ...) are all ASSUMED SATISFIED and must not reduce a figure."
    It is not a violation, and the second half is the proof: the GBP 0 depends entirely on this
    file's helper passing ``context=None``, which NO production caller does. `generate_quote` runs
    `sizing.deliverability.produce_gate_context`, and under D57 that function ALWAYS returns a
    context carrying an assumed-approved portfolio aggregate
    (`sizing/deliverability.py:349`), so every campaign site clears the minimum via the
    aggregation route and prices in full.

    Both halves are asserted here so the first can never again be read as a live gate. Every one of
    the eight campaign sites is sub-1 MW; if the D57 aggregation context ever stopped being
    produced, the second half goes RED and the whole campaign silently returns to GBP 0.
    """
    # 1. No context: D-92 refuses on size, and the refusal is the OLD, pre-D57 shape.
    entry, floor, _ = _priced(make_site, stream, power_kw=300.0, deliverable_mw=0.3)
    assert entry.annual_revenue_gbp == 0.0
    assert all(v.stream != stream for v in floor)
    assert not any(w.startswith(CM_DERATING_BASIS_REFUSAL) for w in entry.warnings)
    assert any("cannot be entered at this unit's size" in w for w in entry.warnings)

    # 2. The PRODUCTION context: D57's assumed-approved aggregate clears the same threshold, and
    #    the same unit prices its own fractional MW inside the aggregate (D57 limbs 2 and 3).
    context, context_warnings = produce_gate_context(
        power_kw=300.0, energy_kwh=600.0, duration_hours=2.0, constraints=[], assumptions=LIB
    )
    assert context is not None, "D57: a gate context is produced on every run"
    assert context.portfolio_mw is not None and context.portfolio_mw > 0
    assert any("D57 ASSUMED APPROVED" in w for w in context_warnings)

    site = _site(
        make_site,
        consents=(_consent(stream, AGREED_RATE, RateUnit.GBP_PER_MW_YEAR),),
        mic_kw=300.0,
    )
    agg_floor, agg_upside, _ = build_value_streams(
        _schedule(power_kw=300.0, deliverable_mw=0.3), site, LIB, context=context
    )
    aggregated = _entry((agg_floor, agg_upside), stream)
    assert aggregated.annual_revenue_gbp == pytest.approx(AGREED_RATE * 0.3), (
        "under D57 the assumed aggregation must clear the statutory minimum and price the unit's "
        "own fractional MW inside the aggregate"
    )
    assert any("eligible via AGGREGATION" in w for w in aggregated.warnings)


def test_the_capacity_market_prices_on_a_rate_re_based_to_rated_capacity(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """WAS ``test_the_capacity_market_still_prices_on_its_own_de_rating_at_the_same_size``.

    RE-PINNED from GBP 100,000 to GBP 454,959.05 by FIX 5 (commit 05133e4, 2026-08-19), which made
    the Capacity Market de-rating land ONCE instead of twice. The consented rate is treated as
    quoted on de-rated capacity at the library's 2 h reference bin, so it is divided back out to a
    rate on rated capacity before the volume's own de-rating is applied. The old pin assumed the
    consented rate was multiplied by the deliverable MW with no re-basing.

    The FIX 5 uplift is a uniform 1/0.2198 = 4.55x, at every duration, because the divisor is the
    fixed reference bin and not the candidate's own. (The commit body's table stated it as "4.55x
    at 0.5 h" against an "unchanged" 2 h, which compares two different units; the ratio is
    duration-independent.)

    The ORIGINAL purpose of this test survives untouched: the volume-basis change is about
    IMPORTING the CM factor into BM and DC/DM/DR, never about the Capacity Market itself, for which
    the duration de-rating IS the correct basis (it is the scaled EFC the capacity-adequacy auction
    pays on). So no refusal label may appear on a CM entry at any size.
    """
    reference_derate, _ = value_for_duration(
        LIB.capacity_market["de_rating_factor_by_duration_h"].value, 2.0
    )
    assert reference_derate is not None

    site = _site(
        make_site,
        consents=(_consent(MarketStream.CAPACITY_MARKET, 50.0, RateUnit.GBP_PER_KW_YEAR),),
        mic_kw=4_000.0,
    )
    floor, upside, _ = build_value_streams(
        _schedule(power_kw=4_000.0, deliverable_mw=2.0),
        site,
        LIB,
        context=vmod_gate_context(),
    )
    cm = _entry((floor, upside), MarketStream.CAPACITY_MARKET)

    # The literal, so a drift fails with a readable number ...
    assert cm.annual_revenue_gbp == pytest.approx(454_959.05, abs=0.01)
    # ... and the reconciliation, so the literal cannot be re-pinned without the mechanism holding.
    assert cm.annual_revenue_gbp == pytest.approx(
        (50.0 / reference_derate) * 2.0 * 1000.0, abs=0.01
    )
    # The re-basing is STATED on the record with its confirmation ask (D56 rule 4 / D57 limb 4).
    assert any("RE-BASED TO RATED CAPACITY" in w for w in cm.warnings)
    assert any("CONFIRMATION ASK" in w for w in cm.warnings)
    # And the retired BM/ancillary refusal never touches the Capacity Market.
    assert not any(w.startswith(CM_DERATING_BASIS_REFUSAL) for w in cm.warnings)


def vmod_gate_context():
    """A deliverability-confirmed context, so the CM case above isolates the basis question."""
    from gridq.value_stack import GateContext

    return GateContext(deliverability_confirmed=True)


def test_two_runs_of_the_priced_stream_are_byte_identical(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """WAS ``test_two_runs_of_the_refusal_are_byte_identical`` - renamed, not weakened.

    The refusal it named no longer exists, and a test whose name describes a retired mechanism is a
    trap for the next reader. The property is unchanged and still worth holding: the notes are
    assembled from sourced values, never from a clock or an unordered set.
    """
    first, _, _ = _priced(make_site, BM, power_kw=4_000.0)
    second, _, _ = _priced(make_site, BM, power_kw=4_000.0)
    assert list(first.warnings) == list(second.warnings)
    assert first.annual_revenue_gbp == second.annual_revenue_gbp
