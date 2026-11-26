"""Extract the real Harwell half-hourly series and the Grundon monthly invoice series.

Both source workbooks are read-only. Nothing here fabricates a value: the Harwell series is the
meter's own kWh, only re-labelled from period-END (the supplier's export convention) to
period-START (the convention `gridq.optimisation.tariff_calendar` bands against). The Grundon
series is the invoice kWh per meter per month, verbatim.

**2026-08-13 (Olly) — three additions, all closing defects found by the bill reconciliation
(`bill_reconciliation.md`, base year calendar 2025):**

1. **`'Tanhouse (MRF, WS, Depot)'` was missing from `GRUNDON_SITES`** (§3.2). The tab exists —
   MPAN `2000052173090`, meter `K12C03674`, monthly kWh *and* cost from 2018-01 to 2026-06 — but
   because it was never listed here it never reached `grundon_monthly.json`, so
   `data/sites/hh/manifest.json` and `examples/real/sites/tanhouse.json` both recorded "no
   per-meter tab exists" and the manifest named monthly totals "the single highest-value data ask
   for this site". The data ask was already in hand. Adding the tab makes Tanhouse's seasonal
   shape MEASURED instead of modelled.

2. **The purchased / self-generated split is now extracted** (§3.3). The `'2025 Summary'` tab
   decomposes each site's 2025 energy by source (Grid / EfW / Solar / LFG). Tanhouse is
   1,541,453.4 kWh EfW + 139,172.28 kWh on-site solar, and the site model priced the combined
   1,680,625.68 roll-up as purchased private-wire import — so 9.03% of the modelled load was
   energy the customer does not buy. The split is recorded per meter (`supply_split_2025`) and
   rolled up per site (`purchased_kwh_2025` / `self_generated_kwh_2025`) so a reader can see which
   volume is bought and which is self-generated. **The self-generated volume is not deleted and
   not spread into months** — it is an annual figure from one tab and is carried as exactly that.

3. **`grundon_invoices_2025.json` is written** — the per-site invoice evidence
   `gridq.ingestion.reconciliation.reconcile_bill_against_invoice` consumes (Gate 3b). Invoiced
   kWh and cost are the meter tabs' own figures; the documented-omission itemisation is
   transcribed from `bill_reconciliation.md` §6 with each line's source, and the transcription is
   assert-checked against that document's stated subtotals so a typo fails loudly.

Nothing here re-derives the reconciliation's numbers; it verifies and carries them.

**2026-08-15 (Olly) — the invoice evidence is re-split onto the NEW site boundary.**

Bishops Cleeve is now **two sites**, because the metered half-hourly data proved Ash Plant and MRF
run concurrently 91.7% of the time — separate supplies, not parent and child
(`scripts/convert_grundon_hh.py`). Gate 3b looks its evidence up **by `site_id`**
(`quotes/pipeline._invoice_evidence_for`), so evidence still keyed by the retired combined id
`grundon_bishops_cleeve` would have left BOTH new sites resolving a "NO INVOICE EVIDENCE" absence
record while the evidence sat in the file under a name nothing looks for.

The workbook's **per-meter tabs** are what make the split possible: each Bishops Cleeve meter has
its own tab with its own MPAN, its own monthly kWh and its own monthly cost, so each new site's
invoice is that meter's own invoice **verbatim** — nothing is apportioned, and no figure is shared
between the two sites.

Three consequences are recorded rather than smoothed:

1. **The two new sites do not sum to the retired combined figure, and are not made to.** Three
   further Bishops Cleeve meters carry 2025 volume (`BC Import`, `BC Weighbridge`, `BC Lodge`) and
   belong to **neither** new site boundary. Their 32,904.7 kWh / £14,707.27 is recorded in the
   `_bishops_cleeve_split` block with the full arithmetic, and `_verify_bishops_cleeve_split`
   asserts it to the penny so the residual is machine-checked rather than asserted in prose.
2. **The residual is NOT written into `documented_omissions`.** That list feeds
   `reconciliation._itemisation`, which uses it to convert an unexplained cost undershoot into an
   "accounted for" WARNING. £14,707 of other meters' cost against Ash Plant's £20,535 invoice would
   hand the gate a 72%-of-invoice cushion for undershoots those meters have nothing to do with —
   loosening the gate with a figure that is not an omission from either site's modelled stack.
3. **Bishops Cleeve's £18,061.82 "4 omitted per-MPAN standing lines" omission disappears**, because
   each new site is exactly ONE MPAN. That is the split working, not a regression: the explanation
   cushion is gone, so a per-site undershoot will now read as unexplained unless the new site
   models carry their own fixed lines. The omissions are deliberately **not** padded to keep the
   gate green.

Two findings on the other three sites, both recorded in metadata (§`_knowl_hill_coverage`):

- **Knowl Hill's invoice is NOT partial.** Its single grid MPAN 2000053471776 has all 12 months of
  2025, and the metered HH series agrees with the invoice **month by month to ~0.1%** across the
  eleven overlapping months. The 45,732.3 kWh invoice looks small against the 108,242 kWh metered
  year for two reasons that are both real: grid import roughly **tripled** into 2026 (April 2026
  alone is 28.6 MWh against 45.7 MWh for the whole of 2025), and the site is **92.1% self-supplied**
  from on-site LFG generation — 531,680.9 kWh in 2025, against 45,732.3 kWh of grid.
- **The `'2025 Summary'` tab's `BC Ash Plant 1` row (33,534 kWh) is wrong**, and the metered HH data
  now settles a disagreement `bill_reconciliation.md` §3.6 could only record. The per-meter tab says
  66,765.6 kWh over 12 complete months; the metered series says 67,888.6 kWh over its own 12 months.
  The summary row is neither a half-year (H1 41,112.2, H2 25,653.4) nor reconcilable to either — it
  is simply not a volume authority, exactly as `_attach_supply_split` already states.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "sites"
OUT.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- Harwell (real HH)

HARWELL_XLSX = REPO / "examples/real/Harwell/Meter_Data_Interval_Meter_K16W304527.XLSX"
DATE_COL = "Date\xa0Of\xa0Read"


def extract_harwell() -> dict:
    df = pd.read_excel(HARWELL_XLSX, sheet_name="Data Types")
    time_cols = [c for c in df.columns if c not in ("Meter Id", DATE_COL, "Units")]
    assert len(time_cols) == 48, f"expected 48 half-hours, got {len(time_cols)}"
    assert set(df["Units"].str.strip().unique()) == {"kWh"}

    rows: list[tuple[pd.Timestamp, float]] = []
    for _, r in df.iterrows():
        day = pd.Timestamp(r[DATE_COL]).normalize()
        for i, col in enumerate(time_cols):
            # Column labels are period-END (00:30 = the 00:00-00:30 period; the last is 24:00).
            # gridq bands on period-START, so period i (0-indexed) starts at day + i*30min.
            ts = day + timedelta(minutes=30 * i)
            rows.append((ts, float(r[col])))

    s = pd.DataFrame(rows, columns=["timestamp", "import_kwh"]).sort_values("timestamp")
    assert s["timestamp"].is_unique, "duplicate half-hour timestamps"

    full = s.copy()
    full.to_csv(OUT / "harwell_hh_full.csv", index=False)

    # Calendar 2025 is the modelling base year: the one complete calendar year inside coverage,
    # and the same base year the Grundon invoice reconciliation uses.
    y = s[(s.timestamp >= "2025-01-01") & (s.timestamp < "2026-01-01")].copy()
    y.to_csv(OUT / "harwell_hh_2025.csv", index=False)

    expected = 365 * 48
    return {
        "meter_id": df["Meter Id"].iloc[0].strip(),
        "full_coverage": {
            "start": str(s.timestamp.min()),
            "end": str(s.timestamp.max()),
            "intervals": len(s),
            "total_kwh": round(float(s.import_kwh.sum()), 1),
        },
        "base_year_2025": {
            "intervals": len(y),
            "expected_intervals": expected,
            "completeness_pct": round(100 * len(y) / expected, 3),
            "total_kwh": round(float(y.import_kwh.sum()), 1),
            "peak_kwh_per_hh": round(float(y.import_kwh.max()), 3),
            "peak_kw": round(float(y.import_kwh.max()) * 2, 2),
            "mean_kw": round(float(y.import_kwh.mean()) * 2, 2),
            "min_kw": round(float(y.import_kwh.min()) * 2, 2),
            "load_factor_pct": round(100 * y.import_kwh.mean() / y.import_kwh.max(), 2),
            "negative_or_zero_intervals": int((y.import_kwh <= 0).sum()),
        },
    }


# ------------------------------------------------------------------- Grundon (monthly invoice)

GRUNDON_XLSX = REPO / "examples/real/Grundon/NEW Electricity Usage and Costs.xlsx"

# The target sites -> their meter tabs.
#
# `tanhouse_colnbrook` keeps its historical key even though it holds only Colnbrook's tab: the key
# feeds the synthesised HH filename that `examples/real/sites/colnbrook.json` already references,
# so renaming it would silently invalidate that site model.
GRUNDON_SITES: dict[str, list[str]] = {
    "bishops_cleeve": [
        "BC MRF",
        "BC Ash Plant 1",
        "BC Import",
        "BC Weighbridge",
        "BC Flare",
        "BC Canteen",
        "BC Lodge",
        "BC Self-Supply (LFG Gen)",
    ],
    "tanhouse_colnbrook": ["Colnbrook Offices, CWI, TS etc."],
    "knowl_hill": ["Knowl Hill Import"],
    "ewelme": ["Ewelme MRF", "Ewelme Workshop"],
    # Added 2026-08-13. The tab was always there; it was simply never listed here.
    "tanhouse": ["Tanhouse (MRF, WS, Depot)"],
}

# Meter tab -> its row label in the `'2025 Summary'` tab, which decomposes 2025 energy by SOURCE
# (Grid / EfW / Solar / LFG-on-site-gen). The labels differ from the tab names, so the mapping is
# explicit: no fuzzy matching, and a tab with no summary row records the absence rather than
# guessing a split.
SUMMARY_ROW_FOR_TAB: dict[str, str] = {
    "BC MRF": "BC MRF",
    "BC Ash Plant 1": "BC Ash Plant 1",
    "BC Import": "BC Import",
    "BC Weighbridge": "BC Weighbridge",
    "BC Flare": "BC Flare",
    "BC Canteen": "BC Canteen",
    "BC Lodge": "BC Lodge",
    "BC Self-Supply (LFG Gen)": "BC Self-Supply (LFG)",
    "Colnbrook Offices, CWI, TS etc.": "Colnbrook (Office, CWI, TS)",
    "Knowl Hill Import": "Knowl Hill Import",
    "Ewelme MRF": "Ewelme MRF",
    "Ewelme Workshop": "Ewelme Workshop",
    "Tanhouse (MRF, WS, Depot)": "Tanhouse (MRF, WS, Depot)",
}

#: Which `'2025 Summary'` source columns are energy the customer BUYS from a counterparty, and
#: which is generated on site and consumed without a supplier bill. Grid = DNO-delivered supply;
#: EfW = the energy-from-waste private wire (purchased under a private-wire contract, which is why
#: those sites carry a delivered cost at all); Solar and LFG = on-site generation.
PURCHASED_SOURCES = ("grid_kwh", "efw_kwh")
SELF_GENERATED_SOURCES = ("solar_kwh", "lfg_or_onsite_gen_kwh")


def _num(v: object) -> float | None:
    """Coerce a workbook cell to a float, or None. Blank/whitespace/text cells are NOT guessed."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None


def extract_supply_split_2025() -> dict[str, dict]:
    """The `'2025 Summary'` tab's decomposition of 2025 energy by SOURCE, per site row, verbatim.

    This is the tab that shows Tanhouse's 1,680,625.68 kWh is 1,541,453.4 EfW + 139,172.28 solar
    (`bill_reconciliation.md` §3.3). Only the five source columns are read; blanks are recorded as
    absent rather than zero-filled, because "no reading" and "zero kWh" are different facts and
    this tab carries both (BC Canteen is a genuine 0).
    """
    raw = pd.read_excel(GRUNDON_XLSX, sheet_name="2025 Summary", header=None)
    header = [str(v).strip() for v in raw.iloc[1, :6]]
    expected = [
        "Site",
        "Grid (kWh)",
        "EfW (kWh)",
        "Solar (kWh)",
        "LFG/On-Site Gen (kWh)",
        "Total (kWh)",
    ]
    assert header == expected, f"'2025 Summary' columns moved: {header}"

    rows: dict[str, dict] = {}
    for i in range(2, len(raw)):
        label = str(raw.iloc[i, 0]).strip()
        if label in ("nan", "", "TOTAL", "%"):
            continue
        rows[label] = {
            "grid_kwh": _num(raw.iloc[i, 1]),
            "efw_kwh": _num(raw.iloc[i, 2]),
            "solar_kwh": _num(raw.iloc[i, 3]),
            "lfg_or_onsite_gen_kwh": _num(raw.iloc[i, 4]),
            "total_kwh": _num(raw.iloc[i, 5]),
        }
    return rows


def _attach_supply_split(meter: dict, summary: dict[str, dict]) -> None:
    """Record the meter's 2025 source split, and how it compares to the meter tab's own kWh.

    The `'2025 Summary'` tab is NOT treated as a volume authority: it disagrees with the per-meter
    tabs at Bishops Cleeve (Ash Plant 33,534 vs 66,765.6; Lodge 0 vs 14,940 — `02_data_recon-
    ciliation.md` §3.3, `bill_reconciliation.md` §3.6). The delta is recorded so the disagreement
    is visible instead of resolved. It is used only to establish which portion of a volume is
    self-generated.
    """
    row_label = SUMMARY_ROW_FOR_TAB.get(meter["tab"])
    if row_label is None or row_label not in summary:
        meter["supply_split_2025"] = {
            "status": "absent",
            "note": f"no '2025 Summary' row mapped for tab {meter['tab']!r}; source split unknown",
        }
        return

    row = summary[row_label]
    purchased = sum(row[k] or 0.0 for k in PURCHASED_SOURCES)
    self_gen = sum(row[k] or 0.0 for k in SELF_GENERATED_SOURCES)
    split: dict[str, object] = {
        "status": "extracted",
        "summary_row": row_label,
        **row,
        "purchased_kwh": round(purchased, 2),
        "self_generated_kwh": round(self_gen, 2),
        "source": "'2025 Summary' tab, columns Grid/EfW/Solar/LFG — annual 2025 only, no monthly "
        "breakdown by source exists in the workbook",
    }
    meter_kwh = meter.get("kwh_2025")
    if isinstance(meter_kwh, (int, float)):
        split["purchased_minus_meter_tab_kwh"] = round(purchased - float(meter_kwh), 2)
        split["summary_total_minus_meter_tab_kwh"] = round(
            (row["total_kwh"] or 0.0) - float(meter_kwh), 2
        )
    meter["supply_split_2025"] = split


def _meta_and_series(tab: str) -> dict:
    raw = pd.read_excel(GRUNDON_XLSX, sheet_name=tab, header=None)
    meta: dict[str, object] = {"tab": tab, "title": str(raw.iloc[0, 0])}
    header_row = None
    for i in range(min(15, len(raw))):
        label = str(raw.iloc[i, 0]).strip().lower()
        value = raw.iloc[i, 1]
        if label == "date":
            header_row = i
            break
        if label.startswith("mpan") or label.endswith("mpan"):
            meta["mpan"] = str(value).strip()
        # Tabs label this "Meter No", "Meter No:" or "Meter Number:" — the narrower
        # `startswith("meter no")` silently dropped the Tanhouse tab's meter identity (K12C03674).
        elif label.startswith("meter n"):
            meta["meter_no"] = str(value).strip()
        elif label.startswith("kva"):
            meta["kva"] = _num(value)
        elif i == 1 and not pd.isna(raw.iloc[i, 0]):
            meta["address"] = str(raw.iloc[i, 0])

    if header_row is None:
        return {**meta, "error": "no Date header row found"}

    body = raw.iloc[header_row + 1 :, :5].copy()
    body.columns = ["date", "kwh", "cost_gbp", "kgco2e", "p_per_kwh"]
    body["date"] = pd.to_datetime(body["date"], errors="coerce")
    body = body[body["date"].notna()]

    monthly = []
    for d, k, c in zip(body["date"], body["kwh"], body["cost_gbp"], strict=True):
        kwh = _num(k)
        if kwh is None:  # no reading recorded for that month — carried as absent, never zero-filled
            continue
        monthly.append({"month": d.strftime("%Y-%m"), "kwh": round(kwh, 2), "cost_gbp": _num(c)})
    y2025 = [m for m in monthly if m["month"].startswith("2025")]
    meta["monthly"] = monthly
    meta["months_2025"] = len(y2025)
    meta["kwh_2025"] = round(sum(m["kwh"] for m in y2025), 1)
    meta["cost_2025_gbp"] = round(sum(m["cost_gbp"] or 0 for m in y2025), 2)
    if meta["kwh_2025"]:
        meta["blended_p_per_kwh_2025"] = round(100 * meta["cost_2025_gbp"] / meta["kwh_2025"], 2)
    return meta


#: One parse per tab, shared by the monthly extraction and the invoice evidence. The two now group
#: the SAME meters differently — `grundon_monthly.json` keeps its historical site keys while the
#: invoice evidence follows the new site boundary — so a memo is what keeps them provably the same
#: numbers rather than two independent reads that could drift.
_METER_CACHE: dict[str, dict] = {}


def _meter(tab: str) -> dict:
    if tab not in _METER_CACHE:
        _METER_CACHE[tab] = _meta_and_series(tab)
    return _METER_CACHE[tab]


def _monthly_rollup(meters: list[dict]) -> dict[str, dict[str, float]]:
    """Sum the site's meters into one 2025 monthly series of kWh and cost, verbatim.

    A meter-month with no kWh reading never reached `monthly` at all (`_meta_and_series` carries it
    as absent, never zero), so its cost is absent here too. At Bishops Cleeve that drops BC Flare's
    and BC Canteen's `Totals`-tab 2025 cost (£17,946.76 combined against 104 kWh and 0 kWh, both
    per-meter tabs ending in 2024 — `bill_reconciliation.md` §3.6). Direction: if any of that is
    real 2025 spend, the invoice basis here is UNDERSTATED and the modelled bill is further below
    actual than reported. Recorded, not resolved.
    """
    monthly: dict[str, dict[str, float]] = {}
    for meter in meters:
        for row in meter.get("monthly", []):
            if not row["month"].startswith("2025"):
                continue
            bucket = monthly.setdefault(row["month"], {"kwh": 0.0, "cost_gbp": 0.0})
            bucket["kwh"] += row["kwh"]
            bucket["cost_gbp"] += row["cost_gbp"] or 0.0
    return {
        m: {"kwh": round(v["kwh"], 2), "cost_gbp": round(v["cost_gbp"], 2)}
        for m, v in sorted(monthly.items())
    }


def extract_grundon() -> dict:
    summary = extract_supply_split_2025()
    out: dict[str, dict] = {}
    for site, tabs in GRUNDON_SITES.items():
        meters = [_meter(t) for t in tabs]
        for meter in meters:
            _attach_supply_split(meter, summary)
        splits = [
            m["supply_split_2025"]
            for m in meters
            if m.get("supply_split_2025", {}).get("status") == "extracted"
        ]
        out[site] = {
            "meters": meters,
            # The per-meter tabs are the volume authority (`02_data_reconciliation.md` §1).
            "kwh_2025_total": round(sum(m.get("kwh_2025", 0) or 0 for m in meters), 1),
            "cost_2025_total_gbp": round(sum(m.get("cost_2025_gbp", 0) or 0 for m in meters), 2),
            "monthly_2025": _monthly_rollup(meters),
            # The `'2025 Summary'` source split, carried ALONGSIDE rather than replacing the above.
            # `purchased` is what a supplier or private-wire counterparty bills; `self_generated`
            # is consumed on site with no bill, so it must never be priced as import — but it is
            # still consumed, so it is recorded here rather than deducted and forgotten.
            "summary_tab_purchased_kwh_2025": round(
                sum(float(s["purchased_kwh"]) for s in splits), 2
            ),
            "summary_tab_self_generated_kwh_2025": round(
                sum(float(s["self_generated_kwh"]) for s in splits), 2
            ),
        }
        out[site]["summary_tab_purchased_minus_meter_tabs_kwh"] = round(
            out[site]["summary_tab_purchased_kwh_2025"] - out[site]["kwh_2025_total"], 2
        )
    return out


# --------------------------------------------------- invoice evidence for the bill gate (Gate 3b)

#: `SiteModel.site_id` -> the per-meter tabs whose 2025 invoices ARE that site's invoice.
#:
#: Deliberately NOT `GRUNDON_SITES` re-keyed. `GRUNDON_SITES` drives `grundon_monthly.json`, which
#: `scripts/generate_grundon_hh.py` and `tests/test_grundon_site_data_basis.py` read by its
#: historical keys; and since the Bishops Cleeve split the invoice boundary is a different question
#: from the extraction boundary. Keeping the two mappings separate is what lets the site boundary
#: move without silently re-basing the monthly series any other consumer depends on.
#:
#: The site_ids here must match `scripts/convert_grundon_hh.py:SITES` exactly — Gate 3b resolves
#: evidence BY SITE_ID (`quotes/pipeline._invoice_evidence_for`), so a key that does not match a
#: `SiteModel.site_id` is evidence nothing will ever look for, and the site silently resolves a
#: "NO INVOICE EVIDENCE" absence record instead.
INVOICE_SITE_METERS: dict[str, list[str]] = {
    # Bishops Cleeve, split. Each new site is ONE meter tab, ONE MPAN, taken verbatim: no line of
    # either invoice is apportioned, shared or derived from the other.
    "grundon_bishops_cleeve_ash_plant": ["BC Ash Plant 1"],  # MPAN 1420398100000, 93 kVA
    "grundon_bishops_cleeve_mrf": ["BC MRF"],  # MPAN 1460001884093, 250 kVA
    "grundon_colnbrook": ["Colnbrook Offices, CWI, TS etc."],
    "grundon_knowl_hill": ["Knowl Hill Import"],
    "grundon_ewelme": ["Ewelme MRF", "Ewelme Workshop"],
    "grundon_tanhouse": ["Tanhouse (MRF, WS, Depot)"],
}

#: Bishops Cleeve meters with 2025 volume that belong to NEITHER new site, each with the reason.
#: Listed rather than dropped: this is the difference between the retired combined invoice and the
#: two new ones, and a reader must be able to see that nothing was quietly discarded or apportioned.
BISHOPS_CLEEVE_UNASSIGNED_METERS: dict[str, str] = {
    "BC Import": "MPAN 1460001527004, 230 kVA. The metered half-hourly series is 92.4% ZERO with a "
    "169 kW spike — a standby/top-up supply, not a site load (convert_grundon_hh.py:EXCLUDED_"
    "METERS). Assigning it to either new site would attribute demand to a site that did not incur "
    "it. Its 10,237.7 kWh for £9,863.68 = 96.35 p/kWh is almost entirely fixed charges, which is "
    "the multi-MPAN fixed-line structure showing up directly in the invoice (06_bill_"
    "reconciliation.md §6).",
    "BC Weighbridge": "MPAN 1416107661001, kVA not stated in the workbook. A site-entrance "
    "weighbridge, on its own MPAN, with no half-hourly series and no assignment to either the Ash "
    "Plant or the MRF supply. 7,727.0 kWh / £1,883.44 in 2025.",
    "BC Lodge": "MPAN 1415207661005, kVA not stated in the workbook. A separate dwelling "
    "('Wingmoor Lodge' on the bills) on its own MPAN, with no half-hourly series and no assignment "
    "to either supply. 14,940.0 kWh / £2,960.15 in 2025, including the 2025-03 catch-up bill "
    "(10,128 kWh against a ~150 kWh/month baseline) that confounded the retired combined site's "
    "monthly cost series.",
}

#: Bishops Cleeve tabs with NO 2025 volume at all, so they cannot affect any 2025 figure. Recorded
#: because "absent from the total" and "worth zero" are different facts (D45): both tabs end in
#: 2024, and `06_bill_reconciliation.md` §3.6 notes the `Totals` tab may still carry 2025 cost for
#: them. If it does, EVERY Bishops Cleeve invoice basis here is understated, not just one.
BISHOPS_CLEEVE_NO_2025_DATA: dict[str, str] = {
    "BC Flare": "Per-meter tab ends 2024-10 (cost only, no kWh after 2024-02). Note its MPAN "
    "1460001884093 is the SAME as BC MRF's, with a 'New: 1460002086579' annotation — the flare was "
    "metered on the MRF's MPAN before moving. It carries no 2025 volume, so it cannot inflate the "
    "MRF figure, but the shared MPAN is a data ask if the flare is still billed there.",
    "BC Canteen": "MPAN 1416207661007. Per-meter tab ends 2024-08; the last kWh reading is a "
    "genuine 0. No 2025 volume.",
}

#: Site ids that no longer describe a site, and the id(s) that replaced them. Retired as an EXPLICIT
#: absence record rather than deleted: a stale `SiteModel` still carrying the old id then resolves a
#: reasoned absence (LOW ceiling, no number) instead of a silent generic miss — and it can never
#: resolve a 396,699 kWh combined invoice against a site that is now a fraction of that size.
RETIRED_SITE_IDS: dict[str, str] = {
    "grundon_bishops_cleeve": "RETIRED SITE BOUNDARY. Bishops Cleeve is now TWO sites: the metered "
    "half-hourly data shows the Ash Plant and MRF supplies run concurrently 91.7% of the time, "
    "which rules out a parent/child reading (scripts/convert_grundon_hh.py). Its invoice evidence "
    "has been re-split per meter and now lives under 'grundon_bishops_cleeve_ash_plant' and "
    "'grundon_bishops_cleeve_mrf'; see the '_bishops_cleeve_split' block for the full arithmetic, "
    "including the 32,904.7 kWh / £14,707.27 on three further MPANs that belong to neither new "
    "site. The retired combined figure (396,698.7 kWh / £110,869.87) describes a boundary that no "
    "longer exists and must not be reconciled against either new site.",
}

#: Meter-months excluded from the MONTHLY limb of the bill gate, with the reason. Listed, never
#: smoothed: the annual limb still carries the month in full.
#:
#: **Bishops Cleeve's 2025-03 exclusion is now GONE, and that is a result of the split, not a
#: relaxation.** It existed because BC Lodge billed 10,128 kWh in that month against a ~150
#: kWh/month baseline (68x) — a catch-up bill whose cost confounded the combined site's monthly
#: series (`06_bill_reconciliation.md` §4.1). Lodge sits outside BOTH new boundaries, so it can no
#: longer contaminate either monthly series, and neither new site needs the exclusion. Both meters'
#: own 2025 series were checked for outliers before this list was declared empty: `BC Ash Plant 1`
#: runs 3,566-11,705 kWh/month and `BC MRF` 19,474-30,149 kWh/month, both 12/12 months present,
#: both a plain seasonal shape with no step, no gap and no catch-up.
EXCLUDED_MONTHS: dict[str, list[dict[str, str]]] = {}

#: Omissions from the modelled tariff stack that are DOCUMENTED and QUANTIFIED, transcribed from
#: `bill_reconciliation.md` §6 with each line's own source. The bill gate uses these to decide
#: whether an undershoot past WARN is accounted for or unexplained; the subtotals are asserted
#: against that document below, so a transcription slip fails loudly rather than loosening a gate.
#:
#: `kind` is load-bearing and must not be flattened:
#:   "computed"  — a £ line arithmetically derived from a known rate and a known quantity;
#:   "bounded_by_declared_uncertainty" — not independently derived, but shown to sit inside a range
#:   the tariff's own `source` string already declares uncertain. Weaker evidence, and labelled so.
#:
#: **Bishops Cleeve's three transcribed lines are retired with its combined boundary**, and are NOT
#: carried onto either new site. Two of them describe a multi-MPAN structure that no longer exists:
#: the "4 omitted per-MPAN standing lines" (£18,061.82) counted 5 MPANs modelled as 1, and each new
#: site is exactly ONE MPAN, so nothing is omitted; the capacity line (£9,060.54) was charged on
#: BC Import's 230 kVA plus Ash Plant's 93 kVA, and BC Import is outside both boundaries while each
#: new site's own kVA is stated in the workbook (Ash Plant 93, MRF 250) for its site model to carry.
#: The third, the Capacity Market supplier charge, is a stack-level exclusion that still applies and
#: is DERIVED per site below rather than transcribed. Net effect: Bishops Cleeve loses ~£27k of
#: explanation cushion, so an undershoot at either new site will now read as UNEXPLAINED. That is
#: the split working. The omissions are not padded to keep the gate green.
DOCUMENTED_OMISSIONS: dict[str, list[dict[str, object]]] = {
    "grundon_ewelme": [
        {
            "label": "1 omitted per-MPAN standing line",
            "gbp": 4_515.45,
            "kind": "computed",
            "source": "bill_reconciliation.md §6. 2 MPANs modelled as 1. £12.36264/day x 365.25.",
        },
        {
            "label": "Capacity charge on Ewelme MRF 200 kVA",
            "gbp": 5_610.24,
            "kind": "computed",
            "source": "bill_reconciliation.md §6. 200 kVA x £0.0768/kVA/day x 365.25.",
        },
        {
            "label": "Capacity Market supplier charge (excluded by design, ~1.1 p/kWh)",
            "gbp": 7_634.68,
            "kind": "computed",
            "source": "bill_reconciliation.md §6. ~1.1 p/kWh x 694,062 kWh.",
        },
    ],
    "grundon_knowl_hill": [
        {
            "label": "Capacity Market supplier charge (excluded by design, ~1.1 p/kWh)",
            "gbp": 503.06,
            "kind": "computed",
            "source": "bill_reconciliation.md §6. ~1.1 p/kWh x 45,732 kWh.",
        },
        {
            "label": "TNUoS TCR band assumed LV2 — headroom to the published LV1-LV4 range",
            "gbp": 3_952.25,
            "kind": "bounded_by_declared_uncertainty",
            "source": "bill_reconciliation.md §6. £3,952/yr on this site's single MPAN is "
            "+£10.82/day, taking the standing line from £12.36/day to £23.18/day — inside the "
            "£5.797-£38.180/day LV1-LV4 range the tariff's own source string already declares "
            "uncertain (the register publishes band rates but not thresholds, so the band was "
            "ASSUMED). NOT independently derived: this is an attribution to a declared-uncertain "
            "input, and confirming the billed standing charge would close it outright.",
        },
    ],
    # Colnbrook and Tanhouse carry NO documented omissions on purpose. Both OVERSHOOT the invoice,
    # and an omission from the modelled stack can only push the modelled bill DOWN — it can never
    # explain a bill that exceeds a real invoice (bill_reconciliation.md §6, §7).
    "grundon_colnbrook": [],
    "grundon_tanhouse": [],
}

#: Subtotals stated in `bill_reconciliation.md` §6, asserted against the transcription above. The
#: retired `grundon_bishops_cleeve` subtotal (£31,379.44) goes with its boundary; the two new sites
#: are DERIVED, not transcribed, so they are checked by `_CM_TRANSCRIPTION_CHECKS` instead.
_OMISSION_SUBTOTAL_CHECKS: dict[str, float] = {
    "grundon_ewelme": 17_760.38,
    "grundon_knowl_hill": 4_455.31,
}

#: The Capacity Market supplier charge the modelled tariff stack excludes BY DESIGN, in p/kWh. No
#: authoritative public rate exists, so the stack omits it deliberately rather than guessing one
#: (`06_bill_reconciliation.md` §6).
CM_SUPPLIER_CHARGE_P_PER_KWH = 1.1

#: Sites whose Capacity Market omission is DERIVED here rather than transcribed from §6 — the two
#: new Bishops Cleeve sites, which post-date that document and so have no line in it. Nothing else
#: is derived: every other omission stays the reviewed transcription.
DERIVE_CM_OMISSION_FOR: tuple[str, ...] = (
    "grundon_bishops_cleeve_ash_plant",
    "grundon_bishops_cleeve_mrf",
)

#: Transcribed §6 Capacity Market lines, used to prove the derivation below reproduces the SAME
#: construction the document used, on sites where both a transcription and a volume exist. If
#: `_cm_supplier_charge_omission` ever stops reproducing these, the two new sites' derived lines are
#: no longer the reviewed construction and the script fails rather than writing a novel one.
_CM_TRANSCRIPTION_CHECKS: dict[str, tuple[float, float]] = {
    # site_id: (invoiced kWh the document used, the £ it stated)
    "grundon_ewelme": (694_062.0, 7_634.68),
    "grundon_knowl_hill": (45_732.0, 503.06),
}


def _omission_gbp(omission: dict[str, object]) -> float:
    """The £ of one omission entry, narrowed explicitly rather than coerced.

    These entries are `str -> object` JSON shapes, and a non-numeric `gbp` would otherwise reach
    `_itemisation` as a silent zero and loosen the gate by the whole of that line.
    """
    value = omission["gbp"]
    assert isinstance(value, (int, float)), (
        f"omission {omission.get('label')!r} has a non-numeric gbp: {value!r}"
    )
    return float(value)


def _cm_supplier_charge_omission(invoiced_kwh: float) -> dict[str, object]:
    """The Capacity Market supplier-charge omission for one site, from its own invoiced volume.

    The retired combined Bishops Cleeve line was the portfolio's only one computed on *modelled*
    rather than *invoiced* kWh (387,008 vs 396,699). Every other site in §6 used invoiced kWh, and
    the new sites have no modelled kWh yet — their site models are being written concurrently — so
    invoiced kWh is both the consistent basis and the only self-contained one. It is also the
    smaller, tighter choice wherever the model undershoots volume.
    """
    gbp = round(invoiced_kwh * CM_SUPPLIER_CHARGE_P_PER_KWH / 100.0, 2)
    return {
        "label": "Capacity Market supplier charge (excluded by design, ~1.1 p/kWh)",
        "gbp": gbp,
        "kind": "computed",
        "source": f"06_bill_reconciliation.md §6, DERIVED for this site rather than transcribed "
        f"(the document predates the Bishops Cleeve split). "
        f"{CM_SUPPLIER_CHARGE_P_PER_KWH} p/kWh x {invoiced_kwh:,.1f} kWh invoiced on this site's "
        "own meter tab. No authoritative public rate exists, so the stack omits the charge "
        "deliberately. Derivation asserted to reproduce §6's Ewelme and Knowl Hill lines exactly.",
    }


#: Sites with no invoice of any kind. Recorded EXPLICITLY: an absent invoice must never be read as
#: a reconciled one (D45 amendment — a null that reads as zero is worse than a blocked run).
NO_INVOICE_SITES: dict[str, str] = {
    "harwell_campus_k16w304527": "No invoice, tariff or cost data of any kind exists for Harwell "
    "(02_data_reconciliation.md §5 data ask 5; bill_reconciliation.md §3.7). Its tariff is "
    "Ewelme's by analogy. Its delivered cost has NEVER been compared to anything, and because the "
    "one construction available recovers 74-85% of actual on the three grid sites where it CAN be "
    "checked, the direction of any error here is unknown — so it cannot be called conservative.",
}


#: Alternative year bases for the blended delivered cost, so a calibration target can be checked
#: against the year it claims to describe. `bill_reconciliation.md` §3.4: both private-wire
#: residuals were back-solved from the **2026 year-to-date** blended cost and then applied to a
#: **2025** volume basis. Computing every basis makes that mismatch a visible fact in the data
#: rather than a claim in a document.
BLENDED_COST_BASES: dict[str, tuple[str, str]] = {
    "2024": ("2024-01", "2024-12"),
    "2025": ("2025-01", "2025-12"),
    "2026_ytd": ("2026-01", "2026-12"),
    "jul25_jun26": ("2025-07", "2026-06"),
}


def blended_cost_by_basis(meters: list[dict]) -> dict[str, dict[str, float]]:
    """Blended delivered cost (p/kWh) on each candidate year basis, from the meter tabs themselves.

    A private-wire residual is `blended − volume-weighted commodity`, so the blended figure IS the
    calibration target. If the target and the volume basis come from different years the residual is
    wrong by the difference, which is exactly what happened at both private-wire sites.
    """
    out: dict[str, dict[str, float]] = {}
    for label, (lo, hi) in BLENDED_COST_BASES.items():
        kwh = cost = 0.0
        months = 0
        for meter in meters:
            for row in meter.get("monthly", []):
                if lo <= row["month"] <= hi and row["cost_gbp"] is not None:
                    kwh += row["kwh"]
                    cost += row["cost_gbp"]
                    months += 1
        if kwh <= 0:
            continue
        out[label] = {
            "kwh": round(kwh, 1),
            "cost_gbp": round(cost, 2),
            "blended_p_per_kwh": round(100 * cost / kwh, 5),
            "meter_months": months,
        }
    return out


#: Per-site caveats appended to the loader-visible `source` string. `source` is one of the few
#: fields `reconciliation.invoice_evidence_from_mapping` actually carries onto `InvoiceEvidence`, so
#: a caveat placed here travels with the evidence instead of living only in a metadata block a
#: reader has to go looking for.
SOURCE_CAVEATS: dict[str, str] = {
    "grundon_bishops_cleeve_ash_plant": "ONE meter, ONE MPAN (1420398100000, 93 kVA), verbatim — "
    "no line is apportioned or shared with the MRF site. This is NOT the retired combined Bishops "
    "Cleeve invoice: see the '_bishops_cleeve_split' block for the 32,904.7 kWh / £14,707.27 on "
    "three further MPANs that belong to neither new site.",
    "grundon_bishops_cleeve_mrf": "ONE meter, ONE MPAN (1460001884093, 250 kVA), verbatim — no "
    "line is apportioned or shared with the Ash Plant site. This is NOT the retired combined "
    "Bishops Cleeve invoice: see the '_bishops_cleeve_split' block for the 32,904.7 kWh / "
    "£14,707.27 on three further MPANs that belong to neither new site.",
    "grundon_knowl_hill": "GRID IMPORT ONLY, and complete for what it is: one grid MPAN "
    "(2000053471776), all 12 months of 2025. It covers 7.9% of the site's 2025 energy — the same "
    "tab records 531,680.9 kWh of on-site LFG SELF-SUPPLY, which carries no supplier invoice and "
    "must never be priced as purchased import. Grid import also roughly TRIPLED into 2026, so a "
    "calendar-2025 invoice is not the same regime as a 2026 metered year. See "
    "'_knowl_hill_coverage'.",
}


def _verify_bishops_cleeve_split(grundon: dict, out: dict[str, dict]) -> dict[str, object]:
    """Prove the split loses nothing, and record the residual with its arithmetic.

    The two new sites do NOT sum to the retired combined figure and are not made to. Three further
    Bishops Cleeve meters carry 2025 volume and belong to neither new boundary, so the difference is
    recorded here in full and asserted to the penny — a machine-checked residual, not a prose claim.

    Deliberately NOT written into either site's ``documented_omissions``: that list feeds
    `reconciliation._itemisation`, which converts an unexplained cost undershoot into an "accounted
    for" WARNING. £14,707 of other meters' cost against Ash Plant's £20,535 invoice would be a
    72%-of-invoice cushion for undershoots those meters have nothing to do with.
    """
    combined = grundon["bishops_cleeve"]
    assigned = {
        site_id: out[site_id]
        for site_id in ("grundon_bishops_cleeve_ash_plant", "grundon_bishops_cleeve_mrf")
    }
    unassigned = [_meter(tab) for tab in BISHOPS_CLEEVE_UNASSIGNED_METERS]

    assigned_kwh = sum(float(e["invoiced_kwh"]) for e in assigned.values())
    assigned_gbp = sum(float(e["invoiced_cost_gbp"]) for e in assigned.values())
    residual_kwh = sum(float(m.get("kwh_2025") or 0.0) for m in unassigned)
    residual_gbp = sum(float(m.get("cost_2025_gbp") or 0.0) for m in unassigned)

    # The retired combined figures, recomputed from the same meter tabs rather than pasted in.
    combined_kwh = float(combined["kwh_2025_total"])
    combined_gbp = float(combined["cost_2025_total_gbp"])
    assert abs((assigned_kwh + residual_kwh) - combined_kwh) < 0.05, (
        f"Bishops Cleeve split loses volume: assigned {assigned_kwh:,.1f} + residual "
        f"{residual_kwh:,.1f} != combined {combined_kwh:,.1f} kWh"
    )
    assert abs((assigned_gbp + residual_gbp) - combined_gbp) < 0.02, (
        f"Bishops Cleeve split loses cost: assigned £{assigned_gbp:,.2f} + residual "
        f"£{residual_gbp:,.2f} != combined £{combined_gbp:,.2f}"
    )
    for tab in BISHOPS_CLEEVE_NO_2025_DATA:
        assert not _meter(tab).get("kwh_2025"), (
            f"{tab} now carries 2025 volume; it is listed as having none and the split arithmetic "
            "no longer holds."
        )

    return {
        "why": "Bishops Cleeve is TWO sites. The metered half-hourly series show the Ash Plant and "
        "MRF supplies running concurrently 91.7% of the time, which rules out a parent/child "
        "reading (scripts/convert_grundon_hh.py). Each new site's invoice is ONE meter tab taken "
        "verbatim; nothing is apportioned between them.",
        "retired_site_id": "grundon_bishops_cleeve",
        "retired_combined_2025": {
            "kwh": round(combined_kwh, 1),
            "cost_gbp": round(combined_gbp, 2),
        },
        "assigned_2025": {
            site_id: {"kwh": e["invoiced_kwh"], "cost_gbp": e["invoiced_cost_gbp"]}
            for site_id, e in assigned.items()
        },
        "unassigned_2025": {
            "kwh": round(residual_kwh, 1),
            "cost_gbp": round(residual_gbp, 2),
            "share_of_retired_combined_pct": round(100 * residual_kwh / combined_kwh, 2),
            "meters": [
                {
                    "tab": m["tab"],
                    "mpan": m.get("mpan"),
                    "kva": m.get("kva"),
                    "kwh_2025": m.get("kwh_2025"),
                    "cost_2025_gbp": m.get("cost_2025_gbp"),
                    "reason_unassigned": BISHOPS_CLEEVE_UNASSIGNED_METERS[m["tab"]],
                }
                for m in unassigned
            ],
        },
        "no_2025_data": BISHOPS_CLEEVE_NO_2025_DATA,
        "arithmetic": f"{assigned_kwh:,.1f} assigned + {residual_kwh:,.1f} unassigned = "
        f"{combined_kwh:,.1f} kWh; £{assigned_gbp:,.2f} + £{residual_gbp:,.2f} = "
        f"£{combined_gbp:,.2f}. Asserted to the penny by _verify_bishops_cleeve_split.",
        "why_not_documented_omissions": "This residual is NOT written into either site's "
        "documented_omissions. That list feeds reconciliation._itemisation, which uses it to turn "
        "an unexplained cost undershoot into an 'accounted for' WARNING. £14,707 of other meters' "
        "cost against Ash Plant's £20,535 invoice would be a 72%-of-invoice cushion for "
        "undershoots those meters have nothing to do with — a gate loosened by a figure that is "
        "not an omission from either site's modelled stack. Recorded here, consumed by nothing.",
        "confirmation_ask": "Confirm which supply serves the weighbridge and the lodge, and "
        "whether BC Import is a standby/top-up supply to the Ash Plant, the MRF or the site as a "
        "whole. "
        "Until then their 32,904.7 kWh / £14,707.27 is attributed to NEITHER new site: attributing "
        "it on a guess would price demand a site did not incur. Also confirm the kVA on BC "
        "Weighbridge and BC Lodge, which the workbook does not state.",
        "site_model_inputs": {
            "grundon_bishops_cleeve_ash_plant": {"mpan": "1420398100000", "kva": 93},
            "grundon_bishops_cleeve_mrf": {"mpan": "1460001884093", "kva": 250},
            "note": "Each new site is exactly ONE MPAN with its own stated kVA, so the retired "
            "site's '4 omitted per-MPAN standing lines' (£18,061.82) and its capacity-charge "
            "omission (£9,060.54, charged on BC Import 230 kVA + Ash Plant 93 kVA) no longer apply "
            "and are NOT carried forward. If a site model fails to charge its own standing and "
            "capacity lines, Gate 3b will now correctly report the shortfall as UNEXPLAINED "
            "instead of excusing it.",
        },
    }


def _knowl_hill_coverage() -> dict[str, object]:
    """What Knowl Hill's invoice does and does not cover, from the tab's own two series.

    The tab is titled "Knowl Hill (Import & Self-Supply)" and carries a SECOND monthly series
    alongside the grid one. The grid invoice is complete for its single MPAN; it is simply not the
    site's energy. Recorded rather than merged: the grid cost is real and the self-supply cost cell
    is provably broken, so a combined figure could only be fabricated.
    """
    raw = pd.read_excel(GRUNDON_XLSX, sheet_name="Knowl Hill Import", header=None)
    # The self-supply block's own header row, located rather than assumed.
    hdr = next(
        i
        for i in range(len(raw))
        if str(raw.iloc[i, 9]).strip() == "Date" and str(raw.iloc[i, 10]).strip() == "kWh"
    )
    kwh = cost = 0.0
    months = 0
    for i in range(hdr + 1, len(raw)):
        stamp = pd.to_datetime(raw.iloc[i, 9], errors="coerce")
        k = _num(raw.iloc[i, 10])
        if pd.isna(stamp) or stamp.year != 2025 or k is None:
            continue
        kwh += k
        cost += _num(raw.iloc[i, 11]) or 0.0
        months += 1

    grid_kwh = float(_meter("Knowl Hill Import")["kwh_2025"])
    grid_gbp = float(_meter("Knowl Hill Import")["cost_2025_gbp"])
    assert months == 12, f"Knowl Hill self-supply has {months} months of 2025, expected 12"
    return {
        "finding": "The invoice is NOT partial. Knowl Hill has ONE grid MPAN (2000053471776) with "
        "all 12 months of 2025 present, and the metered half-hourly series agrees with it MONTH BY "
        "MONTH to ~0.1% across the eleven overlapping months. The only other Knowl Hill tab, "
        "'Knowl Hill OLD' (MPAN 2000010000555), is marked 'No longer used' and ends in 2021.",
        "invoiced_grid_2025": {"kwh": round(grid_kwh, 1), "cost_gbp": round(grid_gbp, 2)},
        "self_supply_2025": {
            "kwh": round(kwh, 1),
            "months": months,
            "source": "the same tab's second monthly series (columns J-N, 'Date/kWh/Cost/kgCO2e'), "
            "which the tab's own 'Total kWh Self-Supply 2025' cell states as 531,680.9 — an exact "
            "match to the sum of the twelve monthly rows.",
            "corroboration": "The '2025 Summary' tab's Knowl Hill row gives Grid 45,732.3 (EXACT "
            "match) and LFG/On-Site Gen 489,319.7, which is the same series less December "
            "(42,361.2) — an 11-month sum, the same class of stale roll-up that tab already shows "
            "elsewhere. The per-meter tab remains the volume authority.",
            "cost_gbp": None,
            "cost_absent_reason": "NOT RECORDED, because the workbook's figure is provably wrong: "
            f"the 'Total Self-Supply Cost' cell reads £10,689.12 for 2025 while the monthly column "
            f"sums to £{cost:,.2f}, and the identical £10,689.12 appears as the 2026 total and as "
            "the January 2024 monthly value — a stale single-month reference, not a year total. "
            "'Combined Site Costs 2025' (£39,903.35) inherits the same broken cell, so no combined "
            "delivered cost can be taken from this workbook.",
        },
        "grid_share_of_site_energy_pct": round(100 * grid_kwh / (grid_kwh + kwh), 1),
        "why_invoiced_kwh_is_unchanged": "invoiced_kwh stays 45,732.3 — the grid invoice verbatim. "
        "Inflating it to the 577,413.2 kWh combined figure against a GRID-ONLY cost, with a "
        "provably broken self-supply cost cell, would be fabrication. Self-supplied energy carries "
        "no supplier invoice and must never be priced as purchased import (the Tanhouse defect: "
        "139,172 kWh of the customer's own solar priced as bought energy).",
        "base_year_warning": "Grid import roughly TRIPLED after the base year. Calendar 2025 grid "
        "= 45,732.3 kWh; January-June 2026 alone = 73,822.7 kWh, and April 2026 alone is 28,622.8 "
        "kWh — more than half of the entire 2025 year. A modelled load taken from the Aug-2025 to "
        "Aug-2026 metered window (108,242 kWh) will therefore breach Gate 3b's volume limb against "
        "this calendar-2025 invoice by roughly +137%. That is a real regime change recorded "
        "honestly, NOT a reason to re-base either side onto the other.",
        "confirmation_ask": "Confirm which basis year the underwriting should use at Knowl Hill, "
        "and supply 2026 invoices for MPAN 2000053471776 if the 2026 grid-import regime is the one "
        "to price. Confirm too whether the 531,680.9 kWh of LFG self-supply carries any internal "
        "transfer price, since the workbook's own figure for it is a broken cell.",
    }


def build_invoice_evidence(grundon: dict) -> dict:
    """Assemble the per-site invoice evidence the bill reconciliation gate consumes.

    Per site, never portfolio. `bill_reconciliation.md` §5: the portfolio total agrees to +0.7%
    while four individual sites are off by −25.9% to +11.7% — a portfolio gate would have passed
    four large errors cancelling as a validation.

    Keyed by `SiteModel.site_id`, because that is how Gate 3b looks evidence up
    (`quotes/pipeline._invoice_evidence_for`). A key that is not a live site_id is evidence nothing
    will ever read, and the site resolves a "NO INVOICE EVIDENCE" absence record instead.
    """
    # The derived Capacity Market line must be the SAME construction §6 used, not a new one.
    for site_id, (kwh, stated_gbp) in _CM_TRANSCRIPTION_CHECKS.items():
        got = _omission_gbp(_cm_supplier_charge_omission(kwh))
        assert abs(got - stated_gbp) < 0.02, (
            f"_cm_supplier_charge_omission no longer reproduces 06_bill_reconciliation.md §6 for "
            f"{site_id}: {got:,.2f} vs {stated_gbp:,.2f}. The derived Bishops Cleeve lines would "
            "no longer be the reviewed construction."
        )

    out: dict[str, dict] = {
        "_basis": {
            "period": "calendar 2025",
            "source_workbook": "examples/real/Grundon/NEW Electricity Usage and Costs.xlsx",
            "keyed_by": "SiteModel.site_id — Gate 3b resolves evidence by site_id in "
            "gridq.quotes.pipeline._invoice_evidence_for, so a key that is not a live site_id is "
            "evidence nothing looks for and the site silently resolves an absence record.",
            "invoiced_kwh_and_cost": "the per-meter tabs' own monthly figures, summed across the "
            "meters that belong to THAT SITE'S boundary. Nothing is apportioned: where a meter "
            "cannot be assigned to a site on evidence it is excluded and recorded, never split on "
            "a guess. Meter-months with no kWh reading are absent, not zero, so their cost is "
            "absent too (06_bill_reconciliation.md §3.6).",
            "site_boundary": "Bishops Cleeve was re-split per meter on 2026-08-15 after the "
            "metered half-hourly data showed Ash Plant and MRF running concurrently 91.7% of the "
            "time. The combined id 'grundon_bishops_cleeve' is RETIRED to an explicit absence "
            "record; the per-meter tabs are what make the split possible, and each new site's "
            "invoice is one meter tab verbatim. See '_bishops_cleeve_split'.",
            "documented_omissions": "transcribed from 06_bill_reconciliation.md §6 and asserted "
            "against its stated subtotals, EXCEPT the two new Bishops Cleeve sites, which post-"
            "date that document: their single Capacity Market line is derived at 1.1 p/kWh x "
            "invoiced kWh, and the derivation is asserted to reproduce §6's Ewelme and Knowl Hill "
            "lines exactly. The retired site's two multi-MPAN omission lines (£27,122.36) are NOT "
            "carried forward — each new site is one MPAN, so nothing is omitted, and the "
            "explanation cushion is deliberately not replaced.",
            "gate": "gridq.ingestion.reconciliation.reconcile_bill_against_invoice",
            "written_by": "scripts/extract_site_data.py",
        }
    }
    for site_id, tabs in INVOICE_SITE_METERS.items():
        meters = [_meter(t) for t in tabs]
        monthly = _monthly_rollup(meters)
        invoiced_kwh = round(sum(float(m.get("kwh_2025") or 0.0) for m in meters), 1)
        invoiced_gbp = round(sum(float(m.get("cost_2025_gbp") or 0.0) for m in meters), 2)

        omissions = list(DOCUMENTED_OMISSIONS.get(site_id, []))
        if site_id in DERIVE_CM_OMISSION_FOR:
            omissions.append(_cm_supplier_charge_omission(invoiced_kwh))
        expected = _OMISSION_SUBTOTAL_CHECKS.get(site_id)
        if expected is not None:
            got = sum(_omission_gbp(o) for o in omissions)
            assert abs(got - expected) < 0.02, (
                f"{site_id}: transcribed omissions sum to {got:,.2f}, but "
                f"bill_reconciliation.md §6 states {expected:,.2f}"
            )

        source = f"per-meter tabs {[m['tab'] for m in meters]}, calendar 2025"
        if site_id in SOURCE_CAVEATS:
            source = f"{source}. {SOURCE_CAVEATS[site_id]}"
        out[site_id] = {
            "invoice_available": True,
            "period": "calendar 2025",
            "invoiced_kwh": invoiced_kwh,
            "invoiced_cost_gbp": invoiced_gbp,
            "monthly_cost_gbp": {m: v["cost_gbp"] for m, v in monthly.items() if v["cost_gbp"]},
            "monthly_kwh": {m: v["kwh"] for m, v in monthly.items()},
            "excluded_months": EXCLUDED_MONTHS.get(site_id, []),
            "documented_omissions": omissions,
            # The calibration target, on every candidate year basis. A private-wire residual is
            # back-solved from the blended figure, so the basis it was taken from decides whether
            # the residual describes the year the volume comes from.
            "blended_cost_by_basis": blended_cost_by_basis(meters),
            "source": source,
        }
    for site_id, reason in NO_INVOICE_SITES.items():
        out[site_id] = {
            "invoice_available": False,
            "period": "calendar 2025",
            "absence_reason": reason,
        }
    # A retired boundary is an EXPLICIT absence, never a deletion and never a live invoice: a stale
    # SiteModel still carrying the old id then gets a reasoned absence and a LOW ceiling, and can
    # never reconcile against a combined figure for a site that no longer exists.
    for site_id, reason in RETIRED_SITE_IDS.items():
        out[site_id] = {
            "invoice_available": False,
            "period": "calendar 2025",
            "absence_reason": reason,
        }
    out["_bishops_cleeve_split"] = _verify_bishops_cleeve_split(grundon, out)
    out["_knowl_hill_coverage"] = _knowl_hill_coverage()
    return out


def _verify_tanhouse(grundon: dict) -> None:
    """Two-tab corroboration for the Tanhouse split, loud on failure.

    The per-meter tab (12 monthly rows) and the `'2025 Summary'` tab (one annual EfW cell) are
    independent places in the workbook. They must agree, and the solar cell must account for the
    whole difference against the `Totals`-tab roll-up the site model currently prices.
    """
    site = grundon["tanhouse"]
    meter_tab_kwh = site["kwh_2025_total"]
    split = site["meters"][0]["supply_split_2025"]
    efw, solar, total = split["efw_kwh"], split["solar_kwh"], split["total_kwh"]

    assert abs(meter_tab_kwh - efw) < 0.5, (
        f"Tanhouse per-meter tab 2025 = {meter_tab_kwh:,.1f} kWh but the '2025 Summary' EfW cell "
        f"= {efw:,.1f} kWh. Two tabs that must agree do not."
    )
    assert abs((efw + solar) - total) < 0.5, (
        f"'2025 Summary' Tanhouse row does not add up: {efw:,.2f} + {solar:,.2f} != {total:,.2f}"
    )
    assert split["grid_kwh"] == 0, "Tanhouse is recorded as 100% non-grid; a grid figure appeared."
    print(
        f"\n  Tanhouse two-tab check OK: per-meter tab {meter_tab_kwh:,.1f} kWh == "
        f"'2025 Summary' EfW {efw:,.1f} kWh (PURCHASED). "
        f"Solar {solar:,.2f} kWh is SELF-GENERATED and is {100 * solar / efw:.2f}% of purchased "
        f"volume / {100 * solar / total:.2f}% of gross consumption {total:,.2f} kWh."
    )


if __name__ == "__main__":
    harwell = extract_harwell()
    grundon = extract_grundon()
    _verify_tanhouse(grundon)
    invoices = build_invoice_evidence(grundon)
    (OUT / "harwell_summary.json").write_text(json.dumps(harwell, indent=2))
    (OUT / "grundon_monthly.json").write_text(json.dumps(grundon, indent=2))
    (OUT / "grundon_invoices_2025.json").write_text(json.dumps(invoices, indent=2) + "\n")

    print("=== HARWELL ===")
    print(json.dumps(harwell, indent=2))
    print("\n=== GRUNDON 2025 by site ===")
    for site, d in grundon.items():
        print(f"\n{site}: {d['kwh_2025_total']:,.0f} kWh  £{d['cost_2025_total_gbp']:,.0f}")
        print(
            f"   '2025 Summary' split: purchased {d['summary_tab_purchased_kwh_2025']:>12,.1f} kWh "
            f"| self-generated {d['summary_tab_self_generated_kwh_2025']:>12,.1f} kWh "
            f"| purchased − meter tabs {d['summary_tab_purchased_minus_meter_tabs_kwh']:+,.1f} kWh"
        )
        for m in d["meters"]:
            print(
                f"   {m['tab']:<28} mpan={m.get('mpan', '-'):<15} kva={m.get('kva')} "
                f"2025={m.get('kwh_2025', 0):>12,.0f} kWh  "
                f"{m.get('blended_p_per_kwh_2025', '-')} p/kWh  months={m.get('months_2025')}"
            )
    print("\n=== INVOICE EVIDENCE (per site, never portfolio) ===")
    for site_id, ev in invoices.items():
        if site_id.startswith("_"):
            continue
        if not ev["invoice_available"]:
            print(f"   {site_id:<28} NO INVOICE — recorded absence, never 'reconciled'")
            continue
        omit = sum(_omission_gbp(o) for o in ev["documented_omissions"])
        print(
            f"   {site_id:<28} {ev['invoiced_kwh']:>12,.0f} kWh  "
            f"£{ev['invoiced_cost_gbp']:>10,.0f}  "
            f"{len(ev['monthly_cost_gbp'])} months  documented omissions £{omit:,.0f}"
        )
    print(f"\nWritten to {OUT}")
