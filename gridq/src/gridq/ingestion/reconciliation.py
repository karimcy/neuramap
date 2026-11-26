"""Declared-vs-measured reconciliation (validation Gate 3) — hostile review F-10 / F-26.

A `SiteModel` *declares* what its load is (`load_data.annual_kwh`, `max_import_kw`,
`half_hourly_rows`). A separate half-hourly file is what the solver actually prices. Nothing
previously compared the two, so the two halves of an underwriting case could describe different
sites, different windows or different vintages, and the result was still presented as coherent:

- battery kW/kWh, capex and Capacity Market revenue scale off the **declared** peak;
- every revenue line comes from the **measured** file.

A stale or mistyped declared peak therefore produced a capex-to-revenue mismatch at full
confidence, with nothing in the pipeline able to notice.

This is not hypothetical. The Grundon analysis carried a **four-month** meter sum as an annual
figure and understated one site's load by 13.8%
(`docs/grundon_harwell/02_data_reconciliation.md`). A reconciliation gate catches exactly that
shape of error — a declared annual total that the measured series cannot support.

The tolerances below are **data-integrity thresholds, not pricing assumptions** — they carry no
money and belong here rather than in the assumption library, matching the existing precedent in
`data_tier.py`.

**D44 (Olly, 2026-08-12): a breach records and continues, it never blocks.** A threshold breach
records the discrepancy and the basis chosen (`ReconciliationResult.basis_chosen`,
`reconciliation_records`), caps the confidence band at LOW, and the run proceeds — on the *same*
numbers it would otherwise have used. Recording a divergence is not licence to resolve it: the gate
never prefers, averages or re-scales one side onto the other.

Wired at `gridq.quotes.pipeline.generate_quote`, the single choke point every quote passes through.
It was previously called from nowhere in `src/` (contradiction C-6), so a gate the validation
requirements call mandatory was unenforced on every production run.

---

**Gate 3b — the BILL reconciliation (Olly, 2026-08-13).** `reconcile_bill_against_invoice` below.

Gate 3 above compares a *declared* load to a *measured* load: both sides come from the customer's
own data, so it can only catch a bookkeeping mismatch. Invoiced **cost** is the one quantity in the
evidence base the model has never consumed, so comparing the modelled bill to a real invoice tests
the whole chain end to end — load shape x tariff stack x DUoS bands x levies x fixed lines —
against a number that cannot have been fitted. `bill_reconciliation.md` performed that comparison
for the first time and found **no Grundon site within +-7%**, on a pipeline where all five run
records carried `"customer_bill": null` and nothing in `src/` had ever made the comparison.

Two properties of this gate are load-bearing and must not be relaxed:

1. **Per site, never portfolio.** The Grundon portfolio total agrees to **+0.7%** while the five
   individual sites range from **−25.9% to +11.7%** — two ~£29k undershoots on the grid sites
   cancelling a +£28k and a +£41k overshoot on the private-wire sites. A portfolio-level check would
   have reported that as a successful validation. Aggregating this gate destroys it.

2. **The cost tolerance is asymmetric.** Undershoot is wide (WARN −8%, ERROR −30%) because the
   stack's omissions are documented, quantified and all in the low direction — a collapsed
   multi-MPAN fixed-line structure is worth −24.5% at Bishops Cleeve alone, and understating the
   customer's bill understates avoided cost, which is conservative for underwriting. Overshoot is
   tight (WARN +2%, ERROR +5%) because **there is no conservative reading of exceeding a real
   invoice**: it overstates avoided-cost value directly. A symmetric gate would fail three grid
   sites for reasons already itemised (training its reader to ignore it) while passing the one site
   that priced 139,172 kWh of the customer's own solar as purchased energy.

Both limbs record and continue per **D45**, exactly like Gate 3: a breach records the discrepancy
and the basis, caps the band, and never blocks. The gate also never re-bases the model onto the
invoice — scaling a modelled bill to match an invoice would destroy the only independent check
there is.

**An absent invoice is its own state, never a pass.** `InvoiceEvidence.invoice_available=False`
produces an `absent` finding and a LOW ceiling, and `reconciles` returns `None` rather than `True`.
Harwell has no invoice, tariff or cost data of any kind; reading that silence as "reconciled" is
precisely the null-as-zero failure the D45 amendment was written to stop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from gridq.ingestion.hh_data import HalfHourlySummary
from gridq.schemas import ConfidenceBand, DerivedAssumption, SiteModel

__all__ = [
    "ANNUAL_KWH_ERROR_PCT",
    "ANNUAL_KWH_WARN_PCT",
    "BILL_COST_OVERSHOOT_ERROR_PCT",
    "BILL_COST_OVERSHOOT_WARN_PCT",
    "BILL_COST_UNDERSHOOT_ERROR_PCT",
    "BILL_COST_UNDERSHOOT_WARN_PCT",
    "BILL_ITEMISATION_MIN_SHARE_PCT",
    "BILL_MONTHLY_ERROR_PCT",
    "BILL_MONTHLY_FIXED_SHARE_SUPPRESSION_PCT",
    "BILL_MONTHLY_MAX_WARNED_MONTHS",
    "BILL_MONTHLY_WARN_PCT",
    "BILL_VOLUME_ERROR_PCT",
    "BILL_VOLUME_WARN_PCT",
    "PEAK_KW_ERROR_PCT",
    "PEAK_KW_WARN_PCT",
    "BillFinding",
    "BillReconciliationResult",
    "DocumentedOmission",
    "InvoiceEvidence",
    "ReconciliationFinding",
    "ReconciliationResult",
    "bill_reconciliation_records",
    "invoice_evidence_from_mapping",
    "reconcile_bill_against_invoice",
    "reconcile_declared_against_measured",
    "reconciliation_records",
]

#: Identity stamped on the records this gate writes (`gridq.schemas.derived`).
_DERIVED_BY = "gridq.ingestion.reconciliation"

#: Declared vs measured annual energy may differ by this much before it is an ERROR. Meter reads,
#: billing periods and estimated months move totals by a few percent legitimately; a tenth of the
#: year's energy going missing is a different kind of event.
ANNUAL_KWH_WARN_PCT = 5.0
ANNUAL_KWH_ERROR_PCT = 15.0

#: Declared vs measured peak. The declared peak drives sizing and capex; the measured peak drives
#: revenue. They must describe the same site.
PEAK_KW_WARN_PCT = 10.0
PEAK_KW_ERROR_PCT = 25.0


@dataclass(frozen=True)
class ReconciliationFinding:
    """One declared-vs-measured comparison."""

    field: str
    declared: float | None
    measured: float | None
    divergence_pct: float | None
    severity: str  # "ok" | "warning" | "error"
    message: str


@dataclass(frozen=True)
class ReconciliationResult:
    """The verdict of Gate 3 for one site."""

    findings: tuple[ReconciliationFinding, ...]

    @property
    def reconciles(self) -> bool:
        """True when nothing rose to ERROR. WARNINGs cap the confidence band, they do not block."""
        return not any(f.severity == "error" for f in self.findings)

    @property
    def errors(self) -> tuple[ReconciliationFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[ReconciliationFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    def summary(self) -> str:
        if self.reconciles and not self.warnings:
            return "Load basis reconciles against the measured half-hourly series."
        parts = [f"{f.field}: {f.message}" for f in self.findings if f.severity != "ok"]
        return " | ".join(parts)

    @property
    def basis_chosen(self) -> str:
        """What the run actually priced on, stated plainly (D44: record the basis, don't switch it).

        Record-and-continue means exactly that: a breach does not silently re-base the run onto the
        other figure — averaging or preferring one side is what
        `docs/validation_requirements.md` §3 forbids. The run proceeds unchanged and the divergence
        is reported, so the reader can see which half of the case rests on which number.
        """
        if self.reconciles and not self.warnings:
            return (
                "Sizing and capex rest on the declared load figures; revenue rests on the measured "
                "half-hourly series. The two agree within tolerance."
            )
        return (
            "Basis chosen: UNCHANGED — sizing and capex still rest on the DECLARED load figures "
            "and revenue on the MEASURED half-hourly series. The divergence is recorded, not "
            "resolved: neither figure was preferred, blended or scaled to the other."
        )


def reconciliation_records(
    result: ReconciliationResult, *, release: str | None = None
) -> tuple[DerivedAssumption, ...]:
    """Turn each ERROR finding into a recorded assumption requiring confirmation (D44).

    Gate 3 no longer blocks a run: a threshold breach records the discrepancy and the basis chosen,
    caps the confidence band, and the run continues. Each record carries **no value** — the gate
    derives nothing and must never be readable as having chosen a number on the customer's behalf.
    WARNING-level divergences are reported through the band (they cap it at MEDIUM), not as records:
    a few percent between a meter read and an invoice is normal, and a record implies an open ask.
    """
    records: list[DerivedAssumption] = []
    for finding in result.errors:
        declared = "unknown" if finding.declared is None else f"{finding.declared:,.1f}"
        measured = "unknown" if finding.measured is None else f"{finding.measured:,.1f}"
        pct = "" if finding.divergence_pct is None else f" ({finding.divergence_pct:.1f}% apart)"
        records.append(
            DerivedAssumption(
                kind="derived_input",
                key=f"load_data.{finding.field}",
                missing_input=f"a reconciled {finding.field} — declared and measured disagree",
                value=None,
                unit=None,
                basis=f"Declared {declared} vs measured {measured}{pct}, beyond the Gate-3 "
                f"tolerance. {result.basis_chosen}",
                sources=[],
                derivation_method="not_derivable",
                derived_by=_DERIVED_BY,
                derived_on_release=release,
                confidence_ceiling=ConfidenceBand.LOW,
                confirmation_ask=f"Confirm which {finding.field} figure describes this site and "
                "period (and, if the declared figure is a part-year or wrong-meter total, supply "
                "the correct one), so sizing and revenue describe the same load.",
            )
        )
    return tuple(records)


def _divergence_pct(declared: float, measured: float) -> float | None:
    if measured == 0:
        return None
    return abs(declared - measured) / abs(measured) * 100.0


def _compare(
    field: str,
    declared: float | None,
    measured: float | None,
    *,
    warn_pct: float,
    error_pct: float,
    units: str,
) -> ReconciliationFinding:
    if declared is None or measured is None:
        return ReconciliationFinding(
            field, declared, measured, None, "ok", "not compared (one side absent)"
        )
    pct = _divergence_pct(declared, measured)
    if pct is None:
        return ReconciliationFinding(
            field, declared, measured, None, "ok", "not compared (measured value is zero)"
        )
    if pct >= error_pct:
        sev, verdict = "error", "does NOT reconcile"
    elif pct >= warn_pct:
        sev, verdict = "warning", "diverges"
    else:
        return ReconciliationFinding(field, declared, measured, pct, "ok", "reconciles")
    return ReconciliationFinding(
        field,
        declared,
        measured,
        pct,
        sev,
        f"declared {declared:,.1f} {units} vs measured {measured:,.1f} {units} "
        f"({pct:.1f}% apart) — {verdict}. The declared figure drives sizing and capex while the "
        "measured series drives revenue, so the two halves of this case describe different loads.",
    )


def reconcile_declared_against_measured(
    site: SiteModel, summary: HalfHourlySummary
) -> ReconciliationResult:
    """Gate 3. Compare what the site declares against what its half-hourly file measures.

    Compares annual energy and peak import. Returns every comparison — including the ones that
    pass — so the reconciliation is auditable rather than only noisy on failure.
    """
    findings: list[ReconciliationFinding] = []

    declared_annual = site.load_data.annual_kwh or site.annual_consumption_kwh
    # Compare against the ANNUALISED measured figure: a declared annual total is an annual claim,
    # so on a part-year file the like-for-like comparison is the annualised one. (That figure
    # carries the seasonal caveat of the months present — which is precisely why a large
    # divergence here is worth recording as an open ask. Under D44 it caps the band and states the
    # ask; it does not stop the run, and neither figure is re-based onto the other.)
    measured_annual = summary.annualised_import_kwh or summary.total_import_kwh
    findings.append(
        _compare(
            "annual_kwh",
            declared_annual,
            measured_annual,
            warn_pct=ANNUAL_KWH_WARN_PCT,
            error_pct=ANNUAL_KWH_ERROR_PCT,
            units="kWh",
        )
    )

    findings.append(
        _compare(
            "max_import_kw",
            site.load_data.max_import_kw,
            summary.max_import_kw,
            warn_pct=PEAK_KW_WARN_PCT,
            error_pct=PEAK_KW_ERROR_PCT,
            units="kW",
        )
    )

    declared_rows = site.load_data.half_hourly_rows
    measured_rows = summary.coverage.row_count
    if declared_rows is not None and measured_rows is not None and declared_rows != measured_rows:
        pct = _divergence_pct(float(declared_rows), float(measured_rows))
        findings.append(
            ReconciliationFinding(
                "half_hourly_rows",
                float(declared_rows),
                float(measured_rows),
                pct,
                "warning",
                f"declared {declared_rows:,} rows vs {measured_rows:,} in the file — the site "
                "record describes a different extract than the one being priced.",
            )
        )

    return ReconciliationResult(tuple(findings))


# =============================================================================================
# Gate 3b — the modelled customer bill against the site's own invoice.
# =============================================================================================

#: Modelled half-hourly volume vs invoiced kWh. Tight on purpose: where no metered HH series
#: exists the series is *scaled to* the invoice totals, so any real deviation is a basis error, not
#: noise. 1% is rounding; 3% catches a wrong denominator — the Tanhouse defect was +9.03%, a
#: modelled series built on a roll-up that included the customer's own solar generation.
BILL_VOLUME_WARN_PCT = 1.0
BILL_VOLUME_ERROR_PCT = 3.0

#: Modelled annual delivered cost BELOW the invoice. Wide, because the stack's omissions are
#: documented and all in this direction: −8% is about the largest per-site omission that is not
#: itemisable in advance (Ewelme's 1.56 p/kWh unexplained residual = 5.8%, plus ~1 sigma of the
#: invoice's own monthly rate scatter); −30% bounds the largest itemisable class, the 5-MPAN
#: fixed-line collapse at 24.5% of invoiced spend.
BILL_COST_UNDERSHOOT_WARN_PCT = 8.0
BILL_COST_UNDERSHOOT_ERROR_PCT = 30.0

#: Modelled annual delivered cost ABOVE the invoice. Deliberately far tighter than the undershoot
#: bound. Invoiced cost is a hard fact and the stack is documented as biased low, so an overshoot
#: has no conservative story: it overstates avoided-cost value, the largest component of the priced
#: floor. +2% covers calendar-vs-billing-period alignment and the 365.25-day fixed-line convention.
BILL_COST_OVERSHOOT_WARN_PCT = 2.0
BILL_COST_OVERSHOOT_ERROR_PCT = 5.0

#: A shortfall past the WARN bound must be accounted for. The share is measured against the part of
#: the shortfall that EXCEEDS the WARN threshold, not the whole shortfall: the first 8% is already
#: conceded as unexplainable noise, so requiring documented omissions to cover it too would double-
#: count it and fail sites the evidence explains (Ewelme itemises 62% of its whole gap but 130% of
#: its excess). Anything less than this share is an ERROR whatever the size of the gap.
BILL_ITEMISATION_MIN_SHARE_PCT = 80.0

#: Monthly delivered cost, ~2.5 sigma and ~4 sigma of the ~6% monthly rate CV the invoices
#: themselves show (per-site CV 4.8-6.7%). Applied to no more than one month: two or more months
#: past WARN is itself the ERROR, because scatter that big in two months is not billing-period
#: drift.
BILL_MONTHLY_WARN_PCT = 15.0
BILL_MONTHLY_ERROR_PCT = 25.0
BILL_MONTHLY_MAX_WARNED_MONTHS = 1

#: Above this share of fixed charges in the modelled bill the monthly limb is SUPPRESSED and says
#: so. The implied unit rate is then essentially `fixed / kWh` and its scatter measures volume, not
#: the model: Knowl Hill is 71.5% fixed and its invoiced monthly rate ranges 39.67-190.51 p/kWh
#: (CV 52.1%) on data that reconciles exactly on volume.
BILL_MONTHLY_FIXED_SHARE_SUPPRESSION_PCT = 50.0

#: Severity `"absent"` is not a fourth flavour of pass. It exists so that "there is no invoice"
#: can never be counted as `ok`, and `"not_run"` so a limb that could not execute cannot either.
_ABSENT = "absent"
_NOT_RUN = "not_run"


@dataclass(frozen=True)
class DocumentedOmission:
    """One quantified line the modelled tariff stack is known to omit, with its evidence.

    ``kind`` must not be flattened away:

    - ``"computed"`` — a £ figure derived from a known rate and a known quantity (an omitted
      per-MPAN standing line, a capacity charge on a stated kVA, an excluded Capacity Market
      supplier charge at a stated p/kWh).
    - ``"bounded_by_declared_uncertainty"`` — *not* independently derived, but shown to fall inside
      a range the tariff's own ``source`` string already declares unknown. Weaker evidence, and a
      reader must be able to tell the difference: Knowl Hill's whole shortfall is explained this
      way, by TNUoS TCR band headroom, and one invoice page would replace it with a fact.
    """

    label: str
    gbp: float
    kind: str
    source: str


@dataclass(frozen=True)
class InvoiceEvidence:
    """What a site's real invoices say, or the recorded fact that it has none.

    ``documented_omissions`` describe the MODELLED stack rather than the invoice, but they are
    carried here because the gate cannot judge a shortfall without them, and because they must
    travel with the invoice they were quantified against.
    """

    site_id: str
    invoice_available: bool
    period: str = ""
    invoiced_kwh: float | None = None
    invoiced_cost_gbp: float | None = None
    #: Invoiced cost per calendar month, e.g. ``{"2025-01": 11151.71}``. Absent months are absent,
    #: never zero: a month with no reading and a month billed at £0 are different facts.
    monthly_cost_gbp: Mapping[str, float] = field(default_factory=dict)
    #: ``(month, reason)`` pairs excluded from the MONTHLY limb only — a catch-up bill, a cost with
    #: no volume, a recorded outlier. The ANNUAL limb still carries them in full. Listed in the
    #: gate's own output, never smoothed away.
    excluded_months: tuple[tuple[str, str], ...] = ()
    documented_omissions: tuple[DocumentedOmission, ...] = ()
    #: Required when ``invoice_available`` is False, and stated in the finding.
    absence_reason: str | None = None
    source: str = ""


@dataclass(frozen=True)
class BillFinding:
    """One modelled-vs-invoiced comparison.

    ``modelled`` and ``invoiced`` are named rather than reusing Gate 3's declared/measured pair
    because which side is the evidence matters here: the invoice is the independent fact and the
    modelled figure is the claim under test. ``variance_pct`` is SIGNED — negative is an undershoot,
    positive an overshoot — because the two directions carry opposite tolerances and opposite
    commercial meaning, and an absolute value would hide exactly the thing the gate exists to see.
    """

    field: str
    modelled: float | None
    invoiced: float | None
    variance_pct: float | None
    severity: str  # "ok" | "warning" | "error" | "absent" | "not_run"
    message: str


@dataclass(frozen=True)
class BillReconciliationResult:
    """Gate 3b's verdict for ONE site. Never aggregate these into a portfolio figure."""

    site_id: str
    invoice_available: bool
    findings: tuple[BillFinding, ...]

    @property
    def errors(self) -> tuple[BillFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[BillFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def limbs_not_run(self) -> tuple[BillFinding, ...]:
        """Limbs that could not execute or were deliberately suppressed. Reported, not silent."""
        return tuple(f for f in self.findings if f.severity in (_NOT_RUN, _ABSENT))

    @property
    def reconciles(self) -> bool | None:
        """True / False / **None when there is no invoice to reconcile against**.

        The tri-state is the point. A site with no invoice has not passed and has not failed; it has
        never been checked, and the one thing it must never report is "reconciled" (D45 amendment).
        """
        if not self.invoice_available:
            return None
        return not self.errors

    @property
    def status(self) -> str:
        if not self.invoice_available:
            return "no_invoice_evidence"
        if self.errors:
            return "does_not_reconcile"
        if self.warnings:
            return "reconciles_with_warnings"
        return "reconciles"

    @property
    def confidence_ceiling(self) -> ConfidenceBand:
        """D45 band effect: ERROR or no-invoice caps at LOW, WARNING at MEDIUM. Never a block."""
        if not self.invoice_available or self.errors:
            return ConfidenceBand.LOW
        if self.warnings:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.HIGH

    def summary(self) -> str:
        if self.status == "reconciles" and not self.limbs_not_run:
            return (
                f"{self.site_id}: modelled delivered cost and volume reconcile against the site's "
                "own invoices."
            )
        parts = [f"{f.field}: {f.message}" for f in self.findings if f.severity != "ok"]
        return f"{self.site_id}: " + " | ".join(parts)

    @property
    def basis_chosen(self) -> str:
        """What the run priced on. The gate never re-bases the model onto the invoice."""
        if not self.invoice_available:
            return (
                "Basis chosen: UNCHANGED — the modelled delivered cost stands, and it has NOT been "
                "reconciled against anything because no invoice exists for this site. This is a "
                "recorded absence of evidence, not a passed check, and the direction of any error "
                "is unknown."
            )
        if self.status == "reconciles":
            return (
                "The modelled delivered cost reconciles against the invoiced cost within "
                "tolerance; the run prices on the modelled stack, as it would have anyway."
            )
        return (
            "Basis chosen: UNCHANGED — the run still prices on the MODELLED tariff stack. The "
            "divergence against the invoice is recorded, not resolved: the modelled bill was not "
            "scaled, blended or re-based onto the invoice, because doing so would destroy the only "
            "independent check on the tariff stack that exists."
        )


def invoice_evidence_from_mapping(site_id: str, raw: Mapping[str, Any]) -> InvoiceEvidence:
    """Build `InvoiceEvidence` from the JSON shape `scripts/extract_site_data.py` writes.

    Reads `data/sites/grundon_invoices_2025.json`. `Any` is the honest element type at a
    parsed-JSON boundary. **Absent keys stay absent** — nothing is defaulted to zero, because a
    zero invoice and an unknown invoice are different facts and only one of them can be reconciled
    against. A site whose `invoice_available` is False therefore arrives with `invoiced_cost_gbp`
    still `None`, which is what makes the gate's tri-state possible.
    """
    return InvoiceEvidence(
        site_id=site_id,
        invoice_available=bool(raw.get("invoice_available")),
        period=str(raw.get("period", "")),
        invoiced_kwh=None if raw.get("invoiced_kwh") is None else float(raw["invoiced_kwh"]),
        invoiced_cost_gbp=(
            None if raw.get("invoiced_cost_gbp") is None else float(raw["invoiced_cost_gbp"])
        ),
        monthly_cost_gbp={str(k): float(v) for k, v in raw.get("monthly_cost_gbp", {}).items()},
        excluded_months=tuple(
            (str(e["month"]), str(e["reason"])) for e in raw.get("excluded_months", ())
        ),
        documented_omissions=tuple(
            DocumentedOmission(
                label=str(o["label"]),
                gbp=float(o["gbp"]),
                kind=str(o["kind"]),
                source=str(o["source"]),
            )
            for o in raw.get("documented_omissions", ())
        ),
        absence_reason=(None if raw.get("absence_reason") is None else str(raw["absence_reason"])),
        source=str(raw.get("source", "")),
    )


def _variance_pct(modelled: float, invoiced: float) -> float | None:
    """SIGNED variance of the modelled figure against the invoiced one. Direction is the point."""
    if invoiced == 0:
        return None
    return (modelled - invoiced) / abs(invoiced) * 100.0


def _volume_finding(modelled_kwh: float, invoiced_kwh: float) -> BillFinding:
    pct = _variance_pct(modelled_kwh, invoiced_kwh)
    if pct is None:
        return BillFinding(
            "bill_volume_kwh",
            modelled_kwh,
            invoiced_kwh,
            None,
            _NOT_RUN,
            "not compared (invoiced volume is zero, so no percentage is defined)",
        )
    magnitude = abs(pct)
    tail = (
        f"modelled {modelled_kwh:,.1f} kWh vs invoiced {invoiced_kwh:,.1f} kWh ({pct:+.2f}%). "
        "Where no metered series exists the modelled series is SCALED TO the invoice totals, so a "
        "deviation here is a wrong basis or a wrong denominator, not meter noise."
    )
    if magnitude >= BILL_VOLUME_ERROR_PCT:
        return BillFinding(
            "bill_volume_kwh",
            modelled_kwh,
            invoiced_kwh,
            pct,
            "error",
            f"{tail} Does NOT reconcile",
        )
    if magnitude >= BILL_VOLUME_WARN_PCT:
        return BillFinding(
            "bill_volume_kwh", modelled_kwh, invoiced_kwh, pct, "warning", f"{tail} Diverges"
        )
    return BillFinding(
        "bill_volume_kwh", modelled_kwh, invoiced_kwh, pct, "ok", f"{tail} Reconciles"
    )


def _itemisation(
    shortfall_gbp: float, invoiced_cost_gbp: float, omissions: Sequence[DocumentedOmission]
) -> tuple[float, float, float | None]:
    """(itemised £, shortfall £ in excess of the WARN bound, itemised share of that excess %)."""
    warn_gbp = abs(invoiced_cost_gbp) * BILL_COST_UNDERSHOOT_WARN_PCT / 100.0
    excess = shortfall_gbp - warn_gbp
    itemised = sum(o.gbp for o in omissions)
    share = None if excess <= 0 else itemised / excess * 100.0
    return itemised, excess, share


def _cost_finding(
    modelled_cost_gbp: float,
    invoiced_cost_gbp: float,
    omissions: Sequence[DocumentedOmission],
) -> BillFinding:
    pct = _variance_pct(modelled_cost_gbp, invoiced_cost_gbp)
    if pct is None:
        return BillFinding(
            "bill_cost_gbp",
            modelled_cost_gbp,
            invoiced_cost_gbp,
            None,
            _NOT_RUN,
            "not compared (invoiced cost is zero, so no percentage is defined)",
        )
    head = f"modelled £{modelled_cost_gbp:,.0f} vs invoiced £{invoiced_cost_gbp:,.0f} ({pct:+.2f}%)"

    if pct > 0:  # OVERSHOOT — the direction with no conservative reading.
        tail = (
            " The modelled bill EXCEEDS a real invoice, so avoided-cost value is OVERSTATED. There "
            "is no conservative reading of this direction and no omission from the stack can "
            "explain it, because an omission can only push the modelled bill down."
        )
        if pct >= BILL_COST_OVERSHOOT_ERROR_PCT:
            sev, verdict = "error", " Does NOT reconcile."
        elif pct >= BILL_COST_OVERSHOOT_WARN_PCT:
            sev, verdict = "warning", " Diverges."
        else:
            return BillFinding(
                "bill_cost_gbp",
                modelled_cost_gbp,
                invoiced_cost_gbp,
                pct,
                "ok",
                f"{head} Reconciles (overshoot inside the +{BILL_COST_OVERSHOOT_WARN_PCT:.0f}% "
                "alignment allowance).",
            )
        return BillFinding(
            "bill_cost_gbp", modelled_cost_gbp, invoiced_cost_gbp, pct, sev, head + verdict + tail
        )

    # UNDERSHOOT — conservative in direction, but only credible if it is accounted for.
    magnitude = -pct
    if magnitude < BILL_COST_UNDERSHOOT_WARN_PCT:
        return BillFinding(
            "bill_cost_gbp",
            modelled_cost_gbp,
            invoiced_cost_gbp,
            pct,
            "ok",
            f"{head} Reconciles.",
        )

    shortfall = invoiced_cost_gbp - modelled_cost_gbp
    itemised, excess, share = _itemisation(shortfall, invoiced_cost_gbp, omissions)
    labels = "; ".join(f"{o.label} £{o.gbp:,.0f} [{o.kind}]" for o in omissions) or "NONE"
    acct = (
        f" Shortfall £{shortfall:,.0f}, of which £{excess:,.0f} exceeds the "
        f"−{BILL_COST_UNDERSHOOT_WARN_PCT:.0f}% WARN bound; documented omissions total "
        f"£{itemised:,.0f} = {0.0 if share is None else share:.0f}% of that excess "
        f"(minimum {BILL_ITEMISATION_MIN_SHARE_PCT:.0f}%). Omissions: {labels}."
    )
    if magnitude >= BILL_COST_UNDERSHOOT_ERROR_PCT:
        return BillFinding(
            "bill_cost_gbp",
            modelled_cost_gbp,
            invoiced_cost_gbp,
            pct,
            "error",
            f"{head} Does NOT reconcile: the shortfall exceeds "
            f"−{BILL_COST_UNDERSHOOT_ERROR_PCT:.0f}% whatever explains it." + acct,
        )
    if share is None or share < BILL_ITEMISATION_MIN_SHARE_PCT:
        return BillFinding(
            "bill_cost_gbp",
            modelled_cost_gbp,
            invoiced_cost_gbp,
            pct,
            "error",
            f"{head} Does NOT reconcile: the shortfall is UNEXPLAINED — documented omissions do "
            f"not account for {BILL_ITEMISATION_MIN_SHARE_PCT:.0f}% of the part that exceeds the "
            f"WARN bound, so a conservative story cannot be told about it." + acct,
        )
    return BillFinding(
        "bill_cost_gbp",
        modelled_cost_gbp,
        invoiced_cost_gbp,
        pct,
        "warning",
        f"{head} Diverges, in the CONSERVATIVE direction and accounted for: avoided-cost value is "
        f"understated by roughly this much, for reasons itemised before the check existed." + acct,
    )


def _monthly_finding(
    modelled_monthly_gbp: Mapping[str, float] | None,
    evidence: InvoiceEvidence,
    fixed_share_pct: float | None,
) -> BillFinding:
    excluded = {m for m, _ in evidence.excluded_months}
    excl_note = (
        ""
        if not evidence.excluded_months
        else " Excluded and listed, never smoothed: "
        + "; ".join(f"{m} ({why})" for m, why in evidence.excluded_months)
    )

    if fixed_share_pct is not None and fixed_share_pct > BILL_MONTHLY_FIXED_SHARE_SUPPRESSION_PCT:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            None,
            _NOT_RUN,
            f"SUPPRESSED: fixed charges are {fixed_share_pct:.1f}% of the modelled bill (above "
            f"{BILL_MONTHLY_FIXED_SHARE_SUPPRESSION_PCT:.0f}%), so the implied monthly unit "
            "rate is essentially fixed/kWh and its scatter measures volume, not the model. The "
            "annual limbs still apply in full." + excl_note,
        )
    if not modelled_monthly_gbp:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            None,
            _NOT_RUN,
            "NOT RUN: no modelled monthly decomposition was supplied. `CustomerBill` carries "
            "annual totals only, so this limb cannot execute on a production run record — a "
            "REPORTING GAP, not a pass, and it must not be read as one." + excl_note,
        )
    if not evidence.monthly_cost_gbp:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            None,
            _NOT_RUN,
            "NOT RUN: the invoice evidence carries no monthly breakdown, so there is nothing to "
            "compare month by month. Recorded as unchecked, not as reconciled." + excl_note,
        )

    warned: list[tuple[str, float]] = []
    errored: list[tuple[str, float]] = []
    compared = 0
    worst: tuple[str, float] | None = None
    for month, invoiced in sorted(evidence.monthly_cost_gbp.items()):
        if month in excluded or month not in modelled_monthly_gbp or invoiced == 0:
            continue
        pct = _variance_pct(modelled_monthly_gbp[month], invoiced)
        if pct is None:
            continue
        compared += 1
        if worst is None or abs(pct) > abs(worst[1]):
            worst = (month, pct)
        if abs(pct) >= BILL_MONTHLY_ERROR_PCT:
            errored.append((month, pct))
        elif abs(pct) >= BILL_MONTHLY_WARN_PCT:
            warned.append((month, pct))

    if compared == 0:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            None,
            _NOT_RUN,
            "NOT RUN: no month appears on both sides after exclusions." + excl_note,
        )
    fmt = ", ".join(f"{m} {p:+.1f}%" for m, p in errored + warned)
    worst_pct = None if worst is None else worst[1]
    head = f"{compared} months compared"
    if errored:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            worst_pct,
            "error",
            f"{head}; {len(errored)} past +-{BILL_MONTHLY_ERROR_PCT:.0f}% ({fmt}) — the monthly "
            "shape does NOT reconcile." + excl_note,
        )
    if len(warned) > BILL_MONTHLY_MAX_WARNED_MONTHS:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            worst_pct,
            "error",
            f"{head}; {len(warned)} months past +-{BILL_MONTHLY_WARN_PCT:.0f}% ({fmt}) — more than "
            f"{BILL_MONTHLY_MAX_WARNED_MONTHS} is itself the ERROR, because scatter that wide in "
            "several months is not billing-period drift." + excl_note,
        )
    if warned:
        return BillFinding(
            "bill_monthly_cost_gbp",
            None,
            None,
            worst_pct,
            "warning",
            f"{head}; one month past +-{BILL_MONTHLY_WARN_PCT:.0f}% ({fmt})." + excl_note,
        )
    return BillFinding(
        "bill_monthly_cost_gbp",
        None,
        None,
        worst_pct,
        "ok",
        f"{head}, all inside +-{BILL_MONTHLY_WARN_PCT:.0f}% "
        f"(worst {'n/a' if worst is None else f'{worst[0]} {worst[1]:+.1f}%'})." + excl_note,
    )


def reconcile_bill_against_invoice(
    evidence: InvoiceEvidence,
    *,
    modelled_kwh: float | None = None,
    modelled_cost_gbp: float | None = None,
    modelled_fixed_gbp: float | None = None,
    modelled_monthly_cost_gbp: Mapping[str, float] | None = None,
) -> BillReconciliationResult:
    """Gate 3b, for ONE site. Compare the modelled customer bill to that site's own invoices.

    ``modelled_cost_gbp`` is ``CustomerBill.annual_bill_before_gbp`` — the BEFORE side, which is
    dispatch-invariant, so the check tests the tariff stack rather than the battery.
    ``modelled_kwh`` is ``CustomerBill.import_before_kwh``. ``modelled_fixed_gbp`` is
    ``standing_gbp + capacity_gbp`` and drives only the monthly limb's suppression rule.
    ``modelled_monthly_cost_gbp`` has no source on a production run record yet (see the monthly
    limb's own message).

    **Never call this with portfolio aggregates.** See the module docstring: the Grundon portfolio
    reconciles to +0.7% while no individual site is within +-7%.
    """
    if not evidence.invoice_available:
        reason = evidence.absence_reason or "no reason recorded"
        return BillReconciliationResult(
            evidence.site_id,
            False,
            (
                BillFinding(
                    "bill_invoice_evidence",
                    modelled_cost_gbp,
                    None,
                    None,
                    _ABSENT,
                    "NO INVOICE EVIDENCE EXISTS for this site, so its modelled delivered cost has "
                    "never been compared to anything. This is an absence of evidence and is NOT a "
                    f"reconciliation: {reason} Confidence is capped at LOW, and the modelled bill "
                    "must not be described as reconciled, validated or conservative — the "
                    "direction of any error is unknown.",
                ),
            ),
        )

    findings: list[BillFinding] = []
    if modelled_kwh is None or evidence.invoiced_kwh is None:
        findings.append(
            BillFinding(
                "bill_volume_kwh",
                modelled_kwh,
                evidence.invoiced_kwh,
                None,
                _NOT_RUN,
                "NOT RUN: one side of the volume comparison is absent. Recorded as unchecked.",
            )
        )
    else:
        findings.append(_volume_finding(modelled_kwh, evidence.invoiced_kwh))

    if modelled_cost_gbp is None or evidence.invoiced_cost_gbp is None:
        findings.append(
            BillFinding(
                "bill_cost_gbp",
                modelled_cost_gbp,
                evidence.invoiced_cost_gbp,
                None,
                _NOT_RUN,
                "NOT RUN: one side of the delivered-cost comparison is absent. A run record whose "
                "`customer_bill` is null lands here — and every run record in this repo did, which "
                "is why the check had never fired. Recorded as unchecked, never as reconciled.",
            )
        )
    else:
        findings.append(
            _cost_finding(
                modelled_cost_gbp, evidence.invoiced_cost_gbp, evidence.documented_omissions
            )
        )

    fixed_share = None
    if modelled_fixed_gbp is not None and modelled_cost_gbp:
        fixed_share = modelled_fixed_gbp / modelled_cost_gbp * 100.0
    findings.append(_monthly_finding(modelled_monthly_cost_gbp, evidence, fixed_share))

    return BillReconciliationResult(evidence.site_id, True, tuple(findings))


def bill_reconciliation_records(
    result: BillReconciliationResult, *, release: str | None = None
) -> tuple[DerivedAssumption, ...]:
    """Turn Gate 3b's ERRORs and its no-invoice state into recorded open asks (D45).

    Each record carries **no value**: the gate derives nothing and must never read as having chosen
    a delivered cost on the customer's behalf. WARNINGs are reported through the band, not as
    records — a documented, quantified, conservative shortfall is a disclosure, not an open ask.
    """
    records: list[DerivedAssumption] = []

    if not result.invoice_available:
        absent = next(iter(result.limbs_not_run), None)
        return (
            DerivedAssumption(
                kind="derived_input",
                key="tariff_stack.delivered_cost_reconciliation",
                missing_input="an invoice for this site — its modelled delivered cost has never "
                "been compared to a real bill",
                value=None,
                unit=None,
                basis=(absent.message if absent else "no invoice evidence")
                + f" {result.basis_chosen}",
                sources=[],
                derivation_method="not_derivable",
                derived_by=_DERIVED_BY,
                derived_on_release=release,
                confidence_ceiling=ConfidenceBand.LOW,
                confirmation_ask="Supply any invoice for this site showing delivered cost and kWh "
                "for the modelled period, so the tariff stack can be checked against a number the "
                "model has not consumed.",
            ),
        )

    for finding in result.errors:
        modelled = "unknown" if finding.modelled is None else f"{finding.modelled:,.1f}"
        invoiced = "unknown" if finding.invoiced is None else f"{finding.invoiced:,.1f}"
        pct = "" if finding.variance_pct is None else f" ({finding.variance_pct:+.2f}%)"
        records.append(
            DerivedAssumption(
                kind="derived_input",
                key=f"tariff_stack.{finding.field}",
                missing_input=f"a {finding.field} the invoice supports — modelled and invoiced "
                "disagree beyond the Gate-3b tolerance",
                value=None,
                unit=None,
                basis=f"Modelled {modelled} vs invoiced {invoiced}{pct}. {finding.message} "
                f"{result.basis_chosen}",
                sources=[],
                derivation_method="not_derivable",
                derived_by=_DERIVED_BY,
                derived_on_release=release,
                confidence_ceiling=ConfidenceBand.LOW,
                confirmation_ask="Confirm the site's billed delivered cost and volume for this "
                "period, and which tariff lines the invoice actually charges, so the modelled "
                "stack can be corrected rather than the divergence carried.",
            )
        )
    return tuple(records)
