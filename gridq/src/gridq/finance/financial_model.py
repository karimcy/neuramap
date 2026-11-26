"""The financial model orchestrator (M5).

Assembles the deterministic `FinancialCase` around a `SiteModel` + `OptimisationResult` (or a
compatible result object) + a versioned `AssumptionLibrary`, optionally under a `Scenario`:

    cashflows → P50/P90 series → debt/DSCR → payback/IRR → price → FinancialCase

Every number traces to an input or a library assumption; the run is stamped with a
`RunContext` (input hash + assumption/code/schema versions) so it is reproducible. Nothing is
marked bankable off placeholder/incomplete inputs, and no metric is guessed — missing inputs
become warnings and *unavailable* fields.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

from gridq.assumptions import AssumptionLibrary, AssumptionStatus
from gridq.finance.cashflows import (
    DEFAULT_HORIZON_YEARS,
    CashflowModel,
    DispatchEnergy,
    InsufficientInputsError,
    ResultLike,
    blended_import_rate_gbp_per_kwh,
    build_cashflow_model,
    resolve_contract_tenor_evidence,
)
from gridq.finance.credit import CreditRateTenor, build_debt_terms_from_credit
from gridq.finance.debt import (
    CASE_LENDER,
    CASE_P50,
    CASE_P90,
    NO_DEBT_TERMS_REASON,
    CovenantTiers,
    DebtTerms,
    DscrResult,
    assess_debt,
    check_covenant,
    dscr_basis_for_case,
)
from gridq.finance.lender_terms import (
    covenant_tiers_from_lender_terms,
    debt_terms_from_lender_terms,
    lender_terms_are_firm,
    lender_terms_warnings,
    period_refusal_reason,
)
from gridq.finance.lifecycle import (
    ConstructionFunding,
    construction_excluded_warning,
    construction_funding,
    dated_equity_flows,
    dated_project_flows,
    excluded_ledger,
    operating_month,
)
from gridq.finance.pricing import Levelisation, recommend_price
from gridq.finance.returns import (
    DatedFlow,
    capital_recovery,
    consolidate_dated_flows,
    dated_irr,
    equity_irr,
    project_irr,
    simple_payback,
)
from gridq.provenance import RunContext, build_run_context
from gridq.schemas import (
    SCHEMA_VERSION,
    AvailabilityCommitment,
    CapitalRecoveryAccount,
    CashflowCase,
    CovenantStatus,
    DebtAssumptions,
    DscrStatus,
    DscrSummary,
    FinancialCase,
    LenderTerms,
    PriceStructure,
    SiteModel,
)
from gridq.schemas.lifecycle import (
    ConstructionRecord,
    ConstructionStatus,
    DatedFlowEntry,
    IndexationRecord,
    LifecycleInputs,
    LifecycleRecord,
    LineBasisEntry,
    ReturnBasis,
)

__all__ = [
    "FinancialModelResult",
    "capital_recovery_account",
    "InsufficientInputsError",
    "build_financial_case",
    "run_financial_model",
]


def capital_recovery_account(cashflows: list[float]) -> CapitalRecoveryAccount:
    """The mandatory `CapitalRecoveryAccount` for a year-0..N series (D47).

    One conversion, in one place. ``finance.returns.capital_recovery`` does the arithmetic (pure, no
    schema import); this maps it onto the contract object. Exposed publicly because the field is
    mandatory on `FinancialCase`, so every caller that builds a case — production here, and the
    scenario/schema fixtures — needs the same mapping, and a second hand-rolled copy would be
    free to drift from it.
    """
    recovery = capital_recovery(cashflows)
    return CapitalRecoveryAccount(
        annual_net_margin_gbp=recovery.annual_net_margin_gbp,
        operating_cashflow_gbp=recovery.operating_cashflow_gbp,
        capital_outlay_gbp=recovery.capital_outlay_gbp,
        lifetime_net_cashflow_gbp=recovery.lifetime_net_cashflow_gbp,
        capital_recovery_gap_gbp=recovery.capital_recovery_gap_gbp,
        capital_recovery_pct=recovery.capital_recovery_pct,
        every_operating_year_non_positive=recovery.every_operating_year_non_positive,
        status=recovery.status,
        narrative=recovery.narrative,
    )


def _scenario_id(scenario: object | None) -> str | None:
    """The scenario's declared id, or ``None`` when the run carries no scenario.

    **One definition, deliberately.** The id feeds two places that must never disagree: this
    module's own `_hash_input` (so two scenarios split ``case_id``) and `FinancialModelResult`
    below, which is the ONLY route by which a caller assembling a `QuoteRecord` can learn which
    scenario produced the case. If those two ever read the scenario differently, the finance
    identity and the quote identity would be pinning different things while appearing to agree.

    Duck-typed via ``getattr`` because `run_financial_model` deliberately takes ``scenario: object``
    — the finance layer reads a scenario's fields, it does not import the scenario package.
    """
    return getattr(scenario, "scenario_id", None)


@dataclass(frozen=True)
class FinancialModelResult:
    """The full financial-model output: the schema `FinancialCase` plus its supporting detail.

    `FinancialCase` is the contract artifact; the extra fields here (the `CashflowModel`, the
    `DscrResult`, the `RunContext`) carry the traceable detail and provenance that the schema
    does not have a slot for, so callers (quotes/outputs) can stamp and audit the run.

    ``scenario_id`` is the declared id of the `Scenario` this case was run under, or ``None``. It
    is here because it is **not recoverable from `FinancialCase`**: the case records what the
    scenario *did* (multiplied cashflows) but never which scenario was named, and the one field
    that used to carry the name — ``case_id``, ``fc_`` + the finance ``run_id`` — was removed from
    the quote's canonical hash input by 9c07c5b, because it also smuggled ``code_version`` in.
    Two scenarios with identity multipliers therefore produce byte-identical cases, and a caller
    holding only the case cannot tell the runs apart. Exposing the id here is what lets the quote
    layer pin the input directly instead of through a derived identity (see
    `quotes.assembly._canonical_run_input`).
    """

    case: FinancialCase
    cashflows: CashflowModel
    dscr: DscrResult
    run_context: RunContext
    bankable: bool
    scenario_id: str | None = None


def run_financial_model(
    site: SiteModel,
    result: ResultLike,
    assumptions: AssumptionLibrary,
    *,
    horizon_years: int | None = None,
    scenario: object | None = None,
    debt_terms: DebtTerms | None = None,
    dispatch_energy: DispatchEnergy | None = None,
    price_structure: PriceStructure = PriceStructure.FIXED_GBP_PER_KWH,
    commitments: Iterable[AvailabilityCommitment] | None = None,
    contract_tenor_years: Mapping[str, float] | None = None,
    lender_terms: LenderTerms | None = None,
    lifecycle: LifecycleInputs | None = None,
) -> FinancialModelResult:
    """Run the full model and return the `FinancialCase` plus supporting detail + provenance.

    ``lender_terms`` (WP-UW-03, D75) are a lender's stated terms. They size the debt and choose the
    case CFADS (P50 / P90 / contracted LENDER), the amortisation shape and the covenant tiers, and
    they ride onto ``FinancialCase.lender_terms``. They are an alternative to ``debt_terms`` (the
    legacy bare structure), never a supplement: passing both raises `ValueError`, because two debt
    structures on one run would leave the record unable to say which one the DSCR measured.

    ``commitments`` / ``contract_tenor_years`` are the D-33 / D-130 call path. Both fixes have been
    implemented and tested in `cashflows.build_cashflow_model` since the WP-0 batch, and until
    2026-08-13 this function passed no tenor at all — so on every real run the defect behaviour
    persisted (a terminable award earning for the full horizon in P90 and in the lender basis; a
    fixed availability payment shrunk by cell fade). The tenor is now assembled here, from:

      - ``contract_tenor_years`` — an explicit per-stream mapping (a CM agreement term, a DSO
        contract) supplied by a caller holding the record; wins over derived evidence, warns when
        it contradicts it;
      - ``commitments`` — typed `AvailabilityCommitment` payloads, read via their ``tenor_years``;
      - ``site.local_flex_awards`` — the site's own attested local-flex awards (D-130,
        2026-11-22), read here with no caller involvement and placed AHEAD of any caller-supplied
        ``commitments``. No production caller passed ``commitments=`` (the quote, the scenario
        sensitivities and the sizing loop's finance runner all omitted it), so a one-year award
        earned contracted revenue for the whole horizon. Reading it here, the one function every
        path reaches, means the sizing ranking and the priced quote see the same term;
        `tenors_from_commitments`' "shortest of differing terms, with a warning" rule applies
        across both sources;
      - ``site.provisioning_requirements`` — read with no caller involvement at all, so a site
        whose data declares a load-service term gets the term applied.

    No source ⇒ no tenor: the full-horizon projection stands and is warned as the live exposure it
    is. Nothing is inferred from market practice or from the assumption library.

    ``lifecycle`` (WP-UW-17) carries the stated construction programme, the CM agreement and the
    terminal / decommissioning / replacement flows; it rides onto ``FinancialCase.lifecycle_inputs``
    and the derived ``FinancialCase.lifecycle`` record says what the run did with each. A
    construction programme, or a scenario's ``cod_delay_months``, puts the headline IRRs on the
    month-dated timeline with interest during construction (D130); without either the
    annual COD-anchored path is unchanged and says that construction is excluded.

    Raises `InsufficientInputsError` when there is no positive sizing to price (the caller turns
    that into a ranked data ask / not-bankable quote rather than the model inventing a system).
    """
    if lender_terms is not None and debt_terms is not None:
        raise ValueError(
            "pass lender_terms OR debt_terms, not both: one run measures one debt structure"
        )
    warnings: list[str] = []
    # N25: only CALLER-INJECTED terms enter the case identity; credit-derived terms are already a
    # deterministic function of the site and library, which the hash input carries.
    injected_debt_terms = debt_terms

    # D-130: the site's own attested awards are contract evidence on EVERY call path (the quote, the
    # scenario sensitivities and the sizing loop's finance runner all arrive here), so the sizing
    # ranking and the priced quote can never disagree about how long an award runs.
    commitments = [*(site.local_flex_awards or ()), *(commitments or ())]
    tenor_evidence, tenor_warnings = resolve_contract_tenor_evidence(
        site, commitments=commitments, explicit=contract_tenor_years
    )
    warnings.extend(tenor_warnings)

    cashflows = build_cashflow_model(
        site,
        result,
        assumptions,
        horizon_years=horizon_years,
        scenario=scenario,
        dispatch_energy=dispatch_energy,
        contract_tenor_years=tenor_evidence or None,
        lifecycle=lifecycle,
    )
    warnings.extend(cashflows.warnings)
    construction = lifecycle.construction if lifecycle is not None else None
    dated = construction is not None or cashflows.cod_delay_months > 0
    if construction is None:
        # R7 (D132): stated on every run without a programme, delayed or not.
        warnings.append(construction_excluded_warning())
    if lifecycle is not None:
        warnings.append(
            "SIZE SELECTED WITHOUT LIFECYCLE FLOWS: the sizing loop that chose this system does "
            "not see the lifecycle inputs (construction, COD delay, CM agreement, terminal value, "
            "decommissioning, replacements); they are applied to the selected size only "
            "(D132 R6)."
        )

    # Counterparty credit -> rate/tenor (M9). Only when the caller did not inject debt terms: an
    # injected `debt_terms` always wins (so callers/tests keep full control). The deterministic
    # mapping sets rate/tenor from the credit band; gearing (Sim's portfolio finance) is absent in
    # v1, so DebtTerms stays None and DSCR unavailable — but rate/tenor still reach the case.
    credit_rt: CreditRateTenor | None = None
    covenant_tiers: CovenantTiers | None = None
    lender_refusal: str | None = None
    if lender_terms is not None:
        # WP-UW-03 (D75): supplied lender terms replace the credit mapping outright. The covenant
        # tiers come from the terms too (D76), never from the library placeholder.
        warnings.extend(lender_terms_warnings(lender_terms))
        covenant_tiers = covenant_tiers_from_lender_terms(lender_terms)
        lender_refusal = period_refusal_reason(lender_terms)
        if lender_refusal is None:
            debt_terms = debt_terms_from_lender_terms(lender_terms)
    elif debt_terms is None:
        credit_rt, debt_terms, credit_warnings = build_debt_terms_from_credit(
            site.counterparty.credit_status,
            assumptions,
            expected_tenure_years=site.counterparty.expected_tenure_years,
        )
        warnings.extend(credit_warnings)

    # Debt / DSCR on the case's operating cashflows (years 1..N, excluding the year-0 capex): P90
    # unless the lender terms name P50 or the contracted LENDER case. The contracted-basis series
    # is measured only when contracted revenue is actually priced (W8) — a merchant-only run has no
    # contracted basis to report, not a negative one.
    cashflow_case = (
        debt_terms.cashflow_case
        if debt_terms is not None
        else (lender_terms.cashflow_case.value if lender_terms is not None else CASE_P90)
    )
    funding: ConstructionFunding | None = None
    if dated:
        idc_rate, idc_source = _idc_rate(
            construction,
            lifecycle,
            debt_terms,
            lender_terms,
            injected=injected_debt_terms is not None,
            warnings=warnings,
        )
        funding = construction_funding(
            construction,
            delay_months=cashflows.cod_delay_months,
            rate_pct=idc_rate,
            rate_source=idc_source,
        )
    if lender_refusal is not None:
        dscr = DscrResult(
            available=False,
            basis=dscr_basis_for_case(cashflow_case),
            warnings=[lender_refusal],
            status=DscrStatus.PERIOD_NOT_MODELLED,
            unavailable_reason=lender_refusal,
        )
        assert covenant_tiers is not None
        covenant = check_covenant(
            None,
            covenant_tiers.default_dscr,
            lock_up=covenant_tiers.lock_up_dscr,
            source=covenant_tiers.source,
            absent_reason=covenant_tiers.absent_reason,
        )
    else:
        dscr, covenant = assess_debt(
            cashflows.capex.total_gbp,
            _case_cfads(cashflows, cashflow_case),
            assumptions,
            debt_terms=debt_terms,
            p90_contracted_cfads_by_year=(
                _case_cfads(cashflows, CASE_LENDER)
                if cashflows.annual_revenue_contracted_gbp > 0
                else None
            ),
            covenant_tiers=covenant_tiers,
            no_terms_reason=_no_terms_reason(site, credit_rt),
            idc_accumulation_factor=(funding.accumulation_factor if funding is not None else 1.0),
            floor_negative_sculpt=cashflows.lifecycle_cfads_by_year is not None,
        )
    warnings.extend(dscr.warnings)
    warnings.extend(covenant.warnings)

    # Returns on the P50 case (payback + project IRR).
    payback = simple_payback(cashflows.p50_fcf_pre_debt_gbp)
    irr = project_irr(cashflows.p50_fcf_pre_debt_gbp)
    warnings.extend(payback.warnings)
    warnings.extend(irr.warnings)

    # D47 (Olly, 2026-08-17): the ALWAYS-DEFINED return account, on the same P50 series the IRR is
    # measured on. `project_irr` refuses a series that is non-positive in every year — correctly, no
    # discount rate sets NPV to zero when capital is never returned — and on the whole 2026-08
    # campaign that refusal was every site's only return figure. A reader cannot tell a refused
    # metric from an absent measurement, so `irr=None` published as the sole return number reads as
    # "nothing was found" when the model had in fact measured the shortfall. This account carries
    # that shortfall as a figure, and the schema field is mandatory so it can never go missing.
    recovery = capital_recovery(cashflows.p50_fcf_pre_debt_gbp)
    if irr.irr_pct is None:
        # Surface it in the warnings too: the IRR's own refusal message says why there is no ratio,
        # and this says what the position IS instead, so the two are never read as one absence.
        warnings.append(f"RETURN ACCOUNT (no project IRR exists): {recovery.narrative}")

    # Levered (equity) IRR on the P50 case, net of the same debt service the P90 DSCR was measured
    # against — the headline equity return the sizing objective maximises. Unavailable (None) when
    # there is no debt (DSCR unavailable) or the equity series is non-conventional — never guessed.
    eq = equity_irr(
        cashflows.capex.total_gbp,
        dscr.principal_gbp,
        dscr.debt_service_by_year_gbp,
        cashflows.p50_fcf_pre_debt_gbp[1:],
    )
    warnings.extend(eq.warnings)

    # WP-UW-17 (D130): the dated headline. Construction draws before COD, operations from
    # the (possibly delayed) COD, IDC in the principal at COD; payback, the recovery account and
    # p50_cashflows stay annual and COD-anchored, and say so.
    headline_irr = irr.irr_pct
    headline_equity = eq.irr_pct if eq.available else None
    dated_project: list[DatedFlow] | None = None
    dated_equity: list[DatedFlow] | None = None
    levered = bool(dscr.principal_gbp) and bool(dscr.debt_service_by_year_gbp)
    capex_total = cashflows.capex.total_gbp
    operating_fcf = cashflows.p50_fcf_pre_debt_gbp[1:]
    if funding is not None:
        dated_project = dated_project_flows(capex_total, funding, operating_fcf)
        dated_result = dated_irr(dated_project)
        headline_irr = dated_result.irr_pct
        warnings.extend(dated_result.warnings)
        if levered:
            assert dscr.principal_gbp is not None
            dated_equity = dated_equity_flows(
                capex_total,
                funding,
                dscr.principal_gbp,
                dscr.debt_service_by_year_gbp,
                operating_fcf,
            )
            dated_eq_result = dated_irr(dated_equity)
            headline_equity = dated_eq_result.irr_pct
            warnings.extend(dated_eq_result.warnings)
        warnings.append(_dated_basis_warning(funding, construction, irr.irr_pct, headline_irr))

    # Price recommendation (computes a value only where inputs support it), grounded in the
    # customer's alternative: their current blended import cost per kWh — mean of the tariff's
    # import rates plus its £/kWh levy lines, a disclosed blended-rate approximation. N26
    # (2026-08-10 review): this comment used to assert that the parasitic-load cost line used "the
    # same convention"; it did not — it omitted the levies, understating that cost every year. Both
    # sites now call the ONE helper, `cashflows.blended_import_rate_gbp_per_kwh`, so the claim is
    # true by construction rather than by assertion. A price-to-target above the counterfactual is
    # flagged not-commercially-viable rather than presented bare (red-team fix, 2026-07-07).
    annual_opex = cashflows.total_annual_operating_cost_gbp
    price, price_warnings = recommend_price(
        price_structure,
        assumptions,
        capex_gbp=cashflows.capex.total_gbp,
        annual_opex_gbp=annual_opex,
        annual_delivered_energy_kwh=_delivered_energy_kwh(dispatch_energy),
        horizon_years=cashflows.horizon_years,
        counterfactual_import_gbp_per_kwh=_blended_import_rate(site),
        # N3: the project already earns the priced floor. Charging the customer the FULL capex
        # recovery on top would collect the same value twice and — because the inflated price is
        # then tested against the customer's current import cost — declare viable sites unviable.
        other_annual_revenue_gbp=cashflows.total_annual_revenue_gbp,
        # N12: the same run's measured equity IRR, so `hits_target_return` is a comparison rather
        # than a restatement of the target assumption's status. Computed above, off the same debt
        # service the DSCR was measured against — never recomputed here.
        achieved_equity_irr_pct=eq.irr_pct if eq.available else None,
        levelisation=_levelisation(cashflows, dscr, funding, levered=levered),
    )
    warnings.extend(price_warnings)

    debt_assumptions = _debt_assumptions(debt_terms, dscr, credit_rt, lender_terms)

    # The case is bankable only if ALL of: the cashflows are bankable (bankable-candidate result +
    # no non-confirmed cashflow assumption), DSCR is actually available, the covenant passes, and
    # the covenant assumption itself is confirmed. With gb_v0_1 (all placeholders) and v1's absent
    # debt structure this is always False — a placeholder-driven/incomplete run is never bankable.
    # With lender terms the covenant is the lender's, so it is firm only on a term sheet that
    # states one (synthetic and indicative terms never support a bankable verdict).
    covenant_unconfirmed = (
        _covenant_unconfirmed(assumptions)
        if lender_terms is None
        else not lender_terms_are_firm(lender_terms)
    )
    bankable = (
        cashflows.bankable
        and dscr.available
        and covenant.passes is True
        and not covenant_unconfirmed
    )
    if not bankable:
        warnings.append(
            "FinancialCase is NOT bankable (placeholder assumptions and/or unavailable debt/"
            "covenant). Do not present to capital as bankable."
        )

    run_context = build_run_context(
        _hash_input(
            site,
            result,
            assumptions,
            horizon_years,
            scenario,
            dispatch_energy,
            cashflows.contracted_tenor_years,
            debt_terms=injected_debt_terms,
            lender_terms=lender_terms,
            price_structure=price_structure,
            lifecycle=lifecycle,
        ),
        assumption_version=assumptions.metadata.version,
        schema_version=SCHEMA_VERSION,
        warnings=[],
    )

    case = FinancialCase(
        case_id=f"fc_{run_context.run_id}",
        p50_cashflows=cashflows.p50_fcf_pre_debt_gbp,
        p90_cashflows=cashflows.p90_fcf_pre_debt_gbp,
        dscr_summary=DscrSummary(
            min_dscr=dscr.min_dscr,
            by_year=list(dscr.by_year),
            basis=dscr.basis,
            # D75: every run states why its DSCR is or is not a number.
            status=dscr.status,
            unavailable_reason=None if dscr.available else dscr.unavailable_reason,
            cashflow_case=CashflowCase(cashflow_case),
        ),
        irr=headline_irr,
        equity_irr=headline_equity,
        payback_years=payback.years,
        capital_recovery=capital_recovery_account(cashflows.p50_fcf_pre_debt_gbp),
        debt_assumptions=debt_assumptions,
        price_recommendation=price,
        covenant_status=CovenantStatus(
            covenant=covenant.covenant,
            min_p90_dscr=covenant.min_dscr,
            passes=covenant.passes,
            source=covenant.source,
            lock_up_dscr=covenant.lock_up,
            lock_up_passes=covenant.lock_up_passes,
            not_evaluated_reason=covenant.not_evaluated_reason,
        ),
        warnings=_dedupe(warnings),
        lender_terms=lender_terms,
        lifecycle_inputs=lifecycle,
        lifecycle=_lifecycle_record(
            cashflows,
            dscr,
            funding,
            construction,
            cod_anchored_irr=irr.irr_pct,
            cod_anchored_equity_irr=eq.irr_pct if eq.available else None,
            dated_project=dated_project,
            dated_equity=dated_equity,
        ),
    )

    return FinancialModelResult(
        case=case,
        cashflows=cashflows,
        dscr=dscr,
        run_context=run_context,
        bankable=bankable,
        # Read through the SAME helper `_hash_input` uses, so the id this run was identified by and
        # the id it reports to the quote layer cannot drift apart.
        scenario_id=_scenario_id(scenario),
    )


def build_financial_case(
    site: SiteModel,
    result: ResultLike,
    assumptions: AssumptionLibrary,
    *,
    horizon_years: int | None = None,
    scenario: object | None = None,
    debt_terms: DebtTerms | None = None,
    dispatch_energy: DispatchEnergy | None = None,
    price_structure: PriceStructure = PriceStructure.FIXED_GBP_PER_KWH,
    commitments: Iterable[AvailabilityCommitment] | None = None,
    contract_tenor_years: Mapping[str, float] | None = None,
    lender_terms: LenderTerms | None = None,
    lifecycle: LifecycleInputs | None = None,
) -> FinancialCase:
    """Convenience wrapper returning just the schema `FinancialCase` (see `run_financial_model`)."""
    return run_financial_model(
        site,
        result,
        assumptions,
        horizon_years=horizon_years,
        scenario=scenario,
        debt_terms=debt_terms,
        dispatch_energy=dispatch_energy,
        price_structure=price_structure,
        commitments=commitments,
        contract_tenor_years=contract_tenor_years,
        lender_terms=lender_terms,
        lifecycle=lifecycle,
    ).case


def _case_cfads(cashflows: CashflowModel, case: str) -> list[float]:
    """The CFADS series (years 1..N) of a cash-flow case (WP-UW-03).

    P50 / P90 are the operating free cashflow before debt; LENDER is the conservative contracted P90
    series (contracted P90 revenue bearing every operating cost, W8), the case a lender lending
    against contracted revenue alone sizes on. On a merchant-dominant run that series is negative
    by construction, and the DSCR measured on it says so; nothing is reallocated to flatter it.

    WP-UW-17 (D131): with lifecycle flows stated, CFADS is the case EBITDA plus the
    decommissioning and replacement flows only — a lender never credits the terminal value, which
    rides in the FCF series (and so in the IRRs) but never here.
    """
    life = cashflows.lifecycle_cfads_by_year
    if life is not None:
        base = (
            cashflows.p50_ebitda_gbp
            if case == CASE_P50
            else (
                cashflows.p90_contracted_ebitda_gbp
                if case == CASE_LENDER
                else cashflows.p90_ebitda_gbp
            )
        )
        return [round(b + c, 2) for b, c in zip(base, life, strict=True)]
    if case == CASE_P50:
        return cashflows.p50_fcf_pre_debt_gbp[1:]
    if case == CASE_LENDER:
        return list(cashflows.p90_contracted_ebitda_gbp)
    return cashflows.p90_fcf_pre_debt_gbp[1:]


def _idc_rate(
    construction: object | None,
    lifecycle: LifecycleInputs | None,
    debt_terms: DebtTerms | None,
    lender_terms: LenderTerms | None,
    *,
    injected: bool,
    warnings: list[str],
) -> tuple[float | None, str]:
    """The construction-period rate and its label (D130, R4). Never invented.

    A stated ``construction.idc_rate_pct`` wins. Otherwise the term rate of the debt that was
    sized stands in, labelled with where that rate came from, with the "assumed equal to term
    rate" warning and its data request. No debt = no IDC (nothing is drawn).
    """
    stated = getattr(construction, "idc_rate_pct", None)
    if debt_terms is None:
        return None, "no debt sized: no interest during construction"
    if stated is not None and lifecycle is not None:
        source = f"construction programme ({lifecycle.provenance.source.value}: "
        source += f"{lifecycle.provenance.reference})"
        return float(stated), source
    if lender_terms is not None:
        label = (
            f"lender terms, {lender_terms.provenance.source.value}: "
            f"{lender_terms.provenance.reference}"
        )
    elif injected:
        label = "caller-injected DebtTerms, provenance unstated"
    else:
        label = "credit-band mapping from the assumption library"
    rate = debt_terms.annual_rate_pct
    warnings.append(
        f"IDC RATE ASSUMPTION (IDC rate assumed equal to term rate): no construction-period rate "
        f"was supplied; the lender's all-in term rate {rate:g}% ({label}) is applied to the drawn "
        "balance during construction. A construction facility usually prices differently; supply "
        "construction.idc_rate_pct. DATA REQUEST: the construction facility's rate."
    )
    return rate, f"term rate assumed ({label})"


def _dated_basis_warning(
    funding: ConstructionFunding,
    construction: object | None,
    annual_irr: float | None,
    dated_irr_pct: float | None,
) -> str:
    """The one RETURN BASIS line of a dated run (D130)."""
    months = getattr(construction, "construction_months", None)
    build = f"a {months}-month construction programme" if months else "no construction programme"

    def _pct(v: float | None) -> str:
        return "unavailable" if v is None else f"{v:.2f}%"

    return (
        f"RETURN BASIS — DATED MONTHLY (D130): the headline project and equity IRRs are "
        f"measured on a month-dated timeline with {build} and a COD delay of "
        f"{funding.delay_months} month(s); debt is drawn pro rata with capex and interest during "
        "construction is rolled into the principal at COD (factor "
        f"{funding.accumulation_factor:.6f}, rate {funding.idc_rate_source}). Payback, the "
        "capital-recovery account and p50_cashflows stay on the annual COD-anchored series "
        f"(project IRR {_pct(annual_irr)} on that basis; {_pct(dated_irr_pct)} dated)."
    )


def _levelisation(
    cashflows: CashflowModel,
    dscr: DscrResult,
    funding: ConstructionFunding | None,
    *,
    levered: bool,
) -> Levelisation | None:
    """The price-to-target inputs of this run (D128; R3), or ``None`` when the delivered
    volume is unknown (the price then reports "volume not yet known")."""
    energy = cashflows.delivered_energy_kwh_by_year
    if energy is None or sum(energy) <= 0:
        return None
    capex = cashflows.capex.total_gbp
    fcf = cashflows.p50_fcf_pre_debt_gbp
    operating = fcf[1:]
    principal = dscr.principal_gbp or 0.0
    service = list(dscr.debt_service_by_year_gbp)

    def _plus(extra: list[float] | tuple[float, ...] | object) -> list[float]:
        assert isinstance(extra, (list, tuple))
        return [v + x for v, x in zip(operating, extra, strict=True)]

    if funding is None:
        priced: list[DatedFlow] = [(12 * t, v) for t, v in enumerate(fcf)]
        months = [12 * t for t in range(1, len(fcf))]

        def _measure(extra: object) -> float | None:
            if levered:
                return equity_irr(capex, principal, service, _plus(extra)).irr_pct
            return project_irr([fcf[0], *_plus(extra)]).irr_pct

        basis = "annual"
    else:
        priced = dated_project_flows(capex, funding, operating)
        months = [operating_month(t, funding.delay_months) for t in range(1, len(fcf))]

        def _measure(extra: object) -> float | None:
            assert isinstance(extra, (list, tuple))
            extra_list = [float(x) for x in extra]
            if levered:
                flows = dated_equity_flows(
                    capex, funding, principal, service, operating, extra_by_year=extra_list
                )
            else:
                flows = dated_project_flows(capex, funding, operating, extra_by_year=extra_list)
            return dated_irr(flows).irr_pct

        basis = "dated"
    return Levelisation(
        basis=basis,
        priced_flows=priced,
        energy_kwh_by_year=energy[1:],
        operating_months=months,
        measure_irr=_measure,
        measured="equity" if levered else "project",
    )


def _dated_entries(flows: list[DatedFlow] | None) -> list[DatedFlowEntry] | None:
    if flows is None:
        return None
    return [DatedFlowEntry(month=m, gbp=round(v, 2)) for m, v in consolidate_dated_flows(flows)]


def _lifecycle_record(
    cashflows: CashflowModel,
    dscr: DscrResult,
    funding: ConstructionFunding | None,
    construction: object | None,
    *,
    cod_anchored_irr: float | None,
    cod_anchored_equity_irr: float | None,
    dated_project: list[DatedFlow] | None,
    dated_equity: list[DatedFlow] | None,
) -> LifecycleRecord:
    """The always-populated derived record of what the run did (D129/D130/D131)."""
    real_fcf = cashflows.real_p50_fcf_gbp
    real_irr = cashflows.real_project_irr_pct
    real_basis: str | None = None
    if cashflows.escalation_by_year is not None:
        real_basis = ReturnBasis.ANNUAL_COD_ANCHORED.value
        if dated_project is not None and funding is not None:
            # Missed point 3: deflator 1.0 through construction, (1 + c)^(t-1) in operating year t.
            esc = cashflows.escalation_by_year
            real_dated = [
                (
                    m,
                    v
                    if m <= funding.delay_months
                    else v / esc[(m - funding.delay_months) // 12 - 1],
                )
                for m, v in dated_project
            ]
            real_irr = dated_irr(real_dated).irr_pct
            real_basis = ReturnBasis.DATED_MONTHLY.value
    cm = cashflows.cm_indexation
    indexation = IndexationRecord(
        cashflow_basis=cashflows.cashflow_basis,
        cpi_state=cashflows.cpi_state,  # type: ignore[arg-type]
        cpi_pct_per_year=cashflows.cpi_pct_per_year,
        line_basis=[LineBasisEntry(line=k, basis=v) for k, v in cashflows.line_basis],  # type: ignore[arg-type]
        real_p50_fcf_gbp=real_fcf,
        real_project_irr_pct=real_irr,
        real_return_basis=real_basis,
        cm_auction_type=cm.auction_type if cm is not None else None,
        cm_year1_uplift=cm.year1_uplift if cm is not None else None,
        cm_uplift_source=cm.uplift_source if cm is not None else None,
        cm_base_period_label=cm.base_period_label if cm is not None else None,
    )
    principal = dscr.principal_gbp if dscr.principal_gbp else None
    a = funding.accumulation_factor if funding is not None else None
    idc = (
        round(principal - principal / a, 2)
        if principal is not None
        and a is not None
        and funding is not None
        and funding.idc_rate_pct is not None
        else None
    )
    construction_record = ConstructionRecord(
        status=(
            ConstructionStatus.MODELLED
            if construction is not None
            else ConstructionStatus.EXCLUDED_NO_INPUT
        ),
        return_basis=(
            ReturnBasis.DATED_MONTHLY if funding is not None else ReturnBasis.ANNUAL_COD_ANCHORED
        ),
        construction_months=getattr(construction, "construction_months", None),
        cod_delay_months=cashflows.cod_delay_months,
        accumulation_factor=a,
        idc_gbp=idc,
        idc_rate_pct=funding.idc_rate_pct if funding is not None else None,
        idc_rate_source=funding.idc_rate_source if funding is not None else None,
        principal_at_cod_gbp=principal,
        cod_anchored_irr_pct=cod_anchored_irr if funding is not None else None,
        cod_anchored_equity_irr_pct=cod_anchored_equity_irr if funding is not None else None,
        dated_p50_project_flows=_dated_entries(dated_project),
        dated_p50_equity_flows=_dated_entries(dated_equity),
    )
    flows = list(cashflows.lifecycle_flow_entries) or list(excluded_ledger())
    return LifecycleRecord(indexation=indexation, construction=construction_record, flows=flows)


def _no_terms_reason(site: SiteModel, credit_rt: CreditRateTenor | None) -> str:
    """The reader-facing reason a run has no DSCR because no debt terms reached it (B001, D75)."""
    band = site.counterparty.credit_status.value
    if credit_rt is None:
        return (
            f"{NO_DEBT_TERMS_REASON} The counterparty credit band is '{band}', so the credit "
            "mapping supplies no rate or tenor either."
        )
    return (
        f"{NO_DEBT_TERMS_REASON} The '{band}' credit band maps a rate and tenor, but no gearing "
        "is set, so no loan can be sized from it."
    )


def _blended_import_rate(site: SiteModel) -> float | None:
    """The customer's blended import energy cost (£/kWh) — delegates to the ONE shared helper.

    N26: this used to be a second, divergent copy of the calculation (the copy in
    `cashflows.build_cost_lines` omitted the levy lines). It is now a thin alias over
    `cashflows.blended_import_rate_gbp_per_kwh` so the counterfactual and the parasitic-load cost
    line cannot drift apart again.
    """
    return blended_import_rate_gbp_per_kwh(site)


def _covenant_unconfirmed(assumptions: AssumptionLibrary) -> bool:
    """True if the P90-DSCR covenant assumption is absent or not ``confirmed``.

    A bankable verdict cannot rest on a placeholder covenant; injected `DebtTerms` are the
    caller's responsibility (the M9 mapping path), but the covenant always comes from the library.
    """
    sect = getattr(assumptions, "dscr_covenants", None)
    if not isinstance(sect, dict):
        return True
    a = sect.get("min_p90_dscr")
    return a is None or a.status != AssumptionStatus.CONFIRMED


def _delivered_energy_kwh(dispatch_energy: DispatchEnergy | None) -> float | None:
    """Delivered energy for pricing = annual discharge (what the customer is served), if known."""
    if dispatch_energy is None:
        return None
    return dispatch_energy.annual_discharge_kwh


def _debt_assumptions(
    debt_terms: DebtTerms | None,
    dscr: DscrResult,
    credit_rt: CreditRateTenor | None = None,
    lender_terms: LenderTerms | None = None,
) -> DebtAssumptions:
    """Mirror the debt terms into the schema model.

    With full ``DebtTerms`` (gearing + credit rate/tenor) all fields populate. When gearing is
    absent but the credit mapping resolved a rate/tenor, those still flow through (gearing
    ``None``) so the M9 mapping is visible on the case even before Sim's gearing lands. All-``None``
    only when neither is available.

    ``sculpting_basis`` records the debt-service shape that was **actually applied** (from the
    `DscrResult`) so a requested P90-DSCR sculpt that degraded to the annuity fallback is recorded
    as the annuity — the record never claims a sculpt that did not happen. When no schedule was
    built at all, the requested basis on the terms is recorded (nothing was shaped either way).
    """
    if debt_terms is None:
        if lender_terms is not None:
            # Terms were supplied but not sized on (an unmodelled test period): the figures are
            # still the lender's and still shown; nothing was shaped, so no basis is claimed.
            return DebtAssumptions(
                gearing_pct=lender_terms.gearing_pct,
                rate_pct=lender_terms.interest_rate_pct,
                tenor_years=float(lender_terms.tenor_years),
                sculpting_basis=None,
            )
        if credit_rt is not None:
            return DebtAssumptions(
                gearing_pct=None,
                rate_pct=credit_rt.rate_pct,
                tenor_years=float(credit_rt.tenor_years),
                sculpting_basis=None,
            )
        return DebtAssumptions(
            gearing_pct=None, rate_pct=None, tenor_years=None, sculpting_basis=None
        )
    applied_basis = (
        dscr.sculpting_basis if dscr.sculpting_basis is not None else debt_terms.sculpting_basis
    )
    return DebtAssumptions(
        gearing_pct=debt_terms.gearing_pct,
        rate_pct=debt_terms.annual_rate_pct,
        tenor_years=float(debt_terms.tenor_years),
        sculpting_basis=applied_basis,
        principal_gbp=dscr.principal_gbp if dscr.principal_gbp else None,
        sized_by=dscr.sized_by if dscr.principal_gbp else None,
    )


def _hash_input(
    site: SiteModel,
    result: ResultLike,
    assumptions: AssumptionLibrary,
    horizon_years: int | None,
    scenario: object | None,
    dispatch_energy: DispatchEnergy | None,
    contracted_tenor_years: tuple[tuple[str, float], ...] = (),
    *,
    debt_terms: DebtTerms | None = None,
    lender_terms: LenderTerms | None = None,
    price_structure: PriceStructure = PriceStructure.FIXED_GBP_PER_KWH,
    lifecycle: LifecycleInputs | None = None,
) -> dict:
    """Canonical, timestamp-free hash input for the run context (reproducibility pin).

    ``created_at`` is excluded from both models — per the data contract, timestamps are metadata
    and must never feed a computation.

    ``contracted_tenor_years`` is the tenor treatment that was **actually applied** (read back off
    the built `CashflowModel`, not the raw argument). Without it, two runs whose P90 series and
    lender basis differ only by a contract term would share a `case_id` — defect N25/MD-F-14, a
    fixed identity over a moved number, which is worse than the number never having moved. Reading
    the applied tuple rather than the request is deliberate: a tenor that was supplied and correctly
    DROPPED as unused changed no arithmetic and must not split case identity.

    The key is omitted entirely when no tenor was applied, so an evidence-free run keeps the exact
    hash input it had before this parameter existed. That is not cosmetic: it is what lets the
    change land without silently re-identifying every historical run, while any run that actually
    tenors revenue gets a distinct `case_id` from one that does not (present vs absent key, then
    distinct values).

    **N25 (WP-UW-03, D78): the debt structure and the price structure are inputs too.** Before
    this, two runs at 60% and 40% gearing (or on a fixed price and a capacity fee) differed in
    DSCR, equity IRR and price yet shared one ``case_id``. Caller-injected ``debt_terms``, supplied
    ``lender_terms`` and a non-default ``price_structure`` now enter the payload, each under the
    same omit-when-absent rule, so every run that supplied none keeps its exact historical hash
    input.
    """
    payload: dict[str, object] = {
        "site": _dump(site),
        "result": _dump(result),
        "assumption_version": assumptions.metadata.version,
        "horizon_years": horizon_years if horizon_years is not None else DEFAULT_HORIZON_YEARS,
        "scenario_id": _scenario_id(scenario),
        "price_multiplier": getattr(scenario, "price_multiplier", None),
        "cost_multiplier": getattr(scenario, "cost_multiplier", None),
        "dispatch_charge_kwh": getattr(dispatch_energy, "annual_charge_kwh", None),
        "dispatch_discharge_kwh": getattr(dispatch_energy, "annual_discharge_kwh", None),
    }
    if contracted_tenor_years:
        payload["contract_tenor_years"] = [[s, t] for s, t in contracted_tenor_years]
    if debt_terms is not None:
        payload["debt_terms"] = asdict(debt_terms)
    if lender_terms is not None:
        payload["lender_terms"] = lender_terms.model_dump(mode="json")
    if price_structure != PriceStructure.FIXED_GBP_PER_KWH:
        payload["price_structure"] = price_structure.value
    # WP-UW-17 (D130): the stated lifecycle inputs, and a scenario's COD delay, move every
    # dated figure, so both enter the identity — under the same omit-when-absent rule.
    if lifecycle is not None:
        payload["lifecycle"] = lifecycle.model_dump(mode="json")
    cod_delay = int(getattr(scenario, "cod_delay_months", 0) or 0) if scenario is not None else 0
    if cod_delay:
        payload["cod_delay_months"] = cod_delay
    return payload


def _dump(obj: object) -> object:
    """Dump a pydantic model to a timestamp-free JSON dict; pass other objects through."""
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude={"created_at"})
    return repr(obj)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
