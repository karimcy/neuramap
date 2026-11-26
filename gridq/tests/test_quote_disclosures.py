"""What a record owes its reader when it disagrees with itself, or claims more than it evidences.

Two blockers from the 2026-08-13 adversarial review, both of them a pack *claiming* something rather
than a pack getting a number wrong:

**Colnbrook.** ``site.connection.anm_or_curtailment_notes`` asserted "CONSEQUENCE:
import_headroom_kw = 0, so sizing is capacity-blocked — a DATA-BLOCKED result, not an economic one",
reproduced only inside a chart caption roughly 28% into the document, downstream of a headline
presenting an **economic** verdict. Meanwhile ``sizing_grid.selection_reason`` appeared nowhere
at all (grep: 0 hits for ``selection_reason``, 0 for "no bankable candidate"). So the record held
two incompatible accounts of why there was no specification, and the pack surfaced one prominently
and buried the other.

**Tanhouse.** A ``BLOCKING`` site data ask — "explain the 13.13958 p/kWh non-commodity residual,
3.2x Colnbrook's 4.13135 on the same private wire, with no supporting evidence", the largest
avoidable-cost line at the site — never reached the reader: the record carried only the ask *count*.
And ``site.missing_data`` held hand-authored asks while the pack's exhibit showed a smaller
recomputed generic list, presented as if one list. Separately the verdict asserted a per-candidate
return comparison that no persisted figure evidences.

The authored ask count is read from the record rather than restated here, because the number moves
whenever the site's evidence does — the metered rebuild took Tanhouse from 8 asks to 9. What must
not move is that **each** authored ask is a separate entry from the recomputed list and neither
count is presented as the other.

The tests below use the **real site records** for both sites, so they fail if the actual defect data
stops being covered rather than only if a synthetic fixture does. Those records are quarantined
customer data (WP-GRC-05): each is read only through the guard, and every test that needs one
skips, naming its blocker, until the customer's DPA is registered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests._dpa_support import dpa_released

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.quotes.disclosures import (
    DIAGNOSIS_REF,
    ask_list_disclosure,
    blocking_ask_disclosures,
    capacity_conflict_disclosure,
    confidence_capping_disclosures,
    record_disclosures,
    sizing_verdict_disclosure,
    unevidenced_return_disclosure,
)

REAL_SITES = Path(__file__).resolve().parents[1] / "examples" / "real" / "sites"

#: The economic account the sizing search actually records when nothing clears the covenant
#: (`gridq.sizing.sizer._select`) — the string the packs had zero hits for.
ECONOMIC_REASON = (
    "no bankable candidate: no size meets the P90 DSCR covenant (DSCR is unavailable without "
    "injected debt terms and a real dispatch, or every candidate fails the covenant)."
)


def _site(name: str) -> s.SiteModel:
    """The real record ``name``, once its DPA is registered (WP-GRC-05); until then, a skip."""
    return s.SiteModel.model_validate_json(dpa_released(REAL_SITES / f"{name}.json").read_text())


def _no_spec_result() -> s.OptimisationResult:
    """A run that evaluated candidates and specified nothing — the six packs' shape."""
    return s.OptimisationResult(
        result_id="res_test",
        request_id="req_test",
        implementation_level=s.ImplementationLevel.PRODUCTION,
        bankable_status=s.BankableStatus.NOT_BANKABLE,
        solver_status=s.SolverStatus.OPTIMAL,
        selection_status=s.SelectionStatus.NO_VIABLE_CONFIGURATION,
    )


def _grid(
    *,
    full_series: int = 24,
    proxy: int = 127,
    reason: str = ECONOMIC_REASON,
    irr: float | None = None,
    levered: float | None = None,
) -> s.SizingGrid:
    """A grid shaped like the real ones: proxy scoring solves plus full-series priced candidates.

    ``irr``/``levered`` default to ``None`` because that is what the real runs record — every
    candidate at all six sites carried a null project IRR and a null levered return
    (docs/grundon_harwell/07_sizing_diagnosis.md §3).
    """
    points = [
        s.SizingGridPoint(
            power_kw=float(100 + i),
            duration_hours=2.0,
            energy_kwh=float(200 + i),
            project_irr_pct=irr,
            levered_return_pct=levered,
            priceable=True,
            bankable=False,
            solver_status=s.SolverStatus.OPTIMAL,
            proxy=False,
        )
        for i in range(full_series)
    ] + [
        s.SizingGridPoint(
            power_kw=float(50 + i),
            duration_hours=1.0,
            energy_kwh=float(50 + i),
            priceable=True,
            bankable=False,
            solver_status=s.SolverStatus.OPTIMAL,
            proxy=True,
        )
        for i in range(proxy)
    ]
    return s.SizingGrid(
        search_space_size=len(points),
        bankable=False,
        selection_reason=reason,
        points=points,
        selected=None,
        selection_basis="none",
    )


# --- blocker 2: the record's two accounts of its own verdict -------------------------------------


def test_colnbrook_conflict_states_both_accounts_and_which_is_superseded() -> None:
    """Both accounts reach the reader, the conflict is named, and neither field is deleted."""
    site = _site("colnbrook")
    assert site.connection.import_headroom_kw == 0.0  # fixture sanity: the stale note's premise
    disclosure = capacity_conflict_disclosure(site, _no_spec_result(), _grid())
    assert disclosure is not None
    text = disclosure.text

    # Account (1): the site note, quoted in full rather than summarised or paraphrased.
    assert site.connection.anm_or_curtailment_notes in text
    assert "DATA-BLOCKED" in text and "capacity-blocked" in text
    # Account (2): the run's own sizing verdict, verbatim — the string that had 0 hits in the pack.
    assert ECONOMIC_REASON in text
    assert "no bankable candidate" in text
    # The conflict is stated as a conflict, not resolved silently by preferring one.
    assert "two incompatible accounts" in text
    assert "SUPERSEDED" in text
    # ...with the evidence, and the reason the stale field is kept rather than removed.
    assert "24 candidate(s) were dispatch-solved and priced" in text
    assert "not read anywhere on the pricing path" in text
    assert DIAGNOSIS_REF in text
    assert "left on the record rather than deleted" in text


def test_the_conflict_is_detected_from_structure_not_from_the_note_s_wording() -> None:
    """Rewording the note must not switch the disclosure off — detection is on the typed field."""
    site = _site("colnbrook")
    site.connection.anm_or_curtailment_notes = "nothing alarming here at all"
    disclosure = capacity_conflict_disclosure(site, _no_spec_result(), _grid())
    assert disclosure is not None
    assert "nothing alarming here at all" in disclosure.text


def test_no_conflict_claimed_when_the_record_does_not_actually_refute_the_note() -> None:
    """With no priced candidate, nothing contradicts the note, so nothing is called superseded.

    Asserting "superseded" off a search that never ran would be exactly the sort of unevidenced
    claim this work exists to remove.
    """
    site = _site("colnbrook")
    assert capacity_conflict_disclosure(site, _no_spec_result(), _grid(full_series=0)) is None
    assert capacity_conflict_disclosure(site, _no_spec_result(), None) is None


def test_a_site_with_headroom_raises_no_conflict() -> None:
    """Narrow firing: the disclosure is about a zero-headroom note, not about every no-spec run."""
    site = _site("bishops_cleeve_mrf")
    assert site.connection.import_headroom_kw not in (0, 0.0)  # fixture sanity
    assert capacity_conflict_disclosure(site, _no_spec_result(), _grid()) is None


def test_selection_reason_is_surfaced_verbatim_on_a_no_spec_run() -> None:
    """The field the packs never printed. Stated in the record's own words, not summarised."""
    disclosure = sizing_verdict_disclosure(_no_spec_result(), _grid())
    assert disclosure is not None
    assert ECONOMIC_REASON in disclosure.text
    assert "sizing_grid.selection_reason" in disclosure.text
    assert "sizing_grid.selection_basis: none" in disclosure.text
    assert "151 candidate(s)" in disclosure.text


def test_selection_reason_is_not_repeated_when_a_system_was_specified() -> None:
    """On a run that selected a size the reason is noise; the pack already presents the choice."""
    result = _no_spec_result()
    result.selected_power_kw = 165.0
    result.selection_status = s.SelectionStatus.SELECTED
    assert sizing_verdict_disclosure(result, _grid()) is None


# --- blocker 3: blocking asks, two ask lists, an unevidenced verdict claim -----------------------


def test_tanhouse_blocking_ask_is_reproduced_in_full_not_counted() -> None:
    """The 13.13958 p/kWh residual ask, verbatim. A count cannot stand in for it."""
    site = _site("tanhouse")
    # Fixture sanity, on the subject rather than on a total that moves with the site's evidence:
    # exactly one authored ask is marked BLOCKING, and it is the residual one.
    blocking_asks = [a for a in site.missing_data if a.startswith("BLOCKING")]
    assert len(blocking_asks) == 1 and "13.13958" in blocking_asks[0]

    disclosures = blocking_ask_disclosures(site)
    assert disclosures, "the BLOCKING ask on the real Tanhouse record was not disclosed"
    blocking = [d for d in disclosures if "13.13958" in d.text]
    assert len(blocking) == 1
    text = blocking[0].text
    assert "non-commodity residual" in text
    assert "site.missing_data[" in text  # says exactly which entry it is quoting
    # Every blocking ask caps the band, which is what carries it beside the verdict.
    assert all(d.caps_confidence for d in disclosures)


def test_a_blocking_ask_cannot_be_dropped_by_the_record_s_warning_aggregation() -> None:
    """End-to-end on the real record: the ask's own words reach `QuoteRecord.warnings`.

    This is the guard for "a BLOCKING ask cannot be silently dropped". Before it, assembly emitted
    only the "N ranked data ask(s) outstanding" summary and the ask itself existed nowhere in the
    pack.
    """
    from gridq.assumptions import load_assumptions
    from gridq.quotes.assembly import assemble_quote_record

    site = _site("tanhouse")
    quote = assemble_quote_record(
        site,
        _no_spec_result(),
        None,
        load_assumptions("gb_v0_1"),
        sizing_grid=_grid(),
    )
    joined = "\n".join(quote.warnings)
    assert "13.13958" in joined
    assert "BLOCKING DATA ASK" in joined
    # The count survives as a summary, but it no longer stands in for the asks it counts. The
    # expected count is read off the record rather than hard-coded, so this also catches assembly
    # counting some OTHER list — the recomputed generic one — under the authored list's name.
    assert f"{len(site.missing_data)} ranked data ask(s) outstanding" in joined


def test_the_two_ask_lists_are_stated_as_two_lists() -> None:
    """Authored vs recomputed: different provenance, so neither count is presented as both."""
    site = _site("tanhouse")
    authored = len(site.missing_data)
    recomputed = authored - 1
    disclosure = ask_list_disclosure(site, recomputed_count=recomputed)
    assert disclosure is not None
    assert f"{authored} ask(s) hand-authored" in disclosure.text
    assert f"{recomputed} " in disclosure.text and "recompute" in disclosure.text
    assert "not the same list" in disclosure.text
    assert "neither supersedes the other" in disclosure.text
    # Nothing to separate when the two lists agree, or when the detector could not run.
    assert ask_list_disclosure(site, recomputed_count=authored) is None
    assert ask_list_disclosure(site, recomputed_count=None) is None


def test_a_return_comparison_with_no_persisted_return_is_disclosed() -> None:
    """Every candidate records a null IRR and a null levered return, so nothing was compared."""
    disclosure = unevidenced_return_disclosure(_no_spec_result(), _grid())
    assert disclosure is not None
    text = disclosure.text
    assert "project_irr_pct = null and levered_return_pct = null" in text
    assert "24 full-series candidate(s)" in text
    # It states what the record CAN support instead of just what it cannot.
    assert "COMPUTABLE return" in text
    assert DIAGNOSIS_REF in text
    assert disclosure.caps_confidence


def test_no_unevidenced_claim_when_the_grid_carries_a_return() -> None:
    """Narrow firing: with a real IRR on the record the comparison is evidenced."""
    assert unevidenced_return_disclosure(_no_spec_result(), _grid(irr=-4.2)) is None
    assert unevidenced_return_disclosure(_no_spec_result(), _grid(levered=1.5)) is None


def test_proxy_candidates_are_never_counted_as_evidence() -> None:
    """Proxy solves guide the search and were never priced evidence, so they cannot support a claim.

    A grid of proxy points only is a grid with nothing priced: no conflict is asserted and no
    unevidenced-claim disclosure is raised off it.
    """
    grid = _grid(full_series=0, proxy=127)
    assert unevidenced_return_disclosure(_no_spec_result(), grid) is None
    assert capacity_conflict_disclosure(_site("colnbrook"), _no_spec_result(), grid) is None


# --- the aggregate, and the ceiling that carries it beside the verdict ---------------------------


def test_record_disclosures_orders_evidence_problems_before_commentary() -> None:
    site = _site("colnbrook")
    disclosures = record_disclosures(site, _no_spec_result(), _grid(), recomputed_ask_count=3)
    codes = [d.code for d in disclosures]
    assert codes[0] == "RECORD_CONFLICT"
    assert "UNEVIDENCED_RETURN" in codes
    assert codes.index("UNEVIDENCED_RETURN") < codes.index("SIZING_VERDICT")
    assert codes[-1] == "ASK_LISTS_DIFFER"


def test_only_evidence_problems_cap_the_confidence_band() -> None:
    """Restating a verdict, or separating two ask lists, is disclosure — not weaker evidence."""
    site = _site("colnbrook")
    disclosures = record_disclosures(site, _no_spec_result(), _grid(), recomputed_ask_count=3)
    capping = {d.code for d in confidence_capping_disclosures(disclosures)}
    # Colnbrook's own record also carries a BLOCKING ask (the EfW private-wire contract terms).
    assert capping == {"RECORD_CONFLICT", "UNEVIDENCED_RETURN", "BLOCKING_ASK"}
    assert "SIZING_VERDICT" not in capping
    assert "ASK_LISTS_DIFFER" not in capping


def test_a_capping_disclosure_lands_in_the_binding_factors_beside_the_verdict() -> None:
    """The pack prints the binding factors under the verdict; that is how a disclosure lands."""
    from gridq.assumptions import load_assumptions
    from gridq.quotes.assembly import assemble_quote_record
    from gridq.quotes.confidence import assess_confidence

    site = _site("colnbrook")
    quote = assemble_quote_record(
        site, _no_spec_result(), None, load_assumptions("gb_v0_1"), sizing_grid=_grid()
    )
    assessment = assess_confidence(site, quote)
    factor = next(f for f in assessment.factors if f.name == "record disclosures")
    assert factor.ceiling is s.ConfidenceBand.LOW
    assert "RECORD_CONFLICT" in factor.reason
    assert factor in assessment.binding_factors


def test_a_clean_record_reports_no_disclosures_and_caps_nothing() -> None:
    """The factor must be silent on a healthy run: one that always fires is not a signal."""
    from gridq.assumptions import load_assumptions
    from gridq.quotes.assembly import assemble_quote_record
    from gridq.quotes.confidence import assess_confidence

    site = _site("bishops_cleeve_mrf")
    site.missing_data = []
    result = _no_spec_result()
    result.selected_power_kw = 375.0
    result.selection_status = s.SelectionStatus.SELECTED
    quote = assemble_quote_record(site, result, None, load_assumptions("gb_v0_1"))

    assert record_disclosures(site, result, None) == []
    factor = next(
        f for f in assess_confidence(site, quote).factors if f.name == "record disclosures"
    )
    assert factor.ceiling is s.ConfidenceBand.HIGH


def test_no_disclosure_hard_codes_a_site_measurement() -> None:
    """No detector may carry a site-specific figure in its executable code.

    The measured evidence (Colnbrook's median 706.6 kW of charge headroom, 98.6% of half-hours above
    50 kW, the cashflow cause of the absent IRR) is **cited** to the diagnosis document rather than
    copied in, deliberately: a site constant baked into a generic detector would be the same
    unsourced-figure defect appearing in a new place, and it would silently misdescribe the next
    site. Docstrings are excluded — they narrate the review findings, which is what they are for;
    only code that can reach a reader's page is checked.
    """
    import ast

    import gridq.quotes.disclosures as module

    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for invented in ("706.6", "706.646", "98.6", "13.13958", "4.13135", "915.077", "451.863"):
        assert invented not in code, f"{invented} is hard-coded into a detector"


def test_every_figure_in_a_disclosure_is_a_record_count_or_a_citation() -> None:
    """A disclosure's own numbers come from the record it describes, or from a cited document."""
    site = _site("colnbrook")
    grid = _grid()
    for disclosure in record_disclosures(site, _no_spec_result(), grid, recomputed_ask_count=3):
        text = disclosure.text
        # Counts are of the record's own candidates/asks; everything else is a verbatim quote of a
        # record field or a pointer at the diagnosis document.
        assert (
            f"{len(grid.points)}" in text
            or "24 " in text
            or DIAGNOSIS_REF in text
            or "site.missing_data[" in text
            or "hand-authored" in text
        ), text[:200]


def test_the_stale_field_is_still_on_the_real_record() -> None:
    """The resolution is disclosure, not deletion. If the note ever disappears, this fails loudly.

    Deleting a record field that disagrees with the result is how a record stops being evidence, so
    the fix must not have quietly removed it from the site file.
    """
    raw = json.loads(dpa_released(REAL_SITES / "colnbrook.json").read_text())
    assert "DATA-BLOCKED" in raw["connection"]["anm_or_curtailment_notes"]
    assert raw["connection"]["import_headroom_kw"] == 0.0


@pytest.mark.parametrize("name", sorted(p.stem for p in REAL_SITES.glob("*.json")))
def test_disclosures_never_raise_on_a_real_site_record(name: str) -> None:
    """Every real record must pass through the detectors without an exception, grid or no grid.

    Discovered from the directory rather than listed, so a site added to the portfolio is covered
    the moment its file lands. A hand-written list is how the two Bishops Cleeve supplies and the
    two invoice-anchored bases would have gone unexercised here.
    """
    site = _site(name)
    for grid in (None, _grid(), _grid(full_series=0)):
        record_disclosures(site, _no_spec_result(), grid, recomputed_ask_count=7)


# --- SYNTHETIC twins: the detectors keep running while the real records are quarantined ---------
#
# The tests above read the real records on purpose and skip until the customers' DPAs are
# registered (WP-GRC-05). These twins carry the same STRUCTURE (a zero-headroom connection note
# beside a priced search; a BLOCKING authored ask; a connection with headroom) in their own
# SYNTHETIC words and figures, so the detectors stay exercised on every run in the meantime.

_TWIN_NOTE = (
    "SYNTHETIC: CONSEQUENCE: import_headroom_kw = 0, so sizing is capacity-blocked - a "
    "DATA-BLOCKED result, not an economic one"
)
_TWIN_BLOCKING = "BLOCKING: SYNTHETIC ask - explain the 9.87654 p/kWh non-commodity residual"


def _twin(
    *, headroom_kw: float, note: str | None = None, asks: list[str] | None = None
) -> s.SiteModel:
    return s.SiteModel(
        site_id="SYNTHETIC-disclosures",
        site_name="SYNTHETIC disclosure twin (not a real site)",
        postcode="ZZ1 1AA",
        data_tier=s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        load_data=s.LoadDataSummary(annual_kwh=400_000.0),
        annual_consumption_kwh=400_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="SYNTHETIC",
            import_rates={"single": 0.24},
        ),
        connection=s.ConnectionDetails(
            mic_kw=250.0, import_headroom_kw=headroom_kw, anm_or_curtailment_notes=note
        ),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="SYNTHETIC Operator A Ltd (not a real company)"),
        missing_data=asks if asks is not None else [_TWIN_BLOCKING, "SYNTHETIC ask 2"],
    )


def test_twin_conflict_states_both_accounts_detected_from_structure() -> None:
    site = _twin(headroom_kw=0.0, note=_TWIN_NOTE)
    disclosure = capacity_conflict_disclosure(site, _no_spec_result(), _grid())
    assert disclosure is not None
    assert _TWIN_NOTE in disclosure.text and ECONOMIC_REASON in disclosure.text
    assert "two incompatible accounts" in disclosure.text and "SUPERSEDED" in disclosure.text
    reworded = _twin(headroom_kw=0.0, note="nothing alarming here at all")
    assert capacity_conflict_disclosure(reworded, _no_spec_result(), _grid()) is not None
    # Nothing priced refutes the note, and a connection with headroom has no note to refute.
    assert capacity_conflict_disclosure(site, _no_spec_result(), _grid(full_series=0)) is None
    assert capacity_conflict_disclosure(site, _no_spec_result(), None) is None
    roomy = _twin(headroom_kw=120.0, note=_TWIN_NOTE)
    assert capacity_conflict_disclosure(roomy, _no_spec_result(), _grid()) is None


def test_twin_blocking_ask_is_reproduced_verbatim_and_reaches_the_warnings() -> None:
    from gridq.assumptions import load_assumptions
    from gridq.quotes.assembly import assemble_quote_record

    site = _twin(headroom_kw=120.0)
    disclosures = blocking_ask_disclosures(site)
    assert len(disclosures) == 1 and "9.87654" in disclosures[0].text
    assert "site.missing_data[" in disclosures[0].text and disclosures[0].caps_confidence
    quote = assemble_quote_record(
        site, _no_spec_result(), None, load_assumptions("gb_v0_1"), sizing_grid=_grid()
    )
    joined = "\n".join(quote.warnings)
    assert "9.87654" in joined and "BLOCKING DATA ASK" in joined
    assert f"{len(site.missing_data)} ranked data ask(s) outstanding" in joined
    lists = ask_list_disclosure(site, recomputed_count=1)
    assert lists is not None and "not the same list" in lists.text
    assert ask_list_disclosure(site, recomputed_count=2) is None


def test_twin_ordering_capping_and_the_binding_factor() -> None:
    from gridq.assumptions import load_assumptions
    from gridq.quotes.assembly import assemble_quote_record
    from gridq.quotes.confidence import assess_confidence

    site = _twin(headroom_kw=0.0, note=_TWIN_NOTE)
    disclosures = record_disclosures(site, _no_spec_result(), _grid(), recomputed_ask_count=1)
    codes = [d.code for d in disclosures]
    assert codes[0] == "RECORD_CONFLICT" and codes[-1] == "ASK_LISTS_DIFFER"
    assert codes.index("UNEVIDENCED_RETURN") < codes.index("SIZING_VERDICT")
    capping = {d.code for d in confidence_capping_disclosures(disclosures)}
    assert capping == {"RECORD_CONFLICT", "UNEVIDENCED_RETURN", "BLOCKING_ASK"}
    quote = assemble_quote_record(
        site, _no_spec_result(), None, load_assumptions("gb_v0_1"), sizing_grid=_grid()
    )
    assessment = assess_confidence(site, quote)
    factor = next(f for f in assessment.factors if f.name == "record disclosures")
    assert factor.ceiling is s.ConfidenceBand.LOW and factor in assessment.binding_factors

    clean = _twin(headroom_kw=120.0, asks=[])
    result = _no_spec_result()
    result.selected_power_kw = 375.0
    result.selection_status = s.SelectionStatus.SELECTED
    assert record_disclosures(clean, result, None) == []
    quote = assemble_quote_record(clean, result, None, load_assumptions("gb_v0_1"))
    factor = next(
        f for f in assess_confidence(clean, quote).factors if f.name == "record disclosures"
    )
    assert factor.ceiling is s.ConfidenceBand.HIGH
