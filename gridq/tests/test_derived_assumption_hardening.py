"""D45 hardening: the three latent routes back to what the register was built to prevent.

`tests/test_derived_assumptions.py` pins the *stated* properties of `DerivedAssumption` — the two
classes never merge, no record flatters itself, a recorded value moves `run_id`. An adversarial pass
found each of those provable by construction and yet reachable anyway, three different ways. Each is
closed in `gridq.schemas.derived` and pinned here.

1. **The consent-fabrication route.** `kind` was the producer's free choice and nothing tied a
   consent-shaped *key* to the permission *class*, so a `derived_input` keyed
   ``stream_consents.CAPACITY_MARKET`` with ``value="granted"`` validated and rendered under
   *"assumed, confirmation required"* — a consent invented inside the one section of the report that
   exists to disclose assumptions. This is the most serious of the three because the output is a
   plausible-looking sentence, not an error.
2. **The ceiling bypass.** The validator returned early for `pending_permission` and so never
   reached `ceiling_for_key`, which made "no producer may raise its own ceiling" true of
   `derived_input` alone.
3. **The post-construction flip.** A validator guards construction only. Nothing stopped
   ``rec.kind = "pending_permission"`` afterwards, which flips `report_label` to *"requires customer
   consent"* on a record that still carries a value: precisely the record the validator refuses to
   build, assembled in two statements instead of one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.schemas.derived import (
    PERMISSION_KEY_PREFIXES,
    ceiling_for_key,
    is_permission_key,
)


def _record(**overrides: object) -> s.DerivedAssumption:
    payload: dict[str, object] = {
        "kind": "derived_input",
        "key": "connection.mic_kw",
        "missing_input": "the site's maximum import capacity",
        "value": 500.0,
        "unit": "kW",
        "basis": "The measured peak import over the metered year is a lower bound on the agreed "
        "capacity, and no connection agreement has been supplied.",
        "derivation_method": "fable_advisor",
        "derived_by": "claude-fable-5",
        "confidence_ceiling": s.ConfidenceBand.LOW,
        "confirmation_ask": "Send the signed connection agreement stating MIC and ASC.",
    }
    payload.update(overrides)
    return s.DerivedAssumption.model_validate(payload)


# --- (a) the consent-fabrication route ---------------------------------------------------------


def test_a_consent_shaped_key_cannot_be_recorded_as_a_derived_input() -> None:
    """The exact record the review built: a fabricated consent wearing a derived fact's label."""
    with pytest.raises(ValidationError, match="names a PERMISSION"):
        _record(
            key="stream_consents.CAPACITY_MARKET",
            missing_input="the customer's authorisation to bid this site into the Capacity Market",
            value="granted",
            unit=None,
            basis="the customer indicated they were happy in principle",
            confirmation_ask="Ask the customer to authorise Capacity Market participation.",
        )


@pytest.mark.parametrize(
    "key",
    [
        "stream_consents",
        "stream_consents.CAPACITY_MARKET",
        "stream_consents.WHOLESALE_P415",
        "consents.g98",
        "consents.g99",
        "consents.g99_approved",
        "consents.g100",
        "consents.g100.reference",
        "consents.export_mpan",
        "export_mpan",
        "g99",
        "consents.dno_application_state",
        "consents.supplier_consent_state",
    ],
)
def test_every_permission_shaped_key_binds_the_permission_class(key: str) -> None:
    """A permission is not a fact in ANY field, however the producer chose to spell the key."""
    assert is_permission_key(key)
    with pytest.raises(ValidationError, match="names a PERMISSION"):
        _record(key=key, value=None, unit=None, derivation_method="not_derivable")
    # ...and the same key as a `pending_permission` is fine, which is the whole point: the rule
    # redirects the record to the honest class rather than banning the subject.
    permitted = _record(
        kind="pending_permission",
        key=key,
        value=None,
        unit=None,
        derivation_method="not_derivable",
    )
    assert permitted.report_label == "requires customer consent"
    assert "no value used" in permitted.statement()


@pytest.mark.parametrize(
    "key",
    [
        "consents.notes",
        "connection.mic_kw",
        "load_data.annual_kwh",
        "tariff_stack.duos_red_rate",
        "counterparty.sector",
    ],
)
def test_an_ordinary_key_is_not_mistaken_for_a_permission(key: str) -> None:
    assert not is_permission_key(key)


def test_the_classifier_fails_CLOSED_and_that_is_the_intended_direction() -> None:
    """``stream_consents_summary_note`` binds too, and that is the right way round.

    The stem rule matches on a ``.`` or ``_`` boundary, so a key that merely *starts* with a
    permission stem is captured as well. A false positive costs a producer one honest redirection to
    `pending_permission`; a false negative is a fabricated consent in a client report. The asymmetry
    decides it, and it is pinned so nobody "tightens" the rule into the unsafe direction.
    """
    assert is_permission_key("stream_consents_summary_note")
    assert not is_permission_key("consents.notes")


def test_the_permission_classifier_matches_on_stem_boundaries_only() -> None:
    """A prefix rule that matched bare substrings would capture unrelated fields."""
    assert is_permission_key("consents.g99")
    assert is_permission_key("consents.g99_approved")
    assert not is_permission_key("consents.g999_something_else")
    assert not is_permission_key("connection.export_mpan_notes_field")
    assert all(is_permission_key(p) for p in PERMISSION_KEY_PREFIXES)


def test_the_existing_pending_permission_errors_still_take_precedence() -> None:
    """A consent key that ALSO carries a value must still fail on the value, not the key.

    The two-class rule and the no-value rule overlap on the worst record of all. The valueless rule
    is the one that names the actual defect, so it must be the message a producer sees.
    """
    with pytest.raises(ValidationError, match="must not carry a value"):
        _record(
            kind="pending_permission",
            key="stream_consents.CAPACITY_MARKET",
            value=30.0,
            unit="gbp_per_kw_year",
            derivation_method="not_derivable",
        )


# --- (b) the ceiling bypass --------------------------------------------------------------------


def test_a_pending_permission_may_not_raise_its_own_ceiling_either() -> None:
    """`derived.py` returned early for `pending_permission`, skipping `ceiling_for_key` entirely."""
    assert ceiling_for_key("connection.mic_kw") is s.ConfidenceBand.LOW
    with pytest.raises(ValidationError, match="ceiling policy allows at most low"):
        _record(
            kind="pending_permission",
            key="connection.mic_kw",
            value=None,
            unit=None,
            derivation_method="not_derivable",
            confidence_ceiling=s.ConfidenceBand.MEDIUM,
        )


def test_the_ceiling_policy_now_binds_both_classes_identically() -> None:
    """Same key, same policy, whichever class the record is in."""
    both = (("derived_input", "not_derivable"), ("pending_permission", "not_derivable"))
    for kind, method in both:
        shape_ok = _record(
            kind=kind,
            key="load_data.half_hourly_reference_profile",
            value=None,
            unit=None,
            derivation_method=method,
            confidence_ceiling=s.ConfidenceBand.MEDIUM,
        )
        assert shape_ok.confidence_ceiling is s.ConfidenceBand.MEDIUM
        with pytest.raises(ValidationError, match="ceiling policy allows at most low"):
            _record(
                kind=kind,
                key="export_arrangement.declared_export_limit_kw",
                value=None,
                unit=None,
                derivation_method=method,
                confidence_ceiling=s.ConfidenceBand.MEDIUM,
            )


def test_high_is_still_unreachable_for_a_permission() -> None:
    with pytest.raises(ValidationError, match="never declare a HIGH"):
        _record(
            kind="pending_permission",
            key="load_data.half_hourly_reference_profile",
            value=None,
            unit=None,
            derivation_method="not_derivable",
            confidence_ceiling=s.ConfidenceBand.HIGH,
        )


# --- (c) the post-construction flip ------------------------------------------------------------


def test_a_constructed_record_cannot_be_turned_into_a_consent_afterwards() -> None:
    """The record that validated as a derived fact must still be that record when it is read."""
    rec = _record()
    assert rec.report_label == "assumed, confirmation required"
    with pytest.raises(ValidationError, match="frozen"):
        rec.kind = "pending_permission"  # type: ignore[misc]
    assert rec.kind == "derived_input"
    assert rec.report_label == "assumed, confirmation required"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "pending_permission"),
        ("key", "stream_consents.CAPACITY_MARKET"),
        ("value", 9999.0),
        ("confidence_ceiling", s.ConfidenceBand.HIGH),
        ("sources", ["a citation nobody checked"]),
        ("derivation_method", "sourced_default"),
    ],
)
def test_no_field_of_a_record_can_be_reassigned(field: str, value: object) -> None:
    """It feeds `input_hash`: the record consumed must be the record that was hashed."""
    rec = _record()
    with pytest.raises(ValidationError, match="frozen"):
        setattr(rec, field, value)


def test_model_copy_still_works_so_the_freeze_costs_nothing() -> None:
    """Frozen blocks mutation, not derivation: a new record from an old one re-validates."""
    rec = _record()
    other = rec.model_copy(update={"value": 400.0})
    assert other.value == 400.0
    assert rec.value == 500.0
    assert s.DerivedAssumption.model_validate(rec.model_dump(mode="json")) == rec


def test_the_base_class_is_deliberately_not_frozen_and_site_model_proves_why() -> None:
    """The blast-radius finding, pinned so the freeze is not "helpfully" moved to `GridQModel`.

    `SiteModel` normalises by assigning to ``self`` inside two ``mode="after"`` validators, so a
    frozen (or `validate_assignment`) base class would break site construction outright — and the
    empty-list case is the D42/D44 hash-stability path. Both assignments must keep working.
    """
    assert s.GridQModel.model_config.get("frozen") is None
    assert s.GridQModel.model_config.get("validate_assignment") is None
    assert s.DerivedAssumption.model_config["frozen"] is True
    # ...and forbidding unknown fields is still inherited, not lost to the config override.
    assert s.DerivedAssumption.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        _record(not_a_field="x")


# --- the new derivation method -----------------------------------------------------------------


def test_an_archetype_can_say_it_is_an_archetype() -> None:
    """`sourced_default` on an uncited engineering archetype is a claim of a source there isn't.

    `ProfileSource.ARCHETYPE_PENDING_SOURCE` already exists in the ingestion vocabulary; the
    register needed the same word so a reader can tell a transcribed published curve from a
    documented assumption.
    """
    rec = _record(
        key="load_data.half_hourly_reference_profile",
        value="waste_mrf",
        unit=None,
        derivation_method="archetype_pending_source",
        confidence_ceiling=s.ConfidenceBand.MEDIUM,
    )
    assert rec.derivation_method == "archetype_pending_source"
    assert rec.sources == []
    assert "no citation recorded" in rec.statement()


def test_a_permission_still_cannot_claim_the_archetype_method() -> None:
    with pytest.raises(ValidationError, match="cannot be derived from data"):
        _record(
            kind="pending_permission",
            key="load_data.half_hourly_reference_profile",
            value=None,
            unit=None,
            derivation_method="archetype_pending_source",
            confidence_ceiling=s.ConfidenceBand.MEDIUM,
        )
