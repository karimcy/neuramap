"""Convert the Grundon half-hourly meter exports into the pipeline's HH CSV format.

Input: `examples/real/Grundon/*.csv` — read-only evidence, never edited. These are TEAM/Stark
portal "Meter Sequential - 366 Day" exports: six metadata rows, a header row, then ONE ROW PER DAY
carrying 48 half-hourly kWh columns.

Output: `data/sites/hh/<site>_metered_<period>_<hash>.csv` with the two columns the ingestion layer
reads, `timestamp,import_kwh`, plus a manifest entry.

Three conversion decisions carry real money, so each is stated rather than assumed.

**1. The columns are period-ENDING; the pipeline is period-STARTING.**
The export labels its columns ``00:30, 01:00, … 23:30, 00:00`` — "00:30" is the energy consumed in
the interval that ENDS at 00:30, i.e. 00:00–00:30. The pipeline's own files (see any existing
`data/sites/hh/*.csv`) start at ``00:00``. So every column maps to ``label − 30 minutes``, and the
final ``00:00`` column belongs to **23:30 of the same day**, not to midnight of the next.
Using the labels as-is would shift every reading 30 minutes late and push one interval into the
following day. That is not cosmetic: it moves consumption across the 16:00 and 19:00 DUoS red-band
boundaries, and DUOS_RED_BAND_AVOIDANCE is one of only two priced-floor streams.

**2. `-` means ABSENT, never zero (D45).**
The exports write ``-`` where no reading exists. A zero and an unknown are different facts, and the
one thing D45 forbids above all is letting an absence read as a measured zero — here it would
understate consumption and so understate the avoided cost the whole case rests on. Absent intervals
are DROPPED (leaving a gap the ingestion layer's completeness check can see and report), never
written as 0.

**3. The final day is incomplete and is dropped whole.**
The reports were run at 10:03 on 14/08/26, so 13/08/26 is partial — Bishops Cleeve MRF has 46 of its
48 intervals as `-`. A part-day would drag the daily profile and the peak-day statistics without
being visibly wrong, so the last day is excluded entirely and the exclusion is reported.

**A limitation this converter cannot fix, recorded because it is invisible otherwise.** The export
format is a fixed 48 columns per day, so it cannot represent the 50 half-hours of the autumn
clock-change day. Two intervals on 26/10/25 are unrepresentable and absent from the source. The
spring day (29/03/26) correctly carries 46. Timestamps are written as naive Europe/London local
time, matching the existing files; the autumn hour is therefore under-counted by up to two intervals
out of 17,568, which is ~0.01% of the year and is declared rather than silently interpolated.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "examples" / "real" / "Grundon"
OUT = REPO / "data" / "sites" / "hh"
MANIFEST = OUT / "manifest.json"

#: site_id -> the meter files whose COINCIDENT half-hourly sum is that site's import.
#:
#: Bishops Cleeve is TWO sites, not one. Ash Plant and MRF are separate supplies that run
#: concurrently 91.7% of the time, which is what rules out a parent/child reading. The third
#: Bishops Cleeve meter ("Import", 22,379 kWh) is 92.4% ZERO with a 169 kW spike — a standby or
#: top-up supply, not a site load — and is deliberately excluded from both; see `EXCLUDED_METERS`.
SITES: dict[str, list[str]] = {
    "grundon_bishops_cleeve_ash_plant": ["Bishops Cleeve Ash Plant.csv"],
    "grundon_bishops_cleeve_mrf": ["Bishops Cleeve MRF.csv"],
    "grundon_knowl_hill": ["Knowl Hill Import.csv"],
    "grundon_ewelme": ["Ewelme MRF.csv"],
    "grundon_tanhouse": ["Tanhouse PW.csv"],
}

#: Files present in the source directory that are deliberately NOT used, and why. Listing them is
#: the point: a reader must be able to see that nothing was quietly ignored.
EXCLUDED_METERS: dict[str, str] = {
    "Bishops Cleeve Import.csv": (
        "92.4% zero with a 169 kW spike — a standby/top-up supply, not a site load. Assigning "
        "it to either Bishops Cleeve site would attribute another site's demand to that profile."
    ),
    "Bishops Cleeve Export.csv": (
        "BYTE-IDENTICAL to 'Bishops Cleeve Ash Plant.csv' (same MPAN 1420398100000, same reporting "
        "set). The portal ran the import report twice; this is not export data. Using it would "
        "double-count the Ash Plant load."
    ),
    "Knowl Hill Export.csv": (
        "BYTE-IDENTICAL to 'Knowl Hill Import.csv' (same MPAN 2000053471776). Not export data."
    ),
}

ABSENT = "-"


def read_meter(path: Path) -> tuple[dict[datetime, float], dict[str, object]]:
    """Parse one export into {period_start: kWh}, plus the facts needed to declare what happened."""
    rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "Company Name" and len(r) > 8)
    labels = rows[hdr][8 : 8 + 48]
    mpan = ""
    series: dict[datetime, float] = {}
    absent = 0
    unparsed: Counter[str] = Counter()
    days: set[datetime] = set()

    for r in rows[hdr + 1 :]:
        if not r or len(r) < 9 or not r[7]:
            continue
        day = datetime.strptime(r[7], "%d/%m/%y")
        days.add(day)
        mpan = mpan or r[3]
        for label, cell in zip(labels, r[8 : 8 + 48], strict=False):
            cell = cell.strip()
            if cell == ABSENT or cell == "":
                absent += 1
                continue
            try:
                kwh = float(cell)
            except ValueError:
                unparsed[cell] += 1  # counted and reported, never coerced
                continue
            # Period-ENDING label -> period-STARTING timestamp. "00:00" is the day's LAST interval
            # (23:30-24:00), so it maps to 23:30 of the SAME day, not midnight of the next.
            hh, mm = (int(x) for x in label.split(":"))
            end = (
                day + timedelta(days=1)
                if (hh == 0 and mm == 0)
                else day + timedelta(hours=hh, minutes=mm)
            )
            series[end - timedelta(minutes=30)] = kwh

    return series, {
        "file": path.name,
        "mpan": mpan,
        "days": len(days),
        "absent_cells": absent,
        "unparsed_cells": dict(unparsed),
        "first_day": min(days).strftime("%Y-%m-%d"),
        "last_day": max(days).strftime("%Y-%m-%d"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # The manifest is a LIST of per-site entries (the pre-existing shape). Replace this site's
    # entry in place if present, so re-running is idempotent and the MODELLED entries for any site
    # not covered here survive untouched.
    manifest: list[dict[str, object]] = (
        json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    )
    report: list[dict[str, object]] = []

    for site_id, files in SITES.items():
        parts = []
        meta = []
        for name in files:
            s, m = read_meter(SRC / name)
            parts.append(s)
            meta.append(m)

        # Drop the incomplete final day WHOLE (see module docstring), across all this site's meters.
        last_day = max(max(p).date() for p in parts)
        combined: dict[datetime, float] = {}
        for p in parts:
            for ts, kwh in p.items():
                if ts.date() == last_day:
                    continue
                combined[ts] = combined.get(ts, 0.0) + kwh

        stamps = sorted(combined)
        rows = [(ts.strftime("%Y-%m-%d %H:%M:%S"), f"{combined[ts]:.6f}") for ts in stamps]
        payload = "timestamp,import_kwh\n" + "\n".join(f"{a},{b}" for a, b in rows) + "\n"
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        period = f"{stamps[0]:%Y%m%d}_{stamps[-1]:%Y%m%d}"
        out_path = OUT / f"{site_id}_metered_{period}_{digest}.csv"
        out_path.write_text(payload)

        total = sum(combined.values())
        peak_kw = max(combined.values()) * 2.0
        entry = {
            # Field names follow the manifest's pre-existing MODELLED entries so the two bases can
            # be read side by side; `status` is what distinguishes them.
            "site": site_id,
            "status": "metered",
            "file": str(out_path.relative_to(REPO)),
            "sha256_16": digest,
            "intervals": len(stamps),
            "total_kwh": round(total, 3),
            "measured_total_kwh": round(total, 3),
            "peak_kw": round(peak_kw, 3),
            # What this basis MEASURED rather than modelled. On a metered basis that is everything,
            # which is the whole point of the upgrade; the key is required by
            # `test_grundon_site_data_basis::test_no_site_is_left_on_an_annual_only_basis` and its
            # absence here was a real omission, not a test being fussy.
            "measured_components": [
                "annual energy",
                "seasonal (month-to-month) variation",
                "within-day shape",
                "peak demand",
            ],
            "first": stamps[0].isoformat(sep=" "),
            "last": stamps[-1].isoformat(sep=" "),
            "source_meters": meta,
            "excluded_final_day": last_day.isoformat(),
            "conversion_notes": [
                "Columns are period-ENDING in the source; written here as period-STARTING "
                "(label minus 30 minutes). The source's final '00:00' column is 23:30 of the SAME "
                "day. A naive mapping would shift every reading 30 minutes and move consumption "
                "across the 16:00/19:00 DUoS red-band boundaries.",
                "'-' cells are ABSENT and are dropped, never written as 0 (D45): a zero and an "
                "unknown are different facts, and a false zero would understate avoided cost.",
                f"The final day {last_day.isoformat()} is excluded whole: the source reports were "
                "run at 10:03 the following morning, so it is partial.",
                "The export's fixed 48-column day cannot represent the 50 half-hours of the autumn "
                "clock change; up to 2 intervals on 2025-10-26 are absent from the SOURCE. "
                "Timestamps are naive Europe/London local time.",
            ],
        }
        manifest = [m for m in manifest if m.get("site") != site_id]
        manifest.append(entry)
        report.append(entry)

    manifest = [m for m in manifest if m.get("site") != "_excluded_meters"]
    manifest.append({"site": "_excluded_meters", "status": "note", "meters": EXCLUDED_METERS})
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"{'site':34} {'intervals':>9} {'annual kWh':>12} {'peak kW':>9}  file")
    print("-" * 108)
    for e in report:
        print(
            f"{e['site']:34} {e['intervals']:>9,} {e['total_kwh']:>12,.0f} "
            f"{e['peak_kw']:>9,.1f}  {Path(str(e['file'])).name}"
        )
    print(f"\nexcluded meters ({len(EXCLUDED_METERS)}), each for a stated reason:")
    for k, v in EXCLUDED_METERS.items():
        print(f"  {k}: {v[:96]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
