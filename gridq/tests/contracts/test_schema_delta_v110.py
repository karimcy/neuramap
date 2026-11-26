"""Schema delta, 1.0.0 to 1.1.0 (roadmap WP-BO-06; plan docs/shared_work/wp_bo06_plan.md §7 step 5).

The mechanical proof that each 1.1.0 schema names only the planned change. For each contract the
archived 1.0.0 file and the live 1.1.0 file are diffed as JSON, and the set of changed paths must
equal a pinned list. Every changed path is then checked for its value. Allowed kinds:

- ``properties.contract_version``: ``const``/``default`` 1.0.0 becomes ``enum`` [1.0.0, 1.1.0],
  default 1.1.0;
- ``contract_version`` inside ``$defs.EvidenceRef``, ``ConnectionAgreementRef`` and
  ``AvailabilityRef``: ``const`` becomes the ``Version1x`` ``pattern``;
- ``$defs.IssuerRole.enum``: exactly the six third-party roles appended;
- a ``description`` whose only change is the token ``v1.0.0`` to ``v1.1.0``;
- IC-4 only: ``$defs.EvidenceType.enum`` plus seventeen, ``$defs.EvidenceType.description``,
  ``$defs.SelfIssuerRole`` added, ``Issuer.role`` becomes the ``anyOf``, and the
  ``EvidenceObject`` description.

``required`` lists and every property name are identical between the two versions of each schema:
no field is added, removed or renamed. A path outside the list is a STOP (plan §9 condition 5);
the list is not widened.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gridq.contracts.__main__ import ARCHIVED, SCHEMA_DIR
from gridq.contracts._base import VERSION_1X_PATTERN
from gridq.contracts.evidence import V1_0_0_EVIDENCE_TYPES, V1_0_0_ISSUER_ROLES

CONTRACT_VERSION = "properties.contract_version"
VERSION_IN = "$defs.{}.properties.contract_version"
NEW_ROLES = [
    "legal_adviser",
    "certification_body",
    "data_provider",
    "funder",
    "research_partner",
    "independent_verifier",
]
NEW_TYPES = [
    "data_processing_agreement",
    "data_sharing_agreement",
    "non_disclosure_agreement",
    "legal_opinion",
    "conformity_certificate",
    "technical_report",
    "data_licence",
    "method_agreement",
    "service_level_agreement",
    "commissioning_record",
    "control_authority_signoff",
    "fmar_registration",
    "metered_data_record",
    "operational_data_record",
    "baseline_method",
    "trial_record",
    "decision_record",
]

#: Pinned: schema stem -> every leaf path that differs between v1.0.0 and v1.1.0.
CHANGED: dict[str, set[str]] = {
    "ic4_evidence_object": {
        "$defs.EvidenceType.description",
        "$defs.EvidenceType.enum",
        "$defs.Issuer.properties.role",
        "$defs.IssuerRole.enum",
        "$defs.SelfIssuerRole",
        "description",
        CONTRACT_VERSION,
    },
    "ic1_connection_agreement": {
        VERSION_IN.format("EvidenceRef"),
        "description",
        CONTRACT_VERSION,
    },
    "ic2_curtailment_scenario_set": {
        VERSION_IN.format("ConnectionAgreementRef"),
        VERSION_IN.format("EvidenceRef"),
        "$defs.IssuerRole.enum",
        "description",
        CONTRACT_VERSION,
    },
    "ic11_thermal_flex_availability": {
        VERSION_IN.format("EvidenceRef"),
        "description",
        CONTRACT_VERSION,
    },
    "ic11_flex_instruction": {
        VERSION_IN.format("AvailabilityRef"),
        "$defs.IssuerRole.enum",
        "description",
        CONTRACT_VERSION,
    },
    "ic11_flex_acknowledgement": {
        "description",
        CONTRACT_VERSION,
    },
}
#: Leaf keys whose whole value is compared as one unit (so a changed object is one path).
_UNITS = ("contract_version", "role", "SelfIssuerRole")


def _load(stem: str, version: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (SCHEMA_DIR / f"{stem}.v{version}.schema.json").read_text(encoding="utf-8")
    )
    return data


def _changed(old: Any, new: Any, path: tuple[str, ...] = ()) -> set[str]:
    if isinstance(old, dict) and isinstance(new, dict) and (not path or path[-1] not in _UNITS):
        out: set[str] = set()
        for key in set(old) | set(new):
            if key not in old or key not in new:
                out.add(".".join((*path, key)))
            else:
                out |= _changed(old[key], new[key], (*path, key))
        return out
    return set() if old == new else {".".join(path)}


def _at(schema: dict[str, Any], path: str) -> Any:
    node: Any = schema
    for key in path.split("."):
        node = node[key]
    return node


def _walk(node: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    """Every dict in the schema by path."""
    out: dict[tuple[str, ...], Any] = {}
    if isinstance(node, dict):
        out[path] = node
        for key, value in node.items():
            out |= _walk(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out |= _walk(value, (*path, str(i)))
    return out


def test_every_re_versioned_contract_is_pinned_and_archived() -> None:
    assert set(CHANGED) == {
        name.removesuffix(".v1.0.0.schema.json")
        for name in ARCHIVED
        # IC-4 1.2.0 archives the IC-4 v1.1.0 file, IC-12 2.1.0 the IC-12 v2.0.0 files and
        # IC-12 3.0.0 the IC-12 v2.1.0 files
        if name.endswith(".v1.0.0.schema.json")
        and (SCHEMA_DIR / name.replace("v1.0.0", "v1.1.0")).exists()
    }
    assert len(CHANGED) == 6
    for stem in CHANGED:
        assert f"{stem}.v1.0.0.schema.json" in ARCHIVED


@pytest.mark.parametrize("stem", sorted(CHANGED))
def test_the_changed_paths_are_exactly_the_pinned_list(stem: str) -> None:
    assert _changed(_load(stem, "1.0.0"), _load(stem, "1.1.0")) == CHANGED[stem]


@pytest.mark.parametrize("stem", sorted(CHANGED))
def test_no_field_is_added_removed_or_renamed(stem: str) -> None:
    old, new = _walk(_load(stem, "1.0.0")), _walk(_load(stem, "1.1.0"))
    for path, node in old.items():
        if "properties" in node and isinstance(node["properties"], dict):
            assert set(new[path]["properties"]) == set(node["properties"]), path
        if "required" in node:
            assert new[path]["required"] == node["required"], path
    for path, node in new.items():
        if path in old:
            assert ("required" in node) == ("required" in old[path]), path
            assert ("properties" in node) == ("properties" in old[path]), path
    added = set(_load(stem, "1.1.0")["$defs"]) - set(_load(stem, "1.0.0")["$defs"])
    assert added == ({"SelfIssuerRole"} if stem == "ic4_evidence_object" else set())
    assert set(_load(stem, "1.0.0")["$defs"]) <= set(_load(stem, "1.1.0")["$defs"])


@pytest.mark.parametrize("stem", sorted(CHANGED))
def test_the_own_version_becomes_the_two_supported_versions(stem: str) -> None:
    old, new = (
        _at(_load(stem, "1.0.0"), CONTRACT_VERSION),
        _at(_load(stem, "1.1.0"), CONTRACT_VERSION),
    )
    assert (old["const"], old["default"]) == ("1.0.0", "1.0.0")
    assert (new["enum"], new["default"]) == (["1.0.0", "1.1.0"], "1.1.0")
    assert "const" not in new
    rest = {k: v for k, v in old.items() if k not in ("const", "default")}
    assert {k: v for k, v in new.items() if k not in ("enum", "default")} == rest


@pytest.mark.parametrize(
    "stem,path",
    [
        (stem, path)
        for stem, paths in sorted(CHANGED.items())
        for path in sorted(paths)
        if path.startswith("$defs.") and path.endswith(".properties.contract_version")
    ],
)
def test_a_reference_pin_becomes_the_version1x_pattern(stem: str, path: str) -> None:
    old, new = _at(_load(stem, "1.0.0"), path), _at(_load(stem, "1.1.0"), path)
    assert old["const"] == "1.0.0"
    assert new["pattern"] == VERSION_1X_PATTERN
    assert "const" not in new
    assert {k: v for k, v in new.items() if k != "pattern"} == {
        k: v for k, v in old.items() if k != "const"
    }


@pytest.mark.parametrize(
    "stem", sorted(s for s, paths in CHANGED.items() if "$defs.IssuerRole.enum" in paths)
)
def test_the_issuer_role_enum_gains_exactly_the_six_appended(stem: str) -> None:
    old = _at(_load(stem, "1.0.0"), "$defs.IssuerRole.enum")
    new = _at(_load(stem, "1.1.0"), "$defs.IssuerRole.enum")
    assert set(old) == V1_0_0_ISSUER_ROLES
    assert new == [*old, *NEW_ROLES]


def test_issuer_role_description_is_unchanged_wherever_it_is_embedded() -> None:
    for stem in CHANGED:
        old, new = _load(stem, "1.0.0")["$defs"], _load(stem, "1.1.0")["$defs"]
        if "IssuerRole" in old:
            assert new["IssuerRole"]["description"] == old["IssuerRole"]["description"], stem


@pytest.mark.parametrize("stem", sorted(s for s in CHANGED if s != "ic4_evidence_object"))
def test_a_description_changes_only_its_version_token(stem: str) -> None:
    old, new = _load(stem, "1.0.0")["description"], _load(stem, "1.1.0")["description"]
    assert old.count("v1.0.0") == 1
    assert new == old.replace("v1.0.0", "v1.1.0")


def test_ic4_evidence_type_enum_gains_exactly_the_seventeen_appended() -> None:
    old = _at(_load("ic4_evidence_object", "1.0.0"), "$defs.EvidenceType.enum")
    new = _at(_load("ic4_evidence_object", "1.1.0"), "$defs.EvidenceType.enum")
    assert set(old) == V1_0_0_EVIDENCE_TYPES
    assert new == [*old, *NEW_TYPES]


def test_ic4_issuer_role_becomes_either_enum_and_self_issuer_role_is_one_value() -> None:
    old, new = _load("ic4_evidence_object", "1.0.0"), _load("ic4_evidence_object", "1.1.0")
    assert _at(old, "$defs.Issuer.properties.role") == {"$ref": "#/$defs/IssuerRole"}
    assert _at(new, "$defs.Issuer.properties.role") == {
        "anyOf": [{"$ref": "#/$defs/IssuerRole"}, {"$ref": "#/$defs/SelfIssuerRole"}],
        "title": "Role",
    }
    self_role = new["$defs"]["SelfIssuerRole"]
    assert (self_role["enum"], self_role["type"]) == (["neura_self_issued"], "string")


def test_ic4_descriptions_state_the_restricted_issuer_and_keep_the_no_assumption_rule() -> None:
    new = _load("ic4_evidence_object", "1.1.0")
    assert new["description"].startswith("IC-4 v1.1.0.")
    assert "neura_self_issued" in new["description"]
    assert "Neura cannot verify its own record" in new["description"]
    types = new["$defs"]["EvidenceType"]["description"]
    assert "1.1.0 appends seventeen" in types
    assert "There is no assumption-like member" in types


def test_the_diff_is_not_vacuous() -> None:
    old = _load("ic1_connection_agreement", "1.1.0")
    new = json.loads(json.dumps(old))
    new["properties"]["extra_field"] = {"type": "string"}
    new["$defs"]["EvidenceRef"]["properties"]["contract_version"]["pattern"] = "^1\\.0\\.0$"
    assert _changed(old, new) == {
        "properties.extra_field",
        "$defs.EvidenceRef.properties.contract_version",
    }
