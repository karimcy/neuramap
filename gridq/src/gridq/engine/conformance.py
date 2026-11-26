"""The `OptimisationResult` conformance suite — the contract bar every engine must pass.

`OptimisationResult` is the frozen seam (docs/03_data_contracts.md §3). v1 builds the
whole pipeline against the integration-only `BaselineEngine`; Olly's real MILP (issue 014) must drop
in behind the *same* seam with **no silent drift** — the mock and the real engine both satisfy the
schema + stream registry, and finance/quote/outputs consume the output with no code change.

This module is the executable form of that bar. It is **pytest-free and import-safe** — it depends
only on the frozen contracts (`gridq.schemas`) and the engine interface (`gridq.engine.interface`)
— so Olly can run the *exact same* checks against `gridq.optimisation.MilpEngine`::

    from gridq.assumptions import load_assumptions
    from gridq.engine.conformance import assert_conformant
    assert_conformant(MilpEngine(), assumptions=load_assumptions("gb_v0_1"))

**Scope — deliberately the contract, not the numbers.** The checks here are the invariants that
are provably true of *any honest engine* at the `OptimisationResult` level. They intentionally do
**not** assert specific numbers (the real optimiser legitimately produces different dispatch/value
than the mock — issue 014) nor the half-hourly *dispatch* physics (charge ≤ MIC/ASC, throughput ≤
warranty cap): that schedule lives behind `dispatch_schedule_ref`, off the result contract, so
those caps belong in the engine's own M3 tests, not in a contract-level conformance bar.

Several checks have **teeth beyond Pydantic** — they catch drift the schema validators do *not*:
a gated-upside stream priced into the floor as ``contracted`` (the model only bars ``firm``),
non-determinism, a result that no longer echoes its request, or a baseline result quietly claiming
``review_required``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from gridq.assumptions import AssumptionLibrary
from gridq.engine.interface import OptimisationEngine
from gridq.schemas import (
    GATED_UPSIDE_STREAMS,
    SCHEMA_VERSION,
    BankableStatus,
    ConnectionDetails,
    ConsentState,
    Counterparty,
    DataTier,
    Firmness,
    ImplementationLevel,
    LoadDataSummary,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    PhysicalGateStatus,
    SiteModel,
    SolverStatus,
    TariffBasis,
    TariffProvenance,
    TariffStack,
    ValueStreamResult,
)

__all__ = [
    "ConformanceError",
    "ConformanceResult",
    "ConformanceReport",
    "PRICED_FLOOR_FIRMNESS",
    "canonical_conformance_sites",
    "check_result_conformance",
    "check_engine_conformance",
    "assert_conformant",
]

# The "priced floor" is the firmness set finance counts as real revenue (docs/03_data_contracts.md
# §3; finance `_PRICED_FIRMNESS`, OPEN_COMMENTS C014/W2-2). Conditional + gated streams are upside,
# held out of the floor. A gated-upside stream must never reach this set.
PRICED_FLOOR_FIRMNESS: frozenset[Firmness] = frozenset({Firmness.FIRM, Firmness.CONTRACTED})

# Solver statuses that mean "no system was sizeable" — a spec must be absent under these.
_NO_SPEC_STATUSES: frozenset[SolverStatus] = frozenset(
    {SolverStatus.INFEASIBLE, SolverStatus.INSUFFICIENT_DATA}
)


@dataclass(frozen=True)
class ConformanceResult:
    """One conformance check and its outcome."""

    check: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class ConformanceReport:
    """The full set of conformance results for one engine, across the canonical sites."""

    engine_label: str
    results: list[ConformanceResult]

    @property
    def ok(self) -> bool:
        """True iff every check passed."""
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[ConformanceResult]:
        """The checks that failed (empty when conformant)."""
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        """A human-readable one-block summary (PASS/FAIL per check)."""
        passed = sum(1 for r in self.results if r.passed)
        head = f"Conformance for {self.engine_label}: {passed}/{len(self.results)} checks passed."
        lines = [head] + [
            f"  {'PASS' if r.passed else 'FAIL'}  {r.check}"
            + (f" — {r.detail}" if r.detail and not r.passed else "")
            for r in self.results
        ]
        return "\n".join(lines)


class ConformanceError(AssertionError):
    """Raised by :func:`assert_conformant` when an engine fails the conformance suite."""


# --------------------------------------------------------------------------------------------------
# Canonical sites — built from the schema (never hand-written JSON), reusable by any engine's tests.
# --------------------------------------------------------------------------------------------------


def _site(
    *,
    site_id: str,
    data_tier: DataTier,
    load_data: LoadDataSummary,
    annual_consumption_kwh: float | None,
) -> SiteModel:
    """Assemble a minimal valid SiteModel; shared base for the canonical conformance set."""
    return SiteModel(
        site_id=site_id,
        postcode="LE17 4XN",
        data_tier=data_tier,
        load_data=load_data,
        annual_consumption_kwh=annual_consumption_kwh,
        # A synthetic stack: its author states the basis, and no component is anyone's own.
        tariff_stack=TariffStack(
            supplier="ConformanceCo",
            import_rates={"single": 0.24},
            basis=TariffBasis(
                vat="excluded",
                vat_attestation="stated_by_record",
                levies="itemised",
                network_charges="itemised",
            ),
            provenance=TariffProvenance(commodity="assumed", network="assumed", levies="assumed"),
        ),
        connection=ConnectionDetails(mic_kw=300.0, mec_kw=150.0),
        consents=ConsentState(),
        physical_gates=PhysicalGateStatus(),
        counterparty=Counterparty(legal_name="Conformance Test Ltd", company_number="01234567"),
    )


def canonical_conformance_sites() -> list[tuple[str, SiteModel]]:
    """Representative, valid SiteModels covering the contract's branches.

    Each `(label, site)` pair exercises a distinct path any engine must handle:

    - ``t2_full`` — a validated 12-month half-hourly site with `max_import_kw` (spec is sizeable).
    - ``t1_partial`` — partial half-hourly with `max_import_kw` (a different tier, still sizeable).
    - ``t0_no_import`` — annual-only, no `max_import_kw` (the insufficient-data / no-spec path).

    Built from the Pydantic models so they stay valid under the strict (`extra="forbid"`) config and
    can be reused verbatim by the real engine's conformance tests.
    """
    return [
        (
            "t2_full",
            _site(
                site_id="site_conf_t2",
                data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
                load_data=LoadDataSummary(
                    annual_kwh=1_800_000.0,
                    half_hourly_rows=17520,
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 12, 31),
                    max_import_kw=420.0,
                    max_export_kw=120.0,
                ),
                annual_consumption_kwh=1_800_000.0,
            ),
        ),
        (
            "t1_partial",
            _site(
                site_id="site_conf_t1",
                data_tier=DataTier.T1_PARTIAL_HALF_HOURLY,
                load_data=LoadDataSummary(
                    annual_kwh=900_000.0,
                    half_hourly_rows=8000,
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 6, 30),
                    max_import_kw=260.0,
                ),
                annual_consumption_kwh=900_000.0,
            ),
        ),
        (
            "t0_no_import",
            _site(
                site_id="site_conf_t0",
                data_tier=DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
                load_data=LoadDataSummary(annual_kwh=120_000.0),
                annual_consumption_kwh=120_000.0,
            ),
        ),
    ]


# --------------------------------------------------------------------------------------------------
# Result-level conformance — the invariants on a single OptimisationResult.
# --------------------------------------------------------------------------------------------------


def _check(name: str, passed: bool, detail: str = "") -> ConformanceResult:
    return ConformanceResult(check=name, passed=passed, detail=detail)


def _result_dump(result: OptimisationResult) -> dict:
    """Determinism-comparable dump of the *substantive* contract content.

    Excludes fields a correct engine may legitimately run-key without breaking the economic
    determinism the contract guarantees: ``created_at`` and ``solver_metadata.solve_seconds``
    (wall-clock), ``result_id`` (an identity/correlation field, often minted per run), and
    ``dispatch_schedule_ref`` (a pointer to the off-contract schedule, which this suite scopes out).
    The priced spec, value streams, firmness, solver status and version metadata are all compared.
    """
    return result.model_dump(
        mode="json",
        exclude={
            "created_at": True,
            "result_id": True,
            "dispatch_schedule_ref": True,
            "solver_metadata": {"solve_seconds": True},
        },
    )


def check_result_conformance(
    result: OptimisationResult, *, request: OptimisationRequest | None = None
) -> list[ConformanceResult]:
    """Run the per-result contract invariants and return the (named) outcomes.

    Usable standalone on any `OptimisationResult` (check a single result without an engine).
    Pass ``request`` to additionally assert the request<->result echo.
    """
    out: list[ConformanceResult] = []

    # Re-validation: a result must survive a round-trip through its own schema. Catches extra-field
    # / enum / model-validator drift, and any result hand-built bypassing construction validation.
    revalidated = True
    detail = ""
    try:
        OptimisationResult.model_validate(result.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001 — any validation failure is a conformance failure
        revalidated = False
        detail = f"result does not re-validate against OptimisationResult: {exc}"
    out.append(_check("result.revalidates", revalidated, detail))

    # Schema-version pin: the frozen seam versions via `schema_version`; a result on a foreign
    # version is drift the bar must catch (docs/03_data_contracts.md "Change control"). When the
    # contract is deliberately bumped, SCHEMA_VERSION and this suite move together.
    out.append(
        _check(
            "contract.schema_version",
            result.schema_version == SCHEMA_VERSION,
            f"result.schema_version={result.schema_version!r} != SCHEMA_VERSION={SCHEMA_VERSION!r}",
        )
    )

    # Provenance: an engine must stamp the run it produced — model_version (which engine),
    # assumption_version and code_version (issue 014 AC; SKILLS "stamp each run", telemetry/
    # provenance ship from the first quote). A result with no solver_metadata is untraceable.
    meta = result.solver_metadata
    stamped = bool(
        meta
        and (meta.model_version or "").strip()
        and (meta.assumption_version or "").strip()
        and (meta.code_version or "").strip()
    )
    out.append(
        _check(
            "provenance.run_stamped",
            stamped,
            "solver_metadata must carry non-empty model_version + assumption_version + "
            f"code_version (got {meta!r})",
        )
    )

    # Request echo: finance/quote correlate result->request by id, so the result must echo it.
    if request is not None:
        out.append(
            _check(
                "result.echoes_request",
                result.request_id == request.request_id,
                f"result.request_id={result.request_id!r} != "
                f"request.request_id={request.request_id!r}",
            )
        )

    all_streams = list(result.value_streams) + list(result.gated_upside)

    # Stream-registry membership (every reported stream is a registry id).
    off_registry = [v.stream for v in all_streams if v.stream not in set(MarketStream)]
    out.append(
        _check(
            "registry.membership",
            not off_registry,
            f"streams not in the MarketStream registry: {off_registry}",
        )
    )

    # One result per stream *within each list* — a duplicate inside value_streams would
    # double-count downstream (finance sums per stream). Checked per-list, not across the union: a
    # gated stream legitimately appearing in gated_upside is not a duplicate of itself.
    def _dupes(rows: list[ValueStreamResult]) -> list[str]:
        ids = [v.stream for v in rows]
        return sorted({s.value for s in ids if ids.count(s) > 1})

    dupes = sorted(set(_dupes(result.value_streams)) | set(_dupes(result.gated_upside)))
    out.append(
        _check(
            "registry.no_duplicate_streams",
            not dupes,
            f"stream(s) reported more than once within value_streams or gated_upside: {dupes}",
        )
    )

    # gated_upside[] holds only gated-upside streams (also a schema validator; pinned explicitly).
    bad_gated = [
        v.stream.value for v in result.gated_upside if v.stream not in GATED_UPSIDE_STREAMS
    ]
    out.append(
        _check(
            "registry.gated_upside_membership",
            not bad_gated,
            f"non-gated-upside stream(s) in gated_upside[]: {bad_gated}",
        )
    )

    # A gated-upside stream is never firm, anywhere (also a ValueStreamResult validator).
    firm_gated = [
        v.stream.value
        for v in all_streams
        if v.stream in GATED_UPSIDE_STREAMS and v.firmness == Firmness.FIRM
    ]
    out.append(
        _check(
            "firmness.gated_never_firm",
            not firm_gated,
            f"gated-upside stream(s) marked firm: {firm_gated}",
        )
    )

    # TEETH: an UNCONSENTED gated-upside stream must never sit in the priced floor
    # (firm|contracted). Pydantic bars only `firm` for gated streams, so a `contracted` gated passes
    # construction but would over-count the floor (OPEN_COMMENTS C014 / W2-2). The ONE exception
    # (Revenue-Data Drive / D43): a stream the customer explicitly consented to may be priced into
    # the floor as CONTRACTED (never FIRM — the `firmness.gated_never_firm` teeth above still bite);
    # such an entry carries a ``consent_status`` basis. An unconsented one is still a leak.
    floored_gated = [
        v.stream.value
        for v in all_streams
        if v.stream in GATED_UPSIDE_STREAMS
        and v.firmness in PRICED_FLOOR_FIRMNESS
        and not v.consent_status
    ]
    floor = sorted(f.value for f in PRICED_FLOOR_FIRMNESS)
    out.append(
        _check(
            "firmness.gated_excluded_from_priced_floor",
            not floored_gated,
            f"UNCONSENTED gated-upside stream(s) in the priced floor {floor}: {floored_gated}",
        )
    )

    # TEETH: a gated-upside stream you cannot price must say what would unlock it (issue 014:
    # "listed as conditional upside with its unlock condition").
    no_unlock = [
        v.stream.value
        for v in result.gated_upside
        if not (v.unlock_condition and v.unlock_condition.strip())
    ]
    out.append(
        _check(
            "firmness.gated_upside_states_unlock",
            not no_unlock,
            f"gated_upside stream(s) without an unlock_condition: {no_unlock}",
        )
    )

    # TEETH: a baseline-implementation result is never bankable (stronger than the schema, which
    # only bars `bankable_candidate`) — the "never spoof a real engine" failure mode.
    if result.implementation_level == ImplementationLevel.BASELINE:
        out.append(
            _check(
                "bankability.baseline_not_bankable",
                result.bankable_status == BankableStatus.NOT_BANKABLE,
                f"baseline result is bankable_status={result.bankable_status.value!r} "
                "(expected not_bankable)",
            )
        )

    # Spec coherence: power and energy travel together — a BESS spec has both, or neither when no
    # system is sizeable; and a zero-power system holds zero energy (no incoherent 0kW/500kWh spec).
    # NB: we deliberately do *not* assert energy == power × duration — the frozen contract gives no
    # such identity, and an engine sized for end-of-life capability (issue 014) legitimately reports
    # usable energy below nameplate power × duration.
    power, energy = result.selected_power_kw, result.selected_energy_kwh
    paired = (power is None) == (energy is None)
    if power is not None and energy is not None:
        paired = paired and ((power == 0) == (energy == 0))
    out.append(
        _check(
            "sizing.power_energy_coherent",
            paired,
            f"selected_power_kw={power} / selected_energy_kwh={energy} are incoherent "
            "(must be both-None, both-zero, or both-positive)",
        )
    )

    # Status <-> spec coherence: a no-solve status must carry no selected spec (you cannot report a
    # sized system you did not feasibly solve). The converse is not asserted (an engine may
    # legitimately solve to a zero/none system).
    if result.solver_status in _NO_SPEC_STATUSES:
        out.append(
            _check(
                "status.no_spec_when_unsolved",
                power is None and energy is None,
                f"solver_status={result.solver_status.value!r} but a spec is present "
                f"(power={power}, energy={energy}) — an unsolved result must carry no sizing",
            )
        )

    return out


# --------------------------------------------------------------------------------------------------
# Engine-level conformance — drive an engine across the canonical sites + determinism.
# --------------------------------------------------------------------------------------------------


def _prefix(results: list[ConformanceResult], ctx: str) -> list[ConformanceResult]:
    return [replace(r, check=ctx + r.check) for r in results]


def check_engine_conformance(
    engine: OptimisationEngine,
    *,
    assumptions: AssumptionLibrary,
    sites: list[tuple[str, SiteModel]] | None = None,
) -> ConformanceReport:
    """Run the full conformance suite over an engine and return a structured report.

    For each canonical site: build a request, solve, run the per-result invariants, and confirm the
    engine is **deterministic** (same request -> identical result modulo wall-clock fields). Adds
    the interface-level check that the engine satisfies the `OptimisationEngine` Protocol. Engines
    must **not raise** on a valid SiteModel (they report insufficiency via `solver_status`/
    `warnings`); a raised exception is recorded as a failure rather than propagated, so the report
    is complete.
    """
    label = type(engine).__name__
    results: list[ConformanceResult] = [
        _check(
            "interface.protocol",
            isinstance(engine, OptimisationEngine),
            "engine does not satisfy the OptimisationEngine Protocol "
            "(needs build_optimisation_request + solve)",
        )
    ]

    site_list = sites if sites is not None else canonical_conformance_sites()
    for site_label, site in site_list:
        ctx = f"[{site_label}] "

        try:
            request = engine.build_optimisation_request(site, assumptions)
        except Exception as exc:  # noqa: BLE001 — engines must not raise on a valid site
            results.append(
                _check(ctx + "request.build", False, f"build_optimisation_request raised {exc!r}")
            )
            continue

        request_ok = isinstance(request, OptimisationRequest)
        results.append(
            _check(
                ctx + "request.type",
                request_ok,
                f"build_optimisation_request returned {type(request).__name__}, "
                "not OptimisationRequest",
            )
        )
        if request_ok:
            results.append(
                _check(
                    ctx + "request.embeds_site",
                    request.site_model.site_id == site.site_id,
                    "request does not embed the input site",
                )
            )
            results.append(
                _check(
                    ctx + "request.assumption_version",
                    request.assumption_version == assumptions.metadata.version,
                    f"request.assumption_version={request.assumption_version!r} "
                    f"!= {assumptions.metadata.version!r}",
                )
            )
        if not request_ok:
            continue

        try:
            result = engine.solve(request)
        except Exception as exc:  # noqa: BLE001 — engines must not raise on a valid request
            results.append(_check(ctx + "result.solve", False, f"solve raised {exc!r}"))
            continue

        if not isinstance(result, OptimisationResult):
            results.append(
                _check(
                    ctx + "result.type",
                    False,
                    f"solve returned {type(result).__name__}, not OptimisationResult",
                )
            )
            continue
        results.append(_check(ctx + "result.type", True))

        results.extend(_prefix(check_result_conformance(result, request=request), ctx))

        # Determinism: identical request -> identical result (modulo wall-clock fields).
        try:
            again = engine.solve(request)
            deterministic = isinstance(again, OptimisationResult) and _result_dump(
                result
            ) == _result_dump(again)
            detail = (
                "" if deterministic else "two solves of the same request produced different results"
            )
        except Exception as exc:  # noqa: BLE001
            deterministic = False
            detail = f"second solve raised {exc!r}"
        results.append(_check(ctx + "determinism", deterministic, detail))

    return ConformanceReport(engine_label=label, results=results)


def assert_conformant(
    engine: OptimisationEngine,
    *,
    assumptions: AssumptionLibrary,
    sites: list[tuple[str, SiteModel]] | None = None,
) -> None:
    """Assert that ``engine`` passes the conformance suite; raise `ConformanceError` if not.

    The error message lists **every** failing check (not just the first), so a drifting engine shows
    all its violations at once.
    """
    report = check_engine_conformance(engine, assumptions=assumptions, sites=sites)
    if not report.ok:
        raise ConformanceError(report.summary())
