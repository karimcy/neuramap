"""Scenario DSCR/return sensitivities for the underwriting pack (L15 · M6 → M8).

Output 2 of the System Brief lists "DSCR sensitivities". This re-runs the deterministic financial
model under each Low/Central/High scenario (the M6 scenario engine's same axes, applied to the
same case + the same debt) and records each scenario's min DSCR and levered return as a
`ScenarioSensitivity`. There is no new financial maths here — a sensitivity is just the financial
model under a scenario. A figure a scenario cannot support (e.g. DSCR with no debt) stays ``None``,
never guessed.

**What the rows are allowed to assert (N8/N17/N18).** `build_cashflow_model` reads exactly two
fields off a scenario — `price_multiplier` and `cost_multiplier` — and
`build_standard_scenario_set` defaults every unsupplied band to identity (1.0). So on the shipped
library the three financial-model calls below are bit-identical and Low, Central and High carry
equal DSCR and IRR. Three equal rows presented without comment read to a lender as "this case is
insensitive to price and cost" — the strongest possible statement about an asset, made by a table
that ran no sensitivity at all. The builder already writes the honest data-ask onto
`Scenario.warnings`; this module's job is to carry it onto the row rather than drop it one hop
later.

**The COD delay is applied (WP-UW-17, D130).** `cod_delay_months` used to be reported on
the row and read by nothing in `finance/`, so a delayed row's figures were withheld (N18). It now
reaches `run_financial_model`, which puts the run on the month-dated timeline (operations start
the stated months late, IDC accrues over them, contracted in-term months are consumed pro rata)
and enters the case identity. The standard set carries it as a zero-weight ``cod_delay`` row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gridq.finance.financial_model import InsufficientInputsError, run_financial_model
from gridq.scenarios import build_standard_scenario_set
from gridq.schemas import BankableStatus, ScenarioSensitivity

if TYPE_CHECKING:
    from gridq.assumptions import AssumptionLibrary
    from gridq.finance import DebtTerms
    from gridq.finance.cashflows import DispatchEnergy, ResultLike
    from gridq.scenarios import Scenario, ScenarioSet
    from gridq.schemas import LenderTerms, LifecycleInputs, SiteModel

#: The fields of a `Scenario` that the financial model actually reads, with each one's identity
#: value. Anything else on a scenario is, as far as the numbers below are concerned, decoration —
#: and a row must not report it as though it moved something. Keep this in step with
#: `cashflows.build_cashflow_model` and `financial_model.run_financial_model`.
_APPLIED_SCENARIO_FIELDS: dict[str, float] = {
    "price_multiplier": 1.0,
    "cost_multiplier": 1.0,
    "cod_delay_months": 0,
}

#: The note on a delayed row: what the delay does to the figures, and what it does not model.
_APPLIED_COD_NOTE = (
    "COD delay of {months} month(s) APPLIED: construction spend on schedule, IDC accrues {months} "
    "further month(s), operating cashflows start {months} month(s) later for the full {horizon}-"
    "year horizon, and tenored contract months lost to the late start are deducted pro rata "
    "(delivery years are calendar-fixed). NOT modelled: holding costs during the delay, and "
    "termination or penalty risk from the late start."
)

#: The set-level line when no delay was stated (D130).
NO_COD_DELAY_WARNING = "NO COD-DELAY SENSITIVITY: no delay was supplied; none is assumed."


class ScenarioSensitivityResult(list[ScenarioSensitivity]):
    """The sensitivity rows **and** the set-level findings that used to be discarded (N17).

    `build_standard_scenario_set` populates `ScenarioSet.validation_warnings` and
    `ScenarioSet.bankable_status`, and the caller read neither — nor `Scenario.warnings` — so the
    only thing that reached the `QuoteRecord` was the numbers. Carrying them back gives
    `generate_quote` something to merge into the quote's warnings.

    A `list` subclass rather than a wrapper, so every existing caller (which only ever iterated,
    indexed or compared the rows) is unchanged and a plain-list equality still holds.
    """

    def __init__(
        self, rows: list[ScenarioSensitivity], set_warnings: list[str] | None = None
    ) -> None:
        super().__init__(rows)
        self.set_warnings: list[str] = list(set_warnings or [])

    @property
    def rows(self) -> list[ScenarioSensitivity]:
        """The rows as a plain list — for callers that want the distinction to be explicit."""
        return list(self)


def _row_note(scenario: Scenario, computed_note: str | None) -> str | None:
    """Join the scenario's own warnings to whatever the computation had to say.

    The scenario's warnings come first: "no low sensitivity supplied — price and cost both held
    at identity (1.0), so this case equals Central" is the single most important thing a reader
    of an all-identity table needs, and it was being thrown away here.
    """
    parts = [*scenario.warnings]
    if computed_note:
        parts.append(computed_note)
    return " | ".join(parts) if parts else None


def _set_warnings(scenario_set: ScenarioSet) -> list[str]:
    """Set-level findings worth carrying onto the quote (N17)."""
    warnings = list(scenario_set.validation_warnings)
    if scenario_set.bankable_status is BankableStatus.NOT_BANKABLE:
        warnings.append(
            f"scenario set {scenario_set.scenario_set_id!r} is NOT bankable — its sensitivity "
            "rows are indicative only."
        )
    identity = [
        s.name.value
        for s in scenario_set.scenarios
        if all(getattr(s, f) == v for f, v in _APPLIED_SCENARIO_FIELDS.items())
    ]
    if not any(s.cod_delay_months for s in scenario_set.scenarios):
        warnings.append(NO_COD_DELAY_WARNING)
    if len(identity) == len(scenario_set.scenarios):
        warnings.append(
            "NO SENSITIVITY WAS RUN: every scenario in the set holds "
            "price_multiplier and cost_multiplier at identity (1.0), so all "
            f"{len(scenario_set.scenarios)} rows are the base case repeated. Equal Low/Central/"
            "High rows here are the absence of a band, NOT evidence that the case is insensitive "
            "to price or cost. Supply real bands or a hash-pinned forecast reference (N8)."
        )
    return warnings


def compute_scenario_sensitivities(
    site: SiteModel,
    result: ResultLike,
    assumptions: AssumptionLibrary,
    *,
    debt_terms: DebtTerms | None = None,
    dispatch_energy: DispatchEnergy | None = None,
    scenario_set: ScenarioSet | None = None,
    lender_terms: LenderTerms | None = None,
    lifecycle: LifecycleInputs | None = None,
) -> ScenarioSensitivityResult:
    """Return Low/Central/High DSCR/return sensitivities for the priced case.

    Each row is the financial model re-run under one scenario, against the same debt and dispatch
    as the base case (so the central row reproduces the base), and carries the scenario's
    time-to-energisation (COD) delay so consenting-timeline shifts are reported.
    ``scenario_set`` defaults to the standard Low/Central/High set built from the library.
    Deterministic.

    Returns a `ScenarioSensitivityResult`: the rows, plus the set-level warnings the caller must
    merge onto the quote. It iterates and indexes as the plain list it replaced, so existing
    row-reading callers are unchanged.

    ``lifecycle`` (WP-UW-17) is the same stated lifecycle as the base case; its
    ``cod_delay_months`` adds the zero-weight delay row to the default set, and every row runs on
    the same construction programme and lifecycle flows.
    """
    if scenario_set is None:
        scenario_set = build_standard_scenario_set(
            assumptions,
            cod_delay_months=lifecycle.cod_delay_months if lifecycle is not None else None,
        )
    rows: list[ScenarioSensitivity] = []
    for scenario in scenario_set.scenarios:
        label = scenario.name.value.title()
        if scenario.notes and scenario.cod_delay_months:
            label = scenario.notes
        try:
            run = run_financial_model(
                site,
                result,
                assumptions,
                scenario=scenario,
                debt_terms=debt_terms,
                dispatch_energy=dispatch_energy,
                # WP-UW-03: the same lender structure as the base case, so the central row
                # reproduces the base DSCR and Low/High measure the same loan under stress.
                lender_terms=lender_terms,
                lifecycle=lifecycle,
            )
        except InsufficientInputsError as exc:
            rows.append(
                ScenarioSensitivity(
                    scenario_id=scenario.scenario_id,
                    label=label,
                    cod_delay_months=scenario.cod_delay_months,
                    note=_row_note(scenario, f"not priced under this scenario: {exc}"),
                )
            )
            continue
        case = run.case
        # Prefer the levered (equity) return — the headline the sizing objective maximises; fall
        # back to the project IRR and say so. Either may be None (then unavailable, not guessed).
        levered = case.equity_irr
        irr_pct = levered if levered is not None else case.irr
        computed_note = (
            None if levered is not None or case.irr is None else "project IRR (no levered return)"
        )
        if scenario.cod_delay_months:
            delay_note = _APPLIED_COD_NOTE.format(
                months=scenario.cod_delay_months, horizon=run.cashflows.horizon_years
            )
            computed_note = f"{delay_note} | {computed_note}" if computed_note else delay_note
        rows.append(
            ScenarioSensitivity(
                scenario_id=scenario.scenario_id,
                label=label,
                min_dscr=case.dscr_summary.min_dscr,
                irr_pct=irr_pct,
                cod_delay_months=scenario.cod_delay_months,
                # N8/N17: the scenario's own "this case equals Central" data-ask leads the note.
                note=_row_note(scenario, computed_note),
            )
        )
    return ScenarioSensitivityResult(rows, _set_warnings(scenario_set))
