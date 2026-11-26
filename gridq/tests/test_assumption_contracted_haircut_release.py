"""gb_v1_9: the CONTRACTED-firmness P90 haircut gets a declared home, and that home is not a zero.

WHAT THIS FILE GUARDS, and why each assertion exists.

W8 slice 3 shipped a consumer for ``p50_p90_haircuts.p90_revenue_haircut_contracted_pct`` against a
key **no release carried** — verified across all ten shipped releases, gb_v0_1 through gb_v1_8. The
consumer is ``gridq.finance.cashflows._contracted_haircut_pct`` (defined ``cashflows.py:1238``,
called on the production path at ``cashflows.py:1466`` inside ``build_cashflow_model``). Since the
slice landed, every run has therefore taken its fallback branch: contracted revenue shares the
MERCHANT haircut, loudly warned.

gb_v1_9 adds the key with a **null** value and ``status: needs_review``, because the search for a
GB-authoritative scalar failed and D45 as amended forbids inventing one to fill the hole (the
search itself is recorded on the key's ``source``). Adding an empty home is only worth doing if the
emptiness is honest, so this file pins the three things that make it honest:

1. **The home exists and says what would settle it** (``test_gb_v1_9_carries_...``). This is the
   MUTATION TARGET: delete the key from ``assumptions/gb_v1_9.yaml`` and this test fails.
2. **A null reads as the MERCHANT haircut, never as zero.** This is the whole risk of a dormant
   haircut key. 0% would mean contracted revenue is haircut not at all — i.e. treated as FIRM —
   which is a direct P90 and DSCR overstatement. The test pins the identity (null ≡ merchant)
   exactly, and separately measures how big the overstatement WOULD be if the branch ever changed.
3. **The key is genuinely reachable through the production builder**: populate it and the P90
   number MOVES by exactly the differential, the key enters ``assumptions_used``, the
   shares-the-merchant warning stops firing, and P50 does not budge (it is a P90-only instrument).

Deliberately NOT asserted: any claim that gb_v1_9 changes a priced figure. It does not, by design —
the null takes the same branch an absent key took, so gb_v1_9 is behaviourally identical to gb_v1_8.

Note on the mutation check: absent-key and null-value are indistinguishable by warning text (both
hit the same fallback branch and emit the same string), which is exactly why the mutation target
here is the LIBRARY CONTENT rather than the warning.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

import gridq.schemas as s
from gridq.assumptions import AssumptionLibrary, AssumptionStatus, load_assumptions
from gridq.finance.cashflows import build_cashflow_model

RELEASE = "gb_v1_9"
SECTION = "p50_p90_haircuts"
KEY = "p90_revenue_haircut_contracted_pct"
REF = f"{SECTION}.{KEY}"

#: The fallback branch's warning, verbatim enough to bind (``cashflows.py:1254-1259``).
_SHARES_MERCHANT = "shares the merchant P90 haircut"


# --- fixtures ---------------------------------------------------------------


def _result(streams: list[tuple[s.MarketStream, float, s.Firmness]]) -> s.OptimisationResult:
    return s.OptimisationResult(
        result_id="res_contracted_haircut",
        request_id="req_contracted_haircut",
        implementation_level=s.ImplementationLevel.BASELINE,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        selected_power_kw=200.0,
        selected_energy_kwh=400.0,
        duration_hours=2.0,
        value_streams=[
            s.ValueStreamResult(stream=st, annual_revenue_gbp=rev, firmness=firm)
            for st, rev, firm in streams
        ],
        solver_status=s.SolverStatus.BASELINE,
    )


#: Year-1 CONTRACTED revenue in the mixed fixture, on CAPACITY_MARKET (so the penalty line is live).
_CM_CONTRACTED = 9_000.0


def _mixed() -> s.OptimisationResult:
    """FIRM dispatch revenue + a CONTRACTED CM availability payment."""
    return _result(
        [
            (s.MarketStream.TARIFF_ARBITRAGE, 40_000.0, s.Firmness.FIRM),
            (s.MarketStream.DUOS_RED_BAND_AVOIDANCE, 10_000.0, s.Firmness.FIRM),
            (s.MarketStream.CAPACITY_MARKET, _CM_CONTRACTED, s.Firmness.CONTRACTED),
        ]
    )


#: Year-1 CONTRACTED revenue in the contracted-only fixture.
_LOAD_SERVICE_CONTRACTED = 20_000.0


def _contracted_only() -> s.OptimisationResult:
    """A single CONTRACTED stream and NO Capacity Market revenue.

    LOAD_SERVICE is chosen deliberately: it is not in ``GATED_UPSIDE_STREAMS`` (so CONTRACTED is
    legal without a consent basis), and with zero CM revenue ``_p90_penalty_exposure`` returns
    ``None``, which strips the penalty line out of the arithmetic and leaves the haircut as the
    ONLY thing separating the P50 and P90 series. That isolation is the point of the fixture.
    """
    return _result([(s.MarketStream.LOAD_SERVICE, _LOAD_SERVICE_CONTRACTED, s.Firmness.CONTRACTED)])


def _entry(value: float | None) -> dict:
    return {
        "value": value,
        "unit": "percent",
        "source": "test-only override",
        "status": "needs_review",
        "owner_or_reviewer": "test",
        "confidence": "low",
        "last_reviewed": str(date(2026, 8, 15)),
        "notes": "test-only override of the contracted P90 haircut",
    }


def _library_with(pct: float | None) -> AssumptionLibrary:
    """gb_v1_9 with the contracted haircut forced to ``pct`` — every other value untouched."""
    dumped = load_assumptions(RELEASE).model_dump(mode="json")
    dumped[SECTION][KEY] = _entry(pct)
    return AssumptionLibrary.model_validate(dumped)


def _merchant_pct(lib: AssumptionLibrary) -> float:
    return float(lib.get(SECTION, "p90_revenue_haircut_pct").value)


def _availability_factor(lib: AssumptionLibrary) -> float:
    """H-17's fleet de-rate, read from the library rather than hard-coded (gb_v1_9: 95.0%)."""
    return float(lib.get("availability", "fleet_availability_pct").value) / 100.0


# --- 1. the home exists, and it is honest about being empty -----------------
#
# MUTATION TARGET: remove `p90_revenue_haircut_contracted_pct` from assumptions/gb_v1_9.yaml and
# this test fails on the first assertion.


def test_gb_v1_9_carries_the_contracted_haircut_home_as_a_declared_null() -> None:
    lib = load_assumptions(RELEASE)
    assert KEY in lib.p50_p90_haircuts, (
        f"{RELEASE} must carry {REF}. Its consumer (cashflows.py:1238, called :1466) has been "
        "reading for this key since W8 slice 3 with no release carrying it."
    )
    a = lib.get(SECTION, KEY)
    # A null, not a number: no GB source was found and none is invented (D45 as amended).
    assert a.value is None
    assert a.unit == "percent"
    assert a.status == AssumptionStatus.NEEDS_REVIEW
    assert a.status != AssumptionStatus.CONFIRMED  # 0 confirmed repo-wide stays true

    # A null is admissible only if the entry records the failed search and what would settle it —
    # the same discipline gb_v1_8 enforces on its own remaining nulls.
    source = a.source.lower()
    assert "no citable gb source" in source
    assert "bess_constants_register" in source  # the register sweep that returned nothing
    assert "2026-08-15" in a.source  # the fresh search, dated
    notes = a.notes.lower()
    assert "what would settle it" in notes
    # The two constraints on any future value, both load-bearing:
    assert "double-count" in notes  # fleet unavailability is already in the same shortfall
    assert "fleet_availability_pct" in notes
    assert "d-225" in notes and "d-235" in notes  # a flat scalar is the wrong shape


def test_gb_v1_9_carries_every_other_gb_v1_8_value_forward_byte_identical() -> None:
    """Release-cut discipline: a new file, one new key, nothing else touched (D41 convention)."""
    old = load_assumptions("gb_v1_8").model_dump(mode="json")
    new = load_assumptions(RELEASE).model_dump(mode="json")
    assert old.pop("metadata")["version"] == "gb_v1_8"
    assert new.pop("metadata")["version"] == RELEASE
    assert new[SECTION].pop(KEY)["value"] is None
    assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True), (
        "gb_v1_9 must differ from gb_v1_8 by exactly the one new key and the metadata header"
    )


# --- 2. the null reads as the merchant haircut, and never as zero -----------


def test_the_null_reads_as_the_merchant_haircut_and_never_as_zero(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The defect this key is most exposed to: an empty haircut silently meaning 'no haircut'.

    Three runs on the same release, differing only in this key:
      * `shipped`  — gb_v1_9 as released (null),
      * `merchant` — the key forced to the merchant percentage,
      * `zero`     — the key forced to 0.0, i.e. contracted revenue haircut not at all.

    `shipped` must equal `merchant` EXACTLY (the null takes the fallback branch), and must differ
    from `zero` (proving the null is not silently read as 0%). The gap to `zero` is also measured,
    because it is the size of the P90 overstatement this test exists to prevent.
    """
    site, result = make_site(), _mixed()
    shipped = build_cashflow_model(site, result, load_assumptions(RELEASE))
    merchant = build_cashflow_model(
        site, result, _library_with(_merchant_pct(load_assumptions(RELEASE)))
    )
    zero = build_cashflow_model(site, result, _library_with(0.0))

    # (a) null ≡ merchant, across the WHOLE series, not just year 1.
    assert shipped.p90_ebitda_gbp == merchant.p90_ebitda_gbp
    assert shipped.p90_contracted_ebitda_gbp == merchant.p90_contracted_ebitda_gbp
    assert shipped.p90_penalty_exposure_gbp == merchant.p90_penalty_exposure_gbp
    assert any(_SHARES_MERCHANT in w for w in shipped.warnings), (
        "the fallback must stay LOUD: a silent share would be indistinguishable from a real value"
    )
    assert REF not in shipped.assumptions_used  # a null was not consumed as a number

    # (b) null ≢ zero. A 0% contracted haircut treats contracted revenue as FIRM.
    assert zero.p90_ebitda_gbp[0] > shipped.p90_ebitda_gbp[0]
    lib = load_assumptions(RELEASE)
    af, merch = _availability_factor(lib), _merchant_pct(lib)
    # Revenue side: the full merchant haircut on contracted revenue is restored.
    revenue_gap = _CM_CONTRACTED * (merch / 100.0) * af
    # Penalty side: the shortfall drops by the same haircut, so the penalty line shrinks with it.
    penalty_gap = (shipped.p90_penalty_exposure_gbp or 0.0) - (zero.p90_penalty_exposure_gbp or 0.0)
    assert zero.p90_ebitda_gbp[0] - shipped.p90_ebitda_gbp[0] == pytest.approx(
        revenue_gap + penalty_gap, abs=0.02
    ), "the null-vs-zero gap is the exact P90 overstatement a silent zero would introduce"
    assert revenue_gap > 0.0


# --- 3. the key is reachable: populate it and the number moves --------------


def test_a_populated_contracted_haircut_moves_the_p90_through_the_production_builder(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Reachability, asserted as a MOVED NUMBER rather than a loaded key.

    Adding a key to YAML proves nothing on its own; this drives ``build_cashflow_model`` — the same
    entry point the quote pipeline uses — and pins the differential exactly. Delete the read at
    ``cashflows.py:1250`` and both the movement and the ``assumptions_used`` entry disappear.
    """
    site, result = make_site(), _mixed()
    lib = load_assumptions(RELEASE)
    af, merch = _availability_factor(lib), _merchant_pct(lib)
    differentiated_pct = 5.0
    assert differentiated_pct != merch  # the test is vacuous if they coincide

    shipped = build_cashflow_model(site, result, lib)
    populated = build_cashflow_model(site, result, _library_with(differentiated_pct))

    # The key is consumed, and the fallback stops firing.
    assert REF in populated.assumptions_used
    assert not any(_SHARES_MERCHANT in w for w in populated.warnings)

    # The P90 number MOVES, by exactly the haircut differential net of the penalty it also drives.
    revenue_delta = _CM_CONTRACTED * ((merch - differentiated_pct) / 100.0) * af
    penalty_delta = (shipped.p90_penalty_exposure_gbp or 0.0) - (
        populated.p90_penalty_exposure_gbp or 0.0
    )
    assert populated.p90_ebitda_gbp[0] - shipped.p90_ebitda_gbp[0] == pytest.approx(
        revenue_delta + penalty_delta, abs=0.02
    )
    assert populated.p90_ebitda_gbp[0] > shipped.p90_ebitda_gbp[0]

    # ...and it is a P90-ONLY instrument: the expected case must not move at all.
    assert populated.p50_ebitda_gbp == shipped.p50_ebitda_gbp


def test_contracted_p90_differs_from_p50_by_exactly_the_haircut(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The acceptance statement in its cleanest form, on a CONTRACTED-ONLY, penalty-free result.

    With one CONTRACTED stream, no CM revenue (so no penalty line) and no FIRM revenue (so no
    perfect-foresight adjustment), the P50 and P90 series differ by the contracted haircut and
    nothing else. Asserted at BOTH the shipped null (which must show the merchant haircut) and a
    differentiated value (which must show its own), so the identity holds on both branches.
    """
    site, result = make_site(), _contracted_only()
    lib = load_assumptions(RELEASE)
    af = _availability_factor(lib)

    for pct, model in (
        (_merchant_pct(lib), build_cashflow_model(site, result, lib)),  # the shipped null
        (5.0, build_cashflow_model(site, result, _library_with(5.0))),
        (25.0, build_cashflow_model(site, result, _library_with(25.0))),
    ):
        assert model.p90_penalty_exposure_gbp is None, "fixture must isolate the haircut"
        expected_gap = _LOAD_SERVICE_CONTRACTED * (pct / 100.0) * af
        assert model.p50_ebitda_gbp[0] - model.p90_ebitda_gbp[0] == pytest.approx(
            expected_gap, abs=0.02
        ), f"contracted P90 must sit exactly {pct}% below P50 on the year-1 revenue"
        assert expected_gap > 0.0
