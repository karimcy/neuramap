"""A reader with a calculator must not find a row wrong, and a column of nothing is not evidence.

An adversarial review of the six site reports found four display defects, all of them in the
evaluated-points table the sizing map falls back to and in the panel that states the selected size:

1. nine of 24 rows showed a product that did not multiply (``22.6 kW`` by ``0.5 h`` printed beside
   ``12.0 kWh``), because a duration derived as energy over power was rounded to one decimal while
   its own energy was rounded independently;
2. two row pairs were indistinguishable as displayed while carrying different energies, which reads
   as duplicated data, from the same cause;
3. a 151-row table carried 302 cells that all said the same nothing ("unavailable", "not measured"),
   and presented 127 typical-day proxy scores as the equal of 24 full-series solves;
4. a fixed sentence announced three answers above a table whose length came from the record.

These pin the properties rather than the copy: the row reconciles, the rows are distinguishable, an
all-absent column is one sentence instead, the two evidence bases are separated with their counts,
and any count in prose comes from the data it counts.
"""

from __future__ import annotations

import re

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.ingestion.improvement_asks import build_ranked_data_asks
from gridq.outputs import formatting as fmt
from gridq.outputs import report_charts as charts
from gridq.outputs.consulting_report import render_consulting_report, render_report_for_quote
from gridq.outputs.viewmodel import build_view_model

_TABLE = re.compile(r'<table class="grid"><tr><th>Power</th>.*?</table>', re.DOTALL)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_CELL = re.compile(r"<t[dh]>(.*?)</t[dh]>", re.DOTALL)


def _point(
    power: float,
    duration: float,
    *,
    dscr: float | None = None,
    passes: bool | None = None,
    stage: str = "grid",
    proxy: bool = False,
    z: float | None = None,
) -> s.SizingGridPoint:
    """One evaluated candidate, energy derived as `gridq.sizing.search_space` derives it."""
    return s.SizingGridPoint(
        power_kw=power,
        duration_hours=duration,
        energy_kwh=round(power * duration, 6),
        levered_return_pct=z,
        project_irr_pct=z,
        min_p90_dscr=dscr,
        passes_covenant=passes,
        priceable=False,
        bankable=False,
        solver_status=s.SolverStatus.OPTIMAL,
        stage=stage,
        proxy=proxy,
    )


def _grid(points: list[s.SizingGridPoint], **kwargs) -> s.SizingGrid:
    return s.SizingGrid(
        search_space_size=len(points),
        covenant=kwargs.pop("covenant", None),
        bankable=False,
        selection_reason="no candidate met the covenant; the best available return was chosen",
        points=points,
        selected=points[0],
        selection_basis="indicative_fallback",
        search_version="m4-two-stage-v2",
        **kwargs,
    )


def _defective_grid() -> s.SizingGrid:
    """The shape the review was reading: an exact lattice plus a derived-duration proxy cloud.

    The powers are the portfolio's own (a peak and its halves), the lattice durations are the
    search's, and the proxy durations are energy over power, which is where 0.5283 h comes from. The
    first proxy point is the pair the review found indistinguishable: the same power as a lattice
    row and a duration that also prints "0.5 h" at one decimal, carrying a different energy.
    """
    powers = (45.75, 22.875, 91.5, 48.0)
    lattice = [
        _point(power, duration)
        for power in powers
        for duration in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 8.0)
    ]
    proxy = [_point(45.75, 24.17 / 45.75, stage="coarse", proxy=True)]
    proxy.extend(
        _point(
            powers[index % len(powers)],
            0.5 + (index * 0.4234567) % 7.4,
            stage="coarse",
            proxy=True,
        )
        for index in range(1, 128)
    )
    return _grid([*lattice, *proxy])


def _tables(markup: str) -> list[list[list[str]]]:
    """Every evaluated-points table as rows of cells (the header row included)."""
    return [[_CELL.findall(row) for row in _ROW.findall(table)] for table in _TABLE.findall(markup)]


def _number(cell: str) -> float:
    return float(cell.split()[0].replace(",", ""))


def _decimals(cell: str) -> int:
    token = cell.split()[0]
    return len(token.split(".")[1]) if "." in token else 0


# --- defect 1: the row must multiply ---------------------------------------------------------


def test_every_row_of_the_evaluated_points_table_multiplies() -> None:
    """The calculator check, on the strings the reader sees. This table failed 9 rows in 24."""
    markup = charts.sizing_map(grid=_defective_grid())
    tables = _tables(markup)
    assert tables, "the fallback table must render"
    checked = 0
    for rows in tables:
        for cells in rows[1:]:
            power, duration, energy = cells[0], cells[1], cells[2]
            product = f"{_number(power) * _number(duration):,.{_decimals(energy)}f}"
            assert product == energy.split(" kWh")[0], (
                f"{power} times {duration} does not give {energy}"
            )
            checked += 1
    assert checked == len(_defective_grid().points)


def test_the_same_row_of_the_old_report_is_no_longer_wrong() -> None:
    """The review's own example: 45.75 kW at a derived 0.5283 h beside its recorded 24.17 kWh."""
    grid = _grid([_point(45.75, 24.17 / 45.75), _point(45.75, 0.5)])
    rows = _tables(charts.sizing_map(grid=grid))[0][1:]
    for cells in rows:
        product = f"{_number(cells[0]) * _number(cells[1]):,.{_decimals(cells[2])}f}"
        assert product == cells[2].split(" kWh")[0]
    # It is not fixed by dropping a column: all three are still shown.
    assert [cell for cell in _tables(charts.sizing_map(grid=grid))[0][0]][:3] == [
        "Power",
        "Duration",
        "Energy",
    ]


def test_a_table_of_exact_sizes_keeps_the_precision_it_already_had() -> None:
    """Precision is bought only where the data needs it, so a round lattice reads as before."""
    grid = _grid([_point(200.0, 1.0), _point(200.0, 2.0), _point(400.0, 2.0)])
    markup = charts.sizing_map(grid=grid)
    for point in grid.points:
        assert fmt.kw(point.power_kw) in markup
        assert fmt.hours(point.duration_hours) in markup


def test_the_chosen_precision_is_either_exact_or_bounded_at_one_last_place() -> None:
    """Exactness is not always reachable, so the residual is bounded and the table says which."""
    for durations in ((0.5283060109289618,), (1.1666618, 5.1999927), (0.13114754098360656,)):
        triples = [
            (power, duration, round(power * duration, 6))
            for power in (91.5, 22.875)
            for duration in durations
        ]
        precision = charts._precision_for(triples)
        for power, duration, energy in triples:
            shown_power = float(f"{power:.{precision.power_dp}f}")
            shown_duration = float(f"{duration:.{precision.duration_dp}f}")
            shown_energy = float(f"{energy:.{precision.energy_dp}f}")
            gap = abs(shown_power * shown_duration - shown_energy)
            assert gap <= pow(10.0, -precision.energy_dp) + 1e-9


# --- defect 2: two rows are never indistinguishable ------------------------------------------


def test_two_rows_never_show_one_size_with_two_energies() -> None:
    """A repeated (power, duration) pair carrying different energies reads as duplicated data."""
    markup = charts.sizing_map(grid=_defective_grid())
    seen: dict[tuple[str, str], set[str]] = {}
    for rows in _tables(markup):
        for cells in rows[1:]:
            seen.setdefault((cells[0], cells[1]), set()).add(cells[2])
    collisions = {key: values for key, values in seen.items() if len(values) > 1}
    assert not collisions, f"rows indistinguishable as displayed: {collisions}"


# --- defect 3: a column of nothing, and two evidence bases -----------------------------------


def test_a_column_that_says_nothing_on_every_row_becomes_one_sentence() -> None:
    """302 dead cells before: the honest note names the missing input once."""
    markup = charts.sizing_map(grid=_defective_grid())
    header = _tables(markup)[0][0]
    assert "Min P90 DSCR" not in header
    assert "Covenant" not in header
    assert "no P90 DSCR on this evaluation" not in markup
    assert "neither column is drawn" in markup
    assert "no debt structure on the record" in markup
    # The words appear once, in the note that replaced the two columns, not on every row.
    assert markup.count("not measured") == 1
    assert "<td>not measured</td>" not in markup


def test_the_debt_columns_stay_wherever_a_row_carries_a_debt_measure() -> None:
    """Dropping them is conditional on every row being absent, never a blanket simplification."""
    grid = _grid(
        [
            _point(200.0, 1.0, dscr=1.42, passes=True),
            _point(200.0, 2.0),
            _point(400.0, 2.0, dscr=1.05, passes=False),
        ],
        covenant=1.3,
    )
    markup = charts.sizing_map(grid=grid)
    header = _tables(markup)[0][0]
    assert "Min P90 DSCR" in header and "Covenant" in header
    assert fmt.ratio(1.42) in markup and "FAIL" in markup
    # A single unmeasured row is still labelled, never coerced to a failure.
    assert "not measured" in markup


def test_a_proxy_score_is_never_presented_as_a_full_series_solve() -> None:
    """127 of 151 rows were proxy-evaluated and sat in the same table as the 24 that were not."""
    grid = _defective_grid()
    markup = charts.sizing_map(grid=grid)
    tables = _tables(markup)
    assert len(tables) == 2, "the two evidence bases are separate blocks"
    full_series = [point for point in grid.points if not point.proxy]
    proxied = [point for point in grid.points if point.proxy]
    assert f"Evaluated on the full series: {fmt.count(len(full_series))}" in markup
    assert f"Evaluated on a typical-day proxy: {fmt.count(len(proxied))}" in markup
    assert len(tables[0]) == len(full_series) + 1
    assert len(tables[1]) == len(proxied) + 1
    assert "never a reported figure" in markup
    assert "not equivalent evidence" in markup
    # Every evaluation is still on the page: nothing is dropped to shorten the table.
    assert len(tables[0]) + len(tables[1]) == len(grid.points) + 2


def test_a_grid_of_only_full_series_solves_still_names_its_basis() -> None:
    grid = _grid([_point(200.0, 1.0), _point(200.0, 2.0)])
    markup = charts.sizing_map(grid=grid)
    assert "Evaluated on the full series: 2" in markup
    assert "typical-day proxy" not in markup


def test_the_evaluated_points_table_is_deterministic() -> None:
    grid = _defective_grid()
    assert charts.sizing_map(grid=grid) == charts.sizing_map(grid=grid)


# --- the same claim in the spec panel --------------------------------------------------------


def test_the_selected_system_panel_multiplies(rich_quote) -> None:
    """Power, energy and duration state the same product one row apiece instead of one column."""
    power, energy, duration = charts.spec_triple(45.75, round(45.75 * 0.5283, 6), 0.5283)
    assert power is not None and energy is not None and duration is not None
    product = f"{_number(power) * _number(duration):,.{_decimals(energy)}f}"
    assert product == energy.split(" kWh")[0]
    # A round size keeps the precision the rest of the pack uses.
    assert charts.spec_triple(500.0, 1000.0, 2.0) == (
        fmt.kw(500.0),
        fmt.kwh(1000.0),
        fmt.hours(2.0),
    )
    # An absent figure stays absent for the caller to label.
    assert charts.spec_triple(None, None, None) == (None, None, None)


def test_the_panel_and_its_finding_cite_the_same_size(rich_quote) -> None:
    """One exhibit may not print two different renderings of the selected power."""
    vm = build_view_model(rich_quote.quote, rich_quote.site)
    assert vm.spec.selected_power_kw is not None
    html = render_consulting_report(vm, site=rich_quote.site)
    power, energy, _ = charts.spec_triple(
        vm.spec.selected_power_kw, vm.spec.selected_energy_kwh, vm.spec.duration_hours
    )
    assert power is not None and energy is not None
    assert f"a {power} / {energy} system" in html or f"is {power} / {energy}" in html


# --- defect 4: a count in prose comes from the data it counts --------------------------------


def test_the_ranked_ask_count_is_generated_from_the_record(rich_quote) -> None:
    """ "Three answers" was fixed prose above a table the record sized."""
    asks = build_ranked_data_asks(rich_quote.site)
    assert asks, "the fixture site must raise ranked asks"
    html = render_report_for_quote(
        rich_quote.quote,
        rich_quote.site,
        assumptions=load_assumptions("gb_v0_1"),
        data_asks=asks,
    )
    assert "Three answers" not in html
    assert f"The record ranks {fmt.count_word(len(asks))}, in the order they pay back." in html
    # The count really tracks the data: half the asks, half the word.
    fewer = asks[:2]
    html = render_report_for_quote(
        rich_quote.quote,
        rich_quote.site,
        assumptions=load_assumptions("gb_v0_1"),
        data_asks=fewer,
    )
    assert "The record ranks two, in the order they pay back." in html


def test_no_ranked_ask_makes_no_count_claim(rich_quote) -> None:
    html = render_report_for_quote(rich_quote.quote, rich_quote.site)
    assert "in the order they pay back" not in html
    assert "This run carries no ranked answer to it." in html


@pytest.mark.parametrize(
    ("number", "expected"),
    [(0, "no"), (1, "one"), (7, "seven"), (12, "twelve"), (13, "13"), (1500, "1,500")],
)
def test_a_count_reads_as_a_word_until_it_is_better_as_a_numeral(
    number: int, expected: str
) -> None:
    assert fmt.count_word(number) == expected
