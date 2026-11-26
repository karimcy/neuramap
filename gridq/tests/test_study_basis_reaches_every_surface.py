"""Every surface states the pricing basis, in the basis's OWN words, at the TOP (**D46/D55**).

This file asserts on the RENDERED BYTES of every artifact and every screen, for all three states a
record can be in, because that is the only place the defect it closes was ever visible.

**The measured defect (2026-08-18, reproduced on a real bishops_cleeve_mrf record).**
`outputs/viewmodel.py` detected the basis by matching the literal ``"STUDY BASIS (D46)"`` against
`QuoteRecord.warnings`. `AllStreamsEnabledStudyBasis.LABEL` begins ``"STUDY BASIS (D55)"``, so on
the basis that prices EVERY revenue stream into the priced floor the match failed and
``vm.study_basis`` came back False. All four renderers gate on that flag, so all four dropped the
basis:

* the customer proposal's "Feasibility study, not an offer" banner was ABSENT and the document
  called the run "the underwriting run" three times;
* the pack titled "# GridQ: Underwriting pack" carried no basis banner, and its only statement of
  basis was one bullet at line 193 of 219, under "## Warnings";
* the consulting report and the HTML report lost their banners too;
* and the frontend stated the basis on NO screen, on EITHER basis.

Direction of effect: UP, and in the worst possible direction. The run that prices the whole barred
grid stack into the floor was the one presented as an underwriting output.

**What is pinned here, and why each assertion is shaped the way it is.**

1. **Detection is structural, not textual.** `QuoteRecord.study_basis_id` is read first. The test
   that discriminates the two designs is the ID-ONLY record: `quotes.assembly.assemble_quote_record`
   called with ``study_basis=`` persists the id and writes NO prose note (the note is appended by
   `quotes.pipeline.generate_quote`), so such a record was invisible to every renderer under
   prose-only detection and is detected here. A test built only from `generate_quote` records
   CANNOT tell the two designs apart, because those carry both the id and the note.
2. **The legacy fallback survives.** A record persisted before the field existed carries
   ``study_basis_id=None`` even when a study basis priced it. Reading the id alone would re-create
   the original defect on every archived pack, so the NOTE-ONLY record is pinned too, on D55
   specifically, because D55 is the basis the old literal missed.
3. **Position, not mere presence.** Presence was never the whole defect: the D55 memo did state its
   basis, at line 193 of 219 under "## Warnings". Each assertion therefore compares INDEXES, so a
   banner that regresses back into a footnote fails even though the string is still in the file.
4. **A study pack must not call itself an underwriting run**, and an underwriting pack must not
   claim a basis it does not have. Both directions are asserted; a detector stuck ON is as wrong as
   one stuck off.

Every record is built IN this file. ``runs/`` is gitignored, so a test that loaded a real run
directory would pass VACUOUSLY on a fresh checkout (`docs/validation_requirements.md` §3).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

# Pinned before `gridq.app` is imported: the store reads its directory from the environment, and a
# frontend assertion here must never read or write the developer's real run store.
MODULE_STORE = tempfile.mkdtemp(prefix="gridq-study-basis-surface-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

from fastapi.testclient import TestClient  # noqa: E402
from tests._dpa_support import register_fixture_dpas  # noqa: E402
from tests.test_all_streams_study_basis import all_streams  # noqa: E402
from tests.test_study_basis_schema import study  # noqa: E402

from gridq.app import create_app, store  # noqa: E402
from gridq.assumptions import AssumptionLibrary, latest_version, load_assumptions  # noqa: E402
from gridq.engine.baseline import BaselineEngine  # noqa: E402
from gridq.governance.dpa_guard import authorise_store  # noqa: E402
from gridq.outputs.consulting_report import render_consulting_report  # noqa: E402
from gridq.outputs.html_report import render_html_report  # noqa: E402
from gridq.outputs.proposal import render_customer_proposal  # noqa: E402
from gridq.outputs.underwriting import render_underwriting_memo  # noqa: E402
from gridq.outputs.viewmodel import (  # noqa: E402
    _STUDY_BASIS_UNNAMED_LABEL,
    OutputViewModel,
    build_view_model,
    detect_study_basis,
)
from gridq.quotes.assembly import assemble_quote_record  # noqa: E402
from gridq.quotes.pipeline import generate_quote  # noqa: E402
from gridq.schemas import (  # noqa: E402
    QuoteRecord,
    SiteModel,
    SizingGrid,
    SizingGridPoint,
    StudyBasis,
)

#: Resolved from disk, never pinned: MD-F-15's rule is that a superseded release is never pinned
#: "to be safe", and nothing here depends on a release's values.
RELEASE = latest_version()


@pytest.fixture(scope="module")
def lib() -> AssumptionLibrary:
    return load_assumptions(RELEASE)


@pytest.fixture(autouse=True)
def _pin_store(tmp_path_factory: pytest.TempPathFactory) -> None:
    """A FRESH store per test, re-pinned because `store.run_store_dir` reads the env lazily.

    Per test rather than per module on purpose: the Projects listing shows every visible run and
    the dashboard hero reads the newest, so a run left behind by an earlier test in this file
    would make "an underwriting run claims no basis" fail against a study run it never saved.
    Other modules write this variable too, hence re-pinning per test rather than once at import.
    """
    os.environ["GRIDQ_RUN_STORE"] = str(tmp_path_factory.mktemp("gridq-study-basis-surface"))


@pytest.fixture(autouse=True)
def _fixture_dpa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TEST FIXTURE DPA for the fictitious ``site_test``, so `_saved` may store (WP-GRC-05)."""
    register_fixture_dpas(monkeypatch, tmp_path / "evidence", "site_test")


class _StudyBasisEngine(BaselineEngine):
    """A `BaselineEngine` that also exposes the duck-typed ``last_study_basis()`` hook.

    The same stub the two sibling study-basis modules use. It exercises `generate_quote`'s REAL
    forwarding logic without paying for a half-hourly MILP solve (10+ minutes a site); `MilpEngine`
    exposes the identical hook, and what is under test here is `outputs` and `app`, downstream of
    both.
    """

    def __init__(self, basis: StudyBasis | None) -> None:
        self._basis = basis

    def last_study_basis(self) -> StudyBasis | None:
        return self._basis


# ------------------------------------------------------ the three record states, all built here


def _pipeline_record(
    basis: StudyBasis | None, site: SiteModel, lib: AssumptionLibrary
) -> QuoteRecord:
    """The production entry point: `generate_quote`. Carries the basis id AND the prose note."""
    return generate_quote(site, lib, engine=_StudyBasisEngine(basis)).quote


def _id_only_record(basis: StudyBasis, site: SiteModel, lib: AssumptionLibrary) -> QuoteRecord:
    """A record that names its basis by ID and carries no prose statement of it.

    Not a contrivance: this is what `assemble_quote_record` produces for ANY caller that is not
    `generate_quote`, because the note is appended by the pipeline and the id is persisted by the
    assembly. The fixture asserts both halves of its own shape, so if the pipeline ever moves the
    note into the assembly this test says so instead of quietly measuring the wrong thing.
    """
    engine = BaselineEngine()
    result = engine.solve(engine.build_optimisation_request(site, lib))
    record = assemble_quote_record(site, result, None, lib, study_basis=basis)
    assert record.study_basis_id == basis.basis_id, "fixture: the id did not persist"
    assert not any(w.startswith("STUDY BASIS (") for w in record.warnings), (
        "fixture: this record is supposed to carry NO prose note, so that it discriminates "
        "id-based detection from prose-based detection; something now writes the note in assembly"
    )
    return record


def _legacy_record(basis: StudyBasis, site: SiteModel, lib: AssumptionLibrary) -> QuoteRecord:
    """A record as persisted BEFORE ``study_basis_id`` existed: the note, and no id.

    Built by round-tripping a real pipeline record through JSON with the key deleted, which is
    exactly the shape a pre-2026-08-18 ``quote.json`` on disk has.
    """
    data = _pipeline_record(basis, site, lib).model_dump(mode="json")
    data.pop("study_basis_id", None)
    record = QuoteRecord.model_validate(data)
    assert record.study_basis_id is None, "fixture: the id should be absent on a legacy record"
    assert any(w.startswith("STUDY BASIS (") for w in record.warnings), (
        "fixture: a legacy record is detectable ONLY by its prose note, so the note must be there"
    )
    return record


def _short(basis: StudyBasis) -> str:
    """The basis's leading phrase, e.g. ``"STUDY BASIS (D55)"``: what every artifact must print.

    Matched on the short form rather than the whole label because two of the four documents pass
    through `formatting.no_dashes`, which rewrites the label's em dashes to commas, so the full
    string is legitimately absent from them. The short form is dash-free and survives all four.
    """
    return basis.LABEL.split(" — ")[0]


def _first_section(text: str) -> int:
    """Index of the first Markdown section heading: where the leading banner region ends."""
    idx = text.find("\n## ")
    assert idx > 0, "no '## ' section heading found; the position assertion cannot be made"
    return idx


# ------------------------------------------------------------ DETECTION: THE THREE STATES


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
def test_a_record_that_names_its_basis_by_id_alone_is_detected(
    basis_factory: Callable[[], StudyBasis],
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """THE discriminating test. Prose-only detection reports False here; id-first reports True.

    Production call site: ``basis = detect_study_basis(quote)`` in
    `outputs/viewmodel.py::build_view_model`, reading `QuoteRecord.study_basis_id`. Blind that read
    and this test goes red while every `generate_quote`-built test stays green, which is precisely
    why it exists.
    """
    basis = basis_factory()
    record = _id_only_record(basis, make_site(), lib)
    view = detect_study_basis(record)
    assert view.present, "a record naming its basis by id is not detected as a study run"
    assert view.label == basis.LABEL, "the basis was not labelled in its own words"
    assert view.basis_id == basis.basis_id
    assert view.note is None, "this record carries no prose note; the view must not invent one"
    vm = build_view_model(record, None)
    assert vm.study_basis and vm.study_basis_label == basis.LABEL
    assert vm.study_basis_id == basis.basis_id


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
def test_a_legacy_record_with_no_basis_id_is_still_detected_from_its_own_prose(
    basis_factory: Callable[[], StudyBasis],
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """The fallback the id must never replace: ``study_basis_id=None`` is silence, not a denial.

    Every pack archived before 2026-08-18 is this record. Delete the warnings-prefix fallback and
    all of them silently become underwriting outputs, which is the original defect with a longer
    reach.
    """
    basis = basis_factory()
    record = _legacy_record(basis, make_site(), lib)
    view = detect_study_basis(record)
    assert view.present, "a legacy study record was read as an underwriting run"
    assert view.label == basis.LABEL
    assert view.basis_id is None and view.note is not None


def test_an_underwriting_record_claims_no_basis(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """A detector stuck ON is as wrong as one stuck off: no id and no note means no banner."""
    view = detect_study_basis(_pipeline_record(None, make_site(), lib))
    assert not view.present
    assert view.label is None and view.note is None and view.basis_id is None


def test_a_basis_this_build_cannot_name_still_states_the_certain_half(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """A forward-dated ``study_basis_id`` must render, not crash, and must not claim underwriting.

    `schemas.study.study_basis_label` returns ``None`` for an id it does not know, which would have
    rendered the memo banner as the literal ``> **None:**``. The generic label states the half that
    is certain (this is not an underwriting output) and names the gap.
    """
    record = _pipeline_record(None, make_site(), lib).model_copy(
        update={"study_basis_id": "a_basis_from_the_future_v9"}
    )
    view = detect_study_basis(record)
    assert view.present, "a record naming an unknown basis is still a study record"
    assert view.label == _STUDY_BASIS_UNNAMED_LABEL
    memo = render_underwriting_memo(build_view_model(record, None))
    assert "**None:**" not in memo, "the banner rendered a Python None as its label"
    assert "NOT AN UNDERWRITING OUTPUT" in memo
    assert "a_basis_from_the_future_v9" in memo, "the recorded id is the only fact available"


# --------------------------- THE RENDERED BYTES: LEADING BANNER, IN EVERY ARTIFACT


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
@pytest.mark.parametrize("route", ["pipeline", "id_only", "legacy"])
def test_every_artifact_leads_with_the_basis_that_priced_it(
    basis_factory: Callable[[], StudyBasis],
    route: str,
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """Both bases, all three record shapes, all four artifacts, asserted on position.

    Production call sites, each reachable from `build_view_model`:
    `underwriting._banner` (`outputs/underwriting.py`), `html_report._banner`,
    `consulting_report._head` + `._caveats` + `._doc_title`, and the basis block at the head of
    `proposal.render_customer_proposal`.
    """
    basis = basis_factory()
    site = make_site()
    record = {
        "pipeline": lambda: _pipeline_record(basis, site, lib),
        "id_only": lambda: _id_only_record(basis, site, lib),
        "legacy": lambda: _legacy_record(basis, site, lib),
    }[route]()
    vm = build_view_model(record, site)
    label = _short(basis)
    # The OTHER basis's label must be absent: a D55 run described under D46's export-only
    # wording understates its priced floor by five streams, the subtler half of the same defect.
    other = _short(all_streams() if basis.basis_id == StudyBasis.BASIS_ID else study())

    # --- the underwriting pack: above the bankable banner, not in "## Warnings" at line 193 of 219.
    memo = render_underwriting_memo(vm)
    assert label in memo, f"the memo does not name the basis that priced it ({basis.basis_id})"
    assert memo.index(label) < _first_section(memo), (
        "the memo states its basis below the first section heading; the measured defect was a "
        "basis stated at line 193 of 219 under '## Warnings', which is presence without prominence"
    )
    assert memo.index(label) < memo.index("Status on record:"), (
        "the basis must sit ABOVE the bankable-status banner, which is what a reader reads first"
    )
    assert other not in memo, f"the memo describes a {basis.basis_id} run under {other}"

    # --- the customer proposal: the lead is plain English, and it is never "the underwriting run".
    proposal = render_customer_proposal(vm)
    assert "Feasibility study, not an offer." in proposal
    assert proposal.index("Feasibility study, not an offer.") < _first_section(proposal)
    assert "the underwriting run" not in proposal, (
        "the customer proposal calls a study run an underwriting run: the exact measured defect"
    )
    assert "feasibility study run" in proposal

    # --- the consulting report: inside the masthead, on the front page.
    report = render_consulting_report(vm, site=site)
    head_start = report.index('<header class="doc">')
    masthead = report[head_start : report.index("</header>", head_start)]
    assert label in masthead, (
        "the consulting report does not name its basis in the masthead; it used to appear first at "
        "roughly line 140 of 1,070 inside a warning block"
    )
    assert label in report[: report.index("</title>")], "the browser tab does not name the basis"
    assert other not in report

    # --- the HTML report: the banner above the summary card.
    html = render_html_report(vm)
    assert label in html
    assert html.index(label) < html.index("<h2>Summary</h2>"), (
        "the HTML report states its basis below the summary; the banner is the leading element"
    )
    assert other not in html

    # Every artifact says the one thing that governs what a reader may do with the figures.
    for name, text in (("memo", memo), ("consulting", report), ("html", html)):
        assert "NOT AN UNDERWRITING OUTPUT" in text.upper(), f"{name} omits the governing statement"


def test_no_artifact_claims_a_study_basis_on_an_underwriting_run(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The other direction. An underwriting pack must not carry a basis banner or the study wording.

    Asserted on the upper-case chip ``"STUDY BASIS"`` rather than on the phrase "not an underwriting
    output": the D56 assumptions page legitimately says the latter about a run resting on asserted
    commitments, which is a different statement and not a basis claim.
    """
    site = make_site()
    vm = build_view_model(_pipeline_record(None, site, lib), site)
    assert not vm.study_basis and vm.study_basis_label is None
    memo = render_underwriting_memo(vm)
    proposal = render_customer_proposal(vm)
    report = render_consulting_report(vm, site=site)
    html = render_html_report(vm)
    for name, text in (
        ("memo", memo),
        ("proposal", proposal),
        ("consulting", report),
        ("html", html),
    ):
        assert "STUDY BASIS" not in text, f"{name} claims a study basis on an underwriting run"
    assert "Feasibility study, not an offer." not in proposal
    assert "underwriting run" in proposal and "feasibility study run" not in proposal


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
def test_the_verbatim_pointer_is_made_only_where_a_verbatim_statement_exists(
    basis_factory: Callable[[], StudyBasis],
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """ "Reproduced verbatim in the warnings" is a claim about the document, so it must be true.

    On an id-only record there is no warning to reproduce. Promising a lender a verbatim reason that
    is not in the pack is the same class of false claim as dropping the banner: precise, confident
    and wrong.
    """
    basis, site = basis_factory(), make_site()
    with_note = build_view_model(_pipeline_record(basis, site, lib), site)
    without = build_view_model(_id_only_record(basis, site, lib), site)
    pointer = "reproduced verbatim in the warnings"
    for render in (render_underwriting_memo, render_html_report):
        assert pointer in render(with_note), f"{render.__name__} drops a pointer it can honour"
        assert pointer not in render(without), (
            f"{render.__name__} promises a verbatim statement the record does not carry"
        )
    assert pointer in render_consulting_report(with_note, site=site)
    assert pointer not in render_consulting_report(without, site=site)
    # And the honest substitute names the recorded id, which is the whole of what the record says.
    assert basis.basis_id in render_underwriting_memo(without)


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
def test_the_d56_assumptions_page_states_the_basis_on_a_record_that_carries_no_note(
    basis_factory: Callable[[], StudyBasis],
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """The consolidated assumptions page keyed off the NOTE, so an id-only record lost its row.

    Every banner above it stated the basis correctly while the one page whose whole job is to
    enumerate what the run rests on said nothing about the basis that priced it.
    """
    basis, site = basis_factory(), make_site()
    vm = build_view_model(_id_only_record(basis, site, lib), site)
    statement = vm.assumptions_page.basis_statement
    assert "STUDY BASIS: NOT AN UNDERWRITING OUTPUT" in statement
    assert basis.basis_id in statement, "the page does not say WHICH basis priced the run"
    for render in (render_underwriting_memo, render_customer_proposal):
        assert "STUDY BASIS: NOT AN UNDERWRITING OUTPUT" in render(vm)


# --------------------------------------------------------------- THE FRONTEND SCREENS


def _saved(record: QuoteRecord, site: SiteModel) -> str:
    """Persist a record into the pinned test store and return its quote id.

    The store needs a DPA permit for the site (WP-GRC-05); `_fixture_dpa` registers a TEST FIXTURE
    one for the fictitious ``make_site`` id, so this passes through the real guard.
    """
    return store.save_run(record, site, None, dpa=authorise_store(record.site_id))


def _listable(record: QuoteRecord) -> QuoteRecord:
    """The same record carrying the one priceable grid point the LISTING predicate requires.

    `app.app._quote_complete` admits a run to the Projects listing and the dashboard hero only if it
    produced a real answer: a dispatch profile, a priceable candidate, or the explicit
    no-viable-configuration verdict. A `BaselineEngine` record has none of those (measured: empty
    grid, no profile), so without this it never reaches either surface and the listing assertions
    would pass vacuously against an empty table. The grid is the only field changed; the basis, the
    warnings and every figure stay as the pipeline wrote them.
    """
    grid = SizingGrid(
        search_space_size=1,
        bankable=False,
        selection_reason="single illustrative candidate, so the listing predicate admits the run",
        points=[
            SizingGridPoint(
                power_kw=42.0,
                duration_hours=2.0,
                energy_kwh=84.0,
                priceable=True,
                bankable=False,
                solver_status="optimal",
            )
        ],
    )
    return record.model_copy(update={"sizing_grid": grid})


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
@pytest.mark.parametrize("route", ["pipeline", "id_only", "legacy"])
def test_every_run_screen_states_the_basis(
    basis_factory: Callable[[], StudyBasis],
    route: str,
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """The frontend stated the basis on NO screen, on EITHER basis, before this landed.

    Production call sites: ``"basis": _basis_display(detect_study_basis(stored.quote))`` in
    `app/app.py::_run_context`, rendered by ``templates/_statusbar.html``, which all four run
    screens include. Asserted on the HTTP response bytes of every one of them.
    """
    basis = basis_factory()
    site = make_site()
    record = {
        "pipeline": lambda: _pipeline_record(basis, site, lib),
        "id_only": lambda: _id_only_record(basis, site, lib),
        "legacy": lambda: _legacy_record(basis, site, lib),
    }[route]()
    quote_id = _saved(record, site)
    client = TestClient(create_app())
    for screen in ("", "/spec", "/artifacts", "/telemetry"):
        resp = client.get(f"/run/{quote_id}{screen}")
        assert resp.status_code == 200, f"{screen or '/'} did not render"
        page = resp.text
        assert _short(basis) in page, (
            f"the {screen or 'results'} screen does not name the basis that priced every figure "
            f"on it ({basis.basis_id})"
        )
        assert "Feasibility basis, not an underwriting output." in page
        assert "NO STUDY BASIS RECORDED" not in page, "the screen contradicts its own banner"


def test_a_run_screen_on_an_underwriting_record_says_so_and_claims_no_basis(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The no-basis screen states what the RECORD says, and never over-claims.

    Worded "no named study basis is recorded" rather than "underwriting basis" on purpose: a record
    persisted before the basis id existed carries no basis field even if a study basis priced it.
    The first sentence is true of both; the second would not be.
    """
    site = make_site()
    quote_id = _saved(_pipeline_record(None, site, lib), site)
    client = TestClient(create_app())
    for screen in ("", "/spec", "/artifacts", "/telemetry"):
        page = client.get(f"/run/{quote_id}{screen}").text
        assert "NO STUDY BASIS RECORDED" in page
        assert "Feasibility basis, not an underwriting output." not in page
        assert "STUDY BASIS (D46)" not in page and "STUDY BASIS (D55)" not in page


@pytest.mark.parametrize("basis_factory", [study, all_streams], ids=["d46", "d55"])
def test_the_project_listing_and_dashboard_hero_state_the_basis(
    basis_factory: Callable[[], StudyBasis],
    make_site: Callable[..., SiteModel],
    lib: AssumptionLibrary,
) -> None:
    """The listing links to the run and the hero headlines its IRR, so both must name the basis.

    Production call sites: `app/app.py::_run_summary` (the ``"basis"`` key, rendered by
    ``templates/projects.html``) and `app/app.py::_hero_stats` (rendered by ``landing.html``). The
    listing showed a study run beside an underwriting one with nothing to tell them apart.
    """
    basis, site = basis_factory(), make_site()
    _saved(_listable(_pipeline_record(basis, site, lib)), site)
    client = TestClient(create_app())
    listing = client.get("/projects")
    assert listing.status_code == 200
    assert "Pricing basis" in listing.text, "the listing has no basis column"
    assert _short(basis) in listing.text, (
        "the listing does not name the basis of the run it links to"
    )
    landing = client.get("/")
    assert landing.status_code == 200
    assert _short(basis) in landing.text, "the hero headlines a study figure without saying so"
    assert "feasibility basis, not an underwriting output." in landing.text


def test_the_listing_and_hero_claim_no_basis_on_an_underwriting_run(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The other direction on the two listing surfaces: no id, no note, no basis claim."""
    site = make_site()
    _saved(_listable(_pipeline_record(None, site, lib)), site)
    client = TestClient(create_app())
    listing, landing = client.get("/projects").text, client.get("/").text
    assert "Pricing basis" in listing and "None recorded" in listing
    for page in (listing, landing):
        assert "STUDY BASIS (D46)" not in page and "STUDY BASIS (D55)" not in page
    assert "feasibility basis, not an underwriting output." not in landing


def test_the_basis_label_reaches_every_surface_with_its_em_dashes_rewritten(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """House style: no rendered GridQ surface carries an em or en dash (`formatting.no_dashes`).

    Every `StudyBasis.LABEL` joins its phrases with em dashes, and two of the four documents are
    deliberately NOT scrubbed at document level (their warnings must stay byte-identical to the
    record), so the label is scrubbed at the POINT OF USE instead. Asserted as "the dashed label is
    absent and the scrubbed short form is present" rather than "no dash anywhere on the page",
    because record-authored text elsewhere on a run screen legitimately carries dashes and this test
    is about the label. `test_outputs_no_em_dashes` sweeps the documents whole.
    """
    site, basis = make_site(), all_streams()
    vm = build_view_model(_id_only_record(basis, site, lib), site)
    quote_id = _saved(_listable(_id_only_record(basis, site, lib)), site)
    client = TestClient(create_app())
    page = client.get(f"/run/{quote_id}").text
    surfaces = {
        "memo_banner": render_underwriting_memo(vm).split("\n## ")[0],
        "html_banner": render_html_report(vm).split("<h2>Summary</h2>")[0],
        "consulting": render_consulting_report(vm, site=site),
        "proposal": render_customer_proposal(vm),
        "run_screen_status_strip": page[page.index("statusstrip") : page.index("<main")],
        "projects": client.get("/projects").text,
        "landing": client.get("/").text,
    }
    for name, text in surfaces.items():
        assert "—" not in text and "–" not in text, f"{name} carries an em or en dash"
        assert _short(basis) in text, f"{name} lost the basis label to the dash rewrite"


def test_the_consulting_recommendation_refuses_to_send_a_study_floor_to_underwriting(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The one branch a study basis had to change and did not, until now.

    Production call site: `outputs/consulting_report.py::_recommendation`, rendered by
    `_answer_first`.
    On a BANKABLE record it returned "Proceed to underwriting on the priced floor" and the sentence
    "The merchant upside stays outside the underwritten case" regardless of basis. On a study basis
    both are false in the same direction as the original defect: the priced floor HERE contains the
    revenue the underwriting basis holds outside it, so the pack recommended underwriting a floor
    that does not exist on the basis being underwritten to.

    ``bankable`` and a solved ``solver_status`` are forced on the view model rather than coaxed out
    of a fixture, because what is under test is one BRANCH of `_recommendation` and no cheap
    fixture reaches it: a `BaselineEngine` record trips the earlier unsolved-dispatch branch, and
    the shipped all-placeholder library never reaches "bankable candidate". Both forced values are
    exactly that branch's own preconditions; every other field stays as the record produced it, and
    the closing assertion proves the same fixture still reaches the UNTOUCHED branch on an
    underwriting basis, so this is not a test that would pass against a blanket refusal.
    """
    site = make_site()

    def bankable_solved(basis: StudyBasis | None) -> OutputViewModel:
        vm = build_view_model(_pipeline_record(basis, site, lib), site)
        return replace(
            vm,
            bankable=True,
            bankable_status="bankable_candidate",
            spec=replace(vm.spec, solver_status="optimal"),
        )

    for basis in (study(), all_streams()):
        report = render_consulting_report(bankable_solved(basis), site=site)
        assert "Proceed to underwriting on the priced floor" not in report, (
            f"a {basis.basis_id} pack recommends underwriting a floor that prices revenue the "
            "underwriting basis holds outside the floor altogether"
        )
        assert "The merchant upside stays outside the underwritten case" not in report, (
            "on a study basis the merchant upside IS the case, so this sentence is false"
        )
        assert "reprice it on the underwriting basis" in report.lower()
    # And the underwriting-basis recommendation is untouched: never a blanket refusal.
    assert "Proceed to underwriting on the priced floor" in render_consulting_report(
        bankable_solved(None), site=site
    )
