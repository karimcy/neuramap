"""IC-11 thermal flexibility v1.1.0 (roadmap WP-BO-06; plan docs/shared_work/wp_bo06_plan.md §6).

What changes: IC-4 references and the surface reference accept the 1.x range; ``on_behalf_of``
gains the six IC-4 1.1.0 third-party roles; `availability_ref` states the surface's own version.
A surface or instruction labelled 1.0.0 may hold only what 1.0.0 allowed (the down-level rule).
The acknowledgement changes only its own version. Cells, curves, limits, `check_instruction`,
`verify_acknowledgement` and the meaning of a declared kW do not change, and the 1.0.0
conformance tests (``test_ic11_thermal_flex.py``) still run against the 1.1.0 code.

The cross-link: a ``trialled`` surface resting on Neura's own trial record and baseline method
(IC-4 1.1.0, ADR-D10 option b). `resolve_ref` refuses those records unless the consumer names the
slot's type and admits self-issued evidence.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import _ic11_fixtures
import pytest
from _contracts_support import (
    GOLDEN_HASHES_V110,
    IC4_FIXTURES,
    IC4_V110_FIXTURES,
    dumps,
    load,
    load_v110,
)
from pydantic import ValidationError

from gridq.contracts._base import Unknown
from gridq.contracts.evidence import (
    V1_0_0_ISSUER_ROLES,
    EvidenceObject,
    EvidenceRefused,
    EvidenceType,
    IssuerRole,
    SelfIssuedEvidenceRefused,
    SyntheticEvidenceRefused,
    evidence_ref,
    require_third_party,
    resolve_ref,
)
from gridq.contracts.thermal_flex import (
    CONTRACT_VERSION,
    GIT_TAG,
    SUPPORTED_VERSIONS,
    AvailabilityRef,
    EvidenceClass,
    Flex,
    FlexAcknowledgement,
    FlexInstruction,
    ThermalFlexAvailability,
    TrialledBasis,
    ZeroFlex,
    availability_ref,
)

MODELLED = "ic11_surface_chilled_water_modelled.synthetic.json"
METERED = "ic11_surface_metered_trial.synthetic.json"
INSTRUCTION = "ic11_instruction_winter_night.synthetic.json"
ACKS = (
    "ic11_ack_accepted.synthetic.json",
    "ic11_ack_partial.synthetic.json",
    "ic11_ack_rejected.synthetic.json",
)
TRIALLED = "ic11_surface_trialled_self_issued.synthetic.json"
BASELINE = "ic4_self_issued_baseline_method.synthetic.json"
TRIAL = "ic4_self_issued_trial_record.synthetic.json"
NEW_ROLES = sorted(r.value for r in IssuerRole if r.value not in V1_0_0_ISSUER_ROLES)


def _surface(data: dict[str, Any]) -> ThermalFlexAvailability:
    return ThermalFlexAvailability.model_validate_json(dumps(data))


def _instruction(data: dict[str, Any]) -> FlexInstruction:
    return FlexInstruction.model_validate_json(dumps(data))


def _evidence_objects() -> list[EvidenceObject]:
    return [EvidenceObject.model_validate_json(dumps(load(p.name))) for p in IC4_FIXTURES] + [
        EvidenceObject.model_validate_json(dumps(load_v110(p.name))) for p in IC4_V110_FIXTURES
    ]


def _ic4_ref(version: str, char: str = "d") -> dict[str, Any]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": version,
        "evidence_hash": "sha256:" + char * 64,
    }


# ------------------------------------------------------------------ 1. version and fixture


def test_contract_version_is_1_1_0_and_reads_1_0_0() -> None:
    assert CONTRACT_VERSION == "1.1.0"
    assert SUPPORTED_VERSIONS == ("1.0.0", "1.1.0")
    assert SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == "contracts/ic11-thermal-flex/v1.1.0"
    for model in (ThermalFlexAvailability, FlexInstruction, FlexAcknowledgement):
        assert model.model_fields["contract_version"].default == "1.1.0"


def test_the_trialled_surface_validates_and_matches_its_golden_hash() -> None:
    obj = _surface(load_v110(TRIALLED))
    assert (obj.contract, obj.contract_version) == ("IC-11", "1.1.0")
    assert obj.content_hash() == GOLDEN_HASHES_V110[TRIALLED]
    assert obj.evidence_class is EvidenceClass.TRIALLED
    assert obj.record_class == "synthetic_fixture"


def test_the_trialled_surface_rests_on_neuras_own_trial_record_and_baseline_method() -> None:
    obj = _surface(load_v110(TRIALLED))
    basis = obj.evidence
    assert isinstance(basis, TrialledBasis)
    trial = EvidenceObject.model_validate_json(dumps(load_v110(TRIAL)))
    baseline = EvidenceObject.model_validate_json(dumps(load_v110(BASELINE)))
    assert basis.trial_evidence == (evidence_ref(trial),)
    assert basis.baseline_method == evidence_ref(baseline)
    assert {r.contract_version for r in (*basis.trial_evidence, basis.baseline_method)} == {"1.1.0"}
    for record in (trial, baseline):
        assert record.is_self_issued
        assert record.scope.site_id == obj.site_id
    # The baseline method was fixed before the trial began; the trial record follows it.
    assert isinstance(baseline.effective_from, date)
    assert baseline.effective_from < basis.observed_from.date()
    assert basis.observed_until.date() < trial.issued_on


def test_the_trialled_surface_has_findings_only_in_the_trialled_months() -> None:
    obj = _surface(load_v110(TRIALLED))
    for cell in obj.cells:
        trialled = cell.season_id == "jul_sep"
        finding = isinstance(cell.availability, Flex | ZeroFlex)
        assert finding is trialled, cell
        if not trialled:
            assert isinstance(cell.availability, Unknown)


def test_committed_v110_surface_equals_a_fresh_build() -> None:
    assert set(_ic11_fixtures.BUILDERS_V110) == {_ic11_fixtures.SURFACE_TRIALLED_V110}
    for path, build in _ic11_fixtures.BUILDERS_V110.items():
        assert path.read_text(encoding="utf-8") == _ic11_fixtures.render(build()), path.name
    assert _ic11_fixtures.TRIAL_RECORD_HASH == GOLDEN_HASHES_V110[TRIAL]
    assert _ic11_fixtures.BASELINE_METHOD_HASH == GOLDEN_HASHES_V110[BASELINE]


# ------------------------------------------------------------------ 2. the cross-link


@pytest.mark.parametrize(
    "slot,kind",
    [
        ("trial_evidence", EvidenceType.TRIAL_RECORD),
        ("baseline_method", EvidenceType.BASELINE_METHOD),
    ],
)
def test_resolve_ref_refuses_the_surfaces_own_records_by_default_and_returns_them_when_admitted(
    slot: str, kind: EvidenceType
) -> None:
    surface = _surface(load_v110(TRIALLED))
    basis = surface.evidence
    assert isinstance(basis, TrialledBasis)
    ref = basis.trial_evidence[0] if slot == "trial_evidence" else basis.baseline_method
    objects = _evidence_objects()
    with pytest.raises(SyntheticEvidenceRefused):
        resolve_ref(ref, objects)
    with pytest.raises(SelfIssuedEvidenceRefused, match="self-issued by Neura"):
        resolve_ref(ref, objects, include_synthetic=True)
    with pytest.raises(SelfIssuedEvidenceRefused):
        resolve_ref(ref, objects, expected_type=kind, include_synthetic=True)
    with pytest.raises(ValueError, match="name the expected_type"):
        resolve_ref(ref, objects, admit_self_issued=True, include_synthetic=True)
    other = (
        EvidenceType.BASELINE_METHOD
        if kind is EvidenceType.TRIAL_RECORD
        else EvidenceType.TRIAL_RECORD
    )
    with pytest.raises(EvidenceRefused, match="expected") as caught:
        resolve_ref(
            ref, objects, expected_type=other, admit_self_issued=True, include_synthetic=True
        )
    assert type(caught.value) is EvidenceRefused
    found = resolve_ref(
        ref, objects, expected_type=kind, admit_self_issued=True, include_synthetic=True
    )
    assert found.evidence_type is kind
    assert found.is_self_issued
    assert found.content_hash() == ref.evidence_hash
    assert found.scope.site_id == surface.site_id
    with pytest.raises(SelfIssuedEvidenceRefused):
        require_third_party(found)


# ------------------------------------------------------------------ 3. the down-level rule


def test_down_level_the_trialled_surface_cannot_be_labelled_1_0_0() -> None:
    data = load_v110(TRIALLED)
    data["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError, match="not in IC-11 1.0.0"):
        _surface(data)


def _set_meter_ref(data: dict[str, Any], version: str) -> None:
    data["evidence"]["meter_evidence"][0]["contract_version"] = version


def _set_metered_baseline(data: dict[str, Any], version: str) -> None:
    data["evidence"]["baseline_method"]["contract_version"] = version


def _set_sla_ref(data: dict[str, Any], version: str) -> None:
    data["limits"]["customer_sla"]["evidence"]["contract_version"] = version


def _set_calibration(data: dict[str, Any], version: str) -> None:
    data["evidence"]["calibration_evidence"] = [_ic4_ref(version)]


SURFACE_REFS = [
    (METERED, _set_meter_ref),
    (METERED, _set_metered_baseline),
    (METERED, _set_sla_ref),
    (MODELLED, _set_calibration),
]


@pytest.mark.parametrize("name,setter", SURFACE_REFS, ids=lambda v: getattr(v, "__name__", v))
def test_down_level_a_1_0_0_surface_with_a_1_1_0_reference_is_refused(
    name: str, setter: Any
) -> None:
    data = load(name)
    setter(data, "1.1.0")
    with pytest.raises(ValidationError, match="not in IC-11 1.0.0"):
        _surface(data)
    data["contract_version"] = "1.1.0"
    assert _surface(data).contract_version == "1.1.0"
    setter(data, "1.0.0")
    data["contract_version"] = "1.0.0"
    assert _surface(data).contract_version == "1.0.0"


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
@pytest.mark.parametrize("version", ["2.0.0", "0.9.0"])
@pytest.mark.parametrize("name,setter", SURFACE_REFS, ids=lambda v: getattr(v, "__name__", v))
def test_a_surface_reference_outside_1x_is_refused_at_either_version(
    name: str, setter: Any, version: str, label: str
) -> None:
    data = load(name)
    data["contract_version"] = label
    setter(data, version)
    with pytest.raises(ValidationError):
        _surface(data)


@pytest.mark.parametrize("role", NEW_ROLES)
def test_down_level_a_1_0_0_instruction_with_a_new_role_is_refused(role: str) -> None:
    data = load(INSTRUCTION)
    data["on_behalf_of"] = role
    with pytest.raises(ValidationError, match="not in IC-11 1.0.0"):
        _instruction(data)
    data["contract_version"] = "1.1.0"
    assert _instruction(data).on_behalf_of == IssuerRole(role)


@pytest.mark.parametrize("role", sorted(V1_0_0_ISSUER_ROLES))
def test_down_level_admits_every_1_0_0_role_at_1_0_0(role: str) -> None:
    data = load(INSTRUCTION)
    data["on_behalf_of"] = role
    assert _instruction(data).contract_version == "1.0.0"


def test_down_level_a_1_0_0_instruction_with_a_1_1_0_surface_reference_is_refused() -> None:
    data = load(INSTRUCTION)
    data["availability"]["contract_version"] = "1.1.0"
    with pytest.raises(ValidationError, match="not in IC-11 1.0.0"):
        _instruction(data)
    data["contract_version"] = "1.1.0"
    assert _instruction(data).availability.contract_version == "1.1.0"


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
def test_on_behalf_of_refuses_neura_self_issued_at_either_version(label: str) -> None:
    data = load(INSTRUCTION)
    data["contract_version"] = label
    data["on_behalf_of"] = "neura_self_issued"
    with pytest.raises(ValidationError):
        _instruction(data)


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
@pytest.mark.parametrize("version", ["2.0.0", "0.9.0"])
def test_an_instructions_surface_reference_outside_1x_is_refused(label: str, version: str) -> None:
    data = load(INSTRUCTION)
    data["contract_version"] = label
    data["availability"]["contract_version"] = version
    with pytest.raises(ValidationError):
        _instruction(data)


@pytest.mark.parametrize("name", ACKS)
def test_an_acknowledgement_changes_only_its_own_version(name: str) -> None:
    data = load(name)
    old = FlexAcknowledgement.model_validate_json(dumps(data))
    data["contract_version"] = "1.1.0"
    new = FlexAcknowledgement.model_validate_json(dumps(data))
    assert old.contract_version == "1.0.0"
    assert new.model_dump(exclude={"contract_version"}) == old.model_dump(
        exclude={"contract_version"}
    )


# ------------------------------------------------------------------ 4. availability_ref


def test_availability_ref_carries_the_surfaces_own_version() -> None:
    for data, version in ((load(MODELLED), "1.0.0"), (load_v110(TRIALLED), "1.1.0")):
        obj = _surface(data)
        ref = availability_ref(obj)
        assert (ref.contract, ref.contract_version) == ("IC-11", version)
        assert ref.availability_hash == obj.content_hash()


def test_a_1_1_0_instruction_may_plan_against_the_1_1_0_surface() -> None:
    surface = _surface(load_v110(TRIALLED))
    data = load(INSTRUCTION)
    data["contract_version"] = "1.1.0"
    data["site_id"] = surface.site_id
    data["availability"] = availability_ref(surface).model_dump(mode="json")
    obj = _instruction(data)
    assert obj.availability == availability_ref(surface)
    data["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError, match="not in IC-11 1.0.0"):
        _instruction(data)


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.10.0"])
def test_an_availability_ref_accepts_the_1x_range(version: str) -> None:
    payload = {
        "kind": "thermal_flex_availability_ref",
        "contract": "IC-11",
        "contract_version": version,
        "availability_hash": "sha256:" + "e" * 64,
    }
    assert AvailabilityRef.model_validate_json(dumps(payload)).contract_version == version
    payload["contract_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        AvailabilityRef.model_validate_json(dumps(payload))
