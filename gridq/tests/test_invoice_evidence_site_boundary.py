"""The invoice evidence must be keyed by the site_ids that actually exist — Gate 3b's lookup key.

`gridq.quotes.pipeline._invoice_evidence_for` resolves a site's invoice **by `site_id`**, and every
failure mode — absent file, unreadable JSON, unknown site — collapses to the same absence record.
That is right for the gate (a reconciliation check must never kill a run) and it is exactly what
makes a key mismatch **silent**: evidence filed under a name nothing looks for is indistinguishable
from no evidence at all, and the run reports "NO INVOICE EVIDENCE EXISTS for this site" while the
invoice sits in the file two lines away.

This drive has already paid for that once. Bishops Cleeve was split into two sites after the
metered half-hourly data showed the Ash Plant and MRF supplies running concurrently 91.7% of the
time (`scripts/convert_grundon_hh.py`), while the invoice evidence stayed keyed by the retired
combined id `grundon_bishops_cleeve` — so both new sites resolved absence records and the whole
reconciliation was skipped without a single test going red.

**Why these tests go through `_invoice_evidence_for` and not just `invoice_evidence_from_mapping`.**
`invoice_evidence_from_mapping(site_id, raw[site_id])` is handed its entry by the caller, so it
cannot notice a key mismatch — it is the *parser*, not the *lookup*. Deleting the production
resolver, renaming a key, or renaming a site would leave a mapping-only test perfectly green. Every
assertion here starts from a site_id and nothing else, which is the only shape that fails when the
keys drift (`CLAUDE.md` §4: nothing is fixed until it is reachable).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from gridq.ingestion.reconciliation import invoice_evidence_from_mapping
from gridq.quotes.pipeline import _INVOICE_EVIDENCE_PATH, _invoice_evidence_for

REPO = Path(__file__).resolve().parents[1]
INVOICES = REPO / "data" / "sites" / "grundon_invoices_2025.json"

#: The site boundary as it now stands. These are the ids the HH converter writes and the ids the
#: site models carry; the evidence file must answer to exactly these and no others.
LIVE_SITE_IDS = (
    "grundon_bishops_cleeve_ash_plant",
    "grundon_bishops_cleeve_mrf",
    "grundon_knowl_hill",
    "grundon_ewelme",
    "grundon_tanhouse",
)

#: The retired combined boundary. Kept in the file as an explicit absence, never as a live invoice.
RETIRED_SITE_ID = "grundon_bishops_cleeve"

#: Each new Bishops Cleeve site's invoice IS one meter tab, verbatim: (invoiced kWh, invoiced £).
#: Pinned as literals so a re-derivation that quietly apportions, shares or drops a line fails here
#: rather than in a published pack.
BISHOPS_CLEEVE_SPLIT = {
    "grundon_bishops_cleeve_ash_plant": (66_765.6, 20_534.95),  # BC Ash Plant 1, MPAN 1420398100000
    "grundon_bishops_cleeve_mrf": (297_028.4, 75_627.65),  # BC MRF, MPAN 1460001884093
}

#: The retired combined figures, and the residual on the three meters that belong to NEITHER new
#: site (BC Import + BC Weighbridge + BC Lodge). The split does not sum to the combined total, and
#: this test exists partly to stop anyone "fixing" that.
RETIRED_COMBINED = (396_698.7, 110_869.87)
UNASSIGNED_RESIDUAL = (32_904.7, 14_707.27)


def _raw() -> dict:
    return json.loads(INVOICES.read_text(encoding="utf-8"))


def _convert_grundon_hh_sites() -> dict:
    """`scripts/convert_grundon_hh.py:SITES`, loaded by path (scripts/ is not an importable pkg)."""
    path = REPO / "scripts" / "convert_grundon_hh.py"
    spec = importlib.util.spec_from_file_location("convert_grundon_hh", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SITES


# =============================================================================================
# The reachability proof: a site_id in, real evidence out — through the PRODUCTION resolver.
# =============================================================================================


@pytest.mark.parametrize("site_id", BISHOPS_CLEEVE_SPLIT)
def test_both_new_bishops_cleeve_sites_resolve_real_evidence(site_id) -> None:
    """THE ACCEPTANCE TEST. Rename either key and this fails; that is the whole point of it.

    Starts from the site_id alone and goes through `_invoice_evidence_for`, the same call
    `_gate_3b` makes on every production run. An absence record here means Gate 3b never runs for
    this site and its confidence is capped at LOW for a reason that is a filing error, not a fact
    about the site.
    """
    evidence = _invoice_evidence_for(site_id)

    assert evidence.invoice_available is True, (
        f"{site_id} resolved an ABSENCE record: {evidence.absence_reason}. The invoice evidence is "
        "keyed by a name Gate 3b does not look for."
    )
    assert evidence.site_id == site_id
    assert evidence.absence_reason is None

    expected_kwh, expected_gbp = BISHOPS_CLEEVE_SPLIT[site_id]
    assert evidence.invoiced_kwh == pytest.approx(expected_kwh, abs=0.05)
    assert evidence.invoiced_cost_gbp == pytest.approx(expected_gbp, abs=0.01)
    assert len(evidence.monthly_cost_gbp) == 12, "12 complete months, from the meter's own tab"
    assert sum(evidence.monthly_cost_gbp.values()) == pytest.approx(expected_gbp, abs=0.05)


def test_every_live_site_id_resolves_evidence_through_the_production_resolver() -> None:
    """No live site may silently fall back to an absence record."""
    absent = {
        site_id: _invoice_evidence_for(site_id).absence_reason
        for site_id in LIVE_SITE_IDS
        if not _invoice_evidence_for(site_id).invoice_available
    }
    assert not absent, f"live sites with no resolvable invoice evidence: {absent}"


def test_the_evidence_keys_match_the_hh_converter_site_ids() -> None:
    """The two files must agree on the site boundary, or the evidence is filed under a dead name.

    `convert_grundon_hh.py` names the sites the metered series are written for; this file names the
    sites the invoices are filed under. They are edited independently, and the split cost a publish
    cycle precisely because they disagreed.
    """
    assert set(_convert_grundon_hh_sites()) == set(LIVE_SITE_IDS)
    assert set(LIVE_SITE_IDS) <= set(_raw()), sorted(set(LIVE_SITE_IDS) - set(_raw()))


def test_the_mapping_parser_also_builds_real_evidence_for_both_new_sites() -> None:
    """The parser limb, for completeness. It cannot catch a key mismatch — the tests above do."""
    raw = _raw()
    for site_id, (kwh, gbp) in BISHOPS_CLEEVE_SPLIT.items():
        evidence = invoice_evidence_from_mapping(site_id, raw[site_id])
        assert evidence.invoice_available is True
        assert evidence.invoiced_kwh == pytest.approx(kwh, abs=0.05)
        assert evidence.invoiced_cost_gbp == pytest.approx(gbp, abs=0.01)


# =============================================================================================
# The split loses nothing, and invents nothing.
# =============================================================================================


def test_the_two_new_sites_do_not_sum_to_the_retired_combined_invoice() -> None:
    """They must NOT, and the shortfall must be recorded rather than forced.

    Three further Bishops Cleeve meters carry 2025 volume and belong to neither new boundary. Making
    the two sites add up to 396,698.7 kWh would mean apportioning another supply's demand onto a
    site that did not incur it.
    """
    assigned_kwh = sum(v[0] for v in BISHOPS_CLEEVE_SPLIT.values())
    assigned_gbp = sum(v[1] for v in BISHOPS_CLEEVE_SPLIT.values())
    assert assigned_kwh < RETIRED_COMBINED[0]
    assert assigned_kwh + UNASSIGNED_RESIDUAL[0] == pytest.approx(RETIRED_COMBINED[0], abs=0.05)
    assert assigned_gbp + UNASSIGNED_RESIDUAL[1] == pytest.approx(RETIRED_COMBINED[1], abs=0.01)

    split = _raw()["_bishops_cleeve_split"]
    assert split["unassigned_2025"]["kwh"] == pytest.approx(UNASSIGNED_RESIDUAL[0], abs=0.05)
    assert split["unassigned_2025"]["cost_gbp"] == pytest.approx(UNASSIGNED_RESIDUAL[1], abs=0.01)
    assert {m["tab"] for m in split["unassigned_2025"]["meters"]} == {
        "BC Import",
        "BC Weighbridge",
        "BC Lodge",
    }
    for meter in split["unassigned_2025"]["meters"]:
        assert meter["reason_unassigned"], f"{meter['tab']} is excluded with no stated reason"
    assert split["confirmation_ask"]


def test_the_unassigned_residual_is_not_smuggled_into_documented_omissions() -> None:
    """A gate-loosening defect, guarded against by name.

    `reconciliation._itemisation` sums `documented_omissions` and uses the total to convert an
    unexplained cost undershoot into an "accounted for" WARNING. £14,707 of OTHER meters' cost
    against Ash Plant's £20,535 invoice would be a 72%-of-invoice cushion for undershoots those
    meters have nothing to do with — and none of it is an omission from either site's modelled
    stack. Each new site's only omission is the Capacity Market supplier charge, which the stack
    genuinely excludes by design.
    """
    for site_id, (kwh, invoiced_gbp) in BISHOPS_CLEEVE_SPLIT.items():
        omissions = _invoice_evidence_for(site_id).documented_omissions
        assert len(omissions) == 1, [o.label for o in omissions]
        (only,) = omissions
        assert "Capacity Market" in only.label
        assert only.kind == "computed"
        # 1.1 p/kWh on this site's OWN invoiced volume — the §6 construction, no other line.
        assert only.gbp == pytest.approx(kwh * 0.011, abs=0.01)
        total = sum(o.gbp for o in omissions)
        assert total < UNASSIGNED_RESIDUAL[1], "the residual has leaked into the itemisation"
        assert total < invoiced_gbp * 0.08, (
            "documented omissions now exceed the −8% WARN bound on their own, which would excuse a "
            "shortfall before one has even been measured"
        )


def test_the_retired_combined_boundary_is_an_explicit_absence_never_a_live_invoice() -> None:
    """A stale SiteModel gets a reasoned absence, never a 396,699 kWh invoice for a dead site."""
    evidence = _invoice_evidence_for(RETIRED_SITE_ID)
    assert evidence.invoice_available is False
    assert evidence.invoiced_kwh is None, "absent, not zero, and certainly not the combined total"
    assert evidence.invoiced_cost_gbp is None
    assert evidence.absence_reason
    assert "RETIRED" in evidence.absence_reason
    # It must name its replacements, or a reader has an absence with nowhere to go.
    for site_id in BISHOPS_CLEEVE_SPLIT:
        assert site_id in evidence.absence_reason


# =============================================================================================
# The sites that did NOT move must not have moved.
# =============================================================================================


@pytest.mark.parametrize(
    ("site_id", "kwh", "gbp"),
    [
        ("grundon_colnbrook", 3_032_360.2, 395_610.72),
        ("grundon_knowl_hill", 45_732.3, 29_214.23),
        ("grundon_ewelme", 694_062.0, 185_862.31),
        ("grundon_tanhouse", 1_541_453.4, 346_973.58),
    ],
)
def test_the_untouched_sites_keep_their_figures(site_id, kwh, gbp) -> None:
    """Colnbrook is explicitly out of scope for the split; the other three were only verified."""
    evidence = _invoice_evidence_for(site_id)
    assert evidence.invoice_available is True
    assert evidence.invoiced_kwh == pytest.approx(kwh, abs=0.05)
    assert evidence.invoiced_cost_gbp == pytest.approx(gbp, abs=0.01)
    assert len(evidence.monthly_cost_gbp) == 12


def test_harwell_still_has_no_invoice_at_all() -> None:
    evidence = _invoice_evidence_for("harwell_campus_k16w304527")
    assert evidence.invoice_available is False
    assert evidence.absence_reason and evidence.invoiced_cost_gbp is None


# =============================================================================================
# Knowl Hill: the invoice is complete for what it is, and what it is must be stated.
# =============================================================================================


def test_knowl_hill_records_that_its_invoice_is_grid_import_only() -> None:
    """Its 45,732.3 kWh is 7.9% of the site's 2025 energy, and the rest carries no invoice.

    The invoice is NOT partial: one grid MPAN, 12/12 months, agreeing with the metered series month
    by month. But the same tab records 531,680.9 kWh of on-site LFG self-supply, and pricing that as
    purchased import is the Tanhouse defect (139,172 kWh of the customer's own solar billed as
    bought energy). The figure stays the grid invoice verbatim and the coverage is declared.
    """
    coverage = _raw()["_knowl_hill_coverage"]
    assert coverage["invoiced_grid_2025"]["kwh"] == pytest.approx(45_732.3, abs=0.05)
    assert coverage["self_supply_2025"]["kwh"] == pytest.approx(531_680.9, abs=0.05)
    assert coverage["self_supply_2025"]["months"] == 12
    # The workbook's own self-supply COST cell is broken, so no combined cost is recorded.
    assert coverage["self_supply_2025"]["cost_gbp"] is None
    assert coverage["self_supply_2025"]["cost_absent_reason"]
    assert coverage["grid_share_of_site_energy_pct"] == pytest.approx(7.9, abs=0.1)
    assert coverage["base_year_warning"] and coverage["confirmation_ask"]

    # And the caveat travels ON the evidence, not only in the metadata block.
    assert "GRID IMPORT ONLY" in _invoice_evidence_for("grundon_knowl_hill").source


# =============================================================================================
# File hygiene.
# =============================================================================================


def test_metadata_blocks_can_never_be_mistaken_for_a_site() -> None:
    """`_`-prefixed keys are documentation. None may ever resolve as a live invoice."""
    for key, entry in _raw().items():
        if not key.startswith("_"):
            continue
        assert not entry.get("invoice_available"), f"{key} reads as a live invoice"
        assert _invoice_evidence_for(key).invoice_available is False


def test_the_gate_reads_the_file_this_test_reads() -> None:
    """Guards the one assumption every test above rests on."""
    assert _INVOICE_EVIDENCE_PATH.resolve() == INVOICES.resolve()
