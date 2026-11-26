"""The report's drawn exhibits: geometry from the record, and nothing authored.

`gridq.outputs.report_charts` exists so `consulting_report` can stay arithmetic-free while its
exhibits are still drawn to scale. That split is only worth having if the chart module is held to
the same honesty standard the report module is held to, so these pin:

* **no displayed figure is authored**: every numeric run in every SVG text node traces to a value
  the record carries, formatted through `gridq.outputs.formatting`;
* **holes stay holes**: the sizing map draws one cell per real solve and nothing between them, and
  never reads an unmeasured covenant as a failure;
* **capacity is never implied**: on a site whose MIC is really its own peak, no capacity rule is
  drawn and the exhibit says why;
* **absence is an exhibit**: a missing record projection renders a framed, reasoned unavailable
  rather than a blank, a zero or an illustration;
* **the rendered document carries no em dash or en dash**, including in the record text the report
  reproduces verbatim, while the record itself keeps its original strings;
* the exhibits are deterministic and self-contained (no script, no external asset, no namespace
  declaration, so the report stays one file).
"""

from __future__ import annotations

import re
from html import escape

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.outputs import formatting as fmt
from gridq.outputs import generate_artifacts, render_report_for_quote
from gridq.outputs import report_charts as charts
from gridq.outputs.consulting_report import ConfidenceView
from gridq.outputs.viewmodel import build_view_model
from gridq.quotes.confidence import assess_confidence

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

_TEXT_NODE = re.compile(r"<text[^>]*>(.*?)</text>", re.DOTALL)
#: A maximal run of digits with the separators a formatted figure may contain.
_NUMERIC_RUN = re.compile(r"[0-9][0-9,.]*")


# --- fixtures: the persisted engine projections the drawn exhibits need ----------------------


def _dispatch_profile() -> s.DispatchProfile:
    """A deterministic representative day: overnight charge, evening red-band discharge."""
    points: list[s.DispatchProfilePoint] = []
    # Starts and stays within the recorded usable capacity (500 kWh), as a real solved day must.
    soe = 50.0
    for period in range(48):
        hour = period / 2.0
        red = 16.0 <= hour < 19.0
        charge = 90.0 if hour < 5.0 else 0.0
        # Below the recorded load, so the exhibit shows the battery covering part of the site's
        # demand: the reading the reference figure's "from the battery" block is making.
        discharge = 150.0 if red else 0.0
        if charge > 0.0:
            soe = soe + 45.0
        if discharge > 0.0:
            soe = max(0.0, soe - 125.0)
        points.append(
            s.DispatchProfilePoint(
                period=period,
                charge_kw=charge,
                discharge_kw=discharge,
                soe_kwh=soe,
                site_load_kw=210.0 if red else 160.0,
                duos_rate_gbp_per_kwh=0.18 if red else 0.02,
                red_band=red,
            )
        )
    return s.DispatchProfile(
        power_kw=250.0,
        energy_kwh=500.0,
        duration_hours=2.0,
        interval_hours=0.5,
        representative_day_index=118,
        selection_basis="highest total discharge",
        points=points,
    )


def _sizing_grid(
    *,
    covenant_flag: bool | None = True,
    selection_basis: str = "covenant",
    with_hole: bool = True,
) -> s.SizingGrid:
    """A 3x2 lattice with one deliberate hole, plus a confirm-stage point off the lattice."""
    spec = [
        (200.0, 1.0, 6.2),
        (200.0, 2.0, 7.4),
        (400.0, 1.0, 8.1),
        (400.0, 2.0, 9.6),
        (600.0, 1.0, 5.3),
        (600.0, 2.0, None if with_hole else 4.1),
    ]
    points = [
        s.SizingGridPoint(
            power_kw=power,
            duration_hours=duration,
            energy_kwh=power * duration,
            levered_return_pct=z,
            project_irr_pct=None if z is None else z,
            min_p90_dscr=None if covenant_flag is None else 1.42,
            passes_covenant=covenant_flag,
            priceable=True,
            bankable=False,
            solver_status=s.SolverStatus.OPTIMAL,
            stage="grid",
        )
        for power, duration, z in spec
    ]
    points.append(
        s.SizingGridPoint(
            power_kw=330.0,
            duration_hours=1.5,
            energy_kwh=495.0,
            levered_return_pct=9.9,
            project_irr_pct=9.9,
            min_p90_dscr=None if covenant_flag is None else 1.55,
            passes_covenant=covenant_flag,
            priceable=True,
            bankable=False,
            solver_status=s.SolverStatus.OPTIMAL,
            stage="confirm",
        )
    )
    return s.SizingGrid(
        search_space_size=7,
        covenant=1.30,
        bankable=False,
        selection_reason="best levered return among the covenant-feasible candidates",
        points=points,
        selected=points[3],
        selection_basis=selection_basis,
        search_version="m4-two-stage-v2",
    )


def _customer_bill(*, saving_positive: bool = True) -> s.CustomerBill:
    """A bill whose components sum to its totals (the schema validates that)."""
    energy_after = 88_000.0 if saving_positive else 128_000.0
    after_total = energy_after + 21_000.0 + 12_500.0 + 1_500.0 + 8_000.0
    before_total = 100_000.0 + 30_000.0 + 12_000.0 + 1_500.0 + 8_000.0
    return s.CustomerBill(
        annual_bill_before_gbp=before_total,
        annual_bill_after_gbp=after_total,
        annual_saving_gbp=before_total - after_total,
        energy_before_gbp=100_000.0,
        energy_after_gbp=energy_after,
        duos_before_gbp=30_000.0,
        duos_after_gbp=21_000.0,
        levies_before_gbp=12_000.0,
        levies_after_gbp=12_500.0,
        standing_gbp=1_500.0,
        capacity_gbp=8_000.0,
        import_before_kwh=1_800_000.0,
        import_after_kwh=1_850_000.0,
        levy_rate_gbp_per_kwh=0.01,
        peak_import_before_kw=420.0,
        peak_import_after_kw=430.0,
        basis="Energy, DUoS and levies from the site's own tariff and the solved dispatch. TNUoS "
        "residual excluded; standing and capacity charges carried identically on both sides.",
    )


@pytest.fixture
def drawn(rich_quote):
    """The rich record with all three persisted projections attached, so the exhibits can draw."""
    return rich_quote.quote.model_copy(
        update={
            "dispatch_profile": _dispatch_profile(),
            "sizing_grid": _sizing_grid(),
            "customer_bill": _customer_bill(),
        }
    )


def _capacity_unsupplied_site(site: s.SiteModel) -> s.SiteModel:
    """A site in the portfolio's capacity-blocked state: MIC set to the peak, ASC absent."""
    return site.model_copy(
        update={
            "connection": s.ConnectionDetails(
                mic_kw=420.0,
                asc_kw=None,
                import_headroom_kw=0.0,
                anm_or_curtailment_notes="NO CONNECTION CAPACITY HAS BEEN SUPPLIED FOR THIS SITE.",
            )
        }
    )


def _text_nodes(svg: str) -> list[str]:
    return [body for body in _TEXT_NODE.findall(svg)]


def _flat_text(svg: str) -> str:
    """Every text node joined back into flowing prose.

    SVG text does not reflow, so a caption is emitted as one node per wrapped line. Joining on a
    space reconstructs the sentence exactly (the wrap splits on whitespace), which is what a reader
    sees and therefore what a prose assertion should read.
    """
    return " ".join(_text_nodes(svg))


def _numeric_runs(text: str) -> set[str]:
    return {match.group(0).strip(".,") for match in _NUMERIC_RUN.finditer(text)}


# --- the central invariant: no displayed figure is authored ---------------------------------


def _allowed_runs(quote: s.QuoteRecord, site: s.SiteModel) -> set[str]:
    """Every numeric run any formatting of a record value could legitimately produce.

    Built from the record rather than hand-written, so the test cannot drift into blessing an
    invented figure: a run that appears in an SVG and not here came from somewhere other than this
    record. Clock labels are pure geometry (a half-hour index), and integers up to the interval
    count are counts of recorded points, so both are allowed explicitly.
    """
    values: list[float] = [0.0, site.connection.mic_kw]
    for optional in (
        site.connection.asc_kw,
        site.connection.mec_kw,
        site.load_data.annual_kwh,
        site.load_data.max_import_kw,
    ):
        if optional is not None:
            values.append(optional)
    profile = quote.dispatch_profile
    if profile is not None:
        values.extend([profile.power_kw, profile.energy_kwh, profile.duration_hours])
        for point in profile.points:
            values.extend([point.charge_kw, point.discharge_kw, point.soe_kwh, point.site_load_kw])
    grid = quote.sizing_grid
    if grid is not None:
        values.append(grid.covenant if grid.covenant is not None else 0.0)
        for candidate in grid.points:
            values.extend([candidate.power_kw, candidate.duration_hours, candidate.energy_kwh])
            for metric in (
                candidate.levered_return_pct,
                candidate.project_irr_pct,
                candidate.min_p90_dscr,
            ):
                if metric is not None:
                    values.append(metric)
    bill = quote.customer_bill
    if bill is not None:
        values.extend(
            [
                bill.annual_bill_before_gbp,
                bill.annual_bill_after_gbp,
                bill.annual_saving_gbp,
                bill.energy_before_gbp,
                bill.energy_after_gbp,
                bill.duos_before_gbp,
                bill.duos_after_gbp,
                bill.levies_before_gbp,
                bill.levies_after_gbp,
                bill.standing_gbp,
                bill.capacity_gbp,
                bill.import_before_kwh,
                bill.import_after_kwh,
            ]
        )
    spec = build_view_model(quote, site).spec
    for optional_spec in (spec.selected_power_kw, spec.selected_energy_kwh, spec.duration_hours):
        if optional_spec is not None:
            values.append(optional_spec)

    allowed: set[str] = set()
    for value in values:
        for formatted in (
            fmt.kw(value),
            fmt.kwh(value),
            fmt.hours(value),
            fmt.pct(value),
            fmt.gbp(value),
            fmt.gbp_compact(value),
            fmt.ratio(value),
        ):
            allowed |= _numeric_runs(formatted)
    # Clock labels and counts of recorded points: geometry, not figures.
    interval_count = 0 if profile is None else len(profile.points)
    for period in range(interval_count + 1):
        allowed |= _numeric_runs(charts._clock(period, 0.5))
        allowed.add(str(period))
    if grid is not None:
        allowed.add(str(grid.search_space_size))
        allowed.add(str(len(grid.points)))
        for count in range(len(grid.points) + 1):
            allowed.add(str(count))
    return allowed


def test_every_number_drawn_in_an_svg_traces_to_a_record_value(drawn, rich_quote) -> None:
    """The chart module's central rule: it may scale and position, never author a figure."""
    html = render_report_for_quote(drawn, rich_quote.site, assumptions=load_assumptions("gb_v0_1"))
    allowed = _allowed_runs(drawn, rich_quote.site)
    offenders: list[str] = []
    for node in _text_nodes(html):
        for run in _numeric_runs(node):
            if run not in allowed:
                offenders.append(f"{run!r} in SVG text {node!r}")
    assert not offenders, "SVG text carries a figure the record does not:\n" + "\n".join(offenders)


def test_each_exhibit_shows_the_record_field_it_cites(drawn, rich_quote) -> None:
    """The positive direction: the values the exhibits promise really are the record's."""
    profile = drawn.dispatch_profile
    bill = drawn.customer_bill
    grid = drawn.sizing_grid
    assert profile is not None and bill is not None and grid is not None

    firming = charts.firming_diagram(profile=profile, site=rich_quote.site)
    assert fmt.kwh(profile.energy_kwh) in firming  # the usable-capacity rule
    assert fmt.kw(rich_quote.site.connection.mic_kw) in firming  # the recorded capacity rule
    assert "00:00" in firming and "24:00" in firming  # the shared clock axis

    columns = charts.bill_columns(bill=build_view_model(drawn, rich_quote.site).bill_savings)
    for component in (
        bill.energy_before_gbp,
        bill.energy_after_gbp,
        bill.duos_before_gbp,
        bill.levies_after_gbp,
        bill.annual_saving_gbp,
    ):
        assert fmt.gbp(component) in columns

    sizing = charts.sizing_map(grid=grid)
    for candidate in grid.points:
        if candidate.levered_return_pct is not None and candidate.stage == "grid":
            assert fmt.pct(candidate.levered_return_pct) in sizing


# --- the sizing map: one cell per real solve ------------------------------------------------


def test_sizing_map_leaves_an_unevaluated_cell_as_a_hole() -> None:
    """No interpolation, no iso-lines: an unsolved size is drawn as not evaluated."""
    grid = _sizing_grid(with_hole=True)
    svg = charts.sizing_map(grid=grid)
    assert "not evaluated" in _flat_text(svg)
    # The hole is the only dashed cell rect in the lattice; a filled cell would assert a return.
    assert svg.count('stroke-dasharray="2 3"') >= 1
    absent = [p for p in grid.points if p.levered_return_pct is None]
    assert absent, "the fixture must carry an unevaluated lattice cell"


def test_sizing_map_never_reads_an_unmeasured_covenant_as_a_failure() -> None:
    """``passes_covenant is None`` is the common state on this portfolio, and it is not a fail."""
    svg = charts.sizing_map(grid=_sizing_grid(covenant_flag=None))
    flat = _flat_text(svg)
    assert "debt feasibility not measured" in flat
    assert "no debt structure on the record" in flat
    assert "FAIL" not in flat


def test_sizing_map_captions_an_indicative_fallback_as_a_recommendation() -> None:
    svg = charts.sizing_map(grid=_sizing_grid(selection_basis="indicative_fallback"))
    assert "a recommendation, not a feasible optimum" in _flat_text(svg)


def test_sizing_map_names_the_binding_constraint_when_the_record_does() -> None:
    svg = charts.sizing_map(
        grid=_sizing_grid(), constraints_binding=("max_import_kw", "throughput_kwh")
    )
    assert "The selected size was limited by: max_import_kw, throughput_kwh" in _flat_text(svg)


def test_sizing_map_falls_back_to_a_table_when_no_point_carries_a_return() -> None:
    """A map with nothing to colour is a decorated blank; the evaluated points are the truth."""
    grid = _sizing_grid()
    stripped = grid.model_copy(
        update={
            "points": [
                p.model_copy(update={"levered_return_pct": None, "project_irr_pct": None})
                for p in grid.points
            ]
        }
    )
    out = charts.sizing_map(grid=stripped)
    assert "<svg" not in out
    assert "<table" in out
    assert fmt.kw(grid.points[0].power_kw) in out


# --- the firming diagram --------------------------------------------------------------------


def test_firming_diagram_draws_the_solved_series_and_says_what_is_missing(rich_quote) -> None:
    svg = charts.firming_diagram(profile=_dispatch_profile(), site=rich_quote.site)
    assert "<polyline" in svg  # the load line and the state-of-energy trace
    assert "<polygon" in svg  # the battery and recharge areas
    flat = _flat_text(svg)
    assert "From the battery" in flat and "Recharge" in flat and "Site load" in flat
    assert "Grid import series not recorded on this run" in flat
    assert "no reserve rule is drawn" in flat
    assert "Representative day (highest total discharge)" in flat
    assert "DUoS red band" in flat
    assert "Charging 10 of 48 half-hours" in flat


def test_firming_diagram_draws_no_capacity_rule_when_none_was_supplied(rich_quote) -> None:
    """A MIC that is really the site's own peak is not a ceiling, so no rule is drawn from it."""
    site = _capacity_unsupplied_site(rich_quote.site)
    svg = charts.firming_diagram(profile=_dispatch_profile(), site=site)
    assert "Connection capacity unknown" in _flat_text(svg)
    assert ">MIC<" not in svg
    assert fmt.kw(site.connection.mic_kw) not in svg


def test_firming_diagram_without_a_profile_states_the_missing_projection() -> None:
    svg = charts.firming_diagram(
        profile=None, site=None, solver_note="The dispatch was not solved."
    )
    flat = _flat_text(svg)
    assert "unavailable (no representative dispatch day is persisted on this record" in flat
    assert "The dispatch was not solved." in flat
    assert "<polyline" not in svg


# --- the bill exhibit -----------------------------------------------------------------------


def test_bill_columns_keep_the_two_fixed_lines_as_separate_recorded_values(drawn, rich_quote):
    """Their total is not a figure the record carries, so it is never displayed."""
    view = build_view_model(drawn, rich_quote.site).bill_savings
    svg = charts.bill_columns(bill=view)
    assert view.standing_gbp is not None and view.capacity_gbp is not None
    assert fmt.gbp(view.standing_gbp) in svg
    assert fmt.gbp(view.capacity_gbp) in svg
    assert fmt.gbp(view.standing_gbp + view.capacity_gbp) not in svg
    assert "fixed: unchanged by the battery" in _flat_text(svg)


def test_bill_columns_present_a_rising_bill_as_a_result(rich_quote) -> None:
    """The schema represents a negative saving; the exhibit does not hide it."""
    quote = rich_quote.quote.model_copy(
        update={"customer_bill": _customer_bill(saving_positive=False)}
    )
    view = build_view_model(quote, rich_quote.site).bill_savings
    svg = charts.bill_columns(bill=view)
    assert view.annual_savings_gbp is not None and view.annual_savings_gbp < 0.0
    flat = _flat_text(svg)
    assert "The bill rises under this dispatch" in flat
    assert "losses are bought at the price faced" in flat


def test_bill_columns_without_a_bill_keep_the_pending_seam(rich_quote) -> None:
    view = build_view_model(rich_quote.quote, rich_quote.site).bill_savings
    assert not view.available
    svg = charts.bill_columns(bill=view)
    assert "unavailable (Bill before/after and savings need the customer-bill model" in _flat_text(
        svg
    )


# --- the system diagram and the load-duration curve -----------------------------------------


def test_system_diagram_marks_an_unconfirmed_boundary_and_an_unrecorded_export_path(rich_quote):
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    assert rich_quote.site.supply_boundary.behind_the_meter is None
    svg = charts.system_diagram(site=rich_quote.site, spec=vm.spec, export_stream_on_record=False)
    flat = _flat_text(svg)
    assert "Boundary unconfirmed" in flat
    assert "export path: no arrangement recorded" in flat
    assert "MPAN not recorded" in flat
    assert "not recorded" in flat  # the absent ASC / MEC chips
    assert fmt.kw(rich_quote.site.connection.mic_kw) in flat


def test_system_diagram_draws_an_unsized_record_as_an_empty_battery_block(rich_quote) -> None:
    quote = rich_quote.quote.model_copy(update={"optimisation_result": None})
    vm = build_view_model(quote, rich_quote.site)
    svg = charts.system_diagram(site=rich_quote.site, spec=vm.spec, export_stream_on_record=False)
    assert "no system selected on this record" in _flat_text(svg)


def test_load_duration_curve_states_the_projection_it_needs(rich_quote) -> None:
    """Engineering-gated on E-2: the year exists only as a file reference on the site record."""
    svg = charts.load_duration_curve(site=rich_quote.site)
    flat = _flat_text(svg)
    assert "no load-duration projection" in flat
    assert "file reference" in flat
    assert "<polygon" not in svg  # never an illustrated curve


def test_a_capacity_blocked_site_is_never_shown_unused_capacity(rich_quote) -> None:
    site = _capacity_unsupplied_site(rich_quote.site)
    assert not charts.capacity_is_supplied(site)
    curve = _flat_text(charts.load_duration_curve(site=site))
    assert "Connection capacity unknown" in curve
    assert "no unused capacity is implied" in curve


# --- absence, determinism, self-containment -------------------------------------------------


def test_every_exhibit_degrades_to_a_framed_reasoned_unavailable(rich_quote) -> None:
    """A minimal record renders each exhibit as a stated absence: never blank, never zero."""
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    for svg in (
        charts.firming_diagram(profile=None, site=None),
        charts.sizing_map(grid=None),
        charts.load_duration_curve(site=rich_quote.site),
        charts.bill_columns(bill=vm.bill_savings),
    ):
        assert "unavailable (" in _flat_text(svg)
        assert "<polyline" not in svg
        assert ">0.0<" not in svg
        assert ">0<" not in svg


def test_exhibits_are_deterministic(drawn, rich_quote) -> None:
    first = render_report_for_quote(drawn, rich_quote.site)
    second = render_report_for_quote(drawn, rich_quote.site)
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


def test_exhibits_are_self_contained(drawn, rich_quote) -> None:
    """One file: no script, no external asset, no namespace declaration, no remote font."""
    html = render_report_for_quote(drawn, rich_quote.site)
    assert html.count("<svg") >= 5
    for forbidden in ("http://", "https://", "<script", "<link", "@import", "xmlns", "url("):
        assert forbidden not in html


# --- no em dash or en dash reaches the reader ------------------------------------------------


def test_rendered_report_carries_no_em_or_en_dash_including_record_text(drawn, rich_quote) -> None:
    """The check the requirement actually needs: the record's own warnings are swept too.

    The existing package sweep clears ``warnings`` before rendering, because they are authored in
    `gridq.finance` / `gridq.assumptions` and reproduced verbatim. The client requirement is
    absolute, so this renders **with** them, and with a confidence band whose factor reasons carry
    a dash of their own.
    """
    band_source = assess_confidence(rich_quote.site, rich_quote.quote)
    assert drawn.warnings, "the fixture must carry the record's own warnings"
    assert any(EM_DASH in warning for warning in drawn.warnings), (
        "the fixture's warnings must contain an em dash for this test to mean anything"
    )
    assert any(EM_DASH in f.reason for f in band_source.binding_factors)

    html = render_report_for_quote(
        drawn,
        rich_quote.site,
        assumptions=load_assumptions("gb_v0_1"),
        confidence=ConfidenceView.from_assessment(band_source, ceiling="medium"),
    )
    assert EM_DASH not in html
    assert EN_DASH not in html
    # and the text really did render, dash rewritten rather than dropped
    assert "Projection horizon defaulted to 15 years" in html
    assert "confirm the horizon." in html
    dashed_reason = next(f.reason for f in band_source.binding_factors if EM_DASH in f.reason)
    assert escape(fmt.no_dashes(dashed_reason)) in html


def test_the_run_record_keeps_its_own_dashes_after_rendering(drawn, rich_quote) -> None:
    """The rewrite happens on the way out. Nothing upstream is scrubbed."""
    before = tuple(drawn.warnings)
    render_report_for_quote(drawn, rich_quote.site)
    assert tuple(drawn.warnings) == before
    assert any(EM_DASH in warning for warning in drawn.warnings)


def test_which_artifacts_are_dash_rewritten_is_a_recorded_decision(drawn, rich_quote) -> None:
    """ALL FOUR are rewritten. The decision is pinned rather than left to be discovered.

    **This test used to record the opposite split, and D59 reversed it.** The underwriting pack and
    the combined HTML report were exempt so that the run's warnings appeared byte-for-byte; the
    persisted campaign packs carried 100 and 101 em dashes as a result. The owner has now instructed
    three times that no report may carry one, and that instruction outranks a typographic exemption.

    The verbatim guarantee is unchanged and is asserted here: every dash-bearing warning still
    appears in the memo and the HTML report IN FULL, with nothing but the two dash characters
    rewritten. The
    record itself is never touched, which is what makes the rewrite a display decision rather than a
    change to the evidence.
    """
    artifacts = generate_artifacts(drawn, rich_quote.site, assumptions=load_assumptions("gb_v0_1"))
    dashed = [w for w in drawn.warnings if EM_DASH in w]
    assert dashed, "the fixture must carry a warning with an em dash"

    for name, doc in (
        ("consulting report", artifacts.consulting_report_html),
        ("customer proposal", artifacts.proposal_markdown),
        ("underwriting pack", artifacts.memo_markdown),
        ("HTML report", artifacts.report_html),
    ):
        assert EM_DASH not in doc, f"{name} must carry no em dash"
        assert EN_DASH not in doc, f"{name} must carry no en dash"

    # ...and the warning is still there in full, in the engine's own words, dash rewritten.
    assert fmt.no_dashes(dashed[0]) in artifacts.memo_markdown
    assert escape(fmt.no_dashes(dashed[0])) in artifacts.report_html
    # The record is untouched: the rewrite happens on the way to the reader, never on the evidence.
    assert EM_DASH in dashed[0]


def test_no_dashes_rewrites_asides_as_commas_and_ranges_as_the_word_to() -> None:
    assert fmt.no_dashes(f"a name {EM_DASH} a reason") == "a name, a reason"
    assert fmt.no_dashes(f"BASIS {EM_DASH} PRE-TAX {EM_DASH} no tax") == "BASIS, PRE-TAX, no tax"
    assert fmt.no_dashes(f"typically 1{EN_DASH}2 years") == "typically 1 to 2 years"
    assert fmt.no_dashes(f"before{EM_DASH}after") == "before, after"
    assert fmt.no_dashes("nothing to rewrite") == "nothing to rewrite"


# --- the copy-throughs the exhibits depend on ------------------------------------------------


def test_view_model_carries_the_levered_return(rich_quote) -> None:
    """E-5: the record's equity IRR used to be dropped, so no artifact could ever show it."""
    case = rich_quote.case.model_copy(update={"equity_irr": 14.25})
    quote = rich_quote.quote.model_copy(update={"financial_case": case})
    view = build_view_model(quote, rich_quote.site).returns
    assert view.equity_irr_pct == 14.25
    assert view.irr_pct == rich_quote.case.irr


def test_view_model_carries_the_binding_constraints(rich_quote) -> None:
    """E-6: whether the chosen size sits against a constraint is one line of caption truth."""
    result = rich_quote.result.model_copy(update={"constraints_binding": ["max_import_kw"]})
    quote = rich_quote.quote.model_copy(update={"optimisation_result": result})
    assert build_view_model(quote, rich_quote.site).spec.constraints_binding == ("max_import_kw",)
    assert build_view_model(rich_quote.quote, rich_quote.site).spec.constraints_binding == ()


def test_view_model_carries_the_bill_decomposition(drawn, rich_quote) -> None:
    """E-4: totals answer 'how much'; the components answer 'where the change comes from'."""
    bill = drawn.customer_bill
    view = build_view_model(drawn, rich_quote.site).bill_savings
    assert bill is not None
    assert view.energy_before_gbp == bill.energy_before_gbp
    assert view.duos_after_gbp == bill.duos_after_gbp
    assert view.levies_before_gbp == bill.levies_before_gbp
    assert view.standing_gbp == bill.standing_gbp
    assert view.capacity_gbp == bill.capacity_gbp
    assert view.import_after_kwh == bill.import_after_kwh


def test_the_binding_constraints_reach_the_spec_panel_and_the_sizing_map(drawn, rich_quote) -> None:
    result = rich_quote.result.model_copy(update={"constraints_binding": ["max_import_kw"]})
    quote = drawn.model_copy(update={"optimisation_result": result})
    html = render_report_for_quote(quote, rich_quote.site)
    assert "Binding constraints" in html
    assert "The selected size was limited by: max_import_kw" in html


# --- the report is produced by a run, not by hand --------------------------------------------


def test_a_resolved_run_produces_the_consulting_report(drawn, rich_quote) -> None:
    """The deliverable follows the validation: generating artifacts renders the report too."""
    artifacts = generate_artifacts(drawn, rich_quote.site, assumptions=load_assumptions("gb_v0_1"))
    assert artifacts.consulting_report_html.startswith("<!doctype html>")
    assert artifacts.consulting_report_html.rstrip().endswith("</html>")
    assert "<svg" in artifacts.consulting_report_html
    # the ranked data asks are built from the site, so section VI is populated rather than seamed
    assert "no ranked data ask was supplied with this run" not in artifacts.consulting_report_html


def test_the_report_is_produced_without_a_site_and_says_what_is_missing(drawn) -> None:
    artifacts = generate_artifacts(drawn)
    html = artifacts.consulting_report_html
    assert html.startswith("<!doctype html>")
    assert "no site model was supplied alongside the run record" in html
    assert "no ranked data ask was supplied with this run" in html
