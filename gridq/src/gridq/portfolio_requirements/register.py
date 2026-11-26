"""Register the five sites as Projects on the underwriting platform.

A "Project" on the platform is a saved run under ``$GRIDQ_RUN_STORE`` (default ``<repo>/runs``),
keyed by a deterministic ``quote_id`` — see :mod:`gridq.app.store`. This module builds a real
:class:`~gridq.schemas.SiteModel` for each site from the metered/invoiced facts established in
``docs/grundon_harwell/01_data_foundation.md``, stamps it through
:func:`~gridq.provenance.run_context.build_run_context` so the id is a pure function of the inputs,
and saves it via :func:`gridq.app.store.save_run`.

Two deliberate constraints:

* **Quote mode is ``indicative``, never ``underwritten``.** These numbers rest on derived load
  shapes for the four Grundon sites (no half-hourly data has been supplied) and on three unresolved
  blocking verifications. Presenting them as underwritten would be false.
* **Every warning is carried onto the record.** The data-quality anomalies and the blocking
  verification travel with the Project rather than living only in a document.

Run it with::

    python -m gridq.portfolio_requirements.register
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date

from gridq.app.store import list_runs, save_run
from gridq.governance.dpa_guard import DpaEvidenceMissing, authorise_store
from gridq.portfolio_requirements.sites import (
    SITE_ORDER,
    cases_for,
    evaluate_site,
)
from gridq.provenance.run_context import build_run_context
from gridq.schemas import (
    Confidence,
    ConnectionDetails,
    ConsentState,
    ConsentStatus,
    Counterparty,
    CreditStatus,
    DataTier,
    LoadDataSummary,
    PhysicalGateStatus,
    QuoteMode,
    QuoteRecord,
    SiteModel,
    TariffProvenance,
    TariffStack,
    TelemetryEvent,
    VoltageLevel,
)
from gridq.tariffs.basis import UNSTATED_PROVENANCE, derived_vat_basis, h01_vat_basis_record

ASSUMPTION_VERSION = "pr_sites_v1"
SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True)
class SiteFacts:
    """Metered / invoiced facts for one site — 2025 base year."""

    name: str
    postcode: str
    annual_kwh: float
    max_import_kw: float
    mic_kw: float
    import_mpan: str | None
    export_mpan: str | None
    day_rate: float
    night_rate: float | None
    blended: float
    warnings: tuple[str, ...]


# Sources in docs/grundon_harwell/01_data_foundation.md.
SITE_FACTS: dict[str, SiteFacts] = {
    "bishops_cleeve": SiteFacts(
        name="Bishops Cleeve (Grundon)",
        postcode="GL52 7EG",
        annual_kwh=550_330.0,
        max_import_kw=250.0,
        mic_kw=480.0,  # 250 kVA MRF + 230 kVA Import
        import_mpan="1460001884093",
        export_mpan="1460001527004",
        day_rate=0.09827,
        night_rate=0.06315,
        blended=0.2658,
        warnings=(
            "NO half-hourly data supplied — load shape is DERIVED from monthly invoice totals plus "
            "plant-type engineering estimates. Every shape-dependent figure is an assumption.",
            "Client brief states the site is off-grid; the data contradicts this. BC Import is a "
            "real electrical meter (MPAN 1460001527004, 230 kVA) and 348,631 kWh/yr (63% of site "
            "load) is grid-supplied.",
            "LFG gensets generated 6,902,822 kWh in 2025 vs 8,614,011 kWh in 2024 — a -19.9% "
            "year-on-year decline. Decline curve is an assumption pending gas-field projections.",
            "BLOCKING: the uniform 0.49 p/kWh 'Feed in tariff' line is unconfirmed — FiT consumer "
            "levy or actual export price? If the latter, re-contracting 6.7 GWh/yr is worth "
            "~GBP400-435k/yr.",
            "BC Flare shares MPAN 1460001884093 with BC MRF — do not double-count.",
            "BC Self-Supply 2026-03 export to grid is -29,676 kWh (negative) — a meter correction, "
            "excluded from trend fitting.",
        ),
    ),
    "tanhouse_colnbrook": SiteFacts(
        name="Tanhouse & Colnbrook (Grundon)",
        postcode="SL3 0HQ",
        annual_kwh=4_712_985.0,
        max_import_kw=700.0,
        mic_kw=1_000.0,
        import_mpan="2000052173090",
        export_mpan=None,
        day_rate=0.09876,
        night_rate=0.06351,
        blended=0.2307,
        warnings=(
            "NO half-hourly data supplied — load shape is DERIVED from monthly invoice totals.",
            "100% non-grid: supply is EfW private wire (4,573,813 kWh) plus existing solar "
            "(139,172 kWh). There is no grid connection, so no Capacity Market, frequency "
            "response, Balancing Mechanism or wholesale access.",
            "Colnbrook runs at 13.97 p/kWh delivered — the cheapest tariff in the estate. Avoided-"
            "cost value per kWh is roughly half that of a grid-supplied site.",
            "NO BATTERY PROPOSED in any case: a flat private wire offers no spread to shift.",
            "BLOCKING: the EfW private-wire contract must be confirmed to permit displacement, "
            "with no take-or-pay floor. If take-or-pay, solar value is GBP0 and the site drops "
            "out.",
            "Financed on the merchant tier, not contracted — avoided-cost revenue against an "
            "unverified private-wire contract is not contracted revenue.",
        ),
    ),
    "knowl_hill": SiteFacts(
        name="Knowl Hill (Grundon)",
        postcode="RG10 9YE",
        annual_kwh=535_052.0,
        max_import_kw=200.0,
        mic_kw=325.0,
        import_mpan=None,
        export_mpan=None,
        day_rate=0.1541,
        night_rate=None,
        blended=0.3302,
        warnings=(
            "NO half-hourly data supplied — load shape is DERIVED from monthly invoice totals.",
            "LFG supplies 489,320 kWh (91%) of the 535,052 kWh site load and is failing. Grid "
            "import series (MWh): 2021 607 (hydroclave, since decommissioned) / 2022 215 / "
            "2023 72 / 2024 39 / 2025 46 / 2026 ~148 annualised.",
            "Tariff is 15.41 p/kWh day with NO night rate — battery arbitrage value is GBP0 on the "
            "current tariff. The battery is optional, not the case.",
            "NOT INVESTABLE on Central (~0% levered) under the agreed 15-year convention. Framed "
            "as solar-led cost avoidance, not an investment case.",
            "No MPAN recorded in the source workbook; 325 kVA connection is stated.",
            "Faster LFG failure IMPROVES the solar case — solar only earns when displacing grid "
            "import, since LFG is free fuel. Stated openly rather than buried.",
        ),
    ),
    "ewelme": SiteFacts(
        name="Ewelme MRF & Workshop (Grundon)",
        postcode="OX10 6HQ",
        annual_kwh=694_062.0,
        max_import_kw=200.0,
        mic_kw=950.0,  # 750 kVA Workshop + 200 kVA MRF
        import_mpan="2000055901639",
        export_mpan=None,
        day_rate=0.1072,
        night_rate=0.0664,
        blended=0.2876,
        warnings=(
            "NO half-hourly data supplied — load shape is DERIVED from monthly invoice totals.",
            "Ewelme Workshop holds a 750 kVA connection while drawing ~65 kW average — 8.7% "
            "utilisation. This stranded connection (the failed aerosol plant) is the estate's best "
            "asset and the whole investment case.",
            "BLOCKING: confirm the connection agreement's MIC was not de-rated or terminated after "
            "the aerosol plant failed. If de-rated, the merchant stack falls away and site fixed "
            "capex reverts toward GBP250k.",
            "Solar was connected within the last month — there is no meaningful generation "
            "history. "
            "Capacity plus an irradiance assumption is used instead.",
            "Solar self-consumption of 55% requires day-weighted workshop load — must be checked "
            "against the new solar meter's first months.",
        ),
    ),
    "harwell": SiteFacts(
        name="Harwell Science & Innovation Campus",
        postcode="OX11 0QX",
        annual_kwh=908_590.0,
        max_import_kw=213.8,
        mic_kw=300.0,
        import_mpan="2000056536831",
        export_mpan=None,
        day_rate=0.24,
        night_rate=0.14,
        blended=0.24,
        warnings=(
            "REAL half-hourly data: 758 days, 2024-04-01 to 2026-05-01, ZERO missing intervals. "
            "The only site of the five with measured shape.",
            "NO tariff data supplied. The avoidable rate is ASSUMED at 24 p/kWh Central "
            "(20p Pessimistic / 27p Optimistic) and classified Stretch until an invoice is "
            "obtained. One bill is the cheapest de-risking action on this site.",
            "POWEN's solar proposal assumes 426,000 kWh/yr consumption; the meter shows 908,590 "
            "kWh/yr — 2.1x higher. Re-based, Option 2 self-consumption is ~85%, not the 50% POWEN "
            "state. This flips the sizing recommendation to Option 2.",
            "POWEN's table mislabels kWp as MWp ('PV Capacity (MWp) 87.20 / 259.42').",
            "Load factor is 43% — flat, high-baseload. The worst shape for peak-shaving value; the "
            "peak is only 2.3x mean.",
            "Private campus network: Capacity Market / frequency response / Balancing Mechanism "
            "access and export rights may be restricted. Services are GATED until the campus "
            "connection agreement is seen.",
        ),
    ),
}


def build_site_model(site_key: str) -> SiteModel:
    """A real ``SiteModel`` from the site's metered and invoiced facts."""
    f = SITE_FACTS[site_key]
    cases = cases_for(site_key)
    is_harwell = site_key == "harwell"

    rates: dict[str, float] = {"day": f.day_rate}
    if f.night_rate is not None:
        rates["night"] = f.night_rate

    return SiteModel(
        site_id=site_key,
        site_name=f.name,
        postcode=f.postcode,
        country="GB",
        # Only Harwell has measured half-hourly data; the Grundon sites are invoice-derived.
        data_tier=(
            DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
            if is_harwell
            else DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
        ),
        load_data=LoadDataSummary(
            annual_kwh=f.annual_kwh,
            half_hourly_file_ref=(
                "examples/real/Harwell/Meter_Data_Interval_Meter_K16W304527.XLSX"
                if is_harwell
                else None
            ),
            half_hourly_rows=36_384 if is_harwell else None,
            start_date=date(2024, 4, 1) if is_harwell else None,
            end_date=date(2026, 5, 1) if is_harwell else None,
            missing_intervals=0 if is_harwell else None,
            max_import_kw=f.max_import_kw,
            max_export_kw=None,
            validation_warnings=list(f.warnings),
        ),
        annual_consumption_kwh=f.annual_kwh,
        tariff_stack=TariffStack(
            supplier="per invoice data (Grundon estate workbook)" if not is_harwell else "unknown",
            tariff_name=(
                "Commodity day/night from the estate tariff sheet; delivered blend from invoices"
                if not is_harwell
                else "ASSUMED science-campus I&C — no tariff data supplied"
            ),
            import_rates=rates,
            export_rates={},
            source="invoice_derived" if not is_harwell else "assumed_pending_invoice",
            confidence=Confidence.MEDIUM if not is_harwell else Confidence.LOW,
            # WP-UW-07: nothing in the estate workbook states VAT, so the basis is derived under
            # H-01 and recorded below. Commodity is the estate's own invoiced day/night rate except
            # at Harwell, where no tariff data was supplied; no network or levy line is carried.
            basis=derived_vat_basis(),
            provenance=(
                TariffProvenance(commodity="own_invoice", network="assumed", levies="assumed")
                if not is_harwell
                else UNSTATED_PROVENANCE
            ),
        ),
        derived_assumptions=[
            h01_vat_basis_record(
                subject=(
                    "the estate tariff sheet and invoices" if not is_harwell else "this record"
                )
            )
        ],
        connection=ConnectionDetails(
            mic_kw=f.mic_kw,
            mec_kw=None,
            asc_kw=None,
            voltage_level=VoltageLevel.LV,
            anm_or_curtailment_notes=(
                "Connection capacity from the kVA figures recorded in the estate workbook. "
                "MIC/MEC/ASC NOT confirmed with the DNO — a real data ask remains."
            ),
        ),
        consents=ConsentState(
            g98=ConsentStatus.NOT_STARTED,
            g99=ConsentStatus.NOT_STARTED,
            g100=ConsentStatus.NOT_STARTED,
            export_mpan=f.export_mpan,
            dno_application_state=ConsentStatus.NOT_STARTED,
            supplier_consent_state=ConsentStatus.NOT_STARTED,
            notes=(
                "No consents obtained or claimed. This is a pre-diligence indicative assessment; "
                "no DNO, supplier or aggregator evidence exists."
            ),
        ),
        physical_gates=PhysicalGateStatus(
            footprint_available=None,
            fire_separation_ok=None,
            access_ok=None,
            notes="Not surveyed — footprint, fire separation and access all unassessed.",
        ),
        counterparty=Counterparty(
            legal_name=(
                "Grundon Waste Management Limited"
                if site_key != "harwell"
                else "Harwell Science & Innovation Campus"
            ),
            company_number=None,
            sector="waste_management" if site_key != "harwell" else "research_campus",
            credit_status=CreditStatus.UNKNOWN,
            expected_tenure_years=15.0,
            notes="No credit assessment performed; company number pending for lookup.",
        ),
        source_references=[
            "examples/real/Grundon/NEW Electricity Usage and Costs.xlsx (invoice-derived, monthly)"
            if site_key != "harwell"
            else "examples/real/Harwell/Meter_Data_Interval_Meter_K16W304527.XLSX (half-hourly)",
            "docs/grundon_harwell/01_data_foundation.md",
            f"gridq.portfolio_requirements.sites — {len(cases)} cases "
            "(Pessimistic/Central/Optimistic)",
        ],
        missing_data=[
            "half_hourly_meter_data" if site_key != "harwell" else "tariff_stack_detail",
            "tariff_decomposition_network_levies_capacity",
            "dno_confirmed_connection_capacity",
            "site_survey",
        ],
    )


def register_all(*, owner_id: str | None = None, dry_run: bool = False) -> list[str]:
    """Build and save a Project per site. Returns the quote ids."""
    ids: list[str] = []
    for site_key, label in SITE_ORDER:
        site = build_site_model(site_key)
        ctx = build_run_context(
            site,
            assumption_version=ASSUMPTION_VERSION,
            schema_version=SCHEMA_VERSION,
            warnings=list(site.load_data.validation_warnings),
        )
        central = next((c for c in cases_for(site_key) if c.case == "Central"), None)
        proposed = central is not None and central.proposed
        r = evaluate_site(central) if proposed and central is not None else None

        quote = QuoteRecord(
            quote_id=ctx.run_id,
            # INDICATIVE, never underwritten: derived shapes + unresolved blocking verifications.
            quote_mode=QuoteMode.INDICATIVE,
            site_id=site_key,
            input_hash=ctx.input_hash,
            assumption_version=ASSUMPTION_VERSION,
            code_version=ctx.code_version,
            data_tier=site.data_tier,
            warnings=list(site.load_data.validation_warnings),
        )
        note = (
            f"{label} — Central case: "
            + (
                f"unlevered {r.unlevered_irr * 100:.2f}% / levered {r.levered_irr * 100:.2f}%, "
                f"gearing {r.gearing * 100:.0f}% (bound by {r.debt_bound_by}), "
                f"DSCR {r.dscr_avg:.2f}x"
                if r is not None and r.unlevered_irr is not None and r.levered_irr is not None
                else "no proposal"
            )
            + ". Indicative pre-diligence only; artefacts in deliverables/."
        )
        telemetry = TelemetryEvent(
            event_id=f"{ctx.run_id}-registered",
            quote_id=ctx.run_id,
            event_type="project_registered",
            data_tier=site.data_tier,
            contributor="Olly",
            missing_data_count=len(site.missing_data),
            notes=note,
        )
        if not dry_run:
            # Customer sites: stored only under the customer's DPA evidence (WP-GRC-05).
            permit = authorise_store(site.site_id)
            save_run(quote, site, telemetry, dpa=permit, owner_id=owner_id)
        ids.append(ctx.run_id)
        print(f"  {'(dry) ' if dry_run else ''}{site_key:22s} -> {ctx.run_id}")
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register the five sites as platform Projects.")
    ap.add_argument("--owner", default=None, help="owner id to scope the runs to")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print("Registering Projects (quote_mode=indicative):")
    try:
        ids = register_all(owner_id=args.owner, dry_run=args.dry_run)
    except DpaEvidenceMissing as exc:
        print(f"refused (WP-GRC-05 DPA guard): {exc}")
        return 3
    if not args.dry_run:
        total = len(list_runs())
        print(f"\n{len(ids)} Projects registered; {total} runs now in the store.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
