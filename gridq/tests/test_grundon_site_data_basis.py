"""The Grundon load basis: what volume is PURCHASED, what is self-generated, and where it came from.

Three defects from `bill_reconciliation.md` are pinned here, because each was invisible until
somebody compared two sources by hand:

1. **§3.2 — a claimed data gap that was a code omission.** `data/sites/hh/manifest.json` and
   `examples/real/sites/tanhouse.json` both stated "no per-meter tab exists in the workbook" and the
   manifest called monthly totals "the single highest-value data ask for this site". The tab existed
   all along — `'Tanhouse (MRF, WS, Depot)'`, MPAN 2000052173090 — and was simply absent from
   `GRUNDON_SITES` in `scripts/extract_site_data.py`. Grundon were being asked for data already in
   hand, and Tanhouse's seasonal shape was modelled when it could be measured.

2. **§3.3 — 9.03% of the modelled Tanhouse load was the customer's own solar.** The `'2025 Summary'`
   tab decomposes the 1,680,625.68 kWh roll-up the site model priced into 1,541,453.4 kWh purchased
   EfW + 139,172.28 kWh self-generated solar. A battery cannot avoid a cost that was never charged,
   so the priced series must be the purchased volume — but the solar is still consumed, so it is
   RECORDED rather than deleted. Representing it properly is blocked on D-174.

3. Both figures corroborate across two independent tabs, and `test_two_tab_corroboration` pins that:
   the per-meter tab's twelve monthly rows sum to exactly the `'2025 Summary'` EfW cell.

These tests read the generated artifacts, so they fail if `scripts/extract_site_data.py` or
`scripts/generate_grundon_hh.py` is run with a changed basis and the outputs are not regenerated.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from tests._dpa_support import dpa_released

REPO = Path(__file__).resolve().parents[1]
SITES = REPO / "data" / "sites"
MONTHLY = SITES / "grundon_monthly.json"
MANIFEST = SITES / "hh" / "manifest.json"
#: Both files, and every series the manifest names, are quarantined customer data (WP-GRC-05):
#: read only through `dpa_released`, so a test that needs one skips, naming its blocker, until the
#: customer's DPA is registered.

TANHOUSE_TAB = "Tanhouse (MRF, WS, Depot)"
TANHOUSE_MPAN = "2000052173090"
PURCHASED_KWH = 1_541_453.4
SOLAR_KWH = 139_172.28
GROSS_KWH = 1_680_625.68
#: The `Totals`-tab roll-up the site model priced as purchased private-wire import.
OLD_ROLLUP_KWH = 1_680_626.0


def _script(name: str) -> Any:
    """Import a `scripts/` module by path so its constants are pinned, not string-matched."""
    spec = importlib.util.spec_from_file_location(f"_gridq_script_{name}", REPO / "scripts" / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def monthly() -> dict:
    return json.loads(dpa_released(MONTHLY).read_text())


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict]:
    return {entry["site"]: entry for entry in json.loads(dpa_released(MANIFEST).read_text())}


# =============================================================================================
# §3.2 — the tab exists and is extracted.
# =============================================================================================


def test_the_tanhouse_per_meter_tab_is_extracted() -> None:
    """THE REGRESSION. A data ask sent to the client for a tab that was already in the workbook."""
    sites = _script("extract_site_data.py").GRUNDON_SITES
    assert TANHOUSE_TAB in sites.get("tanhouse", []), (
        "the Tanhouse per-meter tab must be listed in GRUNDON_SITES; its absence is what made the "
        "pipeline report a non-existent data gap"
    )


def test_the_extracted_tanhouse_totals_are_the_invoice_figures(monthly) -> None:
    site = monthly["tanhouse"]
    meter = site["meters"][0]
    assert meter["tab"] == TANHOUSE_TAB
    assert meter["mpan"] == TANHOUSE_MPAN, "same MPAN examples/real/sites/tanhouse.json records"
    assert meter["meter_no"] == "K12C03674", (
        "the meter identity must survive extraction — the tab labels it 'Meter Number:', which the "
        "old startswith('meter no') scan silently dropped"
    )
    assert meter["months_2025"] == 12, "twelve MEASURED monthly totals, not an annual figure"
    assert site["kwh_2025_total"] == pytest.approx(PURCHASED_KWH, abs=0.05)
    assert site["cost_2025_total_gbp"] == pytest.approx(346_973.58, abs=0.005)


def test_the_purchased_basis_is_below_the_roll_up_the_model_priced(monthly) -> None:
    """Direction stated: this REDUCES the modelled volume by 8.28%, so it reduces avoided cost."""
    purchased = monthly["tanhouse"]["kwh_2025_total"]
    assert purchased < OLD_ROLLUP_KWH
    assert (OLD_ROLLUP_KWH - purchased) == pytest.approx(SOLAR_KWH, abs=0.5), (
        "the whole difference between the roll-up and the per-meter tab is the solar — the two "
        "defects are one arithmetic seen from opposite sides"
    )
    assert (OLD_ROLLUP_KWH - purchased) / OLD_ROLLUP_KWH * 100 == pytest.approx(8.28, abs=0.01)
    assert SOLAR_KWH / purchased * 100 == pytest.approx(9.03, abs=0.01)


# =============================================================================================
# §3.3 — the purchased / self-generated split.
# =============================================================================================


def test_two_tab_corroboration(monthly) -> None:
    """The per-meter tab's 12 rows and the `'2025 Summary'` EfW cell are independent, and agree."""
    site = monthly["tanhouse"]
    split = site["meters"][0]["supply_split_2025"]
    assert split["status"] == "extracted"
    assert split["efw_kwh"] == pytest.approx(PURCHASED_KWH, abs=0.005)
    assert split["solar_kwh"] == pytest.approx(SOLAR_KWH, abs=0.005)
    assert split["grid_kwh"] == 0, "Tanhouse is recorded as 100% non-grid"
    assert split["total_kwh"] == pytest.approx(GROSS_KWH, abs=0.005)
    assert split["purchased_minus_meter_tab_kwh"] == pytest.approx(0.0, abs=0.05), (
        "the summary tab's purchased figure must equal the per-meter tab's own sum"
    )


def test_the_split_is_recorded_per_site_not_only_per_meter(monthly) -> None:
    site = monthly["tanhouse"]
    assert site["summary_tab_purchased_kwh_2025"] == pytest.approx(PURCHASED_KWH, abs=0.005)
    assert site["summary_tab_self_generated_kwh_2025"] == pytest.approx(SOLAR_KWH, abs=0.005)


def test_the_summary_tab_is_not_treated_as_a_volume_authority(monthly) -> None:
    """It disagrees with the per-meter tabs at Bishops Cleeve, and the disagreement is recorded.

    Ash Plant 1 is 33,534 kWh in the summary against 66,765.6 in its own tab, and Lodge is 0 against
    14,940. The extraction keeps the per-meter tabs as the basis and records the delta, so a reader
    sees the conflict instead of a silently chosen winner.
    """
    bc = monthly["bishops_cleeve"]
    assert bc["kwh_2025_total"] == pytest.approx(396_698.7, abs=0.05), "per-meter tabs, unchanged"
    assert bc["summary_tab_purchased_minus_meter_tabs_kwh"] < -40_000, "a recorded disagreement"
    ash = next(m for m in bc["meters"] if m["tab"] == "BC Ash Plant 1")
    assert ash["kwh_2025"] == pytest.approx(66_765.6, abs=0.05)
    assert ash["supply_split_2025"]["purchased_minus_meter_tab_kwh"] == pytest.approx(
        33_534 - 66_765.6, abs=0.05
    )


def test_knowl_hill_self_supply_was_already_excluded_from_the_import_basis(monthly) -> None:
    """The split confirms Tanhouse was the ONLY site whose import basis mixed in self-generation.

    Knowl Hill also has 489,319.7 kWh/yr of LFG self-supply, but its import meter tab carries only
    the 45,732.3 kWh it buys, so its basis was already correct. That is the contrast that makes
    Tanhouse a defect rather than a portfolio-wide modelling choice.
    """
    kh = monthly["knowl_hill"]
    assert kh["summary_tab_self_generated_kwh_2025"] == pytest.approx(489_319.7, abs=0.05)
    assert kh["summary_tab_purchased_minus_meter_tabs_kwh"] == pytest.approx(0.0, abs=0.05)


# =============================================================================================
# The regenerated half-hourly series.
# =============================================================================================


def test_the_tanhouse_season_is_now_measured(manifest) -> None:
    entry = manifest["tanhouse"]
    assert "seasonal (month-to-month) variation" in entry["measured_components"], (
        "with 12 monthly totals the seasonal shape is MEASURED; only the within-month shape is not"
    )
    assert entry["scaling_basis"] == "12 monthly invoice totals, 2025"
    assert entry["total_kwh"] == pytest.approx(PURCHASED_KWH, abs=0.05)
    assert entry["measured_total_kwh"] == pytest.approx(PURCHASED_KWH, abs=0.05)


def test_no_site_is_left_on_an_annual_only_basis(manifest) -> None:
    """Tanhouse was the only one, and it graduated. The weakest basis in the portfolio is gone.

    The manifest also carries bookkeeping entries describing no series at all — `_excluded_meters`
    records which source meters were rejected and why. Those are skipped STRUCTURALLY, on the
    absence of a `file`, never by name: a name-based skip list is a place a real site could hide.
    """
    assert _script("generate_grundon_hh.py").ANNUAL_ONLY_SITES == {}
    series = {name: e for name, e in manifest.items() if "file" in e}
    assert len(series) >= 5, "the skip must not have swallowed the sites it is meant to check"
    for site, entry in series.items():
        assert "seasonal (month-to-month) variation" in entry["measured_components"], site


def test_the_stale_no_per_meter_tab_claim_is_gone() -> None:
    """The manifest asserted a data gap that did not exist. It must not say so any more."""
    text = dpa_released(MANIFEST).read_text()
    assert "No per-meter tab exists" not in text
    assert "no per-meter tab" not in text.lower()
    assert "highest-value data ask for this" not in text, (
        "the ask was closed from data already in the workbook"
    )


def test_the_self_generated_volume_is_recorded_not_deleted(manifest) -> None:
    """The site still consumes the solar. A smaller number with no explanation would lose that."""
    entry = manifest["tanhouse"]
    assert entry["series_basis"] == "PURCHASED IMPORT ONLY"
    gen = entry["onsite_generation_2025"]
    assert gen["self_generated_kwh"] == pytest.approx(SOLAR_KWH, abs=0.005)
    assert gen["purchased_import_kwh"] == pytest.approx(PURCHASED_KWH, abs=0.05)
    assert gen["gross_consumption_kwh"] == pytest.approx(GROSS_KWH, abs=0.05)
    assert gen["self_generated_pct_of_purchased"] == pytest.approx(9.029, abs=0.01)
    assert gen["self_generated_pct_of_gross"] == pytest.approx(8.281, abs=0.01)


def test_no_solar_profile_is_invented(manifest) -> None:
    """Only an annual total exists, in one cell of one tab. Nothing may imply more than that."""
    gen = manifest["tanhouse"]["onsite_generation_2025"]
    assert gen["granularity"] == "annual_total_only"
    assert "'2025 Summary'" in str(gen["source"])
    assert "no monthly or half-hourly solar breakdown" in str(gen["source"])
    assert "D-174" in str(gen["blocked_by"]), "the blocking finding must be named, not implied"
    assert "pv_generation_kw" in str(gen["blocked_by"])
    # A per-month solar series would be a fabrication; the manifest must carry no such thing.
    assert not any("2025-" in str(k) for k in gen)


def test_the_purchased_only_basis_is_warned_about_loudly(manifest) -> None:
    warning = next(w for w in manifest["tanhouse"]["warnings"] if "PURCHASED IMPORT ONLY" in w)
    assert "139,172.28 kWh" in warning
    assert "does not buy it" in warning
    assert "cannot avoid a cost never charged" in warning
    assert "D-174" in warning


def test_the_generated_series_totals_the_purchased_volume(manifest) -> None:
    path = REPO / manifest["tanhouse"]["file"]
    assert path.exists()
    rows = dpa_released(path).read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("import_kw") if "import_kw" in header else header.index("import_kwh")
    factor = 0.5 if header[col] == "import_kw" else 1.0
    total = sum(float(r.split(",")[col]) for r in rows[1:]) * factor
    assert len(rows) - 1 == 17_520, "half-hourly, calendar 2025"
    assert total == pytest.approx(PURCHASED_KWH, rel=1e-9)


def test_the_superseded_series_is_left_in_place(manifest) -> None:
    """`examples/real/sites/tanhouse.json` still points at the old file and is owned elsewhere.

    Deleting it would break that site model before its `half_hourly_file_ref` is updated. The old
    file is superseded, not removed, and this test is the reminder that the swap is still pending.
    """
    old = SITES / "hh" / "grundon_tanhouse_modelled_2025_138b75ff5c2c0719.csv"
    assert old.exists(), "do not delete until tanhouse.json's half_hourly_file_ref is updated"
    assert manifest["tanhouse"]["file"] != str(old.relative_to(REPO))


@pytest.mark.parametrize(
    ("site", "total_kwh", "peak_kw"),
    [
        ("bishops_cleeve", 387_008.155, 125.871),
        ("tanhouse_colnbrook", 3_032_360.2, 915.077),
        ("knowl_hill", 45_732.3, 22.619),
        ("ewelme", 694_062.0, 181.184),
    ],
)
def test_the_other_four_series_are_untouched(manifest, site, total_kwh, peak_kw) -> None:
    """Only Tanhouse's basis changed. Churn anywhere else would mean an unintended input moved."""
    entry = manifest[site]
    assert entry["total_kwh"] == pytest.approx(total_kwh, abs=0.001)
    assert entry["peak_kw"] == pytest.approx(peak_kw, abs=0.001)


def test_the_colnbrook_rationale_no_longer_claims_tanhouse_has_no_tab(manifest) -> None:
    rationale = manifest["tanhouse_colnbrook"]["profile_rationale"]
    assert "COLNBROOK ONLY" in rationale, "the key still covers one site; that part was true"
    assert "no per-meter tab" not in rationale.lower()
    assert "separate `tanhouse` entry" in rationale
