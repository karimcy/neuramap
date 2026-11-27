"""WP-SITE-06 acceptance 1: the ramp terms are read from the IC-1 instance (D-NEW-SITE06-2).

All inputs are SYNTHETIC (``tests/_ramp_ridethrough_support.py``).
"""

from __future__ import annotations

from fractions import Fraction

from _ramp_ridethrough_support import (
    AS_OF,
    SITE,
    agreement,
    all_study_evidence,
    declaration,
    study,
    unknown,
)
from gridq.contracts._base import content_hash
from gridq.contracts.evidence import EvidenceType
from gridq.ops.dossier_model import AskHolder
from gridq.ramp_ridethrough import NetImportSeries, screen_ramp, screen_ramp_ridethrough
from gridq.ramp_ridethrough.ramp import AgreedRamp, NoLimitAgreed, RampTermsUnknown


def _ramp(**kw):  # type: ignore[no-untyped-def]
    return {"kind": "ramp_terms", **kw}


def test_agreed_envelope_is_exactly_the_ic1_ramp_terms() -> None:
    a = agreement()
    r = screen_ramp(a, declaration(), None)
    assert isinstance(r, AgreedRamp)
    assert r.agreement_hash == a.content_hash()
    # The fixture's 5.0 MW/min up and 10.0 MW/min down, in W/min, exactly.
    assert r.envelope.max_towards_charge_w_per_min.as_fraction() == 5_000_000
    assert r.envelope.max_towards_discharge_w_per_min.as_fraction() == 10_000_000
    assert r.envelope.label == "ceiling_for_oem_ppc_configuration_not_a_recommended_setting"
    assert [c.clause_id for c in r.basis] == ["10.3.4"]


def test_changing_only_the_ic1_ramp_changes_the_verdict() -> None:
    bess = declaration()  # PPC 4 MW/min towards charge, 8 MW/min towards discharge
    within = screen_ramp(agreement(), bess, None)
    tighter = screen_ramp(
        agreement(ramp=_ramp(max_ramp_up_mw_per_min=3.0, max_ramp_down_mw_per_min=10.0)),
        bess,
        None,
    )
    assert isinstance(within, AgreedRamp) and isinstance(tighter, AgreedRamp)
    assert within.settings.kind == "settings_check" and tighter.settings.kind == "settings_check"
    assert within.settings.towards_charge.verdict == "within"
    assert tighter.settings.towards_charge.verdict == "exceeds"
    assert tighter.settings.towards_charge.margin_w_per_min.as_fraction() == -1_000_000
    assert tighter.settings.towards_discharge.verdict == "within"


def test_a_decimal_ic1_ramp_is_read_exactly() -> None:
    r = screen_ramp(
        agreement(ramp=_ramp(max_ramp_up_mw_per_min=0.1, max_ramp_down_mw_per_min=0.3)),
        declaration(),
        None,
    )
    assert isinstance(r, AgreedRamp)
    assert r.envelope.max_towards_charge_w_per_min.as_fraction() == Fraction(100_000)
    assert r.envelope.max_towards_discharge_w_per_min.as_fraction() == Fraction(300_000)


def test_not_applicable_ramp_gives_no_limit_and_the_g99_statement() -> None:
    r = screen_ramp(
        agreement(ramp={"kind": "not_applicable", "reason": "SYNTHETIC: agreement states none"}),
        declaration(),
        None,
    )
    assert isinstance(r, NoLimitAgreed)
    assert isinstance(r.g99_assumed_max_ramp_statement, str)
    assert "4000000 W per minute towards charge" in r.g99_assumed_max_ramp_statement
    assert not hasattr(r, "settings")  # no verdict without an agreed limit


def test_unknown_ramp_is_an_ask_for_the_agreement_not_a_default() -> None:
    r = screen_ramp(agreement(ramp=unknown().model_dump()), declaration(), None)
    assert isinstance(r, RampTermsUnknown)
    (a,) = r.requests
    assert a.holder is AskHolder.CUSTOMER
    assert set(a.closes_with) == {
        EvidenceType.CONNECTION_AGREEMENT,
        EvidenceType.CONNECTION_OFFER,
    }
    assert EvidenceType.G99_APPROVAL not in a.closes_with


def test_undeclared_ppc_settings_are_not_computed_with_an_ask() -> None:
    r = screen_ramp(agreement(), declaration(ppc_ramp_towards_charge_w_per_min=unknown()), None)
    assert isinstance(r, AgreedRamp)
    assert r.settings.kind == "not_computed"
    assert any("PPC active-power ramp-limit" in a.request for a in r.requests)


def test_trip_steps_are_for_the_dno_to_interpret_never_judged() -> None:
    r = screen_ramp(agreement(), declaration(), None)
    assert isinstance(r, AgreedRamp)
    steps = {s.event: s for s in r.trip_steps if s.kind == "trip_step"}
    assert steps["trip_at_full_discharge"].import_change_w == 5_000_000
    assert steps["trip_at_full_discharge"].multiple_of_limit.as_fraction() == 1
    assert steps["trip_at_full_charge"].import_change_w == -5_000_000
    assert steps["trip_at_full_charge"].multiple_of_limit.as_fraction() == Fraction(1, 2)
    assert {s.outcome for s in steps.values()} == {"for_dno_interpretation"}
    assert any(
        a.holder is AskHolder.NETWORK_OPERATOR and "protection trip" in a.request
        for a in r.requests
    )


def test_series_check_against_the_ic1_limits() -> None:
    series = NetImportSeries(
        site_id=SITE,
        record_class="synthetic_fixture",
        basis="modelled",
        source="SYNTHETIC: illustrative minute series",
        start_utc="2026-11-26T00:00:00Z",
        import_w=(0, 4_000_000, 10_000_000, 9_000_000, -2_000_000),
    )
    r = screen_ramp(agreement(), declaration(), series, series_hash=content_hash(series))
    assert isinstance(r, AgreedRamp) and r.series.kind == "series_check"
    assert r.series.max_rise_w_per_min == 6_000_000
    assert r.series.max_fall_w_per_min == 11_000_000
    assert [(e.minute_index, e.change_w) for e in r.series.exceedances] == [
        (2, 6_000_000),
        (4, -11_000_000),
    ]
    assert r.series.verdict == "exceeds"


def test_the_record_carries_the_ramp_read_from_this_ic1_instance() -> None:
    a = agreement()
    s = screen_ramp_ridethrough(
        a,
        declaration(),
        study(),
        evidence=all_study_evidence(),
        as_of=AS_OF,
        record_class="synthetic_fixture",
    )
    assert s.agreement_hash == a.content_hash() == s.ramp.agreement_hash
    item = s.handoff.item("ic1_ramp_terms").entry
    assert item.kind == "provided" and item.value.kind == "ic1_term"
    assert {f.name: f.value.as_fraction() for f in item.value.fields} == {  # type: ignore[union-attr]
        "max_ramp_up_mw_per_min": 5,
        "max_ramp_down_mw_per_min": 10,
    }
