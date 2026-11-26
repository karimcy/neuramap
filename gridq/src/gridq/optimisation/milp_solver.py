"""The real half-hourly dispatch MILP — ``MilpDispatchSolver`` (linopy / HiGHS).

Implements the inner `gridq.dispatch.DispatchSolver` Protocol: given one fixed system spec plus
the site, the assumption release and the dispatch constraints, it solves a deterministic
half-hourly battery dispatch and returns a contract-valid `DispatchResult` with real per-stream
value and real annual charge/discharge volumes. It is the production replacement for the
integration-only `SimpleSolver`, dropping in behind the same contract with no downstream change.

Model (per half-hour ``t`` over the metered horizon, Δt = interval/60 h):

- variables ``charge_t``, ``discharge_t`` (kW, AC side) and ``soe_t`` (kWh, state of energy);
- **SoE dynamics + SPLIT round-trip efficiency (B-01):**
  ``soe_t = soe_{t-1} + charge_t·η_ch·Δt − (discharge_t + export_t)·Δt ÷ η_dis`` (cyclic
  ``soe_0 = soe_T``) with ``η_ch = η_dis = √RTE`` from ``round_trip_efficiency.ac_round_trip``.
  Splitting the AC round trip symmetrically leaves the *financial* identity untouched — over a
  closed cycle total grid charge still equals total discharge ÷ RTE, the convention finance grosses
  up by — but fixes the *trajectory*: the energy a held-SoE floor or a pre-position must actually
  store to deliver 1 kWh at the AC terminals is ``1 ÷ η_dis`` (≈ +6.6% at RTE 0.88), not 1 kWh.
  Charging all the losses (the pre-B-01 ``charge·RTE·Δt`` in / ``discharge·Δt`` out) certified
  deliverability the battery could not meet — an obligation-BREACH direction, not a
  conservative one;
- **usable SoC window (D-169/JSON-F-46):** ``soc_min·energy ≤ soe_t ≤ soc_max·energy`` when the
  release carries ``warranty_cycle_limits.soc_min_fraction`` / ``soc_max_fraction``. Absent = the
  full 0–100% sweep, which overstates deliverable energy per cycle by the BMS reserve — so absence
  warns loudly and asks for the datasheet/warranty window rather than inventing 5%/10%;
- **connection (MIC/ASC):** ``charge_t ≤ min(MIC, ASC) − net_load_t`` on the SIGNED net position
  (D-174: PV surplus is real charge headroom, never clipped away), optionally de-rated by a
  release-supplied power factor (B-17 — kVA, not kW, is what the connection limits);
- **behind-the-meter:** ``discharge_t ≤ max(net_load_t, 0)`` (no export on the priced floor —
  export / wholesale is gated upside) and ``charge_t, discharge_t ≤ power``;
- **warranty throughput (HARD):** annual full-cycle-equivalents ≤
  ``warranty_cycle_limits.max_cycles_per_year_by_duration_h`` (the horizon share of the annual cap);
- **objective:** maximise the site bill reduction NET OF WEAR,
  ``Σ price_t·(discharge_t − charge_t)·Δt − c_deg·Σ(discharge_t + export_t)·Δt``, where
  ``price_t`` is the site's own time-of-use energy rate + DUoS placed in time by ``tariff_calendar``
  **plus the flat £/kWh levy stack** (F-03: a kWh through the meter costs energy + DUoS + levies, so
  the optimiser must see all three or it books cycles whose margin the levy leakage erases; the levy
  is a decision price only and is charged once, in finance's ``levies_on_charge``) and ``c_deg`` is
  the marginal cycle cost (DEG-01 — without it cycling is free and the break-even arbitrage spread
  is ~65% too low, so every trade in the £11–33/MWh band is booked profitable and is loss-making).
  Round-trip losses are bought at the price faced — the ``charge_t`` term. A negligible tie-break
  regulariser biases the optimum toward no pointless cycling so the schedule stays deterministic.

Determinism: HiGHS runs single-threaded with a fixed random seed and deterministic presolve; every
binary solve pins ``mip_rel_gap`` and records the ACHIEVED gap (B-09 — an unstated gap means two
HiGHS versions can return different "optimal" incumbents and the reported £ carries no statement of
distance from proven optimal); all extracted quantities are rounded. No LLM, no wall-clock, no RNG
on the path. It **never raises** on insufficient site data — it reports via
``solver_status``/``warnings`` (no HH series, no observed peak, an invalid RTE or a MIP gap above
tolerance all degrade to ``INSUFFICIENT_DATA`` with no invented volumes).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from gridq.assumptions import AssumptionLibrary
from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.engine.errors import InvalidRequestError
from gridq.ingestion import HalfHourlyDataError, load_half_hourly_csv
from gridq.optimisation._assumptions import (
    ceiling_value_for_duration,
    is_confirmed,
    maybe,
    numeric,
)
from gridq.optimisation.export_prices import align_export_prices
from gridq.optimisation.schedule import HalfHourlyDispatchSchedule
from gridq.optimisation.tariff_calendar import (
    duos_windows_from_assumptions,
    energy_objective_coefficients,
    energy_price_envelope,
    network_rate_series_with_absences,
    red_band_mask,
    tariff_provenance_phrase,
    windowed_levy_rate_series,
)
from gridq.optimisation.valuation import build_value_streams
from gridq.schemas import (
    AvailabilityCommitment,
    BankableStatus,
    CdcmTariff,
    CustomerBill,
    ImplementationLevel,
    MarketStream,
    SiteModel,
    SolverStatus,
)
from gridq.sizing.deliverability import produce_gate_context
from gridq.tariffs.hv_tariff_model import CDCM_REACTIVE_THRESHOLD
from gridq.value_stack.gb_streams import (
    _FULL_YEAR_COVERAGE_TOLERANCE,
    AgreedCapacityValue,
    agreed_capacity_basis,
    dfs_service_period_mask,
    dfs_utilisation_rate_gbp_per_kwh,
    dfs_zone_for_site,
    price_agreed_capacity_reduction,
)
from gridq.value_stack.registry import StreamRegistry, default_registry

if TYPE_CHECKING:
    from gridq.ingestion.market_forecasts import MarketForecast
    from gridq.value_stack.interface import DispatchConstraintDescriptor

_HOURS_PER_YEAR = 8760.0
_DEFAULT_INTERVAL_MIN = 30
# A negligible numerical tie-break regulariser (NOT a price): it is orders of magnitude below any
# real £/kWh, and only breaks objective ties toward *less* throughput, so the optimum is unique and
# deterministic and the engine never cycles the battery for zero economic gain.
_EPS = 1e-7
# Second tie-break regulariser (NOT a price, NOT a calibrated degradation cost): among schedules
# already tied on arbitrage £ and throughput, minimise the residual PEAK net import after the
# battery — the exact quantity customer_bill.peak_import_after_kw reports (max over t of
# load + charge − discharge). This protects the peak-shaving / firming deliverable (C029) instead
# of leaving the placement to an arbitrary LP vertex. Epsilon-scale (orders of magnitude below any
# real £/kWh) so it never moves a reported £. Replaces the D37 SoE-holding term, whose charge-late
# lean pushed charging into the morning load ramp and raised the peak it should protect (D38).
# Honest scope (D67): at this scale the tie-break is NOT enforced under HiGHS's default tolerances
# on a full year. On the demo 2,540 half-hours sit on the peak plateau, so the pull on any one of
# them is about 1e-6 / 2,540 = 4e-10, below the 1e-7 dual tolerance, and the solver returns
# whichever £-optimal schedule its route reaches (278.4 kW on macOS, 260.2 kW on Linux, against a
# true minimum of 235.8 kW). The PUBLISHED peak is made exact by the second pass below
# (`DispatchProblem.minimise_published_peak`), not by this term.
_EPS_PEAK = 1e-6
# D67: the bill-producing solve's second pass minimises the peak while holding the £ objective to
# within this much of its first-pass optimum. £1e-6 keeps every reported £ unchanged to the penny
# (measured on the demo; £0.01 of slack moved the saving by a penny and the peak by 0.26 kW).
_PEAK_PASS_ECON_SLACK_GBP = 1e-6
# D67: the second schedule is adopted only when it lowers the peak by more than this (the bill's
# 0.001 kW resolution). Below it the first schedule is kept bit for bit, so a measured zero (a
# battery the economics leave idle) is never disturbed by second-pass solver noise.
_PEAK_PASS_MIN_GAIN_KW = 1e-3
# Third, strictly-subordinate tie-break (_EPS_AGE ≪ _EPS_PEAK): among schedules also tied on the
# peak, lean discharge EARLIER (the calendar-ageing direction — less dwell at high SoC, matching
# the MPC path's aging_alpha). It touches DISCHARGE ONLY, so it adds no charge-late pressure and
# can never fight the firming term. Uses a horizon-normalised interval index so its scale is bounded
# regardless of solve length. Honest scope (verified, D38): at this scale it breaks ties in exact
# arithmetic only — per-slot coefficient deltas sit below HiGHS's working tolerances, so placement
# the peak does not bind (weekend day-rate discharge, sub-peak night charge) remains solver-chosen:
# deterministic per machine, not unique by construction. Raising it enough to visibly order every
# half-hour would put it in conflict with the peak term, so it deliberately stays formal.
# Not a £-priced ageing cost — that stays deferred to M7 (D38).
_EPS_AGE = 1e-9
_RTE_KEY = "round_trip_efficiency.ac_round_trip"
_WARRANTY_KEY = "warranty_cycle_limits.max_cycles_per_year_by_duration_h"
_DEG_COST_KEY = "degradation.marginal_cycle_cost_gbp_per_mwh_discharged"
# Where the quantity actually lives in the shipped releases (gb_v1_6 £5.0 -> gb_v1_7 £21.3).
_DEG_COST_KEY_LEGACY = "battery_opex.degradation_marginal_cost_gbp_per_mwh"

# --- DEG-01: the marginal cost of a cycle -------------------------------------------------------
# The FIRST real cost coefficient on the discharge side. Everything above (_EPS, _EPS_PEAK,
# _EPS_AGE) is an epsilon tie-break that explicitly disclaims being a price; before DEG-01 the
# default annual objective therefore priced NO marginal cycle cost and cycling was free.
#
# SOURCED, not invented, and read from the release wherever one carries it. Preferred name:
# ``_DEG_COST_KEY`` (states its own basis — per MWh discharged, cycle wear only); no release uses
# that name yet. Actual home: ``_DEG_COST_KEY_LEGACY``, carried by gb_v1_2…gb_v1_7, which is why
# both are looked up. The constant below is the LAST resort, reached only on a release older than
# gb_v1_2 (e.g. the gb_v0_1 the test fixtures pin), and it warns as a data ask when it is.
# It is the levelised first-pass GB figure derived in
# ``docs/bess_degradation_requirements.md`` C-34 and tabulated there under "Cost basis for the
# degradation term" as `Derived c_deg (worked example) | ~21.3 | £/MWh discharged | £120k/MWh,
# 6,000 EFC, η_dis 0.94`, i.e. c_deg = C_repl / (N_EFC,EOL · η_dis). The register row
# `docs/bess_constants_register.md` "Battery pack replacement cost (used for degradation pricing)"
# (Xu et al. $300,000/MWh; Hesse EUR 500/kWh) is deliberately NOT used for the level — its own
# caveat says those vintages are "cited for the FORM of the degradation cost, not the level" and
# that the £/MWh "must be re-derived from a current replacement cost". That re-derivation is a
# release job (C-37: one sourced, dated C_repl with a stated basis), not a solver constant, so the
# solver carries the doc's own worked first-pass number and says so, loudly, every solve.
#
# CALIBRATION DEPTH (C-35, required whenever a flat adder is used): the 6,000-EFC basis is a deep
# (~80% DoD) duty. Φ is convex (k₅ = 2.03), so a flat adder overcharges shallow cycling by up to
# ~8× and undercharges the deep tail; the depth-dependent piecewise cost (C-16/C-35) is deferred.
# Never reuse this number at a different depth without restating the calibration.
_DEG_COST_GBP_PER_MWH_DEFAULT = 21.3
# Break-even spread this implies at a £70/MWh charge price and RTE 0.86 (C-36's worked example):
# Δp_min = 70·(1/0.86 − 1) + 21.3 = £32.7/MWh, against the £11.4/MWh an efficiency-only objective
# sets. Quoted in the warning so the reader can check the consequence, never used as a coefficient.
_DEG_BREAK_EVEN_NOTE = (
    "break-even arbitrage spread rises from ~£11.4/MWh (efficiency only) to ~£32.7/MWh "
    "(C-36 worked example, £70/MWh charge price, RTE 0.86)"
)

# --- D-169 / JSON-F-46: the usable SoC window ---------------------------------------------------
# Release keys, NOT constants. The register row "Usable state-of-charge window (published case
# studies)" (Xu 15–95%, Hesse 10–95%, Garifi 15–85%) carries its own caveat: "Sources disagree by
# up to 15 percentage points on usable depth; the value is a warranty/vendor parameter, not a
# physical constant, and must come from the specific product datasheet." So the solver reads it
# from the release beside the warranty cycle cap it belongs with, and refuses to invent a number
# when absent — it warns and asks, leaving today's 0–100% sweep visible rather than silently
# "fixed" with a literature figure the register forbids reusing.
_SOC_MIN_KEY = "warranty_cycle_limits.soc_min_fraction"
_SOC_MAX_KEY = "warranty_cycle_limits.soc_max_fraction"

# --- B-17: power factor ------------------------------------------------------------------------
# Release key, NOT a constant. Real C&I sites run 0.90–0.95 PF, but no site in this schema carries
# a reactive/kVArh series, so the solver will not invent one: absent key = today's kVA≈kW at unity
# PF, stated as a labelled warning plus a data ask.
_POWER_FACTOR_KEY = "mic_asc_headroom.assumed_power_factor"

# --- WP-UW-07 (D106): reactive power ------------------------------------------------------
#: The INSCRIBED regular octagon of a P-Q circle of radius S: ``|P| + k|Q| <= S`` and
#: ``k|P| + |Q| <= S`` with ``k = sqrt(2) - 1``. Every vertex lies on the circle, so the octagon
#: never claims capability (or connection headroom) the circle does not have. Geometry, not data.
_OCTAGON_K = math.sqrt(2.0) - 1.0
#: The LC14 excess-reactive threshold, 0.33 kVArh per kWh (PF 0.95; "The square root calculation
#: will be to two decimal places"), the same constant the published-tariff tests hold.
_REACTIVE_THRESHOLD = float(CDCM_REACTIVE_THRESHOLD)

# --- B-09: MIP gap ------------------------------------------------------------------------------
# Pinned relative gap for every branch-and-bound solve, and the tolerance a priced result must
# meet. 1e-6 matches the £ rounding the schedule applies (2 dp on annual £ figures, 6 dp on kW):
# an incumbent within 1e-6 of proven optimal cannot move a reported figure. HiGHS's own default is
# 1e-4, which can.
_MIP_REL_GAP = 1e-6

# The kind→treatment registry (W7-2, C026): every DispatchConstraintDescriptor kind arriving on
# the ``problem.constraints`` seam is one of —
# - STRUCTURAL: enforced by the model's own bound/constraint construction above (the charge upper
#   bound encodes min(MIC, ASC) − load; the warranty throughput cap is the dense annual row);
# - COMPILED: built into window constraints here when the descriptor carries a typed
#   `AvailabilityCommitment` payload. A compilable kind with **no payload** is *declared, not
#   contracted* — today's universal state until the W7-4 producer exists — and changes nothing
#   (existing solves stay byte-identical; commitments only ever activate additively);
# - DEFERRED: not a dispatch-time constraint (deliverability is the sizing lane's closed-form
#   end-of-tenor check, W7-3).
# An unknown kind warns loudly: the seam is the single audit path, nothing is silently dropped.
# `load_service` (issue 017 / D42) is compiled from `SiteModel.provisioning_requirements`
# directly — the site is the contract carrier — not via the descriptor seam below.
# `export_limit` (issue 018 / D43) is STRUCTURAL: the MEC bound on the overlay `export` variable
# is built into the model's own bound construction (`_solve_milp` below, behind an aligned wholesale
# price series), exactly like the MIC/ASC charge bound — not compiled off this seam. It stays a
# skip here (behaviour-neutral: DEFERRED and STRUCTURAL both `continue` in `_compile_commitments`).
_STRUCTURAL_KINDS = frozenset({"connection_limit", "throughput_cap", "export_limit"})
_COMPILED_KINDS = frozenset({"availability_window", "held_state_of_energy"})
_DEFERRED_KINDS = frozenset({"deliverability"})


def levy_rate_gbp_per_kwh(site: SiteModel) -> float:
    """The site's flat £/kWh levy stack (CCL, RO, FiT, BSUoS, AAHEDC …) — 0.0 when none is declared.

    One definition, three consumers: the customer bill (`solve`), the dispatch objective price
    (`_solve_milp` — F-03) and, through the same tariff stack, finance's ``levies_on_charge`` cost
    line. They must agree or the optimiser optimises a different site from the one being priced.
    """
    levies = site.tariff_stack.levies or {}
    return float(sum(v for v in levies.values() if isinstance(v, (int, float))))


def windowed_levy_series_gbp_per_kwh(
    site: SiteModel, timestamps: pd.DatetimeIndex
) -> tuple[np.ndarray, list[str]]:
    """The site's TIME-WINDOWED £/kWh levy component, per half-hour, plus its labelled notes.

    Dense and as long as ``timestamps``; zeros outside every window, which is a measured £0 (the
    levy is genuinely not charged there) and not a D45 null.

    **Why a windowed levy cannot go in the flat map.** `levy_rate_gbp_per_kwh` above sums
    ``tariff_stack.levies`` and the objective applies that total to EVERY half-hour. That is right
    for CCL, RO, CfD, BSUoS, AAHEDC and Nuclear RAB — an independent literature review confirmed all
    of them bite on boundary import across the year — and catastrophically wrong for the GB Capacity
    Market supplier obligation, which is charged on 16:00–19:00 working-day November–February
    consumption only. Adding a ~30 p/kWh Capacity Market line to the flat map would charge it on
    every charged kWh at 03:00 in July and destroy the dispatch. So the windowed component is a
    separate array, added to the objective price in the right half-hours only.

    Two consumers, exactly like `levy_rate_gbp_per_kwh`'s three: the dispatch objective price
    (`_solve_milp`), and the schedule (which carries the array so the customer bill charges it on
    in-window import and `windowed_levy_avoidance_gbp` reports the £ finance credits). They must be
    the same array or the optimiser optimises a different site from the one being priced.
    """
    return windowed_levy_rate_series(site.tariff_stack, timestamps)


def duos_absence_warning(absent: tuple[str, ...]) -> str | None:
    """The labelled warning for DUoS band rates that are ABSENT rather than zero (D45).

    ``None`` when every band the site faces carries a declared rate (including a genuine ``0.0``,
    which is a fact and must stay silent — ``or 0.0`` swallowed that case too).

    One definition, two consumers (`MilpDispatchSolver._solve_milp` and `MpcDispatchSolver.solve`),
    because both build the same ``price_t`` from the same array and must report the same gap.

    `duos_rate_series_with_absences` keeps the returned array DENSE — an absent band contributes
    zeros so every consumer keeps a full-length series — so the array alone cannot distinguish
    "this site faces no red-band charge" from "nobody has told us the red-band rate". That is the
    D45 defect: the zero enters the objective's ``price_t``, the customer bill and
    `DUOS_RED_BAND_AVOIDANCE` (one of only TWO priced-floor streams) looking exactly like a
    measured result. This function is what makes the gap visible instead.

    Two shapes, and the direction of the error differs, so the text does too:

    - **every band absent** — the DUoS component drops out of ``price_t`` uniformly. There is no
      band spread to monetise, so the reported stream is £0 exactly and
      `value_stack.gates.has_duos_rate_data` independently closes the stream on the same null.
      Conservative, and the two layers agree. Live case: Colnbrook and Tanhouse.
    - **some bands absent** — the modelled spread between a DECLARED band and a zeroed absent one
      is wider than the spread the site actually faces, so the optimiser is solved against prices
      the site does not face. Where the declared band is the expensive one (red) this OVERSTATES
      DUoS avoidance, because the off-peak charging cost it should net off is missing.

    No rate is derived here. Deriving a published DNO band rate is sourcing, not invention (D45),
    but it is an OFFLINE step that records provenance and writes the value back onto the
    `TariffStack` — the pipeline only ever reads recorded values (D44, `schemas/derived.py`). The
    release already carries the table to do it from; the warning names it rather than executing it.
    """
    if not absent:
        return None
    bands = ", ".join(absent)  # fixed red/amber/green order from the declaration map
    if len(absent) == 3:
        detail = (
            "The DUoS component therefore drops out of the dispatch price entirely and "
            "DUOS_RED_BAND_AVOIDANCE reports £0 — a floor stream reading zero because an input is "
            "EMPTY, not because the site faces no charge. The stream gate closes on the same null, "
            "so no revenue is claimed, and the £0 is conservative; the dispatch is still solved "
            "without the red-band signal it should be shaped by."
        )
    else:
        detail = (
            "The declared band(s) are priced against absent ones read as zero, so the modelled "
            "band spread is WIDER than the spread the site faces: where the declared band is the "
            "expensive one, DUOS_RED_BAND_AVOIDANCE is OVERSTATED because the off-peak charging "
            "cost that should net against it is missing. The stream gate only tests the red rate, "
            "so a partially-populated tariff passes it and prices this overstatement."
        )
    return (
        f"DUoS BAND RATE ABSENT, NOT ZERO (D45) — {bands}: no rate is recorded for "
        f"{'this band' if len(absent) == 1 else 'these bands'}, and an absent rate is a MISSING "
        f"INPUT, never a measured £0. {detail} Derive the rate OFFLINE from the site's DNO "
        "charging statement (the release carries the table: duos.unit_rates_p_per_kwh_by_dno, plus "
        "duos.tcr_band_thresholds_kva for the capacity band) and record it onto "
        "tariff_stack.duos_red_rate / duos_amber_rate / duos_green_rate with its provenance. "
        "Nothing is derived inside the solve, and no rate is invented here."
    )


def fixed_line_absence_warnings(site: SiteModel) -> list[str]:
    """Labelled warnings for the battery-invariant bill lines whose rate is ABSENT (D45).

    The standing charge and the capacity/availability charge used to be read as
    ``standing_charge_per_day_gbp or 0.0``, which conflated three different states into one silent
    £0: the tariff genuinely has no such line (a FACT, correctly recorded as ``0.0``), nobody has
    recorded the line yet (a NULL), and — because ``or`` triggers on falsiness rather than on
    ``None`` — a genuine ``0.0`` was swallowed as though it were the missing case. Separating them
    is the whole point: ``is None`` is the test, and a declared ``0.0`` stays silent because it is
    evidence.

    An absent line still contributes £0 to the bill, because inventing a rate on the pricing path is
    the worse error. Both lines are battery-invariant — billed on the agreed capacity and the
    calendar, not on anything the dispatch chooses — so an absent one cannot move a dispatch
    decision, a value stream, the revenue floor or the DSCR. It understates BOTH sides of the
    customer bill by the same £, which leaves the reported SAVING unaffected and only makes the
    totals incomplete. That is worth saying rather than hiding, which is what this returns.

    Emitted from `_solve_milp` rather than from `solve`. That is deliberate and was measured, not
    assumed: on the production sizing path the reported `OptimisationResult` inherits the warnings
    of the candidate solve, and the candidates do not come through the `solve` Protocol entry point
    — a demo run makes exactly two `solve` calls, whose warnings are discarded, so a warning raised
    there never reaches the run record. `_solve_milp`'s warnings do. An absence nobody can see in
    the record is the same defect as no warning at all.
    """
    out: list[str] = []
    tariff = site.tariff_stack
    if tariff.standing_charge_per_day_gbp is None:
        out.append(
            "STANDING CHARGE NOT RECORDED (D45): tariff_stack.standing_charge_per_day_gbp is "
            "absent, so the customer bill carries £0 for the daily standing charge. That £0 is an "
            "UNKNOWN read as zero, not a measured absence — both bill totals are short by the same "
            "amount, so the reported SAVING is unaffected and only the totals are incomplete (the "
            "line is battery-invariant: no dispatch decision, value stream, floor or DSCR figure "
            "depends on it). Record the rate from the site's supply contract; if the site truly "
            "faces no standing charge, record 0.0 so the fact is stated rather than inferred."
        )
    if tariff.capacity_charge_gbp_per_kva_day is None:
        capacity_kva = site.connection.asc_kw or site.connection.mic_kw or 0.0
        out.append(
            "CAPACITY/AVAILABILITY CHARGE NOT RECORDED (D45): "
            "tariff_stack.capacity_charge_gbp_per_kva_day is absent, so the customer bill carries "
            f"£0 for the £/kVA/day capacity charge on {round(float(capacity_kva), 3)} kVA of "
            "agreed capacity. That £0 is an UNKNOWN read as zero, not a measured absence; it is "
            "battery-invariant so it moves no dispatch decision and no revenue figure, but both "
            "bill totals are short by the same amount. Record the rate from the DNO/supplier "
            "charging statement (the release carries duos.demand_residual_bands_gbp_per_day and "
            "duos.tcr_band_thresholds_kva to derive the TCR residual OFFLINE); if the site "
            "genuinely faces no capacity charge, record 0.0 so the fact is stated."
        )
    return out


def degradation_cost_gbp_per_kwh(assumptions: AssumptionLibrary) -> tuple[float, str]:
    """The marginal cycle cost the objective charges per kWh DISCHARGED (DEG-01).

    Returns ``(£/kWh, warning)``. Two release homes are tried in order — the better-named
    ``degradation.marginal_cycle_cost_gbp_per_mwh_discharged``, then the one shipped releases
    actually use, ``battery_opex.degradation_marginal_cost_gbp_per_mwh`` (gb_v1_2…gb_v1_7) — so the
    versioned, provenanced value wins whenever there is one. Only a release older than gb_v1_2
    falls through to the sourced first-pass figure from
    ``docs/bess_degradation_requirements.md`` C-34, with a loud data ask. **Never zero** unless the
    release explicitly says zero, because a zero here means cycling is free and every trade in the
    £11–33/MWh spread band is booked profitable while being loss-making.

    Whichever home supplies it, the value stays unconfirmed today, so ``_solved`` gates BOTH keys
    into the bankability check and every run remains ``not_bankable`` on this input.

    Units matter: the objective's prices are £/kWh and its volumes kWh, so the £/MWh figure is
    divided by 1,000 here — once, at the only place that reads it.
    """
    # Two release homes, most-precise first. ``_DEG_COST_KEY`` is the name this quantity SHOULD
    # have (it states the basis: per MWh discharged, cycle wear only). ``_DEG_COST_KEY_LEGACY`` is
    # where it actually lives today: gb_v1_6 carried it at £5.0/MWh and gb_v1_7 re-derived it to
    # £21.3/MWh from the same C-34 basis as the fallback below (C_repl 120k/MWh, 6,000 EFC,
    # eta_dis 0.94) — two independent derivations that agree, which is why reading it is safe.
    # Without this second lookup the shipped, provenanced release value goes UNREAD and the solver
    # silently uses its own constant instead: the same number, but unsourced at the point of use.
    for key, section, leaf in (
        (_DEG_COST_KEY, "degradation", "marginal_cycle_cost_gbp_per_mwh_discharged"),
        (_DEG_COST_KEY_LEGACY, "battery_opex", "degradation_marginal_cost_gbp_per_mwh"),
    ):
        released = numeric(maybe(assumptions, section, leaf))
        if released is not None and released >= 0.0:
            return released / 1000.0, (
                f"marginal cycle cost £{released:g}/MWh discharged, read from the assumption "
                f"release ({key}); the dispatch objective is profit, not revenue (DEG-01/C-34). "
                "DECISION cost only: finance books no degradation opex line (D36 charges the fade "
                "once, as the revenue decay), so this filters trades without appearing as a £ in "
                "the pack."
            )
    return _DEG_COST_GBP_PER_MWH_DEFAULT / 1000.0, (
        f"DEGRADATION COST NOT IN THE ASSUMPTION RELEASE (data ask, {_DEG_COST_KEY}): the "
        f"dispatch objective charges the sourced first-pass figure £"
        f"{_DEG_COST_GBP_PER_MWH_DEFAULT:g}/MWh discharged from docs/bess_degradation_"
        "requirements.md C-34 (c_deg = C_repl / (N_EFC,EOL x eta_dis); £120k/MWh, 6,000 EFC, "
        "eta_dis 0.94), calibrated at a DEEP (~80% DoD) duty — it overcharges shallow cycling by "
        f"up to ~8x and undercharges the deep tail (C-35). {_DEG_BREAK_EVEN_NOTE}. Re-derive "
        "C_repl from a current, dated replacement cost and publish it as a release key before any "
        "bankable use (C-37); this figure is INDICATIVE, not bankable. NOTE the open seam: it is a "
        "DECISION cost only — finance books no degradation opex line, so the wear it filters "
        "trades on does not yet appear as a £ in the pack."
    )


def _soc_window_fractions(assumptions: AssumptionLibrary) -> tuple[float, float, str | None]:
    """The usable SoC window ``(soc_min, soc_max)`` as fractions of nameplate energy (D-169).

    Read from the release beside the warranty cycle cap they belong with. Returns
    ``(0.0, 1.0, warning)`` — today's full 0–100% sweep — when the release carries neither key or
    carries an unusable pair, because the register's own caveat forbids substituting a literature
    window for the product's datasheet value. A partial or invalid declaration is refused as a pair
    (never half-applied) and warned.
    """
    lo = numeric(maybe(assumptions, "warranty_cycle_limits", "soc_min_fraction"))
    hi = numeric(maybe(assumptions, "warranty_cycle_limits", "soc_max_fraction"))
    if lo is None and hi is None:
        return (
            0.0,
            1.0,
            (
                f"USABLE SoC WINDOW NOT IN THE ASSUMPTION RELEASE (data ask, {_SOC_MIN_KEY} / "
                f"{_SOC_MAX_KEY}): state of energy is optimised over the FULL 0-100% of nameplate, "
                "which overstates deliverable energy per cycle by the BMS reserve (typically "
                "5-10%) "
                "and can breach the warranty DoD terms this same solve's cycle cap is honouring "
                "(D-169). Published windows disagree by up to 15 percentage points (15-95%, "
                "10-95%, 15-85% — docs/bess_constants_register.md, 'Usable state-of-charge "
                "window'), and the register states the value is a warranty/vendor parameter, not a "
                "physical constant, so "
                "no window is assumed here. Supply the product datasheet window as a release key."
            ),
        )
    lo_v = 0.0 if lo is None else lo
    hi_v = 1.0 if hi is None else hi
    if not (0.0 <= lo_v < hi_v <= 1.0):
        return (
            0.0,
            1.0,
            (
                f"usable SoC window declared as [{lo_v:g}, {hi_v:g}] fraction of nameplate is "
                "not a "
                f"valid window (need 0 <= {_SOC_MIN_KEY} < {_SOC_MAX_KEY} <= 1) — NOT applied; the "
                "full 0-100% sweep stands (never half-applied, never silently corrected). Fix the "
                "release."
            ),
        )
    note = None
    if lo is None or hi is None:
        note = (
            f"usable SoC window only partially declared ({_SOC_MIN_KEY}="
            f"{'absent' if lo is None else lo} / {_SOC_MAX_KEY}="
            f"{'absent' if hi is None else hi}); the missing side defaults to the nameplate bound "
            f"({lo_v:g}/{hi_v:g}) — declare both sides explicitly."
        )
    return lo_v, hi_v, note


def _energy_revenue_term(
    price_da: Any,
    buy_price: np.ndarray | None,
    coords: Any,
    w_da: Any,
    charge: Any,
    discharge: Any,
    dt: float,
) -> Any:
    """The objective's delivered-price term: ``Σ w·(sell·discharge − buy·charge)·Δt`` (WP-UW-07).

    ``buy_price`` is ``None`` when every half-hour's energy band is priced as itself, and the term
    is then the historic ``Σ price·w·(discharge − charge)·Δt``, expression for expression, so the
    model matrix is unchanged. Otherwise charging pays ``buy_price`` (the no-spread envelope's
    upper rate on unresolved half-hours) and discharging earns ``price`` (its lower rate).
    """
    if buy_price is None:
        return (price_da * w_da * (discharge - charge) * dt).sum()
    import xarray as xr

    buy_da = xr.DataArray(buy_price, coords=[coords])
    return (price_da * w_da * discharge * dt).sum() - (buy_da * w_da * charge * dt).sum()


@dataclass(frozen=True)
class ReactiveInputs:
    """The metered reactive quantity and its price, aligned to one solve's half-hours (WP-UW-07).

    ``site_kvar`` is ``m_t = max(RI_t, RE_t)`` as an average kVAr over each interval (the LC14
    quantity: whichever quadrant is larger); ``rate_gbp_per_kvarh`` is the published CDCM excess
    reactive rate on the stack's `network_tariff`, ``None`` when no rate is stated (EDCM has none).
    """

    site_kvar: np.ndarray
    rate_gbp_per_kvarh: float | None
    rate_source: str | None


def _reactive_kvar_from_frame(
    df: pd.DataFrame, interval_min: int
) -> tuple[np.ndarray | None, str | None]:
    """``max(RI, RE)`` in average kVAr per row of ``df``, or ``(None, note)`` when not usable.

    Both quadrants are required. A file that meters one reactive channel only is NOT modelled: the
    missing quadrant is unknown, and taking it as zero would understate ``max(RI, RE)`` wherever the
    site runs leading. No partial fill, no invented channel.
    """
    per_hour = 60.0 / float(interval_min)

    def _channel(stem: str) -> np.ndarray | None:
        if f"{stem}_kvar" in df.columns:
            return np.asarray(df[f"{stem}_kvar"].to_numpy(dtype=float))
        if f"{stem}_kvarh" in df.columns:
            return np.asarray(df[f"{stem}_kvarh"].to_numpy(dtype=float)) * per_hour
        return None

    ri, re_ = _channel("reactive_import"), _channel("reactive_export")
    if ri is None and re_ is None:
        return None, None
    if ri is None or re_ is None:
        present = "reactive_import" if ri is not None else "reactive_export"
        return None, (
            f"the half-hourly file meters one reactive channel only ({present}); reactive power is "
            "NOT modelled because LC14 charges max(reactive import, reactive export) and the "
            "missing quadrant is unknown, never taken as zero. Supply both channels."
        )
    return np.maximum(ri, re_), None


def _reactive_connection_screen(
    base_kw: np.ndarray,
    charge_ub: np.ndarray,
    site_kvar: np.ndarray,
    limit_kva: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Where the kVA connection polygon needs a row, and where charging must be barred.

    Returns ``(charge_ub, row_mask, n_outside)``. ``base_kw`` is the active import with the battery
    idle. Where the metered site ALREADY sits outside the inscribed polygon of ``limit_kva`` (a
    real breach the battery did not cause), charging is barred and no row is built, which keeps the
    solve feasible without relaxing anything. Elsewhere a row is built only where full charging
    could carry the point outside, so a site well inside its connection adds no row at all.
    """
    k = _OCTAGON_K
    p0 = np.abs(base_kw)
    outside = (p0 + k * site_kvar > limit_kva + 1e-9) | (k * p0 + site_kvar > limit_kva + 1e-9)
    capped = np.where(outside, 0.0, charge_ub)
    reach = base_kw + capped
    rows = (~outside) & (site_kvar > 0.0)
    rows &= (reach + k * site_kvar > limit_kva) | (k * reach + site_kvar > limit_kva)
    return capped, rows, int(outside.sum())


def _add_inverter_pq_rows(
    model: Any,
    charge: Any,
    export_flow: Any,
    comp: Any,
    power: float,
    forced_kw: np.ndarray,
    mask: np.ndarray,
    coords: Any,
) -> None:
    """The inverter's P-Q capability as its inscribed octagon, where reactive is supplied.

    ``A_t = discharge_t (+ export_t) + charge_t + forced_t >= |P_t|`` (at most one of charge and
    discharge is non-zero at a vertex, so ``A_t`` is the inverter's real power), and
    ``A_t + k c_t <= S``, ``k A_t + c_t <= S`` with ``S`` the rated kW taken as the inverter's kVA.
    """
    import xarray as xr

    k = _OCTAGON_K
    m = xr.DataArray(mask, coords=[coords])
    model.add_constraints(
        export_flow + charge + k * comp <= xr.DataArray(power - forced_kw, coords=[coords]),
        name="reactive_inverter_octagon_p",
        mask=m,
    )
    model.add_constraints(
        k * export_flow + k * charge + comp <= xr.DataArray(power - k * forced_kw, coords=[coords]),
        name="reactive_inverter_octagon_q",
        mask=m,
    )


def _add_connection_kva_rows(
    model: Any,
    charge: Any,
    discharge: Any,
    comp: Any,
    base_kw: np.ndarray,
    site_kvar: np.ndarray,
    limit_kva: float,
    mask: np.ndarray,
    coords: Any,
) -> None:
    """The connection's kVA limit as the inscribed polygon in active import and residual reactive.

    ``AI_t = base_t + charge_t - discharge_t`` and ``r_t = m_t - c_t``:
    ``AI_t + k r_t <= L`` and ``k AI_t + r_t <= L`` with ``L = min(MIC, ASC)``. Replaces the scalar
    power-factor de-rating whenever reactive is modelled (never both).
    """
    import xarray as xr

    k = _OCTAGON_K
    m = xr.DataArray(mask, coords=[coords])
    model.add_constraints(
        charge - discharge - k * comp
        <= xr.DataArray(limit_kva - base_kw - k * site_kvar, coords=[coords]),
        name="reactive_connection_kva_p",
        mask=m,
    )
    model.add_constraints(
        k * charge - k * discharge - comp
        <= xr.DataArray(limit_kva - k * base_kw - site_kvar, coords=[coords]),
        name="reactive_connection_kva_q",
        mask=m,
    )


def _add_reactive_excess_epigraph(
    model: Any,
    charge: Any,
    discharge: Any,
    comp: Any,
    load_kw: np.ndarray,
    site_kvar: np.ndarray,
    dt: float,
    coords: Any,
) -> Any:
    """``x_t >= (m_t - c_t) dt - 0.33 AI_t dt``, ``x_t >= 0``: the chargeable kVArh per interval.

    Rows only where the site has reactive (``m_t > 0``); elsewhere ``x_t`` is minimised to zero.
    The LC14 rule counts reactive only at times of active import, which is non-convex; the row
    counts it at ``AI_t <= 0`` too, which over-charges (conservative, declared in the warning); the
    reported £ uses the exact rule (`HalfHourlyDispatchSchedule.reactive_charge_after_gbp`).
    """
    import xarray as xr

    th = _REACTIVE_THRESHOLD
    x = model.add_variables(lower=0.0, coords=[coords], name="reactive_excess_kvarh")
    model.add_constraints(
        x + comp * dt + (th * dt) * charge - (th * dt) * discharge
        >= xr.DataArray(site_kvar * dt - th * dt * load_kw, coords=[coords]),
        name="reactive_excess_epi",
        mask=xr.DataArray(site_kvar > 0.0, coords=[coords]),
    )
    return x


def _reactive_charge_term(rate_gbp_per_kvarh: float, excess: Any, w_da: Any) -> Any:
    """The objective's reactive cost, ``rate · Σ w_t x_t`` (£ over the modelled horizon)."""
    return rate_gbp_per_kvarh * (excess * w_da).sum()


def _reactive_modelled_note(
    inputs: ReactiveInputs,
    *,
    power: float,
    limit_kva: float,
    n_outside: int,
    n_no_import: int,
) -> str:
    """The text that replaces the unity-power-factor warning when reactive is modelled."""
    n = int(inputs.site_kvar.size)
    if inputs.rate_gbp_per_kvarh is None:
        rate = (
            "no reactive unit rate is declared on the tariff (EDCM has none; for a CDCM site the "
            "published excess reactive rate is a data ask), so the kVA physics is modelled and no "
            "reactive £ is priced"
        )
    else:
        rate = (
            "excess reactive above 0.33 kVArh per kWh is priced at the published rate "
            f"£{inputs.rate_gbp_per_kvarh:.5f}/kVArh ({inputs.rate_source}); the objective counts "
            f"reactive in the {n_no_import} half-hour(s) with no metered active import, which the "
            "LC14 rule does not charge (conservative), and the reported £ uses the exact rule. The "
            "reactive £ is on the customer bill; finance does not yet book it as a cost line "
            "(stated gap: the P&L omits the reactive saving, the conservative direction)"
        )
    breach = (
        ""
        if n_outside == 0
        else f" In {n_outside} half-hour(s) the metered site already exceeds the connection "
        "polygon with the battery idle: charging is barred there and the breach is the site's own."
    )
    return (
        "REACTIVE MODELLED (WP-UW-07; the unity-power-factor assumption is retired for this run): "
        f"the site's metered reactive channels (max of reactive import and export) cover all {n} "
        f"solved half-hours; the inverter may supply reactive power inside the inscribed octagon "
        f"of its rated S = {power:g} kVA; the connection is held to min(MIC, ASC) = "
        f"{limit_kva:g} kVA as an inscribed polygon in active and reactive power, replacing any "
        f"scalar power-factor de-rating; {rate}. Reactive provision is assumed lossless (no "
        f"sourced figure).{breach}"
    )


def _charge_power_factor(assumptions: AssumptionLibrary) -> tuple[float, str]:
    """The assumed power factor de-rating the kW charge bound against a kVA connection (B-17).

    Returns ``(power_factor, warning)``. ``1.0`` — today's behaviour — whenever the release carries
    no sourced factor, because no site in this schema supplies a reactive/kVArh series and a power
    factor is never invented here. The warning states the exposure and asks for the data.
    """
    pf = numeric(maybe(assumptions, "mic_asc_headroom", "assumed_power_factor"))
    if pf is not None and 0.0 < pf <= 1.0:
        return pf, (
            f"charge headroom de-rated to kVA at power factor {pf:g} (release key "
            f"{_POWER_FACTOR_KEY}): the connection limits kVA, not kW, so the usable kW bound is "
            f"min(MIC, ASC) x {pf:g} (B-17). Still an ASSUMED factor, not a metered kVArh series."
        )
    if pf is not None:
        return 1.0, (
            f"{_POWER_FACTOR_KEY} = {pf:g} is not a usable power factor (need 0 < pf <= 1) — NOT "
            "applied; the unity-PF bound stands (never silently corrected)."
        )
    return 1.0, (
        "UNITY POWER FACTOR ASSUMED (B-17, data ask): no site in this schema carries a reactive / "
        "kVArh half-hourly series, so kVA is taken as equal to kW throughout — the charge bound "
        "against min(MIC, ASC), the capacity/availability charge on the customer bill, and the "
        "exceeded-capacity headroom. Real C&I sites run 0.90-0.95 PF, so TRUE kVA exceeds the kW "
        "this model uses by ~5-11% and charging at full kW near the MIC can itself trigger the "
        "exceeded-capacity charge this model reports as impossible (DUoS exceeded capacity = "
        "max(2*sqrt(AI^2 + max(RI,RE)^2) - MIC, 0) per half hour, charged for the whole billing "
        "period; excess reactive above 33% of kWh = PF 0.95 is separately chargeable - "
        "docs/bess_constants_register.md, LPN Use of System Charging Statement 2026 paras "
        "2.41/2.48-2.53). No power factor is invented here: supply a metered kVArh series, or a "
        f"sourced {_POWER_FACTOR_KEY}, to price the reactive exposure. A file carrying both "
        "reactive channels (reactive_import_kvarh, reactive_export_kvarh) replaces this assumption "
        "with the modelled reactive variable (WP-UW-07)."
    )


def _achieved_mip_gap(model: Any) -> tuple[float | None, str]:
    """The relative optimality gap HiGHS actually achieved on a binary solve (B-09).

    Returns ``(gap, note)`` — ``(None, note)`` when the running solver stack does not expose it, so
    the caller can say "unreadable" instead of implying a gap it never read. Read through linopy's
    ``solver_model`` (a ``highspy.Highs`` for this backend) rather than parsed from a log, and
    defensively: a linopy/highspy version that renames or drops ``getInfo().mip_gap`` degrades to
    the honest note, never to an exception on the pricing path.
    """
    solver_model = getattr(model, "solver_model", None)
    get_info = getattr(solver_model, "getInfo", None)
    if get_info is None:
        return None, (
            "SOLVER OPTIMALITY GAP UNREADABLE (B-09): the MILP requested a relative gap of "
            f"{_MIP_REL_GAP:.0e} but this solver stack exposes no gap to read back "
            f"(solver_model={type(solver_model).__name__}), so the reported £ carries no measured "
            "statement of its distance from proven optimal."
        )
    try:
        gap = float(get_info().mip_gap)
    except Exception:  # pragma: no cover - version-dependent solver introspection
        return None, (
            "SOLVER OPTIMALITY GAP UNREADABLE (B-09): the MILP requested a relative gap of "
            f"{_MIP_REL_GAP:.0e} but reading the achieved gap from the HiGHS solution object "
            "failed, so the reported £ carries no measured statement of its distance from proven "
            "optimal."
        )
    if not math.isfinite(gap) or gap < 0.0:
        return None, (
            f"SOLVER OPTIMALITY GAP UNREADABLE (B-09): HiGHS reported mip_gap={gap!r}, which is "
            "not a usable relative gap."
        )
    return gap, (
        f"MILP solved to a relative optimality gap of {gap:.3e} (requested "
        f"{_MIP_REL_GAP:.0e}); the reported £ is within that distance of proven optimal."
    )


#: The response product the study-basis ancillary rate is built from, and the two library keys that
#: hold its measured throughput and the rule that says the budget is shared. Named once so the
#: throughput charged here can never drift from the rate that priced the stream: the consent notes
#: on every campaign site record read "product DCL, net_gbp_per_kw_yr lower bound GBP 20.75/kW/yr",
#: and 20.75 x (1 - 0.20) = 16.60 = the GBP 16,600/MW/yr those records carry.
_ANCILLARY_PRODUCT = "DCL"
_ANCILLARY_THROUGHPUT_KEY = (
    "ancillary_services",
    "study_basis_availability_gbp_per_kw_yr_by_product",
)
_EXCLUSIVITY_MATRIX_KEY = ("ancillary_services", "study_basis_stream_exclusivity_matrix")
_EXCLUSIVITY_RULE_2 = "rule_2_one_warranty_budget"


def _ancillary_response_throughput_kwh(
    site: SiteModel,
    assumptions: AssumptionLibrary,
    *,
    power_kw: float,
    annual_factor: float,
) -> tuple[float, list[str]]:
    """The response service's own throughput, charged against the ONE warranty budget (rule 2).

    Returns ``(kwh_over_this_horizon, notes)``; ``(0.0, [])`` where the site is not being paid for
    the stream or the library publishes no measured throughput, so every such run stays
    byte-identical.

    **THE DEFECT (2026-08-19).** The ancillary rate is built NET of the degradation cost of its own
    measured throughput (`ancillary_services.study_basis_availability_gbp_per_kw_yr_by_product`,
    DCL `throughput_kwh_per_kw_yr` = [58.1, 69.0] kWh/kW/yr at GBP 21.3/MWh) - so the MONEY is
    accounted for - but that throughput was never charged against the warranty cycle budget the
    dispatch enforces. On the two audited campaign runs the budget was already 92-100 pct consumed
    by priced dispatch, and the ancillary service's wear sat entirely on top: 119-125 pct of a cap
    the MILP treats as binding. The library's own release header states the rule being broken
    ("the streams are NOT additive, because at 2 h duration wholesale arbitrage alone consumes
    100 pct of the warranty throughput budget and DRL alone consumes 125-144 pct of it").

    **THIS IS PHYSICS, NOT ELIGIBILITY, WHICH IS WHY IT IS THE ONE RULE WIRED.** The cells have a
    finite throughput and a response delivered is a cycle spent; D58 limb 1 waives the REGULATORY
    exclusivity and expressly keeps "one annual warranty throughput budget" as the half that is
    enforced in the dispatch. Rules 1, 3, 4 and 7 of the same matrix are regulatory or eligibility
    rules that D58 limb 1 settles as waived and they stay UNWIRED - nothing here reads them.

    **NO STREAM IS GATED BY THIS.** The ancillary revenue is unchanged and stays priced in full;
    what changes is that the priced dispatch has less warranty headroom to spend, so the arbitrage,
    DUoS and DFS legs compete for what is left. Charged only where the site is actually being paid
    for the service, mirroring `prov_throughput`, so a site without the consent is untouched.

    **D56 FAVOURABLE END.** The published throughput is a RANGE and the LOW end is taken, which is
    the favourable pick: less wear charged, more headroom left for priced dispatch. The note states
    the other end.
    """
    if site.agreed_rate_for(MarketStream.ANCILLARY_DC_DM_DR) is None:
        return 0.0, []
    entry = maybe(assumptions, *_ANCILLARY_THROUGHPUT_KEY)
    value = entry.value if entry is not None else None
    row = None
    if isinstance(value, dict):
        products = value.get("by_product")
        if isinstance(products, dict):
            row = products.get(_ANCILLARY_PRODUCT)
    published = row.get("throughput_kwh_per_kw_yr") if isinstance(row, dict) else None
    per_kw_yr: float | None = None
    other_end: float | None = None
    if isinstance(published, list | tuple) and published:
        numbers = [float(v) for v in published if isinstance(v, int | float)]
        if numbers:
            per_kw_yr, other_end = min(numbers), max(numbers)
    elif isinstance(published, int | float):
        per_kw_yr = float(published)
    if per_kw_yr is None or per_kw_yr <= 0 or power_kw <= 0 or annual_factor <= 0:
        key = ".".join(_ANCILLARY_THROUGHPUT_KEY)
        return 0.0, [
            "ANCILLARY RESPONSE THROUGHPUT NOT CHARGED AGAINST THE WARRANTY BUDGET: "
            f"{key}.by_product.{_ANCILLARY_PRODUCT}.throughput_kwh_per_kw_yr publishes no usable "
            "figure, so the service's own wear is not deducted from the cycle cap. DIRECTION OF "
            "THE RESIDUAL ERROR: the priced dispatch is given warranty headroom the response "
            "service has already spent, so throughput-limited streams are OVERSTATED. DATA ASK: "
            "the measured annual throughput of the response product this site is paid for."
        ]
    annual_kwh = per_kw_yr * power_kw
    horizon_kwh = annual_kwh / annual_factor
    rule = maybe(assumptions, *_EXCLUSIVITY_MATRIX_KEY)
    rule_text = ""
    if rule is not None and isinstance(rule.value, dict):
        stated = rule.value.get(_EXCLUSIVITY_RULE_2)
        if isinstance(stated, str):
            rule_text = f" THE PUBLISHED RULE, VERBATIM: {stated}"
    return horizon_kwh, [
        f"ONE WARRANTY BUDGET ACROSS ALL STREAMS: the {_ANCILLARY_PRODUCT} response service this "
        f"site is paid for consumes {per_kw_yr:g} kWh/kW/yr of measured throughput, i.e. "
        f"{round(annual_kwh, 1)} kWh/yr on {power_kw:,.3f} kW, and that wear is DEDUCTED from the "
        "warranty cycle cap before any priced dispatch may spend it - exactly as the load-service "
        "obligation already is. It was previously priced into the ancillary rate (net of GBP "
        "21.3/MWh degradation) and charged against NO throughput budget, so the total ran at "
        "119-125 pct of the cap on the audited campaign runs. THE ANCILLARY REVENUE IS UNCHANGED "
        "and no stream is gated: what changes is how much warranty headroom the priced dispatch "
        f"has left to compete for. D56 FAVOURABLE END TAKEN: the published range is {per_kw_yr:g}"
        + (f"-{other_end:g}" if other_end is not None and other_end != per_kw_yr else "")
        + " kWh/kW/yr and the LOW end is charged, which leaves the most headroom."
        + rule_text
    ]


def _floor_value(sched: HalfHourlyDispatchSchedule) -> float:
    """The FIRM GBP/yr this schedule earns: exactly the dispatch-measured streams in the floor.

    Module-level rather than nested inside the agreed-capacity overlay so it can be asserted
    directly: the netting proof it feeds is the mechanism that stops the same pound being booked in
    both AGREED_CAPACITY_REDUCTION and the stream the overlay gave up to buy a lower peak, and a
    proof that cannot be tested is an assertion.

    **DFS ADDED (2026-08-19).** `dfs_utilisation_value_gbp` was omitted, and as of the D57 change
    set DFS is a priced-floor, dispatch-measured stream worth 6.4-6.8 pct of revenue on the audited
    campaign runs (GBP 428.71 at Harwell, GBP 1,044.91 at Tanhouse). If the overlay gives up
    DFS delivery in order to shave the annual peak, that sacrifice was NOT subtracted from the
    capacity saving, so the same pound was booked in both DFS and the capacity credit. Both audited
    runs happened to report ``sacrificed = GBP 0.00``, so the measured leak was zero on those two -
    but a proof whose docstring calls it "arithmetic rather than assertion" must cover every stream
    in the floor, not the ones that were in it when it was written.

    DIRECTION OF EFFECT: DOWN on the reported capacity saving, by exactly the DFS value the overlay
    gives up; zero where it gives up none.
    """
    return (
        sched.arbitrage_value_gbp
        + sched.duos_avoidance_value_gbp
        + sched.windowed_levy_avoidance_gbp
        + sched.dfs_utilisation_value_gbp
    )


def _commitment_mask(
    commitment: AvailabilityCommitment, timestamps: pd.DatetimeIndex
) -> np.ndarray:
    """The boolean half-hour mask of a commitment's windows over the metered horizon.

    Deterministic calendar mapping — month/day-of-week filters plus the same-day
    ``[start_time, end_time)`` span, unioned across the commitment's windows. Times compare
    against the metered timestamps exactly; nothing is interpolated or invented.

    Contract windows are GB local-clock content, exactly like the DUoS bands: a timezone-aware
    (e.g. UTC-labelled) metered index is converted to Europe/London before its clock fields are
    read, mirroring ``tariff_calendar._hours_weekday`` (F-003) — otherwise a 17:00 contracted
    window would sit at 18:00 BST all summer on a legal-obligation surface. Naive input is
    read as the local clock it documents.
    """
    idx = timestamps
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/London")
    n = len(idx)
    months = idx.month.to_numpy()
    days = idx.dayofweek.to_numpy()
    times = np.asarray(idx.time)
    total = np.zeros(n, dtype=bool)
    from datetime import time as _time

    for w in commitment.windows:
        m = np.ones(n, dtype=bool)
        if w.months is not None:
            m &= np.isin(months, w.months)
        if w.days_of_week is not None:
            m &= np.isin(days, w.days_of_week)
        m &= times >= w.start_time
        # end_time == 00:00 means the exclusive end of the day (24:00, W7-5): no upper bound.
        if w.end_time != _time(0, 0):
            m &= times < w.end_time
        total |= m
    return total


class _UnpriceableDispatch(RuntimeError):
    """The dispatch cannot be priced from the inputs given (F-01).

    Raised inside the model build and turned into an `INSUFFICIENT_DATA` result by `solve`, so the
    refusal travels the same honest-degrade path as every other missing-input case rather than
    escaping as an unhandled exception.
    """


@dataclass(frozen=True)
class ObjectiveWeights:
    """Objective coefficient overrides for the dispatch MILP.

    Default zero values select the annual-sizing branch (the three ``_EPS`` tie-breaks).
    Non-zero values activate the calendar/cycle aging soft penalties used by the MPC
    inner MILP — adapted from the reference MPCSolver alpha/beta parameters.

    **These are regularisers, not the cycle cost.** Since DEG-01 the real marginal cycle
    cost ``c_deg`` is part of the BASE objective in *both* branches, so it applies whatever
    is passed here — a zero-weight call is no longer a zero-wear objective. ``aging_beta``
    remains a *supplementary* soft throughput penalty on top of it (and on top of the hard
    warranty cap), never a substitute for either.

    ``aging_alpha``: soft calendar-aging penalty on mean SoC (biases toward lower SoC
    when it doesn't reduce bill savings).

    ``aging_beta``: soft cycle-aging throughput penalty (supplements the hard warranty
    cap and the priced ``c_deg`` but never replaces either).
    """

    aging_alpha: float = 0.0
    aging_beta: float = 0.0


@dataclass(frozen=True)
class WindowOutcome:
    """One rolling-horizon window solve: the schedule, or the refusal and its reason (WP-SIZE-01).

    Exactly one of ``schedule`` and ``refusal_status`` is set. ``refusal_status`` is never
    ``OPTIMAL``: it is the status the annual ``solve`` reports for the same refusal.
    """

    schedule: HalfHourlyDispatchSchedule | None
    refusal_status: SolverStatus | None
    reasons: tuple[str, ...]


class MilpDispatchSolver:
    """The production half-hourly dispatch solver (linopy/HiGHS). See module docstring."""

    MODEL_VERSION = "highs-milp-v1"

    def __init__(self, *, registry: StreamRegistry | None = None, seed: int = 0) -> None:
        self._registry = registry if registry is not None else default_registry()
        self._seed = int(seed)
        # The full half-hourly schedule from the most recent successful `solve` (reset each call).
        # Retained so a caller (the engine's `last_dispatch_profile` hook) can project a
        # representative day onto the QuoteRecord; ``None`` after an unsolved/insufficient-data run.
        self._last_schedule: HalfHourlyDispatchSchedule | None = None
        # The customer bill (before vs after, C017/W7-5) from the most recent successful solve —
        # same lifecycle as the schedule; ``None`` when unsolved or no time-mappable energy rate.
        self._last_customer_bill: CustomerBill | None = None
        # MICASC-3: the agreed-capacity/TCR-band overlay result from the most recent successful
        # solve — same lifecycle again. ``None`` when the site carries no capacity rate and none
        # is derivable, or the overlay solve did not reach an optimum.
        self._last_capacity_reduction: AgreedCapacityValue | None = None
        # WP-UW-07: each loaded file's metered reactive quantity ``max(RI, RE)`` (kVAr) by
        # timestamp, read once by `_load_series_signed` and aligned per solve. Absent key = the
        # file meters no reactive channel, and every solve on it is byte-identical to before.
        self._reactive_series: dict[str, pd.Series] = {}

    def last_schedule(self) -> HalfHourlyDispatchSchedule | None:
        """The full half-hourly schedule from the last successful ``solve`` (``None`` otherwise)."""
        return self._last_schedule

    def last_customer_bill(self) -> CustomerBill | None:
        """The bill decomposition from the last successful ``solve`` (``None`` otherwise)."""
        return self._last_customer_bill

    def last_capacity_reduction(self) -> AgreedCapacityValue | None:
        """The agreed-capacity/TCR-band overlay result from the last ``solve`` (MICASC-3).

        ``None`` when the overlay could not run at all — no £/kVA/day rate on the site and no
        licence area to derive one from, or no optimum. A returned record with
        ``annual_value_gbp == 0.0`` is a different statement: a measured or refused zero, with the
        reason on ``notes``.
        """
        return self._last_capacity_reduction

    # --- DispatchSolver Protocol -------------------------------------------

    def solve(self, problem: DispatchProblem) -> DispatchResult:
        """Solve one fixed-spec half-hourly dispatch into a contract-valid `DispatchResult`."""
        if not isinstance(problem, DispatchProblem):
            raise InvalidRequestError(
                f"MilpDispatchSolver.solve expects a DispatchProblem, got {type(problem).__name__}"
            )
        self._last_schedule = None  # reset; only a successful solve repopulates it
        self._last_customer_bill = None
        self._last_capacity_reduction = None
        site = problem.site_model
        assumptions = problem.assumptions

        max_import = site.load_data.max_import_kw
        if max_import is None or max_import <= 0:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: load_data.max_import_kw is missing or non-positive — a real "
                "half-hourly dispatch cannot bound charge load against the connection (MIC/ASC).",
            )
        if not site.load_data.half_hourly_file_ref:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: no half-hourly series (load_data.half_hourly_file_ref) — the "
                "real MILP needs the metered half-hourly load; only summary coverage is present. "
                "A load shape is never invented.",
            )

        try:
            load, net_load, timestamps, interval_min, load_warn = self._load_series_signed(
                site.load_data.half_hourly_file_ref
            )
        except HalfHourlyDataError as exc:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                f"Insufficient data: half-hourly series unreadable — {exc}",
            )
        if load.size == 0:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: the half-hourly series has no usable (non-null) load readings.",
            )

        rte_a = maybe(assumptions, "round_trip_efficiency", "ac_round_trip")
        rte = numeric(rte_a)
        if rte is None or not 0 < rte <= 1:
            return self._unsolved(
                problem,
                SolverStatus.INSUFFICIENT_DATA,
                "Insufficient data: round_trip_efficiency.ac_round_trip is missing or invalid; "
                "round-trip losses cannot be modelled.",
            )

        try:
            schedule, binding, solve_warn = self._solve_milp(
                problem, load, timestamps, interval_min, rte, net_load_kw=net_load
            )
        except _UnpriceableDispatch as exc:
            # F-01: refuse rather than price charge energy at £0 (validation Gate 1).
            return self._unsolved(
                problem, SolverStatus.INSUFFICIENT_DATA, str(exc), extra_warnings=list(load_warn)
            )
        if schedule is None:
            # Honest degrade keeps its evidence: the model-build warnings (over-commit /
            # over-hold explanations, HiGHS termination) explain WHY there is no dispatch.
            return self._unsolved(
                problem,
                SolverStatus.INFEASIBLE,
                "Dispatch MILP returned no optimal solution.",
                extra_warnings=[*load_warn, *solve_warn],
            )
        self._last_schedule = schedule  # retain for the representative-day projection
        # The customer bill (C017/W7-5): the site's own £/kWh levy lines over the same series
        # the MILP optimised — None when no time-mappable energy rate (honest pending seam).
        # Fixed battery-invariant lines (2026-07-07): the standing charge (365.25 days/yr) and
        # the capacity/availability charge (£/kVA/day × the agreed supply capacity — the ASC,
        # falling back to the MIC when no separate ASC is recorded).
        #
        # D45, and the reason the old ``or 0.0`` here was wrong: "the tariff has no such line" is a
        # FACT and "nobody has recorded the line yet" is a NULL, and they are not the same
        # statement. ``or 0.0`` collapsed both into a silent £0 — and swallowed a genuine ``0.0``
        # too, so a site that truly faces no standing charge was indistinguishable from one whose
        # rate is simply unknown. Split with ``is None``:
        #   * a declared rate (INCLUDING a real ``0.0``) is priced as declared, silently — it is
        #     evidence, and a recorded zero is the correct way to say "this site has no such line";
        #   * ``None`` is UNKNOWN. The line still contributes £0, because inventing a rate on the
        #     pricing path is the worse error, but the £0 is WARNED so the customer bill is read as
        #     incomplete rather than as a measured total.
        # Both lines are battery-invariant, so an absent one cannot move the dispatch, the value
        # streams or the revenue floor — it understates BOTH sides of the bill by the same £, i.e.
        # the reported saving is unaffected and only the totals are short.
        #
        # B-17: the ASC/MIC is already a kVA figure, so this line needs no conversion. Without a
        # metered reactive series the site's kVA is taken as equal to its kW everywhere else in
        # this model and `_charge_power_factor` emits the labelled warning and the data ask; the
        # excess-reactive charge is then absent from BOTH sides of this bill, a stated gap, not a
        # modelled zero. With both reactive channels metered (WP-UW-07) the schedule carries the
        # reactive quantity and the bill prices the excess-reactive line on both sides at the
        # published rate. Never invent a power factor to close the gap.
        tariff = site.tariff_stack
        levy_rate = levy_rate_gbp_per_kwh(site)
        # ``is None``, never ``or`` — see `fixed_line_absence_warnings`, which raises the labelled
        # absence for both lines from inside `_solve_milp` (the warning channel that actually
        # reaches the run record). Numerically this is identical to the old ``or 0.0``: the two
        # forms differ only when the value IS 0.0, and both then contribute £0.
        standing = (
            0.0
            if tariff.standing_charge_per_day_gbp is None
            else float(tariff.standing_charge_per_day_gbp) * 365.25
        )
        # NOTE (adjacent, deliberately NOT changed): the ``or`` chain below is the same falsy
        # pattern, but here it selects BETWEEN two capacity fields rather than substituting a price,
        # and rewriting it to ``is None`` would change behaviour for a recorded ASC of exactly 0.0
        # (today it falls through to the MIC). That is a schema-semantics question for the owner of
        # `connection`, not a null-as-zero defect on the pricing path — flagged, left alone.
        capacity_kva = site.connection.asc_kw or site.connection.mic_kw or 0.0
        capacity = (
            0.0
            if tariff.capacity_charge_gbp_per_kva_day is None
            else float(tariff.capacity_charge_gbp_per_kva_day) * capacity_kva * 365.25
        )
        self._last_customer_bill = schedule.customer_bill(
            levy_rate_gbp_per_kwh=levy_rate,
            standing_gbp_per_year=standing,
            capacity_gbp_per_year=capacity,
            tariff_provenance=tariff_provenance_phrase(tariff),
        )
        # MICASC-3: the agreed-capacity overlay. Runs AFTER the floor solve and never touches it —
        # `schedule`, `_last_schedule` and `_last_customer_bill` above are the floor's own and stay
        # so. Its £ is CONDITIONAL upside, reported on its own stream.
        self._last_capacity_reduction = self._capacity_reduction_overlay(
            problem, schedule, load, timestamps, interval_min, rte, net_load
        )
        return self._solved(
            problem,
            schedule,
            binding,
            [*load_warn, *solve_warn],
            capacity_reduction=self._last_capacity_reduction,
        )

    def _capacity_reduction_overlay(
        self,
        problem: DispatchProblem,
        floor_schedule: HalfHourlyDispatchSchedule,
        load: np.ndarray,
        timestamps: pd.DatetimeIndex,
        interval_min: int,
        rte: float,
        net_load: np.ndarray | None,
    ) -> AgreedCapacityValue | None:
        """Price the avoided agreed-capacity charge + TCR band drop (MICASC-3). ``None`` ⇒ absent.

        A SECOND solve, deliberately. The floor solve maximises FIRM bill savings and its optimum
        is the underwritten one; this overlay re-solves the same model with the site's own
        agreed-capacity rate attached to the residual-peak epigraph, so the dispatch actually
        trades against the peak. The floor solve's schedule, bill and reported £ are untouched.

        **The double-count proof, and it is arithmetic rather than assertion.** Whatever firm
        revenue the overlay dispatch gives up to buy its lower peak is measured — the same three
        priced-floor accessors evaluated on both schedules — and SUBTRACTED from the capacity
        saving. So the reported figure is the saving net of everything the floor already books,
        and it cannot restate a £ that tariff arbitrage or DUoS avoidance has already claimed.
        The legs are disjoint in kind too (energy £/kWh vs capacity £/kVA/day), but the netting is
        what makes that a proof instead of a story.

        Skipped, with the reason recorded on the stream, when: the site has no capacity rate and no
        licence area to derive one from; the solve is a typical-period proxy (a weighted subset
        cannot evidence an ANNUAL worst case); or the overlay solve does not reach an optimum.
        """
        site = problem.site_model
        assumptions = problem.assumptions
        if assumptions is None:
            return None
        full_series = floor_schedule.interval_weights is None
        basis = agreed_capacity_basis(site, assumptions)
        if basis is None:
            return None
        coverage = float(floor_schedule.annual_factor)
        if not full_series or coverage > _FULL_YEAR_COVERAGE_TOLERANCE:
            # Refuse to claim rather than claim from a proxy or from a part year: the helper
            # returns the £0-with-reason record and states which of the two refusals applies.
            # Refused BEFORE the overlay solve — there is no point paying for a second full-year
            # LP whose peak could not be underwritten anyway.
            return price_agreed_capacity_reduction(
                site,
                assumptions,
                modelled_peak_kw=float(basis.metered_peak_kw),
                rated_power_kw=float(floor_schedule.power_kw),
                full_series=full_series,
                coverage_factor=coverage,
            )
        try:
            overlay, _binding, _warn = self._solve_milp(
                problem,
                load,
                timestamps,
                interval_min,
                rte,
                net_load_kw=net_load,
                capacity_price_gbp_per_kw_year=(basis.capacity_rate_gbp_per_kva_day * 365.25),
            )
        except (_UnpriceableDispatch, InvalidRequestError):
            return None
        if overlay is None:
            return None

        sacrificed = _floor_value(floor_schedule) - _floor_value(overlay)
        overlay_peak = float(
            (overlay.site_load_kw + overlay.charge_kw - overlay.discharge_kw).max()
        )
        priced = price_agreed_capacity_reduction(
            site,
            assumptions,
            modelled_peak_kw=overlay_peak,
            rated_power_kw=float(overlay.power_kw),
            full_series=True,
            coverage_factor=coverage,
        )
        if priced is None:
            return None
        # The floor solve is the optimum of the un-priced objective, so `sacrificed` is >= 0 up to
        # HiGHS tolerance; a tiny negative is numerical and is floored at 0 rather than credited.
        sacrificed = max(0.0, sacrificed)
        net = max(0.0, priced.annual_value_gbp - sacrificed)
        floor_peak = float(
            (
                floor_schedule.site_load_kw + floor_schedule.charge_kw - floor_schedule.discharge_kw
            ).max()
        )
        notes = [
            *priced.notes,
            f"NETTED AGAINST THE PRICED FLOOR (no double-count): the overlay dispatch gives up "
            f"£{sacrificed:,.2f}/yr of FIRM bill saving (tariff arbitrage + DUoS red-band + "
            f"windowed levy, measured on both schedules) to buy its lower peak, and that £ is "
            f"SUBTRACTED here — gross £{priced.annual_value_gbp:,.2f}/yr, net "
            f"£{net:,.2f}/yr. The floor solve's own dispatch, bill and reported revenue are "
            f"unchanged by this overlay; only this conditional stream reads it.",
            f"PEAK MEASURED, not assumed: metered peak "
            f"{floor_schedule.site_load_kw.max():,.2f} kW; the FLOOR dispatch leaves "
            f"{floor_peak:,.2f} kW (it optimises the bill, not the peak); "
            f"the OVERLAY dispatch reaches {overlay_peak:,.2f} kW as the worst net import in any "
            f"settlement period of the modelled year.",
        ]
        return priced._replace(annual_value_gbp=round(net, 2), notes=notes)

    def solve_window(
        self,
        problem: DispatchProblem,
        load: np.ndarray,
        timestamps: pd.DatetimeIndex,
        interval_min: int,
        rte: float,
        initial_soe_kwh: float,
        weights: ObjectiveWeights | None = None,
    ) -> HalfHourlyDispatchSchedule | None:
        """Solve a single rolling-horizon window (binary MILP) for the MPC loop.

        Returns the full-window schedule, or ``None`` if infeasible.  The MPC caller
        applies only the first ``na_steps`` half-hours before re-solving with the
        actual SoE as the new ``initial_soe_kwh``.

        Not the ``DispatchSolver`` Protocol entry-point. ``MpcDispatchSolver`` calls
        ``solve_window_outcome`` instead, which keeps the refusal's reason and status.
        """
        return self.solve_window_outcome(
            problem, load, timestamps, interval_min, rte, initial_soe_kwh, weights
        ).schedule

    def solve_window_outcome(
        self,
        problem: DispatchProblem,
        load: np.ndarray,
        timestamps: pd.DatetimeIndex,
        interval_min: int,
        rte: float,
        initial_soe_kwh: float,
        weights: ObjectiveWeights | None = None,
    ) -> WindowOutcome:
        """Solve one rolling-horizon window and keep WHY it refused, if it did (WP-SIZE-01).

        ``solve_window`` returns a bare ``None`` on refusal, which is how the MPC used to lose the
        reason (quarantine fault 4). This returns the schedule together with the status the annual
        ``solve`` would have reported for the same refusal: ``INSUFFICIENT_DATA`` for an
        unpriceable window (F-01, including a start state outside the usable SoE window), and
        ``INFEASIBLE`` when HiGHS returns no optimal solution. ``reasons`` carries the refusal text
        and the model-build warnings that explain it (over-commitment, HiGHS termination).
        """
        try:
            schedule, _binding, warnings = self._solve_milp(
                problem,
                load,
                timestamps,
                interval_min,
                rte,
                initial_soe_kwh=initial_soe_kwh,
                use_binary=True,
                weights=weights,
            )
        except _UnpriceableDispatch as exc:
            # F-01: an unpriceable window yields no schedule rather than one priced at £0 charge
            # energy, and the reason travels with it.
            return WindowOutcome(None, SolverStatus.INSUFFICIENT_DATA, (str(exc),))
        if schedule is None:
            return WindowOutcome(None, SolverStatus.INFEASIBLE, tuple(warnings))
        return WindowOutcome(schedule, None, ())

    def solve_with_export(
        self,
        problem: DispatchProblem,
        market_forecast: MarketForecast,
        *,
        export_limit_kw: float | None = None,
        export_limit_basis: str | None = None,
    ) -> tuple[HalfHourlyDispatchSchedule | None, list[str]]:
        """Solve the fixed-spec dispatch with the consent-gated wholesale EXPORT overlay (A2/A3).

        Reuses ``solve``'s preamble (metered series load, RTE resolution), aligns the wholesale
        forecast to the loaded timestamps via the pure B2 helper (full-coverage-or-skip; never
        interpolated; £/MWh→£/kWh), then solves the SAME model plus an ``export`` variable priced
        at the aligned wholesale £/kWh. Returns ``(schedule, warnings)``; the schedule is ``None``
        (with the reason in ``warnings``) when site data is insufficient OR the price series does
        not fully cover the metered horizon. Deliberately does NOT touch ``_last_schedule`` /
        ``_last_customer_bill`` — the overlay is a measurement (the WHOLESALE_P415 delta), never the
        reported dispatch. The engine (`MilpEngine._export_upside`) computes the upside from this
        schedule's ``priced_floor_terms_gbp`` + ``export_revenue_gbp`` against the floor solve.

        ``export_limit_kw`` / ``export_limit_basis``: an EXPLICIT, LABELLED export limit supplied by
        the caller for a feasibility study, used INSTEAD of ``site.connection.mec_kw``
        (`MilpEngine.ExportFeasibilityOverride`). ``None`` (every ordinary run) reads the site's own
        declared MEC exactly as before. The basis label is stated on the returned warnings, so a
        study limit can never be mistaken for a declared one, and NOTHING is written to the site.
        """
        if not isinstance(problem, DispatchProblem):
            raise InvalidRequestError(
                "MilpDispatchSolver.solve_with_export expects a DispatchProblem, got "
                f"{type(problem).__name__}"
            )
        site = problem.site_model
        assumptions = problem.assumptions
        max_import = site.load_data.max_import_kw
        if max_import is None or max_import <= 0 or not site.load_data.half_hourly_file_ref:
            return None, [
                "export overlay: insufficient site data (no connection bound or HH series)."
            ]
        try:
            load, net_load, timestamps, interval_min, _ = self._load_series_signed(
                site.load_data.half_hourly_file_ref
            )
        except HalfHourlyDataError as exc:
            return None, [f"export overlay: half-hourly series unreadable — {exc}"]
        if load.size == 0:
            return None, ["export overlay: the half-hourly series has no usable readings."]
        rte = numeric(maybe(assumptions, "round_trip_efficiency", "ac_round_trip"))
        if rte is None or not 0 < rte <= 1:
            return None, ["export overlay: round_trip_efficiency.ac_round_trip missing or invalid."]
        export_price, reason = align_export_prices(market_forecast, timestamps)
        if export_price is None:
            return None, [f"export overlay not run: {reason}"]
        try:
            schedule, _binding, warnings = self._solve_milp(
                problem,
                load,
                timestamps,
                interval_min,
                rte,
                net_load_kw=net_load,
                export_price_gbp_per_kwh=export_price,
                export_limit_kw=export_limit_kw,
                export_limit_basis=export_limit_basis,
            )
        except _UnpriceableDispatch as exc:
            # F-01: refuse rather than price charge energy at £0. Every _solve_milp entry point
            # must handle this — an unhandled refusal surfaces as a 500 instead of a data ask.
            return None, [f"export overlay not run: {exc}"]
        return schedule, warnings

    # --- half-hourly series load (never invents) ---------------------------

    def _load_series(self, file_ref: str) -> tuple[np.ndarray, pd.DatetimeIndex, int, list[str]]:
        """Load the metered HH net import (kW) + timestamps, CLIPPED at 0 (never fill).

        The stable 4-tuple every caller outside this module reads (``two_stage``, ``mpc_solver``).
        The clipped series is the *reporting and billing* basis; ``_load_series_signed`` below is
        the physical one the MILP builds on (D-174). A caller that takes only this tuple gets the
        conservative treatment: no PV surplus headroom, exactly as before.
        """
        load, _net, ts, interval_min, warnings = self._load_series_signed(file_ref)
        return load, ts, interval_min, warnings

    def _load_series_signed(
        self, file_ref: str
    ) -> tuple[np.ndarray, np.ndarray, pd.DatetimeIndex, int, list[str]]:
        """Load the metered HH series, signed and clipped.

        Returns ``(clipped_kw, signed_net_kw, ts, interval_min, warnings)``.

        D-174 / JSON-F-49. The old behaviour clipped a negative net import — the signature of solar
        export — to 0 with only a warning, corrupting the modelled profile UPWARD in exactly the
        midday hours a battery would charge from surplus: the charge headroom against the connection
        was understated by the whole surplus, and the discharge bound claimed there was site load to
        serve when the site was actually exporting.

        Both are returned so each consumer takes the right one, and nothing is invented:

        - ``signed_net_kw`` — the true signed net position at the boundary (import positive, surplus
          negative). Drives the CHARGE headroom (``min(MIC,ASC) − net``: surplus is real headroom)
          and, via ``max(net, 0)``, the discharge bound and deliverable envelope.
        - ``clipped_kw`` = ``max(signed_net_kw, 0)`` — the reporting/billing basis handed to the
          schedule (``site_load_kw``): the downstream bill arithmetic is defined on non-negative
          metered import, and a signed series there would silently redefine the customer bill in a
          module this one does not own. Conservative in the priced direction — charging from surplus
          is still billed and priced as retail import, so the optimiser UNDER-uses free surplus
          rather than over-claiming it. The export/retail differential on surplus charging (E-03)
          stays deferred to the export overlay, which prices past-the-meter flow explicitly.

        The **PV / on-site generation channel** is the loader's existing ``export_kw`` /
        ``export_kwh`` column (whose alias list already includes ``generation_kw``): present ⇒
        ``net = import − generation``. Absent ⇒ ``net`` is the raw signed import, so every input
        without a generation column and without negative readings is byte-identical to before.
        """
        df = load_half_hourly_csv(file_ref)
        interval_min = int(df.attrs.get("expected_interval_minutes", _DEFAULT_INTERVAL_MIN))
        if "import_kw" in df.columns:
            raw = df["import_kw"]
        else:
            raw = df["import_kwh"] * (60.0 / interval_min)
        # On-site generation (PV) channel, when the file carries one. Never invented: a file with
        # no generation column yields all-zeros and the signed net position is the raw import.
        if "export_kw" in df.columns:
            gen = df["export_kw"]
        elif "export_kwh" in df.columns:
            gen = df["export_kwh"] * (60.0 / interval_min)
        else:
            gen = None
        ts = pd.DatetimeIndex(df["timestamp"])
        mask = raw.notna().to_numpy()
        # WP-UW-07: the metered reactive quantity, carried row for row with the load below.
        reactive_all, reactive_note = _reactive_kvar_from_frame(df, interval_min)
        reactive = reactive_all[mask] if reactive_all is not None else None
        net = raw.to_numpy(dtype=float)[mask]
        gen_arr = (
            np.nan_to_num(gen.to_numpy(dtype=float)[mask], nan=0.0)
            if gen is not None
            else np.zeros_like(net)
        )
        ts = ts[mask]
        warnings: list[str] = []
        if len(ts) > 1 and not ts.is_monotonic_increasing:
            # A stable time order is a precondition for the SoE recurrence; file order is
            # not trusted. Reordering changes nothing physical — the rows keep their values.
            order = np.argsort(ts.asi8, kind="stable")
            ts = ts[order]
            net = net[order]
            gen_arr = gen_arr[order]
            if reactive is not None:
                reactive = reactive[order]
            warnings.append(
                "half-hourly rows were not in time order; sorted by timestamp before dispatch."
            )
        dropped = int((~mask).sum())
        if dropped:
            warnings.append(
                f"{dropped} half-hour(s) with null load excluded from the dispatch (never "
                "back-filled); the horizon is scaled to a year over the present intervals only."
            )
        generating = int((gen_arr > 0).sum())
        if generating:
            warnings.append(
                f"on-site generation present in {generating} half-hour(s) (export_kw / "
                "generation_kw column): the dispatch nets it against import to get the SIGNED net "
                "position, so surplus counts as real charge headroom against the connection "
                "(D-174). Charging from surplus is still PRICED and billed at the retail import "
                "rate — conservative: the export/retail differential is not credited here."
            )
            net = net - gen_arr
        if reactive_note:
            warnings.append(reactive_note)
        if reactive is not None:
            self._reactive_series[file_ref] = pd.Series(reactive, index=ts)
        else:
            self._reactive_series.pop(file_ref, None)
        load = np.clip(net, 0.0, None)
        negatives = int((net < 0).sum())
        if negatives:
            warnings.append(
                f"{negatives} negative net import reading(s) (surplus / on-site generation): the "
                "signed net position is carried into the dispatch physics, and clipped to 0 only "
                "for the behind-the-meter discharge bound and the reported billing basis (never "
                "clipped away from the charge headroom, D-174); verify the source's sign "
                "convention."
            )
        return load, net, ts, interval_min, warnings

    def _reactive_model_inputs(
        self, site: SiteModel, timestamps: pd.DatetimeIndex
    ) -> tuple[ReactiveInputs | None, str | None]:
        """The site's metered reactive quantity on THIS solve's half-hours, and its rate.

        ``(None, None)`` when the file meters no reactive channel: no variable, row or term is then
        built and the model is byte-identical. ``(None, note)`` when the channel exists but does not
        cover every solved half-hour, or reads negative: reactive is a whole-horizon quantity, so a
        partial series is reported and not modelled (never filled).
        """
        ref = site.load_data.half_hourly_file_ref
        series = self._reactive_series.get(ref) if ref else None
        if series is None:
            return None, None
        idx = pd.DatetimeIndex(timestamps)
        if len(idx) == len(series) and bool((series.index == idx).all()):
            values = series.to_numpy(dtype=float)
        elif series.index.has_duplicates:
            return None, (
                "the reactive channel's timestamps repeat and do not match this solve's half-hours "
                "one for one; reactive power is not modelled on this solve."
            )
        else:
            values = series.reindex(idx).to_numpy(dtype=float)
        missing = int(np.isnan(values).sum())
        if missing:
            return None, (
                f"the reactive channel is missing for {missing} of {len(values)} solved "
                "half-hours; reactive power is NOT modelled on this solve (a whole-horizon "
                "quantity is never partly filled)."
            )
        if bool((values < 0.0).any()):
            return None, (
                f"the reactive channel reads negative in {int((values < 0.0).sum())} half-hour(s); "
                "reactive import and export are both magnitudes, so the sign convention of the "
                "file is unclear and reactive power is NOT modelled. Check the source."
            )
        rate: float | None = None
        source: str | None = None
        nt = site.tariff_stack.network_tariff
        if isinstance(nt, CdcmTariff) and nt.reactive_p_per_kvarh is not None:
            rate = float(nt.reactive_p_per_kvarh) / 100.0
            source = f"{nt.dno} {nt.voltage} {nt.charging_year}, {nt.source}"
        return ReactiveInputs(site_kvar=values, rate_gbp_per_kvarh=rate, rate_source=source), None

    # --- the MILP -----------------------------------------------------------

    def _solve_milp(
        self,
        problem: DispatchProblem,
        load: np.ndarray,
        timestamps: pd.DatetimeIndex,
        interval_min: int,
        rte: float,
        *,
        initial_soe_kwh: float | None = None,
        use_binary: bool = False,
        weights: ObjectiveWeights | None = None,
        interval_weights: np.ndarray | None = None,
        export_price_gbp_per_kwh: np.ndarray | None = None,
        export_limit_kw: float | None = None,
        export_limit_basis: str | None = None,
        net_load_kw: np.ndarray | None = None,
        capacity_price_gbp_per_kw_year: float | None = None,
    ) -> tuple[HalfHourlyDispatchSchedule | None, list[str], list[str]]:
        """Build + solve the linopy/HiGHS model; return (schedule, binding, warnings).

        ``initial_soe_kwh``: when set, breaks the cyclic SoE wrap and instead fixes
        SoE at t=0 to this value — used by the MPC rolling-horizon loop.  When
        ``None`` (default) the annual MILP's cyclic constraint is preserved exactly.

        ``use_binary``: when ``True``, adds a binary ``is_charging`` variable that
        enforces hard mutual exclusivity of charge and discharge (real MILP).  When
        ``False`` (default) the LP approximation is used with IPM + crossover, which
        is faster for the full-year annual solve.

        ``weights``: objective aging terms.  ``None`` → zero weights → the existing
        ``_EPS`` tie-break for the annual LP is preserved.

        ``interval_weights``: per-interval representation weights for the sizing-search
        typical-period proxy (D32) — interval ``t`` stands for ``interval_weights[t]``
        real intervals in the objective, the annualisation and the warranty throughput
        cap. ``None`` (every production dispatch) is all-ones and byte-identical to the
        unweighted model. Weighted results are search guidance only, never reported.

        ``export_price_gbp_per_kwh``: the consent-gated wholesale EXPORT overlay (issue 018 /
        D43). ``None`` (every priced-floor solve) creates NO export variable — the model matrix
        is byte-identical to the pre-018 floor (structural byte-identity, not tolerance). When a
        per-instant £/kWh array is supplied it adds an ``export`` variable bounded by
        ``min(power, MEC)`` (the STRUCTURAL export_limit), drained from SoE like discharge, sharing
        the inverter with discharge, counted in the warranty throughput and the reservation
        aggregate, and paid ``+Σ export_price·export·Δt`` in the objective. Export is GATED UPSIDE:
        the caller reports it only as the WHOLESALE_P415 delta, never in the priced floor.

        ``export_limit_kw`` / ``export_limit_basis``: an explicit, labelled export limit used in
        place of ``site.connection.mec_kw`` for a feasibility study (see ``solve_with_export``).
        Only read when an export price series is supplied; ``None`` restores the site's own MEC. The
        resolved cap and its basis are stated on the warnings either way — including the case where
        the cap resolves to **0 kW**, which used to be silent and made the overlay an unannounced
        no-op on every site whose ``mec_kw`` is unrecorded.

        ``capacity_price_gbp_per_kw_year``: the MICASC-3 AGREED-CAPACITY OVERLAY price (£/kVA/yr).
        ``None`` (every priced-floor solve) leaves the residual-peak epigraph at its historic
        ``_EPS_PEAK`` tie-break coefficient and the model is byte-identical. A supplied price makes
        the residual peak a real cost in the objective, so the dispatch will trade energy value for
        peak reduction — which is CONDITIONAL money, so this must never be set on a floor solve.
        Only honoured on the zero-weight (LP) branch, which is the only branch that builds the
        epigraph variable; a non-zero ``weights`` with a price set is a programming error and
        raises rather than silently pricing nothing.

        ``net_load_kw``: the SIGNED net position at the boundary (D-174) — negative where on-site
        generation exceeds site load. Drives the charge headroom only; ``load`` stays the clipped
        reporting/discharge basis. ``None`` (the MPC window path and the sizing-search proxy, which
        both receive the already-clipped series) falls back to ``load``, so surplus contributes no
        extra headroom there — byte-identical to the pre-D-174 model and conservative in the
        priced direction.
        """
        import linopy
        import xarray as xr

        site = problem.site_model
        assumptions = problem.assumptions
        warnings: list[str] = []

        n = int(load.size)
        dt = interval_min / 60.0
        power = float(problem.power_kw)
        energy = float(problem.energy_kwh)
        duration = float(problem.duration_hours)
        weighted_intervals = float(interval_weights.sum()) if interval_weights is not None else n
        annual_factor = _HOURS_PER_YEAR / (weighted_intervals * dt)
        # D-174: the SIGNED net position drives charge headroom; ``load`` stays the clipped basis.
        net_load = (
            np.asarray(net_load_kw, dtype=float).reshape(-1)
            if net_load_kw is not None
            else np.asarray(load, dtype=float).reshape(-1)
        )
        if net_load.size != n:
            raise InvalidRequestError(
                f"net_load_kw has {net_load.size} intervals but the load series has {n}."
            )

        # B-01: split the AC round trip symmetrically. η_ch = η_dis = √RTE, so η_ch·η_dis = RTE and
        # the closed-cycle financial identity (grid charge = discharge ÷ RTE) is untouched, while
        # the
        # SoE trajectory finally reflects that delivering 1 kWh AC drains 1/η_dis kWh of stored
        # energy. Directional measured values would replace this pair the moment a release carries
        # them; √RTE is the only split derivable from a single AC round-trip number without
        # inventing an asymmetry.
        eta = math.sqrt(rte)

        # D-169 / JSON-F-46: the USABLE SoC window, resolved before anything reads an energy
        # capacity. Sweeping the full 0-100% of nameplate overstates deliverable energy per cycle by
        # the BMS reserve and produces schedules that breach the warranty DoD terms the throughput
        # cap below is honouring — and it disagreed with the simulator, which uses a 5% floor. No
        # window is invented: an absent release key leaves the old bounds with a loud data ask (see
        # ``_soc_window_fractions``). ``usable_energy`` is stored-kWh capacity inside the window;
        # ``deliverable_energy`` is what that delivers at the AC terminals after discharge losses.
        soc_min_frac, soc_max_frac, soc_note = _soc_window_fractions(assumptions)
        if soc_note:
            warnings.append(soc_note)
        soe_floor = soc_min_frac * energy
        soe_ceiling = soc_max_frac * energy
        usable_energy = soe_ceiling - soe_floor
        deliverable_energy = usable_energy * eta
        if soc_min_frac > 0.0 or soc_max_frac < 1.0:
            warnings.append(
                f"usable energy {round(usable_energy, 3)} kWh of {round(energy, 3)} kWh nameplate "
                f"(SoC window {soc_min_frac:g}-{soc_max_frac:g}), delivering "
                f"{round(deliverable_energy, 3)} kWh at the AC terminals after discharge losses "
                f"(eta_dis {round(eta, 4)}). The warranty throughput cap stays on NAMEPLATE energy "
                "because the release does not declare the warranty's own metering basis (C-31) — "
                "declare it before treating the cycle count as contractual."
            )

        # D-169 fallout, caught here rather than left to HiGHS. A rolling-horizon caller supplies
        # its own start state, and `mpc_solver` starts the loop at SoE 0 ("fully discharged —
        # conservative"), which was true while the window was 0-100% and is OUTSIDE it now. The
        # `soe_initial` row below then fixes soe[0] at a value the [soe_floor, soe_ceiling] bounds
        # forbid unless the first half-hour can charge the whole reserve: feasible only while
        # ``power·η_ch·Δt ≥ soc_min·energy``. Measured on gb_v1_7 (window 0.15-0.95) at 100 kW:
        # 1 h and 2 h solve, 4 h and 8 h come back INFEASIBLE — i.e. exactly the long-duration
        # specs go dark, and a bare "infeasible" sends the reader hunting for a load fault.
        #
        # So say what is actually wrong, and REFUSE rather than repair: raising soe[0] to the floor
        # would gift the pack ``soc_min·energy`` kWh it never bought, which is a value-creating
        # invention on the pricing path. Every `_solve_milp` entry point already turns this into an
        # honest degrade (INSUFFICIENT_DATA on `solve`, "no window" on `solve_window`). The fix is
        # the CALLER's start-state convention — begin the loop at the usable floor, not at zero —
        # and that lives in `mpc_solver`, not here.
        if initial_soe_kwh is not None and not (
            soe_floor - 1e-9 <= float(initial_soe_kwh) <= soe_ceiling + 1e-9
        ):
            raise _UnpriceableDispatch(
                f"Insufficient data: the rolling-horizon start state "
                f"initial_soe_kwh={float(initial_soe_kwh):g} kWh lies outside the usable state-of-"
                f"energy window [{soe_floor:g}, {soe_ceiling:g}] kWh set by "
                f"{_SOC_MIN_KEY}/{_SOC_MAX_KEY} ({soc_min_frac:g}-{soc_max_frac:g} of "
                f"{round(energy, 3)} kWh nameplate). A pack cannot start outside its own operating "
                "window, and the start state is NOT silently moved onto the floor because that "
                "would credit energy the pack never bought (D-169). Start the horizon at or above "
                f"{soe_floor:g} kWh."
            )

        conn_candidates = [
            v for v in (site.connection.mic_kw, site.connection.asc_kw) if v is not None and v > 0
        ]
        conn_limit = min(conn_candidates) if conn_candidates else 0.0
        # B-17: min(MIC, ASC) is a kVA limit; the model's flows are kW. Absent a metered kVArh
        # series the solver will not invent a power factor — it warns and, if a release ever
        # supplies a sourced factor, de-rates the CHARGE bound (the one place an assumed PF changes
        # a physical decision) while leaving the reported connection limit as declared.
        #
        # WP-UW-07 (D106): when the site's file meters both reactive channels, the kVA limit
        # is modelled as a P-Q polygon below and the scalar factor is not applied (never both).
        reactive, reactive_note = self._reactive_model_inputs(site, timestamps)
        if reactive_note:
            warnings.append(reactive_note)
        if reactive is None:
            power_factor, pf_note = _charge_power_factor(assumptions)
            warnings.append(pf_note)
        else:
            power_factor = 1.0
            released_pf = numeric(maybe(assumptions, "mic_asc_headroom", "assumed_power_factor"))
            if released_pf is not None:
                warnings.append(
                    f"{_POWER_FACTOR_KEY} = {released_pf:g} is NOT applied: the site's metered "
                    "reactive channels are modelled, so the kVA bound is the metered one and an "
                    "assumed factor is never applied on top of it (WP-UW-07)."
                )
        charge_conn_limit = conn_limit * power_factor

        # W9: the charging calendar is versioned content — a release may override the DUoS band
        # windows (duos.band_windows_weekday); absent, the documented code fallback applies with
        # byte-identical behaviour (no note); an applied override or malformed key warns loudly.
        #
        # LIT-F-05: pass the site's own licence area so the release's per-area windows
        # (`duos.band_windows_by_dno`, all 14 areas sourced in gb_v1_8) actually reach the model.
        # Without the second argument the resolver silently returns the generic national window,
        # which is the defect LIT-F-05 files: one window for 14 areas, on a stream whose entire £ is
        # set by WHEN the band falls. The real variation is material — SEPD and both SPEN areas are
        # 16:30–19:30, London has two red windows, and SEVEN areas charge amber at weekends where
        # the generic calendar says green. Measured on the three SEPD campaign sites, correcting the
        # window alone is neutral-to-negative (the battery just discharges 30 min later) while the
        # weekend amber it reveals is worth +3.5% of the DUoS line and, at Harwell, +£836/yr of
        # priced floor because it unlocks weekend cycling. `dno_region` is free text with no enum,
        # so an unrecognised value falls back LOUDLY, never onto a neighbouring area.
        windows, windows_note = duos_windows_from_assumptions(
            assumptions, site.connection.dno_region
        )
        if windows_note:
            warnings.append(windows_note)
        # WP-UW-07 (acceptance 2): every band priced as itself. A half-hour whose band the stack
        # does not quote or time carries the no-spread envelope (`energy_price_envelope`), never
        # the mean of the quoted bands; `energy_objective_coefficients` splits the energy term into
        # a sell price on discharge and a buy price on charge only when such half-hours exist.
        envelope, energy_warn = energy_price_envelope(
            site.tariff_stack.import_rates,
            timestamps,
            windows,
            site.tariff_stack.import_rate_windows,
        )
        if energy_warn:
            warnings.append(energy_warn)
        energy_price = None if envelope is None else envelope.bill
        # D45: take the ABSENCE REPORT, never the discarding wrapper. The array stays dense (an
        # absent band contributes zeros, so `price` below keeps its full length), but an absent band
        # is now reported instead of being priced at £0 indistinguishably from a measured rate.
        # `duos_absence_warning` states which bands are missing and which way the error runs.
        # WP-UW-07: a published `network_tariff` row (CDCM bands or EDCM super red) prices the
        # network unit rate when the stack carries one; otherwise the flat duos_* lines, unchanged.
        duos, duos_absent = network_rate_series_with_absences(
            site.tariff_stack, timestamps, windows
        )
        duos_note = duos_absence_warning(duos_absent)
        if duos_note:
            warnings.append(duos_note)
        # The other two null-as-zero tariff lines (D45). Raised here, not in `solve`, because this
        # is the warning channel the reported result inherits — see `fixed_line_absence_warnings`.
        warnings.extend(fixed_line_absence_warnings(site))
        red_mask = red_band_mask(timestamps, windows)
        if envelope is None or energy_price is None:
            # F-01 (hostile review 2026-08-10, CRITICAL). The old behaviour substituted
            # ``np.zeros(n)`` here, which priced CHARGE ENERGY AT £0: the objective then paid
            # nothing for the round-trip losses, so the solver cycled daily to harvest DUoS
            # avoidance it had never paid to obtain, and booked the whole of it as FIRM
            # priced-floor revenue. On the worked example that fabricated ~£16.1k of a reported
            # ~£17.0k — 95% of the stream, enough to flip the sign of the business case.
            #
            # A flat tariff no longer reaches this branch (tariff_calendar returns a constant
            # array for it). Reaching here means the site's energy price is genuinely UNKNOWN,
            # and an unknown price is not a zero price. Refuse — per validation Gate 1, the
            # correct response to a MILP that cannot be priced is refusal, not a degraded number.
            raise _UnpriceableDispatch(
                "Insufficient data: the site's half-hourly energy price could not be determined "
                f"({energy_warn or 'no usable import tariff'}). Charging energy would have to be "
                "valued at £0, which fabricates the entire arbitrage/DUoS margin. Supply a tariff "
                "whose bands map to a known time-window scheme (red/amber/green, day/night, "
                "peak/off-peak), or a single flat rate."
            )
        # F-03 (2026-08-10 review). The DECISION price is the full delivered cost of a kWh through
        # the meter: energy band + DUoS + the site's flat £/kWh levy stack. Charging used to be
        # priced at energy+DUoS only, so the optimiser saw charge energy 2–5p/kWh cheaper than it
        # really is and booked marginal cycles whose margin was smaller than the levy leakage they
        # incurred — a leakage finance then charged (``levies_on_charge``) against revenue the
        # optimiser had assumed was free of it.
        #
        # NO DOUBLE COUNT, and this is the reconciliation the review asked for. The levy enters
        # the objective ONLY; it is deliberately absent from the reported value streams, which are
        # built from the separate ``energy_price``/``duos`` arrays on the schedule (see
        # ``HalfHourlyDispatchSchedule.arbitrage_value_gbp`` / ``duos_avoidance_value_gbp``). So
        # the levy £ is still charged exactly ONCE — in finance's ``levies_on_charge`` line, on the
        # same net-import convention (levy × max(0, charge − discharge)) that the objective's
        # ``Σ levy·(discharge − charge)`` term is the exact negative of. Deleting the finance line
        # would have deleted the cost entirely, because reported revenue never contained it.
        #
        # TIME-WINDOWED LEVIES. ``levy_rate`` is the FLAT component — every levy that bites on
        # boundary import across the whole year (CCL, RO, CfD, BSUoS, AAHEDC, Nuclear RAB). A levy
        # charged only inside a window (in GB the Capacity Market supplier obligation: "Obligation
        # Costs … are charged on consumption during the winter peak period; from 16:00 and 19:00
        # on working days between November & February") cannot go in that scalar — applied flat it
        # would charge ~30 p/kWh on every charged kWh at 03:00 in July and wreck the dispatch. It
        # enters here as a per-half-hour ARRAY that is zero outside its window, so the optimiser
        # values in- window discharge at the rate the site actually avoids and out-of-window
        # charging at the rate it actually pays. The net-import convention is UNCHANGED, only the
        # rate is now time- varying: the objective term is still Σ levy_t·(discharge_t −
        # charge_t)·Δt, and finance's two levy lines are still its exact negatives —
        # ``levies_on_charge`` for the flat scalar and ``windowed_levies_avoided`` for this array.
        levy_rate = levy_rate_gbp_per_kwh(site)
        windowed_levy, windowed_levy_notes = windowed_levy_series_gbp_per_kwh(site, timestamps)
        warnings.extend(windowed_levy_notes)
        # DFS (D57 item 4, 2026-08-18): THE OBJECTIVE TERM THAT DID NOT EXIST.
        # `grep MarketStream.DFS src/gridq/optimisation/` returned nothing before this: no objective
        # term, no schedule field, no valuation branch, so the only direct NESO revenue route open
        # to
        # a sub-1 MW asset could not be earned by any dispatch at any price. The service is
        # EVENT-DRIVEN, so it enters exactly as the windowed levy does - a per-half-hour ARRAY that
        # is `P x K / 1000` inside the called settlement periods of the DFS winter season and ZERO
        # everywhere else - and the optimiser then decides for itself whether a called half-hour is
        # worth more as DFS delivery than as anything else it could do with that kW. Same net-import
        # convention as every other price component, which is also the correct settlement
        # convention:
        # DFS volume is measured against a BASELINE, so charging inside a called period is negative
        # delivery.
        #
        # NO POUND IS DOUBLE-BOOKED. The array is disjoint from energy, DUoS and levy, and
        # `schedule.dfs_utilisation_value_gbp` is the only reader of it, on its own stream. That the
        # same half-hour also reduces the site's own bill is two payers (NESO pays utilisation, the
        # supplier bills less), not one pound twice.
        #
        # ZONAL, AND SCALED TO THE OBSERVED PERIODS (owner instruction, 2026-08-19: "scale the DFS
        # then to the evidence, scaling based on battery size and location"). The site's OWN DFS
        # zone sets both the price and the call count, and they move in opposite directions across
        # the twelve zones, so both come from `dfs_zone_for_site` and neither is a national average.
        # The count is the zone's OBSERVED period count, not an annualisation of it.
        dfs_zone, _dfs_zone_resolved, dfs_zone_notes = dfs_zone_for_site(site)
        warnings.extend(dfs_zone_notes)
        dfs_mask, dfs_notes = dfs_service_period_mask(
            timestamps, zone=dfs_zone, assumptions=assumptions
        )
        warnings.extend(dfs_notes)
        dfs_price = np.zeros(n, dtype=float)
        if bool(dfs_mask.any()):
            dfs_rate, _dfs_rate_used, dfs_rate_notes = dfs_utilisation_rate_gbp_per_kwh(
                assumptions, zone=dfs_zone
            )
            warnings.extend(dfs_rate_notes)
            dfs_price[dfs_mask] = dfs_rate
        price = energy_price + duos + levy_rate + windowed_levy + dfs_price
        # The charge-side price differs from `price` only on half-hours the stack does not price
        # (None everywhere else, so every fully-priced stack builds the identical model).
        energy_sell, energy_buy = energy_objective_coefficients(envelope)
        buy_price = None if energy_buy is None else price + (energy_buy - energy_sell)

        # DEG-01 (2026-08-10 register §5). The objective is PROFIT, not revenue: every kWh that
        # leaves the battery consumes cycle life, and until now nothing in the default objective
        # charged for it. Same reconciliation discipline as the levy (F-03 above): c_deg is a
        # DECISION cost only — it enters the objective so the optimiser cannot book a trade whose
        # margin its own wear erases, and it is deliberately absent from the reported value streams,
        # which are built from the separate ``energy_price``/``duos`` arrays on the schedule. So no
        # £ is double-counted. UNLIKE the levy, though, there is no finance line that charges it
        # today (finance books no degradation opex), which is stated in the warning rather than
        # hidden: the filter is real, the £ is not yet in the pack.
        deg_cost, deg_note = degradation_cost_gbp_per_kwh(assumptions)
        warnings.append(deg_note)

        # Parasitic (auxiliary) load of the BESS itself, release-gated (D33 pattern): when the
        # assumption key exists, the continuous auxiliary draw occupies import capacity on top of
        # the site load, shrinking the charge headroom. Absent key (all pre-gb_v1_2 releases) =
        # zero draw = behaviour unchanged. The £ cost of the auxiliary energy is finance's line
        # (same key), not the dispatcher's; discharge never serves the auxiliary draw here
        # (conservative — no self-supplied headroom).
        parasitic_fraction = numeric(
            maybe(assumptions, "battery_opex", "parasitic_load_fraction_of_power")
        )
        aux_kw = 0.0
        if parasitic_fraction is not None and parasitic_fraction > 0:
            aux_kw = parasitic_fraction * power
            warnings.append(
                f"parasitic auxiliary draw {round(aux_kw, 3)} kW "
                f"({parasitic_fraction} x rated power, battery_opex.parasitic_load_fraction_of_"
                "power) reduces the connection charge headroom; auxiliary energy is costed by "
                "finance from the same assumption."
            )

        # D-174: charge headroom is measured against the SIGNED net position — during a PV surplus
        # the wire is carrying energy the other way, so the headroom is genuinely larger than
        # ``conn_limit − load`` and clipping the surplus away understated it in exactly the midday
        # hours a battery would charge. The discharge bound stays on the CLIPPED series: there is no
        # site load to displace while the site is net-exporting (and export past the boundary is the
        # gated-upside overlay's variable, never the priced floor).
        charge_ub = np.minimum(power, np.maximum(0.0, charge_conn_limit - net_load - aux_kw))
        discharge_ub = np.minimum(power, load)

        # Issue 017 / D42: declared load-service obligations. The shortfall can ONLY come from
        # the battery (the wire is capped), so it is a CONSTANT forced draw — it enters the SoE
        # balance, the inverter headroom, the warranty throughput and the reservation aggregate
        # as data, and the objective cannot see it (no import-displacement value is ever
        # credited for serving load the connection could never have delivered; the product's
        # value is the declared counterfactual stream, not arbitrage on hypothetical load).
        # No requirements => all-zero arrays => byte-identical model.
        prov = self._compile_provisioning(
            site,
            load,
            aux_kw,
            conn_limit,
            power,
            deliverable_energy,
            rte,
            timestamps,
            dt,
        )
        prov_unmet, prov_addition, prov_prepos, prov_ids, prov_warn = prov
        warnings.extend(prov_warn)
        prov_active = bool(np.any(prov_unmet > 0.0) or np.any(prov_addition > 0.0))
        if prov_active:
            # The declared addition draws through the wire inside windows (charge headroom
            # shrinks); the forced service shares the inverter with priced discharge.
            charge_ub = np.minimum(
                power, np.maximum(0.0, charge_conn_limit - net_load - aux_kw - prov_addition)
            )
            discharge_ub = np.minimum(np.maximum(power - prov_unmet, 0.0), load)

        # WP-UW-07: screen the kVA connection polygon once the charge bound is final (see
        # `_reactive_connection_screen`); nothing here runs without a metered reactive series.
        reactive_rows: np.ndarray | None = None
        reactive_base_kw = net_load + aux_kw + (prov_addition if prov_active else 0.0)
        reactive_outside = 0
        if reactive is not None and conn_limit > 0.0:
            charge_ub, reactive_rows, reactive_outside = _reactive_connection_screen(
                reactive_base_kw, charge_ub, reactive.site_kvar, conn_limit
            )

        # --- gap segmentation (SoE is never chained across missing intervals) --------
        # A discontinuity in the metered timestamps (missing interval(s), duplicate stamp or an
        # irregular step) is a wall: the state of energy must not carry across it as if no time
        # passed, or the optimiser charges cheaply before a data hole and discharges dearly after
        # it — value manufactured from absent data. Each contiguous segment gets its own cyclic
        # wrap (soe at segment start = soe at segment end), which reduces *exactly* to the
        # original global cyclic constraint when the series has no gaps.
        if n > 1:
            # timedelta64 comparison is unit-aware (the parsed index may be s- or ns-resolution).
            # For a tz-aware index `.values` is the physical (UTC) instants, so a DST clock
            # change is NOT a gap — the series is physically continuous. A naive local-clock
            # series shows a 60-min label step at spring-forward, which conservatively starts
            # one extra segment per year rather than inventing the missing labels.
            deltas = np.diff(timestamps.values)
            seg_starts = np.flatnonzero(deltas != np.timedelta64(int(interval_min), "m")) + 1
        else:
            seg_starts = np.array([], dtype=np.intp)
        starts = np.concatenate(([0], seg_starts)).astype(np.intp)
        ends = np.concatenate((seg_starts - 1, [n - 1])).astype(np.intp)
        if seg_starts.size:
            warnings.append(
                f"{seg_starts.size} timestamp discontinuit"
                f"{'y' if seg_starts.size == 1 else 'ies'} in the half-hourly series; the "
                "dispatch is segmented at each gap — state of energy is never chained across "
                "missing intervals."
            )
        # Predecessor permutation: t-1 within a segment, the segment's own end at its start.
        pred = np.arange(n, dtype=np.intp) - 1
        pred[starts] = ends

        coords = pd.RangeIndex(n, name="t")
        model = linopy.Model()
        charge = model.add_variables(
            lower=0.0, upper=xr.DataArray(charge_ub, coords=[coords]), name="charge"
        )
        discharge = model.add_variables(
            lower=0.0, upper=xr.DataArray(discharge_ub, coords=[coords]), name="discharge"
        )
        # SoE lives inside the usable window resolved above (D-169).
        soe = model.add_variables(lower=soe_floor, upper=soe_ceiling, coords=[coords], name="soe")

        # Wholesale EXPORT overlay (issue 018 / D43). Behind ``export_price is not None`` ONLY —
        # so with no aligned price series NOT ONE variable/constraint below is created and the model
        # matrix is byte-identical to the priced floor. ``export`` is the only past-the-meter flow:
        # bounded per-instant by min(power, MEC) (the STRUCTURAL export_limit), it drains SoE like
        # discharge and shares the inverter with discharge, but is priced only in the gated-upside
        # objective term (never the floor). ``export_flow`` = discharge + export enters the SoE
        # balance, the warranty cap and the reservation aggregate; ``None`` recovers ``discharge``.
        export = None
        export_flow: Any = discharge
        export_wash_binary = False
        if export_price_gbp_per_kwh is not None:
            site_mec = site.connection.mec_kw
            declared_txt = (
                "NOT DECLARED (connection.mec_kw is unrecorded)"
                if site_mec is None
                else f"{float(site_mec):,.3f} kW (connection.mec_kw)"
            )
            if export_limit_kw is None:
                mec = float(site_mec or 0.0)
                limit_basis = f"the site's own declared export capacity — {declared_txt}"
            else:
                # An explicit, labelled study limit. It NEVER reaches the site record: the caller
                # holds it as a scenario input and it is stated here so no reader can mistake it for
                # a declared MEC or for an export consent.
                mec = float(export_limit_kw)
                limit_basis = (
                    f"an EXPLICIT EXPORT-LIMIT OVERRIDE of {mec:,.3f} kW — "
                    f"{export_limit_basis or 'labelled study input, NOT customer consent'}; the "
                    f"site's own export capacity is {declared_txt} and is UNCHANGED"
                )
            export_cap = float(np.clip(min(power, mec), 0.0, None))
            warnings.append(
                f"export overlay export limit {export_cap:,.3f} kW = min(rated power "
                f"{power:,.3f} kW, export limit {mec:,.3f} kW), from {limit_basis}."
                + (
                    ""
                    if export_cap > 0.0
                    else " THE EXPORT CAP IS 0 kW, so the overlay is a NO-OP: the export variable "
                    "is fixed at zero, no kWh can leave the site and the measured wholesale upside "
                    "is £0 for a STRUCTURAL reason, not an economic one. Supply an export limit "
                    "(the DNO's MEC, or an explicit labelled study override) before reading £0 as "
                    "'export does not pay here'."
                )
            )
            export = model.add_variables(
                lower=0.0, upper=export_cap, coords=[coords], name="export"
            )
            export_flow = discharge + export
            # Shared inverter: BTM discharge (bounded ≤ load by discharge_ub) plus past-boundary
            # export cannot together exceed the rated power net of any forced load-service draw.
            inverter_cap = np.maximum(power - prov_unmet, 0.0) if prov_active else np.full(n, power)
            model.add_constraints(
                export_flow <= xr.DataArray(inverter_cap, coords=[coords]), name="export_inverter"
            )
            # Wash prevention (design doc, R1). At an instant where buying from the grid at the
            # retail price and re-exporting at wholesale is PROFITABLE round-trip — wholesale·rte >
            # price — the LP would set charge and export simultaneously (a physically impossible
            # single-inverter wash) to bank the spread. Forbid it there, and there ONLY, with a
            # surgical ``is_charging`` binary (charge XOR export_flow). Everywhere else the wash is
            # loss-making, so the _EPS throughput regulariser already kills any pointless
            # simultaneous flow — no binary needed (keeps the annual MILP an LP wherever it can).
            # ``use_binary`` (the MPC path) already enforces this exclusivity, so skip when set.
            if not use_binary:
                wash_risk = (np.asarray(export_price_gbp_per_kwh, dtype=float) * rte) > (
                    (price if buy_price is None else buy_price) + 1e-9
                )
                if bool(wash_risk.any()):
                    export_wash_binary = True
                    wash_mask = xr.DataArray(wash_risk, coords=[coords])
                    is_charging_wash = model.add_variables(
                        binary=True, coords=[coords], name="export_wash_excl"
                    )
                    model.add_constraints(
                        charge <= power * is_charging_wash,
                        name="export_wash_charge",
                        mask=wash_mask,
                    )
                    model.add_constraints(
                        export_flow <= power * (1 - is_charging_wash),
                        name="export_wash_flow",
                        mask=wash_mask,
                    )

        # WP-UW-07 (D106): the reactive variable, built only with a metered series.
        # ``c_t`` is the kVAr the inverter supplies against the site's own reactive direction,
        # sign-free because it can never exceed the site's own ``m_t`` (over-compensation is never
        # optimal), so one variable covers lagging and leading sites alike.
        reactive_comp: Any = None
        reactive_excess: Any = None
        if reactive is not None:
            m_kvar = reactive.site_kvar
            reactive_comp = model.add_variables(
                lower=0.0,
                upper=xr.DataArray(np.minimum(power, m_kvar), coords=[coords]),
                name="reactive_comp",
            )
            _add_inverter_pq_rows(
                model,
                charge,
                export_flow,
                reactive_comp,
                power,
                prov_unmet if prov_active else np.zeros(n),
                m_kvar > 0.0,
                coords,
            )
            if reactive_rows is not None and bool(reactive_rows.any()):
                _add_connection_kva_rows(
                    model,
                    charge,
                    discharge,
                    reactive_comp,
                    reactive_base_kw,
                    m_kvar,
                    conn_limit,
                    reactive_rows,
                    coords,
                )
            if reactive.rate_gbp_per_kvarh is not None:
                reactive_excess = _add_reactive_excess_epigraph(
                    model, charge, discharge, reactive_comp, load, m_kvar, dt, coords
                )
            warnings.append(
                _reactive_modelled_note(
                    reactive,
                    power=power,
                    limit_kva=conn_limit,
                    n_outside=reactive_outside,
                    n_no_import=int(((load <= 0.0) & (m_kvar > 0.0)).sum()),
                )
            )

        # B-01: soe[t] - soe[pred[t]] - charge[t]*eta_ch*dt
        #        + (discharge[t]+export[t])*dt/eta_dis == 0,
        # with pred the per-segment predecessor (a coordless indexer so the expression keeps coords
        # 0..n-1). The discharge side now DIVIDES by eta_dis — the stored energy a discharge
        # consumes, not the energy it delivers.
        balance_full = (
            soe
            - soe.isel(t=xr.DataArray(pred, dims=["t"]))
            - charge * (eta * dt)
            + export_flow * (dt / eta)
        )
        # Provisioning forced draw enters the balance as a constant RHS (issue 017 / D42). It is a
        # real discharge, so it drains stored energy at 1/eta_dis like every other discharge (B-01).
        prov_rhs = xr.DataArray(-prov_unmet * (dt / eta), coords=[coords]) if prov_active else None
        if initial_soe_kwh is not None:
            # Rolling-horizon: fix SoE at t=0 instead of wrapping the first segment; later
            # segments (a gap inside the window) keep their own conservative cyclic wrap.
            if n > 1:
                model.add_constraints(
                    balance_full.isel(t=slice(1, None))
                    == (prov_rhs.isel(t=slice(1, None)) if prov_rhs is not None else 0.0),
                    name="soe_balance",
                )
            model.add_constraints(
                soe.isel(t=[0])
                - charge.isel(t=[0]) * (eta * dt)
                + export_flow.isel(t=[0]) * (dt / eta)
                == initial_soe_kwh - float(prov_unmet[0]) * (dt / eta),
                name="soe_initial",
            )
        else:
            # Annual solve: per-segment cyclic wrap (global soe_0 = soe_T when gapless).
            model.add_constraints(
                balance_full == (prov_rhs if prov_rhs is not None else 0.0), name="soe_balance"
            )

        if use_binary:
            # Hard mutual exclusivity: charge and (discharge + export) cannot both be nonzero.
            # ``export_flow`` is exactly ``discharge`` when no export variable exists, so this is
            # byte-identical on the MPC path; with export it forbids the buy-and-export wash here
            # too (the overlay's own surgical wash binaries below cover the LP annual path).
            is_charging = model.add_variables(binary=True, coords=[coords], name="is_charging")
            model.add_constraints(charge <= power * is_charging, name="charge_binary_ub")
            model.add_constraints(
                export_flow <= power * (1 - is_charging), name="discharge_binary_ub"
            )

        # Per-interval representation weights fold into every horizon sum (all-ones when None,
        # which multiplies through as exactly the unweighted model).
        w_da = (
            xr.DataArray(np.asarray(interval_weights, dtype=float), coords=[coords])
            if interval_weights is not None
            else 1.0
        )

        throughput_cap_kwh = 0.0
        cap_table = maybe(assumptions, "warranty_cycle_limits", "max_cycles_per_year_by_duration_h")
        if cap_table is not None:
            # Ceiling lookup (D35, resolves F-002/C030): the warranty table DECREASES with
            # duration, so the conservative neighbour is the node ABOVE — the cap never
            # exceeds what any bracketing contract point allows, and no intermediate value is
            # invented (north-star D28's no-interpolation stance kept). On tabulated nodes the
            # value is unchanged. CM de-rating keeps the floor (its conservative direction).
            cap_cycles, cap_warn = ceiling_value_for_duration(cap_table.value, duration)
            if cap_cycles is not None and cap_cycles > 0:
                throughput_cap_kwh = cap_cycles * energy / annual_factor
                cap_rhs = throughput_cap_kwh
                # RULE 2 OF THE PUBLISHED EXCLUSIVITY MATRIX, WIRED (2026-08-19). The response
                # product's own measured throughput was priced into its rate and consumed NO
                # warranty budget, so the total ran at 119-125 pct of the cap on both audited
                # campaign runs. Charged here, exactly as `prov_throughput` is below.
                anc_throughput, anc_notes = _ancillary_response_throughput_kwh(
                    site, assumptions, power_kw=power, annual_factor=annual_factor
                )
                warnings.extend(anc_notes)
                cap_rhs -= anc_throughput
                if prov_active:
                    # The forced service is real throughput: it consumes warranty cycles
                    # before any priced dispatch may (issue 017 / D42 — honest wear).
                    w_np_cap = (
                        np.asarray(interval_weights, dtype=float)
                        if interval_weights is not None
                        else np.ones(n)
                    )
                    prov_throughput = float((prov_unmet * w_np_cap).sum() * dt)
                    cap_rhs -= prov_throughput
                    if cap_rhs < 0.0:
                        warnings.append(
                            f"load-service obligations alone require {round(prov_throughput, 1)} "
                            f"kWh of throughput vs the warranty cap {round(throughput_cap_kwh, 1)} "
                            "kWh — the obligation exceeds the warranty by construction (never "
                            "silently relaxed)."
                        )
                model.add_constraints(
                    (export_flow * w_da * dt).sum() <= cap_rhs, name="throughput_cap"
                )
            if cap_warn:
                warnings.append(cap_warn + ".")

        # W7-2: compile the seam's typed availability commitments into window constraints —
        # additive by construction (no payload ⇒ nothing changes; see the kind registry above).
        # Per-instant deliverable net-injection envelope for contract reservations (W7-1): on
        # the no-export floor a response can serve only the site's own load, net of the BESS's
        # continuous parasitic draw.
        # D57 (Olly, 2026-08-18): the SITE-LOAD CAP IS REMOVED from the reservation envelope.
        #
        # This read ``min(power - aux_kw, load)``: a committed response could only ever be as large
        # as the site's own metered import at that instant, because on the no-export floor a
        # discharge can serve nothing but the site's own load. That cap is a consequence of EXPORT
        # CONSENT BEING ABSENT, and D57 assumes export consent, the export MPAN and G99/G100
        # APPROVED for every one of these sites and battery sizes. With export approved a response
        # is deliverable as net injection past the meter, so there is no longer anything for the
        # site's load to bound: the envelope is the battery's own capability net of parasitic draw.
        #
        # WHY THIS IS THE FIX AND NOT MERELY A LOOSENING. The load cap did not reduce the
        # local-flex figure, it DELETED the stream: `gb_streams.local_flex_award_constraints`
        # had to bound every award by the site's MINIMUM in-window metered import to keep the
        # model feasible, and that minimum is ZERO at five of nine sites (Bishops Cleeve MRF
        # imports zero for 16% of the published 10:00-16:00 April-September window, Knowl Hill
        # for 83%), so those awards were
        # refused outright and priced GBP 0. It also made the constraint itself infeasible-adjacent:
        # at a zero-load in-window instant ``net_injection <= 0 - committed`` FORCES net charging of
        # at least the committed kW, which is what made 138 of 139 candidates INFEASIBLE on a real
        # Bishops Cleeve MRF run. Removing the cap makes the constraint feasible for every candidate
        # whose committed kW fits inside its own capability, which is the bound D57 asks for.
        #
        # THE SIGN, and it is a genuine finding independent of D57. NGED's Port Ham award - the only
        # open, undersubscribed award any of these sites sits in - is a DEMAND TURN UP product: it
        # pays the asset to CHARGE in the summer daytime, not to discharge. Bounding a turn-UP
        # obligation by metered IMPORT VOLUME is wrong-directional twice over: turning demand up
        # consumes import HEADROOM (connection limit less load), which is LARGEST exactly when
        # metered import is smallest, so the old bound was tightest precisely where the real product
        # was easiest to deliver. That is why the zero-import windows read as "nothing to turn down"
        # when the awarded product never asked the site to turn anything down.
        #
        # DIRECTION OF EFFECT: UP, and feasibility improves rather than degrades. The reservation is
        # still real - it is still subtracted from net injection through the award's own windows, so
        # no kW is double-sold inside the dispatch - and an over-committed instant is still warned
        # and still returns INFEASIBLE rather than being silently relaxed.
        deliverable_envelope = np.clip(power - aux_kw, 0.0, None) * np.ones_like(load)
        # Pre-window SoE floors at the WORST-CASE exceedance level (issue 017 problem 2): the
        # battery arrives at every window occurrence holding the worst observed occurrence's
        # energy, not just what this trace's foresight needs. Above-usable floors were warned
        # at compile; the constraint is added anyway (never silently relaxed).
        #
        # B-01 + D-169 rescale: ``_level`` is the energy the obligation must DELIVER at the AC
        # terminals, so the stored energy required is ``soc_min·E + level/eta_dis`` — the reserve
        # below the usable floor is not deliverable, and the discharge losses must be pre-stored.
        # Holding only ``level`` certified deliverability the battery could not meet (an
        # obligation-BREACH direction).
        for _rid, _starts, _level in prov_prepos:
            if _level <= 0.0 or _starts.size == 0:
                continue
            _pre = np.zeros(n, dtype=bool)
            _pre[pred[_starts]] = True
            model.add_constraints(
                soe >= soe_floor + _level / eta,
                name=f"load_service_prepos_{re.sub(r'[^0-9A-Za-z_]', '_', _rid)}",
                mask=xr.DataArray(_pre, coords=[coords]),
            )
        compiled, commit_warnings, committed_kw = self._compile_commitments(
            problem,
            model,
            charge,
            discharge,
            soe,
            timestamps,
            coords,
            power,
            deliverable_energy,
            deliverable_envelope,
            soe_floor,
            eta,
            pre_reserved_kw=prov_unmet if prov_active else None,
            export=export,
        )
        warnings.extend(commit_warnings)
        if compiled and seg_starts.size:
            warnings.append(
                "metered gaps intersect the horizon while contract commitments are compiled — "
                "constraints apply only to metered instants; a committed window inside a data "
                "hole is unverifiable from this dataset (never invented)."
            )

        price_da = xr.DataArray(price, coords=[coords])
        # Wholesale export revenue term (issue 018 / D43): the ONLY objective coefficient that is a
        # real £ other than the retail floor price — but it pays the ``export`` variable, never
        # discharge/charge, so it is a strictly-additive gated-upside term that leaves the floor
        # objective untouched when no export variable exists. ``_EPS`` also nudges export toward no
        # pointless churn (extends the throughput regulariser to charge+discharge+export); the
        # firming peak (_EPS_PEAK, import-side) and ageing (_EPS_AGE, discharge-only) tie-breaks are
        # deliberately export-free (conservative; design doc R4).
        export_rev_term = None
        if export is not None:
            export_price_da = xr.DataArray(
                np.asarray(export_price_gbp_per_kwh, dtype=float), coords=[coords]
            )
            export_rev_term = (export_price_da * w_da * export * dt).sum()
        w = weights or ObjectiveWeights()
        # The base objective, shared by BOTH weight branches: revenue MINUS the marginal cost of the
        # cycle that earned it (DEG-01). `deg_term` charges every kWh that leaves the pack —
        # discharge AND gated-upside export, since export wears cells exactly as discharge does.
        # It is a real £ coefficient, unlike the three epsilon tie-breaks; ObjectiveWeights' zero
        # default therefore no longer means a zero-wear objective (see its docstring). Putting it in
        # the base is what makes it apply on the MPC path too, where `aging_beta` was only ever a
        # dimensionless soft proxy for it.
        revenue_term = _energy_revenue_term(
            price_da, buy_price, coords, w_da, charge, discharge, dt
        )
        deg_term = deg_cost * (export_flow * w_da * dt).sum()
        # Built only on the zero-weight LP branch below; the D67 second pass keys on it.
        peak_import: Any = None
        peak_coefficient = _EPS_PEAK
        if w.aging_alpha == 0.0 and w.aging_beta == 0.0:
            # LP behaviour, three epsilon-scale tie-breaks (never prices; no reported £ reads them):
            #   _EPS      — less throughput (no pointless cycling), incl. export;
            #   _EPS_PEAK — lower residual peak net import (firming, C029/D38): epigraph var
            #               `peak_import ≥ load + charge − discharge` at every t, minimised;
            #   _EPS_AGE  — discharge EARLIER (calendar-ageing lean), discharge-only and strictly
            #               subordinate to the peak term via a horizon-normalised interval index.
            # Deliberately the CLIPPED series, not the signed net position (D-174): this epigraph
            # is the exact quantity ``customer_bill.peak_import_after_kw`` reports, and it is the
            # conservative one (a surplus half-hour counts its charging as import peak).
            load_da = xr.DataArray(np.asarray(load, dtype=float).reshape(-1), coords=[coords])
            peak_import = model.add_variables(lower=0.0, name="peak_import")
            # Operands ordered so the linopy expression owns the arithmetic (a leading DataArray
            # would send `+ charge` through xarray and mis-broadcast the variable).
            model.add_constraints(
                peak_import >= charge - discharge + load_da, name="peak_import_epi"
            )
            idx_norm = xr.DataArray(np.arange(n, dtype=float) / max(n - 1, 1), coords=[coords])
            # MICASC-3: the AGREED-CAPACITY OVERLAY. ``None`` on every priced-floor solve, which
            # leaves the coefficient at the historic `_EPS_PEAK` tie-break and the model matrix
            # byte-identical. When a price is supplied the SAME epigraph variable stops being a
            # tie-break and becomes a real £ the dispatch trades against — which is the whole
            # defect: the agreed capacity was `capacity_charge × (asc_kw or mic_kw) × 365.25`, a
            # battery-invariant constant no dispatch could reduce.
            #
            # WHY AN OVERLAY AND NOT THE FLOOR OBJECTIVE. The money this buys is CONDITIONAL (it
            # needs the customer to re-negotiate with the DNO) while arbitrage and DUoS avoidance
            # are FIRM. Putting the capacity price in the floor objective would let the optimiser
            # sell firm, bankable revenue to buy conditional, unbankable revenue — the underwritten
            # floor would fall and the compensation would sit somewhere debt cannot see it. The
            # floor solve therefore keeps its own optimum and the overlay is priced as a delta
            # against it, with the sacrificed floor revenue netted off by the caller.
            #
            # UNITS: the revenue terms are £ over the MODELLED HORIZON, not annualised, so the
            # annual £/kW is divided by `annual_factor` to land on the same basis. Getting this
            # wrong on a part-year series would scale the capacity price against the revenue it
            # trades with.
            peak_coefficient = _EPS_PEAK
            if capacity_price_gbp_per_kw_year is not None:
                peak_coefficient += float(capacity_price_gbp_per_kw_year) / annual_factor
            objective = (
                revenue_term
                - deg_term
                - _EPS * ((export_flow + charge) * w_da * dt).sum()
                - peak_coefficient * peak_import
                - _EPS_AGE * (idx_norm * discharge * dt).sum()
            )
        elif capacity_price_gbp_per_kw_year is not None:
            # This branch builds no `peak_import` epigraph, so an overlay price here would be
            # silently dropped and the caller would read the resulting (unchanged) peak as
            # evidence the battery cannot shave — a £0 that looks measured. Refuse instead.
            raise InvalidRequestError(
                "capacity_price_gbp_per_kw_year is only supported on the zero-weight LP branch: "
                "the residual-peak epigraph the overlay prices is not built when aging weights "
                "are set, so the price would be silently ignored."
            )
        else:
            objective = revenue_term - deg_term
            if w.aging_alpha != 0.0:
                # Soft penalty on mean SoC (normalised) — biases toward lower stored energy.
                objective = objective - w.aging_alpha * (soe * w_da / energy).sum()
            if w.aging_beta != 0.0:
                # Soft throughput penalty — supplements the hard warranty cap.
                objective = objective - w.aging_beta * ((export_flow + charge) * w_da * dt).sum()
        if export_rev_term is not None:
            objective = objective + export_rev_term
        if reactive_excess is not None and reactive is not None:
            # A real £ (the published excess reactive rate), on the same horizon basis as the
            # revenue terms. Present only with a metered series AND a stated rate.
            rate = float(reactive.rate_gbp_per_kvarh or 0.0)
            objective = objective - _reactive_charge_term(rate, reactive_excess, w_da)
        model.add_objective(objective, sense="max")

        # HiGHS interior-point + crossover: the annual throughput cap is a single dense constraint
        # over every half-hour, which makes dual simplex pathologically slow on a full year (~15 s);
        # IPM reaches the identical optimum in well under a second, and the crossover cleans it to a
        # basic (vertex) solution so the schedule has no spurious simultaneous charge/discharge.
        # Single-threaded with a fixed seed for bit-reproducibility (the determinism contract).
        # Branch-and-bound (binary MILP) cannot use IPM — the solver selects its own LP relaxation
        # method internally when binary variables are present.
        solve_kwargs: dict = dict(
            solver_name="highs",
            progress=False,
            output_flag=False,
            log_to_console=False,
            threads=1,
            random_seed=self._seed,
            presolve="on",
        )
        # B-09: pin the relative MIP gap on every branch-and-bound solve, and record the achieved
        # gap below. HiGHS's default is 1e-4 relative, which is large enough that two HiGHS versions
        # can each return a different "optimal" incumbent and the reported £ carries no statement of
        # its distance from proven optimal — a determinism hole AND an unstated accuracy claim.
        # Deliberately NOT set on the pure-LP path (`ipm` + crossover): an LP has no MIP gap, the
        # crossover returns a basic optimal vertex, and setting the option there would only be
        # misleading. Do not "fix" that by setting it unconditionally.
        binary_solve = use_binary or export_wash_binary
        if binary_solve:
            solve_kwargs["mip_rel_gap"] = _MIP_REL_GAP
        else:
            solve_kwargs["solver"] = "ipm"
            solve_kwargs["run_crossover"] = "on"

        status = model.solve(**solve_kwargs)
        if status[1] != "optimal":
            warnings.append(f"HiGHS termination '{status[1]}' — no optimal dispatch.")
            return None, [], warnings
        if binary_solve:
            # Read the ACHIEVED gap back, never assume the request was honoured. An unreadable gap
            # is reported as unreadable — the one thing that must not happen is a priced £ carrying
            # a silent accuracy claim.
            achieved, gap_note = _achieved_mip_gap(model)
            if achieved is None:
                warnings.append(gap_note)
            elif achieved > _MIP_REL_GAP + 1e-12:
                # Above tolerance the incumbent is not proven optimal to the £ rounding, so this is
                # INSUFFICIENT_DATA, never a priced number. Every `_solve_milp` entry point already
                # turns `_UnpriceableDispatch` into an honest degrade.
                raise _UnpriceableDispatch(
                    f"Insufficient data: the dispatch MILP stopped at a relative optimality gap of "
                    f"{achieved:.3e}, above the {_MIP_REL_GAP:.0e} tolerance the reported £ "
                    "rounding requires (B-09). The incumbent is not proven optimal, so no priced "
                    "number is returned. Re-run with a longer time limit or a smaller horizon."
                )
            else:
                warnings.append(gap_note)

        # The first-pass solution, copied, so a second pass that is not adopted (or not optimal)
        # leaves the returned schedule bit for bit what it was before D67.
        charge_raw = np.array(charge.solution.values, dtype=float)
        discharge_raw = np.array(discharge.solution.values, dtype=float)
        soe_raw = np.array(soe.solution.values, dtype=float)
        export_raw = np.array(export.solution.values, dtype=float) if export is not None else None
        comp_raw = (
            np.array(reactive_comp.solution.values, dtype=float)
            if reactive_comp is not None
            else None
        )
        # D67: the PUBLISHED peak. Among the £-optimal schedules the first pass returns an
        # arbitrary one (see `_EPS_PEAK`), so on the solve whose schedule becomes the customer bill
        # the peak is minimised outright with the £ objective held at its optimum. LP only: never
        # on a binary (MPC / export-wash) solve, never on the agreed-capacity overlay (where the
        # peak carries a real price), and never in the sizing loop (the flag is unset there).
        if (
            problem.minimise_published_peak
            and not binary_solve
            and peak_import is not None
            and capacity_price_gbp_per_kw_year is None
            and model.objective.value is not None  # always set after an optimal solve
        ):
            peak_1 = float(peak_import.solution)
            # The objective with the peak term removed (`_EPS` and `_EPS_AGE` stay inside it).
            econ_expr = objective + peak_coefficient * peak_import
            econ_star = float(model.objective.value) + peak_coefficient * peak_1
            model.add_constraints(
                econ_expr >= econ_star - _PEAK_PASS_ECON_SLACK_GBP, name="peak_pass_econ_floor"
            )
            model.add_objective(1.0 * peak_import, overwrite=True, sense="min")
            status_2 = model.solve(**solve_kwargs)
            if status_2[1] != "optimal":
                warnings.append(
                    "the peak-minimising second solve did not reach an optimum; the reported peak "
                    "is the first solve's and may not be the minimum"
                )
            elif peak_1 - float(peak_import.solution) > _PEAK_PASS_MIN_GAIN_KW:
                charge_raw = np.array(charge.solution.values, dtype=float)
                discharge_raw = np.array(discharge.solution.values, dtype=float)
                soe_raw = np.array(soe.solution.values, dtype=float)
                if export is not None:
                    export_raw = np.array(export.solution.values, dtype=float)
                if reactive_comp is not None:
                    comp_raw = np.array(reactive_comp.solution.values, dtype=float)

        charge_sol = np.clip(np.round(charge_raw, 6), 0.0, None)
        discharge_sol = np.clip(np.round(discharge_raw, 6), 0.0, None)
        soe_sol = np.clip(np.round(soe_raw, 6), 0.0, None)
        # Export solution (issue 018): ``None`` on every priced-floor solve (no export variable).
        export_sol = np.clip(np.round(export_raw, 6), 0.0, None) if export_raw is not None else None

        # Post-solve commitment accounting: the dormant schedule fields populate ONLY when a
        # commitment was actually compiled and reserved (never asserted from nameplate alone).
        deliverable_mw: float | None = None
        tender_mwh: float | None = None
        tender_charge_mwh: float | None = None
        # D61: what this solve actually RESERVED for each local-flex award, so the valuation bills
        # the reservation rather than re-deriving it. ``None`` when no award compiled.
        flex_reserved: tuple[tuple[str, float], ...] | None = None
        cm_reserved = [
            c
            for c, _, kind in compiled
            if kind == "availability_window" and c.stream == MarketStream.CAPACITY_MARKET
        ]
        if cm_reserved:
            deliverable_mw = round(sum(c.committed_power_kw for c in cm_reserved) / 1000.0, 6)
        flex_avail = [
            (c, m)
            for c, m, kind in compiled
            if kind == "availability_window" and c.stream == MarketStream.LOCAL_FLEX_PICLO
        ]
        if flex_avail:
            flex_reserved = tuple(
                (str(c.commitment_id), float(c.committed_power_kw)) for c, _m in flex_avail
            )
            # Per-instant contribution CAPPED at the committed kW (adoption review W7-5): a
            # 50 kW award on a 200 kW battery earns utilisation on at most 50 kW — the tender
            # pays for the awarded response, never for every co-incident discharge.
            flex_committed = np.zeros_like(discharge_sol)
            for c, m in flex_avail:
                flex_committed[m] += float(c.committed_power_kw)
            flex_union = flex_committed > 0
            # Weighted like every other annualised aggregate: on a typical-period proxy solve
            # (D32) each present interval stands for interval_weights[t] real intervals.
            w_np = (
                np.asarray(interval_weights, dtype=float)
                if interval_weights is not None
                else np.ones_like(discharge_sol)
            )
            capped = np.minimum(discharge_sol, flex_committed)
            tender_mwh = round(
                float((capped * w_np)[flex_union].sum()) * dt * annual_factor / 1000.0, 6
            )
            # D57 item 5b: the CHARGE-side mirror, same windows, same committed-kW cap. The awarded
            # product at these sites is DEMAND TURN UP (paid to charge), so this is the direction
            # its utilisation leg is delivered in. Measured, never assumed: it is the solved
            # charge series, capped at the awarded kW exactly as the discharge leg is.
            capped_charge = np.minimum(charge_sol, flex_committed)
            tender_charge_mwh = round(
                float((capped_charge * w_np)[flex_union].sum()) * dt * annual_factor / 1000.0, 6
            )

        schedule = HalfHourlyDispatchSchedule(
            power_kw=power,
            energy_kwh=energy,
            duration_hours=duration,
            interval_hours=dt,
            annual_factor=annual_factor,
            charge_kw=charge_sol,
            discharge_kw=discharge_sol,
            soe_kwh=soe_sol,
            site_load_kw=load,
            duos_rate_gbp_per_kwh=duos,
            red_band_mask=red_mask,
            energy_price_gbp_per_kwh=energy_price,
            energy_buy_price_gbp_per_kwh=energy_buy,
            # ``None`` when the site carries no windowed levy, so every existing schedule stays
            # byte-identical (the array is all-zeros in that case and would be a silent no-op, but
            # `ref()` and `windowed_levy_avoidance_gbp` key on the None to guarantee it).
            windowed_levy_rate_gbp_per_kwh=(
                windowed_levy if site.tariff_stack.windowed_levies else None
            ),
            interval_weights=interval_weights,
            deliverable_capacity_mw=deliverable_mw,
            # ``None`` when no DFS period fell in this horizon, so a schedule with no DFS
            # is byte-identical to the pre-D57 one (`ref()` keys on the None to guarantee
            # it, exactly like the windowed levy above).
            dfs_price_gbp_per_kwh=(dfs_price if bool(dfs_mask.any()) else None),
            tender_window_discharge_mwh=tender_mwh,
            tender_window_charge_mwh=tender_charge_mwh,
            local_flex_reserved_kw=flex_reserved,
            load_service_kw=prov_unmet if prov_active else None,
            export_kw=export_sol,
            export_price_gbp_per_kwh=(
                np.asarray(export_price_gbp_per_kwh, dtype=float) if export is not None else None
            ),
            site_reactive_kvar=None if reactive is None else reactive.site_kvar,
            reactive_compensation_kvar=(
                None if comp_raw is None else np.clip(np.round(comp_raw, 6), 0.0, None)
            ),
            reactive_rate_gbp_per_kvarh=None if reactive is None else reactive.rate_gbp_per_kvarh,
        )
        binding = schedule.binding_constraints(
            connection_limit_kw=conn_limit, throughput_cap_kwh=throughput_cap_kwh
        )
        for rid in prov_ids:
            # The obligation floor is forced by construction — it always binds.
            binding.append(f"load_service:{rid}")
        # Commitment constraints that actually bind, in deterministic descriptor order. The
        # availability reservation is the AGGREGATE constraint (W7-2): a commitment reports
        # binding when the net injection touches the jointly-reserved envelope inside its
        # own window.
        tol = 1e-6
        net_injection_sol = discharge_sol - charge_sol
        if export_sol is not None:
            net_injection_sol = net_injection_sol + export_sol
        reserve_slack = deliverable_envelope - committed_kw - net_injection_sol
        for c, mask, kind in compiled:
            if kind == "availability_window":
                if bool(np.any(reserve_slack[mask] <= tol)):
                    binding.append(f"availability_reserve:{c.commitment_id}")
            elif c.held_energy_kwh is not None and bool(
                # The hold binds at the STORED level the AC obligation costs (B-01 + D-169), which
                # is the level the constraint was actually built at.
                np.any(soe_sol[mask] <= soe_floor + c.held_energy_kwh / eta + tol)
            ):
                binding.append(f"held_soe:{c.commitment_id}")
        return schedule, binding, warnings

    def _compile_provisioning(
        self,
        site: SiteModel,
        load: np.ndarray,
        aux_kw: float,
        conn_limit: float,
        power: float,
        deliverable_energy: float,
        rte: float,
        timestamps: pd.DatetimeIndex,
        dt: float,
    ) -> tuple[np.ndarray, np.ndarray, list[tuple[str, np.ndarray, float]], list[str], list[str]]:
        """Compile `SiteModel.provisioning_requirements` into forced-draw data (issue 017 / D42).

        Returns ``(unmet_kw, addition_kw, prepos, requirement_ids, warnings)``: the summed
        per-instant shortfall the battery MUST serve, the summed declared addition (eats
        charge headroom in windows), and per-requirement pre-position entries
        ``(id, occurrence_start_indices, worst-case SoE level)``. Everything is derived via
        the issue-017 stage-2 layer — one calendar, one unmet formula, nothing invented. An
        infeasible obligation is compiled anyway and warned (the house rule: a contracted
        obligation is never silently dropped or relaxed).

        ``deliverable_energy`` is the energy the pack can actually put on the AC terminals —
        ``(soc_max − soc_min)·nameplate·eta_dis`` (D-169 + B-01), not nameplate. Comparing a
        pre-position requirement against nameplate certified deliverability the battery could not
        meet; comparing against this does not.

        ``recharge_scan`` below still takes the single AC round-trip number: it lives in
        ``gridq.sizing.provisioning`` (outside this module's ownership), so the split-efficiency
        treatment of the recharge feasibility scan is left as a follow-up there. Its direction is
        optimistic by the same ~sqrt(RTE) factor B-01 corrects here.
        """
        n = len(load)
        unmet_total = np.zeros(n, dtype=float)
        addition_total = np.zeros(n, dtype=float)
        prepos: list[tuple[str, np.ndarray, float]] = []
        rids: list[str] = []
        warnings: list[str] = []
        requirements = site.provisioning_requirements
        if not requirements:
            return unmet_total, addition_total, prepos, rids, warnings

        # Call-time import: the derivation layer lives in gridq.sizing (module-level import
        # would be circular via the engine); semantics are shared with `_commitment_mask` by
        # a parity-pinned test.
        from gridq.sizing.provisioning import (
            daily_profile_series,
            exceedance_levels,
            occurrences,
            recharge_scan,
            unmet_series,
            window_mask,
        )

        for req in requirements:
            cap = req.import_cap_kw if req.import_cap_kw is not None else conn_limit
            if cap is None or cap <= 0:
                warnings.append(
                    f"provisioning requirement '{req.requirement_id}' has no usable import "
                    "cap (neither import_cap_kw nor a positive site connection limit) — NOT "
                    "compiled (data ask; never defaulted)."
                )
                continue
            mask = window_mask(req.windows, timestamps)
            if not mask.any():
                warnings.append(
                    f"provisioning requirement '{req.requirement_id}' has windows that never "
                    "intersect the metered horizon — vacuous over this dataset (no "
                    "obligation compiled; verify the calendar against the metered period)."
                )
                continue
            addition: float | np.ndarray
            if req.additional_load_profile_daily_kw is not None:
                addition = daily_profile_series(req.additional_load_profile_daily_kw, timestamps)
            else:
                addition = req.additional_load_kw or 0.0
            unmet = unmet_series(load, aux_kw, addition, cap, mask)
            addition_total += np.where(mask, np.asarray(addition, dtype=float), 0.0)
            unmet_total += unmet
            rids.append(req.requirement_id)
            peak = float(unmet.max())
            if peak <= 0.0:
                warnings.append(
                    f"provisioning requirement '{req.requirement_id}': no shortfall observed "
                    f"in its windows at cap {cap:g} kW — the obligation demands nothing on "
                    "this horizon (verify the declared addition and the cap)."
                )
                continue
            if peak > power - aux_kw + 1e-9:
                warnings.append(
                    f"provisioning requirement '{req.requirement_id}' needs {round(peak, 3)} kW "
                    f"of forced service, above the battery's usable {round(power - aux_kw, 3)} "
                    "kW — infeasible by construction (never silently relaxed)."
                )
            occs = occurrences(unmet, mask, timestamps, dt)
            _, level = exceedance_levels(occs)
            if level > deliverable_energy + 1e-9:
                warnings.append(
                    f"provisioning requirement '{req.requirement_id}' needs a "
                    f"{round(level, 1)} kWh worst-case pre-position, above the "
                    f"{deliverable_energy:g} kWh this pack can DELIVER at the AC terminals "
                    "(usable SoC window net of discharge losses, D-169/B-01) — infeasible by "
                    "construction (never silently relaxed)."
                )
            starts = np.asarray([o.start for o in occs], dtype=np.intp)
            prepos.append((req.requirement_id, starts, float(level)))
            scan = recharge_scan(load, aux_kw, cap, unmet, mask, timestamps, dt, rte)
            if not scan.feasible:
                warnings.append(
                    f"provisioning requirement '{req.requirement_id}' is recharge-infeasible "
                    f"through this connection at ANY battery size (running balance bottoms at "
                    f"{scan.worst_margin_kwh:.1f} kWh, {scan.worst_at}) — the honest "
                    "recommendation is connection reinforcement (issue 017 / D39)."
                )
        return unmet_total, addition_total, prepos, rids, warnings

    def _compile_commitments(
        self,
        problem: DispatchProblem,
        model: Any,  # linopy.Model (untyped upstream, as elsewhere in this module,
        charge: Any,
        discharge: Any,
        soe: Any,
        timestamps: pd.DatetimeIndex,
        coords: pd.RangeIndex,
        power: float,
        deliverable_energy: float,
        envelope: np.ndarray,
        soe_floor_kwh: float,
        eta_dis: float,
        pre_reserved_kw: np.ndarray | None = None,
        export: Any = None,
    ) -> tuple[list[tuple[AvailabilityCommitment, np.ndarray, str]], list[str], np.ndarray]:
        """The kind→builder table (W7-2): compile seam descriptors into model constraints.

        For each descriptor whose kind is compilable AND that carries a typed
        `AvailabilityCommitment` payload (C026):

        - ``availability_window`` → reserved **net-injection headroom** through the windows,
          enforced as ONE aggregate constraint against the per-instant deliverable
          ``envelope`` = min(power − parasitic, site load) (adoption review W7-1/W7-2):
          ``discharge_t − charge_t ≤ envelope_t − Σ committed_t`` — overlapping commitments
          reserve the SUM of their kW (never double-sold), curtailing a standing charge counts
          toward the response, and an undeliverable/jointly-infeasible window is warned, never
          silently relaxed;
        - ``held_state_of_energy`` → ``soe_t ≥ soe_floor + held_energy_kwh ÷ η_dis`` through the
          windows. **B-01 + D-169**: ``held_energy_kwh`` is contracted energy DELIVERABLE at the AC
          terminals, so the stored energy the constraint must hold is the reserve below the usable
          floor plus the AC requirement grossed up for discharge losses. The pre-B-01 floor
          (``soe_t ≥ held_energy_kwh``) certified ~√RTE more deliverable energy than the battery
          could produce — an obligation-BREACH direction. A commitment whose ``held_energy_kwh`` is
          ``None`` holds nothing (no explicit hold was contracted — never invented). Overlapping
          holds bind at the max floor (per-commitment constraints).

        Returns ``(compiled triples, warnings, committed kW per instant)`` for post-solve
        accounting. An infeasible commitment is compiled anyway and warned — the solve honestly
        returns INFEASIBLE rather than silently relaxing a contract.
        """
        import xarray as xr

        compiled: list[tuple[AvailabilityCommitment, np.ndarray, str]] = []
        warnings: list[str] = []
        # Load-service obligations pre-reserve capability (issue 017 / D42): grid-side
        # commitments see only the residual envelope — reservations SUM, the site's own
        # obligation is never double-sold to a tender.
        committed = (
            pre_reserved_kw.astype(float).copy()
            if pre_reserved_kw is not None
            else np.zeros(len(envelope), dtype=float)
        )
        # name -> the commitment compiled under it: a re-listed identical (kind, commitment)
        # skips silently; a DIFFERENT commitment whose sanitised id collides gets a uniquified
        # constraint name + a warning — a contracted obligation is never silently dropped.
        seen: dict[str, AvailabilityCommitment] = {}
        # LIT-E-01/LIT-E-02 (2026-08-13): every compilable descriptor that arrives WITHOUT a typed
        # payload, in seam order. Collected rather than skipped silently — see the warning below.
        declared_only: list[str] = []
        descriptors: list[DispatchConstraintDescriptor] = list(problem.constraints)
        for d in descriptors:
            if d.kind in _STRUCTURAL_KINDS or d.kind in _DEFERRED_KINDS:
                continue  # enforced above / not a dispatch-time constraint (see registry)
            if d.kind not in _COMPILED_KINDS:
                warnings.append(
                    f"dispatch constraint kind '{d.kind}' (id '{d.constraint_id}') is not "
                    "recognised by the solver's kind registry — NOT compiled; extend the "
                    "registry before relying on it."
                )
                continue
            c = d.parameters
            if c is None:
                # Declared, not contracted — nothing to compile (see registry note). NOT silent:
                # D45's discipline is that an ABSENT input must never read as a satisfied
                # constraint, and a payload-less descriptor is exactly that shape. Recorded here
                # and warned once below; the model itself is untouched (byte-identical).
                declared_only.append(f"{d.kind}:{d.constraint_id}")
                continue
            name = f"{d.kind}_{re.sub(r'[^0-9A-Za-z_]', '_', c.commitment_id)}"
            if name in seen:
                if seen[name] == c:
                    continue  # identical (kind, commitment) already compiled
                warnings.append(
                    f"distinct commitments collide on sanitised constraint name '{name}' "
                    f"('{seen[name].commitment_id}' vs '{c.commitment_id}') — both compiled "
                    "under uniquified names; give commitments distinct ids."
                )
                name = f"{name}__{len(seen)}"
            mask = _commitment_mask(c, timestamps)
            if not mask.any():
                warnings.append(
                    f"availability commitment '{c.commitment_id}' has windows that never "
                    "intersect the metered horizon — vacuous over this dataset (no constraint "
                    "added; verify the contract calendar against the metered period)."
                )
                continue
            seen[name] = c
            mask_da = xr.DataArray(mask, coords=[coords])
            if d.kind == "availability_window":
                # Per-instant deliverable envelope (adoption review W7-1): on the no-export
                # floor a discharge can only serve the site's own load, and the BESS's
                # continuous parasitic draw (F-007) is consumed behind the meter first — so the
                # response capability at instant t is min(power − aux, load_t) − discharge_t +
                # charge_t (curtailing a standing charge counts toward the response). A
                # commitment above the largest in-window envelope is warned: it cannot be
                # delivered as net injection from this dataset's physics.
                max_in_window = float(np.max(envelope[mask])) if mask.any() else 0.0
                if c.committed_power_kw > max_in_window + 1e-9:
                    warnings.append(
                        f"commitment '{c.commitment_id}' commits {c.committed_power_kw} kW, "
                        f"above the largest in-window deliverable response "
                        f"{round(max_in_window, 3)} kW (min of rated-net-of-parasitic power "
                        "and the metered site load on the no-export floor) — infeasible over "
                        "at least part of the window (never silently relaxed)."
                    )
                committed[mask] += float(c.committed_power_kw)
            else:  # held_state_of_energy
                if c.held_energy_kwh is None:
                    continue  # no explicit hold contracted — never invented
                if c.held_energy_kwh > deliverable_energy + 1e-9:
                    warnings.append(
                        f"commitment '{c.commitment_id}' holds {c.held_energy_kwh} kWh, above the "
                        f"{round(deliverable_energy, 3)} kWh this pack can DELIVER at the AC "
                        "terminals (usable SoC window net of discharge losses, D-169/B-01) — "
                        "infeasible by construction (never silently relaxed)."
                    )
                # B-01 + D-169: hold the STORED energy the AC obligation actually costs.
                model.add_constraints(
                    soe >= soe_floor_kwh + c.held_energy_kwh / eta_dis, name=name, mask=mask_da
                )
            compiled.append((c, mask, d.kind))
        # LIT-E-01/LIT-E-02 (2026-08-13) + C031 item 2. The reservation COMPILER below is real and
        # tested, but nothing in `src/` constructs an `AvailabilityCommitment` yet: every
        # `gb_streams` descriptor carries a `parameter_source` string and no payload, so on a
        # production run the loop above compiles NOTHING and the dormancy was invisible.
        #
        # That invisibility is the defect. An availability commitment with no compiled reservation
        # means the same kW and the same kWh back the dispatch AND the CM/response obligation at
        # once, inside the priced floor the DSCR covenant is measured on — so the floor is
        # OVERSTATED by whatever the market layer prices off these descriptors. One warning, listing
        # every affected descriptor in seam order (deterministic), so an auditor reading the result
        # can see that the constraint is absent rather than satisfied. This is the D45 discipline
        # applied to a constraint instead of a price: an absent input never reads as a met one.
        #
        # Behaviour is byte-identical — the model is untouched and no £ moves. The fix that makes
        # the reservation BITE is a producer of typed payloads (W7-4), not a change here.
        if declared_only:
            warnings.append(
                "NO AVAILABILITY RESERVATION COMPILED (LIT-E-01/LIT-E-02, C031 item 2) for "
                f"{len(declared_only)} declared dispatch commitment descriptor(s): "
                f"{', '.join(declared_only)}. Each is DECLARED (it names a parameter_source) but "
                "carries no typed AvailabilityCommitment payload, so NO kW of power and NO kWh of "
                "stored energy is held back for it in this solve. Any availability/capacity "
                "revenue priced off these descriptors is sold against capability the dispatch is "
                "simultaneously free to spend, which OVERSTATES the priced floor and the DSCR that "
                "covenant is measured on. Deliverability is NOT certified by this run. Supply the "
                "commitment payload (committed_power_kw, optional held_energy_kwh, windows, tenor, "
                "provenance) on the descriptor and the reservation compiles and binds."
            )
        # ONE aggregate availability constraint (adoption review W7-2): at every instant the
        # SUM of committed kW across overlapping windows must fit inside the deliverable
        # envelope — two 150 kW awards on one battery reserve 300 kW, never max(150, 150).
        # No kW is ever double-sold; a jointly-over-committed instant is warned and the solve
        # honestly returns INFEASIBLE rather than silently relaxing a contract.
        agg_mask = committed > 0
        if agg_mask.any():
            over = agg_mask & (committed > envelope + 1e-9)
            if over.any():
                warnings.append(
                    f"overlapping commitments jointly reserve up to "
                    f"{round(float(np.max(committed[over])), 3)} kW where the deliverable "
                    f"envelope is at most {round(float(np.min(envelope[over])), 3)} kW "
                    f"({int(over.sum())} instant(s)) — jointly infeasible (never silently "
                    "relaxed)."
                )
            # Net injection reserved against the envelope. Export (issue 018) is a real
            # net-injection flow, so it joins discharge − charge here — but the envelope stays
            # min(power−aux, load) (NOT extended by MEC): a committed response is deliverable
            # behind the meter, so export competes with discharge for the reserved headroom
            # (conservative; design doc R2). ``net_injection`` is exactly ``discharge − charge``
            # when no export variable exists (byte-identical).
            net_injection = (
                (discharge + export - charge) if export is not None else discharge - charge
            )
            model.add_constraints(
                net_injection <= xr.DataArray(envelope - committed, coords=[coords]),
                name="availability_reserve_aggregate",
                mask=xr.DataArray(agg_mask, coords=[coords]),
            )
        return compiled, warnings, committed

    # --- result assembly ----------------------------------------------------

    def _solved(
        self,
        problem: DispatchProblem,
        schedule: HalfHourlyDispatchSchedule,
        binding: list[str],
        warnings: list[str],
        capacity_reduction: AgreedCapacityValue | None = None,
    ) -> DispatchResult:
        """Assemble a solved, contract-valid `DispatchResult` from the schedule + value streams.

        ``capacity_reduction`` (MICASC-3) is passed EXPLICITLY by each solve entry point rather
        than read off ``self``, and that is not a style choice. ``_solved`` has two production
        callers — ``MilpDispatchSolver.solve`` and ``PreloadedDispatchSolver.solve``, which is the
        one the sizing search and the confirm stage actually use — and the first version of this
        change set the value as instance state inside ``solve`` only. The result was a stream that
        priced correctly in every direct-``solve`` test and reported £0 on all 181 points of a real
        ``gridq run``: the twelfth instance of this repo's built-tested-never-called defect, caught
        by running the CLI rather than by the tests. An explicit argument makes the omission a
        signature error instead of a silent zero, and removes the staleness hazard of a per-solve
        value living on the solver between calls.
        """
        site = problem.site_model
        assumptions = problem.assumptions
        # W7-4: the solver is the first GateContext producer — evidence-bearing gate facts from
        # the seam's referenced commitments (None when there are none: byte-identical to before).
        context, context_warnings = produce_gate_context(
            power_kw=schedule.power_kw,
            energy_kwh=schedule.energy_kwh,
            duration_hours=schedule.duration_hours,
            constraints=list(problem.constraints),
            assumptions=assumptions,
        )
        value_streams, gated_upside, used = build_value_streams(
            schedule,
            site,
            assumptions,
            registry=self._registry,
            context=context,
            capacity_reduction=capacity_reduction,
        )
        warnings = [*warnings, *context_warnings]
        # Every key the DISPATCH depended on, not just the ones the value streams read — so an
        # un-sourced marginal cycle cost or an assumed full-window SoC sweep can never be presented
        # as bankable, the same discipline the RTE placeholder already enforces.
        #
        # BOTH degradation-cost homes are listed because ``degradation_cost_gbp_per_kwh`` reads
        # either one: gating on ``_DEG_COST_KEY`` alone would leave the key the solve ACTUALLY
        # consumed (``_DEG_COST_KEY_LEGACY``, the only one any shipped release carries — gb_v1_6
        # £5.0, gb_v1_7 £21.3) unchecked, so a placeholder-status wear cost could have reached a
        # bankable verdict through the unexamined branch. Listing both is strictly conservative:
        # `_bankable_status` fails on the FIRST unconfirmed key, and an absent key is unconfirmed.
        # Neither is confirmed in any release today (gb_v1_7's is `needs_review`), and gb_v1_7 is
        # the first release to carry the SoC window at all, so every current run is NOT_BANKABLE
        # here by construction — as it should be.
        status = self._bankable_status(
            assumptions,
            set(used)
            | {
                _RTE_KEY,
                _WARRANTY_KEY,
                _DEG_COST_KEY,
                _DEG_COST_KEY_LEGACY,
                _SOC_MIN_KEY,
                _SOC_MAX_KEY,
            },
        )
        return DispatchResult(
            power_kw=round(schedule.power_kw, 6),
            energy_kwh=round(schedule.energy_kwh, 6),
            duration_hours=round(schedule.duration_hours, 6),
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=status,
            solver_status=SolverStatus.OPTIMAL,
            dispatch_schedule_ref=schedule.ref(),
            value_streams=value_streams,
            gated_upside=gated_upside,
            constraints_binding=binding,
            annual_charge_kwh=schedule.annual_charge_kwh,
            annual_discharge_kwh=schedule.annual_discharge_kwh,
            annual_throughput_mwh=schedule.annual_throughput_mwh,
            equivalent_full_cycles=schedule.equivalent_full_cycles,
            # The windowed-levy credit finance charges once, on the cost side. ``None`` on a site
            # with no windowed levy, so the finance line is ABSENT rather than a modelled £0 — and
            # every pre-windowed result stays field-for-field identical.
            windowed_levy_avoidance_gbp=(
                None
                if schedule.windowed_levy_rate_gbp_per_kwh is None
                else schedule.windowed_levy_avoidance_gbp
            ),
            warnings=[
                "MILP dispatch (highs-milp-v1): per-stream value is site-derived from the "
                "half-hourly load × tariff, net of round-trip losses.",
                *warnings,
            ],
        )

    def _unsolved(
        self,
        problem: DispatchProblem,
        status: SolverStatus,
        reason: str,
        extra_warnings: list[str] | None = None,
    ) -> DispatchResult:
        """A contract-valid, no-dispatch result (honest degrade) — no spec, no invented volumes."""
        value_streams, gated_upside, _ = build_value_streams(
            None, problem.site_model, problem.assumptions, registry=self._registry, context=None
        )
        return DispatchResult(
            power_kw=None,
            energy_kwh=None,
            duration_hours=None,
            implementation_level=ImplementationLevel.PRODUCTION,
            bankable_status=BankableStatus.NOT_BANKABLE,
            solver_status=status,
            dispatch_schedule_ref=None,
            value_streams=value_streams,
            gated_upside=gated_upside,
            constraints_binding=[],
            annual_charge_kwh=None,
            annual_discharge_kwh=None,
            annual_throughput_mwh=None,
            equivalent_full_cycles=None,
            warnings=[
                reason,
                *(extra_warnings or []),
                "MILP dispatch: no schedule solved; charge/discharge volume unavailable "
                "(None, never invented).",
            ],
        )

    @staticmethod
    def _bankable_status(assumptions: AssumptionLibrary, keys: set[str]) -> BankableStatus:
        """Never bankable_candidate here; not_bankable if a used assumption is unconfirmed.

        On the all-placeholder gb_v0_1 library this is always NOT_BANKABLE (round-trip efficiency is
        placeholder); the final bankable verdict is finance's (DSCR + covenant), not the solver's.
        """
        for key in keys:
            section, name = key.split(".", 1)
            if not is_confirmed(maybe(assumptions, section, name)):
                return BankableStatus.NOT_BANKABLE
        return BankableStatus.REVIEW_REQUIRED
