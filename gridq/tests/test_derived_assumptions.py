"""D44 — recorded derived assumptions: the schema's teeth, and the run identity they must move.

`docs/decisions.md` **D44** (Olly, 2026-08-12): no missing input may block a validation run. Where a
fact about the site is absent it is derived, recorded, and stated as requiring confirmation.

Three properties are load-bearing and each is pinned here:

1. **A derived record can never be written as consent granted.** A missing *permission* and a
   missing *fact* are different things; the distinction is enforced by the model, not by convention,
   because convention is what a later change quietly drops.
2. **No derived record can flatter itself.** HIGH is unreachable, and the per-field ceiling policy
   (`ceiling_for_key`) is a cap a producer cannot raise.
3. **A derived value that changed what was priced must change `run_id`.** Two runs on different
   derived connection capacities are different runs. Conversely an ABSENT record must leave every
   pre-D44 site's `input_hash` byte-identical (the C033/D42 pop-if-None guarantee).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine.baseline import BaselineEngine
from gridq.quotes.assembly import _canonical_site_dump, assemble_quote_record
from gridq.schemas.derived import ceiling_for_key


@pytest.fixture
def assumptions():
    return load_assumptions("gb_v0_1")


def _derived_mic(value: float = 500.0) -> s.DerivedAssumption:
    return s.DerivedAssumption(
        kind="derived_input",
        key="connection.mic_kw",
        missing_input="the site's maximum import capacity",
        value=value,
        unit="kW",
        basis="No MIC is stated on the connection agreement; the measured peak import over the "
        "metered year is used as a lower bound on the agreed capacity.",
        sources=[],
        derivation_method="fable_advisor",
        derived_by="claude-fable-5",
        derived_on_release="gb_v1_6",
        confidence_ceiling=s.ConfidenceBand.LOW,
        confirmation_ask="Send the signed connection agreement stating MIC and ASC in kVA/kW.",
    )


def _result(site: s.SiteModel, assumptions) -> s.OptimisationResult:
    engine = BaselineEngine()
    return engine.solve(engine.build_optimisation_request(site, assumptions, None))


# --- 1. the two classes never merge -----------------------------------------------------------


def test_a_pending_permission_cannot_carry_a_value() -> None:
    """The anti-fabrication rule, in the schema: a permission is not a derivable fact.

    If a consent record could carry a value it could be written as though the consent were held —
    which is precisely the failure the two-class split exists to prevent.
    """
    with pytest.raises(ValidationError, match="must not carry a value"):
        s.DerivedAssumption(
            kind="pending_permission",
            key="stream_consents.CAPACITY_MARKET",
            missing_input="the customer's authorisation to bid this site into the Capacity Market",
            value=30.0,  # a rate, i.e. a claim that the consent exists on these terms
            unit="gbp_per_kw_year",
            basis="assumed granted",
            derivation_method="not_derivable",
            derived_by="claude-fable-5",
            confidence_ceiling=s.ConfidenceBand.LOW,
            confirmation_ask="Ask the customer to authorise Capacity Market participation.",
        )


def test_a_pending_permission_cannot_claim_to_have_been_derived() -> None:
    with pytest.raises(ValidationError, match="cannot be derived from data"):
        s.DerivedAssumption(
            kind="pending_permission",
            key="export_arrangement.route",
            missing_input="an export MPAN and G99 approval",
            value=None,
            basis="comparable sites in the area export, so this one probably may",
            derivation_method="site_analogue",  # a permission has no analogue
            derived_by="claude-fable-5",
            confidence_ceiling=s.ConfidenceBand.LOW,
            confirmation_ask="Confirm the export MPAN and the G99 approval reference.",
        )


def test_the_two_classes_carry_different_report_language() -> None:
    """A derived fact and a missing permission must never share a sentence in a report."""
    fact = _derived_mic()
    permission = s.DerivedAssumption(
        kind="pending_permission",
        key="stream_consents.LOCAL_FLEX_PICLO",
        missing_input="the customer's authorisation to tender this site into local flexibility",
        value=None,
        basis="The stream is modelled and its value shown, but no authorisation is recorded.",
        derivation_method="not_derivable",
        derived_by="gridq.quotes.pipeline",
        confidence_ceiling=s.ConfidenceBand.LOW,
        confirmation_ask="Ask the customer to authorise local-flexibility participation.",
    )
    assert fact.report_label == "assumed, confirmation required"
    assert permission.report_label == "requires customer consent"
    assert fact.report_label != permission.report_label
    assert "requires customer consent" in permission.statement()
    assert "no value used" in permission.statement()


# --- 2. no record can flatter itself ----------------------------------------------------------


def test_no_derived_record_may_declare_high_confidence() -> None:
    with pytest.raises(ValidationError, match="never declare a HIGH"):
        s.DerivedAssumption.model_validate(
            {**_derived_mic().model_dump(mode="json"), "confidence_ceiling": "high"}
        )


def test_a_structural_input_cannot_be_recorded_at_medium() -> None:
    """Connection capacity decides whether the recommended system is physically possible."""
    assert ceiling_for_key("connection.mic_kw") is s.ConfidenceBand.LOW
    with pytest.raises(ValidationError, match="ceiling policy allows at most low"):
        s.DerivedAssumption.model_validate(
            {**_derived_mic().model_dump(mode="json"), "confidence_ceiling": "medium"}
        )


def test_a_shape_like_input_may_be_recorded_at_medium() -> None:
    """The archetype convention: modelled shape, measured totals, capped at MEDIUM."""
    assert ceiling_for_key("load_data.half_hourly_reference_profile") is s.ConfidenceBand.MEDIUM
    record = s.DerivedAssumption(
        kind="derived_input",
        key="load_data.half_hourly_reference_profile",
        missing_input="the site's load shape",
        value="non_domestic_unrestricted",
        basis="modelled shape scaled to the site's own annual total",
        derivation_method="sourced_default",
        derived_by="gridq.ingestion.hh_synthesis",
        confidence_ceiling=s.ConfidenceBand.MEDIUM,
        confirmation_ask="Supply the meter export or the operating pattern.",
    )
    assert record.confidence_ceiling is s.ConfidenceBand.MEDIUM


def test_an_unrecognised_key_is_capped_at_low_not_assumed_harmless() -> None:
    assert ceiling_for_key("some.field.nobody.classified") is s.ConfidenceBand.LOW


def test_not_derivable_cannot_carry_a_value() -> None:
    with pytest.raises(ValidationError, match="cannot carry a value"):
        s.DerivedAssumption.model_validate(
            {
                **_derived_mic().model_dump(mode="json"),
                "derivation_method": "not_derivable",
            }
        )


def test_an_empty_source_list_is_allowed_and_stays_visible() -> None:
    """An uncited derivation is a weaker record, and the reader is entitled to see which it is."""
    record = _derived_mic()
    assert record.sources == []
    assert "no citation recorded" in record.statement()


# --- 3. the hash: absent is byte-identical, set moves the identity ----------------------------


def test_canonical_dump_is_hash_stable_when_nothing_was_derived(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """D42/C033 pop-if-None: every pre-D44 site's input_hash / run_id is byte-stable."""
    site = make_site()
    assert site.derived_assumptions is None
    assert "derived_assumptions" not in _canonical_site_dump(site)  # bytes identical to pre-D44


def test_an_empty_derived_list_is_normalised_so_it_cannot_shift_the_hash(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """`[]` and absent carry the same information — nothing was derived — so neither may hash.

    Closed twice on purpose: the schema normalises `[]` to None on validation, and the canonical
    dump pops an empty list regardless. `model_copy` does not re-run validators, so a site built
    that way would otherwise slip an empty list into the identity.
    """
    validated = s.SiteModel.model_validate(
        {**make_site().model_dump(mode="json"), "derived_assumptions": []}
    )
    assert validated.derived_assumptions is None
    assert "derived_assumptions" not in _canonical_site_dump(validated)

    copied = make_site().model_copy(update={"derived_assumptions": []})
    assert "derived_assumptions" not in _canonical_site_dump(copied)
    assert _canonical_site_dump(copied) == _canonical_site_dump(make_site())


def test_a_recorded_derived_value_enters_the_run_identity(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """Two runs on different derived capacities must never share a run_id."""
    site = make_site()
    result = _result(site, assumptions)

    plain = assemble_quote_record(site, result, None, assumptions)
    with_500 = assemble_quote_record(
        site.model_copy(update={"derived_assumptions": [_derived_mic(500.0)]}),
        result,
        None,
        assumptions,
    )
    with_900 = assemble_quote_record(
        site.model_copy(update={"derived_assumptions": [_derived_mic(900.0)]}),
        result,
        None,
        assumptions,
    )
    explicit_none = assemble_quote_record(
        site.model_copy(update={"derived_assumptions": None}), result, None, assumptions
    )

    assert plain.input_hash == explicit_none.input_hash  # None == absent (popped)
    assert plain.input_hash != with_500.input_hash  # a real input enters the identity
    assert with_500.input_hash != with_900.input_hash  # …and different values are different runs
    assert with_500.quote_id != with_900.quote_id


def test_the_site_exposes_its_records_for_the_report(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site().model_copy(update={"derived_assumptions": [_derived_mic()]})
    (record,) = site.assumptions_requiring_confirmation
    assert record.key == "connection.mic_kw"
    assert make_site().assumptions_requiring_confirmation == ()
