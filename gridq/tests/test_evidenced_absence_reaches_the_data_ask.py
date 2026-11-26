"""The D45 null-input guard learns the third register class, and the ask stops being wrong.

`schemas.derived` grew a `not_applicable` class and `quotes.confidence` counts it, so the
**confidence factor** already tells a reader which register entries are evidenced absences. The
guard on the gate path did not, and that is the half a customer actually receives: on every run,
`value_stack.gates.enforce_declared_inputs` wrote

    tariff_stack.duos_red_rate (declared, reads NULL - D45: a null is not a measured zero)

into the missing-requirements list of a 100% private-wire site, which is a data ask for a
distribution rate **no supplier could ever issue that customer**. Colnbrook's own register record
says, in its own prose, "NO DISTRIBUTION CHARGE APPLIES TO THIS SITE, AND THE ZERO IS EVIDENCED
RATHER THAN MISSING". The pack asked for it anyway, because the guard read the field and never the
register.

## What this is and is not

**It is a disclosure change and moves no number, and that is pinned rather than asserted in
prose.** A blocking null still closes the gate, the stream is still EXCLUDED, still outside
`finance.cashflows._PRICED_FIRMNESS`, still outside the priced floor, P50, P90 and the DSCR, and
still GBP 0. `test_an_evidenced_absence_moves_no_gbp_no_gate_and_no_firmness` compares the two
sites directly, because the tempting version of this change - let the evidenced absence open the
gate - would put a stream into the floor on the strength of a **relabelled record**, which is the
direction that needs new evidence rather than a new label.

**It is narrow on purpose.** Only `kind == "not_applicable"` and only an exact key match soften the
ask. A `derived_input` with `value=None` on the same key - which is what Colnbrook and Tanhouse both
carried until 2026-08-17, and the reason the branch never fired on a real run (refutation R1) - is a
value derived offline and never written back, so the engine reads the same raw `None` and its ask
must stay hard: `test_a_valueless_derived_input_keeps_the_hard_ask` is what stops a future edit
widening the rule to "any record on the key". Both site files now carry the `not_applicable` class
on all three bands, pinned by `tests/test_private_wire_duos_absence_on_real_sites.py`.

## Why the acceptance runs where it does

`docs/validation_requirements.md` section 3 records **eleven** defects built, tested, merged and
never called, every one with a passing test, because each test called the new code directly. So:

- the structural acceptance runs through `optimisation.valuation.build_value_streams`, the
  production function `optimisation.milp_solver` calls (reached from `cli.py` ->
  `MilpEngine.optimise`), and asserts on the returned `ValueStreamResult`;
- the reader acceptance publishes that **same real result** through `quotes.persistence.publish_run`
  and asserts on the bytes of `report.html` and `consulting_report.html`, because the sentence only
  matters where a customer reads it.

Deleting the `evidenced_absences(site)` read in `value_stack/gates.py` leaves both red.

**No em dash or en dash is written into any new production string here** (`report.html` is not
sanitised at document level), and the rendered-bytes test checks for the two characters directly.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from html import unescape
from pathlib import Path

import numpy as np
import pytest

import gridq.schemas as s
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.valuation import build_value_streams
from gridq.quotes.persistence import publish_run
from gridq.value_stack.gates import D45_NULL_INPUT_LABEL
from gridq.value_stack.interface import GateResult, GateStatus
from gridq.value_stack.registry import default_registry

LIB: AssumptionLibrary = load_assumptions("gb_v1_8")

DUOS_KEY = "tariff_stack.duos_red_rate"

#: The record's own ask, and the whole point of the change: this is what a private-wire customer
#: can actually answer. Distinctive enough that a hit in a rendered document can only be this.
PRIVATE_WIRE_ASK = (
    "Supply the private wire supply contract and one full invoice showing every line item."
)

#: The generic ask the guard writes for a key nobody has sourced. Quoted from `gates._asks`.
UNSOURCED_ASK_FRAGMENT = "declared, reads NULL"

_EM_DASH = chr(0x2014)
_EN_DASH = chr(0x2013)


def _absence_record(key: str = DUOS_KEY) -> dict[str, object]:
    """The Colnbrook shape: a DUoS line that does not exist because the site is private wire."""
    return {
        "kind": "not_applicable",
        "key": key,
        "missing_input": "the site's DNO distribution charge basis",
        "basis": "No distribution charge applies: the supply is 100% private wire, so the site is "
        "not supplied through the DNO assets DUoS recovers, and the non-commodity cost it does pay "
        "is already in the stack as a back-solved residual. Adding a rate would double-count it.",
        "derivation_method": "not_derivable",
        "derived_by": "gridq.tests",
        "confidence_ceiling": "medium",
        "confirmation_ask": PRIVATE_WIRE_ASK,
    }


def _unsourced_record(key: str = DUOS_KEY) -> dict[str, object]:
    """What Colnbrook carries TODAY: valueless, but classed as a derived fact about the site."""
    record = _absence_record(key)
    record["kind"] = "derived_input"
    return record


def _all_bands() -> list[dict[str, object]]:
    """The whole DUoS stack recorded as absent, which is the real private-wire shape.

    A site with no distribution connection faces no band at all, so it records all three. Recording
    only the red band leaves amber and green unsourced, and the guard is right to keep asking about
    those: `test_a_record_on_another_key_softens_nothing` pins that half.
    """
    return [
        _absence_record(DUOS_KEY),
        _absence_record("tariff_stack.duos_amber_rate"),
        _absence_record("tariff_stack.duos_green_rate"),
    ]


def _site(make_site: Callable[..., s.SiteModel], *records: dict[str, object]) -> s.SiteModel:
    """A site with a NULL red band rate and ``records`` on its register, built through the real
    validation path."""
    dump = make_site().model_dump(mode="json")
    dump["tariff_stack"]["duos_red_rate"] = None
    dump["derived_assumptions"] = list(records)
    return s.SiteModel.model_validate(dump)


def _schedule() -> HalfHourlyDispatchSchedule:
    """A synthetic solved schedule with real red-band discharge (the shape the guard test uses).

    Not a MILP solve: what has to be proved is that the gate's verdict and its ASK reach the
    production `ValueStreamResult`, and a synthetic schedule isolates that and keeps this fast.
    """
    n = 48
    discharge = np.zeros(n)
    discharge[12:24] = 100.0
    mask = np.zeros(n, dtype=bool)
    mask[12:24] = True
    zeros = np.zeros(n)
    return HalfHourlyDispatchSchedule(
        power_kw=200.0,
        energy_kwh=400.0,
        duration_hours=2.0,
        interval_hours=0.5,
        annual_factor=1.0,
        charge_kw=zeros,
        discharge_kw=discharge,
        soe_kwh=zeros,
        site_load_kw=np.full(n, 500.0),
        duos_rate_gbp_per_kwh=np.full(n, 0.1),
        red_band_mask=mask,
        energy_price_gbp_per_kwh=np.full(n, 0.25),
        deliverable_capacity_mw=0.2,
    )


def _duos(site: s.SiteModel) -> s.ValueStreamResult:
    """The DUoS stream as the production valuation path reports it."""
    streams, _, _ = build_value_streams(_schedule(), site, LIB)
    match = [v for v in streams if v.stream == s.MarketStream.DUOS_RED_BAND_AVOIDANCE]
    assert match, "the DUoS red-band stream must always be reported, priced or not"
    return match[0]


def _note(result: s.ValueStreamResult | GateResult) -> str:
    notes = [w for w in result.warnings if w.startswith(D45_NULL_INPUT_LABEL)]
    assert len(notes) == 1, f"expected exactly one D45 note, got {len(notes)}"
    return notes[0]


def _gate(site: s.SiteModel) -> GateResult:
    """The gate as the production stream reports it, for the ASK LIST specifically.

    The note reaches a reader on every path; the ask list does not, and that is a separate defect
    found while writing this and deliberately not fixed here (it is another lane's file):
    `optimisation.valuation._result` copies ``gate.warnings`` onto the `ValueStreamResult` and
    **drops** ``gate.missing_requirements``, and nothing in `outputs/` or `quotes/` reads that field
    at all, so on a solved MILP run the data asks never reach a document. That is why the ask-list
    assertions below are the SUPPORTING half and the note assertions are the acceptance: the note
    is what a customer actually receives, and the note is where the wrong instruction lived.
    """
    return default_registry().get(s.MarketStream.DUOS_RED_BAND_AVOIDANCE).check_gates(site, LIB)


def _ask_text(site: s.SiteModel) -> str:
    return " ".join(_gate(site).missing_requirements)


# ============================================================ acceptance: the production gate path


def test_an_evidenced_absence_is_asked_to_be_CONFIRMED_not_supplied(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """**The defect, at the layer that writes the sentence a customer receives.**

    Production call site: ``gridq.value_stack.gates.enforce_declared_inputs`` reads
    ``evidenced_absences(site)`` and passes it to ``_asks``, reached from ``cli.py`` ->
    ``MilpEngine.optimise`` -> ``optimisation.valuation.build_value_streams`` ->
    ``ValueStream.check_gates`` -> ``streams._guard_check_gates`` (``value_stack/streams.py:113``).
    Replace the register read with an empty dict and this goes red on every assertion below.
    """
    site = _site(make_site, *_all_bands())
    result = _duos(site)

    note = _note(result)
    assert "EVIDENCED ABSENCE" in note
    assert "the component does not exist for this site" in note
    assert "no rate to supply and none is asked for" in note
    assert "there is nothing to source and no rate to ask for" in note

    asks = _ask_text(site)
    assert PRIVATE_WIRE_ASK in asks, (
        "the ask a private-wire customer receives must be the register's own confirmation ask, "
        "which they can answer, and not a request for a rate no supplier could issue them"
    )
    assert "CONFIRM the absence" in asks
    assert UNSOURCED_ASK_FRAGMENT not in asks, (
        "'declared, reads NULL' is the exact line that sent a DUoS data ask to a site with no "
        "distribution connection"
    )
    assert "Resolve it OFFLINE" not in note, (
        "there is nothing to resolve offline: the component does not exist, so instructing anyone "
        "to go and source it is the same wrong ask in the guard's own closing paragraph"
    )


def test_an_evidenced_absence_moves_no_gbp_no_gate_and_no_firmness(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """**The direction-of-effect pin: relabelling a record must never price a stream.**

    The tempting version of this change is to let an evidenced absence open the gate, since the
    site "has no missing data". That would move DUOS_RED_BAND_AVOIDANCE from EXCLUDED into the
    priced floor on the strength of a relabelled record, with the engine still reading a raw
    ``None`` for the rate. Every number here is identical to the unsourced case; only the words
    differ.
    """
    absence = _duos(_site(make_site, *_all_bands()))
    unsourced = _duos(_site(make_site, _unsourced_record()))

    assert absence.gate_status == unsourced.gate_status == GateStatus.CLOSED.value
    assert absence.firmness == unsourced.firmness == s.Firmness.EXCLUDED
    assert absence.annual_revenue_gbp == unsourced.annual_revenue_gbp == 0.0
    assert absence.annual_cost_gbp == unsourced.annual_cost_gbp


def test_a_valueless_derived_input_keeps_the_hard_ask(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """**The case the narrowness protects, and it is live rather than hypothetical.**

    `examples/real/sites/colnbrook.json` carried a `derived_input` with ``value=None`` on this exact
    key until 2026-08-17, which is precisely why the branch never fired in production (refutation
    R1); it is now `not_applicable`, and the shape below is kept because any future record may
    arrive in the weaker class. A `derived_input` whose value was never written back onto the
    SiteModel leaves
    the engine reading the same raw ``None`` (`optimisation.tariff_calendar` reads the field
    directly), so softening its ask would restore the silent GBP 0 the guard exists to prevent.
    Only the class that says "this component does not exist" may change the sentence.
    """
    site = _site(make_site, _unsourced_record())
    result = _duos(site)

    note = _note(result)
    assert "EVIDENCED ABSENCE" not in note
    assert "not a measured zero" in note
    assert "Resolve it OFFLINE" in note
    assert UNSOURCED_ASK_FRAGMENT in _ask_text(site)
    assert PRIVATE_WIRE_ASK not in _ask_text(site)


def test_a_record_on_another_key_softens_nothing(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Exact key match, never a stem: one absence must not vouch for a whole tariff section.

    A record on ``tariff_stack.duos_amber_rate`` says nothing the guard may read about the red
    band. Inferring the rest of a stack from one record would be a derivation, and this guard
    derives nothing (D44/D45).
    """
    site = _site(make_site, _absence_record("tariff_stack.duos_amber_rate"))
    result = _duos(site)

    assert UNSOURCED_ASK_FRAGMENT in _ask_text(site), (
        "the red band is still unsourced: its ask must stay hard"
    )
    assert PRIVATE_WIRE_ASK not in _ask_text(site)
    note = _note(result)
    assert "not a measured zero" in note, "the blocking key keeps its own sentence"
    assert "Resolve it OFFLINE" in note, "something here really is unsourced"
    assert "EVIDENCED ABSENCE" in note, "the amber band's absence is still disclosed as one"
    assert "tariff_stack.duos_amber_rate also read(s) as NULL and are recorded" in note


def test_a_site_with_no_register_produces_the_note_it_always_did(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The change is conditional-additive: every site in the portfolio today is byte-identical.

    Verified directly against the pre-change module at the time of writing (the guard's output was
    compared against ``git show HEAD:src/gridq/value_stack/gates.py`` across all nine real site
    files and matched on every one). This keeps that property enforced.
    """
    result = _duos(_site(make_site))
    note = _note(result)

    assert note == (
        f"{D45_NULL_INPUT_LABEL} {_EM_DASH} DUOS_RED_BAND_AVOIDANCE: "
        "tariff_stack.duos_red_rate read(s) as NULL on this site. A null is not a measured zero: "
        "priced through, it would report £0 for this stream and that £0 would enter P50, "
        "P90 and the DSCR indistinguishable from a measured result. The stream is therefore held "
        "OUT of the priced floor rather than priced at zero. tariff_stack.duos_amber_rate, "
        "tariff_stack.duos_green_rate also read(s) as NULL. These do not block pricing, but an "
        "absent rate is consumed as zero downstream, which WIDENS the modelled spread against the "
        "declared bands and OVERSTATES this stream "
        f"{_EM_DASH} it does not zero it. "
        "Resolve it OFFLINE and record the value back onto the SiteModel with its provenance "
        f"(D44/D45): deriving a published GB value {_EM_DASH} a DNO CDCM tariff, a NESO de-rating "
        f"factor, an ENA formula {_EM_DASH} is SOURCING, not invention. Nothing is derived inside "
        "this guard, and no value is invented here. If the component genuinely does not apply to "
        "this site, record the evidenced 0.0 with the reason; the guard reads a declared zero as "
        "the fact it is."
    )


def test_the_new_sentences_carry_no_dash_characters(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """`report.html` is deliberately NOT sanitised at document level, so this is checked at source.

    The legacy sentences carry em dashes and are left exactly as they were; every sentence added
    for the evidenced-absence path must not, so nothing new reaches a browser with a dash the
    renderer has to rewrite.
    """
    note = _note(_duos(_site(make_site, *_all_bands())))
    # Split off the label prefix, which is legacy text and keeps its own dash.
    _, _, added = note.partition("DUOS_RED_BAND_AVOIDANCE:")

    assert "EVIDENCED ABSENCE" in added, "fixture sanity: these are the sentences under test"
    assert _EM_DASH not in added and _EN_DASH not in added


# ==================================================================== acceptance: the rendered pack


@pytest.fixture
def published(rich_quote, make_site: Callable[..., s.SiteModel], tmp_path: Path) -> dict[str, str]:
    """Publish a run carrying the REAL gate-produced DUoS stream, and read the documents off disk.

    The stream is not hand-built: it is the `ValueStreamResult` `build_value_streams` returns for a
    private-wire site, so a document assertion below can only pass if `gates.py` wrote the sentence.
    """
    duos = _duos(_site(make_site, *_all_bands()))
    result = rich_quote.result.model_copy(update={"value_streams": [duos], "gated_upside": []})
    quote = rich_quote.quote.model_copy(
        update={"quote_mode": s.QuoteMode.UNDERWRITTEN, "optimisation_result": result}
    )

    class _Run:  # the minimal duck publish_run reads: quote / site / telemetry
        pass

    run = _Run()
    run.quote = quote  # type: ignore[attr-defined]
    run.site = rich_quote.site  # type: ignore[attr-defined]
    run.telemetry = None  # type: ignore[attr-defined]

    directory = publish_run(run, root=tmp_path).persisted.directory  # type: ignore[arg-type]
    return {
        name: (directory / name).read_text(encoding="utf-8")
        for name in ("report.html", "consulting_report.html")
    }


def _flat(text: str) -> str:
    """Rendered markup as flowing text, so markup between words cannot defeat an assertion."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text)))


@pytest.mark.parametrize("document", ["report.html", "consulting_report.html"])
def test_the_pack_a_customer_reads_states_the_absence(
    published: dict[str, str], document: str
) -> None:
    """**The reader acceptance.** The sentence has to survive all the way to the rendered bytes.

    A confidence factor counted the class correctly while the document a customer opens still asked
    them for the rate. That gap is exactly the class of defect this repo has shipped eleven times,
    so the assertion is on the bytes.
    """
    flat = _flat(published[document])

    assert "EVIDENCED ABSENCE" in flat, (
        "the customer is never told the register calls this an evidenced absence"
    )
    assert "the component does not exist for this site" in flat
