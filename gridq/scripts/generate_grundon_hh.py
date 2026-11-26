"""Generate modelled half-hourly series for the four Grundon sites (validation Gate 2).

Grundon supplied **monthly invoice totals**, not half-hourly meter exports. Under the HH mandate a
site is never underwritten without a half-hourly series, and where no meter export exists the
shape is modelled from a reference profile scaled to the site's own measured totals — standard
practice, not a degradation (`docs/validation_requirements.md` §1 Gate 2).

Because the totals are **monthly**, the seasonal shape is MEASURED (each month reconciles to its
own invoice) and only the within-month day-of-week and half-hourly shape is modelled.

Run: `python scripts/generate_grundon_hh.py`
Reads `data/sites/grundon_monthly.json` (from `scripts/extract_site_data.py`).
Writes `data/sites/hh/` + a manifest recording the basis for every site.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from gridq.ingestion.hh_synthesis import synthesise_half_hourly

REPO = Path(__file__).resolve().parent.parent
SITE_DATA = REPO / "data" / "sites"
OUT = SITE_DATA / "hh"
YEAR = 2025

# Site -> (reference profile, connection kVA cap, why this profile).
# The profile is chosen from the dominant load at the site, and the choice is recorded so it can
# be challenged: it is the single largest modelling assumption in each of these cases.
SITE_PROFILES: dict[str, tuple[str, float | None, str]] = {
    "bishops_cleeve": (
        "waste_mrf",
        250.0,  # BC MRF is the dominant meter (297 MWh of 397 MWh) and is 250 kVA
        "BC MRF (a materials recovery facility) is 75% of site grid load.",
    ),
    "tanhouse_colnbrook": (
        "waste_mrf",
        None,  # no kVA recorded for either meter
        "Colnbrook/Tanhouse are MRF + transfer-station operations behind an EfW private wire. "
        "NOTE: this key covers COLNBROOK ONLY (tab 'Colnbrook Offices, CWI, TS etc.', MPAN "
        "2000027318857). Tanhouse is the separate `tanhouse` entry below, from its own per-meter "
        "tab. The key retains its historical name because examples/real/sites/colnbrook.json "
        "already references the filename it produces.",
    ),
    # 2026-08-13 (Olly). Tanhouse was in ANNUAL_ONLY_SITES on an annual roll-up, because
    # `scripts/extract_site_data.py` never listed its per-meter tab. The tab exists — MPAN
    # 2000052173090, meter K12C03674, 12 monthly kWh AND cost rows for 2025
    # (`bill_reconciliation.md` §3.2) — so the seasonal shape is now MEASURED, not modelled, and
    # the manifest's "single highest-value data ask for this site" is closed from data in hand.
    #
    # The basis is the PURCHASED volume only: 1,541,453.4 kWh of EfW private-wire import. The
    # `Totals`-tab roll-up previously used (1,680,626 kWh) also contained 139,172.28 kWh of
    # self-consumed on-site solar — energy the customer does not buy — so pricing it as import
    # overstated the avoidable volume by 9.03%. The solar is recorded in the manifest, not deleted:
    # the site still consumes it, and representing it properly needs a PV channel the pipeline does
    # not have (D-174). See ONSITE_GENERATION_2025 below.
    "tanhouse": (
        "waste_mrf",
        None,  # no kVA figure exists for Tanhouse anywhere in the workbook
        "Tanhouse MRF + workshop + depot behind the same EfW private wire as Colnbrook. Own "
        "per-meter tab 'Tanhouse (MRF, WS, Depot)', MPAN 2000052173090, 12 measured monthly "
        "totals for 2025. PURCHASED (EfW) volume only — on-site solar excluded, see warnings.",
    ),
    "knowl_hill": (
        "waste_mrf",
        325.0,
        "Waste transfer station; grid import only (LFG self-supply is a separate flow).",
    ),
    "ewelme": (
        "workshop_depot",
        750.0,  # Ewelme Workshop, the dominant meter, sits on a 750 kVA connection
        "Ewelme Workshop is 82% of site load and is a vehicle workshop, not a sorting plant.",
    ),
}

# Sites with NO per-meter tab in the workbook, only a line in the `Totals` roll-up. They get an
# ANNUAL total, so their seasonal shape is MODELLED rather than measured — a materially weaker
# basis than the sites above, and recorded as such.
#
# EMPTY since 2026-08-13. Tanhouse was the only entry and it graduated to `SITE_PROFILES` once its
# per-meter tab was extracted. The path is kept because it is the honest representation for a site
# that genuinely has only an annual figure — but no current site does, and nothing should be put
# here without first checking the workbook actually lacks a per-meter tab. That check is what was
# missed: the tab was there the whole time.
ANNUAL_ONLY_SITES: dict[str, tuple[str, float, float | None, str]] = {}

# On-site generation that is CONSUMED at the site but NOT purchased from any counterparty, so it
# must never be priced as import — and is not part of the half-hourly series generated here.
#
# `bill_reconciliation.md` §3.3: the `'2025 Summary'` tab decomposes Tanhouse's 1,680,625.68 kWh
# into 1,541,453.4 kWh EfW (purchased) + 139,172.28 kWh solar (self-generated). The site model
# priced the whole roll-up as purchased private-wire import.
#
# **This is recorded, not netted away.** The site still consumes the solar; it simply does not pay
# a supplier for it, and a battery cannot avoid a cost that was never charged. Representing it
# properly requires a PV channel the pipeline does not have — open finding D-174: there is no
# `pv_generation_kw` series on `LoadData`, and negative net import is clipped to zero rather than
# carried as export. Until D-174 lands, the series here is the site's PURCHASED IMPORT, which is
# also the correct dispatch basis (it is the net-of-PV profile the battery actually sees), and the
# gross consumption figure is carried alongside it so nothing is silently lost.
#
# NO GENERATION PROFILE IS INVENTED. Only an annual total exists, from one cell of one tab.
ONSITE_GENERATION_2025: dict[str, dict[str, object]] = {
    "tanhouse": {
        "self_generated_kwh": 139_172.28,
        "source_column": "solar",
        "source": "'2025 Summary' tab, row 'Tanhouse (MRF, WS, Depot)', Solar (kWh) column — an "
        "ANNUAL figure only; the workbook carries no monthly or half-hourly solar breakdown.",
        "granularity": "annual_total_only",
        "blocked_by": "D-174: no pv_generation_kw channel exists and negative net import is "
        "clipped to zero, so on-site generation cannot be represented in the priced series.",
    },
}

# Meter-months known to be contaminated, from docs/grundon_harwell/02_data_reconciliation.md.
# Excluded from the SHAPE with the exclusion recorded — never silently smoothed.
ANOMALIES: dict[tuple[str, str], str] = {
    ("BC Lodge", "2025-03"): (
        "10,128 kWh against a ~150 kWh/month baseline (68x) — a catch-up or corrected bill, not "
        "March consumption. Replaced with the site's own 11-month mean for that meter."
    ),
}


def _site_monthly(site_key: str, meters: list[dict]) -> tuple[dict[str, float], list[str]]:
    """Aggregate the site's meters into one monthly series, correcting recorded anomalies."""
    monthly: dict[str, float] = defaultdict(float)
    notes: list[str] = []
    for meter in meters:
        rows = [m for m in meter.get("monthly", []) if m["month"].startswith(str(YEAR))]
        if not rows:
            continue
        key = meter["tab"]
        clean = [r for r in rows if (key, r["month"]) not in ANOMALIES]
        mean = sum(r["kwh"] for r in clean) / len(clean) if clean else 0.0
        for r in rows:
            if (key, r["month"]) in ANOMALIES:
                monthly[r["month"]] += mean
                notes.append(
                    f"{key} {r['month']}: {r['kwh']:,.0f} kWh excluded and replaced with "
                    f"{mean:,.0f} kWh (this meter's 11-month mean). "
                    f"{ANOMALIES[(key, r['month'])]}"
                )
            else:
                monthly[r["month"]] += r["kwh"]
    return dict(monthly), notes


def main() -> None:
    data = json.loads((SITE_DATA / "grundon_monthly.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    for site_key, (profile_id, kva, rationale) in SITE_PROFILES.items():
        monthly, notes = _site_monthly(site_key, data[site_key]["meters"])
        if len(monthly) != 12:
            print(
                f"  !! {site_key}: only {len(monthly)} months of {YEAR} — SKIPPED (never scaled up)"
            )
            manifest.append({"site": site_key, "status": "refused", "months_present": len(monthly)})
            continue

        result = synthesise_half_hourly(
            site_id=f"grundon_{site_key}",
            profile=profile_id,
            year=YEAR,
            out_dir=OUT,
            monthly_kwh=monthly,
            peak_kw_cap=kva,
        )
        measured = sum(monthly.values())
        print(
            f"  {site_key:<20} {result.total_kwh:>12,.0f} kWh "
            f"(measured {measured:,.0f}, delta {result.total_kwh - measured:+.3f})  "
            f"peak {result.peak_kw:>7,.1f} kW  profile={profile_id}"
        )
        for n in notes:
            print(f"      anomaly: {n}")

        entry: dict = {
            "site": site_key,
            "status": "modelled",
            "file": str(result.path.relative_to(REPO)),
            "profile": result.profile.profile_id,
            "profile_source": result.profile.source.value,
            "profile_rationale": rationale,
            "scaling_basis": result.scaling_basis,
            "measured_components": list(result.measured_components),
            "total_kwh": result.total_kwh,
            "measured_total_kwh": round(measured, 3),
            "peak_kw": result.peak_kw,
            "connection_kva_cap": kva,
            "confidence_ceiling": result.admission().confidence_ceiling,
            "anomaly_corrections": notes,
            "warnings": list(result.warnings),
        }
        gen = ONSITE_GENERATION_2025.get(site_key)
        if gen is not None:
            self_gen = float(gen["self_generated_kwh"])  # type: ignore[arg-type]
            gross = measured + self_gen
            entry["series_basis"] = "PURCHASED IMPORT ONLY"
            entry["onsite_generation_2025"] = {
                **gen,
                "purchased_import_kwh": round(measured, 2),
                "gross_consumption_kwh": round(gross, 2),
                "self_generated_pct_of_purchased": round(100 * self_gen / measured, 3),
                "self_generated_pct_of_gross": round(100 * self_gen / gross, 3),
            }
            entry["warnings"].append(
                f"PURCHASED IMPORT ONLY. This series is {measured:,.1f} kWh of purchased energy. "
                f"The site also consumes {self_gen:,.2f} kWh/yr of self-generated "
                f"{gen['source_column']} ({100 * self_gen / gross:.2f}% of its "
                f"{gross:,.2f} kWh gross consumption), which is NOT in this series because the "
                f"customer does not buy it and a battery cannot avoid a cost never charged. "
                f"That volume is recorded, not discarded — see `onsite_generation_2025`. "
                f"REPRESENTING IT IS BLOCKED: {gen['blocked_by']} No generation profile has been "
                f"invented; only an annual total exists ({gen['source']})."
            )
        manifest.append(entry)

    for site_key, (profile_id, annual, kva, source_ref) in ANNUAL_ONLY_SITES.items():
        result = synthesise_half_hourly(
            site_id=f"grundon_{site_key}",
            profile=profile_id,
            year=YEAR,
            out_dir=OUT,
            annual_kwh=annual,
            peak_kw_cap=kva,
        )
        print(
            f"  {site_key:<20} {result.total_kwh:>12,.0f} kWh "
            f"(measured {annual:,.0f}, delta {result.total_kwh - annual:+.3f})  "
            f"peak {result.peak_kw:>7,.1f} kW  profile={profile_id}  [SEASON MODELLED]"
        )
        manifest.append(
            {
                "site": site_key,
                "status": "modelled",
                "file": str(result.path.relative_to(REPO)),
                "profile": result.profile.profile_id,
                "profile_source": result.profile.source.value,
                "profile_rationale": source_ref,
                "scaling_basis": result.scaling_basis,
                "measured_components": list(result.measured_components),
                "total_kwh": result.total_kwh,
                "measured_total_kwh": annual,
                "peak_kw": result.peak_kw,
                "connection_kva_cap": kva,
                "confidence_ceiling": result.admission().confidence_ceiling,
                "anomaly_corrections": [],
                "warnings": list(result.warnings),
            }
        )

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nManifest: {(OUT / 'manifest.json').relative_to(REPO)}")
    measured_season = sum(
        1 for m in manifest if "seasonal" in " ".join(m.get("measured_components", []))
    )
    print(
        f"{len(manifest)} series: {measured_season} with a MEASURED season (monthly invoices), "
        f"{len(manifest) - measured_season} with a MODELLED season (annual total only)."
    )


if __name__ == "__main__":
    main()
