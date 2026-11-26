"""Assemble a stamped, traceable `QuoteRecord` from a finished run.

This is the join point of the pipeline: it binds a `SiteModel`, the engine's
`OptimisationResult` and (when one exists) the finance `FinancialCase` into a single
`QuoteRecord`, and **stamps** it with provenance via `build_run_context(...)` so the quote
pins its exact inputs + assumption/code/schema versions (X.2). The flat
`input_hash`/`assumption_version`/`code_version` fields on the record are populated from that
run context, and the `quote_id` is derived deterministically from it (identical inputs →
identical id), mirroring how `FinancialCase.case_id` is derived in the finance model.

No pricing or optimisation logic lives here, and nothing is invented: the record only reflects
what the run produced. A baseline run is always carried as `not_bankable` with honest warnings.

Kept deliberately free of pandas / heavy imports so the quote layer stays light.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from gridq.assumptions import AssumptionLibrary
from gridq.extensions import Registration, UnknownExtension, study_bases
from gridq.provenance import build_run_context
from gridq.quotes.disclosures import record_disclosures
from gridq.schemas import (
    SCHEMA_VERSION,
    BankableStatus,
    ConfidenceBand,
    CustomerBill,
    DispatchProfile,
    EvidenceLink,
    FinancialCase,
    HalfHourlyBasis,
    NamedBasis,
    OptimisationResult,
    QuoteMode,
    QuoteRecord,
    ScenarioSensitivity,
    SiteModel,
    SizingGrid,
)

if TYPE_CHECKING:
    from gridq.scenarios.forecast_reference import ForecastReference


# The honest disclaimer carried on every quote that is not a bankable candidate. Engine-agnostic:
# it holds whether the run used the integration placeholder (prices nothing) or the real MILP on
# placeholder assumptions (prices real revenue but cannot be bankable until the
# assumptions/debt/covenant are confirmed). Phrased to avoid the review-gate smell strings (it must
# never read as a spoofed bankable claim).
#
# N30: the status value is INTERPOLATED, not hard-coded. This note is appended for every status
# other than BANKABLE_CANDIDATE, which includes REVIEW_REQUIRED — the status
# `MilpDispatchSolver._bankable_status` returns once every used assumption is confirmed. The old
# literal `bankable_status=not_bankable` therefore stated a field value the record itself
# contradicted. It is accurate today only because the shipped release has zero confirmed
# assumptions; it becomes a false claim about the record the moment one is confirmed.
def _not_bankable_note(status: BankableStatus) -> str:
    return (
        f"This quote is NOT bankable: bankable_status={status.value}. It is indicative only — the "
        "run uses placeholder assumptions and/or no real debt/covenant case is established. Do "
        "not present to capital as bankable."
    )


def _canonical_site_dump(site: SiteModel) -> dict[str, object]:
    """The site half of the canonical hash input, timestamp-free and growth-stable.

    D42 hash policy: additive optional SiteModel fields are POPPED when None so every
    pre-existing site's input_hash / run_id stays byte-stable across schema growth. A SET
    value stays in the dump — a real input must enter the identity.
    """
    dump = site.model_dump(mode="json", exclude={"created_at"})
    if dump.get("provisioning_requirements") is None:
        dump.pop("provisioning_requirements", None)
    if dump.get("export_arrangement") is None:
        dump.pop("export_arrangement", None)
    if dump.get("stream_consents") is None:
        dump.pop("stream_consents", None)
    # D44: recorded derived inputs are real inputs — two runs on different derived connection
    # capacities must never share a run_id — so a SET list enters the identity. Absent stays popped,
    # which is what keeps every pre-D44 site's input_hash byte-identical. An EMPTY list is popped
    # too: it carries the same information as absent (nothing was derived), and `model_copy` does
    # not re-run the schema's normalising validator, so the empty case is closed here too —
    # otherwise `[]` would silently shift the hash for no information at all.
    if not dump.get("derived_assumptions"):
        dump.pop("derived_assumptions", None)
    # D54: an attested local-flex award is a real input — it opens the gate, reserves dispatch
    # headroom and puts £ into the priced floor — so a SET list MUST enter the identity, and two
    # runs on different awards must never share a run_id. Absent/empty stays popped, which is what
    # keeps every pre-D54 site's input_hash byte-identical.
    if not dump.get("local_flex_awards"):
        dump.pop("local_flex_awards", None)
    return dump


#: Fields of `SolverMetadata` that are RUN METADATA, not inputs, and are excluded from the canonical
#: hash input (see `_canonical_result_dump`). Each moves without any input or any deterministic
#: output moving, which is the discriminator:
#:
#: * ``code_version`` — read from git HEAD on **every call** (`MilpEngine._metadata`), so it
#:   moved `input_hash` on every commit. This is the defect the exclusion exists for.
#: * ``solve_seconds`` — wall-clock timing. The same "timestamps are metadata" rule that excludes
#:   ``created_at``; `engine.conformance._result_dump` already excludes it from the determinism
#:   comparison for the same reason.
#: * ``reproduction_env`` — the machine/environment the solve ran on, not an input to it.
#:
#: ``seed`` (a genuine solver input), ``model_version`` (the dispatch formulation's hand-bumped
#: identifier — it moves only when someone deliberately moves it) and ``assumption_version`` STAY:
#: two runs that differ in any of them are genuinely two different runs.
_SOLVER_METADATA_NON_INPUTS = frozenset({"code_version", "solve_seconds", "reproduction_env"})


def _canonical_result_dump(result: OptimisationResult) -> dict[str, object]:
    """The result half of the canonical hash input, stripped of per-run/per-commit metadata.

    ``code_version`` is the load-bearing exclusion. `SolverMetadata.code_version` is read from git
    HEAD on every call, so leaving it in the hash input made ``input_hash`` move on **every commit**
    even when not one input had changed — which destroys the only question ``input_hash`` exists to
    answer. ``run_id`` derives from ``(input_hash, assumption_version, code_version,
    schema_version)`` precisely so those four pins are *independent*; an ``input_hash`` that already
    contains ``code_version`` cannot separate "same inputs, new code" from "the inputs changed".

    ``solve_seconds`` and ``reproduction_env`` go with it (`_SOLVER_METADATA_NON_INPUTS`): both are
    ``None`` on every production path today, so excluding them is free **now** and would cost a
    second break of a frozen contract later.
    """
    return result.model_dump(
        mode="json",
        exclude={
            "created_at": True,
            "solver_metadata": dict.fromkeys(_SOLVER_METADATA_NON_INPUTS, True),
        },
    )


#: WP-UW-03 (D78): derived additive finance fields kept out of the canonical hash input. See
#: `_canonical_financial_case_dump`. ``case_id`` is the original exclusion (derived identity).
_FINANCIAL_CASE_DERIVED_ECHOES: dict[str, bool | set[str]] = {
    "case_id": True,
    "dscr_summary": {"status", "unavailable_reason", "cashflow_case"},
    "covenant_status": {"source", "lock_up_dscr", "lock_up_passes", "not_evaluated_reason"},
    "debt_assumptions": {"principal_gbp", "sized_by"},
    # WP-UW-17 (D132): the derived lifecycle record and the three derived price fields are
    # determined by inputs this hash already carries (the lifecycle inputs, the library version and
    # the series), so they are echoes under the same rule. `lifecycle_inputs` is an INPUT and stays.
    "lifecycle": True,
    "price_recommendation": {
        "levelisation_basis",
        "measured_irr_with_price_pct",
        "equity_closed_loop_gbp_per_kwh",
    },
}


def _canonical_financial_case_dump(financial_case: FinancialCase) -> dict[str, object]:
    """The finance half of the canonical hash input, stripped of its derived identity.

    ``case_id`` is ``fc_`` + the finance `RunContext`'s ``run_id`` (minted in
    `finance.financial_model.run_financial_model`), and that ``run_id`` binds ``code_version``. So
    the case id was a **second, independent** route by which the git commit reached ``input_hash`` —
    and the one that actually bit in production, since every real run carries a `FinancialCase`.
    Excluding only
    `SolverMetadata.code_version` would have left ``input_hash`` moving every commit exactly as
    before.

    The general rule, and the reason this is not a special case: **a derived identity must never
    feed another computed identity.** It is circular, and it silently re-imports every volatile pin
    its parent had. The same rule already excludes ``created_at`` above, and it is why
    `tests/test_determinism_golden.py` classes ``case_id``/``quote_id``/``run_id``/``input_hash``
    together as identity metadata rather than as outputs.

    Nothing the case *computed* is lost: the cashflow series, DSCR, IRR, debt assumptions, price
    recommendation and covenant status all stay in the dump, so any finance input that changes a
    number still changes the hash.

    **WP-UW-03 (D78).** The fields that package added to existing finance contracts are excluded
    (`_FINANCIAL_CASE_DERIVED_ECHOES`): each is a status, a reason, a source label or an echo that
    is fully determined by inputs this hash already carries (the DSCR figures, the debt
    assumptions, and the lender terms themselves). Every run populates them, so leaving them in
    would re-mint ``input_hash``/``quote_id`` for every historical run while no input moved. The
    one new INPUT, ``lender_terms``, stays in under the D42 pop-if-None rule: absent on every run
    that supplied none, so those identities are byte-identical, and present on every run that
    did, so two different term sheets can never share a quote.
    """
    dump = financial_case.model_dump(mode="json", exclude=_FINANCIAL_CASE_DERIVED_ECHOES)
    # D42 pop-if-None for each real INPUT: absent on every run that supplied none, so those
    # identities are byte-identical; present on every run that did (WP-UW-03 lender terms,
    # WP-UW-17 lifecycle inputs).
    for input_key in ("lender_terms", "lifecycle_inputs"):
        if dump.get(input_key) is None:
            dump.pop(input_key, None)
    return dump


def _basis_registration(study_basis: NamedBasis) -> Registration[type[NamedBasis]]:
    """The chassis registration of the basis a run was priced on, or a refusal (IC-12 2.0.0).

    A basis reaches a record only if it is registered, and only as an instance of the class
    registered under its id. That is the rule `STUDY_BASIS_CLASSES` existed to keep ("a basis no
    consumer can label must not reach a report"), now kept for a basis from any package: the
    registry is where every renderer finds the banner (`schemas.study.study_basis_label`).
    """
    try:
        registration = study_bases.registration(study_basis.basis_id)
    except UnknownExtension:
        raise ValueError(
            f"study basis {study_basis.basis_id!r} is not registered, so no consumer could label "
            "a record priced on it. Register the basis class through gridq.extensions."
        ) from None
    if type(study_basis) is not registration.item:
        raise ValueError(
            f"study basis {study_basis.basis_id!r} is registered as "
            f"{registration.item.__name__}, but the run carries a {type(study_basis).__name__}."
        )
    return registration


def _canonical_run_input(
    site: SiteModel,
    result: OptimisationResult,
    financial_case: FinancialCase | None,
    assumptions: AssumptionLibrary,
    forecast_content_hash: str | None,
    study_basis: NamedBasis | None = None,
    scenario_id: str | None = None,
) -> dict[str, object]:
    """Build the canonical, timestamp-free hash input that pins the whole quote.

    Mirrors `finance.financial_model._hash_input`: every model is dumped with
    ``mode="json"`` (enums → values, dates → ISO strings) and the top-level ``created_at`` is
    excluded, because timestamps are metadata and must never feed a computed identity. The
    forecast is pinned by its ``content_hash`` only (never the whole `ForecastReference`, whose
    ``verified``/``warnings`` fields mutate and would make the pin non-reproducible).

    **The same principle, applied where it had been missed.** ``code_version`` and the derived
    ``case_id`` were both entering this hash and both move on every commit, so ``input_hash`` moved
    on every commit while the inputs stood still — see `_canonical_result_dump` and
    `_canonical_financial_case_dump` for the two routes and why each is metadata rather than an
    input. Removing them **re-mints ``input_hash``/``quote_id``/``run_id`` for every existing run**;
    that is a deliberate, one-off frozen-contract break (CLAUDE.md §7), and it is the point of the
    change rather than a side effect of it. It is bundled — both routes plus the two always-``None``
    metadata fields — so the contract breaks once, not four times.

    **D46 — the pricing BASIS is part of the run's identity.** Two bases must never share a
    ``run_id``: the same site, library and code version priced with export in the floor and priced
    with export as gated upside are two different runs, and a reader who cannot tell them apart by
    identity has only prose between them. So a declared `StudyBasis` enters the hash, carrying the
    derived tariff and export limit it priced at. It follows the C033/D42 pop-if-None policy — the
    key is **absent** (not ``None``) on an underwriting run — so every existing run identity is
    byte-identical across this schema growth, which `tests/test_study_basis_hash_identity.py` pins.

    **``scenario_id`` is an INPUT, and enters DIRECTLY — which is not a contradiction of the
    ``case_id`` exclusion above, but the same rule applied correctly.** The discriminator this
    docstring states is *derived identity may never feed a computed identity*, and the two records
    sit on opposite sides of it. ``case_id`` is minted BY a run and binds whatever its parent
    `RunContext` bound, ``code_version`` included, so hashing it re-imports a pin that has nothing
    to do with the inputs. ``scenario_id`` is declared by the CALLER before the run: it says which
    case the run was *asked* to price, exactly as ``assumption_version`` says which library it was
    asked to price against, and it moves only when someone deliberately moves it.

    It closes an accepted residual of 9c07c5b. Removing ``case_id`` was right, but ``case_id`` was
    the ONLY route by which the scenario reached this hash, so two scenarios that differ in name
    alone (identity multipliers — precisely the shipped Low/Central/High set, see
    `quotes.sensitivities`) collapsed onto one ``input_hash`` and therefore one ``quote_id``. That
    is the milder of the two identity failures — two labels over one identical result, not one
    identity over two different numbers (N25/MD-F-14) — but it is still a collapse, and threading
    the input directly fixes it without re-importing the commit. Same pop-if-None policy: on a run
    with no scenario the key is **absent**, so no existing identity moves.

    **A basis from another package also pins its package (IC-12 2.0.0).** The basis dump carries
    the ``basis_id``, which is enough for a built-in: its code is this repository's, pinned by
    ``code_version``. A basis registered by a plugin is code from another distribution, so its
    registration's owner and version enter the hash under ``study_basis_extension``. The key is
    absent for a built-in basis and for no basis, so no existing identity moves.
    """
    canonical: dict[str, object] = {
        "site": _canonical_site_dump(site),
        "result": _canonical_result_dump(result),
        "financial_case": (
            _canonical_financial_case_dump(financial_case) if financial_case is not None else None
        ),
        "assumption_version": assumptions.metadata.version,
        "forecast_content_hash": forecast_content_hash,
        "study_basis": (study_basis.model_dump(mode="json") if study_basis is not None else None),
        "scenario_id": scenario_id,
    }
    if canonical.get("study_basis") is None:
        canonical.pop("study_basis", None)
    if study_basis is not None:
        registration = _basis_registration(study_basis)
        if not registration.builtin:
            canonical["study_basis_extension"] = {
                "owner": registration.owner,
                "version": registration.version,
            }
    if canonical.get("scenario_id") is None:
        canonical.pop("scenario_id", None)
    return canonical


def _merged_evidence_links(
    site: SiteModel, result: OptimisationResult
) -> tuple[EvidenceLink, ...] | None:
    """The site's and the result's evidence links as one de-duplicated tuple, or ``None``."""
    merged: dict[tuple[str, str], EvidenceLink] = {}
    for link in (*(site.evidence_refs or ()), *(result.evidence_refs or ())):
        merged.setdefault((link.supports, link.evidence.evidence_hash), link)
    return tuple(merged.values()) or None


def _recomputed_ask_count(site: SiteModel) -> int | None:
    """How many ranked asks the detector derives from this site, or ``None`` if it cannot run.

    The pack's ranked-ask exhibit shows this recomputed list, while ``site.missing_data`` holds the
    hand-authored one. Counting both is what lets the record say they are two lists instead of
    presenting one as the other (see `disclosures.ask_list_disclosure`). Imported lazily: assembly
    stays free of heavy imports, and a detector failure must never break quote assembly.
    """
    try:
        from gridq.ingestion.improvement_asks import build_ranked_data_asks

        return len(build_ranked_data_asks(site))
    except Exception:  # noqa: BLE001 — a diagnostic count is never worth failing a run for
        return None


def _quote_warnings(
    site: SiteModel,
    result: OptimisationResult,
    financial_case: FinancialCase | None,
    extra_warnings: list[str] | None,
    sizing_grid: SizingGrid | None = None,
) -> list[str]:
    """Aggregate honest, de-duplicated warnings from every layer of the run.

    Also carries the record's own **disclosures** (`gridq.quotes.disclosures`): a conflict between
    two accounts of the same verdict, a verdict claim no persisted figure evidences, and every
    ``BLOCKING`` data ask stated in full. Those used to be either absent from the record or
    compressed into the ranked-ask *count* below, which is how a blocking ask and a contradicted
    capacity note both failed to reach the reader of a finished pack.

    The count line is kept — it is still the useful summary — but it no longer stands in for the
    asks it counts, and it now says which of the two ask lists it is counting.
    """
    warnings: list[str] = []
    warnings.extend(result.warnings)
    if financial_case is not None:
        warnings.extend(financial_case.warnings)
    # Disclosures precede the tally deliberately: a conflict or a blocking ask outranks a count.
    for disclosure in record_disclosures(
        site, result, sizing_grid, recomputed_ask_count=_recomputed_ask_count(site)
    ):
        warnings.append(disclosure.text)
    if site.missing_data:
        warnings.append(
            f"{len(site.missing_data)} ranked data ask(s) outstanding on the site record — see "
            "SiteModel.missing_data, reproduced above where marked BLOCKING; supply them to "
            "improve the quote."
        )
    if result.bankable_status != BankableStatus.BANKABLE_CANDIDATE:
        warnings.append(_not_bankable_note(result.bankable_status))
    if extra_warnings:
        warnings.extend(extra_warnings)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(warnings))


def assemble_quote_record(
    site: SiteModel,
    result: OptimisationResult,
    financial_case: FinancialCase | None,
    assumptions: AssumptionLibrary,
    *,
    quote_mode: QuoteMode = QuoteMode.DRAFT,
    forecast_ref: ForecastReference | None = None,
    expiry_date: date | None = None,
    extra_warnings: list[str] | None = None,
    sizing_grid: SizingGrid | None = None,
    dispatch_profile: DispatchProfile | None = None,
    scenario_sensitivities: list[ScenarioSensitivity] | None = None,
    customer_bill: CustomerBill | None = None,
    provisioning_opportunity_cost_gbp: float | None = None,
    half_hourly_basis: HalfHourlyBasis | None = None,
    confidence_ceiling: ConfidenceBand | None = None,
    confidence_band: ConfidenceBand | None = None,
    confidence_binding_factors: list[str] | None = None,
    study_basis: NamedBasis | None = None,
    scenario_id: str | None = None,
) -> QuoteRecord:
    """Bind a finished run into a stamped `QuoteRecord`.

    Builds a quote-level `RunContext` over the canonical (timestamp-free) run input, copies its
    ``input_hash``/``assumption_version``/``code_version`` onto the flat record fields, and
    derives a deterministic ``quote_id`` from it. ``financial_case`` is ``None`` on a degraded
    run (no positive sizing to price) — the record is still produced, as a `not_bankable` quote
    with the outstanding data asks surfaced in ``warnings``.

    The default ``quote_mode`` is ``DRAFT``: a baseline run is never assembled as ``underwritten``
    (the engine is plumbing-only and stays ``not_bankable``).

    ``sizing_grid`` (W5-0) is the optional full evaluated power×duration grid for the 3D sizing
    surface, ``dispatch_profile`` (W5-2) the optional representative dispatch day for the
    dispatch chart, and ``customer_bill`` (W7-5, C017) the optional bill before/after
    decomposition for the proposal headline — each persisted onto the record as-is. None is part
    of the canonical hash input, so passing them does not change ``input_hash``/``quote_id`` —
    all three fields are additive and backward-compatible.

    ``half_hourly_basis`` / ``confidence_ceiling`` / ``confidence_band`` /
    ``confidence_binding_factors`` (N21) are the validation Gate-2 verdict and the assessed
    evidential-strength band, persisted so the lifecycle gate can **read** them instead of a
    reader having to parse them out of a warning string. Like the three above they are additive,
    excluded from the canonical hash input, and default to ``None`` for callers that do not run
    the gate (tests, direct API users) — an absent basis is honestly absent, never inferred.

    ``study_basis`` (D46) is the named FEASIBILITY-STUDY basis the run was priced on, available from
    `MilpEngine.last_study_basis()`. It is **hash-bearing**, unlike the additive fields above:
    two bases must never share a ``run_id``. ``None`` — every underwriting run — is popped from the
    canonical input, so passing nothing leaves every existing ``input_hash``/``quote_id``
    byte-identical (C033/D42 pop-if-None).

    ``scenario_id`` (the declared id of the `Scenario` the `financial_case` was priced under, read
    from `finance.FinancialModelResult.scenario_id`) is hash-bearing on the same terms and for the
    same reason: two named cases must never share a ``quote_id``. See `_canonical_run_input` for why
    a caller-declared scenario id belongs in the hash while the finance-minted ``case_id`` does not.

    .. note::
       **``study_basis`` IS wired to production** (D55, 2026-08-18).
       `quotes.pipeline.generate_quote` reads it off the engine through the duck-typed
       ``last_study_basis()`` hook and forwards it here, so every study run is identified by the
       basis that actually priced it and no caller has to remember to declare it twice.
       `tests/test_all_streams_study_basis.py` pins the production path (and fails if the forward is
       dropped), and the same call site now caps the record's confidence band at the basis's own
       ceiling.

       Worth knowing for anyone reasoning about identity here: the basis was **already**
       hash-bearing before that wiring, but only through PROSE — `MilpEngine` writes
       ``study_basis.note()`` as the first entry of `OptimisationResult.warnings`
       (`optimisation/engine.py:2218`) and
       `_canonical_result_dump` hashes the whole result, so two bases never actually collided
       (measured 2026-08-18). What the wiring adds is a **structured** ``basis_id`` in the canonical
       input instead of an identity that depends on the wording of a sentence.

    .. warning::
       **``scenario_id`` still has no production caller** (re-verified 2026-08-18).
       `quotes.pipeline.generate_quote` has no ``scenario`` parameter to forward, so it always
       prices the base case. It is therefore **reachable plumbing, not an enforced pin** — do not
       read a
       passing test on it as evidence that a real run is identified by it
       (validation_requirements.md §3). Wiring it needs ``generate_quote`` to take a ``scenario``,
       forward it to `run_financial_model`, then pass ``scenario_id=model.scenario_id`` here. Delete
       this warning when that lands.
    """
    forecast_content_hash = forecast_ref.content_hash if forecast_ref is not None else None
    run_context = build_run_context(
        _canonical_run_input(
            site,
            result,
            financial_case,
            assumptions,
            forecast_content_hash,
            study_basis,
            scenario_id,
        ),
        assumptions.metadata.version,
        SCHEMA_VERSION,
    )

    return QuoteRecord(
        quote_id=f"quote_{run_context.run_id}",
        quote_mode=quote_mode,
        site_id=site.site_id,
        input_hash=run_context.input_hash,
        assumption_version=run_context.assumption_version,
        code_version=run_context.code_version,
        optimisation_result=result,
        financial_case=financial_case,
        sizing_grid=sizing_grid,
        dispatch_profile=dispatch_profile,
        scenario_sensitivities=list(scenario_sensitivities) if scenario_sensitivities else [],
        customer_bill=customer_bill,
        provisioning_opportunity_cost_gbp=provisioning_opportunity_cost_gbp,
        # D46/D55: the basis, persisted machine-readably on the record. The whole `StudyBasis`
        # already enters `input_hash` above, but a hash answers "was this the same run?" and never
        # "which basis priced it?" — so a consumer holding a finished record had no structured basis
        # field to branch on and every renderer sniffed `StudyBasis.note()` out of the warnings
        # prose instead. `study_basis_id` is that field. It is also what caps the confidence BAND:
        # `quotes.confidence._study_basis_factor` reads it off the record, so the "capped at LOW"
        # sentence in `StudyBasis.note()` is now enforced by a factor rather than true by
        # coincidence of the Gate-2 admission.
        study_basis_id=study_basis.basis_id if study_basis is not None else None,
        # IC-12 2.0.0: the record names its IC-1 agreement and its IC-4 evidence itself. Both come
        # from the site and the result, which are hashed above; the record adds nothing to them.
        connection_agreement=site.connection.agreement,
        evidence_refs=_merged_evidence_links(site, result),
        half_hourly_basis=half_hourly_basis,
        confidence_ceiling=confidence_ceiling,
        confidence_band=confidence_band,
        confidence_binding_factors=list(confidence_binding_factors or []),
        data_tier=site.data_tier,
        expiry_date=expiry_date,
        status_history=[],
        # The grid is passed so the record's own sizing verdict and the conflict/unevidenced-claim
        # checks can be stated in the warnings; it changes no hashed field.
        warnings=_quote_warnings(site, result, financial_case, extra_warnings, sizing_grid),
    )
