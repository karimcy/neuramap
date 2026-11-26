"""IC-4 EvidenceObject v1.2.0 (roadmap WP-GRC-09; plan docs/shared_work/wp_grc09_plan.md §2).

1.2.0 is a types-only minor and re-versions IC-4 alone (README, reference-pin cost table). It
appends five evidence types: the executed counterparts of the contract templates that no 1.1.0
member fits. Their required fields live in IC-15 (`gridq.contracts.contract_templates`), not here.

What is tested here:
1. The version constants, and that the five types are appended after the thirty-two in order.
2. The literal 1.1.0 type set equals the archived 1.1.0 schema, which is pinned by SHA-256.
3. R7, the down-level rule for 1.1.0: an object labelled 1.1.0 holds only 1.1.0 types. R1 still
   refuses a 1.0.0 label for the new types.
4. No new type is self-issuable, and none names an assumption, a placeholder or Neura.
5. The schema delta 1.1.0 to 1.2.0 is exactly the pinned list of paths; no field is added,
   removed or renamed, and every other contract's schema is untouched.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _contracts_support import load, load_v110
from pydantic import ValidationError

from gridq.contracts.__main__ import ARCHIVED, FROZEN, SCHEMA_DIR
from gridq.contracts.evidence import (
    CONTRACT_VERSION,
    GIT_TAG,
    SELF_ISSUABLE_TYPES,
    SUPPORTED_VERSIONS,
    V1_0_0_EVIDENCE_TYPES,
    V1_1_0_EVIDENCE_TYPES,
    EvidenceObject,
    EvidenceType,
    IssuerRole,
)

#: The five types 1.2.0 appends, in order (plan §2).
NEW_TYPES = (
    "assessment_statement_of_work",
    "master_services_agreement",
    "site_access_installation_agreement",
    "lender_reliance_letter",
    "performance_wrapper_agreement",
)
DPA = "ic4_data_processing_agreement.synthetic.json"
BASELINE = "ic4_self_issued_baseline_method.synthetic.json"
VALID_V100 = "ic4_valid_connection_offer.synthetic.json"
V110_SCHEMA = "ic4_evidence_object.v1.1.0.schema.json"
V120_SCHEMA = "ic4_evidence_object.v1.2.0.schema.json"


def _parse(data: dict[str, Any]) -> EvidenceObject:
    return EvidenceObject.model_validate_json(json.dumps(data))


def _schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return data


# ------------------------------------------------------------------ 1. version and members


def test_ic4_writes_1_2_0_and_reads_1_1_0_and_1_0_0() -> None:
    assert CONTRACT_VERSION == "1.2.0"
    assert SUPPORTED_VERSIONS == ("1.0.0", "1.1.0", "1.2.0")
    assert GIT_TAG == "contracts/ic4-evidence-object/v1.2.0"
    assert ("ic4_evidence_object", EvidenceObject, "1.2.0") in FROZEN


def test_five_types_are_appended_after_the_thirty_two() -> None:
    values = [t.value for t in EvidenceType]
    assert len(values) == 37
    assert set(values[:32]) == V1_1_0_EVIDENCE_TYPES
    assert tuple(values[32:]) == NEW_TYPES


def test_no_issuer_role_is_added() -> None:
    # Plan §2 item 6: no new role, so IC-2 and IC-11 (which embed IssuerRole) are not re-versioned.
    assert len(IssuerRole) == 22
    assert (
        _schema(V110_SCHEMA)["$defs"]["IssuerRole"] == _schema(V120_SCHEMA)["$defs"]["IssuerRole"]
    )


def test_the_literal_1_1_0_set_equals_the_archived_schema() -> None:
    assert V110_SCHEMA in ARCHIVED
    schema = _schema(V110_SCHEMA)
    assert schema["properties"]["contract_version"]["enum"] == ["1.0.0", "1.1.0"]
    assert frozenset(schema["$defs"]["EvidenceType"]["enum"]) == V1_1_0_EVIDENCE_TYPES
    assert len(V1_1_0_EVIDENCE_TYPES) == 32
    assert V1_0_0_EVIDENCE_TYPES < V1_1_0_EVIDENCE_TYPES


# ------------------------------------------------------------------ 2. R7 and R1


@pytest.mark.parametrize("value", NEW_TYPES)
def test_r7_a_1_1_0_object_with_a_1_2_0_type_is_refused(value: str) -> None:
    data = load_v110(DPA)
    data["evidence_type"] = value
    with pytest.raises(ValidationError, match=f"evidence_type {value} is not in IC-4 1.1.0"):
        _parse(data)
    data["contract_version"] = "1.2.0"
    obj = _parse(data)
    assert obj.evidence_type.value == value and not obj.is_self_issued


@pytest.mark.parametrize(
    "value", sorted(V1_1_0_EVIDENCE_TYPES - {t.value for t in SELF_ISSUABLE_TYPES})
)
def test_r7_admits_every_1_1_0_type_at_1_1_0(value: str) -> None:
    data = load_v110(DPA)
    data["evidence_type"] = value
    assert _parse(data).contract_version == "1.1.0"


@pytest.mark.parametrize("value", NEW_TYPES)
def test_r1_a_1_0_0_object_with_a_1_2_0_type_is_refused(value: str) -> None:
    data = load(VALID_V100)
    data["evidence_type"] = value
    with pytest.raises(ValidationError, match=f"evidence_type {value} is not in IC-4 1.0.0"):
        _parse(data)


@pytest.mark.parametrize("value", NEW_TYPES)
def test_neura_may_not_self_issue_a_1_2_0_type_at_any_label(value: str) -> None:
    data = load_v110(BASELINE)
    data["evidence_type"] = value
    for version in ("1.1.0", "1.2.0"):
        data["contract_version"] = version
        with pytest.raises(ValidationError, match="Neura may self-issue only"):
            _parse(data)


def test_no_new_type_is_self_issuable() -> None:
    assert not {EvidenceType(v) for v in NEW_TYPES} & SELF_ISSUABLE_TYPES


def test_no_new_type_names_an_assumption_a_placeholder_or_neura() -> None:
    words = ("assum", "derived", "placeholder", "pending", "study", "self", "neura", "estimate")
    assert not [(v, w) for v in NEW_TYPES for w in words if w in v]


# ------------------------------------------------------------------ 3. schema delta 1.1.0 -> 1.2.0

#: Pinned: every leaf path that differs between the archived 1.1.0 and the live 1.2.0 schema.
CHANGED_V120 = {
    "$defs.EvidenceType.description",
    "$defs.EvidenceType.enum",
    "description",
    "properties.contract_version",
}


def _changed(old: Any, new: Any, path: tuple[str, ...] = ()) -> set[str]:
    if (
        isinstance(old, dict)
        and isinstance(new, dict)
        and (not path or path[-1] != "contract_version")
    ):
        out: set[str] = set()
        for key in set(old) | set(new):
            if key not in old or key not in new:
                out.add(".".join((*path, key)))
            else:
                out |= _changed(old[key], new[key], (*path, key))
        return out
    return set() if old == new else {".".join(path)}


def test_the_1_1_0_to_1_2_0_delta_is_exactly_the_pinned_paths() -> None:
    assert _changed(_schema(V110_SCHEMA), _schema(V120_SCHEMA)) == CHANGED_V120


def test_the_type_enum_is_the_old_enum_plus_the_five() -> None:
    old = _schema(V110_SCHEMA)["$defs"]["EvidenceType"]["enum"]
    new = _schema(V120_SCHEMA)["$defs"]["EvidenceType"]["enum"]
    assert new == [*old, *NEW_TYPES]


def test_the_own_version_becomes_the_three_supported_versions() -> None:
    old = _schema(V110_SCHEMA)["properties"]["contract_version"]
    new = _schema(V120_SCHEMA)["properties"]["contract_version"]
    assert (old["enum"], old["default"]) == (["1.0.0", "1.1.0"], "1.1.0")
    assert (new["enum"], new["default"]) == (["1.0.0", "1.1.0", "1.2.0"], "1.2.0")
    assert {k: v for k, v in old.items() if k not in ("enum", "default")} == {
        k: v for k, v in new.items() if k not in ("enum", "default")
    }


def test_no_field_is_added_removed_or_renamed() -> None:
    old, new = _schema(V110_SCHEMA), _schema(V120_SCHEMA)
    assert set(old["$defs"]) == set(new["$defs"])
    for name, node in old["$defs"].items():
        assert set(node.get("properties", {})) == set(new["$defs"][name].get("properties", {}))
        assert node.get("required") == new["$defs"][name].get("required")
    assert set(old["properties"]) == set(new["properties"])
    assert old["required"] == new["required"]


def test_the_description_changes_only_by_the_version_tokens() -> None:
    old, new = _schema(V110_SCHEMA)["description"], _schema(V120_SCHEMA)["description"]
    assert old != new
    assert "1.2.0" in new and "1.2.0" not in old
