"""GridQ CLI.

`version` / `info` describe the scaffold; `run` is the real end-to-end command — it takes a
site through ingestion → the real half-hourly dispatch MILP (`gridq.optimisation.MilpEngine`,
the default for runs) → the financial model → a stamped, traceable `QuoteRecord`, emits a
`TelemetryEvent`, and degrades honestly (validation warnings + ranked data asks) when inputs are
missing. On the `gb_v0_1` placeholder assumption library every `run` quote still stays
`not_bankable` (a real dispatch alone is not bankable on placeholders); the integration-only
`BaselineEngine` remains the fallback.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import typer
from rich.console import Console
from rich.table import Table

from gridq import __version__

app = typer.Typer(
    help="GridQ — BESS sizing & underwriting engine (scaffold).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

#: Exit code when the DPA guard refuses (WP-GRC-05): customer data without DPA evidence.
EXIT_DPA_REFUSED = 3

_F = TypeVar("_F", bound=Callable[..., Any])


def _dpa_refusals(command: _F) -> _F:
    """Turn a DPA-guard refusal anywhere in a command into one message and exit code 3.

    Applied under ``@app.command()``; `functools.wraps` keeps the signature typer reads.
    """

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from gridq.governance.dpa_guard import (
            EVIDENCE_DIR_ENV,
            DpaEvidenceMissing,
            EvidenceRegistryError,
        )

        # `--evidence-dir` sets the registry for THIS command only: restored afterwards, so an
        # in-process caller (CliRunner, a notebook) never inherits another run's registry.
        before = os.environ.get(EVIDENCE_DIR_ENV)
        try:
            return command(*args, **kwargs)
        except (DpaEvidenceMissing, EvidenceRegistryError) as exc:
            console.print(f"[red]Refused (WP-GRC-05 DPA guard):[/red] {exc}")
            console.print(
                "Nothing was written. Register the customer's executed DPA as an IC-4 "
                "data_processing_agreement evidence object (one per site) in the evidence "
                "registry, or pass --evidence-dir."
            )
            raise typer.Exit(code=EXIT_DPA_REFUSED) from exc
        finally:
            if before is None:
                os.environ.pop(EVIDENCE_DIR_ENV, None)
            else:
                os.environ[EVIDENCE_DIR_ENV] = before

    return wrapper  # type: ignore[return-value]


def _use_evidence_dir(evidence_dir: Path | None) -> None:
    """Point the DPA guard at ``evidence_dir`` for this process (``--evidence-dir``)."""
    if evidence_dir is not None:
        from gridq.governance.dpa_guard import EVIDENCE_DIR_ENV

        os.environ[EVIDENCE_DIR_ENV] = str(evidence_dir)


_EVIDENCE_DIR_HELP = (
    "IC-4 evidence registry holding the customer's DPA evidence objects "
    "(default: $GRIDQ_EVIDENCE_DIR, else <repo>/evidence). Without valid, verified DPA evidence "
    "for the site, a customer site is refused (exit 3) and nothing is written."
)

# (package, responsibility) — kept in sync with the scaffolded subpackages.
_MODULES: list[tuple[str, str]] = [
    (
        "schemas",
        "Data contracts: SiteModel, OptimisationRequest/Result, FinancialCase, "
        "QuoteRecord, TelemetryEvent",
    ),
    ("ingestion", "M0 — validate & grade a SiteModel"),
    ("assumptions", "X.1 — versioned assumption library"),
    ("provenance", "X.2 — hashing, versions, run record"),
    ("engine", "Optimisation engine interface + baseline + conformance (the platform/engine seam)"),
    (
        "dispatch",
        "M3 — dispatch-solver seam (Protocol + SimpleSolver placeholder; real MILP later)",
    ),
    ("sizing", "M4 — sizing optimiser outer loop (SearchSpace, SizingObjective, enumerate_sizing)"),
    (
        "optimisation",
        "M3/M4 — real half-hourly dispatch MILP + sizing engine (linopy/HiGHS); default for runs",
    ),
    ("value_stack", "M2 — generic value & constraint stream interface + GB stream content"),
    ("scenarios", "M6 — discrete price/policy scenarios"),
    ("finance", "M5 (+M9) — P50/P90, DSCR, price-to-target, credit mapping"),
    ("quotes", "Quote lifecycle + telemetry (X.2/X.3)"),
    ("telemetry", "X.3 — manual quote benchmark (cost-compression measurement)"),
    ("outputs", "M8 — memo + proposal generation"),
    ("app", "Demo web shell (optional `app` extra: FastAPI + Jinja2)"),
    ("dev", "Dev tooling — review gates, skills protocol, team-sync"),
]


@app.command()
def version() -> None:
    """Print the GridQ package version."""
    console.print(f"gridq [bold cyan]{__version__}[/bold cyan]")


@app.command()
def info() -> None:
    """List the scaffolded modules and their responsibilities."""
    table = Table(title=f"GridQ {__version__} — modules (scaffold)")
    table.add_column("Package", style="bold")
    table.add_column("Responsibility")
    for package, responsibility in _MODULES:
        table.add_row(package, responsibility)
    console.print(table)


@app.command()
@_dpa_refusals
def run(
    site_json: Path = typer.Argument(  # noqa: B008 (typer option object)
        ..., exists=True, dir_okay=False, help="Path to a SiteModel JSON file."
    ),
    hh: Path | None = typer.Option(  # noqa: B008
        None,
        "--hh",
        exists=True,
        dir_okay=False,
        help="Real half-hourly load CSV to fold into the site and re-grade its data tier.",
    ),
    forecast: Path | None = typer.Option(  # noqa: B008
        None,
        "--forecast",
        exists=True,
        dir_okay=False,
        # N22: this said "gated upside; never priced into the floor" while the engine prices it
        # into the floor. The Elexon-MID delta IS gated upside — but on a site that consents to
        # WHOLESALE_P415 and declares an agreed export tariff, the forecast-driven export overlay
        # measures the export volume and `MilpEngine._final_result` adds
        # `tariff x export MWh` to value_streams as CONTRACTED (D43), which finance sums into
        # P50/P90, the DSCR and both IRRs. Supplying a forecast therefore can and does raise the
        # priced floor; the run warns on the quote whenever it did.
        help=(
            "Wholesale/Modo price-forecast file. The Elexon-MID delta is gated upside; on a site "
            "consented to WHOLESALE_P415 with an agreed export tariff it ALSO prices that tariff "
            "x measured export MWh into the floor as CONTRACTED (the run says so when it does)."
        ),
    ),
    forecast_source: str | None = typer.Option(  # noqa: B008
        None, "--forecast-source", help="Forecast provider, if the file does not record one."
    ),
    forecast_version: str | None = typer.Option(  # noqa: B008
        None, "--forecast-version", help="Forecast vintage label, if the file does not record one."
    ),
    assumptions_version: str | None = typer.Option(  # noqa: B008
        None,
        "--assumptions",
        help="Assumption library version to price against (default: the newest release).",
    ),
    contributor: str | None = typer.Option(  # noqa: B008
        None, "--contributor", help="Who ran this quote (recorded on the telemetry event)."
    ),
    wholesale: bool = typer.Option(  # noqa: B008
        False,
        "--wholesale/--no-wholesale",
        help="Treat wholesale/P415 as requested (lists the forecast file as a data ask if absent).",
    ),
    out: Path | None = typer.Option(  # noqa: B008
        None,
        "--out",
        file_okay=False,
        help="Directory for the run artifacts (default: runs/<quote_id>/).",
    ),
    json_out: bool = typer.Option(  # noqa: B008
        False, "--json", help="Print the QuoteRecord as JSON instead of a summary table."
    ),
    evidence_dir: Path | None = typer.Option(  # noqa: B008
        None, "--evidence-dir", file_okay=False, help=_EVIDENCE_DIR_HELP
    ),
    lender_terms_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--lender-terms",
        exists=True,
        dir_okay=False,
        # WP-UW-03 (B001): before this option the CLI could not supply debt terms at all, so every
        # real run's DSCR was None. Without it the run states why there is no DSCR.
        help=(
            "Lender terms file (JSON, or YAML by extension): tenor, all-in rate, max gearing, "
            "amortisation, cash-flow case, test period, covenant tiers and their source. The DSCR "
            "is sized and measured on these terms; without them the run states why it has none."
        ),
    ),
    lifecycle_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--lifecycle",
        exists=True,
        dir_okay=False,
        # WP-UW-17: construction programme, COD delay, CM agreement, terminal value,
        # decommissioning and replacements, each with its source. Nothing is defaulted.
        help=(
            "Lifecycle inputs file (JSON, or YAML by extension): construction months, drawdown "
            "and IDC rate, a COD delay to stress, the CM agreement (auction type, delivery year, "
            "CPI), terminal value, decommissioning and replacements, with their source. Without "
            "it each is excluded and labelled on the case."
        ),
    ),
) -> None:
    """Run a site end-to-end into a stamped, traceable quote (real MILP; gb_v0_1 not_bankable).

    The DPA guard runs before any computation (WP-GRC-05): a site that is not the attested demo or
    a ``SYNTHETIC-`` fixture needs valid, verified DPA evidence, or the run exits 3 having written
    nothing. The permit is recorded beside the artifacts as ``dpa_evidence.json``.
    """
    from gridq.governance.dpa_guard import DPA_SIDECAR, authorise_store, write_dpa_sidecar

    _use_evidence_dir(evidence_dir)
    # Heavy (pandas-backed) imports are local so `version`/`info` stay fast.
    from gridq.assumptions import AssumptionError, load_assumptions
    from gridq.finance.lender_terms import LenderTermsError, load_lender_terms
    from gridq.finance.lifecycle import LifecycleInputsError, load_lifecycle_inputs
    from gridq.ingestion import (
        HalfHourlyDataError,
        SiteModelLoadError,
        assemble_site_model,
        load_half_hourly_csv,
        load_site_model_json,
        populate_missing_data,
        summarise_half_hourly_data,
    )
    from gridq.optimisation import MilpEngine
    from gridq.quotes import generate_quote
    from gridq.schemas import LenderTerms, LifecycleInputs, SiteModel

    notes: list[str] = []

    # 0. Lender terms are read FIRST: a malformed term sheet must fail before a minutes-long solve,
    # and must never be silently dropped (the run would then publish without the user's terms).
    lender_terms: LenderTerms | None = None
    if lender_terms_file is not None:
        try:
            lender_terms = load_lender_terms(lender_terms_file)
        except LenderTermsError as exc:
            console.print(f"[red]Could not load lender terms:[/red] {exc}")
            raise typer.Exit(code=2) from exc
    # WP-UW-17: lifecycle inputs are read first for the same reason.
    lifecycle: LifecycleInputs | None = None
    if lifecycle_file is not None:
        try:
            lifecycle = load_lifecycle_inputs(lifecycle_file)
        except LifecycleInputsError as exc:
            console.print(f"[red]Could not load lifecycle inputs:[/red] {exc}")
            raise typer.Exit(code=2) from exc

    # 1. Load + validate the SiteModel.
    try:
        base_site = load_site_model_json(site_json)
    except SiteModelLoadError as exc:
        console.print(f"[red]Could not load site:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    # 1b. The DPA guard, before anything is read or computed for this site (WP-GRC-05).
    permit = authorise_store(base_site.site_id)

    # 2. Optionally fold a real half-hourly file in and re-grade the data tier.
    hh_summary = None
    if hh is not None:
        try:
            # Attribute the confidential-data read to the site (M0 access log, L03).
            df = load_half_hourly_csv(
                hh,
                site_id=base_site.site_id,
                is_personal_data=base_site.is_personal_data,
            )
            hh_summary = summarise_half_hourly_data(df, file_ref=str(hh))
            notes.extend(hh_summary.report.messages())
        except HalfHourlyDataError as exc:
            notes.append(f"half-hourly file {hh} not used: {exc}")
            hh_summary = None

    wholesale_requested = wholesale and forecast is None

    # 2b. Gate 3's measured side, and it must never go missing quietly (same class as C-6: correct
    # in the module, absent on the path). `generate_quote` was called with no `hh_summary` even
    # though one exists here, so on a `--hh` run the gate re-read the file it had just been handed;
    # and on a run with no `--hh` the pipeline resolved the site's own RELATIVE
    # `half_hourly_file_ref` against the PROCESS CWD. Run from anywhere but the repo root that read
    # failed, `reconciliation` came back None, and the mandatory reconciliation gate was silently
    # un-run at band MEDIUM, indistinguishable from a corrupt file. A mandatory gate that can no-op
    # in silence is not a gate. So: resolve the ref against the site file as well as the cwd, and
    # when it still cannot be read, SAY SO on the quote.
    gate3_summary = hh_summary
    declared_ref = site_hh_ref = base_site.load_data.half_hourly_file_ref
    if gate3_summary is None and declared_ref:
        resolved = _resolve_declared_hh_ref(declared_ref, site_json)
        if resolved is None:
            notes.append(
                "LOAD RECONCILIATION (Gate 3) COULD NOT RUN: the site declares its half-hourly "
                f"series at {declared_ref!r}, and no readable file was found at that path relative "
                f"to the working directory ({Path.cwd()}) or to the site file "
                f"({site_json.resolve().parent}). The declared load basis has therefore NOT been "
                "checked against the series being priced, so nothing establishes that the sizing "
                "side and the revenue side of this quote describe the same load. This is a missing "
                "check, not a passed one: re-run with --hh pointing at the series, or from a "
                "directory the declared path resolves against."
            )
        else:
            try:
                frame = load_half_hourly_csv(
                    resolved,
                    site_id=base_site.site_id,
                    is_personal_data=base_site.is_personal_data,
                )
                # `file_ref` stays the DECLARED ref, not the resolved absolute path: the summary is
                # provenance about the site's own declaration, and pinning the absolute path would
                # make the same site's quote text depend on where it was run from.
                gate3_summary = summarise_half_hourly_data(frame, file_ref=str(site_hh_ref))
            except HalfHourlyDataError as exc:
                notes.append(
                    "LOAD RECONCILIATION (Gate 3) COULD NOT RUN: the site's declared half-hourly "
                    f"series at {declared_ref!r} was found at {resolved} but could not be read: "
                    f"{exc}. The declared load basis has NOT been checked against the series being "
                    "priced. This is a missing check, not a passed one."
                )

    # 3. Assemble: fold HH + populate the ranked data asks into SiteModel.missing_data.
    site: SiteModel
    if hh_summary is not None:
        from pydantic import ValidationError

        try:
            site = assemble_site_model(
                base_site, hh_summary=hh_summary, wholesale_requested=wholesale_requested
            )
        except ValidationError as exc:
            first = exc.errors()[0]["msg"] if exc.errors() else str(exc)
            notes.append(
                f"half-hourly file {hh} not folded (would invalidate the site): {first}. "
                "Proceeding on the originally supplied site."
            )
            site = populate_missing_data(base_site, wholesale_requested=wholesale_requested)
    else:
        site = populate_missing_data(base_site, wholesale_requested=wholesale_requested)

    # 4. Optionally pin the wholesale forecast as gated-upside provenance (never the floor). The
    # raw forecast is also threaded into the engine (issue 018 / D43): it enables the consent-gated
    # export overlay ONLY on a site whose consents cohere, and even then only as gated upside.
    forecast_ref, market_forecast = _load_forecast_reference(
        forecast, forecast_source, forecast_version, notes
    )

    # 5. Load the versioned assumption library. The default is the NEWEST release, resolved from
    # disk — never a hard-coded version (F-15: every default run priced against gb_v0_1 long after
    # gb_v1_6 shipped, showing a Capacity Market line the library's own audit says was overstated
    # ~2.3-4x). Choosing a superseded release explicitly is allowed, but it says so loudly.
    from gridq.assumptions import available_versions, latest_version

    try:
        if assumptions_version is None:
            assumptions_version = latest_version()
        else:
            known = available_versions()
            if known and assumptions_version in known and assumptions_version != known[-1]:
                notes.append(
                    f"Priced against SUPERSEDED assumption release {assumptions_version} "
                    f"(newest is {known[-1]}). Superseded releases carry known-stale values — "
                    "gb_v0_1 in particular overstates Capacity Market de-rating. Re-run without "
                    "--assumptions to price against the current library."
                )
        assumptions = load_assumptions(assumptions_version)
    except AssumptionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    # 6. Engine → finance → stamped quote + telemetry (degrades honestly). The real MILP engine
    # (linopy/HiGHS) is the default for `run`; the integration-only BaselineEngine remains the
    # fallback used by tests/integration. On gb_v0_1 placeholders the quote is still not_bankable.
    # A supplied wholesale forecast is passed to the engine as the export-overlay price series.
    # The MID delta is gated upside; the agreed export tariff on a consented site is priced into
    # the FLOOR as CONTRACTED (N22 — see `_load_forecast_reference`), and the run warns when that
    # happened. Absent a forecast the engine is byte-identical to before.
    run_result = generate_quote(
        site,
        assumptions,
        engine=MilpEngine(market_forecast=market_forecast),
        forecast_ref=forecast_ref,
        contributor=contributor,
        extra_warnings=notes,
        # Gate 3's measured side, resolved in step 2b. Previously omitted, which is what let the
        # mandatory reconciliation gate be silently un-run whenever the pipeline's own cwd-relative
        # read of the declared ref missed.
        hh_summary=gate3_summary,
        lender_terms=lender_terms,
        lifecycle=lifecycle,
    )

    # 7. Persist the artifacts (always; --out overrides the location).
    out_dir = out if out is not None else Path("runs") / run_result.quote.quote_id
    out_dir.mkdir(parents=True, exist_ok=True)
    write_dpa_sidecar(out_dir / DPA_SIDECAR, permit)
    (out_dir / "quote.json").write_text(
        run_result.quote.model_dump_json(indent=2), encoding="utf-8"
    )
    (out_dir / "telemetry.json").write_text(
        run_result.telemetry.model_dump_json(indent=2), encoding="utf-8"
    )

    # 7b. Generate the three M8 documents from the run record (numbers injected; prose LLM-free).
    from gridq.outputs import generate_for_run

    artifacts = generate_for_run(run_result)
    (out_dir / "memo.md").write_text(artifacts.memo_markdown, encoding="utf-8")
    (out_dir / "proposal.md").write_text(artifacts.proposal_markdown, encoding="utf-8")
    (out_dir / "report.html").write_text(artifacts.report_html, encoding="utf-8")
    # The client-facing feasibility validation follows the run automatically: a resolved run
    # produces its deliverable rather than leaving it to a later hand-assembly step.
    (out_dir / "consulting_report.html").write_text(
        artifacts.consulting_report_html, encoding="utf-8"
    )

    # 8. Present.
    if json_out:
        console.print_json(run_result.quote.model_dump_json())
    else:
        _render_run_summary(run_result, out_dir)


@app.command()
@_dpa_refusals
def render(
    quote_json: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        dir_okay=False,
        help="Path to a saved QuoteRecord JSON (e.g. runs/.../quote.json).",
    ),
    site_json: Path | None = typer.Option(  # noqa: B008
        None,
        "--site",
        exists=True,
        dir_okay=False,
        help="Optional SiteModel JSON for the counterparty / consents / gate sections.",
    ),
    out: Path | None = typer.Option(  # noqa: B008
        None,
        "--out",
        file_okay=False,
        help="Directory for memo.md / proposal.md / report.html (default: alongside the quote).",
    ),
    evidence_dir: Path | None = typer.Option(  # noqa: B008
        None, "--evidence-dir", file_okay=False, help=_EVIDENCE_DIR_HELP
    ),
) -> None:
    """Regenerate the three M8 documents from a saved `QuoteRecord` (numbers injected from it).

    The documents are the site's data: the DPA guard decides before anything is written
    (WP-GRC-05; exit 3 on refusal), and the permit is recorded beside them.
    """
    from gridq.governance.dpa_guard import DPA_SIDECAR, authorise_store, write_dpa_sidecar
    from gridq.outputs import generate_artifacts
    from gridq.schemas import QuoteRecord, SiteModel

    try:
        quote = QuoteRecord.model_validate_json(quote_json.read_text(encoding="utf-8"))
    except ValueError as exc:
        console.print(f"[red]Could not load quote:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    site = None
    if site_json is not None:
        try:
            site = SiteModel.model_validate_json(site_json.read_text(encoding="utf-8"))
        except ValueError as exc:
            console.print(
                f"[yellow]Site not used ({exc}); rendering without site context.[/yellow]"
            )

    _use_evidence_dir(evidence_dir)
    permit = authorise_store(quote.site_id)
    artifacts = generate_artifacts(quote, site)
    out_dir = out if out is not None else quote_json.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    write_dpa_sidecar(out_dir / DPA_SIDECAR, permit)
    (out_dir / "memo.md").write_text(artifacts.memo_markdown, encoding="utf-8")
    (out_dir / "proposal.md").write_text(artifacts.proposal_markdown, encoding="utf-8")
    (out_dir / "report.html").write_text(artifacts.report_html, encoding="utf-8")
    (out_dir / "consulting_report.html").write_text(
        artifacts.consulting_report_html, encoding="utf-8"
    )
    console.print(
        f"Generated memo.md, proposal.md, report.html, consulting_report.html in "
        f"[bold]{out_dir}[/bold] from quote {quote.quote_id}"
        + ("" if site is not None else " (no site context — counterparty/consents/gates omitted)")
    )


def _resolve_declared_hh_ref(declared_ref: str, site_json: Path) -> Path | None:
    """Find the file a site's `load_data.half_hourly_file_ref` names, or ``None`` if absent.

    A site file records its series as a **repo-relative** path (``data/sites/hh/....csv``), and the
    only base the pipeline ever tried was the **process cwd**. Run from anywhere but the repo root,
    a perfectly valid site silently lost its measured series and Gate 3 came back ``None`` — the
    same "un-run gate looks like a passed gate" shape as the C-6 defect.

    Candidate bases, in this order:

    1. the working directory, so a run from the repo root behaves exactly as it always did;
    2. the directory holding the site file;
    3. each ancestor of the site file, which is what finds a repo-relative ref when the site JSON
       lives several directories down (``examples/real/sites/x.json`` → ``<repo>/data/sites/hh/``).

    An absolute ref is used as given. The walk is bounded by the filesystem root and touches nothing
    but ``is_file`` checks, and returning ``None`` is an honest "not found" the caller must report —
    never a silently skipped gate.
    """
    candidate = Path(declared_ref)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    site_json = site_json.resolve()
    seen: set[Path] = set()
    for base in (Path.cwd().resolve(), site_json.parent, *site_json.parents):
        if base in seen:
            continue
        seen.add(base)
        resolved = base / candidate
        if resolved.is_file():
            return resolved
    return None


def _load_forecast_reference(
    forecast: Path | None,
    source: str | None,
    version: str | None,
    notes: list[str],
):  # -> tuple[ForecastReference | None, MarketForecast | None]
    """Load + hash-pin a wholesale forecast; return ``(ForecastReference, MarketForecast)`` or None.

    The `ForecastReference` pins it for provenance; the raw `MarketForecast` is handed to the
    engine as the export-overlay price series (issue 018 / D43), used only on a consent-coherent
    site. If the file cannot be read or lacks the metadata to pin it honestly (source / version /
    production date — never invented), the run continues without either and says so.

    **A forecast is not purely gated upside** (N22). The Elexon-MID delta is: it stays on the
    WHOLESALE_P415 gated-upside entry and never reaches the floor. But the same overlay is what
    MEASURES the exported volume, and on a site consented to WHOLESALE_P415 with a declared export
    tariff, `MilpEngine._final_result` prices ``agreed tariff x that volume`` into `value_streams`
    as CONTRACTED — inside `_PRICED_FIRMNESS`, so finance sums it into P50/P90, the DSCR and both
    IRRs. Supplying a forecast can therefore raise the priced floor, and the engine records a
    warning on the quote naming the amount whenever it did. This docstring previously asserted the
    opposite of what the engine does.
    """
    if forecast is None:
        return None, None
    from gridq.ingestion import (
        MarketForecastError,
        load_market_forecast_csv,
        load_market_forecast_json,
    )
    from gridq.scenarios.forecast_reference import forecast_reference_from_market_forecast

    loader = (
        load_market_forecast_json
        if forecast.suffix.lower() == ".json"
        else (load_market_forecast_csv)
    )
    try:
        fc = loader(forecast, source=source, forecast_version=version)
        return forecast_reference_from_market_forecast(fc), fc
    except (MarketForecastError, ValueError) as exc:
        notes.append(
            f"forecast file {forecast} not pinned as gated-upside provenance ({exc}); "
            "supply --forecast-source/--forecast-version or a file that records them."
        )
        return None, None


def _dscr_summary_line(quote: object) -> str:
    """The min DSCR as a value with its basis, or "unavailable: <reason>" (never a bare None)."""
    from gridq.schemas import QuoteRecord

    assert isinstance(quote, QuoteRecord)
    fc = quote.financial_case
    if fc is None:
        return "unavailable: no financial case on this quote (see the warnings)"
    d = fc.dscr_summary
    if d.min_dscr is None:
        return f"unavailable: {d.unavailable_reason or 'no reason recorded'}"
    line = f"{d.min_dscr:.2f}x ({d.basis})"
    cov = fc.covenant_status
    if cov.passes is not None and cov.covenant is not None:
        line += f" · covenant {cov.covenant:g}x {'met' if cov.passes else 'BREACHED'}"
    if cov.lock_up_passes is not None and cov.lock_up_dscr is not None:
        line += f" · lock-up {cov.lock_up_dscr:g}x {'clear' if cov.lock_up_passes else 'TRIGGERED'}"
    return line


def _render_run_summary(run_result: object, out_dir: Path) -> None:
    """Render a quote run as a readable terminal summary."""
    from gridq.quotes import QuoteRunResult

    assert isinstance(run_result, QuoteRunResult)
    quote = run_result.quote
    result = run_result.result

    table = Table(title=f"GridQ run — {quote.quote_id}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("quote_mode", quote.quote_mode.value)
    table.add_row("site_id", quote.site_id)
    table.add_row("data_tier", quote.data_tier.value)
    bankable = result.bankable_status.value
    table.add_row(
        "bankable_status",
        f"[red]{bankable}[/red]" if bankable == "not_bankable" else bankable,
    )
    table.add_row("implementation_level", result.implementation_level.value)
    table.add_row("solver_status", result.solver_status.value)
    table.add_row("min DSCR", _dscr_summary_line(quote))
    lender = quote.financial_case.lender_terms if quote.financial_case is not None else None
    table.add_row("lender terms", lender.label() if lender is not None else "none supplied")
    life = quote.financial_case.lifecycle_inputs if quote.financial_case is not None else None
    table.add_row("lifecycle inputs", life.label() if life is not None else "none supplied")
    table.add_row("input_hash", quote.input_hash or "—")
    table.add_row("assumption_version", quote.assumption_version or "—")
    table.add_row("code_version", quote.code_version or "—")
    if run_result.forecast_ref is not None:
        ref = run_result.forecast_ref
        table.add_row(
            "forecast (gated upside)",
            f"{ref.source} {ref.forecast_version} · verified={ref.verified}",
        )
    table.add_row("artifacts", str(out_dir))
    console.print(table)

    if run_result.site.missing_data:
        asks = Table(title="Ranked data asks (supply in this order to improve the quote)")
        asks.add_column("#", justify="right")
        asks.add_column("Ask")
        for line in run_result.site.missing_data:
            num, _, rest = line.partition(". ")
            asks.add_row(num, rest or line)
        console.print(asks)

    if quote.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warning in quote.warnings:
            console.print(f"  • {warning}")

    console.print(
        f"[dim]telemetry: {run_result.telemetry.event_type} · "
        f"tier={run_result.telemetry.data_tier.value if run_result.telemetry.data_tier else '—'} · "
        f"missing_data_count={run_result.telemetry.missing_data_count}[/dim]"
    )


def main() -> None:
    """Console-script entry point (``gridq``)."""
    app()


if __name__ == "__main__":
    main()
