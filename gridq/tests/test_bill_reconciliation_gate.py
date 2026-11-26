"""Validation Gate 3b — the modelled customer bill must reconcile against the site's own invoice.

`HalfHourlyDispatchSchedule.customer_bill()` has always rebuilt the site's historic annual bill from
the modelled load and the tariff stack, and **that figure had never been compared to an invoice**:
every run record in the repo carried `"customer_bill": null`, and nothing in `src/` made the
comparison. `bill_reconciliation.md` made it for the first time and found no Grundon site within
+-7%, with two sites wrong in the direction that OVERSTATES avoided-cost value.

The acceptance test of this gate is that it reproduces that document's six per-site verdicts. Those
are pinned in `test_reproduces_the_six_site_verdicts` and are the reason the thresholds are what
they are: change a threshold and that test tells you which real site's verdict you just changed.

Two regressions this file exists to prevent:

- **A portfolio-level bill check.** `test_a_portfolio_check_would_have_passed_four_large_errors`
  drives the real five-site figures through both a portfolio sum and the per-site gate. The
  portfolio agrees to +0.7%; four of five sites are wrong. Aggregating this gate destroys it.
- **A symmetric cost tolerance.** `test_the_cost_tolerance_is_asymmetric_by_direction` pins that
  −7.1% passes while +7.1% is an ERROR. An overshoot means the model claims the customer pays more
  than the invoice says, so avoided cost is overstated — the one direction with no conservative
  reading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests._dpa_support import dpa_released

from gridq.ingestion.reconciliation import (
    BILL_COST_OVERSHOOT_ERROR_PCT,
    BILL_COST_OVERSHOOT_WARN_PCT,
    BILL_COST_UNDERSHOOT_ERROR_PCT,
    BILL_COST_UNDERSHOOT_WARN_PCT,
    BILL_ITEMISATION_MIN_SHARE_PCT,
    BILL_MONTHLY_FIXED_SHARE_SUPPRESSION_PCT,
    BILL_VOLUME_ERROR_PCT,
    BILL_VOLUME_WARN_PCT,
    DocumentedOmission,
    InvoiceEvidence,
    bill_reconciliation_records,
    invoice_evidence_from_mapping,
    reconcile_bill_against_invoice,
)
from gridq.schemas import ConfidenceBand

REPO = Path(__file__).resolve().parents[1]
#: Quarantined customer data (WP-GRC-05): read only through `dpa_released`, so every test that
#: needs it skips, naming its blocker, until the customer's DPA is registered.
INVOICES = REPO / "data" / "sites" / "grundon_invoices_2025.json"

# ---------------------------------------------------------------------------------------------
# The five Grundon sites as `bill_reconciliation.md` §5 measured them, BEFORE the task 1-3 fixes.
# (invoiced kWh, invoiced £, modelled kWh, modelled £, modelled fixed £)
# ---------------------------------------------------------------------------------------------
REAL_SITES: dict[str, tuple[float, float, float, float, float]] = {
    "grundon_bishops_cleeve": (396_699, 110_870, 387_008, 82_113, 4_515 + 7_013),
    "grundon_colnbrook": (3_032_360, 395_611, 3_032_360, 423_622, 0.0),
    "grundon_ewelme": (694_062, 185_862, 694_062, 157_292, 4_515 + 21_038),
    "grundon_knowl_hill": (45_732, 29_214, 45_732, 24_759, 4_515 + 9_117),
    "grundon_tanhouse": (1_541_453, 346_974, 1_680_626, 387_722, 0.0),
}


def _evidence(site_id: str = "s", **kw) -> InvoiceEvidence:
    base = {
        "invoice_available": True,
        "period": "calendar 2025",
        "invoiced_kwh": 1_000_000.0,
        "invoiced_cost_gbp": 200_000.0,
    }
    base.update(kw)
    return InvoiceEvidence(site_id=site_id, **base)  # type: ignore[arg-type]


def _finding(result, field: str):
    return next(f for f in result.findings if f.field == field)


#: Bishops Cleeve's documented omissions **as `bill_reconciliation.md` §6 quantified them**, for the
#: combined 5-MPAN boundary that was retired on 2026-08-15 when the site split into
#: `grundon_bishops_cleeve_ash_plant` and `grundon_bishops_cleeve_mrf`
#: (`tests/test_invoice_evidence_site_boundary.py`). The evidence file now carries that id as an
#: explicit absence record, so these are inlined here rather than read from it.
#:
#: They are kept, not deleted, because this row is the **only** one in `REAL_SITES` that exercises
#: the itemised-shortfall path at −25.9%: a documented, quantified, conservative undershoot that
#: `_itemisation` accepts as accounted for. Dropping it would silently retire a limb of the
#: acceptance test along with the site boundary. It is a HISTORICAL measurement of a boundary that
#: no longer exists — never live evidence, and never a figure for any current site.
_RETIRED_BISHOPS_CLEEVE_OMISSIONS = (
    DocumentedOmission(
        label="4 omitted per-MPAN standing lines (TNUoS + DUoS TCR fixed residual)",
        gbp=18_061.82,
        kind="computed",
        source="bill_reconciliation.md §6. SiteModel holds ONE connection and ONE standing charge; "
        "5 MPANs carry 2025 volume at this site. 4 x £12.36264/day x 365.25.",
    ),
    DocumentedOmission(
        label="Capacity charge on BC Import 230 kVA + BC Ash Plant 1 93 kVA",
        gbp=9_060.54,
        kind="computed",
        source="bill_reconciliation.md §6. 323 kVA x £0.0768/kVA/day x 365.25.",
    ),
    DocumentedOmission(
        label="Capacity Market supplier charge (excluded by design, ~1.1 p/kWh)",
        gbp=4_257.09,
        kind="computed",
        source="bill_reconciliation.md §6. ~1.1 p/kWh x 387,008 kWh modelled.",
    ),
)

#: Site ids in `REAL_SITES` whose boundary has since been retired, with the §6 evidence inlined.
RETIRED_EVIDENCE: dict[str, InvoiceEvidence] = {
    "grundon_bishops_cleeve": InvoiceEvidence(
        site_id="grundon_bishops_cleeve",
        invoice_available=True,
        period="calendar 2025 (RETIRED combined boundary — historical, not a live site)",
        invoiced_kwh=396_698.7,
        invoiced_cost_gbp=110_869.87,
        documented_omissions=_RETIRED_BISHOPS_CLEEVE_OMISSIONS,
        source="bill_reconciliation.md §5-§6, for the 5-MPAN combined boundary retired 2026-08-15.",
    )
}


def _real_evidence(site_id: str) -> InvoiceEvidence:
    """The site's real invoice evidence, from the file the extraction script writes.

    Falls back to `RETIRED_EVIDENCE` for a boundary that has since been split, so this file keeps
    testing the verdicts `bill_reconciliation.md` measured without pinning the *current* evidence
    file to a site id that no longer describes a site.
    """
    raw = json.loads(dpa_released(INVOICES).read_text())
    if site_id in RETIRED_EVIDENCE:
        assert raw[site_id]["invoice_available"] is False, (
            f"{site_id} is retired in this file and must stay an explicit absence record; it has "
            "become a live invoice again."
        )
        return RETIRED_EVIDENCE[site_id]
    return invoice_evidence_from_mapping(site_id, raw[site_id])


def _run_real(site_id: str, **overrides):
    inv_kwh, inv_gbp, mod_kwh, mod_gbp, fixed = REAL_SITES[site_id]
    ev = _real_evidence(site_id)
    # Use the document's own invoiced figures so the verdicts are pinned to it, not to the file.
    ev = InvoiceEvidence(
        site_id=ev.site_id,
        invoice_available=True,
        period=ev.period,
        invoiced_kwh=inv_kwh,
        invoiced_cost_gbp=inv_gbp,
        monthly_cost_gbp=ev.monthly_cost_gbp,
        excluded_months=ev.excluded_months,
        documented_omissions=ev.documented_omissions,
        source=ev.source,
    )
    kw = {
        "modelled_kwh": mod_kwh,
        "modelled_cost_gbp": mod_gbp,
        "modelled_fixed_gbp": fixed,
    }
    kw.update(overrides)
    return reconcile_bill_against_invoice(ev, **kw)  # type: ignore[arg-type]


# =============================================================================================
# The acceptance test: the six verdicts of bill_reconciliation.md §8.
# =============================================================================================


@pytest.mark.parametrize(
    ("site_id", "volume_sev", "cost_sev", "ceiling"),
    [
        # Volume −2.44% (the recorded BC Lodge correction) WARNs but passes ERROR; cost −25.9% is
        # inside the −30% bound AND itemised to documented omissions, so it is a WARNING.
        # HISTORICAL: this combined boundary was retired on 2026-08-15 (see `RETIRED_EVIDENCE`).
        # It stays because it is the only row that exercises the itemised-shortfall path.
        ("grundon_bishops_cleeve", "warning", "warning", ConfidenceBand.MEDIUM),
        # Volume exact; cost +7.1% OVERSHOOT — no conservative reading, so ERROR.
        ("grundon_colnbrook", "ok", "error", ConfidenceBand.LOW),
        # Volume exact; cost −15.4%, 130% of the excess itemised.
        ("grundon_ewelme", "ok", "warning", ConfidenceBand.MEDIUM),
        # Volume exact; cost −15.3%, explained by the assumed TNUoS TCR band.
        ("grundon_knowl_hill", "ok", "warning", ConfidenceBand.MEDIUM),
        # Volume +9.03% ERROR (solar priced as purchased) AND cost +11.7% ERROR.
        ("grundon_tanhouse", "error", "error", ConfidenceBand.LOW),
    ],
)
def test_reproduces_the_six_site_verdicts(site_id, volume_sev, cost_sev, ceiling) -> None:
    """THE ACCEPTANCE TEST. Every threshold in the gate is justified by one of these five rows."""
    result = _run_real(site_id)
    assert _finding(result, "bill_volume_kwh").severity == volume_sev, result.summary()
    assert _finding(result, "bill_cost_gbp").severity == cost_sev, result.summary()
    assert result.confidence_ceiling is ceiling, result.summary()
    assert result.reconciles is (cost_sev != "error" and volume_sev != "error")


def test_the_sixth_verdict_harwell_has_no_invoice_at_all() -> None:
    """Harwell. An absence of evidence must never read as a reconciliation (D45 amendment)."""
    ev = _real_evidence("harwell_campus_k16w304527")
    assert ev.invoice_available is False
    assert ev.absence_reason

    result = reconcile_bill_against_invoice(ev, modelled_cost_gbp=178_597.16, modelled_kwh=908_590)

    assert result.reconciles is None, "no invoice is neither a pass nor a measured failure"
    assert result.status == "no_invoice_evidence"
    assert result.confidence_ceiling is ConfidenceBand.LOW
    text = result.summary() + result.basis_chosen
    assert "NOT a reconciliation" in text or "not been reconciled" in text
    assert "reconciles against" not in text
    # The absence is an open ask, recorded with NO value (the gate never derives a bill).
    records = bill_reconciliation_records(result, release="gb_v1_8")
    assert len(records) == 1
    assert records[0].value is None
    assert records[0].confidence_ceiling is ConfidenceBand.LOW
    assert "invoice" in records[0].confirmation_ask.lower()


# =============================================================================================
# Direction and asymmetry.
# =============================================================================================


def test_the_cost_tolerance_is_asymmetric_by_direction() -> None:
    """The same magnitude means different things up and down. A sign slip inverts the whole gate."""
    inv = 100_000.0
    under = reconcile_bill_against_invoice(
        _evidence(invoiced_cost_gbp=inv, invoiced_kwh=1_000.0),
        modelled_kwh=1_000.0,
        modelled_cost_gbp=inv * 0.929,  # −7.1%
    )
    over = reconcile_bill_against_invoice(
        _evidence(invoiced_cost_gbp=inv, invoiced_kwh=1_000.0),
        modelled_kwh=1_000.0,
        modelled_cost_gbp=inv * 1.071,  # +7.1%
    )
    assert _finding(under, "bill_cost_gbp").severity == "ok", "−7.1% is inside the −8% WARN bound"
    assert _finding(over, "bill_cost_gbp").severity == "error", "+7.1% is past the +5% ERROR bound"
    assert _finding(over, "bill_cost_gbp").variance_pct == pytest.approx(7.1, abs=0.01)
    assert _finding(under, "bill_cost_gbp").variance_pct == pytest.approx(-7.1, abs=0.01)
    assert "OVERSTATED" in _finding(over, "bill_cost_gbp").message


@pytest.mark.parametrize(
    ("factor", "expected"),
    [
        (1.0 + (BILL_COST_OVERSHOOT_WARN_PCT - 0.5) / 100, "ok"),
        (1.0 + BILL_COST_OVERSHOOT_WARN_PCT / 100, "warning"),
        (1.025, "warning"),
        (1.0 + BILL_COST_OVERSHOOT_ERROR_PCT / 100, "error"),
        (1.071, "error"),
        (1.117, "error"),
    ],
)
def test_overshoot_ladder(factor, expected) -> None:
    result = reconcile_bill_against_invoice(
        _evidence(), modelled_kwh=1_000_000.0, modelled_cost_gbp=200_000.0 * factor
    )
    assert _finding(result, "bill_cost_gbp").severity == expected


def test_an_overshoot_cannot_be_excused_by_documented_omissions() -> None:
    """An omission can only push the modelled bill DOWN, so it can never explain an overshoot."""
    ev = _evidence(
        documented_omissions=(
            DocumentedOmission("a huge omitted line", 500_000.0, "computed", "irrelevant here"),
        )
    )
    result = reconcile_bill_against_invoice(
        ev, modelled_kwh=1_000_000.0, modelled_cost_gbp=200_000.0 * 1.071
    )
    assert _finding(result, "bill_cost_gbp").severity == "error"
    assert "can only push the modelled bill down" in _finding(result, "bill_cost_gbp").message


# =============================================================================================
# Volume — tight, because the series is scaled TO the invoice.
# =============================================================================================


@pytest.mark.parametrize(
    ("factor", "expected"),
    [
        (1.0, "ok"),
        (1.0 + (BILL_VOLUME_WARN_PCT - 0.1) / 100, "ok"),
        (1.0 + BILL_VOLUME_WARN_PCT / 100, "warning"),
        (1.0 - BILL_VOLUME_WARN_PCT / 100, "warning"),
        (0.9756, "warning"),  # Bishops Cleeve −2.44%, the recorded Lodge correction
        (1.0 + BILL_VOLUME_ERROR_PCT / 100, "error"),
        (1.0903, "error"),  # Tanhouse +9.03% — solar priced as purchased energy
    ],
)
def test_volume_ladder_is_symmetric_and_tight(factor, expected) -> None:
    result = reconcile_bill_against_invoice(
        _evidence(), modelled_kwh=1_000_000.0 * factor, modelled_cost_gbp=200_000.0
    )
    assert _finding(result, "bill_volume_kwh").severity == expected


def test_the_tanhouse_solar_defect_is_a_volume_error() -> None:
    """THE DEFECT. 139,172 kWh of the customer's own solar was priced as purchased energy."""
    result = _run_real("grundon_tanhouse")
    vol = _finding(result, "bill_volume_kwh")
    assert vol.severity == "error"
    assert vol.variance_pct == pytest.approx(9.03, abs=0.01)
    assert result.reconciles is False


# =============================================================================================
# The itemisation rule — and its denominator, which is the whole subtlety.
# =============================================================================================


def test_the_itemisation_denominator_is_the_excess_over_warn_not_the_whole_shortfall() -> None:
    """Ewelme is the case that fixes the denominator.

    It itemises 62% of its whole £28,570 gap but 130% of the £13,701 that exceeds the −8% WARN
    bound. Measuring against the whole gap would ERROR a site whose shortfall the evidence explains,
    because the first 8% is already conceded as unexplainable noise — counting it twice is the bug.
    """
    result = _run_real("grundon_ewelme")
    cost = _finding(result, "bill_cost_gbp")
    assert cost.severity == "warning", cost.message
    itemised = sum(o.gbp for o in _real_evidence("grundon_ewelme").documented_omissions)
    shortfall = 185_862 - 157_292
    assert itemised / shortfall * 100 < BILL_ITEMISATION_MIN_SHARE_PCT, "62% of the whole gap"
    excess = shortfall - 185_862 * BILL_COST_UNDERSHOOT_WARN_PCT / 100
    assert itemised / excess * 100 > BILL_ITEMISATION_MIN_SHARE_PCT, "130% of the excess"


def test_an_unexplained_shortfall_past_warn_is_an_error_however_small() -> None:
    """Size is not the test, accountability is. A −10% gap with nothing behind it is an ERROR."""
    result = reconcile_bill_against_invoice(
        _evidence(documented_omissions=()),
        modelled_kwh=1_000_000.0,
        modelled_cost_gbp=200_000.0 * 0.90,
    )
    cost = _finding(result, "bill_cost_gbp")
    assert cost.severity == "error"
    assert "UNEXPLAINED" in cost.message
    assert "Omissions: NONE" in cost.message


def test_knowl_hill_needs_its_declared_uncertain_line_to_pass() -> None:
    """Knowl Hill passes only because the ASSUMED TNUoS TCR band is admitted as an omission.

    The Capacity Market charge alone is 24% of its excess — an ERROR. The band attribution takes it
    to 210%. That line is `bounded_by_declared_uncertainty`, not `computed`, and the distinction
    must survive into the message so a reader is not told this is settled arithmetic.
    """
    ev = _real_evidence("grundon_knowl_hill")
    kinds = {o.kind for o in ev.documented_omissions}
    assert kinds == {"computed", "bounded_by_declared_uncertainty"}

    full = _run_real("grundon_knowl_hill")
    assert _finding(full, "bill_cost_gbp").severity == "warning"
    assert "bounded_by_declared_uncertainty" in _finding(full, "bill_cost_gbp").message

    computed_only = InvoiceEvidence(
        site_id=ev.site_id,
        invoice_available=True,
        invoiced_kwh=45_732,
        invoiced_cost_gbp=29_214,
        documented_omissions=tuple(o for o in ev.documented_omissions if o.kind == "computed"),
    )
    stripped = reconcile_bill_against_invoice(
        computed_only, modelled_kwh=45_732, modelled_cost_gbp=24_759
    )
    assert _finding(stripped, "bill_cost_gbp").severity == "error"
    assert "UNEXPLAINED" in _finding(stripped, "bill_cost_gbp").message


def test_a_huge_shortfall_errors_even_when_fully_itemised() -> None:
    """−30% is a hard bound: past it, the modelled bill is not a model of the same thing."""
    ev = _evidence(
        documented_omissions=(
            DocumentedOmission("everything", 200_000.0, "computed", "fully accounted"),
        )
    )
    result = reconcile_bill_against_invoice(
        ev,
        modelled_kwh=1_000_000.0,
        modelled_cost_gbp=200_000.0 * (1 - (BILL_COST_UNDERSHOOT_ERROR_PCT + 1) / 100),
    )
    cost = _finding(result, "bill_cost_gbp")
    assert cost.severity == "error"
    assert "whatever explains it" in cost.message


# =============================================================================================
# The monthly limb.
# =============================================================================================


def _monthly(*values: float) -> dict[str, float]:
    return {f"2025-{i + 1:02d}": v for i, v in enumerate(values)}


def test_monthly_limb_is_suppressed_where_fixed_charges_dominate() -> None:
    """Knowl Hill: 71.5% fixed, so the implied monthly rate is fixed/kWh and carries no signal.

    Suppression must SAY so, naming the share. A silently skipped check reads as a passed one.
    """
    inv = _monthly(*([1_000.0] * 12))
    ev = _evidence(invoiced_cost_gbp=12_000.0, monthly_cost_gbp=inv)
    result = reconcile_bill_against_invoice(
        ev,
        modelled_kwh=1_000_000.0,
        modelled_cost_gbp=12_000.0,
        modelled_fixed_gbp=12_000.0 * 0.715,
        modelled_monthly_cost_gbp=_monthly(*([2_000.0] * 12)),  # wildly off, and irrelevant
    )
    monthly = _finding(result, "bill_monthly_cost_gbp")
    assert monthly.severity == "not_run"
    assert "SUPPRESSED" in monthly.message
    assert "71.5%" in monthly.message
    assert monthly in result.limbs_not_run
    assert result.confidence_ceiling is not ConfidenceBand.LOW, "suppression is not a failure"


def test_fixed_share_just_below_the_suppression_bound_still_runs() -> None:
    ev = _evidence(invoiced_cost_gbp=12_000.0, monthly_cost_gbp=_monthly(*([1_000.0] * 12)))
    result = reconcile_bill_against_invoice(
        ev,
        modelled_kwh=1_000_000.0,
        modelled_cost_gbp=12_000.0,
        modelled_fixed_gbp=12_000.0 * (BILL_MONTHLY_FIXED_SHARE_SUPPRESSION_PCT / 100),
        modelled_monthly_cost_gbp=_monthly(*([1_000.0] * 12)),
    )
    assert _finding(result, "bill_monthly_cost_gbp").severity == "ok"


def test_two_months_past_warn_is_itself_an_error() -> None:
    """Scatter that wide in two months is not billing-period drift."""
    inv = _monthly(*([1_000.0] * 12))
    one_bad = _monthly(*([1_000.0] * 11 + [1_200.0]))
    two_bad = _monthly(*([1_000.0] * 10 + [1_200.0, 1_200.0]))
    for modelled, expected in ((one_bad, "warning"), (two_bad, "error")):
        result = reconcile_bill_against_invoice(
            _evidence(invoiced_cost_gbp=12_000.0, monthly_cost_gbp=inv),
            modelled_kwh=1_000_000.0,
            modelled_cost_gbp=12_000.0,
            modelled_fixed_gbp=0.0,
            modelled_monthly_cost_gbp=modelled,
        )
        assert _finding(result, "bill_monthly_cost_gbp").severity == expected


def test_an_anomaly_month_is_excluded_and_listed_never_smoothed() -> None:
    """Bishops Cleeve 2025-03 carries a catch-up bill, so its cost variance is confounded.

    The month is dropped from the MONTHLY limb with its reason stated, and the ANNUAL limb still
    carries it in full — the gate never edits an invoice.
    """
    inv = _monthly(*([1_000.0] * 12))
    modelled = _monthly(*([1_000.0] * 2 + [400.0] + [1_000.0] * 9))  # March −60%
    ev = _evidence(
        invoiced_cost_gbp=12_000.0,
        monthly_cost_gbp=inv,
        excluded_months=(("2025-03", "BC Lodge catch-up bill, 68x baseline"),),
    )
    result = reconcile_bill_against_invoice(
        ev,
        modelled_kwh=1_000_000.0,
        modelled_cost_gbp=12_000.0,
        modelled_fixed_gbp=0.0,
        modelled_monthly_cost_gbp=modelled,
    )
    monthly = _finding(result, "bill_monthly_cost_gbp")
    assert monthly.severity == "ok"
    assert "11 months compared" in monthly.message
    assert "catch-up bill" in monthly.message
    assert "never smoothed" in monthly.message


def test_a_missing_monthly_decomposition_is_not_run_not_a_pass() -> None:
    """`CustomerBill` carries annual totals only, so on a real run record this limb cannot fire."""
    result = _run_real("grundon_colnbrook")
    monthly = _finding(result, "bill_monthly_cost_gbp")
    assert monthly.severity == "not_run"
    assert "NOT RUN" in monthly.message
    assert "REPORTING GAP" in monthly.message
    assert "not a pass" in monthly.message


# =============================================================================================
# Record-and-continue (D45), and the per-site rule.
# =============================================================================================


def test_a_breach_records_and_continues_and_never_re_bases_the_model() -> None:
    result = _run_real("grundon_tanhouse")
    assert result.reconciles is False
    records = bill_reconciliation_records(result, release="gb_v1_8")
    assert len(records) == 2, "one per ERROR limb: volume and cost"
    for rec in records:
        assert rec.value is None, "the gate derives nothing; it must not appear to have chosen a £"
        assert rec.unit is None
        assert rec.derivation_method == "not_derivable"
        assert rec.confidence_ceiling is ConfidenceBand.LOW
        assert rec.derived_on_release == "gb_v1_8"
        assert rec.confirmation_ask
    assert "UNCHANGED" in result.basis_chosen
    assert "not scaled, blended or re-based onto the invoice" in result.basis_chosen


def test_a_warning_is_a_disclosure_not_an_open_ask() -> None:
    """A documented, quantified, conservative shortfall caps the band; it is not a data ask."""
    result = _run_real("grundon_ewelme")
    assert result.reconciles is True
    assert result.warnings
    assert bill_reconciliation_records(result) == ()
    assert result.confidence_ceiling is ConfidenceBand.MEDIUM


def test_a_portfolio_check_would_have_passed_four_large_errors() -> None:
    """THE REASON THIS GATE IS PER SITE.

    Summing the five real sites gives +0.7% — inside even the +2% overshoot WARN. Four of the five
    sites individually breach. A portfolio gate would have reported four large errors cancelling
    as a successful validation.
    """
    inv_total = sum(v[1] for v in REAL_SITES.values())
    mod_total = sum(v[3] for v in REAL_SITES.values())
    portfolio = reconcile_bill_against_invoice(
        _evidence(
            site_id="PORTFOLIO",
            invoiced_kwh=sum(v[0] for v in REAL_SITES.values()),
            invoiced_cost_gbp=inv_total,
        ),
        modelled_kwh=sum(v[2] for v in REAL_SITES.values()),
        modelled_cost_gbp=mod_total,
    )
    assert (mod_total - inv_total) / inv_total * 100 == pytest.approx(0.65, abs=0.05)
    assert _finding(portfolio, "bill_cost_gbp").severity == "ok", "the aggregate hides everything"

    breached = [s for s in REAL_SITES if _run_real(s).confidence_ceiling is not ConfidenceBand.HIGH]
    assert len(breached) == 5, breached
    errored = [s for s in REAL_SITES if _run_real(s).reconciles is False]
    assert sorted(errored) == ["grundon_colnbrook", "grundon_tanhouse"]


def test_no_site_reaches_a_high_ceiling_on_the_real_evidence() -> None:
    """`bill_reconciliation.md` §8: "Nothing rises. Two sites are pushed down on evidence." """
    for site_id in REAL_SITES:
        assert _run_real(site_id).confidence_ceiling is not ConfidenceBand.HIGH, site_id


# =============================================================================================
# Absent inputs are absent, never zero (D45 amendment).
# =============================================================================================


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"modelled_cost_gbp": None, "modelled_kwh": 1_000_000.0}, "bill_cost_gbp"),
        ({"modelled_cost_gbp": 200_000.0, "modelled_kwh": None}, "bill_volume_kwh"),
    ],
)
def test_a_null_modelled_side_is_recorded_as_unchecked(kwargs, field) -> None:
    """A run record with `"customer_bill": null` — which every record in this repo had."""
    result = reconcile_bill_against_invoice(_evidence(), **kwargs)
    assert _finding(result, field).severity == "not_run"
    assert "NOT RUN" in _finding(result, field).message
    assert _finding(result, field) in result.limbs_not_run


def test_a_zero_invoice_is_not_divided_by() -> None:
    result = reconcile_bill_against_invoice(
        _evidence(invoiced_cost_gbp=0.0, invoiced_kwh=0.0),
        modelled_kwh=1_000.0,
        modelled_cost_gbp=1_000.0,
    )
    for f in ("bill_volume_kwh", "bill_cost_gbp"):
        assert _finding(result, f).severity == "not_run"
        assert _finding(result, f).variance_pct is None


# =============================================================================================
# The evidence file the gate consumes.
# =============================================================================================


def test_the_invoice_evidence_file_loads_for_every_validated_site() -> None:
    raw = json.loads(dpa_released(INVOICES).read_text())
    expected = (
        set(REAL_SITES)
        | {"harwell_campus_k16w304527"}
        # The Bishops Cleeve split (2026-08-15). Their key-resolution through the PRODUCTION
        # lookup is `tests/test_invoice_evidence_site_boundary.py`; here they only have to parse.
        | {"grundon_bishops_cleeve_ash_plant", "grundon_bishops_cleeve_mrf"}
    )
    assert expected <= set(raw), sorted(expected - set(raw))
    for site_id in expected:
        ev = invoice_evidence_from_mapping(site_id, raw[site_id])
        assert ev.site_id == site_id
        if not ev.invoice_available:
            assert ev.absence_reason, "an absence must state its reason"
            assert ev.invoiced_cost_gbp is None, "absent, not zero"
            continue
        assert ev.invoiced_kwh and ev.invoiced_cost_gbp
        assert len(ev.monthly_cost_gbp) == 12, site_id
        assert sum(ev.monthly_cost_gbp.values()) == pytest.approx(ev.invoiced_cost_gbp, abs=0.05)
        for o in ev.documented_omissions:
            assert o.kind in ("computed", "bounded_by_declared_uncertainty")
            assert o.source and o.gbp > 0


def test_the_overshooting_sites_carry_no_documented_omissions() -> None:
    """By design: an omission cannot explain a bill that exceeds the invoice (§6)."""
    for site_id in ("grundon_colnbrook", "grundon_tanhouse"):
        assert _real_evidence(site_id).documented_omissions == ()


# ---------------------------------------------------------------------------------------------
# The calibration base year — why both private-wire sites overshoot.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("site_id", "residual_in_model_p", "commodity_p"),
    [
        # `levies.private_wire_non_commodity_residual` and the volume-weighted commodity rate the
        # site model's own `source` string records. residual = blended − commodity, so
        # residual + commodity is the blended figure the residual was calibrated to.
        ("grundon_colnbrook", 5.0551, 8.91495),
        ("grundon_tanhouse", 13.7002, 9.36992),
    ],
)
def test_both_private_wire_residuals_were_calibrated_on_2026_prices(
    site_id, residual_in_model_p, commodity_p
) -> None:
    """§3.4. The residual's implied blended cost is the 2026 YTD figure, not the 2025 base year.

    This is the whole of Colnbrook's +7.1% overshoot and 21% of Tanhouse's +11.7%. Identified by
    NEAREST BASIS rather than an absolute tolerance, because the recorded residual was itself
    back-solved from a 2-dp-rounded blended figure: the implied value lands within 0.004 p/kWh of
    the 2026 year-to-date basis and 0.34-2.62 p/kWh from every other candidate. `blended_cost_by_
    basis` is computed from the meter tabs, so the evidence file carries the proof and the
    correction is auditable rather than asserted.
    """
    bases = json.loads(dpa_released(INVOICES).read_text())[site_id]["blended_cost_by_basis"]
    implied = residual_in_model_p + commodity_p
    distance = {k: abs(implied - v["blended_p_per_kwh"]) for k, v in bases.items()}

    assert min(distance, key=lambda k: distance[k]) == "2026_ytd", distance
    assert distance["2026_ytd"] < 0.005, "effectively exact, to the rounding of the recorded figure"
    assert distance["2025"] > 0.5, (
        f"the 2025 base year is not what the residual describes: {distance}"
    )
    assert bases["2026_ytd"]["meter_months"] < 12, (
        "and the target is a PART-YEAR 2026 window applied to a full-year 2025 volume basis"
    )
    assert bases["2025"]["meter_months"] == 12

    # Re-based on 2025-on-2025 the residual FALLS, so modelled delivered cost and therefore
    # avoided-cost value both fall. Both sites look WORSE, and that is the correct direction.
    rebased = bases["2025"]["blended_p_per_kwh"] - commodity_p
    assert rebased < residual_in_model_p, "re-basing must reduce, not raise, the residual"
