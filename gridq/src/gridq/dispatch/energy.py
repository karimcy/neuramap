"""The dispatch-energy producer seam (resolves OPEN_COMMENTS C010 / decision D13).

Finance carries an injected `DispatchEnergy` hook (``annual_charge_kwh`` →
``levies_on_charge``; ``annual_discharge_kwh`` → the price-to-target delivered-energy volume),
but nothing produced it — callers had to hand-inject it. This module is the producer: it maps a
`DispatchResult` to finance's `DispatchEnergy`, so the moment a real dispatch fills the two
volumes, finance's levies-on-charge and fixed-£/kWh price-to-target compute with **zero
downstream change**. On the placeholder both volumes are ``None``, so finance stays honestly
*unavailable* (never zero, never invented).

This is the only module in ``gridq.dispatch`` that imports finance; the import is top-level
(finance pulls no pandas and does not import dispatch, so there is no cycle and no heavy
dependency). It keeps the dependency *direction* clean — dispatch produces, finance consumes —
without coupling the dispatch contract itself to finance.
"""

from __future__ import annotations

from gridq.dispatch.contract import DispatchResult
from gridq.finance.cashflows import DispatchEnergy


def to_finance_dispatch_energy(result: DispatchResult) -> DispatchEnergy:
    """Map a `DispatchResult` to finance's `DispatchEnergy` hook.

    A faithful copy of the two annual volumes — both may be ``None`` (unavailable). The field
    names already match the names decision D13 reserved for the eventual `OptimisationResult`
    promotion, so this converter and that promotion are the same trivial mapping.

    Also copies ``windowed_levy_avoidance_gbp``: the £/yr the dispatch avoided on the site's
    TIME-WINDOWED levy lines. It rides this seam rather than being recomputed in finance because
    only the solver holds the half-hourly arrays the figure is a sum over — the levy's own window
    calendar × the solved charge/discharge — and recomputing it from annual volumes is impossible
    (an annual net-charge figure cannot say which half-hours the flow fell in). ``None`` passes
    through as ``None``, so finance reports the line unavailable rather than as a modelled £0.
    """
    return DispatchEnergy(
        annual_charge_kwh=result.annual_charge_kwh,
        annual_discharge_kwh=result.annual_discharge_kwh,
        windowed_levy_avoidance_gbp=result.windowed_levy_avoidance_gbp,
    )
