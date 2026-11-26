"""A record that selected no system must say something TRUE about why its streams read 0.00.

**The defect.** Every no-spec record wrote ``"no dispatch solved - value unavailable (not
invented)."`` on every stream. On all eight live campaign sites both clauses were false: dispatch
WAS solved (162 candidates, every one optimal) and on six of them it also RAN and produced value.
What did not happen is SELECTION. So the record told a reader the value was unavailable when it had
been measured -- the same null-versus-measured-zero confusion D45 exists to prevent, one layer up,
and the exact distinction the 2026-08-15 Tanhouse investigation had to re-derive by hand.

**Why this file exists rather than more unit tests.** The prior fix in this area was correct,
tested, and reached from nowhere: `valuation._zero_dispatch_note` fires inside the
``schedule is not None`` branch, and a no-spec run takes the ``schedule is None`` branch, so it
could never run on a real campaign record. That is the NINTH instance of built-tested-never-called
here. Both tests below therefore drive `generate_quote` END TO END and assert on the
``OptimisationResult`` **production actually returns** -- never a `DispatchResult`, never a
hand-built schedule, never the note builder called directly.

Production call sites:
- `MilpEngine._no_spec_dispatch_evidence` invoked from `MilpEngine._no_spec_result`'s two
  evidence-bearing exits (`optimisation/engine.py`, the ``no_viable_configuration`` exit and the
  undeclared-capacity exit), reached from `generate_quote` with no test-only code.
- `valuation.build_value_streams(..., no_spec_evidence=...)` -> `valuation._no_spec_notes`.
"""

from __future__ import annotations

import re

import pytest

from _milp_support import multi_rate_site, write_hh_csv
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.optimisation import MilpEngine
from gridq.optimisation.valuation import VALUE_UNAVAILABLE_MARKER, ZERO_DISPATCH_LABEL
from gridq.outputs import viewmodel as vmod
from gridq.quotes import generate_quote
from gridq.schemas import MarketStream, SelectionStatus
from gridq.schemas.tariff import TariffStack

RELEASE = "gb_v1_8"


def _arbitrage_warnings(result) -> list[str]:
    """The TARIFF_ARBITRAGE stream's warnings, off the REPORTED OptimisationResult."""
    stream = next(v for v in result.value_streams if v.stream == MarketStream.TARIFF_ARBITRAGE)
    return list(stream.warnings)


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions(RELEASE)


def _site(tmp_path_factory, name: str, tariff: TariffStack):
    path = tmp_path_factory.mktemp(name) / "hh.csv"
    rows, _ts = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows, site_id=f"site_{name}")
    return site.model_copy(update={"tariff_stack": tariff})


@pytest.fixture(scope="module")
def zero_dispatch_run(tmp_path_factory, assumptions):
    """No candidate is viable AND the optimiser declined every cycle (the Tanhouse/Colnbrook shape).

    A 0.5p spread against a 3p levy stack and a 2.13p/kWh cycle cost: margin -0.052464 GBP/kWh, so
    the dispatch is empty. Same construction as `tests/test_no_spec_model_disclosures.py`.
    """
    site = _site(
        tmp_path_factory,
        "zerodispatch",
        TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="MilpTestCo",
            import_rates={"day": 0.30, "night": 0.295},
            duos_red_rate=0.001,
            levies={"ro": 0.02, "fit": 0.01},
        ),
    )
    return generate_quote(site, assumptions, engine=MilpEngine())


@pytest.fixture(scope="module")
def dispatched_run(tmp_path_factory, assumptions):
    """No candidate is viable BUT the dispatch ran hard (the other six campaign sites' shape).

    A 5p spread plus a 2p DUoS red band clears the round-trip hurdle, so candidates discharge real
    energy and still fail on economics. This is the COMMON case and it needs its own wording: a
    record saying "no dispatch solved" here is false in a different way from the zero case.
    """
    site = _site(
        tmp_path_factory,
        "dispatched",
        TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="MilpTestCo",
            import_rates={"day": 0.30, "night": 0.25},
            duos_red_rate=0.02,
            levies={"ro": 0.02, "fit": 0.01},
        ),
    )
    return generate_quote(site, assumptions, engine=MilpEngine())


def test_the_fixtures_really_are_the_two_no_spec_branches(zero_dispatch_run, dispatched_run):
    """Guard both premises: if either fixture selects a spec, every assertion below goes vacuous."""
    for label, run in (("zero-dispatch", zero_dispatch_run), ("dispatched", dispatched_run)):
        assert run.result.selection_status == SelectionStatus.NO_VIABLE_CONFIGURATION, (
            f"the {label} fixture no longer reaches the economics verdict: "
            f"{run.result.selection_status}"
        )
        assert run.result.selected_power_kw is None, f"the {label} fixture selected a spec"


def test_a_no_spec_record_never_claims_no_dispatch_was_solved(zero_dispatch_run, dispatched_run):
    """THE regression: the false sentence must not appear on a record whose candidates solved.

    This is the sentence all eight live sites carried. Delete either ``no_spec_evidence=`` argument
    in `MilpEngine._no_spec_result`'s exits and it comes straight back.
    """
    for label, run in (("zero-dispatch", zero_dispatch_run), ("dispatched", dispatched_run)):
        warnings = _arbitrage_warnings(run.result)
        assert not any("no dispatch solved" in w for w in warnings), (
            f"the {label} record still claims no dispatch was solved, on a run whose candidates "
            f"were all solved and priced: {warnings}"
        )


def test_zero_dispatch_record_reports_a_measured_zero_with_its_arithmetic(zero_dispatch_run):
    """Candidates solved, none selected, none dispatched on the tariff: the 0.00 is MEASURED.

    D62 limb 1 (2026-11-22). DFS is in every objective (D57, 205ed92), so the best candidate
    (350 kW / 3 h on gb_v1_8) discharges 34,414.286 kWh/yr, ALL of it inside the DFS-called
    periods and 0.0 outside them. The record states the tariff decision on the discharge OUTSIDE
    those periods and states the DFS delivery separately, as a positive MWh.
    """
    warnings = _arbitrage_warnings(zero_dispatch_run.result)
    note = next((w for w in warnings if w.startswith(ZERO_DISPATCH_LABEL)), None)
    assert note is not None, f"no decision note on the reported result: {warnings}"
    assert "DISCHARGED NOTHING OUTSIDE THE DFS-CALLED PERIODS" in note
    delivered = re.search(r"delivered ([\d,]+\.\d{3}) MWh/yr inside the DFS-called periods", note)
    assert delivered is not None, f"the DFS delivery is not stated separately: {note}"
    assert float(delivered.group(1).replace(",", "")) > 0.0
    assert "candidate(s) were solved and priced" in note
    # The arithmetic, so a reader can audit the verdict instead of taking it on trust.
    assert "a cycle needs a spread of 0.06562 GBP/kWh" in note
    assert "offers is 0.00600 GBP/kWh" in note
    assert "Net -0.052464 GBP/kWh" in note
    # A measured zero is NOT an absent value, so the absence marker must be gone.
    assert VALUE_UNAVAILABLE_MARKER not in note, (
        "a measured 0.00 still carries the absence marker, so `viewmodel.value_measured` will keep "
        "reporting the most thoroughly measured figure on the record as unavailable"
    )


def test_dispatched_record_says_dispatch_ran_and_that_the_failure_was_economic(dispatched_run):
    """The COMMON case: dispatch ran and produced value; only selection failed."""
    warnings = _arbitrage_warnings(dispatched_run.result)
    note = next((w for w in warnings if "DISPATCH WAS SOLVED AND RAN" in w), None)
    assert note is not None, f"no dispatched-but-not-selected note on the record: {warnings}"
    assert "kWh/yr" in note and "DISCHARGED" in note
    assert ZERO_DISPATCH_LABEL not in note, (
        "a dispatching site was described as dispatching nothing"
    )
    # Here the per-candidate value genuinely IS unavailable: the record reports no candidate.
    assert VALUE_UNAVAILABLE_MARKER in note


def test_the_notes_reach_the_quote_the_renderers_read(zero_dispatch_run, dispatched_run):
    """A record entry no renderer sees is the same defect one layer further out.

    `outputs.viewmodel` builds its stream rows from the quote's value streams, so this is the step
    between "on the OptimisationResult" and "in front of the reader".
    """
    for run in (zero_dispatch_run, dispatched_run):
        streams = run.quote.optimisation_result.value_streams
        stream = next(v for v in streams if v.stream == MarketStream.TARIFF_ARBITRAGE)
        assert stream.warnings, "the quote record carries no explanation for the 0.00"
        assert not any("no dispatch solved" in w for w in stream.warnings)


def test_value_measured_now_distinguishes_the_two_records(zero_dispatch_run, dispatched_run):
    """The viewmodel's own question, answered correctly for the first time.

    `StreamRow.value_measured` keys on the absence marker. Before this change every no-spec record
    carried the marker on every stream, so it was permanently False -- including on the two sites
    whose 0.00 is the most thoroughly measured figure they report.
    """
    zero_note = next(
        w
        for w in _arbitrage_warnings(zero_dispatch_run.result)
        if w.startswith(ZERO_DISPATCH_LABEL)
    )
    dispatched_note = next(
        w for w in _arbitrage_warnings(dispatched_run.result) if "DISPATCH WAS SOLVED AND RAN" in w
    )
    assert vmod._VALUE_UNAVAILABLE not in zero_note, "a measured zero reads as unavailable"
    assert vmod._VALUE_UNAVAILABLE in dispatched_note, "an unreported candidate reads as measured"


def test_the_absence_marker_is_one_contract_across_the_two_modules():
    """`outputs` duplicates the marker rather than importing it; pin them so they cannot drift.

    The producer (`optimisation.valuation`) decides when the condition holds; the reader
    (`outputs.viewmodel`) matches on it. Keeping the string in two places avoids an
    outputs -> optimisation dependency, which makes this equality the only thing holding them.
    """
    assert vmod._VALUE_UNAVAILABLE == VALUE_UNAVAILABLE_MARKER


def test_the_new_notes_carry_no_em_or_en_dashes(zero_dispatch_run, dispatched_run):
    """These strings reach ``quote.warnings`` and report.html, which does not sanitise."""
    for run in (zero_dispatch_run, dispatched_run):
        for warning in _arbitrage_warnings(run.result):
            assert "—" not in warning and "–" not in warning, (
                f"a dash that report.html will not sanitise reached the record: {warning[:160]}"
            )
