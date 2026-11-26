"""IC-1 ConnectionAgreement v1.0.0 conformance (roadmap WP-CONN-01).

Acceptance:
1. Contract version is tagged and conformance tests pass.
2. Every A1-01 field is present and typed; unknown values are explicit, not null.
3. Firm, hybrid and fully non-firm archetype fixtures validate.
"""

from __future__ import annotations

import json
import types
import typing
from typing import Any

import pytest
from _contracts_support import IC1_FIXTURES, dumps, load
from pydantic import BaseModel, ValidationError

from gridq.contracts import _base, connection, evidence
from gridq.contracts._base import ContractModel, NotApplicable, Unknown
from gridq.contracts.connection import (
    A1_01_FIELDS,
    CONTRACT_VERSION,
    GIT_TAG,
    IC1_EXTRA_FIELDS,
    ConnectionAgreement,
    ConnectionArchetype,
    EvidenceBacked,
    ImportProfile,
    SyntheticArchetype,
)
from gridq.contracts.evidence import EvidenceObject, evidence_ref

FIRM = "ic1_archetype_firm.synthetic.json"
HYBRID = "ic1_archetype_hybrid.synthetic.json"
NON_FIRM = "ic1_archetype_fully_non_firm.synthetic.json"

ALL_SPEC_FIELDS = sorted(
    {f for fields in (*A1_01_FIELDS.values(), *IC1_EXTRA_FIELDS.values()) for f in fields}
)
REQUIRED_FIELDS = sorted(n for n, f in ConnectionAgreement.model_fields.items() if f.is_required())


def _ca(data: dict[str, Any]) -> ConnectionAgreement:
    return ConnectionAgreement.model_validate_json(dumps(data))


def _allows_none(annotation: Any) -> bool:
    if annotation is None or annotation is type(None):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _allows_none(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return any(_allows_none(a) for a in typing.get_args(annotation))
    if origin is typing.Literal:
        return None in typing.get_args(annotation)
    return False


def _contract_models() -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    for mod in (_base, evidence, connection):
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, ContractModel)
                and obj is not ContractModel
            ):
                out.append(obj)
    return out


# ---------------------------------------------------------------- 1. version tagged


def test_contract_version_is_tagged_in_module_and_on_every_instance() -> None:
    assert CONTRACT_VERSION == "1.1.0"
    assert GIT_TAG == f"contracts/ic1-connection-agreement/v{CONTRACT_VERSION}"
    for path in IC1_FIXTURES:
        ca = _ca(load(path.name))
        assert (ca.contract, ca.contract_version) == ("IC-1", "1.0.0")


@pytest.mark.parametrize(
    "field,value",
    [("contract_version", "1.0.1"), ("contract_version", "2.0.0"), ("contract", "IC-4")],
)
def test_a_payload_of_another_contract_or_version_is_refused(field: str, value: str) -> None:
    data = load(HYBRID)
    data[field] = value
    with pytest.raises(ValidationError):
        _ca(data)


# ---------------------------------------------------------------- 2. A1-01 present, typed, no null


def test_every_a1_01_phrase_and_ic1_addition_maps_to_a_required_field() -> None:
    assert len(A1_01_FIELDS) == 14  # the 14 comma-separated A1-01 terms
    assert set(IC1_EXTRA_FIELDS) == {"required ANM response time", "signal protocol"}
    for name in ALL_SPEC_FIELDS:
        assert name in ConnectionAgreement.model_fields, name
        assert ConnectionAgreement.model_fields[name].is_required(), name


@pytest.mark.parametrize("model", _contract_models(), ids=lambda m: m.__name__)
def test_no_field_of_any_contract_model_admits_none(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert not _allows_none(field.annotation), f"{model.__name__}.{name} admits None"


def test_the_frozen_json_schemas_contain_no_null_type() -> None:
    for model in (ConnectionAgreement, EvidenceObject):
        assert '"null"' not in json.dumps(model.model_json_schema())


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_null_is_refused_for_every_required_field(field: str) -> None:
    data = load(HYBRID)
    data[field] = None
    with pytest.raises(ValidationError):
        _ca(data)


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_omitting_any_required_field_is_refused(field: str) -> None:
    data = load(HYBRID)
    del data[field]
    with pytest.raises(ValidationError):
        _ca(data)


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "unknown"},
        {"kind": "unknown", "reason": ""},
        {"kind": "unknown", "reason": "   "},
        {"reason": "kind omitted"},
        {"kind": "unknown", "reason": "x", "value": 0},
    ],
)
def test_unknown_must_be_explicit_and_carry_a_reason(value: dict[str, Any]) -> None:
    data = load(HYBRID)
    data["firm_import_mw"] = value
    with pytest.raises(ValidationError):
        _ca(data)


def test_every_spec_field_accepts_an_explicit_unknown() -> None:
    for name in ALL_SPEC_FIELDS:
        if name == "archetype":
            continue  # the archetype is the classification itself; it is always stated
        data = load(HYBRID)
        data[name] = {"kind": "unknown", "reason": f"test: {name} not stated"}
        ca = _ca(data)
        assert isinstance(getattr(ca, name), Unknown), name


def test_numbers_are_typed_not_coerced_from_strings() -> None:
    data = load(HYBRID)
    data["firm_import_mw"] = "10"
    with pytest.raises(ValidationError):
        _ca(data)


def test_the_free_text_curtailment_notes_are_not_carried() -> None:
    assert not any("note" in f for f in ConnectionAgreement.model_fields)


def test_instances_are_immutable_and_final() -> None:
    ca = _ca(load(HYBRID))
    with pytest.raises(ValidationError):
        ca.firm_import_mw = 99.0  # type: ignore[misc]
    with pytest.raises(TypeError, match="final"):

        class Looser(ConnectionAgreement):  # type: ignore[misc]
            pass


# ---------------------------------------------------------------- 3. archetype fixtures


def test_the_three_archetype_fixtures_validate() -> None:
    got = {_ca(load(p.name)).archetype for p in IC1_FIXTURES}
    assert got == set(ConnectionArchetype)
    assert len(IC1_FIXTURES) == 3


def test_fixtures_are_marked_synthetic_and_cite_public_sources() -> None:
    for path in IC1_FIXTURES:
        ca = _ca(load(path.name))
        assert isinstance(ca.source, SyntheticArchetype)
        assert "SYNTHETIC ARCHETYPE FOR SCHEMA TESTING" in ca.source.statement
        assert "not a real customer agreement" in ca.source.statement
        assert ca.site_id.startswith("SYNTHETIC-")
        assert ca.agreement_ref.startswith("SYNTH-")
        assert isinstance(ca.network_operator, Unknown)
        assert any(c.url.startswith("https://") for c in ca.source.based_on)


def test_archetype_specific_shapes() -> None:
    firm, hybrid, nonfirm = _ca(load(FIRM)), _ca(load(HYBRID)), _ca(load(NON_FIRM))
    assert (firm.firm_import_mw, firm.non_firm_import_mw) == (20.0, 0.0)
    assert isinstance(firm.lifo_positions, NotApplicable)
    assert (hybrid.firm_import_mw, hybrid.non_firm_import_mw) == (10.0, 20.0)
    assert hybrid.tenure == "interim" and hybrid.signal_type == "setpoint"
    assert (nonfirm.firm_import_mw, nonfirm.non_firm_import_mw) == (0.0, 15.0)
    assert isinstance(nonfirm.profiled_import, ImportProfile)
    assert nonfirm.profiled_import.covers_whole_year
    assert nonfirm.signal_type == "trip"


@pytest.mark.parametrize(
    "fixture,changes,msg",
    [
        (FIRM, {"non_firm_import_mw": 5.0}, "non_firm_import_mw == 0"),
        (NON_FIRM, {"firm_import_mw": 1.0}, "firm_import_mw == 0"),
        (HYBRID, {"firm_import_mw": 0.0}, "hybrid"),
        (FIRM, {"firm_import_mw": 0.0}, "empty|firm_import_mw > 0"),
    ],
)
def test_archetype_and_capacities_must_agree(
    fixture: str, changes: dict[str, Any], msg: str
) -> None:
    with pytest.raises(ValidationError, match=msg):
        _ca({**load(fixture), **changes})


def test_unknown_capacities_skip_the_consistency_check_rather_than_guess() -> None:
    data = load(HYBRID)
    data["firm_import_mw"] = {"kind": "unknown", "reason": "offer letter page missing"}
    assert isinstance(_ca(data).firm_import_mw, Unknown)


def test_profile_blocks_may_not_overlap_or_exceed_capacity() -> None:
    data = load(NON_FIRM)
    data["profiled_import"]["blocks"][1]["start_period"] = 30
    with pytest.raises(ValidationError, match="overlap"):
        _ca(data)
    data = load(NON_FIRM)
    data["profiled_import"]["blocks"][0]["max_import_mw"] = 16.0
    with pytest.raises(ValidationError, match="exceeds"):
        _ca(data)


@pytest.mark.parametrize(
    "limit",
    [
        {"kind": "curtailment_limit", "basis": "hours_per_year", "value": 9000.0},
        {"kind": "curtailment_limit", "basis": "percent_of_energy_per_year", "value": 101.0},
        {"kind": "curtailment_limit", "basis": "hours_per_year", "value": -1.0},
    ],
)
def test_curtailment_limit_is_bounded(limit: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _ca({**load(HYBRID), "curtailment_limit": limit})


def test_lifo_positions_are_checked() -> None:
    data = load(NON_FIRM)
    data["lifo_positions"][0]["position"] = 8
    with pytest.raises(ValidationError, match="queue length"):
        _ca(data)
    data = load(NON_FIRM)
    data["lifo_positions"].append(dict(data["lifo_positions"][0]))
    with pytest.raises(ValidationError, match="one LIFO position"):
        _ca(data)


def test_a_non_firm_agreement_needs_a_signal_or_a_profile() -> None:
    data = load(HYBRID)
    data["signal_type"] = {"kind": "not_applicable", "reason": "test"}
    with pytest.raises(ValidationError, match="profile"):
        _ca(data)


def test_a_real_agreement_references_its_ic4_evidence_by_hash() -> None:
    offer = EvidenceObject.model_validate_json(
        dumps(load("ic4_valid_connection_offer.synthetic.json"))
    )
    data = load(HYBRID)
    data["source"] = evidence_ref(offer).model_dump(mode="json")
    data["source"] = {"kind": "evidence_backed", "evidence": data["source"]}
    ca = _ca(data)
    assert isinstance(ca.source, EvidenceBacked)
    assert ca.source.evidence.evidence_hash == offer.content_hash()
    bad = json.loads(json.dumps(data))
    bad["source"]["evidence"]["contract_version"] = "0.9.0"
    with pytest.raises(ValidationError):
        _ca(bad)
