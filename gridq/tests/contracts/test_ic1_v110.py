"""IC-1 ConnectionAgreement v1.1.0 (roadmap WP-BO-06; plan docs/shared_work/wp_bo06_plan.md §6).

The one change: ``source.evidence`` accepts an IC-4 reference in the 1.x range, and an agreement
labelled 1.0.0 may hold only a 1.0.0 reference (the down-level rule). The 1.0.0 conformance tests
(``test_ic1_connection_agreement.py``) still run against the 1.1.0 code.
"""

from __future__ import annotations

from typing import Any

import pytest
from _contracts_support import (
    GOLDEN_HASHES,
    GOLDEN_HASHES_V110,
    IC1_FIXTURES,
    dumps,
    load,
    load_v110,
)
from pydantic import ValidationError

from gridq.contracts.connection import (
    CONTRACT_VERSION,
    GIT_TAG,
    SUPPORTED_VERSIONS,
    ConnectionAgreement,
    EvidenceBacked,
)
from gridq.contracts.evidence import (
    EvidenceObject,
    EvidenceType,
    SyntheticEvidenceRefused,
    evidence_ref,
    resolve_ref,
)

HYBRID = "ic1_archetype_hybrid.synthetic.json"
EVIDENCE_BACKED = "ic1_hybrid_evidence_backed.synthetic.json"
OFFER = "ic4_valid_connection_offer.synthetic.json"


def _ca(data: dict[str, Any]) -> ConnectionAgreement:
    return ConnectionAgreement.model_validate_json(dumps(data))


def _offer() -> EvidenceObject:
    return EvidenceObject.model_validate_json(dumps(load(OFFER)))


def _source(version: str) -> dict[str, Any]:
    return {
        "kind": "evidence_backed",
        "evidence": {
            "kind": "evidence_ref",
            "contract": "IC-4",
            "contract_version": version,
            "evidence_hash": GOLDEN_HASHES[OFFER],
        },
    }


def test_contract_version_is_1_1_0_and_reads_1_0_0() -> None:
    assert CONTRACT_VERSION == "1.1.0"
    assert SUPPORTED_VERSIONS == ("1.0.0", "1.1.0")
    assert SUPPORTED_VERSIONS[-1] == CONTRACT_VERSION
    assert GIT_TAG == "contracts/ic1-connection-agreement/v1.1.0"
    data = load(HYBRID)
    del data["contract_version"]
    assert _ca(data).contract_version == "1.1.0"


def test_the_evidence_backed_fixture_validates_and_matches_its_golden_hash() -> None:
    obj = _ca(load_v110(EVIDENCE_BACKED))
    assert (obj.contract, obj.contract_version) == ("IC-1", "1.1.0")
    assert obj.content_hash() == GOLDEN_HASHES_V110[EVIDENCE_BACKED]
    assert isinstance(obj.source, EvidenceBacked)
    assert obj.source.evidence.contract_version == "1.0.0"


def test_the_evidence_backed_fixture_keeps_the_hybrid_terms() -> None:
    new, old = load_v110(EVIDENCE_BACKED), load(HYBRID)
    assert list(new) == list(old)
    changed = {k for k in new if new[k] != old[k]}
    assert changed == {"contract_version", "agreement_ref", "source"}


def test_a_1_1_0_agreement_cites_a_1_0_0_offer_and_resolves_it() -> None:
    # A newer contract citing older evidence: the reference states the offer's own version.
    obj, offer = _ca(load_v110(EVIDENCE_BACKED)), _offer()
    assert isinstance(obj.source, EvidenceBacked)
    assert obj.source.evidence == evidence_ref(offer)
    assert offer.scope.site_id == obj.site_id
    with pytest.raises(SyntheticEvidenceRefused):
        resolve_ref(obj.source.evidence, [offer], expected_type=EvidenceType.CONNECTION_OFFER)
    found = resolve_ref(
        obj.source.evidence,
        [offer],
        expected_type=EvidenceType.CONNECTION_OFFER,
        include_synthetic=True,
    )
    assert found is offer


def test_down_level_a_1_0_0_agreement_with_a_1_1_0_reference_is_refused() -> None:
    data = load(HYBRID)
    data["source"] = _source("1.1.0")
    with pytest.raises(ValidationError, match="not in IC-1 1.0.0"):
        _ca(data)
    data["contract_version"] = "1.1.0"
    obj = _ca(data)
    assert isinstance(obj.source, EvidenceBacked)
    assert obj.source.evidence.contract_version == "1.1.0"


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
def test_down_level_admits_a_1_0_0_reference_at_either_version(label: str) -> None:
    data = load_v110(EVIDENCE_BACKED)
    data["contract_version"] = label
    assert _ca(data).contract_version == label


@pytest.mark.parametrize("label", SUPPORTED_VERSIONS)
@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "1.1", "1.01.0"])
def test_an_ic4_reference_outside_1x_is_refused_at_either_version(label: str, version: str) -> None:
    data = load(HYBRID)
    data["contract_version"] = label
    data["source"] = _source(version)
    with pytest.raises(ValidationError):
        _ca(data)


@pytest.mark.parametrize("path", IC1_FIXTURES, ids=lambda p: p.name)
def test_a_1_0_0_archetype_relabelled_1_1_0_is_valid_and_hashes_differently(path: Any) -> None:
    data = load(path.name)
    old = _ca(data)
    data["contract_version"] = "1.1.0"
    new = _ca(data)
    assert old.content_hash() == GOLDEN_HASHES[path.name]
    assert new.content_hash() != old.content_hash()
