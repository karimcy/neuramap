"""D46 run identity: a declared basis enters `input_hash`; an ABSENT one moves nothing.

Two properties, and both matter for a different reason.

- **Two bases must never share a ``run_id``.** The same site, library and code version priced with
  export in the floor and priced with export as gated upside are two different runs. A reader who
  cannot separate them by identity has only prose between them, and D46 records that a reader
  mistaking one for the other is the entire risk the second basis creates.
- **An absent basis leaves every existing run identity byte-identical.** The C033/D42 pop-if-None
  policy: the key is *absent*, not ``None``, on an underwriting run, so this schema growth cannot
  silently re-mint the identity of any run that came before it. Proven against a literal
  reconstruction of the pre-D46 canonical shape rather than against the implementation, so the two
  cannot drift together.
"""

from __future__ import annotations

import pytest
from tests.test_study_basis_schema import study

from gridq.assumptions import load_assumptions
from gridq.provenance import build_run_context
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import (
    _canonical_run_input,
    _canonical_site_dump,
    assemble_quote_record,
)
from gridq.schemas import (
    SCHEMA_VERSION,
    BankableStatus,
    ImplementationLevel,
    MarketStream,
    OptimisationResult,
    SolverMetadata,
    SolverStatus,
)


@pytest.fixture(scope="module")
def parts(request: pytest.FixtureRequest) -> tuple:
    """A minimal (site, result, assumptions) triple — no solve needed to pin a hash shape."""
    from tests.conftest import _make_site  # the shared factory

    site = _make_site()
    lib = load_assumptions("gb_v0_1")
    result = OptimisationResult(
        result_id="res_x",
        request_id="req_x",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.NOT_BANKABLE,
        solver_status=SolverStatus.OPTIMAL,
        value_streams=[],
        gated_upside=[],
        constraints_binding=[],
        solver_metadata=SolverMetadata(
            seed=0, assumption_version=lib.metadata.version, code_version="c", model_version="m"
        ),
        warnings=[],
    )
    return site, result, lib


def _pre_d46_canonical(site, result, lib) -> dict[str, object]:
    """The canonical run input EXACTLY as it was shaped before D46 — five keys, no basis.

    Reconstructed LITERALLY rather than by calling the implementation, so this file pins the shape
    independently of the code that produces it. That is the point of the helper and is why it must
    be updated by hand whenever the canonical shape is deliberately re-minted.

    Re-minted 2026-08-15: `solver_metadata`'s three non-input fields are now excluded from the hash.
    `code_version` is read from git HEAD on every call, so leaving it in moved `input_hash` on every
    commit and destroyed the one question that field exists to answer. `solve_seconds` and
    `reproduction_env` are wall-clock and machine identity — the same "metadata must never feed a
    computed identity" rule the docstring in `assembly.py` already states for `created_at`.
    `seed`, `model_version` and `assumption_version` stay IN: those are genuine inputs, and
    excluding them would let two different seeds or two different MILP formulations share one
    identity, which is a fixed identity over a moved number and strictly worse than the defect
    being fixed here.

    The D46 claim this file exists to make is unaffected: an absent study basis still leaves the
    canonical input byte-identical to the pre-D46 shape. It now pins the post-re-mint version of it.
    """
    return {
        "site": _canonical_site_dump(site),
        "result": result.model_dump(
            mode="json",
            exclude={
                "created_at": True,
                "solver_metadata": {
                    "code_version": True,
                    "solve_seconds": True,
                    "reproduction_env": True,
                },
            },
        ),
        "financial_case": None,
        "assumption_version": lib.metadata.version,
        "forecast_content_hash": None,
    }


def test_absent_basis_leaves_the_canonical_input_byte_identical_to_pre_d46(parts) -> None:
    site, result, lib = parts
    canonical = _canonical_run_input(site, result, None, lib, None)
    assert "study_basis" not in canonical  # POPPED, not present-and-null
    assert canonical == _pre_d46_canonical(site, result, lib)
    assert hash_json(canonical) == hash_json(_pre_d46_canonical(site, result, lib))


def test_passing_none_explicitly_is_identical_to_omitting_it(parts) -> None:
    site, result, lib = parts
    omitted = _canonical_run_input(site, result, None, lib, None)
    explicit = _canonical_run_input(site, result, None, lib, None, None)
    assert omitted == explicit


def test_a_declared_basis_enters_the_hash_and_changes_the_run_id(parts) -> None:
    site, result, lib = parts
    without = _canonical_run_input(site, result, None, lib, None)
    with_basis = _canonical_run_input(site, result, None, lib, None, study())
    assert "study_basis" in with_basis
    assert hash_json(with_basis) != hash_json(without)
    ctx_a = build_run_context(without, lib.metadata.version, SCHEMA_VERSION)
    ctx_b = build_run_context(with_basis, lib.metadata.version, SCHEMA_VERSION)
    assert ctx_a.run_id != ctx_b.run_id


def test_two_different_bases_never_share_a_run_id(parts) -> None:
    site, result, lib = parts
    from tests.test_study_basis_schema import tariff_record

    a = study()
    # A different derived tariff is a different basis: it is a different priced floor.
    b = study(
        export_tariff_gbp_per_kwh=0.2,
        tariff_record=tariff_record(value=0.2),
    )
    hash_a = hash_json(_canonical_run_input(site, result, None, lib, None, a))
    hash_b = hash_json(_canonical_run_input(site, result, None, lib, None, b))
    assert hash_a != hash_b
    # ...and so is a different export limit, for the same reason.
    from tests.test_study_basis_schema import limit_record

    c = study(export_limit_kw=100.0, export_limit_record=limit_record(value=100.0))
    assert hash_json(_canonical_run_input(site, result, None, lib, None, c)) != hash_a


def test_assemble_quote_record_default_is_the_unchanged_identity(parts) -> None:
    site, result, lib = parts
    plain = assemble_quote_record(site, result, None, lib)
    explicit_none = assemble_quote_record(site, result, None, lib, study_basis=None)
    assert plain.input_hash == explicit_none.input_hash
    assert plain.quote_id == explicit_none.quote_id
    # And it equals the hash of the literal pre-D46 shape — the growth is invisible.
    assert plain.input_hash == hash_json(_pre_d46_canonical(site, result, lib))


def test_assemble_quote_record_on_a_study_basis_gets_its_own_identity(parts) -> None:
    site, result, lib = parts
    plain = assemble_quote_record(site, result, None, lib)
    studied = assemble_quote_record(site, result, None, lib, study_basis=study())
    assert studied.input_hash != plain.input_hash
    assert studied.quote_id != plain.quote_id


def test_the_basis_reaches_the_hash_through_the_result_too(parts) -> None:
    """A second, independent layer: the banner and the model_version suffix are IN the result.

    Belt and braces on purpose. The explicit ``study_basis`` key is the structural pin, but even a
    consumer that never passes it (today: `quotes.pipeline`, which is not this change's file) cannot
    accidentally give a study run an underwriting run's identity, because the engine writes the
    basis banner into ``OptimisationResult.warnings`` and the basis suffix into
    ``solver_metadata.model_version``, both of which are already inside the hashed result dump.
    """
    site, result, lib = parts
    basis = study()
    studied_result = result.model_copy(
        update={
            "warnings": [basis.note()],
            "solver_metadata": result.solver_metadata.model_copy(
                update={"model_version": f"m{basis.SOLVER_SUFFIX}"}
            ),
        }
    )
    assert hash_json(_canonical_run_input(site, studied_result, None, lib, None)) != hash_json(
        _canonical_run_input(site, result, None, lib, None)
    )


def test_the_gated_upside_stream_set_is_untouched_by_this_change() -> None:
    """D46 amends WHERE export may be priced, never the never-FIRM invariant behind it."""
    assert (
        MarketStream.WHOLESALE_P415
        in __import__("gridq.schemas", fromlist=["GATED_UPSIDE_STREAMS"]).GATED_UPSIDE_STREAMS
    )
