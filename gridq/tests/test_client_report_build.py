"""The two client feasibility reports: what they may say, and what they may never draw.

`scripts/build_client_report.py` renders the Grundon estate report and the Harwell report from
published run records. It is a RENDERER, and this suite pins the three properties that make that
claim true rather than aspirational.

1. **No figure is authored.** Every headline number on the page is checked back to the run record
   it came from. A renderer that quietly computed one would fail here.
2. **Nothing is drawn between two solved candidates.** The three dimensional sizing figure is the
   one place a smooth interpolated skin would be tempting, and it is exactly the place where a
   fabricated figure would be invisible to a reader. The mark count is pinned to the number of
   candidates that actually returned a project return, and the figure is checked to contain no
   filled data surface at all.
3. **The study basis is stated and the invoice anchored twins stay out.** A D57 study figure that
   loses its basis banner reads as an underwriting output, and the two invoice anchored runs share
   a `site_id` with their metered twin, so one document carrying both would put two different
   answers under one site name.

Also pinned: zero em and en dashes anywhere in either document, which is house style for anything
a client reads, and which is enforced at the document seam because the site names themselves carry
them.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "build_client_report.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_client_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


@pytest.fixture(scope="module")
def sites(builder):
    runs = builder.GRUNDON + builder.HARWELL
    missing = [rid for rid, _ in runs if not (REPO / "runs" / rid / "quote.json").exists()]
    if missing:
        pytest.skip(f"published run records absent: {missing}")
    return [builder.load(rid, name) for rid, name in runs]


@pytest.fixture(scope="module")
def documents(builder, sites):
    return {"grundon": builder.build_grundon(), "harwell": builder.build_harwell()}


def test_no_em_or_en_dashes_reach_a_client_document(documents):
    """House style, enforced at the seam: the site names carry them, so content rules miss them."""
    for name, html in documents.items():
        for ch in ("—", "–", "−"):
            assert ch not in html, f"{name} carries {ch!r}"


def test_every_headline_figure_traces_to_the_run_record(builder, sites, documents):
    """No authored numbers.

    The check is made against the KPI block ITSELF, not against the whole document. Searching the
    page is too weak: the revenue total also appears in the donut centre and the table footer, so
    an inflated KPI would still find its correct twin somewhere on the page and pass.
    """
    for site in sites:
        block = builder.kpis(site)
        for value in (
            builder.gbp(site.revenue_total),
            builder.pct(site.irr_pct),
            builder.gbp(site.capex_gbp),
            f"{builder.num(site.power_kw, 1)} kW",
            f"{builder.num(site.payback_years, 1)} yr",
        ):
            assert builder.no_dashes(value) in block, f"{site.display}: {value} not in the KPIs"
        # and the whole document carries them too, so the KPI block is not orphaned
        html = documents["harwell" if site.run_id in dict(builder.HARWELL) else "grundon"]
        assert builder.no_dashes(builder.gbp(site.capex_gbp)) in html


def test_revenue_total_is_the_plain_sum_of_the_records_own_streams(sites):
    for site in sites:
        expected = sum(float(v["annual_revenue_gbp"] or 0.0) for v in site.streams)
        assert site.revenue_total == pytest.approx(expected)


def test_sizing_figure_draws_one_mark_per_solved_candidate_and_nothing_between(builder, sites):
    """The integrity property of the three dimensional figure.

    Stems are the only elements stroked in a data colour, so counting them counts candidates. If a
    future edit adds an interpolated mesh, the fill assertion fails; if it drops or duplicates
    candidates, the count assertion fails.
    """
    data_colours = set(builder.RAMP) | {"#aeb8c2"}
    for site in sites:
        svg = builder.fig_sizing_3d(site)
        scored = [p for p in site.grid["points"] if p.get("project_irr_pct") is not None]
        stems = [
            m
            for m in re.findall(r'<line [^>]*stroke="(#[0-9a-fA-F]{6})"', svg)
            if m in data_colours
        ]
        assert len(stems) == len(scored), (
            f"{site.display}: {len(stems)} stems drawn, {len(scored)} candidates solved"
        )
        fills = re.findall(r'<path [^>]*fill="([^"]+)"', svg)
        assert all(f == "none" for f in fills), f"{site.display} draws a filled data surface"


def test_the_study_basis_is_stated_on_both_documents(documents):
    for name, html in documents.items():
        assert "all_streams_enabled_study_v1" in html, name
        assert "not an underwriting output" in html, name
        assert "assumed approved" in html, name


def test_invoice_anchored_runs_never_appear(builder, documents):
    """They share a site_id with their metered twin, so mixing bases would collide identities."""
    excluded = {"quote_run_21c5c376037ddbff", "quote_run_2d72303cb5c1b1fe"}
    assert not excluded & {rid for rid, _ in builder.GRUNDON + builder.HARWELL}
    for name, html in documents.items():
        for rid in excluded:
            assert rid not in html, f"{name} carries the invoice anchored run {rid}"
    # The phrase is allowed exactly once, in the note that says those results live elsewhere.
    assert documents["grundon"].count("invoice anchored") == 1
    assert "published separately" in documents["grundon"]
    assert "invoice anchored" not in documents["harwell"]


def test_grundon_carries_five_metered_sites_and_harwell_carries_one(builder, documents):
    for _, name in builder.GRUNDON:
        assert name in documents["grundon"], name
    assert "Harwell Campus" in documents["harwell"]
    assert len(builder.GRUNDON) == 5
