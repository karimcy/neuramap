"""The private-wire DUoS absence is recorded on the REAL site records, so the branch that reads it
actually fires — and the pack stops asking a private-wire customer for a CDCM rate.

## The refutation this closes (R1, MAJOR, 2026-08-17)

`value_stack/gb_streams.py` carries a branch that states the structural truth about a 100%
private-wire supply: DUoS red-band avoidance is GBP 0 *because no distribution band applies*, not
because a rate is missing, so **no data ask is made**. It is keyed on

    gates.evidenced_absences(site_model).get("tariff_stack.duos_red_rate")

returning a `not_applicable` `DerivedAssumption`. An adversarial verifier established that **zero
persisted site records carried any `not_applicable` record at all**: Tanhouse and Colnbrook each
carried a `derived_input` with `value=None` on that key, which `evidenced_absences` deliberately
does not match (a value derived offline and never written back leaves the engine reading the same
raw `None`, so its ask must stay hard). The branch was therefore unreachable in production, and
Tanhouse's pack still printed

    tariff_stack.duos_red_rate (declared, reads NULL - D45: a null is not a measured zero)

as a data ask -- a request for a distribution rate no supplier could ever issue that customer.

**The fix is in the DATA, not the branch.** `examples/real/sites/tanhouse.json` and
`colnbrook.json` now record all three DUoS bands as `not_applicable`, which is what both files'
own `tariff_stack.source` prose has said in full since gb_v1_8.

## Why the acceptance runs where it does

`docs/validation_requirements.md` section 3: eleven defects have shipped built, tested, merged and
never called, every one with a passing test, because each test called the new code directly. So
this module asserts on the **real site records on disk** through the **production** valuation
function, never on a synthetic fixture:

- production call sites: `value_stack/gb_streams.py:946`
  (`DuosRedBandAvoidanceStream.check_gates`, the structural sentence) and
  `value_stack/gates.py:671` (`enforce_declared_inputs`, the data ask);
- reached from `cli.py` -> `MilpEngine.optimise` -> `optimisation/milp_solver.py`
  (`build_value_streams`) -> `optimisation/valuation.py` (`stream.check_gates`);
- deleting that read leaves `test_the_structural_reason_reaches_the_real_site_result` and
  `test_no_cdcm_rate_is_asked_of_a_private_wire_customer` RED;
- deleting the three records from either site file leaves the same two tests RED, plus
  `test_both_private_wire_sites_record_all_three_bands_as_not_applicable`.

`tests/test_evidenced_absence_reaches_the_data_ask.py` is the mechanism's own acceptance and stays
on synthetic sites by design (it must be able to construct both classes). This module is the
complement it lacked: proof that a **shipped** record reaches the branch.

## What must NOT move, and it is asserted rather than asserted-in-prose

GBP 0 is the correct number for both sites and it stays GBP 0.
`test_the_records_move_no_gbp_no_gate_and_no_firmness` prices each real site twice -- once as
shipped, once with the three records stripped back out -- and requires the revenue, the gate
status and the firmness to be identical, with only the disclosure text differing. A relabelled
record must never buy a stream its way into the priced floor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gridq.schemas as s
from gridq.assumptions import AssumptionLibrary, latest_version, load_assumptions
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas.market import MarketStream
from gridq.value_stack.interface import GateStatus
from gridq.value_stack.registry import default_registry

#: The release is resolved from disk exactly as `cli.py` resolves it (MD-F-15). The branch under
#: test reads the SITE record and no library value, so it is release-independent -- and pinning to
#: `latest_version()` means a new release cut cannot quietly retire this coverage.
LIB: AssumptionLibrary = load_assumptions(latest_version())

SITES_DIR = Path(__file__).resolve().parent.parent / "examples" / "real" / "sites"

#: The two 100% private-wire supplies (EfW private wire; Tanhouse also has on-site solar). Both are
#: recorded as fully non-grid in `docs/grundon_harwell/01_data_foundation.md` section 3.2.
PRIVATE_WIRE = ("tanhouse", "colnbrook")

BAND_KEYS = (
    "tariff_stack.duos_red_rate",
    "tariff_stack.duos_amber_rate",
    "tariff_stack.duos_green_rate",
)

#: Written by the structural branch at `gb_streams.py:946-960`. Distinctive enough that a hit can
#: only come from that branch reading a `not_applicable` record off the site.
STRUCTURAL_FRAGMENT = "for a STRUCTURAL reason, not a missing input"
NOT_APPLICABLE_FRAGMENT = "records tariff_stack.duos_red_rate as NOT "
NO_ASK_FRAGMENT = "NO DATA ASK IS MADE"

#: The generic ask the guard writes for a key nobody has sourced (`gates._asks`). This is the
#: sentence that was reaching a private-wire customer, and it must be gone from both sites.
UNSOURCED_ASK_FRAGMENT = "declared, reads NULL"

#: The "go and source it" closing sentence of the D45 note (`gates.enforce_declared_inputs`).
SOURCE_IT_FRAGMENT = "Resolve it OFFLINE"

#: The closing sentence the all-absences branch writes instead.
NOTHING_TO_SOURCE_FRAGMENT = "Every null above is an EVIDENCED ABSENCE"


def _raw(site_id: str) -> dict[str, object]:
    return json.loads((SITES_DIR / f"{site_id}.json").read_text())


def _site(site_id: str) -> s.SiteModel:
    """The shipped record, validated exactly as the CLI validates it."""
    return s.SiteModel.model_validate(_raw(site_id))


def _duos(site: s.SiteModel) -> s.ValueStreamResult:
    """The DUoS stream as the PRODUCTION valuation function returns it."""
    floor, gated, _used = build_value_streams(None, site, LIB)
    return next(r for r in (*floor, *gated) if r.stream is MarketStream.DUOS_RED_BAND_AVOIDANCE)


def _gate_text(site: s.SiteModel) -> str:
    """Every reader-facing string the DUoS gate produces for this site, warnings and asks."""
    stream = next(
        st
        for st in default_registry().all()
        if st.stream_id is MarketStream.DUOS_RED_BAND_AVOIDANCE
    )
    gate = stream.check_gates(site, LIB, None)
    return " ".join([*gate.warnings, *gate.missing_requirements])


def _without_absences(site_id: str) -> s.SiteModel:
    """The same real site with the three evidenced-absence records taken back out.

    Reconstructs the pre-fix shape: the red band as a valueless `derived_input` (what the file
    carried before this change) and no amber/green record at all.
    """
    raw = _raw(site_id)
    records = [
        r
        for r in (raw.get("derived_assumptions") or [])
        if not (isinstance(r, dict) and r.get("key") in BAND_KEYS)
    ]
    raw["derived_assumptions"] = records
    return s.SiteModel.model_validate(raw)


@pytest.mark.parametrize("site_id", PRIVATE_WIRE)
def test_both_private_wire_sites_record_all_three_bands_as_not_applicable(site_id: str) -> None:
    """The data fix itself, on the file the run reads.

    All three bands, because `evidenced_absences` matches the EXACT dotted key: a record on the red
    band says nothing the guard may read about amber and green, and inferring the rest of the stack
    from one record would be a derivation the guard is forbidden to make.
    """
    site = _site(site_id)
    by_key = {r.key: r for r in site.assumptions_requiring_confirmation}

    for key in BAND_KEYS:
        record = by_key.get(key)
        assert record is not None, f"{site_id}: no register record on {key}"
        assert record.kind == "not_applicable", (
            f"{site_id}: {key} is recorded as {record.kind!r}. Only `not_applicable` reaches "
            "gb_streams.py:946, so any other class puts the CDCM data ask back in front of a "
            "private-wire customer."
        )
        # The class is valueless by construction; asserted here because the whole point is that no
        # rate is asserted about a site that faces none.
        assert record.value is None and record.unit is None
        assert record.report_label == "does not apply to this site, evidenced"

    # The record is only honest while the field really is empty.
    tariff = site.tariff_stack
    assert tariff is not None
    assert tariff.duos_red_rate is None
    assert tariff.duos_amber_rate is None
    assert tariff.duos_green_rate is None


@pytest.mark.parametrize("site_id", PRIVATE_WIRE)
def test_the_structural_reason_reaches_the_real_site_result(site_id: str) -> None:
    """The reachability acceptance: the branch fires on the shipped record, and the GBP is 0.

    Deleting the `gates.evidenced_absences(site_model)` read at `gb_streams.py:946`, or the records
    from the site file, leaves this RED.
    """
    result = _duos(_site(site_id))
    joined = " ".join(result.warnings)

    assert STRUCTURAL_FRAGMENT in joined, (
        f"{site_id}: the structural branch did not fire. Either the site record no longer carries "
        "a `not_applicable` record on tariff_stack.duos_red_rate, or the branch that reads it is "
        "gone."
    )
    assert NOT_APPLICABLE_FRAGMENT in joined
    assert NO_ASK_FRAGMENT in joined

    # The number is the point of the refusal: 0 because the charge does not exist, and OUT of the
    # priced floor rather than priced at zero.
    assert result.annual_revenue_gbp == 0.0
    assert result.gate_status == GateStatus.CLOSED.value
    assert result.firmness is s.Firmness.EXCLUDED


@pytest.mark.parametrize("site_id", PRIVATE_WIRE)
def test_no_cdcm_rate_is_asked_of_a_private_wire_customer(site_id: str) -> None:
    """The reader-facing half: the ask becomes CONFIRM the absence, never send us the rate."""
    text = _gate_text(_site(site_id))

    assert UNSOURCED_ASK_FRAGMENT not in text, (
        f"{site_id}: the pack still asks a 100% private-wire customer for a DUoS rate no supplier "
        "could issue them. That ask is the defect R1 refuted."
    )
    assert SOURCE_IT_FRAGMENT not in text, (
        f"{site_id}: the D45 note still says to go and source these rates. There is nothing to "
        "source: the component does not exist for this supply."
    )
    assert NOTHING_TO_SOURCE_FRAGMENT in text
    assert "does not apply to this site, evidenced" in text


@pytest.mark.parametrize("site_id", PRIVATE_WIRE)
def test_the_records_move_no_gbp_no_gate_and_no_firmness(site_id: str) -> None:
    """GBP 0 is the correct number and it stays GBP 0. Only the disclosure changes.

    The tempting version of this fix -- let an evidenced absence open the gate -- would put a
    stream into the priced floor on the strength of a **relabelled record**, which is the direction
    that needs new evidence rather than a new label.
    """
    shipped = _duos(_site(site_id))
    before = _duos(_without_absences(site_id))

    assert shipped.annual_revenue_gbp == before.annual_revenue_gbp == 0.0
    assert shipped.annual_cost_gbp == before.annual_cost_gbp
    assert shipped.gate_status == before.gate_status == GateStatus.CLOSED.value
    assert shipped.firmness is before.firmness is s.Firmness.EXCLUDED

    # ...and the text is the only thing that did move.
    assert STRUCTURAL_FRAGMENT not in " ".join(before.warnings)
    assert UNSOURCED_ASK_FRAGMENT in _gate_text(_without_absences(site_id))


def test_only_the_private_wire_pair_records_a_duos_absence() -> None:
    """Scope guard: an evidenced absence may only ever describe a site with no band at all.

    A `not_applicable` record on a grid-supplied site would silence a REAL data ask and hold a real
    stream out of the floor with a structural excuse, which is the inverse defect. Every other
    shipped record carries all three band rates, so it can never take this branch.
    """
    for path in sorted(SITES_DIR.glob("*.json")):
        site_id = path.stem
        raw = json.loads(path.read_text())
        absent_keys = {
            r.get("key")
            for r in (raw.get("derived_assumptions") or [])
            if isinstance(r, dict) and r.get("kind") == "not_applicable"
        }
        band_absences = absent_keys & set(BAND_KEYS)
        tariff = raw.get("tariff_stack") or {}

        if site_id in PRIVATE_WIRE:
            assert band_absences == set(BAND_KEYS), f"{site_id}: expected all three band absences"
            continue

        assert not band_absences, (
            f"{site_id} records a DUoS band as not_applicable. Only a supply that faces no "
            "distribution band may do that; on a DNO-connected site it would suppress a genuine "
            "data ask."
        )
        assert tariff.get("duos_red_rate") is not None, (
            f"{site_id}: DUoS red rate reads NULL with no evidenced-absence record. Either the "
            "rate is missing (a D45 defect to fix in the data) or the site is private wire and "
            "belongs in PRIVATE_WIRE above."
        )
