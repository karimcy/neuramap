"""WP-SITE-05: the ``gridq.adequacy_result`` 1.1.0 record (D-NEW-SITE05-4).

The WP-SITE-04 goldens are untouched: the 1.1.0 record's counterfactual *is* the 1.0.0 golden
run (same simulation hash), and a genset with a zero run-hour cap leaves the golden paths digest
exactly as it was. The 1.1.0 golden is pinned here, cross-platform (integers and SHA-256 only).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from tests import _adequacy_support as A
from tests import _onsite_support as O
from tests.test_adequacy_reproducible import (
    GOLDEN_PATHS_G1_RESERVE_OFF,
    GOLDEN_SIMULATION_HASH,
    _fresh,
)

from gridq.adequacy.result import AdequacyResult
from gridq.adequacy.result_v1_1 import AdequacyResultV1_1

#: Pinned for `golden_run_with_generation()`. Never re-pinned: a change is a change of method.
GOLDEN_V1_1_SIMULATION_HASH = (
    "sha256:3e12626490900309ddf4d2c6595c740a4de490efd0dc4704bc880d0d608eb294"
)
GOLDEN_V1_1_RESULT_HASH = "sha256:837923fb70338fce4f2ee928f68d6151fe0a094a2fc4ad2203978a285fb9af15"


@pytest.fixture(scope="module")
def golden() -> Any:
    return O.golden_run_with_generation()


@pytest.fixture(scope="module")
def base() -> Any:
    return A.golden_run()


def test_golden_v1_1_hashes(golden: Any) -> None:
    assert isinstance(golden, AdequacyResultV1_1)
    assert (golden.schema_version, golden.method_version) == ("1.1.0", "gridq.adequacy/1.1.0")
    assert golden.simulation_hash == GOLDEN_V1_1_SIMULATION_HASH
    assert golden.content_hash() == GOLDEN_V1_1_RESULT_HASH


def test_the_counterfactual_is_the_wp_site_04_golden(golden: Any, base: Any) -> None:
    w = golden.generation.without_generation
    assert w.simulation_hash == GOLDEN_SIMULATION_HASH
    assert base.simulation_hash == GOLDEN_SIMULATION_HASH


def test_a_zero_cap_genset_leaves_the_golden_paths_unchanged() -> None:
    r = O.golden_run_with_generation(O.golden_generation(cap_s=0))
    run = r.outcome.runs[0]
    assert (run.posture, run.thermal) == ("reserve_held", "off")
    assert dict(run.paths_digests)["g1"] == GOLDEN_PATHS_G1_RESERVE_OFF
    assert all(r_.end_min == r_.start_min for p in r.generation.plans for r_ in p.runs)


def test_the_genset_lowers_unserved_energy_and_its_cap_binds(golden: Any, base: Any) -> None:
    for g, b in zip(golden.outcome.runs, base.outcome.runs, strict=True):
        assert g.eue.expected_ws.as_fraction() <= b.eue.expected_ws.as_fraction()
    assert golden.outcome.runs[0].eue.expected_ws.as_fraction() < (
        base.outcome.runs[0].eue.expected_ws.as_fraction()
    )
    cuts = {c for p in golden.generation.plans for r in p.runs for c in r.cut_by}
    assert cuts == {"L1"}
    for p in golden.generation.plans:
        (lim,) = p.limits
        assert lim.peak_s == lim.cap_s == 3600


def test_a_generation_input_moves_the_hashes(golden: Any) -> None:
    other = O.golden_run_with_generation(O.golden_generation(cap_s=7200))
    assert other.simulation_hash != golden.simulation_hash
    assert other.inputs.generation_hash != golden.inputs.generation_hash
    assert other.generation.without_generation.simulation_hash == GOLDEN_SIMULATION_HASH


def test_without_generation_the_record_is_exactly_1_0_0(base: Any) -> None:
    assert type(base) is AdequacyResult and base.schema_version == "1.0.0"


def test_no_floats_and_skip_invariance(golden: Any) -> None:
    from tests.test_adequacy_reproducible import _floats

    assert _floats(golden.model_dump()) == []
    assert O.golden_run_with_generation(skip=False).content_hash() == golden.content_hash()


def test_fresh_interpreter_reproduces_the_v1_1_golden(golden: Any) -> None:
    got = _fresh(
        """
        from tests import _onsite_support as O
        print(O.golden_run_with_generation().content_hash())
        """
    )
    assert got == golden.content_hash()


def test_approved_for_use_and_the_hash_are_derived(golden: Any) -> None:
    data = json.loads(golden.model_dump_json())
    data["approved_for_use"] = not golden.approved_for_use
    with pytest.raises(ValidationError, match="derived"):
        AdequacyResultV1_1.model_validate_json(json.dumps(data))
    data = json.loads(golden.model_dump_json())
    data["generation"]["credit"] = "none"
    with pytest.raises(ValidationError, match="simulation_hash"):
        AdequacyResultV1_1.model_validate_json(json.dumps(data))
    assert AdequacyResultV1_1.model_validate_json(golden.model_dump_json()) == golden


def test_a_nonconforming_test_schedule_withholds_approval(golden: Any) -> None:
    from gridq.adequacy import admit_floor_signoff, floor_statement_hash

    sh = floor_statement_hash(golden.policy)
    assert isinstance(sh, str)
    signed = (A.signoff(sh),)
    assert admit_floor_signoff(golden.policy, signed, as_of=golden.as_of).kind == "approved"
    gen = O.bundle(
        O.model(
            [O.genset("G1", kw=300, start_s=90)],
            limits=[O.limit("L1", ["G1"], cap_s=300, counts="test_only")],
            tests=[O.weekly_test("T1", ["G1"], weekday=1, start_minute=600, duration_s=1800)],
        )
    )
    bad = O.golden_run_with_generation(gen, evidence=signed)
    assert bad.approval.kind == "approved"
    assert bad.generation.status == "nonconforming" and bad.approved_for_use is False
    codes = {f.code for f in bad.generation.findings()}
    assert "TEST_SCHEDULE_EXCEEDS_LIMIT" in codes


def test_unknown_inputs_compute_with_zero_credit_and_asks() -> None:
    gen = O.bundle(O.model([O.genset("G1", kw=None)]))
    r = O.golden_run_with_generation(gen)
    assert r.generation.credit == "none"
    assert any("rated output" in a.request for a in r.data_requests)
    run = r.outcome.runs[0]
    assert dict(run.paths_digests)["g1"] == GOLDEN_PATHS_G1_RESERVE_OFF


def test_another_sites_model_raises() -> None:
    m = O.model([O.genset("G1")], site="SYNTHETIC-OTHER-SITE")
    with pytest.raises(ValueError, match="generation model names site"):
        O.golden_run_with_generation(O.bundle(m))
