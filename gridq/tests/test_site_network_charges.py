"""Every site prices its network charges on its OWN licence area, or declares why it prices none.

Two separate corrections, and conflating them is the mistake this file exists to prevent.

**1. Four sites carried a cross-region proxy.** `bishops_cleeve`, `ewelme`, `knowl_hill` and
`harwell` all used the NGED East Midlands red rate of 7.777 p/kWh, because the library held
real values for one licence area only. `gb_v1_8` sources all fourteen, and each of those sites'
area is evidenced from its MPAN distributor id. Re-pointing them **makes the numbers worse**
(both real rates are lower than the retired proxy, and the TCR band their capacity implies
carries a much larger fixed line than the band the old stack assumed). It is done anyway,
because these are the published charges. A test that only checked "a rate is present" would
have passed on the proxy, so the check here is that each site's rate **equals its own area's
entry in the release**.

The metered rebuild then re-derived the **band** per supply, which is a second, separate move.
`bishops_cleeve` is gone: the meter proved Ash Plant and MRF run concurrently, so they are two
supplies with two agreed capacities (93 kVA and 250 kVA) and, on the LV 80/150/231 thresholds, two
different bands. `ewelme` and `knowl_hill` each gained an `_invoice_anchored` twin — the same
`site_id`, a different load basis, and in Ewelme's case a different connection (the 750 kVA Workshop
rather than the 200 kVA MRF) and therefore a different band again. Bands moved in **both**
directions and the direction is checked here arithmetically, against the site's own sibling record,
rather than taken from the prose: a record could otherwise say "BETTER" while carrying a worse
number and no test would notice.

**2. Two sites correctly price NO distribution charge, and that is not a null to fill.** Colnbrook
and Tanhouse are 100% non-grid, behind an EfW private wire (`01_data_foundation.md` section 3.2).
Their invoiced non-commodity cost is already carried as a private-wire residual levy derived from
the invoice, so attaching this area's published DUoS lines would **double-count** the cost and
invent a red-band avoidance stream against a charge the site does not pay. What was wrong was the
disclosure, not the number: an empty field reads identically to a data gap. Each now carries a
declaratory record so the evidenced zero is stated as evidenced, which is what
`validation_requirements.md` means by refusing the £ rather than silently reading it as a
zero.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from gridq.ingestion import load_site_model_json
from gridq.schemas import ConfidenceBand

REPO = Path(__file__).resolve().parents[1]
SITES = REPO / "examples" / "real" / "sites"
RELEASE = "gb_v1_8"

DUOS_KEY = "tariff_stack.duos_red_rate"
RETIRED_PROXY_RED = 0.07777
RETIRED_BAND_2_STANDING = 12.36264

#: Each grid supply, the licence area its MPAN evidences, and the TCR band its capacity implies.
#: `harwell` has no agreed capacity at all, so its band is a conservative choice, not an inference.
#: Every other band here is derived from that supply's OWN evidenced kVA — which is the whole point
#: of splitting Bishops Cleeve and of Ewelme's two bases carrying two different bands.
GRID_SITES: dict[str, dict[str, object]] = {
    "bishops_cleeve_ash_plant": {"area": "NGED_WM", "band": "band_2", "band_evidenced": True},
    "bishops_cleeve_mrf": {"area": "NGED_WM", "band": "band_4", "band_evidenced": True},
    "ewelme": {"area": "SSEN_SEPD", "band": "band_3", "band_evidenced": True},
    "ewelme_invoice_anchored": {"area": "SSEN_SEPD", "band": "band_4", "band_evidenced": True},
    "knowl_hill": {"area": "SSEN_SEPD", "band": "band_4", "band_evidenced": True},
    "knowl_hill_invoice_anchored": {"area": "SSEN_SEPD", "band": "band_4", "band_evidenced": True},
    "harwell": {"area": "SSEN_SEPD", "band": "band_4", "band_evidenced": False},
}

PRIVATE_WIRE_SITES = ("colnbrook", "tanhouse")


def _duos_record(name: str):
    """The one `tariff_stack.duos_red_rate` record on a site file, or a hard failure."""
    site = load_site_model_json(SITES / f"{name}.json")
    records = [r for r in site.assumptions_requiring_confirmation if r.key == DUOS_KEY]
    assert len(records) == 1, f"{name}: expected exactly one {DUOS_KEY} record, got {len(records)}"
    return site, records[0]


@pytest.fixture(scope="module")
def duos_rates() -> dict:
    """The release's per-area unit rates. Site files are checked AGAINST this, never guessed."""
    library = yaml.safe_load((REPO / "assumptions" / f"{RELEASE}.yaml").read_text(encoding="utf-8"))
    return library["duos"]["unit_rates_p_per_kwh_by_dno"]["value"]


@pytest.fixture(scope="module")
def tnuos_bands() -> dict:
    library = yaml.safe_load((REPO / "assumptions" / f"{RELEASE}.yaml").read_text(encoding="utf-8"))
    return library["tnuos"]["demand_residual_bands_gbp_per_day"]["value"]["bands"]


@pytest.fixture(scope="module")
def tcr_thresholds() -> dict:
    library = yaml.safe_load((REPO / "assumptions" / f"{RELEASE}.yaml").read_text(encoding="utf-8"))
    return library["duos"]["tcr_band_thresholds_kva"]["value"]


def _lv_rates(area_entry: dict, band: str) -> dict:
    """Read an area's LV site-specific rates, respecting its declared `rate_structure`.

    The release's own warning is that the DNOs publish two different shapes and a consumer MUST
    check `rate_structure` before reading a rate: `banded_units` areas (SEPD, LPN) vary the UNIT
    RATES by TCR band, while `band_invariant_units_with_banded_fixed` areas vary only the fixed
    line.
    Assuming one shape across all fourteen would silently read the wrong number.
    """
    lv = area_entry["lv_site_specific"]
    if "by_tcr_band" in lv:
        return lv["by_tcr_band"][band]
    return lv


# --- 1. the four re-pointed sites ---------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_site_reads_its_own_licence_area_not_a_proxy(name: str, duos_rates) -> None:
    """The drift check: a site's rates must equal its OWN area's published rates, to the digit."""
    spec = GRID_SITES[name]
    site = load_site_model_json(SITES / f"{name}.json")
    assert site.connection.dno_region == spec["area"]
    published = _lv_rates(duos_rates[str(spec["area"])], str(spec["band"]))
    tariff = site.tariff_stack
    assert tariff.duos_red_rate == pytest.approx(published["red"] / 100.0)
    assert tariff.duos_amber_rate == pytest.approx(published["amber"] / 100.0)
    assert tariff.duos_green_rate == pytest.approx(published["green"] / 100.0)
    capacity = duos_rates[str(spec["area"])]["capacity_charge_p_per_kva_day"]["lv_site_specific"]
    assert tariff.capacity_charge_gbp_per_kva_day == pytest.approx(capacity / 100.0)


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_retired_east_midlands_proxy_is_gone(name: str) -> None:
    """A regression guard with teeth: 7.777 p/kWh must not reappear on a non-East-Midlands site."""
    site = load_site_model_json(SITES / f"{name}.json")
    assert site.tariff_stack.duos_red_rate != RETIRED_PROXY_RED
    assert site.tariff_stack.standing_charge_per_day_gbp != RETIRED_BAND_2_STANDING
    assert "NGED East Midlands LV Site Specific HH rates" not in (site.tariff_stack.source or "")


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_standing_charge_is_the_tnuos_band_plus_this_area_s_own_fixed_residual(
    name: str, duos_rates, tnuos_bands
) -> None:
    """Two published lines, summed. SEPD's fixed residual is a published 0.00, not a gap."""
    spec = GRID_SITES[name]
    site = load_site_model_json(SITES / f"{name}.json")
    area = duos_rates[str(spec["area"])]
    band = str(spec["band"])
    tnuos = tnuos_bands["LV" + band.removeprefix("band_")]
    lv = area["lv_site_specific"]
    if "by_tcr_band" in lv:
        duos_fixed_p = lv["by_tcr_band"][band]["fixed_p_per_mpan_day"]
    else:
        duos_fixed_p = area["fixed_p_per_mpan_day"]["lv_site_specific"][band]
    assert site.tariff_stack.standing_charge_per_day_gbp == pytest.approx(
        tnuos + duos_fixed_p / 100.0
    )


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_band_follows_the_published_thresholds_where_a_capacity_exists(
    name: str, tcr_thresholds
) -> None:
    """The boundary is upper-INCLUSIVE: exactly 231 kVA is band 3. Harwell has no capacity."""
    spec = GRID_SITES[name]
    site = load_site_model_json(SITES / f"{name}.json")
    lower, upper = tcr_thresholds["lv_site_specific"][str(spec["band"])]
    capacity = site.connection.asc_kw
    if not spec["band_evidenced"]:
        # Harwell: `mic_kw` is an observed peak, a LOWER bound, and `asc_kw` is deliberately null
        # rather than invented. The band is therefore not derivable and must not be presented as if
        # it were.
        assert capacity is None
        rec = next(r for r in site.assumptions_requiring_confirmation if r.key == DUOS_KEY)
        assert "TCR BAND IS NOT ESTABLISHABLE" in rec.basis
        assert "conservative CHOICE rather than an inference" in rec.basis
        assert "LOWER BOUND on the agreed capacity" in rec.basis
        # The alternative must be quantified, not merely acknowledged: a reader told the band is
        # unresolved needs to know what the other branch would have cost.
        assert "THE ALTERNATIVE, IN FULL" in rec.basis
        assert "5.912" in rec.basis and "14.381374" in rec.basis
        return
    assert capacity is not None
    assert lower < capacity, f"{name}: {capacity} kVA is not above the {spec['band']} lower bound"
    assert upper is None or capacity <= upper


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_duos_record_is_a_sourced_transcription_of_this_area_s_rate(name: str) -> None:
    """The register entry and the priced field must be the same number, labelled as transcribed."""
    site, rec = _duos_record(name)
    assert rec.kind == "derived_input"
    assert rec.value == site.tariff_stack.duos_red_rate
    assert rec.unit == "gbp_per_kwh"
    assert rec.derivation_method == "sourced_default"
    assert rec.derived_on_release == RELEASE
    assert rec.confidence_ceiling is ConfidenceBand.MEDIUM
    # The band is read from a published table, so the record must say the BILLED band may differ.
    if GRID_SITES[name]["band_evidenced"]:
        assert "BAND IS INFERRED, NOT BILLED" in rec.basis
        assert "DCUSA Schedule 32" in rec.basis


#: What moved at each supply, the phrase the record uses for it, and figures the phrase must carry.
#: Every entry is checked against ARITHMETIC as well (see the test), because a direction word is
#: only evidence if a reader can recompute it: the retired world's single "WORSE" verdict was true
#: for the East Midlands re-pointing and is simply not true of the per-supply bands, which moved
#: both ways. Asserting the word alone passes on a record with the word and the wrong number.
DIRECTION: dict[str, dict[str, object]] = {
    # Split onto its own 93 kVA: band 4 -> band 2, so the unavoidable fixed line collapses.
    "bishops_cleeve_ash_plant": {
        "phrase": "DIRECTION OF EFFECT, STATED PLAINLY: BETTER",
        "figures": ("13.70954", "44.012203", "69%"),
    },
    # The same split left THIS supply's band alone, because the composite was already banded on it.
    "bishops_cleeve_mrf": {
        "phrase": "THE SPLIT DID NOT MOVE THIS BAND",
        "figures": ("292,462", "387,008"),
    },
    # A banded-units area, so the band change moved the unit rate UP and the fixed line DOWN.
    "ewelme": {
        "phrase": "DIRECTION OF EFFECT, STATED PLAINLY: MIXED",
        "figures": ("5.912", "5.471", "8.1%", "62%"),
    },
    "ewelme_invoice_anchored": {
        "phrase": "THE TWO EWELME BASES USE DIFFERENT BANDS AND THAT IS CORRECT",
        "figures": ("5.471", "5.912", "750", "200"),
    },
    # Same MPAN, same rates, 2.37x the measured energy: the exposure moved, the tariff did not.
    "knowl_hill": {
        "phrase": "the rates did not move, the exposure did",
        "figures": ("2.37x", "108,242", "45,732"),
    },
    "knowl_hill_invoice_anchored": {
        "phrase": "not the rates, the exposure",
        "figures": ("108,242", "45,732", "42%"),
    },
    # The one site still carrying the original East Midlands re-pointing, unchanged by the rebuild.
    "harwell": {
        "phrase": "DIRECTION OF EFFECT, STATED PLAINLY: WORSE",
        "figures": ("7.777",),
    },
}


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_change_at_this_supply_is_recorded_with_its_direction(name: str) -> None:
    """Every grid supply states what its rebuild changed, in words a reader can check."""
    _, rec = _duos_record(name)
    spec = DIRECTION[name]
    assert str(spec["phrase"]) in rec.basis, f"{name}: the record no longer states what changed"
    for figure in spec["figures"]:
        assert str(figure) in rec.basis, f"{name}: {figure!r} missing from the record's arithmetic"


def test_the_stated_directions_are_arithmetically_true_of_the_records() -> None:
    """The teeth. Each direction word is recomputed from the site files it compares.

    A prose pin alone is hollow — it passes on any record carrying the right sentence. These
    relations hold only if the bands, rates and fixed lines really are what the records claim, so
    flipping a band to match a sibling (the exact error the per-supply derivation exists to prevent)
    breaks them even if every sentence is left intact.
    """
    ash = load_site_model_json(SITES / "bishops_cleeve_ash_plant.json").tariff_stack
    mrf = load_site_model_json(SITES / "bishops_cleeve_mrf.json").tariff_stack
    ewelme = load_site_model_json(SITES / "ewelme.json").tariff_stack
    ewelme_ia = load_site_model_json(SITES / "ewelme_invoice_anchored.json").tariff_stack
    kh = load_site_model_json(SITES / "knowl_hill.json")
    kh_ia = load_site_model_json(SITES / "knowl_hill_invoice_anchored.json")
    harwell = load_site_model_json(SITES / "harwell.json").tariff_stack

    # BETTER at Ash Plant: a band-2 fixed line against the band-4 line the composite applied, which
    # is the line its MRF sibling still carries. Band-invariant area, so the unit rate is unmoved.
    assert ash.standing_charge_per_day_gbp < mrf.standing_charge_per_day_gbp
    assert ash.duos_red_rate == mrf.duos_red_rate

    # MIXED at Ewelme, and mixed in both limbs at once: band 3 buys a HIGHER unit rate and a LOWER
    # fixed line than band 4. Neither limb alone would justify the word.
    assert ewelme.duos_red_rate > ewelme_ia.duos_red_rate
    assert ewelme.standing_charge_per_day_gbp < ewelme_ia.standing_charge_per_day_gbp

    # Knowl Hill: one MPAN, one band, so the two bases must agree on every network figure and
    # differ only in the load the figures are applied to.
    assert kh.tariff_stack.duos_red_rate == kh_ia.tariff_stack.duos_red_rate
    assert kh.tariff_stack.standing_charge_per_day_gbp == (
        kh_ia.tariff_stack.standing_charge_per_day_gbp
    )
    assert kh.load_data.annual_kwh > kh_ia.load_data.annual_kwh

    # WORSE at Harwell: the published rate really is below the retired proxy, so the re-pointing
    # cuts avoided cost. This is the assertion that would fail if the proxy were quietly restored.
    assert harwell.duos_red_rate < RETIRED_PROXY_RED


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_licence_area_is_evidenced_from_the_mpan_never_the_postcode(name: str) -> None:
    """A confidently wrong licence area is worse than an acknowledged unknown, so the basis must
    name the evidence. Bishops Cleeve is the proof case: Gloucestershire says South West, the MPAN
    says West Midlands, and the areas' red rates differ."""
    _, rec = _duos_record(name)
    assert "not a postcode guess" in rec.basis
    assert "distributor id" in rec.basis
    if GRID_SITES[name]["area"] == "NGED_WM":
        assert "'14'" in rec.basis
        assert "Gloucestershire postcode would have said South West" in rec.basis
    else:
        assert "'20'" in rec.basis


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_record_carries_real_citations_and_the_chain_of_custody(name: str) -> None:
    """These rates ARE sourced, unlike the load shape, so `sources` is populated, with attribution.

    The statements were not fetched in this pass: they are transcribed via the release, which
    carries each statement's URL and the sha256 of the exact file read. The citation says so,
    because a citation implying a fetch that did not happen is the start of a fabricated one.

    Three release-borne citations are REQUIRED — the area's charging statement, the NESO TNUoS
    table and the residual-banding rule. A site may cite more (Ewelme cites the per-meter agreed
    capacities its band rests on), so extras are allowed; what is not allowed is a rate citation
    that does not run through the release.
    """
    _, rec = _duos_record(name)
    via_release = [s for s in rec.sources if "assumptions/gb_v1_8.yaml" in s]
    assert len(via_release) == 3, f"{name}: {len(via_release)} of the 3 release citations present"
    assert any("sha256" in s for s in via_release)
    assert any("neso.energy" in s for s in via_release)
    assert "transcribed from assumptions/gb_v1_8.yaml" in rec.basis
    assert "1 April 2026" in " ".join(via_release)
    # Any additional citation must be site evidence, not a second unattributed rate source.
    for extra in set(rec.sources) - set(via_release):
        assert extra.startswith(("data/", "docs/", "examples/")), (
            f"{name}: {extra[:80]!r} is neither a release transcription nor a repo evidence file"
        )


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_unevidenced_lv_assumption_and_the_band_window_gap_are_both_declared(name: str) -> None:
    """Two things the corrected rate does NOT fix, so neither may be left for a reader to discover.

    `voltage_level` is null on every site, and the HV rate is far lower. And the run's band windows
    come from `gridq.optimisation.tariff_calendar`, which applies one national 16:00-19:00 pattern:
    correct for NGED West Midlands, wrong for SEPD's 16:30-19:30 with weekend amber. The rate is
    this site's own; the window is not yet.

    The window half is asymmetric on purpose. SEPD sites must **declare the mismatch**, because one
    exists and it mis-bands real half-hours. NGED West Midlands sites must **not carry SEPD's
    mismatch text**, because they have no mismatch — and a record that claimed one would be the
    wrong area's disclosure pasted onto this site, which is the same class of error as the retired
    East Midlands proxy itself.
    """
    site, rec = _duos_record(name)
    assert "THE LV ASSUMPTION IS LOAD-BEARING AND UNEVIDENCED" in rec.basis
    assert site.connection.voltage_level is None
    if GRID_SITES[name]["area"] == "NGED_WM":
        assert "BAND WINDOW, AND IT DOES NOT MATCH" not in rec.basis
        assert "16:30 to 19:30" not in rec.basis
        assert "AMBER AT WEEKENDS" not in rec.basis
    else:
        assert "BAND WINDOW, AND IT DOES NOT MATCH" in rec.basis
        assert "16:30 to 19:30" in rec.basis
        assert "AMBER AT WEEKENDS" in rec.basis


@pytest.mark.parametrize("name", sorted(GRID_SITES))
def test_the_confirmation_ask_is_the_customer_s_own_invoice(name: str) -> None:
    """A published tariff table cannot settle a billed band or a voltage level. An invoice can."""
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == DUOS_KEY)
    ask = rec.confirmation_ask
    assert "invoice" in ask
    assert "voltage" in ask
    assert "TCR residual band" in ask


# --- 2. the two private-wire sites: an evidenced zero, declared as one --------------------------


@pytest.mark.parametrize("name", PRIVATE_WIRE_SITES)
def test_a_private_wire_site_prices_no_distribution_charge_at_all(name: str) -> None:
    """No rate is invented here, and the test would fail if one were.

    Filling these from the published SEPD table would fabricate a network stack the site does not
    pay and invent a revenue stream that cannot exist. That is the one class of error that cannot be
    walked back, so it is pinned as an invariant rather than left to judgement.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    tariff = site.tariff_stack
    assert tariff.duos_red_rate is None
    assert tariff.duos_amber_rate is None
    assert tariff.duos_green_rate is None
    assert tariff.standing_charge_per_day_gbp is None
    assert tariff.capacity_charge_gbp_per_kva_day is None


@pytest.mark.parametrize("name", PRIVATE_WIRE_SITES)
def test_the_evidenced_zero_is_declared_rather_than_left_looking_like_a_gap(name: str) -> None:
    """The real defect was indistinguishability: an empty field reads as an unfilled input.

    **CLASS CORRECTED 2026-08-17 (refutation R1).** This assertion previously required
    ``kind == "derived_input"``, which is what both files carried - and that was exactly the defect:
    `value_stack.gates.evidenced_absences` matches **only** ``not_applicable``, so a valueless
    `derived_input` reached no consumer, the structural branch at `gb_streams.py:946` never fired on
    any real run, and these two private-wire customers were still asked for a CDCM distribution rate
    no supplier could issue them. The record now carries the class that says what the files' own
    prose has always said. Its VALUE is unchanged (still none) and so is the GBP (still 0.00) -
    `tests/test_private_wire_duos_absence_on_real_sites.py` pins both, and pins that the branch
    fires.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    records = [r for r in site.assumptions_requiring_confirmation if r.key == DUOS_KEY]
    assert len(records) == 1
    rec = records[0]
    assert rec.kind == "not_applicable", (
        "only `not_applicable` is read by gates.evidenced_absences; `derived_input` leaves the "
        "structural branch unreachable and puts the CDCM data ask back in front of the customer"
    )
    assert rec.value is None, "a declaratory record must not smuggle in a rate"
    assert rec.unit is None
    assert rec.derivation_method == "not_derivable"
    assert rec.confidence_ceiling is ConfidenceBand.MEDIUM
    assert "THE ZERO IS EVIDENCED RATHER THAN MISSING" in rec.basis
    assert "no value applies" in rec.statement()
    assert rec.report_label == "does not apply to this site, evidenced"


@pytest.mark.parametrize("name", PRIVATE_WIRE_SITES)
def test_the_refusal_carries_the_whole_evidence_chain(name: str) -> None:
    """Three independent lines, because a refusal is only honest if it can be checked."""
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == DUOS_KEY)
    basis = rec.basis
    # (1) the supply arrangement itself
    assert "100% non-grid" in basis
    assert "01_data_foundation.md section 3.2" in basis
    # (2) the arithmetic: the non-commodity cost is already in the stack, so DUoS double-counts
    assert "private_wire_non_commodity_residual" in basis
    assert "double-count" in basis
    residual_key = "private_wire_non_commodity_residual"
    levies = site.tariff_stack.levies
    assert residual_key in levies, "the record cites a levy the site does not hold"
    assert str(levies[residual_key]) in basis
    # (3) the price check
    assert "26 to 32 p/kWh" in basis
    # ...and what would actually change the answer
    assert "only a pass-through" in basis
    assert "private-wire" in rec.confirmation_ask


@pytest.mark.parametrize("name", PRIVATE_WIRE_SITES)
def test_the_private_wire_sites_keep_their_own_commodity_prices(name: str) -> None:
    """The refusal is scoped to the network stack. The site's own invoiced prices are untouched."""
    site = load_site_model_json(SITES / f"{name}.json")
    assert site.tariff_stack.import_rates
    assert set(site.tariff_stack.import_rates) == {"day", "night"}
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == DUOS_KEY)
    assert "SCOPE OF THE REFUSAL" in rec.basis
    assert "commodity rates and the private-wire non-commodity residual are unaffected" in rec.basis


def test_no_site_carries_more_than_one_record_per_key() -> None:
    """A duplicated key in the register would show the reader two answers for one question."""
    for path in sorted(SITES.glob("*.json")):
        site = load_site_model_json(path)
        keys = [r.key for r in site.assumptions_requiring_confirmation]
        assert len(keys) == len(set(keys)), f"{path.name}: duplicate register keys {keys}"


# --- 3. the private-wire residual: right arithmetic, wrong base year --------------------------

LEVIES_KEY = "tariff_stack.levies"

#: The corrected residual per site, and what it replaces. Both corrections REDUCE delivered cost.
RESIDUALS = {
    "colnbrook": {"new": 0.0413135, "old": 0.0505510, "blended": "13.04630"},
    "tanhouse": {"new": 0.1313958, "old": 0.1370020, "blended": "22.50951"},
}


@pytest.mark.parametrize("name", sorted(RESIDUALS))
def test_the_private_wire_residual_is_on_the_2025_basis(name: str) -> None:
    """It was back-solved from a 2026 year-to-date blended cost applied to a 2025 volume."""
    spec = RESIDUALS[name]
    site = load_site_model_json(SITES / f"{name}.json")
    levies = site.tariff_stack.levies
    assert levies["private_wire_non_commodity_residual"] == spec["new"]
    assert levies["private_wire_non_commodity_residual"] != spec["old"]
    # Full precision on purpose: the retired value was back-solved from a two-decimal blended
    # figure, which is where its own imprecision came from.
    assert len(str(spec["new"]).split(".")[1]) >= 7


@pytest.mark.parametrize("name", sorted(RESIDUALS))
def test_the_residual_correction_is_recorded_with_its_direction(name: str) -> None:
    """A lower residual is a lower delivered cost, so the battery earns less. Say so."""
    spec = RESIDUALS[name]
    site = load_site_model_json(SITES / f"{name}.json")
    records = [r for r in site.assumptions_requiring_confirmation if r.key == LEVIES_KEY]
    assert len(records) == 1
    rec = records[0]
    assert rec.value == spec["new"]
    assert rec.unit == "gbp_per_kwh"
    assert rec.confidence_ceiling is ConfidenceBand.MEDIUM
    assert "2026 year-to-date" in rec.basis
    assert "base-year mismatch" in rec.basis
    assert spec["blended"] in rec.basis
    # It is still a back-solved residual, not a published rate, and the record must not imply one.
    assert "STILL A DERIVED RESIDUAL, NOT A SOURCED RATE" in rec.basis
    assert rec.derivation_method != "sourced_default"
    assert rec.sources and "06_bill_reconciliation.md" in rec.sources[0]


def test_colnbrook_still_states_the_direction_of_its_base_year_correction() -> None:
    """Colnbrook's record is the one the metered rebuild did not touch, so it keeps its full text.

    Tanhouse's was rewritten around its measured day/night split and reframed; Colnbrook's still
    carries the original correction verbatim, including the retired value. Keeping this pinned on
    the untouched site means the retired-value discipline is still enforced somewhere, rather than
    disappearing with the record that was rewritten.
    """
    site = load_site_model_json(SITES / "colnbrook.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == LEVIES_KEY)
    assert "DIRECTION OF EFFECT, STATED PLAINLY: WORSE" in rec.basis
    assert f"{RESIDUALS['colnbrook']['old'] * 100:.5f}" in rec.basis, "the retired value, named"
    # Colnbrook has no solar, so ONLY its base year was wrong; the denominator never was. The
    # record must say which of the two corrections applies here, or a reader will assume both.
    assert "THE DENOMINATOR IS NOT AT ISSUE HERE" in rec.basis
    assert "Solar = 0" in rec.basis


def test_tanhouse_declines_the_flattering_re_derivation_and_says_why() -> None:
    """The measured split would RAISE the residual, so not taking it needs a stated reason.

    The metered series gives Tanhouse a measured 19.54% night share against the 14.36% the residual
    was back-solved on. Re-deriving on it gives 13.32230 p/kWh — higher, therefore more avoided cost
    for the battery to claim. A flattering correction resting on a period mismatch is exactly the
    kind that must not be taken quietly, so the record must show the number it did NOT use, name the
    direction as flattering, and give the base-year reason. Same invariant as the correction this
    section already pins; the only thing that changed is which way the unused number points.
    """
    site = load_site_model_json(SITES / "tanhouse.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == LEVIES_KEY)
    assert "19.54%" in rec.basis, "the measured night share the record declines to re-derive on"
    assert "14.36%" in rec.basis, "and the modelled one it was back-solved on"
    assert "13.32230" in rec.basis, "the value NOT taken must be on the record, not just implied"
    assert "FLATTERING" in rec.basis
    assert "BASE-YEAR CONSISTENCY" in rec.basis
    # The arithmetic, so "flattering" is checkable: the rejected value is above the one in force.
    assert 0.1332230 > site.tariff_stack.levies["private_wire_non_commodity_residual"]
    # And the ask that would settle it is raised rather than left as a modelling preference.
    assert any("SAME twelve months" in a for a in site.missing_data)


def test_the_correction_widened_the_unexplained_gap_and_the_record_admits_it() -> None:
    """Correcting the denominator EXCLUDED the leading explanation instead of confirming it.

    Tanhouse's delivered cost was 65% above Colnbrook's on the same private wire, and one candidate
    explanation was solar sitting in Tanhouse's denominator. On the purchased denominator the gap
    widens to 72%, so that explanation is dead and the puzzle is larger, not smaller. A record that
    quietly dropped the caveat would be reporting a resolution that did not happen.
    """
    site = load_site_model_json(SITES / "tanhouse.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == LEVIES_KEY)
    assert "from 65% to 72%" in rec.basis, "the widening, not just the new number, is the finding"
    assert "excludes the leading explanation" in rec.basis
    # Two corrections compounded at this site, and both must be named: Colnbrook's record carries
    # only the base year, so naming only one here would make the two sites read as one correction.
    assert "the base year and the denominator" in rec.basis
    source = site.tariff_stack.source or ""
    assert "STILL UNEXPLAINED AND MATERIAL" in source
    assert "EXCLUDED" in source
    assert "the gap widened when it was corrected" in source
    # The comparison is between two real records, so it is recomputed rather than read off the text.
    colnbrook = load_site_model_json(SITES / "colnbrook.json")
    tanhouse_residual = site.tariff_stack.levies["private_wire_non_commodity_residual"]
    colnbrook_residual = colnbrook.tariff_stack.levies["private_wire_non_commodity_residual"]
    assert tanhouse_residual > 3 * colnbrook_residual, "the 3.2x the record reports"


def test_the_private_wire_source_text_shows_the_retired_cost_only_as_retired() -> None:
    """The old blended cost must still appear, but only as the thing being replaced.

    Deleting it would hide the correction; leaving it stated as the current cost would contradict
    the new levy. So the current figure must be the one presented as current, and every mention of
    the retired figure must sit inside the sentence that retires it.

    Scoped to Colnbrook. Tanhouse's source text was rewritten for the metered basis and no longer
    quotes its retired 23.07 p/kWh blended cost at all — which cannot fail this test's way round,
    since a figure that is absent is not a figure presented as current. The retired Tanhouse value
    remains recoverable from the cited `06_bill_reconciliation.md`.
    """
    name, retired = "colnbrook", "13.97"
    site = load_site_model_json(SITES / f"{name}.json")
    source = site.tariff_stack.source or ""
    assert RESIDUALS[name]["blended"] in source
    assert "RE-CALIBRATED TO 2025" in source
    assert retired in source, "the retired cost must still be shown, as the thing replaced"
    for sentence in source.split(". "):
        if retired in sentence:
            assert "THIS REPLACES" in sentence or "cheaper still than" in sentence, (
                f"{name}: {retired} p/kWh appears outside a retirement clause: {sentence[:160]}"
            )


def test_the_tanhouse_source_does_not_present_a_retired_blended_cost_as_current() -> None:
    """The weaker half of the rule still binds on the rewritten record: no stale figure as live.

    Tanhouse's source was rebuilt on the metered basis and states 22.50951 p/kWh as its current
    blended cost. The retired 23.07 must not reappear beside it wearing no retirement clause.
    """
    site = load_site_model_json(SITES / "tanhouse.json")
    source = site.tariff_stack.source or ""
    assert RESIDUALS["tanhouse"]["blended"] in source, "the current blended cost, stated"
    for sentence in source.split(". "):
        if "23.07" in sentence:
            assert "THIS REPLACES" in sentence or "cheaper still than" in sentence, (
                f"23.07 p/kWh appears outside a retirement clause: {sentence[:160]}"
            )


# --- 4. nothing elsewhere in the file contradicts the corrected stack --------------------------

ALL_SITES = (
    "bishops_cleeve_ash_plant",
    "bishops_cleeve_mrf",
    "colnbrook",
    "ewelme",
    "ewelme_invoice_anchored",
    "knowl_hill",
    "knowl_hill_invoice_anchored",
    "tanhouse",
    "harwell",
)

#: The HV counterfactual per supply: every HV red rate its ask is entitled to quote. The HV residual
#: bands are 422/1000/1800 kVA, NOT the LV 80/150/231, so an HV supply changes BAND as well as
#: tariff family. 0.377 p/kWh is SEPD HV band 2 and applies only to Ewelme's 750 kVA Workshop;
#: quoting it for the 200 kVA MRF, Knowl Hill (325 kVA) or Harwell (a 213.81 kW lower bound) would
#: be the wrong band's rate, which is the error this pins. `ewelme_invoice_anchored` is the one
#: entry with two, because it prices a site whose two MPANs fall in two different HV bands and its
#: ask has to give both and say which is which.
HV_RATES: dict[str, set[str]] = {
    "bishops_cleeve_ash_plant": {"1.764"},
    "bishops_cleeve_mrf": {"1.764"},
    "ewelme": {"1.266"},
    "ewelme_invoice_anchored": {"0.377", "1.266"},
    "knowl_hill": {"1.266"},
    "knowl_hill_invoice_anchored": {"1.266"},
    "harwell": {"1.266"},
}

#: Every HV red rate any supply in this portfolio could legitimately carry. Membership of this set
#: is what makes "another supply's rate" checkable rather than a guess.
ALL_HV_RATES = {rate for rates in HV_RATES.values() for rate in rates}


@pytest.mark.parametrize("name", sorted(HV_RATES))
def test_the_hv_counterfactual_uses_the_hv_bands_not_the_lv_ones(name: str) -> None:
    """The ask must offer this supply's own HV rate, and must offer no other supply's."""
    _, rec = _duos_record(name)
    ask = rec.confirmation_ask
    entitled = HV_RATES[name]
    assert "HV" in ask
    for rate in entitled:
        assert f"{rate} p/kWh" in ask, f"{name}: its own HV counterfactual is not quoted"
    for other in ALL_HV_RATES - entitled:
        assert f"{other} p/kWh" not in ask, f"{name}: quotes another supply's HV rate"


@pytest.mark.parametrize("name", sorted(k for k in HV_RATES if GRID_SITES[k]["area"] != "NGED_WM"))
def test_a_banded_units_area_names_the_hv_band_and_it_is_not_the_lv_one(name: str) -> None:
    """In SEPD the band IS the rate, so an HV counterfactual is meaningless without its HV band.

    Only the banded-unit areas need this: NGED West Midlands publishes one unit rate per tariff
    family, so its HV red rate does not depend on the band at all and naming one would be noise.
    What is checked is that the HV band named is not simply the LV band repeated — carrying the LV
    band across onto the HV thresholds (422/1000/1800 against 80/150/231) is the specific arithmetic
    error here, and it is the one an eye skimming for "a band is stated" would wave through.
    """
    _, rec = _duos_record(name)
    stated = rec.basis + " " + rec.confirmation_ask
    hv_bands = set(re.findall(r"HV band (\d)", stated))
    assert hv_bands, f"{name}: an HV rate is quoted with no HV band, in an area where band = rate"
    lv_band = str(GRID_SITES[name]["band"]).removeprefix("band_")
    assert lv_band not in hv_bands, (
        f"{name}: HV band {lv_band} repeats the LV band — the HV thresholds are 422/1000/1800 kVA, "
        "not the LV 80/150/231, so the two coinciding is a red flag, not a coincidence"
    )


@pytest.mark.parametrize("name", ALL_SITES)
def test_no_data_ask_contradicts_the_corrected_network_stack(name: str) -> None:
    """A register that discloses a correction beside an ask that denies it shows two answers.

    Same defect class as the empty register: the reader cannot tell which statement is live.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    asks = " ".join(site.missing_data)
    assert "register holds East Midlands only" not in asks
    assert "7.723 vs 2.828" not in asks, "the retired East Midlands red spread"
    assert "5.055 p/kWh" not in asks
    assert "13.700 p/kWh" not in asks
    if name == "tanhouse":
        assert "weakest load basis of the five sites" not in asks
        assert "Only an annual total exists" not in asks
        assert "doubly-modelled" not in asks


@pytest.mark.parametrize("name", ALL_SITES)
def test_the_site_cites_where_its_network_stack_actually_comes_from(name: str) -> None:
    """The stack no longer comes from the constants register alone, so the citation must say so."""
    site = load_site_model_json(SITES / f"{name}.json")
    refs = site.source_references
    expected = (
        "docs/grundon_harwell/06_bill_reconciliation.md"
        if name in PRIVATE_WIRE_SITES
        else "assumptions/gb_v1_8.yaml"
    )
    assert any(expected in r for r in refs), f"{name}: no reference to {expected}"
    # A botched insertion once collapsed two references into one escaped string; keep them clean.
    assert all('"' not in r and "\\" not in r for r in refs)
