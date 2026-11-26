"""The pricing basis, persisted machine-readably and binding the confidence band (**D46/D55**).

Two defects, both instances of the same failure and both closed here.

**1. A consumer holding a finished record could not tell which basis priced it, except by reading
prose.** The whole `StudyBasis` enters ``input_hash`` (D46), but a hash answers "was this the same
run?" and never "which basis priced it?". So every renderer detected the basis by matching a literal
prefix against `StudyBasis.note()` in ``optimisation_result.warnings`` — an identity resting on the
wording of a sentence. The measured consequence on 2026-08-18: the detector read
``"STUDY BASIS (D46)"``, so a D55 all-streams run was invisible to it, every artifact dropped its
banner, and the customer proposal called a study run an underwriting run.
`QuoteRecord.study_basis_id` is the structured field that replaces the string match, and
`study_basis_label` maps it back to the basis's OWN banner so a renderer never has to keep a
constant of its own.

**2. "This run's confidence band is capped at LOW" was true by coincidence.** `StudyBasis.note()`
states it on every artifact of a study run, and `generate_quote` caps the record's
``confidence_ceiling`` FIELD — but `assess_confidence` builds the band from FACTORS and reads
neither that field nor the basis. The band was LOW on every study run only because the shipped
library has zero confirmed assumptions (`_assumption_factor` → LOW), which is an accident of the
release and not a control. The first confirmed assumption would have published a MEDIUM-band study
run whose own disclosure said LOW. `confidence._study_basis_factor` makes it a factor.

**Why the assertions here are shaped the way they are.** Asserting ``band is LOW`` on a study run
proves nothing today — it is LOW anyway. So the band tests assert that the ``"pricing basis"``
factor is PRESENT and BINDING, which is false the moment the factor is unwired, and that the factor
strictly lowers the ceiling relative to an underwriting record. That is the difference between
pinning the mechanism and pinning a coincidence (`docs/validation_requirements.md` §3, CLAUDE.md
§4).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from tests.test_study_basis_schema import study

from gridq.assumptions import AssumptionLibrary, latest_version, load_assumptions
from gridq.engine.baseline import BaselineEngine
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import _canonical_run_input, assemble_quote_record
from gridq.quotes.confidence import ConfidenceBand, _study_basis_factor, assess_confidence
from gridq.quotes.pipeline import generate_quote
from gridq.schemas import (
    STUDY_BASIS_CLASSES,
    STUDY_BASIS_LABELS,
    AllStreamsEnabledStudyBasis,
    DataTier,
    QuoteMode,
    QuoteRecord,
    SiteModel,
    StudyBasis,
    ceiling_for_key,
    study_basis_label,
)

#: Resolved from disk rather than pinned: this file tests a schema/band mechanism that is release
#: independent, and MD-F-15's rule is that a superseded release is never pinned "to be safe".
RELEASE = latest_version()

#: The factor name `confidence._study_basis_factor` reports under. Asserted rather than reproduced
#: from the source so a rename has to be a deliberate edit to this constant.
FACTOR = "pricing basis"


class _StudyBasisEngine(BaselineEngine):
    """A `BaselineEngine` that also exposes the duck-typed ``last_study_basis()`` hook (D13).

    The point of the stub is to exercise `generate_quote`'s REAL forwarding logic without paying for
    a half-hourly MILP solve (10+ minutes a site). Nothing about the claim is weakened by it:
    ``engine`` is a production parameter of `generate_quote`, `MilpEngine` exposes the identical
    hook at ``optimisation/engine.py:2527``, and the lines under test are the pipeline's read of the
    hook and its forward into `assemble_quote_record` — which is exactly where the wiring was
    missing.
    """

    def __init__(self, basis: StudyBasis | None) -> None:
        self._basis = basis

    def last_study_basis(self) -> StudyBasis | None:
        return self._basis


def _quote_on(basis: StudyBasis | None, site: SiteModel, lib: AssumptionLibrary) -> QuoteRecord:
    """One `generate_quote` run whose engine reports ``basis`` — the production entry point."""
    return generate_quote(site, lib, engine=_StudyBasisEngine(basis)).quote


def _bare_record(basis_id: str | None) -> QuoteRecord:
    """The minimum record the basis factor reads: it consults ``study_basis_id`` and nothing else.

    Deliberately not a pipeline product. The factor's contract is "an id caps, an absent id does
    not", and a hand-built record is the only way to assert that over a basis id whose full
    `StudyBasis` fixture lives in a different test module.
    """
    return QuoteRecord(
        quote_id="quote_test",
        quote_mode=QuoteMode.DRAFT,
        site_id="site_test",
        data_tier=DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE,
        study_basis_id=basis_id,
    )


@pytest.fixture(scope="module")
def lib() -> AssumptionLibrary:
    return load_assumptions(RELEASE)


# ------------------------------------------------------------------ THE MARKER, ON THE REAL PATH


def test_generate_quote_persists_the_basis_id_on_the_record(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """THE reachability proof for the marker. Production chain, with no test-only link in it:

    `quotes/pipeline.py::generate_quote` reads the engine's ``last_study_basis()`` hook and forwards
    it as ``study_basis=`` to `assemble_quote_record`, which persists
    ``study_basis_id=study_basis.basis_id`` onto the `QuoteRecord`
    (`quotes/assembly.py`, the ``study_basis_id=`` argument of the `QuoteRecord` construction).

    Delete either link and this test fails: without the pipeline forward the basis never arrives,
    and without the assembly argument the field stays ``None`` on a run that was priced on a basis.
    """
    basis = study()
    record = _quote_on(basis, make_site(), lib)
    assert record.study_basis_id == basis.basis_id
    assert record.study_basis_id == StudyBasis.BASIS_ID == "export_in_priced_floor_study_v1"


def test_an_underwriting_run_records_no_basis_id(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The other direction: the field is ``None`` on an underwriting run, never an empty string.

    Both engines a real caller can hand `generate_quote` are covered — one whose hook returns
    ``None`` and one (`BaselineEngine` itself) that has no such hook at all — because the pipeline
    reads the hook with ``getattr`` and both paths must land on the same honest absence.
    """
    site = make_site()
    assert _quote_on(None, site, lib).study_basis_id is None
    assert generate_quote(site, lib, engine=BaselineEngine()).quote.study_basis_id is None


def test_the_banner_is_recoverable_from_the_id_without_parsing_any_prose(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The renderer handoff: id → the basis's own banner, with no string match on the warnings.

    This is what replaces `outputs/viewmodel.py`'s prefix match on `StudyBasis.note()`. The banner
    it yields is the basis's OWN `LABEL`, so a D55 run cannot be labelled with D46's wording — the
    defect measured on 2026-08-18 — and rewording any disclosure sentence cannot switch detection
    off.
    """
    basis = study()
    record = _quote_on(basis, make_site(), lib)
    assert study_basis_label(record.study_basis_id) == basis.LABEL
    assert "STUDY BASIS (D46)" in basis.LABEL
    # Detection does not depend on the note existing, let alone on its wording.
    assert study_basis_label(None) is None
    assert study_basis_label("some_future_basis_v9") is None


def test_the_marker_is_not_in_the_hash_input_so_it_moves_no_identity(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """Backward compatibility (a): the RECORD field feeds no computed identity.

    `_canonical_run_input` hashes the run's INPUTS — the whole `StudyBasis` among them, per D46 —
    and never the assembled record, so adding a field to `QuoteRecord` cannot re-mint an
    ``input_hash``. Proven against a literal reconstruction of the canonical input rather than
    against the record, so the two cannot drift together. (The pop-if-None leg — an ABSENT basis
    leaving the canonical input byte-identical to its pre-D46 shape — is pinned by
    `test_study_basis_hash_identity.py::test_absent_basis_leaves_the_canonical_input_byte_identical_to_pre_d46`
    and is deliberately not duplicated here.)
    """
    site = make_site()
    engine = _StudyBasisEngine(None)
    result = engine.solve(engine.build_optimisation_request(site, lib))
    canonical = _canonical_run_input(site, result, None, lib, None)
    assert "study_basis_id" not in canonical
    assert "study_basis" not in canonical  # popped, not present-and-null
    plain = assemble_quote_record(site, result, None, lib)
    assert plain.study_basis_id is None
    assert plain.input_hash == hash_json(canonical)


def test_two_bases_and_an_underwriting_run_are_three_identities_through_generate_quote(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """Direction (a): a declared basis MOVES ``input_hash`` on the real entry point.

    D46's requirement is that the basis "enters ``input_hash`` so two bases can never share a
    ``run_id``", and until 2026-08-18 the second half was false on the production path: nothing
    forwarded the basis, so an all-streams study run and an underwriting run of the same site could
    mint the same ``run_id`` while pricing completely different things.

    Asserted here as well as in `test_study_basis_hash_identity` because that file proves it of
    `assemble_quote_record`, whose ``study_basis=`` argument a caller has to remember. This proves
    it of `generate_quote`, which is what a caller actually runs — one site, two bases, two
    identities.
    """
    site = make_site()
    records = [_quote_on(b, site, lib) for b in (None, study())]
    assert len({r.input_hash for r in records}) == 2, (
        "a study run collides with its underwriting run"
    )
    assert len({r.quote_id for r in records}) == 2
    # Identical inputs still mint an identical identity — the change adds a discriminator, it does
    # not make the pipeline non-deterministic (CLAUDE.md §5).
    assert _quote_on(study(), site, lib).input_hash == records[1].input_hash


def test_an_underwriting_runs_identity_is_untouched_by_the_marker_and_the_factor(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """Direction (b): a run with NO study basis is byte-identical to before this change.

    Neither thing added here is hashable. ``study_basis_id`` sits on the record and
    `_canonical_run_input` never reads the record; the ``"pricing basis"`` factor moves only
    ``confidence_band`` / ``confidence_binding_factors``, which `generate_quote` stamps on AFTER the
    identity is minted.

    The proof reconstructs the canonical input from the run's OWN artifacts — the site, result and
    financial case `generate_quote` returns — with the ``study_basis`` key absent rather than
    ``None`` (C033/D42 pop-if-None), which is the pre-change shape exactly. If either addition had
    leaked into the hash input, the reconstruction would no longer reproduce ``input_hash``.
    """
    run = generate_quote(make_site(), lib, engine=_StudyBasisEngine(None))
    canonical = _canonical_run_input(run.site, run.result, run.financial_case, lib, None)
    assert "study_basis" not in canonical and "study_basis_id" not in canonical
    assert run.quote.study_basis_id is None
    assert run.quote.input_hash == hash_json(canonical)
    # The factor ran (it is unconditional) and reported no cap, so nothing about the band moved
    # either: the underwriting band is set by the same factors that set it before.
    assert not any(f.startswith(f"{FACTOR} —") for f in run.quote.confidence_binding_factors)


def test_a_record_persisted_before_the_field_existed_still_validates(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """Backward compatibility (b): a legacy JSON record round-trips and reads ``None``.

    ``None`` on such a record means "this record does not say", NOT "this was an underwriting run" —
    a study run priced before 2026-08-18 also carries ``None``, because nothing wrote the field
    then. A consumer must keep its warnings-prefix fallback for records that lack it, and this test
    exists to make that asymmetry explicit rather than folklore.
    """
    dumped = _quote_on(study(), make_site(), lib).model_dump(mode="json")
    legacy = {k: v for k, v in dumped.items() if k != "study_basis_id"}
    assert "study_basis_id" not in legacy
    revived = QuoteRecord.model_validate(legacy)
    assert revived.study_basis_id is None
    # And the legacy record's basis is still discoverable the old way, which is why the fallback
    # must stay: the note the pipeline wrote is on the record even with the field stripped.
    assert any("STUDY BASIS (D46)" in w for w in revived.warnings)


# ------------------------------------------------------- THE BAND, CAPPED BY EVIDENCE NOT ACCIDENT


def test_the_persisted_basis_binds_the_confidence_band(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """THE reachability proof for the band cap. Production call site: the
    ``_study_basis_factor(quote)`` entry in `quotes/confidence.py::assess_confidence`'s factor
    tuple, reached from `generate_quote`'s `assess_confidence(...)` call on the assembled record.

    Asserting ``band is LOW`` alone would prove nothing — it is LOW on this fixture regardless. What
    is asserted is that the basis is one of the factors HOLDING it there, which is false the instant
    the factor is unwired from the tuple, and that the record's persisted binding factors say so, so
    a reader is told the basis is what capped the band.
    """
    record = _quote_on(study(), make_site(), lib)
    assert record.confidence_band is ConfidenceBand.LOW
    assert any(f.startswith(f"{FACTOR} —") for f in record.confidence_binding_factors), (
        f"the pricing basis does not appear among the factors binding the band: "
        f"{record.confidence_binding_factors}"
    )
    binding = next(f for f in record.confidence_binding_factors if f.startswith(f"{FACTOR} —"))
    assert "FEASIBILITY-STUDY basis" in binding
    assert StudyBasis.BASIS_ID in binding
    assert "MAY NEVER BE PRESENTED AS AN UNDERWRITING OUTPUT" in binding


def test_an_underwriting_run_is_never_capped_by_the_pricing_basis(
    make_site: Callable[..., SiteModel], lib: AssumptionLibrary
) -> None:
    """The factor must be silent on the underwriting basis, or it says nothing when it speaks.

    The band is LOW on this fixture for other reasons (zero confirmed assumptions), so the assertion
    is that the pricing basis is NOT among the binding factors — the record must not tell a reader
    that its basis capped a band its basis did not cap.
    """
    record = _quote_on(None, make_site(), lib)
    assert not any(f.startswith(f"{FACTOR} —") for f in record.confidence_binding_factors)
    assert _study_basis_factor(record).ceiling is ConfidenceBand.HIGH


def test_the_factor_strictly_lowers_the_ceiling_a_study_record_can_reach() -> None:
    """The cap is a real effect of the id, not an artefact of the fixture's other weaknesses.

    Two records identical in every field but ``study_basis_id``, assessed by the factor itself: the
    study record's ceiling must be STRICTLY weaker. This is what makes the band test above a pin on
    the mechanism — the factor demonstrably moves the ceiling, so its presence in the factor tuple
    is load-bearing rather than decorative.
    """
    on_basis = _study_basis_factor(_bare_record(StudyBasis.BASIS_ID))
    underwriting = _study_basis_factor(_bare_record(None))
    assert on_basis.ceiling.rank < underwriting.ceiling.rank
    assert on_basis.name == underwriting.name == FACTOR


def test_the_ceiling_is_computed_from_the_d45_policy_and_not_written_down_twice() -> None:
    """The factor's cap comes from `ceiling_for_key`, the same function the schema validates
    against.

    `StudyBasis._enforce_named_basis` refuses a basis declaring a ceiling above the policy's for the
    export tariff and export limit keys. If the factor restated LOW as a literal, the two could
    drift and the band would stop agreeing with the basis's own validated ceiling. Deriving it means
    they cannot.
    """
    expected = ConfidenceBand.weakest(
        [ceiling_for_key(StudyBasis.TARIFF_KEY), ceiling_for_key(StudyBasis.EXPORT_LIMIT_KEY)]
    )
    assert _study_basis_factor(_bare_record(StudyBasis.BASIS_ID)).ceiling is expected
    assert expected is ConfidenceBand.LOW
    # And the basis a real run carries declares exactly that ceiling, so record and basis agree.
    assert study().confidence_ceiling is expected


@pytest.mark.parametrize(
    "basis_id",
    [StudyBasis.BASIS_ID, AllStreamsEnabledStudyBasis.BASIS_ID, "an_unrecognised_future_basis_v1"],
)
def test_every_declared_basis_caps_including_one_this_build_does_not_know(basis_id: str) -> None:
    """Any declared second basis caps the band — recognised or not.

    The D55 case matters on its own: the basis whose banner the renderers dropped must not also be
    the basis whose band cap goes missing. The unrecognised case is the conservative direction — a
    record naming a basis this build has never heard of is capped, not waved through, because
    over-capping is the safe error and an unknown second basis is still a second basis.
    """
    factor = _study_basis_factor(_bare_record(basis_id))
    assert factor.ceiling is ConfidenceBand.LOW
    assert basis_id in factor.reason


def test_the_factor_reaches_the_band_through_assess_confidence(
    make_site: Callable[..., SiteModel],
) -> None:
    """`assess_confidence` — the function `generate_quote` calls — must carry the factor at all.

    Complements the record-level test above by pinning the factor's presence in the tuple directly,
    so the reason a failure appears is unambiguous: this fails if the tuple entry is deleted, the
    record test fails if the tuple entry OR the assembly persistence OR the pipeline forward is.
    """
    assessment = assess_confidence(make_site(), _bare_record(StudyBasis.BASIS_ID))
    names = [f.name for f in assessment.factors]
    assert names.count(FACTOR) == 1, f"pricing-basis factor not in the tuple exactly once: {names}"
    assert assessment.band is ConfidenceBand.LOW
    assert FACTOR in [f.name for f in assessment.binding_factors]


# --------------------------------------------------------------- THE REGISTRY THE RENDERER READS


def test_every_study_basis_class_is_registered_with_its_own_banner() -> None:
    """A basis no consumer can label must not be able to reach a report.

    Walks `StudyBasis`'s subclass tree rather than a hand-written list, so adding a third basis and
    forgetting `STUDY_BASIS_CLASSES` fails HERE instead of silently rendering a run with no banner —
    which is precisely how the D55 basis shipped invisible to all four renderers.
    """

    def subclasses(cls: type[StudyBasis]) -> set[type[StudyBasis]]:
        found = {cls}
        for sub in cls.__subclasses__():
            found |= subclasses(sub)
        return found

    for cls in subclasses(StudyBasis):
        assert cls.BASIS_ID in STUDY_BASIS_CLASSES, (
            f"{cls.__name__} is not in STUDY_BASIS_CLASSES, so a record priced on it carries an id "
            "no consumer can turn into a banner"
        )
        assert STUDY_BASIS_CLASSES[cls.BASIS_ID] is cls
        assert STUDY_BASIS_LABELS[cls.BASIS_ID] == cls.LABEL


def test_the_two_bases_have_different_ids_and_different_banners() -> None:
    """The D55 defect in one assertion: the bases must be distinguishable in both directions.

    Same id or same label and a consumer cannot tell an export-only study from an everything-enabled
    one — the all-streams basis understates nothing about export but puts five further streams in
    the priced floor, so labelling one as the other misstates the floor by five streams.
    """
    assert StudyBasis.BASIS_ID != AllStreamsEnabledStudyBasis.BASIS_ID
    assert StudyBasis.LABEL != AllStreamsEnabledStudyBasis.LABEL
    assert len(set(STUDY_BASIS_LABELS.values())) == len(STUDY_BASIS_LABELS)
    assert "D55" in AllStreamsEnabledStudyBasis.LABEL and "D46" in StudyBasis.LABEL
