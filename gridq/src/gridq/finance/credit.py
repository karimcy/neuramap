"""Counterparty credit -> rate/tenor mapping (M9 — the deterministic mapping only).

The System Brief places counterparty credit inside GridQ as perimeter module M9: an internal
credit score + memo (Creditsafe API + research agent, later) that maps **deterministically** to
the discount rate and tenor used in the financial model. v1 builds *only* that deterministic
mapping — the credit-band -> (rate, tenor) lookup — and consumes a manual/stub ``CreditStatus``
band as its input. No external API is called and no credit band is invented (B001 / boundary
Open Q2).

**Seam for the future Creditsafe-backed tool (B001).** That tool's job is to produce a
``CreditStatus`` band for a counterparty (a separate, future ``score -> band`` mapping — its own
versioned policy, not built here). It plugs in *upstream* of this module by populating
``SiteModel.counterparty.credit_status``; this ``band -> rate/tenor`` function is unchanged. The
input boundary is therefore ``CreditStatus``: nothing here needs to know whether the band came
from a human or from Creditsafe.

**Determinism.** The mapping is a pure function of ``(credit_status, assumption library)``. The
rate/tenor numbers live in the versioned assumption library
(``financing.credit_rate_tenor_policy``) — no private constants — and are rounded at parse time so
identical inputs reproduce identical ``DebtTerms`` (and an identical run-context hash). The
rate/tenor *values* are placeholder credit policy (Sim's) until confirmed, so a credit-driven
case is never bankable on them. A worse credit band must never buy a cheaper or longer loan
(monotonicity is enforced below — spec M9: "no glowing return at a rate the covenant doesn't
justify").
"""

from __future__ import annotations

from dataclasses import dataclass

from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus
from gridq.finance.debt import DebtTerms
from gridq.schemas.enums import CreditStatus

POLICY_KEY = ("financing", "credit_rate_tenor_policy")
# Gearing is portfolio finance (Sim's), NOT credit data — read here only to *assemble* DebtTerms
# when present (absent in gb_v0_1). It is never written by this module.
GEARING_KEY = ("financing", "debt_gearing_pct")

# Credit bands that map to a debt structure, ordered best -> worst. UNKNOWN / NOT_ASSESSED are
# deliberately excluded: an unassessed counterparty has no rate/tenor and is never defaulted to the
# worst band (that would fabricate a credit assessment). They become a ranked data ask instead.
_MAPPABLE: tuple[CreditStatus, ...] = (
    CreditStatus.STRONG,
    CreditStatus.ADEQUATE,
    CreditStatus.WEAK,
)


@dataclass(frozen=True)
class CreditRateTenor:
    """A credit band's deterministic debt rate + tenor (the M9 mapping output).

    ``policy_status`` carries the assumption status so callers can see the numbers are placeholder
    credit policy (not bankable) until confirmed.
    """

    credit_status: CreditStatus
    rate_pct: float
    tenor_years: int
    policy_status: AssumptionStatus
    source_ref: str = "financing.credit_rate_tenor_policy"


def _maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    sect = getattr(assumptions, section, None)
    if not isinstance(sect, dict):
        return None
    return sect.get(name)


def _band_value(entry: object) -> tuple[float, int] | None:
    """Coerce one band entry ``{rate_pct, tenor_years}`` to a rounded ``(rate_pct, tenor_years)``.

    Returns ``None`` (the caller warns) when the entry is structurally wrong or out of range.
    Nothing is substituted — a malformed table yields *unavailable*, never a guessed number.
    """
    if not isinstance(entry, dict):
        return None
    rate = entry.get("rate_pct")
    tenor = entry.get("tenor_years")
    if isinstance(rate, bool) or isinstance(tenor, bool):
        return None
    if not isinstance(rate, (int, float)) or not isinstance(tenor, (int, float)):
        return None
    rate_pct = round(float(rate), 4)
    tenor_years = int(round(float(tenor)))
    if rate_pct < 0 or tenor_years <= 0:
        return None
    return rate_pct, tenor_years


def _check_monotonic(parsed: dict[CreditStatus, tuple[float, int]]) -> list[str]:
    """A worse band must have a rate that is non-decreasing and a tenor that is non-increasing."""
    present = [band for band in _MAPPABLE if band in parsed]
    warnings: list[str] = []
    for better, worse in zip(present, present[1:], strict=False):
        rate_better, tenor_better = parsed[better]
        rate_worse, tenor_worse = parsed[worse]
        if rate_worse < rate_better:
            warnings.append(
                f"credit policy non-monotonic: {worse.value} rate ({rate_worse}%) < "
                f"{better.value} rate ({rate_better}%) — worse credit must not be cheaper."
            )
        if tenor_worse > tenor_better:
            warnings.append(
                f"credit policy non-monotonic: {worse.value} tenor ({tenor_worse}y) > "
                f"{better.value} tenor ({tenor_better}y) — worse credit must not be longer."
            )
    return warnings


def _parse_policy(policy: Assumption) -> tuple[dict[CreditStatus, tuple[float, int]], list[str]]:
    """Parse + validate the policy table. Returns ``(mapping, warnings)``; empty ⇒ unusable.

    A non-monotonic table is a credit-policy error: this refuses to map on it (returns an empty
    mapping) rather than emit a glowing return at a rate the covenant doesn't justify.
    """
    table = policy.value
    if not isinstance(table, dict):
        return {}, [
            f"{'.'.join(POLICY_KEY)} is malformed (expected a per-band mapping); "
            "credit rate/tenor unavailable — not guessed."
        ]
    parsed: dict[CreditStatus, tuple[float, int]] = {}
    warnings: list[str] = []
    for band in _MAPPABLE:
        entry = table.get(band.value)
        if entry is None:
            continue
        coerced = _band_value(entry)
        if coerced is None:
            warnings.append(
                f"{'.'.join(POLICY_KEY)}.{band.value} is malformed "
                "(need numeric rate_pct >= 0 and tenor_years > 0); band skipped."
            )
            continue
        parsed[band] = coerced
    mono_warnings = _check_monotonic(parsed)
    if mono_warnings:
        return {}, warnings + mono_warnings
    return parsed, warnings


def map_credit_to_rate_tenor(
    credit_status: CreditStatus,
    assumptions: AssumptionLibrary,
) -> tuple[CreditRateTenor | None, list[str]]:
    """Map a counterparty's coarse credit band to a deterministic debt ``(rate_pct, tenor_years)``.

    Returns ``(CreditRateTenor, warnings)`` for a mappable, assessed band, or ``(None, warnings)``
    when the band is unassessed or the policy is absent/invalid. Never invents a band or a number.
    """
    if credit_status not in _MAPPABLE:
        return None, [
            f"Credit status '{credit_status.value}' is not assessed — rate/tenor not set "
            "(never defaulted to a band). Supply a credit assessment (strong/adequate/weak)."
        ]
    policy = _maybe(assumptions, *POLICY_KEY)
    if policy is None:
        return None, [
            f"No '{'.'.join(POLICY_KEY)}' in the assumption library — the credit->rate/tenor "
            "mapping table is absent; rate/tenor unavailable (not guessed)."
        ]
    parsed, warnings = _parse_policy(policy)
    band = parsed.get(credit_status)
    if band is None:
        return None, [
            *warnings,
            f"Credit band '{credit_status.value}' has no usable entry in "
            f"{'.'.join(POLICY_KEY)}; rate/tenor unavailable.",
        ]
    rate_pct, tenor_years = band
    if policy.status != AssumptionStatus.CONFIRMED:
        warnings.append(
            f"Credit rate/tenor policy '{'.'.join(POLICY_KEY)}' is '{policy.status.value}' — the "
            "mapped rate/tenor are indicative credit policy, not bankable, until confirmed."
        )
    return (
        CreditRateTenor(
            credit_status=credit_status,
            rate_pct=rate_pct,
            tenor_years=tenor_years,
            policy_status=policy.status,
        ),
        warnings,
    )


def build_debt_terms_from_credit(
    credit_status: CreditStatus,
    assumptions: AssumptionLibrary,
    *,
    expected_tenure_years: float | None = None,
    gearing_override: float | None = None,
) -> tuple[CreditRateTenor | None, DebtTerms | None, list[str]]:
    """Derive ``DebtTerms`` from a credit band: rate/tenor from the M9 mapping + gearing.

    Gearing is **portfolio finance (Sim's)**, not credit data — it comes from the library
    (``financing.debt_gearing_pct``, absent in v1) or an explicit ``gearing_override``, and is
    never invented here. When gearing is absent the ``CreditRateTenor`` is still returned (so
    rate/tenor flow to finance), but ``DebtTerms`` is ``None`` and DSCR stays unavailable.
    """
    crt, warnings = map_credit_to_rate_tenor(credit_status, assumptions)
    if crt is None:
        return None, None, warnings

    # The counterparty's expected tenure (offtake term) is a different concept from the debt tenor.
    # Flag — but do not silently truncate — debt sized longer than the contracted offtake (a real
    # lender concern). Auto-capping is a financing-policy decision, deferred until library-sourced.
    if expected_tenure_years is not None and expected_tenure_years < crt.tenor_years:
        warnings.append(
            f"Debt tenor ({crt.tenor_years}y) from the {credit_status.value} credit band exceeds "
            f"the counterparty's expected tenure ({expected_tenure_years:g}y) — review; debt "
            "should not outrun the contracted offtake. Not auto-capped in v1."
        )

    gearing = gearing_override
    if gearing is None:
        g_assumption = _maybe(assumptions, *GEARING_KEY)
        if g_assumption is not None:
            value = g_assumption.value
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                gearing = float(value)
                if g_assumption.status != AssumptionStatus.CONFIRMED:
                    warnings.append(
                        f"Gearing '{'.'.join(GEARING_KEY)}' is '{g_assumption.status.value}' — "
                        "debt sizing is indicative, not bankable, until confirmed."
                    )
    if gearing is None:
        warnings.append(
            f"Debt gearing absent ('{'.'.join(GEARING_KEY)}' — Sim's portfolio finance, B001); "
            "full DebtTerms not assembled and DSCR unavailable. Credit rate/tenor are still set."
        )
        return crt, None, warnings
    if not 0 <= gearing <= 100:
        warnings.append(
            f"Gearing {gearing} is outside [0, 100]; DebtTerms not assembled (credit rate/tenor "
            "are still set)."
        )
        return crt, None, warnings

    terms = DebtTerms(
        gearing_pct=gearing,
        annual_rate_pct=crt.rate_pct,
        tenor_years=crt.tenor_years,
    )
    return crt, terms, warnings
