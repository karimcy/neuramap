"""The five Grundon sites are wired to their OWN METERED half-hourly series, and stay wired.

**What this file is for.** Real metered half-hourly data arrived for all five Grundon supplies and
the literature archetypes were retired. A site JSON that quietly reverts to a modelled series — or
that keeps a metered `half_hourly_file_ref` while its `annual_kwh` / `max_import_kw` drift back to
invoice-derived or archetype figures — would be invisible: the pipeline would run, the numbers
would change, and nothing would say so. This repo has shipped a built-tested-never-called defect
eight times, so the acceptance bar (`docs/validation_requirements.md` §3) is not "a test exists"
but "a test FAILS if the wiring is removed". Every assertion below recomputes from the CSV bytes on
disk rather than trusting the manifest or the JSON, so the failure is loud and specific.

**The defect this pins.** `examples/real/sites/knowl_hill.json` carried ``annual_kwh = 45732.3`` —
exactly the ``invoiced_kwh`` in `data/sites/grundon_invoices_2025.json` to one decimal — and
``max_import_kw = 22.619`` from an archetype scaled to it. The meter peaks at **227.2 kW, ten times
higher**. Peak drives sizing, capex, MIC headroom, Capacity Market de-rated capacity and above all
the behind-the-meter discharge cap ``min(power, load)``, so a 22.6 kW peak made any battery above
~22 kW unusable *by construction*. `test_no_site_load_figure_equals_an_invoice_total` is the
tripwire for that specific failure mode: a load figure that equals an invoice line to the decimal
is the fingerprint of a model built from a bill rather than from a meter.

**Two bases, and they must never collide.** Ewelme and Knowl Hill each carry a second,
INVOICE-ANCHORED basis in its own file: the site's own metered SHAPE scaled to its own reconciled
annual invoice total (validation Gate 2's second limb, with the site's own meter in place of a
literature reference profile). Both bases share a ``site_id`` by design, so the only thing keeping
two runs apart is their inputs — `test_the_two_bases_of_a_site_can_never_share_a_run_identity`
computes the canonical site hash for each pair and fails if they match.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from gridq.ingestion import load_site_model_json
from gridq.ingestion.data_tier import infer_data_tier_from_hh_summary
from gridq.ingestion.hh_data import (
    load_half_hourly_csv,
    summarise_half_hourly_data,
)
from gridq.ingestion.hh_mandate import admit_half_hourly
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import _canonical_site_dump
from gridq.schemas import ConfidenceBand, DataTier, HalfHourlyBasis

REPO = Path(__file__).resolve().parents[1]
SITES = REPO / "examples" / "real" / "sites"
HH = REPO / "data" / "sites" / "hh"
MANIFEST = HH / "manifest.json"
INVOICES = REPO / "data" / "sites" / "grundon_invoices_2025.json"

#: filename stem -> the site_id it must declare. **Exactly five supplies**, and Bishops Cleeve is
#: TWO of them: Ash Plant and MRF run concurrently 91.7% of the time, so they are separate
#: supplies, not parent and child. A single combined `bishops_cleeve.json` is a defect, not a
#: legacy file — see `test_the_retired_combined_bishops_cleeve_model_is_gone`.
METERED_SITES: dict[str, str] = {
    "bishops_cleeve_ash_plant": "grundon_bishops_cleeve_ash_plant",
    "bishops_cleeve_mrf": "grundon_bishops_cleeve_mrf",
    "knowl_hill": "grundon_knowl_hill",
    "ewelme": "grundon_ewelme",
    "tanhouse": "grundon_tanhouse",
}

#: The INVOICE-ANCHORED companions: filename stem -> (site_id, the metered stem it is scaled from).
#: The shape is the site's OWN meter; only the level is modelled.
INVOICE_ANCHORED_SITES: dict[str, tuple[str, str]] = {
    "ewelme_invoice_anchored": ("grundon_ewelme", "ewelme"),
    "knowl_hill_invoice_anchored": ("grundon_knowl_hill", "knowl_hill"),
}

#: Strings that can only appear if a literature archetype has come back. They are retired for every
#: metered site; `sources` was deliberately empty on all of them, so no citation is lost by their
#: going. `waste_mrf` and `workshop_depot` remain legitimate for `colnbrook.json`, which still has
#: no metered export — hence the scoping to the five files.
RETIRED_ARCHETYPE_MARKERS = ("waste_mrf", "workshop_depot", "archetype_pending_source")

pytestmark = pytest.mark.skipif(
    not (SITES / "tanhouse.json").is_file(),
    reason="examples/real is gitignored private evidence and is not installed on this host",
)


def _series(path: Path) -> list[tuple[str, float]]:
    """The CSV as (timestamp, kWh) — read from the bytes on disk, never from the manifest."""
    with path.open(encoding="utf-8") as fh:
        return [(r["timestamp"], float(r["import_kwh"])) for r in csv.DictReader(fh)]


def _manifest() -> dict[str, dict]:
    return {e["site"]: e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))}


# =============================================================================================
# 1. Each of the five points at its OWN metered series — the wiring itself.
# =============================================================================================


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_the_site_declares_its_own_site_id(stem: str, site_id: str) -> None:
    """The five ids are a coordination contract: the invoice file is keyed by them."""
    assert load_site_model_json(SITES / f"{stem}.json").site_id == site_id


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_the_site_points_at_its_own_metered_csv(stem: str, site_id: str) -> None:
    """THE WIRING. Fails the moment a site stops pointing at its metered series.

    Three things at once, because any one alone can be satisfied by a stale file: the ref names
    a `_metered_` file, that file exists on disk, and its name carries THIS site's id (so two
    sites can never be cross-wired to the same meter).
    """
    site = load_site_model_json(SITES / f"{stem}.json")
    ref = site.load_data.half_hourly_file_ref
    assert ref, f"{stem}: no half_hourly_file_ref — the site is not wired to any series"
    assert "_metered_" in Path(ref).name, (
        f"{stem}: half_hourly_file_ref is {ref!r}, which is not a METERED series. Real metered "
        "half-hourly data exists for all five Grundon supplies; reverting to a modelled or "
        "invoice-derived series silently changes every number the run produces."
    )
    assert Path(ref).name.startswith(f"{site_id}_metered_"), (
        f"{stem}: {Path(ref).name!r} does not belong to {site_id} — sites are cross-wired"
    )
    assert (REPO / ref).is_file(), f"{stem}: {ref} does not exist on disk"


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_the_declared_basis_is_metered_and_gate_2_agrees(stem: str, site_id: str) -> None:
    """The basis is read from the record, never inferred from a path (N9), so it must be recorded.

    Gate 2 is what turns the declaration into a confidence ceiling, so the gate's own verdict is
    asserted rather than the field alone: METERED is the only basis that can reach HIGH.
    """
    site = load_site_model_json(SITES / f"{stem}.json")
    assert site.load_data.half_hourly_basis == HalfHourlyBasis.METERED.value
    admission = admit_half_hourly(site)
    assert admission.basis is HalfHourlyBasis.METERED
    assert admission.is_metered
    assert admission.confidence_ceiling is ConfidenceBand.HIGH


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_the_load_figures_are_recomputed_from_the_series_bytes(stem: str, site_id: str) -> None:
    """`annual_kwh` and `max_import_kw` must BE the series, not a figure that once matched it.

    Recomputed from the CSV on disk. This is the assertion that fails if the file_ref stays
    metered while the load figures drift back to an invoice total or an archetype peak — the
    exact shape of the Knowl Hill defect (45,732.3 kWh / 22.619 kW against 108,242 kWh / 227.2 kW).
    """
    site = load_site_model_json(SITES / f"{stem}.json")
    rows = _series(REPO / str(site.load_data.half_hourly_file_ref))
    total = sum(kwh for _, kwh in rows)
    peak = max(kwh for _, kwh in rows) * 2.0

    assert site.load_data.annual_kwh == pytest.approx(total, abs=0.01), (
        f"{stem}: annual_kwh {site.load_data.annual_kwh} != the series' own {total:,.3f} kWh"
    )
    assert site.load_data.max_import_kw == pytest.approx(peak, abs=0.01), (
        f"{stem}: max_import_kw {site.load_data.max_import_kw} != the series' own measured peak "
        f"{peak:,.3f} kW. Peak drives sizing, capex, MIC headroom, CM de-rated capacity and the "
        "behind-the-meter discharge cap min(power, load) — a wrong peak makes a whole size range "
        "unusable by construction."
    )
    # The annual figure the rest of the schema reads must not disagree with the load block.
    assert site.annual_consumption_kwh == pytest.approx(total, abs=0.01)
    # Coverage metadata is part of the wiring: a stale 17,520/0 pair would hide a changed file.
    assert site.load_data.half_hourly_rows == len(rows)
    assert site.load_data.missing_intervals == 2, (
        "2 intervals are absent from the SOURCE exports (the portal's fixed 48-column day cannot "
        "represent the 50 half-hours of the 2025-10-26 autumn clock change). Counted, never "
        "back-filled."
    )
    assert str(site.load_data.start_date) == rows[0][0][:10]
    assert str(site.load_data.end_date) == rows[-1][0][:10]


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_the_manifest_and_the_site_agree_on_the_same_file(stem: str, site_id: str) -> None:
    """The manifest is the provenance record; a site pointing somewhere else is a silent fork."""
    entry = _manifest()[site_id]
    assert entry["status"] == "metered"
    site = load_site_model_json(SITES / f"{stem}.json")
    assert site.load_data.half_hourly_file_ref == entry["file"]


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_the_data_tier_is_what_the_repo_s_own_classifier_says(stem: str, site_id: str) -> None:
    """T2 is earned from coverage, not asserted: a full metered year is a validated 12 months."""
    site = load_site_model_json(SITES / f"{stem}.json")
    summary = summarise_half_hourly_data(
        load_half_hourly_csv(REPO / str(site.load_data.half_hourly_file_ref))
    )
    assert infer_data_tier_from_hh_summary(summary, has_annual_kwh=True) == (
        DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY
    )
    assert site.data_tier == DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY


# =============================================================================================
# 2. The archetypes are retired, and the invoice can never become the load basis again.
# =============================================================================================


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_no_retired_archetype_is_declared_as_this_site_s_shape(stem: str, site_id: str) -> None:
    """The shape is measured now. An archetype in a shape FIELD would be a silent regression.

    Scoped to the fields that declare the shape — the prose fields legitimately DISCUSS the
    retired archetypes, and that history is worth keeping readable.
    """
    site = load_site_model_json(SITES / f"{stem}.json")
    assert site.load_data.half_hourly_reference_profile is None
    assert site.load_data.half_hourly_scaled_to is None
    for record in site.assumptions_requiring_confirmation:
        assert record.key != "load_data.half_hourly_reference_profile", (
            f"{stem}: a reference-profile record is back on a METERED site — the shape is the "
            "site's own meter, so there is no reference profile to record."
        )
        assert record.derivation_method != "archetype_pending_source"
        assert not any(m in str(record.value or "") for m in RETIRED_ARCHETYPE_MARKERS)


@pytest.mark.parametrize(("stem", "site_id"), sorted(METERED_SITES.items()))
def test_no_site_load_figure_equals_an_invoice_total(stem: str, site_id: str) -> None:
    """THE FINGERPRINT TEST. A load figure equal to an invoice line means a model built from a bill.

    Knowl Hill carried `annual_kwh = 45732.3`, which was `invoiced_kwh` to one decimal. A meter and
    an invoice measure different twelve months and essentially never agree to 0.1 kWh, so equality
    is evidence of provenance, not of accuracy.
    """
    invoices = json.loads(INVOICES.read_text(encoding="utf-8"))
    site = load_site_model_json(SITES / f"{stem}.json")
    for key, entry in invoices.items():
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        invoiced = entry.get("invoiced_kwh")
        if invoiced is None:
            continue
        assert site.load_data.annual_kwh != pytest.approx(float(invoiced), abs=0.1), (
            f"{stem}: annual_kwh equals data/sites/grundon_invoices_2025.json['{key}']"
            f".invoiced_kwh ({invoiced}). On a METERED basis the annual figure must come from the "
            "meter. If an invoice-anchored basis is wanted, it belongs in its own file with "
            "half_hourly_basis='modelled' and a declared scaling record."
        )


def test_the_retired_combined_bishops_cleeve_model_is_gone() -> None:
    """Bishops Cleeve is two supplies. One combined 5-MPAN model is a connection that never existed.

    Ash Plant and MRF run concurrently 91.7% of the time, so neither is a sub-meter of the other.
    The retired `bishops_cleeve.json` summed five MPANs into a single 387,008 kWh / 125.9 kW
    connection with one agreed capacity and one commodity rate — none of which any meter has.
    """
    assert not (SITES / "bishops_cleeve.json").exists(), (
        "examples/real/sites/bishops_cleeve.json is back. Bishops Cleeve is TWO metered supplies "
        "with different agreed capacities (93 / 250 kVA), different TCR bands (2 / 4) and "
        "different invoiced commodity rates; they are modelled in bishops_cleeve_ash_plant.json "
        "and bishops_cleeve_mrf.json."
    )


# =============================================================================================
# 3. Physical consistency — a peak above the connection is impossible, not merely odd.
# =============================================================================================


@pytest.mark.parametrize("stem", sorted(METERED_SITES) + sorted(INVOICE_ANCHORED_SITES))
def test_the_peak_never_exceeds_the_declared_connection(stem: str) -> None:
    """A measured peak above the declared MIC means one of the two is wrong; both are load-bearing.

    Tanhouse is the live case: the meter peaks at 486.35 kW and the retired file declared
    ``mic_kw = 451.863`` (the MODELLED peak of an archetype), which is both physically impossible
    against the meter and the source of a self-inflicted `import_headroom_kw = 0`
    "capacity-blocked" verdict — a data-blocked result dressed as a physical one.
    """
    site = load_site_model_json(SITES / f"{stem}.json")
    peak = site.load_data.max_import_kw
    mic = site.connection.mic_kw
    assert peak is not None and mic is not None
    assert peak <= mic + 1e-6, (
        f"{stem}: max_import_kw {peak} exceeds connection.mic_kw {mic} — the site cannot import "
        "more than its connection allows, so one of the two figures is wrong."
    )


def test_tanhouse_s_unknown_capacity_is_a_declared_derivation_not_a_pinned_peak() -> None:
    """No kVA figure exists for Tanhouse. D45 requires a DERIVED capacity + the ask, not a refusal.

    And specifically not ``mic_kw = the peak``, which manufactures zero headroom by construction.
    """
    site = load_site_model_json(SITES / "tanhouse.json")
    assert site.connection.mic_kw != site.load_data.max_import_kw, (
        "mic_kw is pinned to the peak again — that makes import_headroom_kw 0 by construction and "
        "relabels an economic result as a data problem (CLAUDE.md §2)."
    )
    records = {r.key: r for r in site.assumptions_requiring_confirmation}
    mic_record = records.get("connection.mic_kw")
    assert mic_record is not None, "a derived connection capacity must be RECORDED (D45)"
    assert mic_record.kind == "derived_input"
    assert mic_record.value == site.connection.mic_kw
    # Structural feasibility input: `gridq.schemas.derived.ceiling_for_key` caps it at LOW.
    assert mic_record.confidence_ceiling is ConfidenceBand.LOW
    assert mic_record.confirmation_ask


# =============================================================================================
# 4. The invoice-anchored basis: the site's own SHAPE, a modelled LEVEL, declared as both.
# =============================================================================================


@pytest.mark.parametrize(("stem", "spec"), sorted(INVOICE_ANCHORED_SITES.items()))
def test_the_invoice_anchored_series_is_the_site_s_own_metered_shape(
    stem: str, spec: tuple[str, str]
) -> None:
    """Gate 2's second limb, with the site's own meter as the reference profile.

    Proved rather than asserted: every interval of the anchored series is the SAME constant
    multiple of the metered series, so the shape is measured and only the level is modelled. A
    reshaped or archetype-derived series fails this.
    """
    _site_id, metered_stem = spec
    anchored = load_site_model_json(SITES / f"{stem}.json")
    metered = load_site_model_json(SITES / f"{metered_stem}.json")
    a = _series(REPO / str(anchored.load_data.half_hourly_file_ref))
    m = _series(REPO / str(metered.load_data.half_hourly_file_ref))

    assert len(a) == len(m)
    assert [ts for ts, _ in a] == [ts for ts, _ in m], "the anchored series must not re-time"
    factor = sum(k for _, k in a) / sum(k for _, k in m)
    for (ts, av), (_, mv) in zip(a, m, strict=True):
        assert av == pytest.approx(mv * factor, abs=1e-4), (
            f"{stem}: interval {ts} is not the metered value x {factor:.9f} — the anchored series "
            "must be the site's own measured shape scaled by ONE constant, nothing reshaped."
        )


@pytest.mark.parametrize(("stem", "spec"), sorted(INVOICE_ANCHORED_SITES.items()))
def test_the_invoice_anchored_basis_declares_itself_modelled_and_caps_its_peak(
    stem: str, spec: tuple[str, str]
) -> None:
    """A scaled level is MODELLED, and the peak it implies is a structural derived input.

    The scaling multiplies the maximum half-hour by exactly the same factor as the mean, so the
    peak is arithmetic, not observation — and peak is what drives sizing, CM de-rated capacity and
    the ``min(power, load)`` discharge cap. It must therefore carry a LOW ceiling, per
    `gridq.schemas.derived.ceiling_for_key`.
    """
    site_id, metered_stem = spec
    site = load_site_model_json(SITES / f"{stem}.json")
    assert site.site_id == site_id
    assert site.load_data.half_hourly_basis == HalfHourlyBasis.MODELLED.value
    admission = admit_half_hourly(site)
    assert admission.basis is HalfHourlyBasis.MODELLED
    assert admission.confidence_ceiling is ConfidenceBand.MEDIUM

    records = {r.key: r for r in site.assumptions_requiring_confirmation}
    scaled = records.get("load_data.half_hourly_scaled_to")
    assert scaled is not None, "the scaling itself must be a recorded, confirmable assumption"
    assert scaled.confidence_ceiling is ConfidenceBand.MEDIUM
    peak_record = records.get("load_data.max_import_kw")
    assert peak_record is not None, (
        f"{stem}: the modelled peak must be recorded — it is the input that decides whether a "
        "battery of a given size can discharge at all."
    )
    assert peak_record.confidence_ceiling is ConfidenceBand.LOW
    assert peak_record.value == site.load_data.max_import_kw

    # The reference profile names the site's OWN meter, never an archetype.
    profile = site.load_data.half_hourly_reference_profile or ""
    assert "METERED" in profile and metered_stem.split("_")[0] not in RETIRED_ARCHETYPE_MARKERS
    assert not any(m in profile for m in RETIRED_ARCHETYPE_MARKERS)


@pytest.mark.parametrize(("stem", "spec"), sorted(INVOICE_ANCHORED_SITES.items()))
def test_the_invoice_anchored_total_is_the_site_s_own_invoice(
    stem: str, spec: tuple[str, str]
) -> None:
    """The anchor must BE the site's reconciled invoice total — the one place equality is right."""
    site_id, _ = spec
    site = load_site_model_json(SITES / f"{stem}.json")
    invoiced = float(json.loads(INVOICES.read_text(encoding="utf-8"))[site_id]["invoiced_kwh"])
    rows = _series(REPO / str(site.load_data.half_hourly_file_ref))
    assert sum(k for _, k in rows) == pytest.approx(invoiced, abs=0.01)
    assert site.load_data.annual_kwh == pytest.approx(invoiced, abs=0.01)
    assert site.load_data.max_import_kw == pytest.approx(max(k for _, k in rows) * 2.0, abs=1e-4)


@pytest.mark.parametrize(("stem", "spec"), sorted(INVOICE_ANCHORED_SITES.items()))
def test_the_two_bases_of_a_site_can_never_share_a_run_identity(
    stem: str, spec: tuple[str, str]
) -> None:
    """Two bases, one site_id — so only the INPUTS can keep the runs apart, and they must.

    ``input_hash`` is computed over the canonical site dump (plus result/finance, both derived from
    it), so a differing site hash is what guarantees two different ``run_id``s. Computed here, not
    assumed.
    """
    _site_id, metered_stem = spec
    anchored = load_site_model_json(SITES / f"{stem}.json")
    metered = load_site_model_json(SITES / f"{metered_stem}.json")
    assert anchored.site_id == metered.site_id, "the two bases are one site, by design"
    assert anchored.load_data.half_hourly_file_ref != metered.load_data.half_hourly_file_ref
    assert hash_json(_canonical_site_dump(anchored)) != hash_json(_canonical_site_dump(metered)), (
        f"{stem} and {metered_stem} hash identically — two load bases would share a run_id and "
        "become indistinguishable in the record."
    )
