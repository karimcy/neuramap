"""A modelled load shape must never be able to report itself as "nothing was assumed" (D45 §8).

The defect this pins was a **disclosure** defect, not an arithmetic one, and that is what made it
dangerous. Five Grundon sites declared ``half_hourly_basis: "modelled"`` — their entire
within-day load shape taken from an engineering archetype — and carried an EMPTY
`derived_assumptions` register. Two consequences followed:

* the D45-mandated assumption register rendered **empty** for them, and
* `gridq.quotes.confidence._derived_factor` reported *"no input had to be assumed, every value the
  run consumed was supplied"*.

No number was wrong: the Gate-2 half-hourly factor still capped the band at MEDIUM. The *claim* was.
For a client report that is worse than a wrong figure, because a wrong figure invites checking and a
false claim of provenance tells the reader there is nothing to check.

Two halves are pinned here. First that the sentence is now **structurally unsayable** for a modelled
site — gated on the site's own basis rather than on the record list, so a future site with an empty
register gets a loud REGISTER INCOMPLETE factor instead of a clean bill. Second that the real site
files actually carry the records, with the content D45 §8.1 requires.

**The metered rebuild moved which files are in the second half, and it is worth being exact about
what survived.** Five Grundon supplies now have their own meter and are `metered`; nothing about
their shape is assumed, so they carry no shape record — and the check on them inverts into "must
carry none", which is a stronger guard than the old single Harwell control.

Three files remain `modelled`, and they are not modelled the same way:

* **`colnbrook`** is the last LITERATURE ARCHETYPE. No meter export exists for it, so its within-day
  shape is still `waste_mrf` with `sources: []`. Every archetype-specific pin below — the 45.2/45.3
  corroboration and its limit, "cites nothing", "a sourced substitute would be worse", the
  circularity statement — lives here and only here now.
* **`ewelme_invoice_anchored`** and **`knowl_hill_invoice_anchored`** are Gate 2's *second* limb: a
  reference profile scaled to the site's own reconciled totals, where the reference profile is the
  site's own meter. Their `derivation_method` is `site_analogue`, not `archetype_pending_source`,
  and their `sources` are correctly populated with the site's own data. Reusing the archetype pins
  on them would demand the record *lie about its own provenance*, so they are scoped out — but the
  invariants that generalise (a modelled basis is declared, capped, and separates measured from
  modelled) are checked on all three, and Knowl Hill's anchored file is the case that proves the
  point: its shape is measured outright, so the only things modelled are the LEVEL and the PEAK, and
  it carries exactly those two records and no shape record at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridq.ingestion import load_site_model_json
from gridq.ingestion.hh_mandate import HalfHourlyAdmission, HalfHourlyBasis
from gridq.quotes.confidence import ConfidenceBand, assess_confidence
from gridq.schemas import DataTier, DerivedAssumption, Firmness

REPO = Path(__file__).resolve().parents[1]
SITES = REPO / "examples" / "real" / "sites"
MANIFEST = REPO / "data" / "sites" / "hh" / "manifest.json"

SHAPE_KEY = "load_data.half_hourly_reference_profile"
SCALED_TO_KEY = "load_data.half_hourly_scaled_to"
PEAK_KEY = "load_data.max_import_kw"

#: The sites whose within-day shape comes from a LITERATURE ARCHETYPE, and the archetype each uses.
#: One survives the metered rebuild: Colnbrook has no half-hourly export, so nothing measured can
#: replace its shape. The archetype-specific disclosures below apply here and nowhere else.
ARCHETYPE_SITES = {"colnbrook": "waste_mrf"}

#: Every site on a MODELLED half-hourly basis, and the EXACT set of `load_data.*` register keys it
#: must carry. The set is the point: it fails both when a record is dropped and when one appears
#: that the file's own basis does not justify. Knowl Hill's anchored file has no shape record
#: because its shape is its own meter — only the level and the peak are modelled there.
MODELLED_SITES: dict[str, set[str]] = {
    "colnbrook": {SHAPE_KEY},
    "ewelme_invoice_anchored": {SHAPE_KEY, SCALED_TO_KEY, PEAK_KEY},
    "knowl_hill_invoice_anchored": {SCALED_TO_KEY, PEAK_KEY},
}

#: Every site on a METERED basis. Their shape is measured, so a shape assumption on one of these
#: would be an archetype smuggled in beside real data — the inverse of the defect this file pins.
METERED_SITES = (
    "bishops_cleeve_ash_plant",
    "bishops_cleeve_mrf",
    "ewelme",
    "harwell",
    "knowl_hill",
    "tanhouse",
)

MODELLED_ADMISSION = HalfHourlyAdmission(
    basis=HalfHourlyBasis.MODELLED,
    file_ref="generated.csv",
    scaled_to="12 monthly invoice totals, 2025",
    reference_profile="waste_mrf (archetype_pending_source)",
)
METERED_ADMISSION = HalfHourlyAdmission(basis=HalfHourlyBasis.METERED, file_ref="real.csv")


class _Stream:
    def __init__(self, firmness: Firmness, revenue: float) -> None:
        self.firmness = firmness
        self.annual_revenue_gbp = revenue


class _Quote:
    def __init__(self) -> None:
        self.uses_unconfirmed_assumptions = False
        self.value_streams = [_Stream(Firmness.CONTRACTED, 50_000.0)]


def _strong_site(make_site, *, basis: str | None):
    """Everything else evidenced, so the derived-assumption factor is the only thing under test."""
    site = make_site().model_copy(deep=True)
    site.data_tier = DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    site.tariff_stack = site.tariff_stack.model_copy(
        update={
            "import_rates": {"red": 0.42, "amber": 0.21, "green": 0.14},
            "duos_red_rate": 0.21,
            "duos_amber_rate": 0.03,
            "duos_green_rate": 0.006,
            "levies": {"RO": 0.025},
        }
    )
    site.load_data = site.load_data.model_copy(update={"half_hourly_basis": basis})
    return site


def _shape_record() -> DerivedAssumption:
    return DerivedAssumption(
        kind="derived_input",
        key=SHAPE_KEY,
        missing_input="the site's own half-hourly load shape",
        value="waste_mrf",
        basis="Modelled from an engineering archetype, scaled to the site's own monthly totals.",
        sources=[],
        derivation_method="archetype_pending_source",
        derived_by="gridq.ingestion.hh_synthesis",
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask="Send the measured monthly maximum demand readings from the invoices.",
    )


def _factor(assessment, name: str):
    matches = [f for f in assessment.factors if f.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} factor, got {len(matches)}"
    return matches[0]


# --- 1. the sentence is structurally unsayable for a modelled site ------------------------------


def test_a_modelled_site_with_an_empty_register_never_claims_nothing_was_assumed(
    make_site,
) -> None:
    """The exact defect: modelled shape, empty register, "every value ... was supplied"."""
    a = assess_confidence(
        _strong_site(make_site, basis="modelled"),
        _Quote(),
        admission=MODELLED_ADMISSION,
        reconciles=True,
        derived_assumptions=[],
    )
    factor = _factor(a, "derived assumptions")
    assert "no input had to be assumed" not in factor.reason
    assert "REGISTER INCOMPLETE" in factor.reason
    assert factor.ceiling is ConfidenceBand.MEDIUM


def test_the_guard_reads_the_site_not_the_admission_alone(make_site) -> None:
    """A caller that omits the admission must not thereby unlock the sentence.

    The fact that matters is the site's declared basis, so the guard consults it directly. Passing a
    precomputed flag instead would have put the guarantee back in the caller's hands.
    """
    a = assess_confidence(
        _strong_site(make_site, basis="modelled"),
        _Quote(),
        reconciles=True,
        derived_assumptions=[],
    )
    assert "REGISTER INCOMPLETE" in _factor(a, "derived assumptions").reason


def test_the_run_s_own_finding_also_triggers_the_guard(make_site) -> None:
    """And a site that never declared a basis, on a run that concluded MODELLED, is caught too."""
    a = assess_confidence(
        _strong_site(make_site, basis=None),
        _Quote(),
        admission=MODELLED_ADMISSION,
        reconciles=True,
        derived_assumptions=[],
    )
    assert "REGISTER INCOMPLETE" in _factor(a, "derived assumptions").reason


def test_a_modelled_site_that_discloses_its_shape_reports_the_record_not_the_gap(
    make_site,
) -> None:
    """Back-filling the record is the fix; the guard then steps aside and the record speaks."""
    a = assess_confidence(
        _strong_site(make_site, basis="modelled"),
        _Quote(),
        admission=MODELLED_ADMISSION,
        reconciles=True,
        derived_assumptions=[_shape_record()],
    )
    factor = _factor(a, "derived assumptions")
    assert "REGISTER INCOMPLETE" not in factor.reason
    assert "no input had to be assumed" not in factor.reason
    assert SHAPE_KEY in factor.reason
    assert factor.ceiling is ConfidenceBand.MEDIUM


def test_a_metered_site_with_nothing_derived_may_still_say_nothing_was_assumed(make_site) -> None:
    """The sentence is not banned, it is gated: on metered data it is true and still said."""
    a = assess_confidence(
        _strong_site(make_site, basis="metered"),
        _Quote(),
        admission=METERED_ADMISSION,
        reconciles=True,
        derived_assumptions=[],
    )
    factor = _factor(a, "derived assumptions")
    assert "no input had to be assumed" in factor.reason
    assert "the load shape is not modelled" in factor.reason
    assert factor.ceiling is ConfidenceBand.HIGH


def test_the_guard_never_softens_a_structural_ceiling(make_site) -> None:
    """A LOW record still binds: the branch takes the weakest ceiling, not the shape's."""
    structural = DerivedAssumption(
        kind="derived_input",
        key="connection.mic_kw",
        missing_input="the agreed import capacity",
        value=500.0,
        unit="kW",
        basis="No connection agreement supplied; measured peak used as a lower bound.",
        derivation_method="fable_advisor",
        derived_by="claude-fable-5",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="Send the connection agreement.",
    )
    a = assess_confidence(
        _strong_site(make_site, basis="modelled"),
        _Quote(),
        admission=MODELLED_ADMISSION,
        reconciles=True,
        derived_assumptions=[structural],
    )
    factor = _factor(a, "derived assumptions")
    assert "REGISTER INCOMPLETE" in factor.reason
    assert factor.ceiling is ConfidenceBand.LOW


def test_the_guard_does_not_change_the_band_for_the_five_real_sites(make_site) -> None:
    """Gate 2 already capped a modelled site at MEDIUM, so closing this hole moves no band."""
    with_gap = assess_confidence(
        _strong_site(make_site, basis="modelled"),
        _Quote(),
        admission=MODELLED_ADMISSION,
        reconciles=True,
        derived_assumptions=[],
    )
    disclosed = assess_confidence(
        _strong_site(make_site, basis="modelled"),
        _Quote(),
        admission=MODELLED_ADMISSION,
        reconciles=True,
        derived_assumptions=[_shape_record()],
    )
    assert with_gap.band is ConfidenceBand.MEDIUM
    assert disclosed.band is ConfidenceBand.MEDIUM


# --- 2. the five real site files carry the records, with the content D45 §8.1 requires ----------


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict]:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {e["site"]: e for e in entries}


def _manifest_entry(site, manifest: dict[str, dict]) -> dict:
    """Resolve a site's manifest entry through the SERIES IT ACTUALLY READS, never by name.

    Name-matching is what let the retired `bishops_cleeve`/`ewelme`/`knowl_hill` modelled entries go
    on satisfying these tests after no site file referenced them any more: the manifest keeps
    superseded entries, so a test keyed on a name can pass against a series nothing prices. Matching
    on `half_hourly_file_ref` means the entry checked is the one the run would load.
    """
    ref = site.load_data.half_hourly_file_ref
    matches = [e for e in manifest.values() if e.get("file") == ref]
    assert len(matches) == 1, f"{site.site_id}: {ref!r} matches {len(matches)} manifest entries"
    return matches[0]


@pytest.mark.parametrize("name", sorted(MODELLED_SITES))
def test_every_modelled_site_discloses_exactly_what_is_modelled(name: str) -> None:
    """A modelled basis must be declared, capped, and itemised — not merely flagged.

    The exact key SET is asserted rather than "at least a shape record", because the two failure
    modes are opposite and both matter: a dropped record hides a modelled input, and a record the
    file's basis does not justify (a shape record on a file whose shape is measured) claims an
    assumption that was never made and sends the reader chasing it.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    assert site.load_data.half_hourly_basis == "modelled"
    assert site.data_tier is DataTier.T1_PARTIAL_HALF_HOURLY
    records = {
        r.key: r for r in site.assumptions_requiring_confirmation if r.key.startswith("load_data.")
    }
    assert set(records) == MODELLED_SITES[name]
    for key, rec in records.items():
        assert rec.kind == "derived_input"
        assert rec.basis and rec.confirmation_ask, f"{name}/{key}: a record with nothing to check"
        # Shape-like inputs cap at MEDIUM; a modelled PEAK is structural and caps at LOW (CLAUDE.md
        # "Ceilings"). Neither may be recorded as HIGH, which would make the record decorative.
        expected = ConfidenceBand.LOW if key == PEAK_KEY else ConfidenceBand.MEDIUM
        assert rec.confidence_ceiling is expected, f"{name}/{key}: wrong ceiling"


@pytest.mark.parametrize("name", METERED_SITES)
def test_a_metered_site_carries_no_shape_assumption_at_all(name: str) -> None:
    """The control, widened from Harwell alone to every metered supply.

    A metered site has nothing to assume about its load, so a `load_data.*` record on one would mean
    an archetype sitting beside real measurement — the inverse of the defect this file pins, and the
    one that the rebuild made possible by giving five sites their own meters.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    assert site.load_data.half_hourly_basis == "metered"
    assert site.data_tier is DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    load_records = [
        r.key for r in site.assumptions_requiring_confirmation if r.key.startswith("load_data.")
    ]
    assert load_records == [], f"{name} is metered yet assumes {load_records}"


def test_the_two_bases_of_one_site_are_never_both_metered_or_both_modelled() -> None:
    """Ewelme and Knowl Hill each ship two files under ONE `site_id`, and the pair must differ.

    That is the whole reason two files exist: one prices the supply as measured, the other prices
    the invoiced whole-site total on a scaled shape. If they ever agreed on basis they would be two
    names for one answer, and the `site_id` they share would make them indistinguishable downstream.
    """
    twins = (
        ("ewelme", "ewelme_invoice_anchored"),
        ("knowl_hill", "knowl_hill_invoice_anchored"),
    )
    for metered, modelled in twins:
        a = load_site_model_json(SITES / f"{metered}.json")
        b = load_site_model_json(SITES / f"{modelled}.json")
        assert a.site_id == b.site_id, "the twins are two bases for ONE site"
        assert a.load_data.half_hourly_basis == "metered"
        assert b.load_data.half_hourly_basis == "modelled"
        assert a.load_data.annual_kwh != b.load_data.annual_kwh
        assert a.load_data.max_import_kw != b.load_data.max_import_kw


@pytest.mark.parametrize("name", sorted(ARCHETYPE_SITES))
def test_the_archetype_site_names_its_archetype(name: str) -> None:
    """The archetype's own name is the value of the record, and it must match the manifest's."""
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
    assert rec.value == ARCHETYPE_SITES[name]
    assert rec.confidence_ceiling is ConfidenceBand.MEDIUM


@pytest.mark.parametrize("name", sorted(ARCHETYPE_SITES))
def test_the_shape_record_says_it_is_an_archetype_and_cites_nothing(name: str) -> None:
    """An uncited archetype must not be labelled `sourced_default`, and must LOOK uncited.

    No sector-matched intraday source for a UK MRF or a vehicle workshop exists (a 26-step search
    found none), so an empty `sources` list is the honest record. A borrowed citation would be the
    one unrecoverable error, and a `sourced_default` label would imply one.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
    assert rec.derivation_method == "archetype_pending_source"
    assert rec.sources == []
    assert "no citation recorded" in rec.statement()
    assert "ARCHETYPE_PENDING_SOURCE" in rec.basis
    assert "no sector-matched source exists" in rec.basis.lower()


@pytest.mark.parametrize("name", sorted(ARCHETYPE_SITES))
def test_the_shape_record_separates_measured_anchors_from_the_modelled_shape(name: str) -> None:
    """D45 §8.1: say which components are MEASURED, not merely that something was modelled."""
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
    basis = rec.basis
    assert "ANCHORS" in basis
    assert "MEASURED" in basis
    assert "12 monthly invoice totals" in basis
    assert "month-to-month" in basis


@pytest.mark.parametrize("name", sorted(set(MODELLED_SITES) - set(ARCHETYPE_SITES)))
def test_the_scaled_basis_separates_what_is_measured_from_what_is_modelled(name: str) -> None:
    """The same D45 §8.1 obligation, on the files where the reference profile is the site's meter.

    These are Gate 2's second limb, and the distinction they have to draw is finer than the
    archetype's: the shape here IS measured, so a reader who takes "modelled basis" to mean "shape
    invented" would understate the evidence, and one who takes "own meter" to mean "measured" would
    price a peak that is an arithmetic product. Both halves must be named explicitly.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SCALED_TO_KEY)
    basis = rec.basis
    assert "WHAT IS MEASURED" in basis
    assert "WHAT IS MODELLED" in basis
    assert "Gate 2's second limb" in basis
    assert "THIS SITE'S OWN METER" in basis
    assert "rather than a literature archetype" in basis
    # The scaling is one constant on every interval, which is what preserves the measured shape.
    assert "multiplied by one constant" in basis
    assert "PERIOD MISMATCH" in basis, "the shape year and the anchor year are not the same year"
    # And the level really is the invoice's, not the meter's.
    assert rec.value == site.load_data.annual_kwh


@pytest.mark.parametrize("name", sorted(set(MODELLED_SITES) - set(ARCHETYPE_SITES)))
def test_the_scaled_peak_is_declared_an_arithmetic_product_at_a_low_ceiling(name: str) -> None:
    """Scaling multiplies the maximum half-hour by the same constant as the mean. Say so.

    This is the record that would have prevented the defect the rebuild found: Knowl Hill's modelled
    22.619 kW peak was fed to `SizingDomain.from_site` as a real capacity and capped the entire
    search at 22.6 kW against a measured 227.2 kW. A modelled peak is structural, so it caps the
    band at LOW and the record must name the mechanisms it propagates into.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == PEAK_KEY)
    assert rec.value == site.load_data.max_import_kw
    assert rec.unit == "kw"
    assert rec.confidence_ceiling is ConfidenceBand.LOW
    assert "ARITHMETIC PRODUCT, NOT AN OBSERVATION" in rec.basis
    assert "min(power, load)" in rec.basis, "the discharge cap the modelled peak silently sets"
    assert "DIRECTION OF EFFECT" in rec.basis
    assert "RETIRED BY" in rec.basis, "a structural assumption must name what would retire it"


@pytest.mark.parametrize("name", sorted(MODELLED_SITES))
def test_every_modelled_site_has_measured_seasonality(name: str, manifest) -> None:
    """The record's "seasonality is measured" claim must match the manifest, not outlive it.

    This is the assertion that would have caught the stale Tanhouse text: its record claimed the
    seasonal shape was modelled long after the per-meter tab that measures it was extracted. The
    manifest entry is resolved through the site's own `half_hourly_file_ref`, so a superseded entry
    that still bears the site's name cannot satisfy it.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    components = _manifest_entry(site, manifest)["measured_components"]
    assert "seasonal (month-to-month) variation" in components, (
        f"{name}: the record claims measured seasonality; the manifest no longer agrees"
    )


@pytest.mark.parametrize("name", sorted(ARCHETYPE_SITES))
def test_the_shape_record_matches_its_manifest_entry(name: str, manifest) -> None:
    """The register is transcription, not re-derivation: the profile must be the manifest's.

    ``tanhouse_colnbrook`` is the trap. That manifest key's own rationale says it covers COLNBROOK
    ONLY; Tanhouse is a separate series generated from the roll-up. Colnbrook's record must map to
    that key and say so, or a reader will double-count the 4,712,986 kWh combined figure.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    entry = _manifest_entry(site, manifest)
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
    assert rec.value == entry["profile"]
    assert entry["profile_source"].upper() in rec.basis.upper()
    assert entry["site"] in rec.basis
    assert "annual energy" in entry["measured_components"]
    if name == "colnbrook":
        assert "COLNBROOK ONLY" in rec.basis
        # Purchased across the pair, and the gross figure that used to be quoted as their combined
        # total, so neither can be double-counted onto this series.
        assert "4,573,813.6" in rec.basis
        assert "4,712,986" in rec.basis


@pytest.mark.parametrize("name", sorted(set(MODELLED_SITES) - set(ARCHETYPE_SITES)))
def test_the_scaled_record_matches_its_manifest_entry(name: str, manifest) -> None:
    """Transcription, not re-derivation, on the scaled files: the anchor and factor are the
    manifest's own, and the manifest key is named so a reader can find the series."""
    site = load_site_model_json(SITES / f"{name}.json")
    entry = _manifest_entry(site, manifest)
    assert entry["status"] == "modelled"
    assert entry["basis_variant"] == "invoice_anchored"
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SCALED_TO_KEY)
    assert rec.value == pytest.approx(entry["total_kwh"], abs=0.05)
    assert str(entry["scale_factor"]) in rec.basis, "the multiplier is the whole derivation"
    assert entry["site"] in rec.basis
    peak = next(r for r in site.assumptions_requiring_confirmation if r.key == PEAK_KEY)
    assert peak.value == pytest.approx(entry["peak_kw"], abs=0.001)
    assert "modelled_peak_warning" in " ".join(peak.sources), "the manifest's own warning, cited"


@pytest.mark.parametrize("name", sorted(MODELLED_SITES))
def test_the_confirmation_ask_is_the_measured_maximum_demand(name: str) -> None:
    """The cheap ask that breaks the circularity, not another request for full HH metering.

    On the archetype file `hh_synthesis` computes `peak_kw` FROM the archetype, so the peak is a
    model output rather than an observation; on the scaled files the peak is the measured peak times
    a constant, which is no more an observation than the archetype's. Either way the monthly
    max-demand / capacity-charge kVA readings already sit on the customer's invoices and are what
    make the modelled peak testable at all — so the ask must be that, on every modelled file.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    key = SHAPE_KEY if name in ARCHETYPE_SITES else PEAK_KEY
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == key)
    ask = rec.confirmation_ask.upper()
    assert "MAXIMUM DEMAND" in ask
    assert "KVA" in ask
    if name in ARCHETYPE_SITES:
        # The archetype's peak is derived from the archetype, so checking it against the archetype
        # proves nothing. That trap has to be stated where the peak is quoted.
        assert "CIRCULARITY" in rec.basis.upper()
        assert "peak_kw" in rec.basis


def test_the_corroboration_is_stated_with_its_limit_and_is_not_borrowed() -> None:
    """45.2% against Elexon PC3's 45.3% corroborates peak-to-mean ONLY, and only for `waste_mrf`.

    The four sites that used to share this pin are metered now and carry no archetype at all;
    Colnbrook is the one left. The rule it enforces is unchanged and is the reason the pin is worth
    keeping on one site: a corroboration that travels beyond the archetype it was computed for is a
    borrowed citation, which is the one class of error that cannot be walked back.
    """
    for name in ARCHETYPE_SITES:
        site = load_site_model_json(SITES / f"{name}.json")
        rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
        assert "45.2%" in rec.basis and "45.3%" in rec.basis
        assert "peak-to-mean only" in rec.basis
        assert "ONE SCALAR" in rec.basis


def test_the_ewelme_substitution_claims_no_corroboration_it_does_not_have() -> None:
    """The anchored Ewelme file puts an MRF's shape on a vehicle workshop, and nothing tests that.

    It replaces a `workshop_depot` archetype, so the temptation is to present a measured shape as
    straightforwardly better. It is better evidence *of the MRF* and an open assumption *about the
    Workshop*, which is 82% of the load. The record has to say the substitution is uncorroborated
    and name the one thing that would settle it, or "measured" does the work "sourced" used to.
    """
    site = load_site_model_json(SITES / "ewelme_invoice_anchored.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
    assert rec.derivation_method == "site_analogue", "not an archetype, and not labelled as one"
    assert rec.sources, "the site's own data IS citable here, unlike the archetype's"
    assert "Nothing corroborates the substitution" in rec.basis
    assert "82%" in rec.basis, "the share of load the stand-in shape is applied to"
    assert "DIFFERENT BUSINESS TYPES" in rec.basis
    assert "200 vs 750 kVA" in rec.basis, "the evidence they are not one operation"
    # The ask is the missing meter, not a better curve — a substitute shape cannot settle this.
    assert "2000055901639" in rec.confirmation_ask
    assert "no substitute curve improves on it" in rec.basis


@pytest.mark.parametrize("name", sorted(ARCHETYPE_SITES))
def test_the_record_says_a_sourced_substitute_would_be_worse(name: str) -> None:
    """The trap the search found: "sourced" is not automatically better than labelled.

    Both real candidate datasets are multi-site aggregates, and aggregation inflates load factor.
    Substituting either would CUT the modelled peak, understating peak-shaving value, DUoS capacity
    exposure and the required battery power rating.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    rec = next(r for r in site.assumptions_requiring_confirmation if r.key == SHAPE_KEY)
    upper = rec.basis.upper()
    assert "22%" in rec.basis and "38%" in rec.basis
    assert "MODELLED PEAK" in upper
    assert "MULTI-SITE AGGREGATE" in upper
    assert "PEAK-SHAVING" in upper


def test_the_retired_bishops_cleeve_anomaly_correction_is_superseded_not_erased(manifest) -> None:
    """The BC Lodge catch-up bill was a correction to a scaling ANCHOR, and no site scales now.

    The record it justified lived on the combined `bishops_cleeve.json`, which the metered rebuild
    deleted: the meter proved Ash Plant and MRF run concurrently 91.7% of the time, so they are two
    supplies rather than a composite, and neither is scaled to a monthly invoice total any more.
    The subject of the assertion is therefore gone — there is no anchor to correct.

    What must NOT happen is the correction quietly vanishing with it. The repo's rule for a
    superseded series is "superseded, not deleted" (`test_the_retired_tanhouse_series_is_preserved_
    not_deleted`), so the manifest entry keeps the anomaly note with every figure that made the
    reading a catch-up bill rather than March consumption. A future reader comparing the metered
    Bishops Cleeve totals against the 2025 invoices meets that 10,128 kWh again, and this is where
    the answer is.
    """
    entry = manifest["bishops_cleeve"]
    assert entry["status"] == "modelled", "the retired entry, kept for exactly this reason"
    assert not any(
        entry["file"] == load_site_model_json(p).load_data.half_hourly_file_ref
        for p in SITES.glob("*.json")
    ), "no live site file reads this series any more; if one does, restore its DerivedAssumption"
    assert len(entry["anomaly_corrections"]) == 1
    note = entry["anomaly_corrections"][0]
    for token in ("BC Lodge", "10,128", "437", "11-month mean", "68x", "~150 kWh/month"):
        assert token in note, f"the anomaly note lost {token!r}; the correction is now illegible"


@pytest.mark.parametrize("name", METERED_SITES)
def test_a_metered_site_states_that_its_shape_is_measured(name: str, manifest) -> None:
    """The positive half of the metered control: the manifest agrees the shape was measured.

    Read through `half_hourly_file_ref`, so a site pointing at a retired modelled series while
    declaring itself metered fails here rather than being taken at its word. Harwell predates the
    Grundon manifest and carries its own extraction summary, so it is checked on the site record.
    """
    site = load_site_model_json(SITES / f"{name}.json")
    if name == "harwell":
        assert site.load_data.half_hourly_file_ref
        assert site.load_data.half_hourly_reference_profile is None
        return
    entry = _manifest_entry(site, manifest)
    assert entry["status"] == "metered"
    for component in ("annual energy", "within-day shape", "peak demand"):
        assert component in entry["measured_components"], f"{name}: {component} is not measured"
    assert site.load_data.max_import_kw == pytest.approx(entry["peak_kw"], abs=0.001)
    assert site.load_data.annual_kwh == pytest.approx(entry["total_kwh"], abs=0.05)


def test_tanhouse_does_not_silently_price_solar_it_never_bought() -> None:
    """139,172.28 kWh of the RETIRED series was solar the customer never bought.

    A battery cannot avoid a cost that was never charged, so self-generated energy must not sit in
    the priced series. On the modelled basis that was a subtraction the model had to make by hand,
    from a gross roll-up. The meter supplies the volume directly now, so the subtraction is no
    longer inferred — but the solar has NOT gone away, and the metered 1,600,178 kWh sits AWKWARDLY
    BETWEEN the purchased 1,541,453 and the gross 1,680,626. Which of the three the meter physically
    measures is therefore an open question, and the record's obligation is to say so rather than let
    "measured" imply it is settled. That obligation is the same one; only the arithmetic moved.
    """
    site = load_site_model_json(SITES / "tanhouse.json")
    assert site.load_data.annual_kwh == 1600178.485
    assert site.annual_consumption_kwh == site.load_data.annual_kwh, (
        "annual_consumption_kwh must be the priced figure: value_stack/gates.py prefers it over "
        "load_data.annual_kwh, and no SiteModel field means 'gross including self-generation'"
    )
    # The measured volume genuinely sits between the two invoice figures. This is the arithmetic
    # that makes the open question real rather than a caveat the record could drop.
    assert 1541453.4 < site.load_data.annual_kwh < 1680625.68

    joined = " ".join(site.load_data.validation_warnings)
    assert "1,541,453.4" in joined and "1,680,625.68" in joined
    assert "139,172.28" in joined, "the solar volume is still recorded, not deleted"
    assert "pv_generation_kw" in joined, "and the reason it cannot be priced is still named"
    # The ambiguity is raised as an ask, not resolved by assertion.
    assert any("WHAT THE PW METER PHYSICALLY MEASURES" in a for a in site.missing_data)


def test_tanhouse_gross_consumption_lives_in_the_manifest_not_the_site_model(manifest) -> None:
    """The +9% figure needs a home that no consumer can mistake for priceable volume.

    It lives on the SUPERSEDED modelled manifest entry — the metered entry has no
    `onsite_generation_2025` block, because the meter measures a volume rather than decomposing it.
    That is the one place the decomposition survives, so this reads it there deliberately.
    """
    entry = manifest["tanhouse"]["onsite_generation_2025"]
    assert entry["purchased_import_kwh"] == 1541453.4
    assert entry["gross_consumption_kwh"] == 1680625.68
    assert entry["self_generated_kwh"] == 139172.28
    assert entry["granularity"] == "annual_total_only"
    site = load_site_model_json(SITES / "tanhouse.json")
    assert site.annual_consumption_kwh != entry["gross_consumption_kwh"]


def test_tanhouses_stale_modelled_basis_warnings_are_gone() -> None:
    """A warning that became false is a disclosure defect like any other.

    Three of these described the modelled basis and are now simply untrue of a metered site; the
    positive assertions are what replaced them. `RETIRED` is the load-bearing word: a reader must be
    able to tell that the archetype was withdrawn rather than merely unmentioned.
    """
    site = load_site_model_json(SITES / "tanhouse.json")
    joined = " ".join(site.load_data.validation_warnings)
    assert "WEAKEST LOAD BASIS OF THE FIVE" not in joined
    assert "No per-meter tab exists for Tanhouse" not in joined
    assert "overstates the avoidable volume by up to 8.3%" not in joined
    assert "METERED SERIES" in joined
    assert "RETIRED" in joined, "the retired archetype must be named as retired, not just dropped"
    assert "103.8%" in joined, "the metered year against the invoice it reconciles to"


def test_the_data_tier_follows_the_half_hourly_basis_at_every_site() -> None:
    """The tier grades half-hourly COVERAGE, so it must track the basis and nothing else.

    Tanhouse used to be the exhibit for "a stronger scaling anchor is not metered data" and it has
    since graduated: it has its own meter now. The invariant that survives is the general one the
    exhibit was an instance of — a modelled site cannot claim the validated-half-hourly tier, and a
    metered site should not be held below it. Stated portfolio-wide, it also catches the reverse
    error the rebuild made possible: a file relabelled `metered` without its tier following.
    """
    for path in sorted(SITES.glob("*.json")):
        site = load_site_model_json(path)
        expected = (
            DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
            if site.load_data.half_hourly_basis == "metered"
            else DataTier.T1_PARTIAL_HALF_HOURLY
        )
        assert site.data_tier is expected, (
            f"{path.name}: basis {site.load_data.half_hourly_basis!r} with tier {site.data_tier}"
        )


def test_the_retired_tanhouse_series_is_preserved_not_deleted() -> None:
    """A sibling test asserts the old file stays until the swap lands, so it must not be removed."""
    hh = REPO / "data" / "sites" / "hh"
    assert (hh / "grundon_tanhouse_modelled_2025_d386ac8376c45a37.csv").is_file()
    assert (hh / "grundon_tanhouse_modelled_2025_138b75ff5c2c0719.csv").is_file()
