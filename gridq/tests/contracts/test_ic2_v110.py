"""IC-2 CurtailmentScenarioSet v1.1.0 (roadmap WP-BO-06; plan docs/shared_work/wp_bo06_plan.md §6).

What changes: IC-4 references and the IC-1 reference accept the 1.x range; ``publisher_role``
gains the six IC-4 1.1.0 third-party roles; `scenario_set_ref` states the set's own version. A set
labelled 1.0.0 may hold only what 1.0.0 allowed (the down-level rule). The 1.0.0 conformance tests
(``test_ic2_curtailment_scenario_set.py``) still run against the 1.1.0 code.
"""

from __future__ import annotations

import hashlib
from typing import Any

import _ic2_fixtures
import _ic2_real
import pytest
from _contracts_support import GOLDEN_HASHES_V110, dumps, load, load_v110
from pydantic import ValidationError

from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.curtailment import (
    CONTRACT_VERSION,
    GIT_TAG,
    SUPPORTED_VERSIONS,
    ConnectionAgreementRef,
    CurtailmentScenarioSet,
    ReplayedRealData,
    ScenarioSetRef,
    scenario_set_ref,
)
from gridq.contracts.evidence import V1_0_0_ISSUER_ROLES, IssuerRole

REPLAY = "ic2_replay_three_years.synthetic.json"
MODELLED = "ic2_modelled_hh_only.synthetic.json"
MODELLED_V110 = "ic2_modelled_v110.synthetic.json"
EVIDENCE_BACKED = "ic1_hybrid_evidence_backed.synthetic.json"
NEW_ROLES = sorted(r.value for r in IssuerRole if r.value not in V1_0_0_ISSUER_ROLES)


def _set(data: dict[str, Any]) -> CurtailmentScenarioSet:
    return CurtailmentScenarioSet.model_validate_json(dumps(data))


def _ic4_ref(version: str) -> dict[str, Any]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": version,
        "evidence_hash": "sha256:" + "b" * 64,
    }


def _replayed(role: str = "dno", registered: dict[str, Any] | None = None) -> dict[str, Any]:
    """The replay fixture's scenarios as a registered replay of one published source."""
    data = load(REPLAY)
    data["evidence_class"] = "replayed_real_data"
    data["site_id"] = "SITE-X"
    data["provenance"] = {
        "kind": "replayed_real_data",
        "sources": [
            {
                "publisher": "X",
                "publisher_role": role,
                "dataset": "d",
                "url": "https://example.invalid/d",
                "retrieved_at": "2026-11-17T00:00:00Z",
                "response_sha256": "sha256:" + hashlib.sha256(b"response").hexdigest(),
                "licence": "CC BY 4.0",
                "attribution": "X",
                "distribution_gate": {"kind": "not_applicable", "reason": "open"},
                "registered_evidence": registered
                or {"kind": "unknown", "reason": "not registered"},
            }
        ],
        "derivation": "synthetic: test derivation",
    }
    return data


def _modelled_run(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    data = load(MODELLED)
    data["evidence_class"] = "modelled"
    data["site_id"] = "SITE-X"
    data["provenance"] = {
        "kind": "modelled",
        "engine": "curtailment_scenario_engine",
        "engine_version": "0.0.0-fixture",
        "input_hash": "sha256:" + "a" * 64,
        "seed": 7,
        "evidence": evidence,
    }
    return data


# ------------------------------------------------------------------ 1. version and fixture


def test_contract_version_is_1_1_0_and_reads_1_0_0() -> None:
    assert CONTRACT_VERSION == "1.1.0"
    assert SUPPORTED_VERSIONS == ("1.0.0", "1.1.0")
    assert SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == "contracts/ic2-curtailment-scenario-set/v1.1.0"
    data = load(MODELLED)
    del data["contract_version"]
    assert _set(data).contract_version == "1.1.0"


def test_the_v110_set_validates_and_links_to_the_evidence_backed_agreement() -> None:
    obj = _set(load_v110(MODELLED_V110))
    assert (obj.contract, obj.contract_version) == ("IC-2", "1.1.0")
    assert obj.content_hash() == GOLDEN_HASHES_V110[MODELLED_V110]
    agreement = ConnectionAgreement.model_validate_json(dumps(load_v110(EVIDENCE_BACKED)))
    ref = obj.connection_agreement
    assert isinstance(ref, ConnectionAgreementRef)
    assert (ref.contract, ref.contract_version) == ("IC-1", "1.1.0")
    assert ref.agreement_hash == agreement.content_hash() == GOLDEN_HASHES_V110[EVIDENCE_BACKED]
    assert obj.site_id == agreement.site_id


def test_committed_v110_fixture_equals_a_fresh_build() -> None:
    assert _ic2_fixtures.MODELLED_V110.read_text(encoding="utf-8") == _ic2_real.render(
        _ic2_fixtures.build_modelled_v110()
    )
    assert _ic2_fixtures.IC1_HYBRID_EVIDENCE_BACKED_HASH == GOLDEN_HASHES_V110[EVIDENCE_BACKED]


def test_the_v110_set_differs_from_the_1_0_0_one_only_in_label_id_and_agreement() -> None:
    new, old = load_v110(MODELLED_V110), load(MODELLED)
    assert list(new) == list(old)
    assert {k for k in new if new[k] != old[k]} == {
        "contract_version",
        "set_id",
        "connection_agreement",
    }


# ------------------------------------------------------------------ 2. the down-level rule


def test_down_level_a_1_0_0_set_with_a_1_1_0_agreement_reference_is_refused() -> None:
    data = load(MODELLED)
    data["connection_agreement"]["contract_version"] = "1.1.0"
    with pytest.raises(ValidationError, match="not in IC-2 1.0.0"):
        _set(data)
    data["contract_version"] = "1.1.0"
    assert _set(data).contract_version == "1.1.0"


@pytest.mark.parametrize("role", NEW_ROLES)
def test_down_level_a_1_0_0_set_with_a_new_publisher_role_is_refused(role: str) -> None:
    data = _replayed(role)
    with pytest.raises(ValidationError, match="not in IC-2 1.0.0"):
        _set(data)
    data["contract_version"] = "1.1.0"
    obj = _set(data)
    assert isinstance(obj.provenance, ReplayedRealData)
    assert obj.provenance.sources[0].publisher_role.value == role


def test_the_six_new_roles_are_the_ones_ic4_1_1_0_appended() -> None:
    assert NEW_ROLES == sorted(
        [
            "legal_adviser",
            "certification_body",
            "data_provider",
            "funder",
            "research_partner",
            "independent_verifier",
        ]
    )


@pytest.mark.parametrize("role", sorted(V1_0_0_ISSUER_ROLES))
def test_down_level_admits_every_1_0_0_role_at_1_0_0(role: str) -> None:
    assert _set(_replayed(role)).contract_version == "1.0.0"


def test_down_level_a_1_0_0_set_with_a_1_1_0_registered_evidence_is_refused() -> None:
    data = _replayed(registered=_ic4_ref("1.1.0"))
    with pytest.raises(ValidationError, match="not in IC-2 1.0.0"):
        _set(data)
    data["contract_version"] = "1.1.0"
    _set(data)
    assert _set(_replayed(registered=_ic4_ref("1.0.0"))).contract_version == "1.0.0"


def test_down_level_a_1_0_0_set_with_a_1_1_0_modelled_run_reference_is_refused() -> None:
    data = _modelled_run([_ic4_ref("1.1.0")])
    with pytest.raises(ValidationError, match="not in IC-2 1.0.0"):
        _set(data)
    data["contract_version"] = "1.1.0"
    _set(data)
    assert _set(_modelled_run([_ic4_ref("1.0.0")])).contract_version == "1.0.0"


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
def test_the_publisher_role_refuses_neura_self_issued_at_either_version(label: str) -> None:
    data = _replayed("neura_self_issued")
    data["contract_version"] = label
    with pytest.raises(ValidationError):
        _set(data)


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
@pytest.mark.parametrize("version", ["2.0.0", "0.9.0"])
def test_a_reference_outside_1x_is_refused_at_either_version(label: str, version: str) -> None:
    agreement = load(MODELLED)
    agreement["connection_agreement"]["contract_version"] = version
    for data in (
        agreement,
        _replayed(registered=_ic4_ref(version)),
        _modelled_run([_ic4_ref(version)]),
    ):
        data["contract_version"] = label
        with pytest.raises(ValidationError):
            _set(data)


# ------------------------------------------------------------------ 3. scenario_set_ref


def test_scenario_set_ref_carries_the_sets_own_version() -> None:
    for data, version in ((load(MODELLED), "1.0.0"), (load_v110(MODELLED_V110), "1.1.0")):
        obj = _set(data)
        ref = scenario_set_ref(obj)
        assert (ref.contract, ref.contract_version) == ("IC-2", version)
        assert ref.scenario_set_hash == obj.content_hash()


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.10.0"])
def test_a_scenario_set_ref_accepts_the_1x_range(version: str) -> None:
    payload = {
        "kind": "scenario_set_ref",
        "contract": "IC-2",
        "contract_version": version,
        "scenario_set_hash": "sha256:" + "c" * 64,
    }
    assert ScenarioSetRef.model_validate_json(dumps(payload)).contract_version == version
    payload["contract_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        ScenarioSetRef.model_validate_json(dumps(payload))
