"""Three D45 hardening gaps that survived the pass which closed the first three.

`tests/test_derived_assumption_hardening.py` closed the adversarial review's three routes back into
a fabricated consent (the consent-shaped key, the ceiling bypass, the post-construction flip). Each
of the three below defeats one of those closures, or repeats their defect one class along:

1. **The consent bind was defeated by the SHIFT KEY.** `is_permission_key` compared raw strings, so
   ``key="Consents.G99"`` with ``value="granted"`` validated as a `derived_input` and rendered under
   *"assumed, confirmation required"*. The pinned intent of that classifier is that it
   **fails closed** (`test_the_classifier_fails_CLOSED_and_that_is_the_intended_direction`); on case
   it failed open, and no hostile intent was required to reach it.
2. **The schema still could not say "this does not apply to this site".**
   `test_value_stack_null_input_guard.py::test_the_guard_cannot_tell_not_applicable_from_not_sourced`
   records that limit and names "a third class in `schemas.derived`" as the remedy. Live on the real
   portfolio: Colnbrook and Tanhouse are 100% private-wire, their own records say in prose that no
   distribution charge exists for them, and both were nevertheless forced into `derived_input`, so
   the register presented an evidenced absence as an assumption and asked those customers for a rate
   no supplier could issue them.
3. **One DUoS band out of three read as a complete network stack.** `_tariff_factor` tested
   ``any(...)``, so a site with red priced and amber/green null was reported at **HIGH** as
   "commodity, network and levy lines all present". A HIGH factor is never a binding factor, so the
   false completeness claim was made and the sentence that would let a reader check it was never
   shown. It is also the wrong way round: a partial stack consumes the absent bands as zero, which
   **overstates** red-band avoidance, while an all-null stack understates it.

**Why these tests run through `generate_quote`.** `docs/validation_requirements.md` §3 records
*eleven* defects built, tested, merged and never called, **every one with a passing test**, because
the tests called the new code directly. So the acceptance cases here start at `generate_quote`
(`gridq.quotes.pipeline`, reached from `cli.py`) and assert on the persisted `QuoteRecord` and the
`ConfidenceAssessment` a reader actually sees. The direct-construction cases are the supporting
half and never the acceptance.

**Direction of effect: no priced number moves in any of the three.** (1) tightens construction only.
(2) is a disclosure class that carries no value and is folded into the band exactly as every other
class, deliberately, so the private-wire sites' band does not RISE on a relabel. (3) can only lower
a band, HIGH to MEDIUM, and only for a partially-populated DUoS stack, of which the real portfolio
today has none (all nine site files are all-three or none).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.quotes import generate_quote
from gridq.quotes.confidence import assess_confidence
from gridq.schemas.derived import is_permission_key

PRIVATE_WIRE_KEY = "tariff_stack.duos_red_rate"
SHAPE_KEY = "load_data.half_hourly_reference_profile"


@pytest.fixture(scope="module")
def assumptions():
    return load_assumptions("gb_v1_6")


def _record(**overrides: object) -> dict[str, object]:
    """The Colnbrook shape: a DUoS line that does not exist because the site is private-wire."""
    payload: dict[str, object] = {
        "kind": "not_applicable",
        "key": PRIVATE_WIRE_KEY,
        "missing_input": "the site's DNO distribution charge basis",
        "value": None,
        "unit": None,
        "basis": "No distribution charge applies: the supply is 100% private wire, so the site is "
        "not supplied through the DNO assets DUoS recovers, and the non-commodity cost it does pay "
        "is already in the stack as a back-solved residual. Adding a rate would double-count it.",
        "derivation_method": "not_derivable",
        "derived_by": "gridq.tests",
        "confidence_ceiling": s.ConfidenceBand.MEDIUM,
        "confirmation_ask": "Confirm the supply is wholly private wire with no DNO-charged import.",
    }
    payload.update(overrides)
    return payload


def _site_with(make_site: Callable[..., s.SiteModel], *records: dict[str, object]) -> s.SiteModel:
    """A site carrying ``records`` on its register, built through the real validation path."""
    dump = make_site().model_dump(mode="json")
    dump["derived_assumptions"] = list(records)
    return s.SiteModel.model_validate(dump)


def _factor(assessment, name: str):
    return next(f for f in assessment.factors if f.name == name)


def _counts(run) -> dict[str, int]:
    """The register the run ACTUALLY consumed, by class.

    Read off `QuoteRunResult.derived_assumptions` rather than hard-coded, because the pipeline adds
    the gates' own records (Gate 2's basis, Gate 3's reconciliation) to the site's, and a literal
    count here would pin the number of gates rather than the property under test.
    """
    out = {"derived_input": 0, "pending_permission": 0, "not_applicable": 0}
    for record in run.derived_assumptions:
        out[record.kind] += 1
    return out


# =============================================================================== gap 1: the case
# ACCEPTANCE. `SiteModel.model_validate` is the site-loading path `cli.py` runs on every file.


def test_a_miscased_consent_key_cannot_enter_the_register_as_a_derived_fact(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """**The whole consent-fabrication route, reopened by one capital letter.**

    Before the fold this payload loaded, and the register then rendered
    *"G99 approval is missing, assumed, confirmation required. used granted."* to a lender: an
    invented third-party approval in the one section of the report whose purpose is to disclose
    what was assumed. The refusal has to happen at site load, because by the time anything reads
    the record the fabricated sentence already exists.
    """
    fabricated = _record(
        kind="derived_input",
        key="Consents.G99",
        missing_input="G99 approval",
        value="granted",
        derivation_method="fable_advisor",
        confidence_ceiling=s.ConfidenceBand.LOW,
        confirmation_ask="Send the DNO's G99 acceptance letter.",
    )
    with pytest.raises(ValidationError, match="names a PERMISSION"):
        _site_with(make_site, fabricated)


@pytest.mark.parametrize(
    "key",
    [
        "Consents.G99",
        "CONSENTS.G99",
        "Stream_Consents.CAPACITY_MARKET",
        "STREAM_CONSENTS.WHOLESALE_P415",
        "Export_MPAN",
        "G100",
        "Consents.DNO_Application_State",
    ],
)
def test_the_permission_bind_is_case_insensitive_in_every_spelling(key: str) -> None:
    """A key is written by a human. Case is not a security boundary and never was one."""
    assert is_permission_key(key)


def test_the_case_fold_does_not_capture_ordinary_keys() -> None:
    """Folding case must not widen the stem rule into unrelated fields."""
    for key in ("Connection.MIC_kW", "Tariff_Stack.duos_red_rate", "Consents.Notes"):
        assert not is_permission_key(key)


def test_the_honest_class_still_loads_with_the_same_miscased_key(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The rule redirects the record, it does not ban the subject (the point of the two classes)."""
    site = _site_with(
        make_site,
        _record(
            kind="pending_permission",
            key="Consents.G99",
            missing_input="the DNO's G99 approval for this connection",
            confidence_ceiling=s.ConfidenceBand.LOW,
            confirmation_ask="Send the DNO's G99 acceptance letter.",
        ),
    )
    (record,) = site.assumptions_requiring_confirmation
    assert record.report_label == "requires customer consent"
    assert record.value is None
    assert "no value used" in record.statement()


# ========================================================================== gap 2: the third class
# ACCEPTANCE. Through `generate_quote`, asserting on what the reader is handed.


def test_an_evidenced_absence_reaches_the_pack_as_an_absence_not_an_assumption(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """**The reader-layer acceptance for the third class.**

    Production call site: ``gridq.quotes.confidence._derived_factor`` reads `_register_composition`
    and `DerivedAssumption.report_label`, reached from ``generate_quote`` ->
    ``quotes/pipeline.py`` -> ``assess_confidence``. Deleting either read leaves this red, because
    the sentence is what the assertion is on.

    What must be true for the private-wire sites: the register says the DUoS line is an EVIDENCED
    ABSENCE, the confirmation ask is to confirm non-applicability, and nowhere does the run tell the
    customer that a rate they can never supply was "assumed".
    """
    run = generate_quote(_site_with(make_site, _record()), assumptions)

    counts = _counts(run)
    assert counts["not_applicable"] == 1, "fixture is inert: the record did not reach the run"
    factor = _factor(run.confidence, "derived assumptions")
    assert "1 input(s) are EVIDENCED ABSENCES, not assumptions" in factor.reason
    assert f"{counts['derived_input']} input(s) required an assumption" in factor.reason, (
        "the evidenced absence must be counted OUT of the assumption count, not merely "
        "mentioned alongside it: that count is the sentence a reader trusts"
    )

    register = [w for w in run.quote.warnings if PRIVATE_WIRE_KEY in w]
    assert register, "the record must reach the quote's own warnings, not only the factor"
    assert any("does not apply to this site, evidenced" in w for w in register)
    assert any("EMPTY BY EVIDENCE rather than unfilled" in w for w in register)
    assert not any("assumed, confirmation required" in w for w in register), (
        "'assumed, confirmation required' on a private-wire DUoS line is the sentence that sent a "
        "data ask for a distribution rate no supplier could issue that customer"
    )
    assert not any("is missing" in w for w in register)


def test_the_three_classes_are_counted_separately_in_one_sentence(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """A register holding all three classes must not collapse them into one word."""
    run = generate_quote(
        _site_with(
            make_site,
            _record(),
            _record(
                kind="derived_input",
                key="connection.mic_kw",
                missing_input="the site's agreed import capacity",
                value=500.0,
                unit="kW",
                derivation_method="fable_advisor",
                confidence_ceiling=s.ConfidenceBand.LOW,
                confirmation_ask="Send the signed connection agreement.",
            ),
            _record(
                kind="pending_permission",
                key="stream_consents.CAPACITY_MARKET",
                missing_input="authorisation to bid this site into the Capacity Market",
                confidence_ceiling=s.ConfidenceBand.LOW,
                confirmation_ask="Confirm Capacity Market participation is authorised.",
            ),
        ),
        assumptions,
    )

    counts = _counts(run)
    reason = _factor(run.confidence, "derived assumptions").reason
    assert counts["pending_permission"] == 1 and counts["not_applicable"] == 1
    assert f"{counts['derived_input']} input(s) required an assumption" in reason
    assert "1 input(s) await a customer permission" in reason
    assert "1 input(s) are EVIDENCED ABSENCES, not assumptions" in reason


def test_relabelling_a_record_as_not_applicable_moves_no_band_and_no_number(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """**The direction-of-effect pin, and the reason it is a pin rather than a nicety.**

    Exempting an evidenced absence from the band fold would RAISE the confidence band on exactly the
    two sites the class was written for, which is the direction that needs new evidence rather than
    a relabel. The class is disclosure: the same key, the same ceiling, the same band, the same £.
    """
    absence = generate_quote(_site_with(make_site, _record()), assumptions)
    assumed = generate_quote(
        _site_with(
            make_site,
            _record(
                kind="derived_input",
                value=0.0713,
                unit="gbp_per_kwh",
                derivation_method="sourced_default",
            ),
        ),
        assumptions,
    )

    assert absence.quote.confidence_band is assumed.quote.confidence_band
    assert _factor(absence.confidence, "derived assumptions").ceiling is (
        _factor(assumed.confidence, "derived assumptions").ceiling
    )


def test_a_permission_shaped_key_cannot_hide_in_the_new_class(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """ "This consent does not apply" is still an assertion about a third party's decision.

    Without this the third class would be a fresh route to the very record the first hardening pass
    closed: a consent-shaped key, valueless, but labelled as an evidenced fact about the site.
    """
    with pytest.raises(ValidationError, match="names a PERMISSION"):
        _site_with(
            make_site,
            _record(
                key="stream_consents.CAPACITY_MARKET",
                missing_input="Capacity Market authorisation",
                confidence_ceiling=s.ConfidenceBand.LOW,
                confirmation_ask="Confirm Capacity Market participation is authorised.",
            ),
        )


def test_an_evidenced_absence_can_never_carry_a_value(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The class means the component does not exist here, so a rate on it is a fabricated charge."""
    with pytest.raises(ValidationError, match="must not carry a value or unit"):
        _site_with(make_site, _record(value=0.0713, unit="gbp_per_kwh"))
    with pytest.raises(ValidationError, match="requires derivation_method='not_derivable'"):
        _site_with(make_site, _record(derivation_method="sourced_default"))


def test_the_ceiling_policy_binds_the_third_class_too(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """No producer raises its own ceiling, whichever of the three classes it writes in."""
    with pytest.raises(ValidationError, match="ceiling policy allows at most low"):
        _site_with(
            make_site,
            _record(key="connection.mic_kw", confidence_ceiling=s.ConfidenceBand.MEDIUM),
        )
    with pytest.raises(ValidationError, match="never declare a HIGH"):
        _site_with(make_site, _record(confidence_ceiling=s.ConfidenceBand.HIGH))


def test_the_three_report_labels_never_share_a_sentence(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _site_with(
        make_site,
        _record(),
        _record(
            kind="derived_input",
            key="connection.mic_kw",
            value=500.0,
            unit="kW",
            derivation_method="fable_advisor",
            confidence_ceiling=s.ConfidenceBand.LOW,
        ),
        _record(
            kind="pending_permission",
            key="stream_consents.CAPACITY_MARKET",
            confidence_ceiling=s.ConfidenceBand.LOW,
        ),
    )
    labels = {r.report_label for r in site.assumptions_requiring_confirmation}
    assert labels == {
        "assumed, confirmation required",
        "requires customer consent",
        "does not apply to this site, evidenced",
    }


def test_only_a_derived_input_discharges_the_modelled_shape_disclosure(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """A valueless record on a shape key silenced REGISTER INCOMPLETE while disclosing nothing.

    The shape test read the KEY alone, and the schema permits a `pending_permission` on a shape key,
    so *"requires customer consent"* against `load_data.half_hourly_reference_profile` satisfied it:
    no archetype named, no anchor, nothing for a reader to check, and the factor's own guarantee
    (that a modelled site can never claim a complete register) quietly gone.
    """
    dump = make_site().model_dump(mode="json")
    dump["load_data"]["half_hourly_basis"] = "modelled"
    dump["derived_assumptions"] = [
        _record(
            kind="pending_permission",
            key=SHAPE_KEY,
            missing_input="the reference profile behind the modelled shape",
            confidence_ceiling=s.ConfidenceBand.MEDIUM,
            confirmation_ask="Confirm the archetype used.",
        )
    ]
    run = generate_quote(s.SiteModel.model_validate(dump), assumptions)

    reason = _factor(run.confidence, "derived assumptions").reason
    assert "REGISTER INCOMPLETE" in reason, (
        "a permission record discloses no shape, so it cannot discharge the shape disclosure"
    )


# ======================================================================== gap 3: the partial stack
# ACCEPTANCE. Through `generate_quote`, on the factor a reader is shown.


def _partial_tariff_site(make_site: Callable[..., s.SiteModel]) -> s.SiteModel:
    """Red priced, amber and green null: the shape the old ``any(...)`` rated HIGH."""
    dump = make_site().model_dump(mode="json")
    dump["tariff_stack"].update(
        {
            "duos_red_rate": 0.06515,
            "duos_amber_rate": None,
            "duos_green_rate": None,
            "levies": {"RO": 0.025, "CfD": 0.008},
        }
    )
    return s.SiteModel.model_validate(dump)


def test_a_partial_duos_stack_is_not_reported_as_a_complete_tariff(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """**One band out of three used to certify all three, at the top band, unbindingly.**

    Production call site: ``gridq.quotes.confidence._tariff_factor``, reached from
    ``generate_quote`` -> ``quotes/pipeline.py`` -> ``assess_confidence``. The assertion is on the
    reason string a reader sees, so restoring ``any(...)`` leaves this red.
    """
    run = generate_quote(_partial_tariff_site(make_site), assumptions)

    factor = _factor(run.confidence, "tariff")
    assert factor.ceiling is s.ConfidenceBand.MEDIUM
    assert "all present" not in factor.reason
    assert "2 of 3 band rates are null" in factor.reason
    assert "tariff_stack.duos_amber_rate" in factor.reason
    assert "tariff_stack.duos_green_rate" in factor.reason
    assert "OVERSTATED" in factor.reason, (
        "the direction matters: an absent band is consumed as zero, which widens the modelled "
        "red-to-green spread and prices red-band avoidance HIGH, not low"
    )


def test_a_complete_duos_stack_still_reads_as_complete(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """The fix must tighten, never refuse a tariff that IS evidenced."""
    dump = make_site().model_dump(mode="json")
    dump["tariff_stack"].update(
        {
            "duos_red_rate": 0.06515,
            "duos_amber_rate": 0.00827,
            "duos_green_rate": 0.001,
            "levies": {"RO": 0.025, "CfD": 0.008},
        }
    )
    run = generate_quote(s.SiteModel.model_validate(dump), assumptions)

    factor = _factor(run.confidence, "tariff")
    assert factor.ceiling is s.ConfidenceBand.HIGH
    assert factor.reason == "commodity, network and levy lines all present"


def test_an_all_null_stack_keeps_its_own_wording_and_its_own_direction(
    make_site: Callable[..., s.SiteModel], assumptions
) -> None:
    """Colnbrook and Tanhouse: all three null. Understated, not overstated, and worded that way."""
    dump = make_site().model_dump(mode="json")
    dump["tariff_stack"].update(
        {
            "duos_red_rate": None,
            "duos_amber_rate": None,
            "duos_green_rate": None,
            "levies": {"private_wire_non_commodity_residual": 0.0413135},
        }
    )
    run = generate_quote(s.SiteModel.model_validate(dump), assumptions)

    factor = _factor(run.confidence, "tariff")
    assert factor.ceiling is s.ConfidenceBand.MEDIUM
    assert "DUoS absent" in factor.reason
    assert "understated" in factor.reason
    assert "OVERSTATED" not in factor.reason


def test_the_partial_branch_names_the_missing_levies_too(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Supporting case: a partial stack with no levies must not silently drop the levy gap."""
    dump = _partial_tariff_site(make_site).model_dump(mode="json")
    dump["tariff_stack"]["levies"] = {}
    site = s.SiteModel.model_validate(dump)

    assessment = assess_confidence(site, _QuoteStub())
    factor = _factor(assessment, "tariff")
    assert factor.ceiling is s.ConfidenceBand.MEDIUM
    assert "The levy lines are absent as well." in factor.reason


class _QuoteStub:
    """The minimum `assess_confidence` reads off a quote, for the supporting cases only."""

    uses_unconfirmed_assumptions = False
    value_streams: tuple[()] = ()
    optimisation_result = None
    sizing_grid = None
