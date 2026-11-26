"""Demo determinism + a CROSS-PLATFORM pin on the demo's priced outputs (L01 · X.2 / M8).

The System Brief's reproducibility floor: *identical inputs, assumptions and seed → identical
outputs*. This module runs the *real* demo path — `DemoEngine` (the real MILP dispatch + sizing
outer loop) over the committed synthetic load — **twice** (the minimum two solves determinism
actually requires) and asserts four things, **on every platform, with no skip**:

  1. the two runs share one `quote_id` + `input_hash`;
  2. the two serialized quotes are byte-identical once clock metadata is normalised
     (same-platform reproducibility of the *whole* artifact, not just its id);
  3. every **priced** output equals its pinned value — £ to the penny, ratios to 4 dp;
  4. every stream's **placement** (priced floor vs gated upside), firmness, gate status and
     consent label equals its pinned value.

Clock/version/identity metadata is *not* an output and is normalised out of (1)–(2) before
comparison: `created_at`, status-history `timestamp`, the per-commit `code_version`, and the
identity stamps that derive from it — `quote_id`, `run_id`, `input_hash`, `case_id`.

**Why there is no exact whole-quote hash pin any more (D47, 2026-08-17).** There used to be a
`GOLDEN_QUOTE_HASH`: sha256 of the whole normalised quote. It was minted on x86 Linux and
`pytest.skip`-ped everywhere else (C034), so on a macOS/ARM contributor's machine it asserted
nothing at all. It was not fixable by normalisation, and the reason was measured rather than
assumed: the priced-floor LP has **multiple optimal vertices**, and the serialized quote persists
the vertex. Solving the demo's own LP twice with a different arithmetic path (HiGHS `presolve` off
+ dual simplex instead of the production `ipm` + crossover) returns the *same objective to 4e-13
relative* while `charge_kw` moves in 44 half-hours by up to **12.42 kW**, `discharge_kw` in 18, and
the whole `soe_kwh` trajectory differs about two-fold — while the summed charge and discharge
setpoints stay **identical to the digit** (158.9028 kW and 139.8344 kW, either way). So the
schedule's *placement* and its SoE level
are chosen by the solver's arithmetic, not implied by the site's data, and a difference at 12 kW
scale cannot be rounded away without rounding away everything the pin was for. (Control: changing
only HiGHS's `random_seed` moves nothing — `random_seed` does not perturb the IPM path — which is
why the pin was reproducible *per machine* and not *across* machines.)

What the LP *does* determine — the objective, hence every priced total, the £ per stream and the
capex/cashflow series — is platform-stable, and that is what (3)–(4) pin. The published peak import
is pinned too, but NOT because `_EPS_PEAK` holds it: that epsilon tie-break sits below HiGHS's
default tolerances on a full year (2,540 half-hours on the demo's peak plateau), and the first
solve returned 278.4 kW on macOS and 260.2 kW on Linux at the same £. Since D67 the solve whose
schedule becomes the customer bill re-solves once for the MINIMUM peak among the £-optimal
schedules, a single value fixed by the data, and
`test_demo_published_peak_is_the_minimum_at_the_optimal_value` holds it with an independent
certificate. The pins live in
plain sight as numbers, so a drift fails with a readable value diff on **every** platform instead of
an opaque hash mismatch on one. What is deliberately **not** pinned, and now carries no
cross-commit guard: per-half-hour dispatch placement (`dispatch_profile.points`) and the
178-candidate sizing surface (`sizing_grid.points`). Restoring a byte pin over those needs the
dispatch vertex made unique first (Olly's filed lexicographic two-pass follow-up), not a wider skip.

Re-mint (3)–(4) deliberately when a change is *meant* to move the demo, never to paper over a diff:
run `_priced_outputs(_RUN_A.quote.model_dump(mode="json"))` and paste, and state the direction of
effect in the changelog entry. The removed pin's final value was
`sha256:cb452ff90ee0ad0b10b7cccbd170f84a6e59a06d2418721b8fcc8763baec26da` (Linux, fully-consented
demo); its full re-mint lineage is in this file's git history before D47.
"""

from __future__ import annotations

from typing import Any

import linopy
import pytest

import gridq.optimisation.milp_solver as milp_solver
from gridq.app.demo import DEMO_DEBT_TERMS, DEMO_LABEL, DemoEngine, load_demo_site
from gridq.assumptions import load_assumptions
from gridq.dispatch import dispatch_constraints_for
from gridq.dispatch.contract import DispatchProblem
from gridq.provenance import hash_json
from gridq.quotes import QuoteRunResult, generate_quote
from gridq.schemas import QuoteMode
from gridq.value_stack.registry import default_registry

ASSUMPTION_VERSION = "gb_v1_4"

#: Quote fields whose values come from the clock or the git commit, not the deterministic run.
#: `quote_id`/`run_id`/`input_hash`/`case_id` all derive from `code_version`, so they change every
#: commit. They are identity metadata, not outputs, and are normalised out before any comparison.
_VOLATILE_KEYS = frozenset(
    {"created_at", "timestamp", "code_version", "quote_id", "run_id", "input_hash", "case_id"}
)

#: £, kW, kWh and hours are pinned as the record rounds them: 2 dp.
_TWO_DP = 0.005
#: Percentages, years and DSCR ratios are pinned as the record rounds them: 4 dp.
_FOUR_DP = 0.00005

_FOUR_DP_KEYS = frozenset(
    {
        "finance.project_irr_pct",
        "finance.equity_irr_pct",
        "finance.payback_years",
        "finance.min_p90_dscr",
    }
)

#: The demo's PRICED outputs. Platform-stable by measurement (see the module docstring): these are
#: the quantities the LP optimum determines, as distinct from the degenerate schedule placement.
#: A change here means the demo's economics moved — update deliberately, never to hide a diff.
#: Minted 2026-08-17 on the Revenue-Data drive tree (macOS/ARM); falsifiable on Linux CI, which is
#: the point: if the two platforms ever disagree about a priced number, that is a real finding and
#: it now surfaces as a named value diff rather than as a skipped test.
# RE-MINTED 2026-08-19 (Olly), and the reason matters more than the numbers.
#
# These two dicts had been RED since D57 landed on 2026-08-18, not since D60. The drift was MEASURED
# rather than argued (scratchpad/golden_remint.py, a plain-python run through this module's own
# helpers): a `floor.DFS` pin of GBP 0 could only ever pass while NO engine branch priced DFS, and
# that stopped being true when the DFS branch was built. The pins were simply never re-minted
# when the streams opened.
#
# THE STRUCTURAL CHANGE THIS PIN EXISTS TO SURFACE, and it did its job: BALANCING_MECHANISM and
# ANCILLARY_DC_DM_DR MIGRATED OUT OF `gated_upside` INTO THE PRICED FLOOR, so the pinned KEY SET
# changed - `gated.BALANCING_MECHANISM` and `gated.ANCILLARY_DC_DM_DR` are gone and `floor.*`
# gained them. Under D57 every stream is open and every consent, award, registration and threshold
# is assumed approved, so a consented gated stream becoming CONTRACTED and entering the floor is
# the intended behaviour, not a leak. CAPACITY_MARKET and DFS likewise moved
# conditional/conditional -> contracted/open.
#
# WHAT D60 ADDED ON TOP: the DFS magnitude on the demo (LE17 4XN sits in zone 6, so the observed
# called-period count fell 1,170 -> 407), which moved the demo's dispatch and its selected size from
# 175.00 kW x 3.15 h to 157.94 kW x 4.00 h. Project IRR 8.4529 -> 25.8359 pct, dominated by the CM
# and BM/ancillary additions rather than by DFS.
#
# The implementing session declined to re-mint these, correctly: three of the four placement changes
# belonged to other lanes and signing them off from that seat would have absorbed someone else's
# structural change. This is the session that owns all of those lanes, so it is signed off here.
PINNED_DEMO_VALUES: dict[str, float] = {
    "bill.annual_bill_after_gbp": 228734.28,
    "bill.annual_bill_before_gbp": 263384.35,
    "bill.annual_saving_gbp": 34650.07,
    # RE-MINTED 2026-11-25 (D67, owner: "OK to re-mint", 2026-11-23): 278.407 -> 235.841 kW. The
    # bill-producing solve now returns the minimum peak at the optimal £ (a second pass holding the
    # £ objective to within £1e-6). DIRECTION: DOWN 42.566 kW; no £ pin moves. Four independent
    # solver routes agree on the chosen spec (157.94 kW x 4 h, gb_v1_4), recomputed for D67:
    #   second pass (minimise peak, £ held to 1e-6), IPM + crossover ... 235.84122 to 235.84125
    #   single pass, dual feasibility tolerance 1e-10 .................. 235.841364
    #   single pass, _EPS_PEAK raised to 1e-4 and to 1e-3 .............. 235.841364
    #   single pass, primal simplex .................................... 235.841364
    # All round to 235.841 (the record's 3 dp), within this pin's 0.005 kW tolerance. Before the
    # battery the site peaks at 248.45 kW, so the battery now LOWERS the published peak by 12.6 kW
    # where the old figure said it raised it by 30 kW.
    "bill.peak_import_after_kw": 235.841,
    "bill.peak_import_before_kw": 248.45,
    "finance.equity_irr_pct": 31.321,
    "finance.min_p90_dscr": 4.6632,
    "finance.p50_cashflow_sum": 343765.23,
    "finance.p90_cashflow_sum": 221495.79,
    "finance.payback_years": 3.5159,
    "finance.project_irr_pct": 25.8359,
    "floor.AGREED_CAPACITY_REDUCTION": 0.0,
    "floor.ANCILLARY_DC_DM_DR": 1895.28,
    "floor.BALANCING_MECHANISM": 2369.1,
    "floor.CAPACITY_MARKET": 14167.08,
    "floor.DFS": 3772.76,
    "floor.DUOS_RED_BAND_AVOIDANCE": 15678.57,
    "floor.LOAD_SERVICE": 0.0,
    "floor.LOCAL_FLEX_PICLO": 0.0,
    "floor.MIC_ASC_HEADROOM": 0.0,
    "floor.PPA_OR_BILL_SAVINGS": 0.0,
    "floor.TARIFF_ARBITRAGE": 19397.62,
    "floor.WHOLESALE_P415": 60.08,
    "floor.total_cost_gbp": 0.0,
    "floor.total_revenue_gbp": 57340.49,
    "gated.WHOLESALE_P415": 483.91,
    "spec.duration_h": 4.0,
    "spec.energy_kwh": 631.76,
    "spec.power_kw": 157.94,
}

#: Where each stream SITS and what it claims: (firmness, gate_status, consent_status). Strings, so
#: platform-independent by construction. This is the guard that a stream cannot quietly migrate
#: between the priced floor and gated upside, gain firmness, or read as consented when it is not.
PINNED_DEMO_PLACEMENT: dict[str, tuple[str, str, str | None]] = {
    "floor.AGREED_CAPACITY_REDUCTION": ("excluded", "closed", None),
    "floor.ANCILLARY_DC_DM_DR": ("contracted", "open", "given"),
    "floor.BALANCING_MECHANISM": ("contracted", "open", "given"),
    "floor.CAPACITY_MARKET": ("contracted", "open", "given"),
    "floor.DFS": ("contracted", "open", None),
    "floor.DUOS_RED_BAND_AVOIDANCE": ("firm", "open", "given"),
    "floor.LOAD_SERVICE": ("excluded", "closed", "given"),
    "floor.LOCAL_FLEX_PICLO": ("conditional", "conditional", "given"),
    "floor.MIC_ASC_HEADROOM": ("firm", "open", "given"),
    "floor.PPA_OR_BILL_SAVINGS": ("firm", "open", "given"),
    "floor.TARIFF_ARBITRAGE": ("firm", "open", "given"),
    "floor.WHOLESALE_P415": ("contracted", "open", "given"),
    "gated.WHOLESALE_P415": ("conditional", "open", "given"),
}


def _run_demo() -> QuoteRunResult:
    """Run the real demo engine over the committed synthetic load (one MILP solve)."""
    return generate_quote(
        load_demo_site(),
        load_assumptions(ASSUMPTION_VERSION),
        engine=DemoEngine(),
        debt_terms=DEMO_DEBT_TERMS,
        quote_mode=QuoteMode.INDICATIVE,
        extra_warnings=[DEMO_LABEL],
    )


def _normalise(obj: Any) -> Any:
    """Recursively replace clock/version metadata with a sentinel, leaving outputs intact."""
    if isinstance(obj, dict):
        return {k: ("<volatile>" if k in _VOLATILE_KEYS else _normalise(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalise(v) for v in obj]
    return obj


def _tolerance(key: str) -> float:
    """The precision the record itself carries for this pin — never wider."""
    return _FOUR_DP if key in _FOUR_DP_KEYS else _TWO_DP


def _priced_outputs(dump: dict[str, Any]) -> dict[str, float]:
    """Read the demo's priced outputs off the SERIALIZED quote — the layer a reader reads.

    Deliberately reads the `model_dump(mode="json")` payload rather than the objects: a field that
    stops serialising, or serialises under a new name, is a regression a reader would see and must
    fail here. A `None` where a number is pinned is a failure too, not a skip (D45: a null on the
    pricing path is a defect), so it is surfaced as `float("nan")`, which no tolerance accepts.
    """

    def _num(value: Any) -> float:
        return float("nan") if value is None else float(value)

    opt = dump["optimisation_result"]
    fin = dump["financial_case"]
    bill = dump["customer_bill"]
    missing = [
        name
        for name, block in (
            ("optimisation_result", opt),
            ("financial_case", fin),
            ("customer_bill", bill),
        )
        if block is None
    ]
    assert not missing, (
        f"the demo quote no longer carries {missing} — a whole block of the reader's figures "
        "stopped being produced. That is a regression, not a reason to pin less."
    )
    out: dict[str, float] = {
        "spec.power_kw": _num(opt["selected_power_kw"]),
        "spec.energy_kwh": _num(opt["selected_energy_kwh"]),
        "spec.duration_h": _num(opt["duration_hours"]),
    }
    for stream in opt["value_streams"]:
        out[f"floor.{stream['stream']}"] = _num(stream["annual_revenue_gbp"])
    for stream in opt["gated_upside"]:
        out[f"gated.{stream['stream']}"] = _num(stream["annual_revenue_gbp"])
    out["floor.total_revenue_gbp"] = round(
        sum(_num(s["annual_revenue_gbp"]) for s in opt["value_streams"]), 2
    )
    out["floor.total_cost_gbp"] = round(
        sum(_num(s["annual_cost_gbp"]) for s in opt["value_streams"]), 2
    )
    out["finance.project_irr_pct"] = _num(fin["irr"])
    out["finance.equity_irr_pct"] = _num(fin["equity_irr"])
    out["finance.payback_years"] = _num(fin["payback_years"])
    out["finance.min_p90_dscr"] = _num(fin["dscr_summary"]["min_dscr"])
    out["finance.p50_cashflow_sum"] = round(sum(_num(c) for c in fin["p50_cashflows"]), 2)
    out["finance.p90_cashflow_sum"] = round(sum(_num(c) for c in fin["p90_cashflows"]), 2)
    for field in (
        "annual_bill_before_gbp",
        "annual_bill_after_gbp",
        "annual_saving_gbp",
        "peak_import_before_kw",
        "peak_import_after_kw",
    ):
        out[f"bill.{field}"] = _num(bill[field])
    return out


def _stream_placement(dump: dict[str, Any]) -> dict[str, tuple[str, str, str | None]]:
    """Read each stream's placement + claims off the serialized quote."""
    opt = dump["optimisation_result"]
    placement: dict[str, tuple[str, str, str | None]] = {}
    for slot, key in (("value_streams", "floor"), ("gated_upside", "gated")):
        for stream in opt[slot]:
            placement[f"{key}.{stream['stream']}"] = (
                stream["firmness"],
                stream["gate_status"],
                stream["consent_status"],
            )
    return placement


# Two solves for the whole module — exactly what determinism needs, and no more (each is a real
# MILP solve). Shared across the tests below.
_RUN_A = _run_demo()
_RUN_B = _run_demo()
_DUMP_A = _RUN_A.quote.model_dump(mode="json")


def test_demo_quote_id_and_input_hash_are_stable() -> None:
    """Identical inputs reproduce the same stamped identity."""
    assert _RUN_A.quote.quote_id == _RUN_B.quote.quote_id
    assert _RUN_A.quote.input_hash == _RUN_B.quote.input_hash


def test_demo_serialized_outputs_are_byte_identical() -> None:
    """The *whole* serialized quote (not just its id) reproduces, modulo clock metadata."""
    a = hash_json(_normalise(_RUN_A.quote.model_dump(mode="json")))
    b = hash_json(_normalise(_RUN_B.quote.model_dump(mode="json")))
    assert a == b


def test_demo_priced_outputs_match_the_pinned_values() -> None:
    """Every priced output the demo reports equals its pinned value, on every platform.

    Replaces the Linux-only `GOLDEN_QUOTE_HASH` (D47). It guards the same regression class the
    golden was there for — silent numeric drift in the demo's deterministic economics — but over
    the quantities the LP optimum actually determines, so it runs everywhere and fails with a
    readable value diff rather than an opaque hash mismatch.
    """
    actual = _priced_outputs(_DUMP_A)
    assert set(actual) == set(PINNED_DEMO_VALUES), (
        "the SET of the demo's priced outputs changed, so a figure appeared or vanished rather "
        "than moved. Reconcile before re-pinning.\n"
        f"  in the run but not pinned: {sorted(set(actual) - set(PINNED_DEMO_VALUES))}\n"
        f"  pinned but not in the run: {sorted(set(PINNED_DEMO_VALUES) - set(actual))}"
    )
    drift = {
        key: (actual[key], expected)
        for key, expected in PINNED_DEMO_VALUES.items()
        if not abs(actual[key] - expected) <= _tolerance(key)
    }
    assert not drift, (
        "the demo's priced outputs drifted from their pinned values. If this change is intended, "
        "re-mint per the module docstring and state the direction of effect; otherwise an "
        "unintended change has reached the pricing path.\n"
        + "\n".join(
            f"  {key}: run={run!r} pinned={pinned!r} delta={run - pinned:+.6g}"
            for key, (run, pinned) in sorted(drift.items())
        )
    )


def test_demo_stream_placement_and_firmness_are_pinned() -> None:
    """No stream migrates between the priced floor and gated upside, or changes what it claims.

    Strings only, so this half of the old golden's coverage is platform-independent by
    construction: firmness, gate status and the consent label a reader is shown.
    """
    actual = _stream_placement(_DUMP_A)
    changed = {
        key: (actual.get(key), expected)
        for key, expected in PINNED_DEMO_PLACEMENT.items()
        if actual.get(key) != expected
    }
    new = sorted(set(actual) - set(PINNED_DEMO_PLACEMENT))
    assert not changed and not new, (
        "the demo's stream placement / firmness / consent labels moved. A stream changing slot or "
        "firmness changes what the pack CLAIMS, so re-pin only with the reason recorded.\n"
        + "\n".join(
            f"  {key}: run={run!r} pinned={pinned!r}"
            for key, (run, pinned) in sorted(changed.items())
        )
        + (f"\n  streams present but not pinned: {new}" if new else "")
    )
    # The posture the whole pack depends on: nothing is confirmed, so nothing is bankable, and the
    # confidence band is the "confirmation required" statement (D45).
    assert _DUMP_A["optimisation_result"]["bankable_status"] == "not_bankable"
    assert (_DUMP_A["confidence_band"], _DUMP_A["confidence_ceiling"]) == ("low", "medium")
    assert _DUMP_A["financial_case"]["dscr_summary"]["basis"] == "P90 PRE-TAX"


#: D67's published peak on the demo's chosen spec: the same number `bill.peak_import_after_kw` pins.
_PEAK_PIN_KW = 235.841


def test_demo_published_peak_is_the_minimum_at_the_optimal_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P2 (D67): the published peak is the LOWEST peak among value-optimal schedules, certified.

    The pin above says the bill shows 235.841 kW; this says why that number and no other. On the
    demo's chosen spec (read off the run, 157.94 kW x 4 h on gb_v1_4) the bill-producing dispatch
    is solved four ways, with a `linopy.Model.solve` wrapper that can cap ``peak_import`` and that
    records the FIRST pass's value objective (``objective + _EPS_PEAK * peak``, the dispatch
    objective with the peak tie-break removed):

      A. flag off: the first pass alone, an arbitrary vertex (278.4 kW on macOS before D67);
      B. flag on (D67): the peak lands on the pin, no higher than A's, and NOTHING priced moves:
         the bill saving and every stream's £ equal A's to the penny;
      C. flag off, peak capped 0.005 kW ABOVE the pin: still optimal, and the value objective is
         A's to within 1e-5 (measured 7.7e-8), so a schedule at the pin IS value-optimal;
      D. flag off, peak capped 0.05 kW BELOW the pin: infeasible, or the value objective falls by
         at least 5e-4 (measured 1.90e-3), so no lower peak is value-optimal.

    C and D bracket the pin from both sides without the second pass, so this certifies the result,
    not the mechanism. What it does NOT claim: the lowest peak at the highest BILL saving. The
    objective is bill saving plus every priced stream, less wear; capping 1 kW lower raises the
    saving by 4p and costs DFS 7p (measured). Thresholds are literals, never the solver constants.
    Dies if the second pass is disabled (B misses the pin), if its £ floor is loosened (B moves the
    saving and DFS), or if a schedule exists at a lower peak and the same value (D).
    """
    opt = _DUMP_A["optimisation_result"]
    power, energy, hours = (
        float(opt["selected_power_kw"]),
        float(opt["selected_energy_kwh"]),
        float(opt["duration_hours"]),
    )
    site = load_demo_site()
    lib = load_assumptions(ASSUMPTION_VERSION)
    registry = default_registry()
    ids = registry.stream_ids()
    constraints = dispatch_constraints_for(
        ids, site, lib, registry=registry, candidate_power_kw=power
    )

    real_solve = linopy.Model.solve
    state: dict[str, Any] = {"cap": None, "first": None}

    def _capped_solve(self: linopy.Model, *args: Any, **kwargs: Any) -> Any:
        if state["cap"] is not None and "peak_import" in self.variables:
            self.variables["peak_import"].upper = state["cap"]
        status = real_solve(self, *args, **kwargs)
        if state["first"] is None:
            econ = None
            value = self.objective.value
            if status[1] == "optimal" and value is not None and "peak_import" in self.variables:
                peak = float(self.variables["peak_import"].solution)
                econ = float(value) + milp_solver._EPS_PEAK * peak
            state["first"] = (status[1], econ)
        return status

    monkeypatch.setattr(linopy.Model, "solve", _capped_solve)

    def _solve(*, cap: float | None, flag: bool) -> dict[str, Any]:
        state["cap"], state["first"] = cap, None
        problem = DispatchProblem(
            site_model=site,
            assumptions=lib,
            power_kw=power,
            energy_kwh=energy,
            duration_hours=hours,
            requested_streams=list(ids),
            constraints=constraints,
            scenario=None,
            minimise_published_peak=flag,
        )
        solver = milp_solver.MilpDispatchSolver(registry=registry, seed=0)
        result = solver.solve(problem)
        bill = solver.last_customer_bill()
        first_status, econ = state["first"]
        streams = result.value_streams
        return {
            "status": first_status,
            "econ": econ,
            "peak": None if bill is None else bill.peak_import_after_kw,
            "saving": None if bill is None else bill.annual_saving_gbp,
            "streams": {str(v.stream): round(v.annual_revenue_gbp, 2) for v in streams},
        }

    a = _solve(cap=None, flag=False)
    assert a["status"] == "optimal" and a["econ"] is not None and a["peak"] is not None

    b = _solve(cap=None, flag=True)
    assert b["peak"] is not None and abs(b["peak"] - _PEAK_PIN_KW) <= 0.005, b["peak"]
    assert b["peak"] <= a["peak"] + 1e-9, (a["peak"], b["peak"])
    assert abs(_DUMP_A["customer_bill"]["peak_import_after_kw"] - b["peak"]) <= 0.005
    assert a["saving"] is not None and b["saving"] is not None
    assert round(b["saving"], 2) == round(a["saving"], 2), "the peak pass moved the bill saving"
    assert b["streams"] == a["streams"], "minimising the published peak moved a priced stream"

    c = _solve(cap=_PEAK_PIN_KW + 0.005, flag=False)
    assert c["status"] == "optimal" and c["econ"] is not None
    assert a["econ"] - c["econ"] <= 1e-5, ("a schedule at the pin is not value-optimal", a, c)

    d = _solve(cap=_PEAK_PIN_KW - 0.05, flag=False)
    assert d["status"] != "optimal" or a["econ"] - d["econ"] >= 5e-4, (
        "a peak 0.05 kW below the pin is value-optimal, so the published peak is not the minimum",
        a,
        d,
    )
