"""E2E proof: the pipeline runs from the real Haywood Oaks facts and returns the CORRECT result.

The derivation of "correct" for these inputs is ``docs/redteam/EXPECTED_RESULT.md`` (2026-07-07):
Robert Thomas Farms on a SmartFix day/night contract has a ~1.5 p/kWh effective arbitrage margin
after losses and CCL, no pass-through DUoS exposure, no export route — **no battery size is
economically viable**, so the correct output is a completed, deterministic quote carrying the
explicit ``no_viable_configuration`` verdict with the full transcribed tariff stack, a modelled
load profile that honours every documented observation, ranked data asks, and NO manufactured
system spec (the pre-fix pipeline recommended the smallest grid corner, 4 kW × 3.09 h).

**2026-08-15 — why this module no longer skips.** It used to load its POST body from
``examples/real/haywood_oaks_prefill.json`` and skip the whole module when that file was absent.
It was absent on every host: the file has **no git history at all**, so the module had been
reporting nine skips and guarding nothing since the day it was written — and its stated reason
("gitignored private facts") stopped being true on 2026-08-11, when ``examples/real/`` was
deliberately un-ignored and committed. The invoice facts that file transcribed are themselves
tracked, in ``docs/redteam/EXPECTED_RESULT.md`` §1 (the authoritative transcription of
SmartestEnergy invoice EVY220LUQ/007 and the observed load facts), so the POST body is now built
**here**, field by field, each line carrying the tracked source it came from. Nothing is invented:
a fact the tracked sources do not state is left blank, exactly as the loader's docstring required
("unknown fields are blank → honest data-asks").

Two honest consequences of that change, recorded rather than papered over:

* AC-1's claim narrows from "runs from ``examples/real`` alone" to "runs headlessly from the
  transcribed intake facts alone" — no upload, no manual step, no half-hourly file. The pipeline
  path exercised (``POST /api/run`` → modelled profile → MILP sizing search → persisted quote →
  results page) is identical.
* ``examples/real/Robert Thomas Farms/robert_thomas_sitemodel.json`` is **not** usable as the
  source: it is a stale, self-labelled ``illustrative_pending_invoice`` transcription
  (148,222 kWh, red/amber/green bands, ASC 500 kW) that contradicts the authoritative facts below
  on every figure this module asserts.

Heavy module: two full pipeline runs (the second proves determinism) — one MILP sizing search
each, roughly 3-4 minutes in total. It no longer skips on any host.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

MODULE_STORE = tempfile.mkdtemp(prefix="gridq-e2e-real-")
os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests._dpa_support import shown_once  # noqa: E402

from gridq.app import create_app, store  # noqa: E402
from gridq.governance.dpa_guard import TransientWorkspace  # noqa: E402
from gridq.schemas import SelectionStatus  # noqa: E402

#: The app module itself (``gridq.app`` re-exports the FastAPI instance under the name ``app``).
app_mod = importlib.import_module("gridq.app.app")

#: The real-mode intake body for Robert Thomas Farms (Haywood Oaks), transcribed from the tracked
#: red-team record. Keys are the ``real_form.html`` field names that `_site_from_form` /
#: `_synthesise_profile_if_needed` actually read. Every value cites the line it came from; every
#: fact the sources do not state is **absent**, so it stays a ranked data-ask (company number,
#: G98/G99/G100, export MPAN, export route, DUoS bands).
PREFILL: dict[str, str] = {
    # --- identity -------------------------------------------------------------------------
    "site_id": "robert_thomas_haywood_oaks",  # EXPECTED_RESULT.md title / RUNBOOK.md title
    "site_name": "Haywood Oaks Farm (Robert Thomas Farms Ltd)",  # EXPECTED_RESULT.md §1
    # NOTE: the two tracked sources disagree on the postcode — `docs/wave5_acceptance_report.md:195`
    # says NG21 0QA, the stale illustrative sitemodel says NG21 0PE. The acceptance report is the
    # later and the authoritative-for-this-run document, so it is used. Nothing asserted below
    # depends on the postcode; it enters only the site_id fallback (overridden above) and the
    # canonical input hash, where self-consistency is all AC-8 needs.
    "postcode": "NG21 0QA",
    # --- observed load facts (EXPECTED_RESULT.md §1, second table) -------------------------
    "annual_consumption_kwh": "215000",  # "~215,000 kWh/yr (588 kWh/day)"
    "max_import_kw": "421",  # "Peak demand ~421 kW (max half-hour 210.5 kWh)"
    "day_kwh_share": "0.548",  # "day 10,240.7 kWh / night 8,450.3 kWh ≈ 54.8% day"
    "peak_period": "overnight",  # "heavy overnight (~48 kW avg 00:00-04:00)"
    "midday_low": "true",  # "near-zero 09:00-14:00"
    # --- connection (EXPECTED_RESULT.md §1, first table + "Immediate consequences") ---------
    "mic_kw": "250",  # the contracted capacity the 421 kW peak already breaches
    "asc_kw": "250",  # "8.76 p/kVA/day × 250 kVA ASC"
    "voltage_level": "LV",  # farm LV supply (MPAN distributor 11, NGED East Midlands)
    "dno_region": "NGED_EM",  # "NGED East Midlands (MPAN distributor 11)"
    # --- tariff (EXPECTED_RESULT.md §1, first table — invoice EVY220LUQ/007, Oct 2025) ------
    "supplier": "SmartestEnergy Business",  # invoice header
    "tariff_name": "SmartFix Standard",  # "Contract | SmartFix Standard, ends 01 Apr 2026"
    "standing_charge": "25.61",  # "Standing charge | £25.61/day"
    "import_rates": "day:0.2036, night:0.165",  # "20.36 p/kWh" day, "16.5 p/kWh" night
    "levies": "ccl:0.00775",  # "Climate Change Levy | 0.775 p/kWh on every imported kWh"
    "capacity_charge_gbp_per_kva_day": "0.0876",  # "8.76 p/kVA/day"
    "tariff_source": "SmartestEnergy invoice EVY220LUQ/007 (Oct 2025), transcribed",
    # DUoS bands are deliberately ABSENT: extract note 3 records "no separate red/amber/green
    # lines — bundled into the unit rates (SmartFix fixed contract)". A rate typed here would be
    # an invention, and the £0 red-band value AC-5 asserts is the *correct* reading of a bundled
    # tariff, not an unpopulated field.
    # --- counterparty (EXPECTED_RESULT.md §1: "company number unknown, credit unassessed") --
    "legal_name": "Robert Thomas Farms Ltd",
    "sector": "agriculture",
}

#: The analytic arbitrage ceiling for this site (EXPECTED_RESULT.md §2 L1): the day-band import
#: (54.8% × 215,000 kWh) valued at the effective margin (day+CCL − (night+CCL)/RTE) — no battery
#: of any size can earn more than this from tariff arbitrage here.
ARBITRAGE_CEILING_GBP = 0.548 * 215_000 * ((0.2036 + 0.00775) - (0.165 + 0.00775) / 0.88)


def _run_pipeline(client: TestClient) -> str:
    """POST the intake facts to /api/run exactly as the real-mode form does; return the quote id."""
    resp = client.post("/api/run", data=PREFILL)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    deadline = time.time() + 900
    while time.time() < deadline:
        status = client.get(f"/api/run/{job_id}").json()
        if status.get("status") in ("done", "error"):
            break
        time.sleep(1)
    assert status.get("status") == "done", f"pipeline did not complete: {status}"
    return str(status["quote_id"])


@dataclass(frozen=True)
class _E2E:
    """One completed real-facts pipeline run, shared by every assertion in this module."""

    client: TestClient
    quote_id: str
    stored: store.StoredRun
    #: The results page as the visitor saw it. A run without a DPA is shown once (held for
    #: `ephemeral.TTL_SECONDS`), and this module outlives that window (AC-8 re-runs the search).
    page_status: int
    page_html: str


@pytest.fixture(scope="module")
def e2e() -> _E2E:
    """Run the pipeline ONCE for the module.

    Module-scoped rather than import-time so collection (and any deselection) stays free: the
    previous shape solved the whole MILP search at import, which made `--collect-only` cost
    minutes and made the module's cost unavoidable even when nothing here was selected.
    """
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE
    client = TestClient(create_app())
    quote_id = _run_pipeline(client)
    # An anonymous visitor with no DPA for the site: the run is shown, never stored (WP-GRC-05,
    # owner decision (a)). `shown_once` asserts both and returns the record the screens serve.
    stored = shown_once(quote_id)
    page = client.get(f"/run/{quote_id}")
    return _E2E(
        client=client,
        quote_id=quote_id,
        stored=stored,
        page_status=page.status_code,
        page_html=page.text,
    )


@contextlib.contextmanager
def _modelled_profile(e2e: _E2E) -> Iterator[Path]:
    """The run's modelled profile, re-modelled from the same facts and held while it is read.

    The run deleted its own copy when it ended: a profile modelled from a visitor's facts is
    their data, and without a DPA nothing of theirs outlives the run (WP-GRC-05). The generator
    is deterministic and names its file by a digest of the content, so the same file name proves
    the same bytes the run priced.
    """
    site = e2e.stored.site
    assert site is not None and site.load_data.half_hourly_file_ref
    data: dict[str, str] = dict(PREFILL)
    app_mod._apply_reference_data(data)  # exactly what the job does before modelling
    with TransientWorkspace() as workspace:
        summary, synthetic, _ = app_mod._synthesise_profile_if_needed(data, None, [], workspace)
        assert synthetic and summary is not None and summary.file_ref
        path = Path(summary.file_ref)
        assert path.name == Path(site.load_data.half_hourly_file_ref).name, (
            "the re-modelled profile is not the one the run priced"
        )
        yield path


@pytest.fixture(autouse=True)
def _pin_store() -> None:
    os.environ["GRIDQ_RUN_STORE"] = MODULE_STORE


def test_ac1_pipeline_completes_headlessly_from_examples_real_alone(e2e: _E2E) -> None:
    # The only inputs were the prefill fields (transcribed invoice/intake facts) — no upload,
    # no manual steps. The quote is held for this visitor (never stored without a DPA, WP-GRC-05)
    # and the results page serves it.
    assert e2e.stored.quote.quote_id == e2e.quote_id
    assert e2e.page_status == 200


def test_ac2_full_transcribed_tariff_stack_reaches_the_model(e2e: _E2E) -> None:
    site = e2e.stored.site
    assert site is not None
    t = site.tariff_stack
    assert t.levies == {"ccl": 0.00775}  # Climate Change Levy from the invoice
    assert t.capacity_charge_gbp_per_kva_day == 0.0876  # availability charge from the invoice
    assert t.standing_charge_per_day_gbp == 25.61
    assert t.import_rates == {"day": 0.2036, "night": 0.165}
    assert t.tariff_name == "SmartFix Standard"
    assert site.connection.asc_kw == 250.0  # the invoice's availability basis


def test_ac3_modelled_profile_honours_every_documented_observation(e2e: _E2E) -> None:
    site = e2e.stored.site
    assert site is not None
    assert site.annual_consumption_kwh == 215_000.0  # stated figure preserved (INV-M0-13)
    with _modelled_profile(e2e) as ref:
        df = pd.read_csv(ref, parse_dates=["timestamp"])
    kwh = float(df["import_kw"].sum()) * 0.5
    assert kwh == pytest.approx(215_000, rel=1e-3)  # annual energy exact
    assert float(df["import_kw"].max()) == pytest.approx(421.0, rel=0.01)  # observed peak exact
    night = df["timestamp"].dt.hour.isin([23, 0, 1, 2, 3, 4, 5, 6])
    day_share = float(df.loc[~night, "import_kw"].sum()) * 0.5 / kwh
    assert day_share == pytest.approx(0.548, abs=0.02)  # bill day/night split honoured
    midday = df["timestamp"].dt.hour.isin([9, 10, 11, 12, 13])
    assert float(df.loc[midday, "import_kw"].mean()) < 5.0  # near-zero midday, as observed
    # ... and the run says so, loudly.
    assert any("MODELLED, not metered" in w for w in e2e.stored.quote.warnings)
    assert site.data_tier.value == "T1_PARTIAL_HALF_HOURLY"


def test_ac4_verdict_is_first_class_no_manufactured_spec(e2e: _E2E) -> None:
    opt = e2e.stored.quote.optimisation_result
    assert opt is not None
    assert opt.selection_status == SelectionStatus.NO_VIABLE_CONFIGURATION
    assert opt.selected_power_kw is None  # never the smallest grid corner again
    assert opt.selected_energy_kwh is None
    assert opt.solver_status.value == "optimal"  # the dispatch SOLVED; economics said no
    assert any(w.startswith("no viable configuration") for w in opt.warnings)
    # The evaluated surface is preserved as evidence, and nothing on it is viable.
    grid = e2e.stored.quote.sizing_grid
    assert grid is not None and len(grid.points) >= 50
    for p in grid.points:
        assert (p.levered_return_pct or 0) <= 0
        assert (p.project_irr_pct or 0) <= 0


def test_ac5_arbitrage_never_exceeds_the_analytic_ceiling(e2e: _E2E) -> None:
    # Solve one representative candidate directly against the stored site and check the engine's
    # own tariff-arbitrage value against the hand-derived ceiling (EXPECTED_RESULT.md §2 L1).
    from gridq.assumptions import load_assumptions
    from gridq.dispatch.contract import DispatchProblem
    from gridq.optimisation.milp_solver import MilpDispatchSolver

    site = e2e.stored.site
    assert site is not None
    with _modelled_profile(e2e) as ref:
        load = site.load_data.model_copy(update={"half_hourly_file_ref": str(ref)})
        result = MilpDispatchSolver().solve(
            DispatchProblem(
                site_model=site.model_copy(update={"load_data": load}),
                assumptions=load_assumptions("gb_v1_4"),
                power_kw=80.0,
                energy_kwh=320.0,
                duration_hours=4.0,
            )
        )
    assert result.solver_status.value == "optimal"
    by = {v.stream.value: v.annual_revenue_gbp for v in result.value_streams}
    assert 0 <= by["TARIFF_ARBITRAGE"] <= ARBITRAGE_CEILING_GBP * 1.02
    assert by["DUOS_RED_BAND_AVOIDANCE"] == 0.0  # bundled tariff: nothing separately avoidable


def test_ac6_no_phantom_finance_on_an_unviable_site(e2e: _E2E) -> None:
    # With no viable system there is nothing to price: no financial case, no PPA price
    # recommendation presented as if it were an offer (pre-fix: £1.06/kWh, 5x the site's rate).
    assert e2e.stored.quote.financial_case is None
    assert any("Financial model skipped" in w for w in e2e.stored.quote.warnings)


def test_ac7_ranked_asks_include_the_decisive_missing_evidence(e2e: _E2E) -> None:
    from gridq.ingestion import build_ranked_data_asks

    site = e2e.stored.site
    assert site is not None
    ask_ids = {a.ask_id for a in build_ranked_data_asks(site)}
    assert "hh_12_months" in ask_ids  # the missing Jan-Mar months / full metered year
    assert "export_consents" in ask_ids  # G99 unknown gates every export stream


def test_ac8_determinism_second_run_reproduces_the_same_quote(e2e: _E2E) -> None:
    # Same inputs + same assumption release + same code ⇒ the identical quote id (the id binds
    # the canonical input hash). This re-runs the entire pipeline, MILP search included.
    assert _run_pipeline(e2e.client) == e2e.quote_id


def test_ac9_outputs_present_the_verdict_not_an_empty_spec(e2e: _E2E) -> None:
    from gridq.outputs import generate_artifacts

    opt = e2e.stored.quote.optimisation_result
    assert opt is not None and opt.bankable_status.value == "not_bankable"
    assert any("NOT bankable" in w for w in e2e.stored.quote.warnings)
    arts = generate_artifacts(e2e.stored.quote, e2e.stored.site)
    # WP-CORE-01 triage (2026-11-16): re-pinned from "No viable configuration". The report's system
    # card was reworded in 12033d0 (changelog 2026-08-17, "A null `financial_case` is no longer
    # presented as an economic verdict"): THAT no system was selected is the recorded outcome, WHY
    # is the engine's own reason reproduced verbatim, and the card's former claim "none is
    # economically viable on the priced value floor" was retired because no-spec records carry no
    # case to support it. Same class as the cc36f04 reword tracked below. The card now opens with
    # the literal at `outputs/html_report.py` `_system`: "No system was selected."
    assert "No system was selected." in arts.report_html
    # The customer-facing sentence was reworded by `cc36f04` (2026-08-15, "Stop the packs asserting
    # seven things the record does not hold"): "a battery **does not stack up** at this site today"
    # made an arithmetic claim — none of the candidates "earns enough to justify its cost" — that
    # no persisted return figure supports, so it was deliberately retired in favour of the verdict
    # without the comparison. This assertion tracks that committed decision; it is NOT loosened
    # (the proposal must still open the section with a first-class refusal). The reword shipped
    # with zero coverage precisely because this module was skipping.
    assert "we are not proposing a battery at this site today" in arts.proposal_markdown
    # WP-CORE-01 triage (2026-11-16): re-pinned from "NO VIABLE CONFIGURATION", the same
    # documented 12033d0 change as the report card above (changelog 2026-08-17: the memo's
    # "none produces a positive return on the priced value floor" was retired with it). The memo's
    # verdict line is now the literal at `outputs/underwriting.py` `_system_spec`.
    assert "Verdict: NO SYSTEM WAS SELECTED." in arts.memo_markdown
    assert "no viable configuration" in e2e.page_html.lower()
