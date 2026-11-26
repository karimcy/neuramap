"""Build the INVOICE-ANCHORED half-hourly series: a site's OWN metered shape, scaled to its
own reconciled annual invoice total.

This is validation Gate 2's second limb — *"a reference profile scaled to the site's own
reconciled totals"* (`CLAUDE.md` §2, `docs/validation_requirements.md`) — with one substitution
that makes it strictly stronger than the practice it replaces: **the reference profile is the
site's own meter.** The retired basis borrowed an engineering archetype (`waste_mrf`,
`workshop_depot`) whose within-day and day-of-week shape was an assumption with no published
curve behind it. Here the shape is 17,518 measured half-hours from this site's own MPAN. Only the
**magnitude** is modelled, and only because the metered window and the invoiced window disagree
about how much energy the site consumed.

**What is measured and what is modelled, stated once.**

- MEASURED: the within-day shape, the day-of-week pattern, the seasonal pattern, and which
  half-hours carry load — all of it this site's own meter, 2025-08-13 to 2026-08-12.
- MODELLED: the **level**. Every interval is multiplied by one constant,
  ``invoiced_annual_kwh / metered_annual_kwh``. Nothing is reshaped, redistributed or clipped.
- CONSEQUENCE, and it is the thing a reader must not miss: **the peak is modelled too.** A
  constant multiplier scales the maximum half-hour by exactly the same factor as the mean, so the
  peak on an invoice-anchored series is an arithmetic product, not an observation. Everything
  peak-dependent inherits that — the required MIC, Capacity Market de-rated capacity, DUoS
  red-band exposure, the behind-the-meter discharge cap ``min(power, load)`` and therefore the
  battery power rating. The site JSON that consumes one of these files must carry a
  `DerivedAssumption` on ``load_data.max_import_kw`` at a LOW confidence ceiling saying so.

**Why the two sites here need it, and why the other three do not.**

``grundon_ewelme`` — the metered series is ONE of the site's two MPANs (Ewelme MRF, 2000027378439,
200 kVA). It reconciles well against its OWN invoice line (118,775 metered vs 123,421 invoiced for
that meter, 96.2%), so the meter is sound; what it does not cover is the Ewelme Workshop MPAN
(2000055901639, 750 kVA, 570,641 kWh, 82% of site load), for which no half-hourly export exists.
Anchoring to the whole-site invoice therefore stretches an MRF's shape by 5.84x to stand in for a
**vehicle workshop, a different business type on a different connection**. That is a real
modelling leap and it is declared, not hidden: it is the same objection that retired the literature
archetypes, now carried at a 5.84x scale factor.

``grundon_knowl_hill`` — the metered series covers the whole supply (one MPAN) but reads 2.41x the
2025 invoice, because grid import at this site is RISING fast as the landfill-gas plant declines
(39 MWh 2024 -> 46 MWh 2025 -> 108 MWh measured Aug-2025 to Aug-2026). Anchoring to the 2025
invoice scales the measured year DOWN by 0.42x — it prices a year the site has already left.

**The period mismatch is real and is not resolved by scaling.** The shape covers 2025-08-13 to
2026-08-12; the invoice total is calendar 2025. Scaling reconciles the *energy*, never the
*calendar*: a series anchored this way reproduces the invoiced annual total on a window the invoice
never described. Declared here, and again in each site JSON's scaling record.

Idempotent: re-running replaces only this script's own manifest entries and leaves every other
entry — including the METERED entries `scripts/convert_grundon_hh.py` writes — untouched.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HH = REPO / "data" / "sites" / "hh"
MANIFEST = HH / "manifest.json"
INVOICES = REPO / "data" / "sites" / "grundon_invoices_2025.json"

#: The invoice-anchored variants to build.
#:
#: ``expected_invoiced_kwh`` is a DRIFT GUARD, not a second source of truth. The annual total is
#: read from `data/sites/grundon_invoices_2025.json` at run time so this script stays correct if
#: that file is re-split per meter; the pinned figure is what it read on 2026-08-15, and a mismatch
#: HALTS rather than silently re-anchoring a series to a total nobody reviewed. Two agents are live
#: in this tree and that file is owned elsewhere.
VARIANTS: dict[str, dict[str, object]] = {
    "grundon_ewelme": {
        "metered": "grundon_ewelme_metered_20250813_20260812_2f21a38ab37f9c8d.csv",
        "invoice_key": "grundon_ewelme",
        "expected_invoiced_kwh": 694062.0,
        "anchor": (
            "whole-site invoiced total, calendar 2025 — both Ewelme MPANs: Workshop "
            "2000055901639 (570,641.2 kWh, 750 kVA) + MRF 2000027378439 (123,420.8 kWh, "
            "200 kVA). The metered shape is the MRF meter alone."
        ),
        "shape_covers": (
            "Ewelme MRF only (MPAN 2000027378439). The Workshop MPAN, 82% of site load and a "
            "DIFFERENT BUSINESS TYPE on a DIFFERENT CONNECTION, has no half-hourly export and is "
            "represented here by the MRF's shape."
        ),
    },
    "grundon_knowl_hill": {
        "metered": "grundon_knowl_hill_metered_20250813_20260812_80e2ea9c456527af.csv",
        "invoice_key": "grundon_knowl_hill",
        "expected_invoiced_kwh": 45732.3,
        "anchor": "invoiced total, calendar 2025 — MPAN 2000053471776 (the whole supply).",
        "shape_covers": (
            "the whole supply (one MPAN). The shape needs no stand-in; only the LEVEL is "
            "re-anchored, from the measured Aug-2025/Aug-2026 year back onto the calendar-2025 "
            "invoice, which is a year this rising load has already left."
        ),
    },
}


def _read_series(path: Path) -> list[tuple[datetime, float]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [(datetime.fromisoformat(r["timestamp"]), float(r["import_kwh"])) for r in rows]


def main() -> int:
    invoices = json.loads(INVOICES.read_text())
    manifest: list[dict[str, object]] = json.loads(MANIFEST.read_text())
    report: list[dict[str, object]] = []

    for site_id, spec in VARIANTS.items():
        src = HH / str(spec["metered"])
        series = _read_series(src)
        metered_total = sum(kwh for _, kwh in series)

        invoiced = float(invoices[str(spec["invoice_key"])]["invoiced_kwh"])
        expected = float(spec["expected_invoiced_kwh"])  # type: ignore[arg-type]
        if abs(invoiced - expected) > 0.05:
            raise SystemExit(
                f"{site_id}: invoiced_kwh in {INVOICES.name} is {invoiced:,.1f} but this script "
                f"was written against {expected:,.1f}. The invoice basis moved — re-review the "
                "anchor and the site JSON before regenerating, do not just update the constant."
            )

        factor = invoiced / metered_total
        scaled = [(ts, kwh * factor) for ts, kwh in series]
        payload = (
            "timestamp,import_kwh\n"
            + "\n".join(f"{ts:%Y-%m-%d %H:%M:%S},{kwh:.6f}" for ts, kwh in scaled)
            + "\n"
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        stamps = [ts for ts, _ in scaled]
        period = f"{stamps[0]:%Y%m%d}_{stamps[-1]:%Y%m%d}"
        out = HH / f"{site_id}_invoice_anchored_{period}_{digest}.csv"
        out.write_text(payload)

        total = sum(kwh for _, kwh in scaled)
        peak = max(kwh for _, kwh in scaled) * 2.0
        metered_peak = max(kwh for _, kwh in series) * 2.0
        key = f"{site_id}_invoice_anchored"
        entry: dict[str, object] = {
            "site": key,
            # The SiteModel id both bases share. Two bases, one site — they are distinguished by
            # `basis_variant` and by their inputs (annual_kwh / max_import_kw / file_ref), which
            # is what makes their `input_hash` differ.
            "site_id": site_id,
            "basis_variant": "invoice_anchored",
            # MODELLED, not metered: the shape is measured but the LEVEL is an arithmetic product.
            "status": "modelled",
            "file": str(out.relative_to(REPO)),
            "sha256_16": digest,
            "intervals": len(scaled),
            "total_kwh": round(total, 3),
            "peak_kw": round(peak, 3),
            "first": stamps[0].isoformat(sep=" "),
            "last": stamps[-1].isoformat(sep=" "),
            "scaled_from": str(src.relative_to(REPO)),
            "scaled_from_total_kwh": round(metered_total, 3),
            "scaled_from_peak_kw": round(metered_peak, 3),
            "scale_factor": round(factor, 9),
            "scaled_to_total_kwh": round(invoiced, 3),
            "scaled_to_source": (
                f"data/sites/grundon_invoices_2025.json['{spec['invoice_key']}'].invoiced_kwh — "
                f"{spec['anchor']}"
            ),
            "shape_covers": spec["shape_covers"],
            "profile_rationale": (
                "GATE 2, SECOND LIMB: a reference profile scaled to the site's own reconciled "
                "totals — where the reference profile is THIS SITE'S OWN METER, not a literature "
                "archetype. The within-day shape, day-of-week pattern and seasonality are "
                "MEASURED (17,518 half-hours, 2025-08-13 to 2026-08-12). Only the level is "
                "modelled."
            ),
            # Same field name and vocabulary as the manifest's pre-existing MODELLED entries, so a
            # reader (and `tests/test_grundon_site_data_basis.py`) can compare bases like for like.
            "measured_components": (
                "within-day shape, day-of-week pattern and seasonal (month-to-month) variation — "
                "all measured from this site's own meter, because multiplying every interval by "
                "one constant preserves every proportion in the series. MODELLED: the LEVEL, and "
                "therefore the peak, which is an arithmetic product rather than an observation."
            ),
            "modelled_peak_warning": (
                f"THE PEAK IS MODELLED. A constant multiplier scales the maximum half-hour by the "
                f"same {factor:.6f} as the mean, so {peak:,.3f} kW is an arithmetic product of a "
                f"{metered_peak:,.3f} kW measurement, not a measurement. Required MIC, CM "
                "de-rated capacity, DUoS red-band exposure and the behind-the-meter discharge cap "
                "min(power, load) all inherit that. Confidence on max_import_kw is capped LOW."
            ),
            "period_mismatch": (
                "The shape covers 2025-08-13 to 2026-08-12; the anchor total is CALENDAR 2025. "
                "Scaling reconciles the energy, never the calendar: this series reproduces an "
                "invoiced annual total on a window the invoice never described."
            ),
            "written_by": "scripts/scale_grundon_hh.py",
        }
        manifest = [m for m in manifest if m.get("site") != key]
        manifest.append(entry)
        report.append(entry)

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"{'site':38} {'factor':>10} {'annual kWh':>13} {'peak kW':>10}  file")
    print("-" * 118)
    for e in report:
        print(
            f"{e['site']:38} {e['scale_factor']:>10.6f} {e['total_kwh']:>13,.1f} "
            f"{e['peak_kw']:>10,.2f}  {Path(str(e['file'])).name}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
