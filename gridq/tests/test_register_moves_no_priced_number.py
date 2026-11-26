"""Adding a `DerivedAssumption` moves the run's IDENTITY and nothing else (D45).

Back-filling the assumption register is a disclosure change. It must move `input_hash` and
`quote_id` — a run that consumed a recorded derived value is not the same run as one that did not,
and D45 makes those records real inputs on purpose — while leaving **every priced figure the
same**.
If it could move a £, the register would be a pricing input in disguise and no back-fill could ever
be applied to a live site.

The empirical check on the five real sites is currently weak through no fault of the invariant: on
`gb_v1_8` all of them return `no_viable_configuration`, so there are no priced figures to move and
"nothing changed" is nearly vacuous. Two checks close that gap:

1. **A structural one.** Nothing on the pricing path may so much as read the field. The optimisation
   engine, the finance model and the value stack are swept for any mention of it.
2. **A behavioural one on a site that DOES price.** A whole `QuoteRecord` is compared field by
   field with and without a record attached, so any priced figure that exists is covered, not only
   the ones a hand-written list happens to name.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gridq.assumptions import load_assumptions
from gridq.optimisation import MilpEngine
from gridq.quotes import generate_quote
from gridq.quotes.assembly import _canonical_site_dump
from gridq.schemas import ConfidenceBand, DerivedAssumption

SRC = Path(__file__).resolve().parents[1] / "src" / "gridq"

#: The packages that decide what a site is worth. None of them may read the register.
PRICING_PATH = ("optimisation", "finance", "value_stack", "dispatch", "sizing", "scenarios")

#: Fields that are SUPPOSED to move: the run identity, and the disclosure the register exists for.
EXPECTED_TO_MOVE = frozenset(
    {"quote_id", "input_hash", "warnings", "confidence_binding_factors", "created_at"}
)


def _record() -> DerivedAssumption:
    return DerivedAssumption(
        kind="derived_input",
        key="load_data.half_hourly_reference_profile",
        missing_input="the site's own metered half-hourly load shape",
        value="waste_mrf",
        basis="Modelled from a documented engineering archetype, scaled to the site's own totals.",
        sources=[],
        derivation_method="archetype_pending_source",
        derived_by="gridq.ingestion.hh_synthesis",
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask="Send the measured monthly maximum demand readings from the invoices.",
    )


def _without_identity(dump: dict) -> dict:
    """Strip the nested identity and timestamp fields, which are SUPPOSED to move.

    `optimisation_result.result_id` derives from the request, which carries the site, so a
    recorded derived value moves it by design. What must not move is any number inside the result,
    and that is what remains once the identity is removed.
    """
    out = dict(dump)
    result = dict(out.get("optimisation_result") or {})
    for field in ("result_id", "request_id", "created_at"):
        result.pop(field, None)
    # `solver_metadata.code_version` is read from git HEAD on every call, so a sibling commit
    # landing between the two arms moves it. That is repo churn, not a register effect, and it would
    # make this test flake for a reason it is not measuring.
    metadata = dict(result.get("solver_metadata") or {})
    metadata.pop("code_version", None)
    if metadata:
        result["solver_metadata"] = metadata
    if result:
        out["optimisation_result"] = result
    case = out.get("financial_case")
    if case:
        case = dict(case)
        case.pop("case_id", None)
        out["financial_case"] = case
    return out


# --- 1. structural: the pricing path cannot see the register ------------------------------------


#: The ONE pricing-path module allowed to read the register, and why.
#:
#: `gates.py` resolves a D45-**declared** capacity: where a connection capacity is absent it is
#: derived offline, recorded as a `derived_input`, and the run reads the recorded value. That is
#: D45's entire mechanism, and the repo already concedes the point in the allowlist below —
#: `quotes/assembly.py` is there precisely because "a recorded derived value is a real input and
#: enters `input_hash`". A blanket ban would forbid D45 from working at all.
#:
#: The narrowed rule the sweep now enforces: a **`pending_permission`** record may never move a £
#: (consent is not a fact, and reading one as capacity is "consent manufactures eligibility"),
#: while a **`derived_input`** may, deliberately — it enters `input_hash` and caps the band.
#: The kind-invariant is carried behaviourally by `test_pending_permission_is_never_capacity`,
#: not by grepping for the filter, which would be a hollow check.
REGISTER_READERS_ON_THE_PRICING_PATH = {"value_stack/gates.py"}


@pytest.mark.parametrize("package", PRICING_PATH)
def test_no_pricing_module_reads_the_derived_assumption_register(package: str) -> None:
    """Only the declared-capacity resolver may read the register. Swept, not assumed.

    A grep is the right instrument here: the claim is about the whole package, and a behavioural
    test can only ever prove it for the paths one fixture happens to reach. Any NEW reader is a
    failure until it is added above with a reason — the deliberation is the point.
    """
    offenders = []
    for path in sorted((SRC / package).rglob("*.py")):
        rel = str(path.relative_to(SRC))
        if rel in REGISTER_READERS_ON_THE_PRICING_PATH:
            continue
        text = path.read_text(encoding="utf-8")
        for term in ("derived_assumptions", "assumptions_requiring_confirmation"):
            if term in text:
                offenders.append(f"{rel}: {term}")
    assert not offenders, (
        "a pricing module reads the assumption register, so a recorded disclosure could move a "
        f"priced number: {offenders}"
    )


def test_the_register_is_read_only_by_identity_disclosure_and_the_confidence_band() -> None:
    """Where it IS read, and why each is legitimate. A new reader must be a deliberate act."""
    readers = set()
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "derived_assumptions" in text or "assumptions_requiring_confirmation" in text:
            readers.add(str(path.relative_to(SRC)))
    assert readers == {
        # the schema that defines it, and the base class that documents why only it is frozen
        "schemas/derived.py",
        "schemas/site.py",
        "schemas/provenance.py",
        # the run identity: a recorded derived value is a real input and enters `input_hash`
        "quotes/assembly.py",
        # the disclosure, and the confidence ceiling it imposes
        "quotes/pipeline.py",
        "quotes/confidence.py",
        # the one offline PRODUCER that writes the field onto a site (it does not price from it)
        "ingestion/hh_synthesis.py",
        # D45: the one pricing-path reader, and only of `derived_input` records — the declared
        # capacity resolver. Guarded by `test_pending_permission_is_never_capacity`.
        "value_stack/gates.py",
        # A RENDERER, not a pricing module, and it reads the register to decide what to SAY rather
        # than what to charge. `capacity_is_derived` asks whether `connection.mic_kw` arrived as a
        # DerivedAssumption, so an exhibit cannot caption a derived capacity as "the recorded agreed
        # capacity" — which Tanhouse's pack did, on a site whose own record says no kVA figure
        # exists. The register is the only authoritative signal for that question; the three
        # heuristics it replaced all missed a capacity derived ABOVE the measured peak.
        # This reads the register and moves no £: it selects prose, never a number.
        "outputs/report_charts.py",
        # A RENDERER, added deliberately by D56 limb 2 (docs/decisions.md D56: "every assumption is
        # enumerated on a dedicated assumptions page in every report"; landed in 12033d0,
        # 2026-08-18). `viewmodel._assumptions_page` reads
        # `site.assumptions_requiring_confirmation` to LIST each record on that page, the
        # register being its first source of authority. It is an `outputs/` module, outside
        # PRICING_PATH, and moves no £. WP-CORE-01 triage (2026-11-16): this expectation predated
        # D56 and failed from 12033d0 onwards.
        "outputs/viewmodel.py",
    }, sorted(readers)


def test_pending_permission_is_never_capacity() -> None:
    """A permission may not become capacity, in two independent layers.

    Layer 1 is the schema: a `pending_permission` cannot carry a value at all, so the hostile
    record is normally unconstructible. Layer 2 is the gate's own `kind` filter, checked against a
    record built through `model_construct` to BYPASS layer 1 — because schema constraints drift
    and `gates.py` is the last line. Without layer 2 this test would prove only that pydantic
    works, which is not the invariant under test.
    """
    from gridq.value_stack.gates import _declared_capacity_mw

    # Layer 1 — the schema refuses to build it.
    with pytest.raises(ValueError):
        DerivedAssumption(
            kind="pending_permission",
            key="connection.mic_kw",
            missing_input="no MIC on file",
            value=5000.0,
            unit="kW",
            basis="hostile",
            sources=[],
            derivation_method="not_derivable",
            derived_by="test",
            derived_on_release="gb_v1_8",
            confidence_ceiling=ConfidenceBand.LOW,
            confirmation_ask="n/a",
        )

    # Layer 2 — even if one existed, the gate must not read it as capacity.
    smuggled = DerivedAssumption.model_construct(
        kind="pending_permission",
        key="connection.mic_kw",
        missing_input="no MIC on file",
        value=5000.0,
        unit="kW",
        basis="hostile",
        sources=[],
        derivation_method="not_derivable",
        derived_by="test",
        derived_on_release="gb_v1_8",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="n/a",
    )
    assert smuggled.value == 5000.0, "fixture is inert: the smuggled record carries no value"

    from tests.conftest import _make_site

    site = _make_site().model_copy(update={"derived_assumptions": [smuggled]})
    assert site.assumptions_requiring_confirmation, "fixture is inert: the record did not attach"
    assert _declared_capacity_mw(site, ("connection.mic_kw",)) is None, (
        "a pending_permission was read as declared capacity: consent manufactured 5 MW of "
        "connection out of a permission that has not been granted"
    )


def test_the_field_is_annotated_as_an_input_to_the_hash_and_nothing_else() -> None:
    """`_canonical_site_dump` is the only place the field reaches a computed value."""
    tree = ast.parse((SRC / "quotes" / "assembly.py").read_text(encoding="utf-8"))
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and "derived_assumptions"
        in ast.get_source_segment(
            (SRC / "quotes" / "assembly.py").read_text(encoding="utf-8"), node
        )
    }
    assert functions == {"_canonical_site_dump"}, functions


# --- 2. behavioural, on a site that actually prices --------------------------------------------


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_6")


@pytest.fixture(scope="module")
def arms(make_site_module, assumptions):
    """The same site run twice: once bare, once with one record attached. Nothing else differs."""
    bare = make_site_module()
    with_record = bare.model_copy(update={"derived_assumptions": [_record()]})
    return (
        generate_quote(bare, assumptions, engine=MilpEngine()).quote,
        generate_quote(with_record, assumptions, engine=MilpEngine()).quote,
    )


@pytest.fixture(scope="module")
def make_site_module():
    """Module-scoped access to the shared site factory (the fixture itself is function-scoped)."""
    from tests.conftest import _make_site

    return _make_site


def test_the_run_identity_moves(arms) -> None:
    """It must: two runs on different declared inputs are different runs (D45, C033/D42 policy)."""
    bare, disclosed = arms
    assert bare.input_hash != disclosed.input_hash
    assert bare.quote_id != disclosed.quote_id


def test_every_other_field_of_the_quote_is_identical(arms) -> None:
    """The whole record is compared, so no priced figure can slip through an incomplete list."""
    bare, disclosed = arms
    a = _without_identity(bare.model_dump(mode="json"))
    b = _without_identity(disclosed.model_dump(mode="json"))
    assert set(a) == set(b)
    moved = {k for k in a if a[k] != b[k]}
    unexpected = moved - EXPECTED_TO_MOVE
    assert not unexpected, f"the register moved fields it must not touch: {sorted(unexpected)}"


def test_the_optimisation_result_is_byte_identical(arms) -> None:
    """The engine's own output, which every revenue and cost figure is derived from."""
    bare, disclosed = arms
    exclude = {"created_at": True, "result_id": True, "solver_metadata": {"code_version"}}
    a = bare.optimisation_result.model_dump(mode="json", exclude=exclude)
    b = disclosed.optimisation_result.model_dump(mode="json", exclude=exclude)
    assert a == b


def test_the_financial_case_is_byte_identical_when_one_was_produced(arms) -> None:
    bare, disclosed = arms
    if bare.financial_case is None:
        pytest.skip("this fixture priced no financial case, so there is nothing to compare")
    a = bare.financial_case.model_dump(mode="json", exclude={"case_id"})
    b = disclosed.financial_case.model_dump(mode="json", exclude={"case_id"})
    assert a == b


def test_only_the_register_entered_the_canonical_input(arms, make_site_module) -> None:
    """And the identity moved for exactly one reason: the record, not a side effect of adding it."""
    bare = make_site_module()
    disclosed = bare.model_copy(update={"derived_assumptions": [_record()]})
    a = _canonical_site_dump(bare)
    b = _canonical_site_dump(disclosed)
    assert set(b) - set(a) == {"derived_assumptions"}
    assert all(a[k] == b[k] for k in a)


def test_an_empty_register_leaves_the_identity_byte_stable(make_site_module) -> None:
    """`[]` carries the same information as absent, so it must not shift a single hash (D42)."""
    bare = make_site_module()
    empty = bare.model_copy(update={"derived_assumptions": []})
    assert _canonical_site_dump(bare) == _canonical_site_dump(empty)
