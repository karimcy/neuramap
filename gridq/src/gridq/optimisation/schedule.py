"""The concrete half-hourly dispatch schedule the MILP produces.

This is what ``DispatchResult.dispatch_schedule_ref`` points at: the full half-hourly charge /
discharge / state-of-energy series plus the price signal the engine optimised against, and the
**aggregate quantities** the value functions read (it satisfies the value-stack
``DispatchScheduleLike`` read-model — ``power_mw``/``duration_h``/``annual_discharge_mwh``/…).

It is the dispatch engine's *internal* artifact (not the frozen seam): a plain frozen
dataclass carrying numpy arrays, with deterministic, rounded aggregate accessors. Partial-year
metered horizons are scaled to a full year by ``annual_factor`` (= 8760 h ÷ covered hours) so the
energy/throughput/value figures are annual; missing intervals are excluded (never back-filled), so
the covered hours reflect only present data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gridq.provenance.hashing import hash_json
from gridq.schemas.customer_bill import CustomerBill
from gridq.schemas.dispatch_profile import DispatchProfile, DispatchProfilePoint
from gridq.tariffs.hv_tariff_model import chargeable_reactive_kvarh

_HOURS_PER_YEAR = 8760.0


@dataclass(frozen=True)
class HalfHourlyDispatchSchedule:
    """A solved half-hourly battery dispatch for one fixed system spec.

    All power arrays are kW on the AC side over the metered horizon; ``soe_kwh`` is the state of
    energy (kWh). ``energy_price_gbp_per_kwh`` is the time-of-use energy price the MILP arbitraged
    (``None`` when the tariff had no time-mappable spread); ``duos_rate_gbp_per_kwh`` is the
    per-half-hour DUoS rate. Aggregate accessors are annualised via ``annual_factor`` and rounded
    for determinism.
    """

    power_kw: float
    energy_kwh: float
    duration_hours: float
    interval_hours: float  # half-hourly granularity Δt (0.5)
    annual_factor: float  # 8760 ÷ covered (weighted) hours (1.0 for a full metered year)
    charge_kw: np.ndarray
    discharge_kw: np.ndarray
    soe_kwh: np.ndarray
    site_load_kw: np.ndarray
    duos_rate_gbp_per_kwh: np.ndarray
    red_band_mask: np.ndarray
    energy_price_gbp_per_kwh: np.ndarray | None = None
    # WP-UW-07 (D105): the CHARGE-side energy price when the stack leaves some half-hours
    # unpriced (a band it did not quote, or labels with no window). On those half-hours charging
    # was costed at the highest quoted rate and discharge credited at the lowest, so no spread is
    # earned from them. ``None`` (every stack that prices every half-hour as itself) means the same
    # array as ``energy_price_gbp_per_kwh``, and every accessor below is byte-identical.
    energy_buy_price_gbp_per_kwh: np.ndarray | None = None
    deliverable_capacity_mw: float | None = None
    tender_window_discharge_mwh: float | None = None
    # D57 item 5b (2026-08-18): the CHARGE-side mirror of the field above, measured over the same
    # local-flex availability windows and capped at the same committed kW. It exists because the
    # awarded product at these sites is DEMAND TURN UP / GENERATION TURN DOWN - NGED's Port Ham
    # clear pays the asset to CHARGE in the summer daytime - and a turn-up obligation delivers by
    # RAISING import, not by discharging. Pricing a turn-up award off the discharge field measures
    # the wrong direction and reads GBP 0 whenever the optimum (correctly) does not discharge into a
    # daytime window. ``None`` when no local-flex availability commitment compiled, exactly like the
    # discharge field, so every schedule without one stays byte-identical.
    tender_window_charge_mwh: float | None = None
    # D61 (2026-11-22): (commitment_id, committed kW) for every LOCAL_FLEX_PICLO availability
    # window the solver compiled; ``None`` when none compiled or the schedule was not produced by a
    # solve. The valuation bills exactly this record, so the invoiced MW is the reserved MW at every
    # candidate size. NOT part of `ref()`, like the tender fields above.
    local_flex_reserved_kw: tuple[tuple[str, float], ...] | None = None
    # Per-interval representation weights (typical-period proxy solves, D32): interval t stands
    # for `interval_weights[t]` real intervals. ``None`` (the default, every full-series solve)
    # means all-ones and is byte-identical to the unweighted behaviour.
    interval_weights: np.ndarray | None = None
    # Provisioning (issue 017 / D42): the constant forced draw serving declared unmet load in
    # obligation windows (kW per instant). ``None`` = no requirement compiled — byte-identical
    # behaviour. Never enters priced value: it is not part of charge_kw/discharge_kw and the
    # objective cannot see it.
    load_service_kw: np.ndarray | None = None
    # Wholesale EXPORT (issue 018 / D43): the past-the-meter injection (kW per instant) and the
    # aligned wholesale export price (£/kWh per instant) from the consent-gated OVERLAY solve.
    # BOTH ``None`` (every priced-floor solve — no export variable was created) is byte-identical
    # to the pre-018 schedule. Export revenue is GATED UPSIDE, reported only as the WHOLESALE_P415
    # delta; it never enters the priced-floor accessors, the customer bill or DispatchEnergy.
    export_kw: np.ndarray | None = None
    export_price_gbp_per_kwh: np.ndarray | None = None
    # Time-windowed levy rate (£/kWh per instant) — the component of the site's levy stack that is
    # charged only inside a stated window (`TariffStack.windowed_levies`; in GB the Capacity Market
    # supplier obligation, charged on 16:00–19:00 working-day November–February consumption). Zeros
    # outside every window, so the array is dense. ``None`` (every site with no windowed levy) is
    # byte-identical to the pre-windowed schedule: `ref()` gains no key and
    # `windowed_levy_avoidance_gbp` is 0.0.
    windowed_levy_rate_gbp_per_kwh: np.ndarray | None = None
    # DFS utilisation price (£/kWh per instant) — ``P x K / 1000`` inside the called settlement
    # periods of the DFS winter season and ZERO everywhere else, so the array is dense. It is a
    # REVENUE price the objective maximised against, exactly parallel in shape to
    # ``windowed_levy_rate_gbp_per_kwh``, and its £ is reported on its own stream
    # (:attr:`dfs_utilisation_value_gbp`) and nowhere else. ``None`` (a solve on a release or a
    # horizon with no DFS periods) is byte-identical to the pre-D57 schedule: `ref()` gains no key
    # and `dfs_utilisation_value_gbp` is 0.0.
    dfs_price_gbp_per_kwh: np.ndarray | None = None
    # WP-UW-07 (D106): REACTIVE POWER, present only when the site's metered file carries
    # both reactive channels for every solved half-hour. ``site_reactive_kvar`` is the site's own
    # ``max(RI, RE)`` as an average kVAr per interval; ``reactive_compensation_kvar`` is what the
    # inverter supplied against it (the dispatch variable, ``0 <= c <= min(S, m)``);
    # ``reactive_rate_gbp_per_kvarh`` is the published CDCM excess reactive rate, ``None`` when the
    # tariff states none (EDCM has none). All ``None`` (every site without the channels) is
    # byte-identical to the pre-UW-07 schedule: `ref()` gains no key and no accessor moves.
    site_reactive_kvar: np.ndarray | None = None
    reactive_compensation_kvar: np.ndarray | None = None
    reactive_rate_gbp_per_kvarh: float | None = None

    # --- reactive power (WP-UW-07) -------------------------------------------

    def _chargeable_kvarh(self, *, after: bool) -> float | None:
        """Annual chargeable kVArh under the exact LC14 rule; ``None`` with no reactive series.

        ``max(max(RI, RE) - 0.33 x AI, 0)`` per half-hour, counted only where active import is
        positive, summed (weighted) and annualised. Before: the metered reactive against the
        metered import. After: the residual ``m - c`` against ``load + charge - discharge``. The
        one rule (`gridq.tariffs.hv_tariff_model.chargeable_reactive_kvarh`) the published-tariff
        tests hold, so the bill and the tariff model cannot disagree.
        """
        site_q = self.site_reactive_kvar
        if site_q is None:
            return None
        dt = self.interval_hours
        if after:
            comp = (
                self.reactive_compensation_kvar
                if self.reactive_compensation_kvar is not None
                else np.zeros_like(site_q)
            )
            residual = np.clip(site_q - comp, 0.0, None)
            active = np.clip(self.site_load_kw + self.charge_kw - self.discharge_kw, 0.0, None)
        else:
            residual = site_q
            active = np.clip(self.site_load_kw, 0.0, None)
        per_interval = chargeable_reactive_kvarh(active * dt, residual * dt, None)
        return float((per_interval * self._weights()).sum()) * self.annual_factor

    @property
    def reactive_charge_before_gbp(self) -> float | None:
        """The excess reactive charge on the metered site, £/yr; ``None`` if unpriced/unmetered."""
        kvarh = self._chargeable_kvarh(after=False)
        rate = self.reactive_rate_gbp_per_kvarh
        return None if kvarh is None or rate is None else round(kvarh * rate, 2)

    @property
    def reactive_charge_after_gbp(self) -> float | None:
        """The excess reactive charge after the dispatch's compensation, £/yr (exact rule)."""
        kvarh = self._chargeable_kvarh(after=True)
        rate = self.reactive_rate_gbp_per_kvarh
        return None if kvarh is None or rate is None else round(kvarh * rate, 2)

    @property
    def reactive_charge_avoidance_gbp(self) -> float | None:
        """before − after, £/yr, **signed**.

        Discharging lowers the 0.33 x AI allowance, so the battery can raise the charge as well as
        cancel it, and a clamp would hide that.
        """
        before, after = self.reactive_charge_before_gbp, self.reactive_charge_after_gbp
        return None if before is None or after is None else round(before - after, 2)

    @property
    def load_service_energy_mwh(self) -> float | None:
        """Annualised energy forcibly served against load-service obligations (MWh).

        ``None`` when no obligation was compiled. Measured delivery for reporting and
        evidence — never priced as import displacement (issue 017 / D42).
        """
        if self.load_service_kw is None:
            return None
        served = float((self.load_service_kw * self._weights()).sum()) * self.interval_hours
        return round(served * self.annual_factor / 1000.0, 6)

    def _weights(self) -> np.ndarray | float:
        """The per-interval representation weights (1.0 when unweighted)."""
        return 1.0 if self.interval_weights is None else self.interval_weights

    # --- DispatchScheduleLike read-model (value-stack `dispatch.py`) --------

    @property
    def power_mw(self) -> float:
        return round(self.power_kw / 1000.0, 6)

    @property
    def duration_h(self) -> float:
        return self.duration_hours

    @property
    def annual_discharge_mwh(self) -> float:
        return round(self.annual_discharge_kwh / 1000.0, 6)

    @property
    def red_band_discharge_mwh(self) -> float:
        weighted = self.discharge_kw * self._weights()
        kwh = float((weighted[self.red_band_mask]).sum()) * self.interval_hours
        return round(kwh * self.annual_factor / 1000.0, 6)

    # --- annual energy accounting (the DispatchEnergy producer figures) ----

    @property
    def annual_charge_kwh(self) -> float:
        kwh = float((self.charge_kw * self._weights()).sum()) * self.interval_hours
        return round(kwh * self.annual_factor, 3)

    @property
    def annual_discharge_kwh(self) -> float:
        kwh = float((self.discharge_kw * self._weights()).sum()) * self.interval_hours
        return round(kwh * self.annual_factor, 3)

    @property
    def annual_discharge_outside_dfs_kwh(self) -> float:
        """Annualised discharge (kWh) OUTSIDE the modelled DFS-called periods (D62 limb 1).

        The same weighted, annualised sum as :attr:`annual_discharge_kwh`, restricted to the
        intervals where ``dfs_price_gbp_per_kwh`` is not ``> 0``. With DFS in every objective (D57)
        a site with called periods never discharges zero in total, so "did the battery decline to
        cycle on the tariff?" is a question about THIS figure, not the total. Equal to
        :attr:`annual_discharge_kwh` when the schedule carries no DFS price at all.
        """
        if self.dfs_price_gbp_per_kwh is None:
            return self.annual_discharge_kwh
        outside = ~(self.dfs_price_gbp_per_kwh > 0.0)
        weighted = self.discharge_kw * self._weights()
        kwh = float(weighted[outside].sum()) * self.interval_hours
        return round(kwh * self.annual_factor, 3)

    @property
    def annual_throughput_mwh(self) -> float:
        return round(self.annual_discharge_kwh / 1000.0, 6)

    @property
    def equivalent_full_cycles(self) -> float | None:
        if self.energy_kwh <= 0:
            return None
        return round(self.annual_discharge_kwh / self.energy_kwh, 4)

    # --- annual value components (site-derived, never a per-MW proxy) -------

    def _net_value(self, price: np.ndarray) -> float:
        """Σ price·(discharge − charge)·Δt over the horizon, annualised. Net bill reduction."""
        net = (self.discharge_kw - self.charge_kw) * price * self._weights()
        return float(net.sum()) * self.interval_hours * self.annual_factor

    def _energy_spread_penalty(self) -> float:
        """Σ (buy − sell)·charge·Δt, annualised: what the no-spread envelope adds to charging.

        Zero (and never computed) when ``energy_buy_price_gbp_per_kwh`` is ``None``. Subtracted
        from the energy value so the reported arbitrage is the objective's own energy term.
        """
        buy, sell = self.energy_buy_price_gbp_per_kwh, self.energy_price_gbp_per_kwh
        if buy is None or sell is None:
            return 0.0
        extra = (buy - sell) * self.charge_kw * self._weights()
        return float(extra.sum()) * self.interval_hours * self.annual_factor

    @property
    def arbitrage_value_gbp(self) -> float:
        """Energy-band bill reduction (£/yr), **signed**; 0 when no time-mappable spread exists.

        SIGNED, not clamped (2026-08-10 hostile review F-02). The previous
        ``max(0.0, ...)`` clamped this component independently of the DUoS component and the two
        were then summed — so the reported floor could **exceed the LP's own optimal objective
        value** whenever the optimum bought energy at a loss to capture a larger DUoS spread (the
        rational behaviour on a DUoS-driven site). The clamp deleted the loss and kept the gain.

        The invariant that must hold, and is pinned by
        ``tests/test_floor_equals_objective.py``:

            arbitrage_value_gbp + duos_avoidance_value_gbp == priced_floor_terms_gbp

        A negative value here is a genuine *cost* and is carried as one: `valuation.py` routes it
        to ``ValueStreamResult.annual_cost_gbp``, which finance subtracts in EBITDA. It is never
        deleted.
        """
        if self.energy_price_gbp_per_kwh is None:
            return 0.0
        value = self._net_value(self.energy_price_gbp_per_kwh) - self._energy_spread_penalty()
        return round(value, 2)

    @property
    def duos_avoidance_value_gbp(self) -> float:
        """DUoS bill reduction (£/yr), **signed** — dominated by red-band discharge.

        Signed for the same reason as `arbitrage_value_gbp` (F-02): the two components must sum to
        the MILP's own objective term, so neither may be clamped in isolation.
        """
        return round(self._net_value(self.duos_rate_gbp_per_kwh), 2)

    @property
    def windowed_levy_avoidance_gbp(self) -> float:
        """Windowed-levy bill reduction (£/yr), **signed**; 0.0 when the site has no windowed levy.

        ``Σ windowed_rate_t · (discharge_t − charge_t) · Δt``, annualised — the exact negative of
        the windowed component of the MILP objective's levy term, on the same net-import convention
        as `arbitrage_value_gbp` and `duos_avoidance_value_gbp`. Positive on a normal dispatch,
        because the window is a peak window: the battery discharges inside it (reducing charged
        import) and charges outside it, where this levy's rate is £0.

        **Load-capped by construction, which is the point.** Capacity Market Chargeable Demand is
        Active Import with no export credit, so avoided cost can never exceed the site's own
        in-window import. ``discharge_t ≤ min(power, load_t)`` is the MILP's behind-the-meter bound
        (`milp_solver`: ``discharge_ub``), so ``load_t + charge_t − discharge_t ≥ 0`` in every
        half-hour and this figure is bounded above by the levy on the site's metered in-window
        import. It is never a rated-power extrapolation.

        SIGNED, not clamped, for the same reason as the other two components (F-02): a clamp would
        delete the loss on a window the optimum charged in and report more avoidance than the
        objective earned.
        """
        if self.windowed_levy_rate_gbp_per_kwh is None:
            return 0.0
        return round(self._net_value(self.windowed_levy_rate_gbp_per_kwh), 2)

    @property
    def dfs_utilisation_value_gbp(self) -> float:
        """DFS utilisation revenue (£/yr), **signed**; 0.0 when no DFS period was modelled.

        ``Σ dfs_rate_t · (discharge_t − charge_t) · Δt``, annualised — the exact DFS term of the
        MILP objective, on the same net convention as `arbitrage_value_gbp`. NET, not gross, and
        deliberately: DFS settlement volume V is measured **against a baseline**, so charging inside
        a called period is negative delivery and the piecewise factor K goes negative for it. A
        clamp here would delete that liability, which is the error `dfs_performance_factor` exists
        to
        refuse one level down.

        **It double-books nothing.** The price array is its own, disjoint from the energy, DUoS and
        levy arrays, so no pound appears twice. That a called DFS half-hour ALSO reduces the site's
        own bill is not double-booking either: NESO pays the utilisation and the supplier bills
        less,
        which is two payers and two pounds. Under D57 the exclusivity rules that would forbid the
        pair on one meter point are assumed satisfied, and the DFS stream says so on the record.
        """
        if self.dfs_price_gbp_per_kwh is None:
            return 0.0
        return round(self._net_value(self.dfs_price_gbp_per_kwh), 2)

    @property
    def dfs_delivered_mwh(self) -> float | None:
        """Net MWh/yr delivered inside the modelled DFS called periods; ``None`` when none exist."""
        if self.dfs_price_gbp_per_kwh is None:
            return None
        called = self.dfs_price_gbp_per_kwh > 0.0
        net = (self.discharge_kw - self.charge_kw) * self._weights()
        kwh = float(net[called].sum()) * self.interval_hours
        return round(kwh * self.annual_factor / 1000.0, 6)

    # --- export (gated upside, issue 018 / D43) ----------------------------

    @property
    def priced_floor_terms_gbp(self) -> float:
        """UNCLAMPED priced-floor objective term Σ (energy+duos)·(discharge−charge)·Δt, annualised.

        This is the arbitrage+DUoS objective term the MILP maximised — the sum the export
        upside delta subtracts across the floor and the overlay solve. It equals
        ``arbitrage_value_gbp + duos_avoidance_value_gbp`` before their separate 2 dp rounding:
        both components have been SIGNED, not clamped at £0, since MD-F-02 (hostile review F-02),
        and a window whose arbitrage went negative to free energy for export nets honestly against
        the export revenue. Uses the energy-price and DUoS arrays of the objective (energy price,
        zeros when the tariff had no time-mappable spread, plus DUoS); the objective's levy,
        windowed levy and DFS terms are excluded here, for the reasons below and in
        ``dfs_utilisation_value_gbp``. Rounded for determinism; not clamped.

        Since F-03 the solved objective ALSO carries a ``levy·(discharge−charge)`` term (the
        optimiser must see the full delivered cost of a charged kWh). That term is deliberately not
        here: this property is the reported priced-floor REVENUE, and the levy is charged once on
        the cost side, in finance's ``levies_on_charge``. **Both levy components are excluded, flat
        and windowed** — the windowed part is charged once too, in finance's
        ``windowed_levies_avoided`` line, and admitting it here would break
        ``arbitrage + duos == priced_floor_terms`` (``tests/test_floor_equals_objective.py``) while
        double-counting the same £. Open seam, stated rather than hidden: the
        export-upside delta built from this property therefore omits the extra levy on the extra
        import an export overlay charges — gated upside only, never the priced floor.
        """
        energy = self.energy_price_gbp_per_kwh
        base = energy if energy is not None else np.zeros_like(self.duos_rate_gbp_per_kwh)
        price = base + self.duos_rate_gbp_per_kwh
        return round(self._net_value(price) - self._energy_spread_penalty(), 2)

    @property
    def annual_export_mwh(self) -> float | None:
        """Annualised wholesale export energy (MWh); ``None`` when no export variable exists."""
        if self.export_kw is None:
            return None
        kwh = float((self.export_kw * self._weights()).sum()) * self.interval_hours
        return round(kwh * self.annual_factor / 1000.0, 6)

    @property
    def export_revenue_gbp(self) -> float:
        """Annualised wholesale export revenue Σ export_price·export·Δt (£/yr); 0 when no export.

        Gated upside — reported only via the WHOLESALE_P415 delta; never in a priced-floor total.
        """
        if self.export_kw is None or self.export_price_gbp_per_kwh is None:
            return 0.0
        rev = self.export_kw * self.export_price_gbp_per_kwh * self._weights()
        return round(float(rev.sum()) * self.interval_hours * self.annual_factor, 2)

    # --- binding-constraint detection (deterministic, tolerance-based) -----

    def binding_constraints(
        self, *, connection_limit_kw: float, throughput_cap_kwh: float
    ) -> list[str]:
        """Which dispatch constraints actually bind, as stable ids (deterministically ordered)."""
        binding: list[str] = []
        tol = 1e-6
        headroom = connection_limit_kw - self.site_load_kw
        # The connection binds if charge is pushed up to the (load-reduced) connection headroom
        # in any half-hour where that headroom is the active limit (below the rated power).
        charge_at_headroom = (self.charge_kw + tol >= headroom) & (headroom + tol < self.power_kw)
        if bool(np.any(charge_at_headroom & (self.charge_kw > tol))):
            binding.append("connection_charge_limit")
        horizon_throughput = (
            float((self.discharge_kw * self._weights()).sum()) * self.interval_hours
        )
        if throughput_cap_kwh > 0 and horizon_throughput + tol >= throughput_cap_kwh:
            binding.append("warranty_throughput_cap")
        return binding

    # --- the customer bill, before vs after (C017 / W7-5) -------------------

    def customer_bill(
        self,
        *,
        levy_rate_gbp_per_kwh: float,
        standing_gbp_per_year: float = 0.0,
        capacity_gbp_per_year: float = 0.0,
        tariff_provenance: str | None = None,
    ) -> CustomerBill | None:
        """The annual customer bill decomposition from the series the MILP optimised.

        Before = the metered load billed at the stack's time-of-use energy + DUoS rates +
        £/kWh levy lines; after = the same rates over the net import (load + charge −
        discharge — losses bought at the price faced). ``standing_gbp_per_year`` and
        ``capacity_gbp_per_year`` are battery-invariant fixed lines carried identically on both
        sides (2026-07-07): they never move the saving but make the totals a real annual bill.
        ``None`` when the tariff had no time-mappable energy rate: a partial bill would misstate
        the headline, so the proposal keeps its honest pending seam instead (never a partial
        figure). ``tariff_provenance`` is `tariff_calendar.tariff_provenance_phrase` of the stack
        priced; the basis then opens with whose rates they are (R8), and says nothing of it
        otherwise rather than implying they are the site's own.
        """
        if self.energy_price_gbp_per_kwh is None:
            return None
        factor = self.interval_hours * self.annual_factor
        net_import = np.clip(self.site_load_kw + self.charge_kw - self.discharge_kw, 0.0, None)
        # Weighted like every other annualised aggregate: on a typical-period proxy solve
        # (D32) each present interval stands for `interval_weights[t]` real intervals.
        w = self._weights()
        price = self.energy_price_gbp_per_kwh  # narrowed by the guard above

        # The windowed levy component (`TariffStack.windowed_levies`) is billed like DUoS, on the
        # kWh imported in its own half-hours — NOT on annual volume — so it enters through the same
        # per-instant multiply the DUoS line uses. Omitting it here while the objective priced it
        # would make the reported customer saving smaller than the saving the P&L credit books, and
        # the two must be the same £.
        windowed = self.windowed_levy_rate_gbp_per_kwh

        def _components(load_kw: np.ndarray) -> tuple[float, float, float, float]:
            kwh = float((load_kw * w).sum()) * factor
            energy = float((load_kw * w * price).sum()) * factor
            duos = float((load_kw * w * self.duos_rate_gbp_per_kwh).sum()) * factor
            levies = kwh * levy_rate_gbp_per_kwh
            if windowed is not None:
                levies += float((load_kw * w * windowed).sum()) * factor
            return round(energy, 2), round(duos, 2), round(levies, 2), round(kwh, 3)

        e_b, d_b, l_b, kwh_b = _components(self.site_load_kw)
        e_a, d_a, l_a, kwh_a = _components(net_import)
        # WP-UW-07 (D106): the excess reactive line, before and after, on the exact LC14
        # rule; ``None`` (not £0) on both sides when the site has no metered reactive channel or
        # the tariff states no reactive rate, so the bill never reads a missing line as measured.
        r_b, r_a = self.reactive_charge_before_gbp, self.reactive_charge_after_gbp
        # WP-UW-07: on half-hours the stack leaves unpriced, the energy line is a LOWER BOUND
        # (lowest quoted rate) and charging there is costed at the highest quoted rate, exactly as
        # the objective costed it, so the bill saving is never larger than the dispatch earned.
        spread = self._energy_spread_penalty()
        if spread:
            e_a = round(e_a + spread, 2)
        standing = round(max(0.0, standing_gbp_per_year), 2)
        capacity = round(max(0.0, capacity_gbp_per_year), 2)
        before = round(e_b + d_b + l_b + standing + capacity + (r_b or 0.0), 2)
        after = round(e_a + d_a + l_a + standing + capacity + (r_a or 0.0), 2)
        # The unity-PF clause is retired once the site's own reactive channel is modelled (B-17,
        # acceptance 4): kVA is then the metered kVA, not kW standing in for it.
        kva_clause = (
            "in kVA, as declared; the site's metered reactive channel is modelled"
            if self.site_reactive_kvar is not None
            else "kVA taken as kW at unity power factor"
        )
        fixed_basis = (
            "Fixed lines carried identically on both sides (battery-invariant): the standing "
            "charge and the capacity/availability charge (billed on the agreed supply capacity, "
            f"{kva_clause}). "
            if (standing > 0 or capacity > 0)
            else ""
        )
        reactive_basis = (
            ""
            if r_b is None
            else "Excess reactive power is billed on the exact LC14 rule (kVArh above 0.33 x kWh "
            "at times of active import) at the published rate of "
            f"£{self.reactive_rate_gbp_per_kvarh:.5f}/kVArh, before and after the inverter's "
            "reactive compensation. "
        )
        return CustomerBill(
            annual_bill_before_gbp=before,
            annual_bill_after_gbp=after,
            annual_saving_gbp=round(before - after, 2),
            # Informational physics (C029): the dispatch is bill-optimised, not peak-optimised.
            peak_import_before_kw=round(float(self.site_load_kw.max()), 3),
            peak_import_after_kw=round(float(net_import.max()), 3),
            energy_before_gbp=e_b,
            energy_after_gbp=e_a,
            duos_before_gbp=d_b,
            duos_after_gbp=d_a,
            levies_before_gbp=l_b,
            levies_after_gbp=l_a,
            standing_gbp=standing,
            capacity_gbp=capacity,
            import_before_kwh=kwh_b,
            import_after_kwh=kwh_a,
            # The FLAT component only: this field is a single £/kWh and a windowed levy is not one.
            # The windowed line is billed per half-hour inside `_components` above and is named in
            # `basis`; putting its rate here would read as charged on every kWh.
            levy_rate_gbp_per_kwh=round(levy_rate_gbp_per_kwh, 6),
            reactive_before_gbp=r_b,
            reactive_after_gbp=r_a,
            basis=(
                # R8 (WP-UW-07 acceptance 5): whose rates these are is the record's own statement,
                # rendered by `tariff_provenance_phrase` and never asserted here.
                ("" if tariff_provenance is None else f"Tariff: the site is {tariff_provenance}. ")
                + "Modelled lines: time-of-use energy (the site's import_rates), DUoS unit "
                "rates, and £/kWh levy lines on imported energy — computed from the metered "
                "half-hourly load and the solved dispatch (round-trip losses bought at the "
                f"price faced). {fixed_basis}{reactive_basis}"
                + (
                    ""
                    if windowed is None
                    else "The levy lines include a TIME-WINDOWED component charged only inside its "
                    f"own window (peak rate up to £{float(windowed.max()):.5f}/kWh on "
                    f"{int((windowed > 0).sum())} of {len(windowed)} half-hours); "
                    "levy_rate_gbp_per_kwh above is the FLAT component alone. "
                )
                + (
                    ""
                    if self.energy_buy_price_gbp_per_kwh is None
                    else "The energy line is a LOWER BOUND on the half-hours whose band the "
                    "tariff stack does not price (charged at the lowest quoted rate); battery "
                    "charging there is costed at the highest quoted rate, so no spread is "
                    "claimed from them. "
                )
                + "NOT modelled (excluded, not zero): the TNUoS "
                "residual, any levy not supplied as a £/kWh line on the tariff stack"
                + (
                    ", and the standing/capacity fixed lines (not supplied on this tariff)."
                    if (standing == 0 and capacity == 0)
                    else "."
                )
            ),
        )

    # --- deterministic content reference ----------------------------------

    def ref(self) -> str:
        """A deterministic content-hash id for the schedule (stable across identical solves)."""
        payload = {
            "power_kw": round(self.power_kw, 6),
            "energy_kwh": round(self.energy_kwh, 6),
            "duration_hours": round(self.duration_hours, 6),
            "annual_charge_kwh": self.annual_charge_kwh,
            "annual_discharge_kwh": self.annual_discharge_kwh,
            "arbitrage_value_gbp": self.arbitrage_value_gbp,
            "duos_avoidance_value_gbp": self.duos_avoidance_value_gbp,
            "charge_kwh_profile_sha": [round(float(x), 4) for x in self.charge_kw[:48]],
            "discharge_kwh_profile_sha": [round(float(x), 4) for x in self.discharge_kw[:48]],
        }
        # Same additive rule as the export keys below: the windowed-levy key enters the ref ONLY
        # when the site actually carries a windowed levy, so every ref on a site without one stays
        # byte-identical to the pre-windowed hash.
        if self.windowed_levy_rate_gbp_per_kwh is not None:
            payload["windowed_levy_avoidance_gbp"] = self.windowed_levy_avoidance_gbp
        # Same additive rule: the DFS key enters the ref ONLY when DFS periods were modelled, so
        # every pre-D57 ref stays byte-identical.
        if self.dfs_price_gbp_per_kwh is not None:
            payload["dfs_utilisation_value_gbp"] = self.dfs_utilisation_value_gbp
        # Export keys enter the ref ONLY when the overlay actually created an export variable —
        # so every priced-floor schedule's ref is byte-identical to the pre-018 hash (issue 018).
        # Same additive rule: the reactive keys enter the ref ONLY when a reactive series was
        # modelled (WP-UW-07), so every ref without one stays byte-identical.
        if self.site_reactive_kvar is not None:
            comp = self.reactive_compensation_kvar
            payload["reactive_compensation_kvar_profile_sha"] = (
                None if comp is None else [round(float(x), 4) for x in comp[:48]]
            )
            payload["reactive_charge_after_gbp"] = self.reactive_charge_after_gbp
        if self.export_kw is not None:
            payload["annual_export_mwh"] = self.annual_export_mwh
            payload["export_revenue_gbp"] = self.export_revenue_gbp
            payload["export_kwh_profile_sha"] = [round(float(x), 4) for x in self.export_kw[:48]]
        return f"milp-dispatch:{hash_json(payload)}"

    # --- representative-day projection (the persisted dispatch chart's source) ---------

    def representative_day_profile(self) -> DispatchProfile:
        """Project one deterministically-selected real day into a serializable `DispatchProfile`.

        Picks the **highest-total-discharge full day** (earliest index on ties) — the most legible
        day of the dispatch strategy — and copies its half-hourly values verbatim (rounded for
        determinism, clamped at 0 to shed solver-tolerance noise on the non-negative powers). It is
        **not** an average over days and invents no shape: every point is a real solved half-hour
        for that one real day. Mirrors `SizingRun.sizing_grid` — an additive projection persisted
        onto the `QuoteRecord`, leaving the frozen `OptimisationResult` untouched (D13).
        """
        intervals = max(1, round(24.0 / self.interval_hours))
        n_days = len(self.discharge_kw) // intervals
        if n_days <= 0:
            # Horizon shorter than a day: the whole (single, partial) horizon is the "day".
            start, intervals, day_index = 0, len(self.discharge_kw), 0
            basis = "full horizon (shorter than one day)"
        else:
            per_day = self.discharge_kw[: n_days * intervals].reshape(n_days, intervals)
            day_index = int(np.argmax(per_day.sum(axis=1)))  # argmax → earliest day on ties
            start = day_index * intervals
            basis = "highest-discharge day"

        price = self.energy_price_gbp_per_kwh
        points = [
            DispatchProfilePoint(
                period=i,
                charge_kw=round(max(0.0, float(self.charge_kw[start + i])), 4),
                discharge_kw=round(max(0.0, float(self.discharge_kw[start + i])), 4),
                soe_kwh=round(max(0.0, float(self.soe_kwh[start + i])), 4),
                site_load_kw=round(float(self.site_load_kw[start + i]), 4),
                duos_rate_gbp_per_kwh=round(float(self.duos_rate_gbp_per_kwh[start + i]), 6),
                red_band=bool(self.red_band_mask[start + i]),
                energy_price_gbp_per_kwh=(
                    None if price is None else round(float(price[start + i]), 6)
                ),
            )
            for i in range(intervals)
        ]
        return DispatchProfile(
            power_kw=round(self.power_kw, 6),
            energy_kwh=round(self.energy_kwh, 6),
            duration_hours=round(self.duration_hours, 6),
            interval_hours=self.interval_hours,
            representative_day_index=day_index,
            selection_basis=basis,
            points=points,
        )
