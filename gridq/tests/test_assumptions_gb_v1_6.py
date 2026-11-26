"""Tests for the gb_v1_6 assumption release (Revenue-Data Drive slice 4).

gb_v1_6 is the FETCHER-BACKED successor to gb_v1_5: four revenue leaves are re-sourced from live
perimeter fetches (NESO CKAN + Piclo), each transcribed against a committed provenance sidecar;
wholesale + BM stay a deliberate zero (source/notes only); every other gb_v1_5 value is carried
forward unchanged. It still confirms NOTHING (0 confirmed repo-wide) and the app stays pinned to
gb_v1_4, so this release is invisible to the golden. These tests lock all of that.
"""

from __future__ import annotations

from datetime import date

import pytest

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import (
    OPTIONAL_SECTION_NAMES,
    SECTION_NAMES,
    Assumption,
    AssumptionStatus,
    load_assumptions,
)
from gridq.schemas import ConsentStatus, Firmness, MarketStream
from gridq.schemas.market import GATED_UPSIDE_STREAMS, ValueStreamResult
from gridq.value_stack import GateStatus, default_registry

_ALL_SECTIONS = tuple(SECTION_NAMES) + tuple(OPTIONAL_SECTION_NAMES)

# The four leaves re-sourced from a live fetch this release, and the value each landed at.
_FETCHED_CHANGES: dict[tuple[str, str], object] = {
    ("capacity_market", "clearing_price_gbp_per_kw_yr"): 27.1,
    ("capacity_market", "de_rating_factor_by_duration_h"): {"1": 0.1099, "2": 0.2198, "4": 0.4396},
    ("ancillary_services", "dc_dm_dr_value_gbp_per_mw_yr"): 28296.9,
    ("local_flex_piclo", "utilisation_price_gbp_per_mwh"): 135.67,
}

# Locked entry mix (across required + optional sections) — drift is caught here.
_TOTAL_ENTRIES = 40
_NEEDS_REVIEW = 10
_PLACEHOLDER = 30
_CONFIRMED = 0


def _entries(lib) -> list[tuple[str, str, Assumption]]:  # type: ignore[no-untyped-def]
    return [(sec, name, a) for sec in _ALL_SECTIONS for name, a in getattr(lib, sec).items()]


# --- Load / validate / sections ---------------------------------------------


def test_gb_v1_6_loads_with_all_sections() -> None:
    lib = load_assumptions("gb_v1_6")
    assert lib.metadata.version == "gb_v1_6"
    assert lib.metadata.market == "GB"
    assert lib.metadata.released == date(2026, 7, 20)
    for section in SECTION_NAMES:
        assert getattr(lib, section), f"required section {section} is empty"


def test_gb_v1_6_entry_and_status_counts_locked() -> None:
    entries = _entries(load_assumptions("gb_v1_6"))
    assert len(entries) == _TOTAL_ENTRIES
    statuses = [a.status for _, _, a in entries]
    needs_review = sum(x == AssumptionStatus.NEEDS_REVIEW for x in statuses)
    placeholder = sum(x == AssumptionStatus.PLACEHOLDER for x in statuses)
    confirmed = sum(x == AssumptionStatus.CONFIRMED for x in statuses)
    print(
        f"gb_v1_6 status mix: needs_review={needs_review} "
        f"placeholder={placeholder} confirmed={confirmed}"
    )
    assert (needs_review, placeholder, confirmed) == (_NEEDS_REVIEW, _PLACEHOLDER, _CONFIRMED)


def test_gb_v1_6_confirms_nothing() -> None:
    # The not-bankable posture is untouched: 0 confirmed, repo-wide.
    for section, name, a in _entries(load_assumptions("gb_v1_6")):
        assert a.status != AssumptionStatus.CONFIRMED, (
            f"{section}.{name} is confirmed — must not be"
        )


def test_gb_v1_6_every_entry_fully_documented() -> None:
    for section, name, a in _entries(load_assumptions("gb_v1_6")):
        where = f"{section}.{name}"
        assert a.source.strip(), f"{where} empty source"
        assert a.notes.strip(), f"{where} empty notes"


# --- The fetched values + their provenance ----------------------------------


def test_gb_v1_6_fetched_values_landed() -> None:
    lib = load_assumptions("gb_v1_6")
    for (section, name), expected in _FETCHED_CHANGES.items():
        assert lib.get(section, name).value == expected, f"{section}.{name} != fetched value"


def test_gb_v1_6_de_rating_keys_are_exactly_1_2_4() -> None:
    factors = (
        load_assumptions("gb_v1_6").get("capacity_market", "de_rating_factor_by_duration_h").value
    )
    assert set(factors) == {"1", "2", "4"}


def test_gb_v1_6_changed_entries_carry_full_fetch_provenance() -> None:
    # Every re-sourced entry must cite URL + sha256(12) + fetched-date + the committed sidecar path,
    # and be a fresh review (needs_review, reviewed 2026-07-20).
    lib = load_assumptions("gb_v1_6")
    for section, name in _FETCHED_CHANGES:
        a = lib.get(section, name)
        src = a.source
        assert "http" in src, f"{section}.{name} source lacks a URL"
        assert "sha256:" in src, f"{section}.{name} source lacks a sha256"
        assert "2026-07-20" in src, f"{section}.{name} source lacks the fetched date"
        assert "assumptions/provenance/gb_v1_6/" in src, (
            f"{section}.{name} source lacks the sidecar path"
        )
        assert a.status == AssumptionStatus.NEEDS_REVIEW
        assert a.last_reviewed == date(2026, 7, 20)


def test_gb_v1_6_provenance_sidecars_exist_and_pin_the_fetch() -> None:
    import json
    from pathlib import Path

    prov = Path(__file__).resolve().parents[1] / "assumptions" / "provenance" / "gb_v1_6"
    for name in ("cm_clearing", "cm_derating", "eac_results", "piclo_competitions"):
        side = json.loads((prov / f"{name}.provenance.json").read_text())
        assert len(side["sha256"]) == 64
        assert side["size_bytes"] > 0
        assert side["record_count"] > 0
        assert side["fetched_utc"].startswith("2026-07-20")
        assert side["source_url"].startswith("http")


# --- Wholesale + BM stay exactly zero (deliberate gate, not a missing source) -


def test_gb_v1_6_wholesale_and_bm_are_exactly_zero() -> None:
    lib = load_assumptions("gb_v1_6")
    assert lib.get("wholesale_p415", "gated_upside_value_gbp_per_mw_yr").value == 0
    assert lib.get("balancing_mechanism", "gated_upside_value_gbp_per_mw_yr").value == 0


# --- Priors carried forward unchanged from gb_v1_5 --------------------------


def test_gb_v1_6_priors_identical_to_gb_v1_5_except_the_fetched_leaves() -> None:
    v5 = load_assumptions("gb_v1_5")
    v6 = load_assumptions("gb_v1_6")
    changed_or_relabelled = set(_FETCHED_CHANGES) | {
        ("wholesale_p415", "gated_upside_value_gbp_per_mw_yr"),
        ("balancing_mechanism", "gated_upside_value_gbp_per_mw_yr"),
    }
    v5_map = {(sec, n): a for sec, n, a in _entries(v5)}
    v6_map = {(sec, n): a for sec, n, a in _entries(v6)}
    assert set(v5_map) == set(v6_map), "the entry key set must not change between v5 and v6"
    for key in v5_map:
        if key in changed_or_relabelled:
            continue
        assert v5_map[key].value == v6_map[key].value, f"prior {key} value drifted"
        assert v5_map[key].source == v6_map[key].source, f"prior {key} source drifted"
        assert v5_map[key].notes == v6_map[key].notes, f"prior {key} notes drifted"


def test_gb_v1_6_spot_check_untouched_sections() -> None:
    v5 = load_assumptions("gb_v1_5")
    v6 = load_assumptions("gb_v1_6")
    for section, name in [
        ("battery_capex", "installed_cost"),
        ("financing", "target_equity_irr_pct"),
        ("p50_p90_haircuts", "p90_revenue_haircut_pct"),
        ("duos", "red_band_avoidance_gbp_per_mw_yr"),
        ("local_flex_piclo", "aggregator_cut_pct"),
    ]:
        assert v5.get(section, name).value == v6.get(section, name).value
        assert v5.get(section, name).source == v6.get(section, name).source


# --- Behavioural: the nonzero ancillary value stays gated / indicative -------


def _site() -> s.SiteModel:
    return s.SiteModel(
        site_id="site_v16",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"red": 0.42, "amber": 0.21, "green": 0.14},
            duos_red_rate=0.18,
        ),
        connection=s.ConnectionDetails(mic_kw=300.0, dno_region="WPD_East_Midlands"),
        consents=s.ConsentState(
            g99=ConsentStatus.APPROVED, g100=ConsentStatus.APPROVED, export_mpan="X"
        ),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


def test_gb_v1_6_ancillary_nonzero_still_gated_and_never_in_the_floor() -> None:
    # The library value is now nonzero (28296.9), but WITHOUT consent + an agreed rate the stream
    # stays CONDITIONAL gated upside — never FIRM, never priced into the floor.
    lib = load_assumptions("gb_v1_6")
    gate = default_registry().get(MarketStream.ANCILLARY_DC_DM_DR).check_gates(_site(), lib)
    assert gate.gate_status == GateStatus.CONDITIONAL
    assert gate.firmness != Firmness.FIRM
    assert gate.can_be_priced_now is False


def test_gb_v1_6_ancillary_can_never_be_marked_firm() -> None:
    # Whatever the fetched £, the schema forbids a gated stream from being FIRM.
    assert MarketStream.ANCILLARY_DC_DM_DR in GATED_UPSIDE_STREAMS
    with pytest.raises(ValueError):
        ValueStreamResult(
            stream=MarketStream.ANCILLARY_DC_DM_DR,
            annual_revenue_gbp=28296.9,
            firmness=Firmness.FIRM,
        )
