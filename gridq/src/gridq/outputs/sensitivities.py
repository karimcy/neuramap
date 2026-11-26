"""Scenario sensitivity rows from the record's own case (W8 — feeds the memo's dormant slot).

Pure record+library maths, deterministic and LLM-free: the standard Low/Central/High set
(weights FROM the assumption library; identity bands warned, a spread is never fabricated) is
run over the case's OWN cashflow series, and each scenario's project IRR is computed with the
same deterministic routine finance uses. Per-scenario DSCR stays ``None`` until real debt terms
exist (B001) — shown n/a, never guessed. Regenerating from the same record + release is
byte-identical.
"""

from __future__ import annotations

from gridq.assumptions import AssumptionLibrary
from gridq.finance.returns import project_irr
from gridq.outputs.viewmodel import SensitivityRow
from gridq.scenarios import build_standard_scenario_set, run_scenario_set
from gridq.schemas import FinancialCase


def scenario_sensitivity_rows(
    case: FinancialCase, assumptions: AssumptionLibrary
) -> tuple[SensitivityRow, ...]:
    """Low/Central/High rows for the memo's sensitivities table (empty when the set can't build).

    An identity-band scenario (no real Low/High spread supplied) is rendered with its warning in
    the note — the table shows the machinery honestly rather than a fabricated spread.
    """
    try:
        scenario_set = build_standard_scenario_set(assumptions)
    except ValueError:
        return ()  # no weights assumption — nothing honest to run (caller keeps the pending seam)
    run = run_scenario_set(case, scenario_set)
    rows: list[SensitivityRow] = []
    for res in run.results:
        irr = project_irr(res.p50_cashflows)
        notes: list[str] = []
        if (
            res.name.value != "central"
            and res.price_multiplier == 1.0
            and res.cost_multiplier == 1.0
        ):
            notes.append(
                "identity multipliers: no real band supplied; a spread is never fabricated"
            )
        rows.append(
            SensitivityRow(
                label=res.name.value.capitalize(),
                min_dscr=None,  # per-scenario DSCR needs real debt terms (B001) — n/a, not guessed
                irr_pct=irr.irr_pct if irr.available else None,
                note="; ".join(notes) or None,
            )
        )
    return tuple(rows)
