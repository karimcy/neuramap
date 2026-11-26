"""Mutation checks for every guard WP-BO-06 adds (plan docs/shared_work/wp_bo06_plan.md §7 step 7,
amended by §11.6: 24 in all).

Each guard is reverted in memory, in a fresh interpreter, and the test named beside it must go red
(pytest exit 1). The runner is the IC-11 one, moved to ``_contracts_support.run_mutant``: the
mutated module is served to every importer, so a mutant in ``_base`` or ``evidence`` reaches IC-1,
IC-2 and IC-11 too. Each mutant has its own control: the same test, the same finder, the source
unmutated, must pass. The fifteen IC-11 mutants stay in ``test_ic11_thermal_flex.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from _contracts_support import run_mutant

TESTS = Path(__file__).resolve().parent
EV, BASE, IC1, IC2, IC11, CLI = (
    "gridq.contracts.evidence",
    "gridq.contracts._base",
    "gridq.contracts.connection",
    "gridq.contracts.curtailment",
    "gridq.contracts.thermal_flex",
    "gridq.contracts.__main__",
)
IC4_TESTS = "test_ic4_v110.py"

#: (plan number and guard, module, exact source text, its in-memory replacement, test file, test).
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "1 R1 down-level type",
        EV,
        "            if self.evidence_type.value not in V1_0_0_EVIDENCE_TYPES:\n",
        "            if False:\n",
        IC4_TESTS,
        "test_r1_a_1_0_0_object_with_a_1_1_0_type_is_refused",
    ),
    (
        "2 R2 down-level role",
        EV,
        "            if self.issuer.role.value not in V1_0_0_ISSUER_ROLES:\n",
        "            if False:\n",
        IC4_TESTS,
        "test_r2_a_1_0_0_object_with_a_new_third_party_role_is_refused",
    ),
    (
        "3 R3 restricted issuer",
        EV,
        "            if self.evidence_type not in SELF_ISSUABLE_TYPES:\n",
        "            if False:\n",
        IC4_TESTS,
        "test_r3_a_self_issued_connection_offer_is_refused_and_a_baseline_method_is_accepted",
    ),
    (
        "4 R4 self-verification",
        EV,
        "                and self.verification.verifier_role is VerifierRole.NEURA_INTERNAL\n",
        "                and False\n",
        IC4_TESTS,
        "test_r4_neura_cannot_verify_its_own_record",
    ),
    (
        "5 require_third_party raise",
        EV,
        "    if checked.is_self_issued:\n",
        "    if False:\n",
        IC4_TESTS,
        "test_require_third_party_raises_on_a_self_issued_object",
    ),
    (
        "6 require_third_party re-validation",
        EV,
        "    checked = EvidenceObject.model_validate_json(canonical_json(obj))\n",
        "    checked = obj\n",
        IC4_TESTS,
        "test_require_third_party_catches_a_model_construct_role_swap",
    ),
    (
        "7 assess skip of self-issued",
        EV,
        "        if obj.is_self_issued and not admit_self_issued:\n",
        "        if False:\n",
        IC4_TESTS,
        "test_assess_skips_a_self_issued_match_by_default",
    ),
    (
        "8 resolve_ref self-issued refusal",
        EV,
        "    if not admit_self_issued:\n        require_third_party(found)\n",
        "    if False:\n        require_third_party(found)\n",
        IC4_TESTS,
        "test_resolve_ref_4_a_self_issued_record_is_refused_unless_admitted_by_type",
    ),
    (
        "9 resolve_ref synthetic refusal",
        EV,
        '    if found.record_class == "synthetic_fixture" and not include_synthetic:\n',
        "    if False:\n",
        IC4_TESTS,
        "test_resolve_ref_3_a_synthetic_fixture_is_refused_by_default",
    ),
    (
        "10 resolve_ref version match",
        EV,
        "    if found.contract_version != ref.contract_version:\n",
        "    if False:\n",
        IC4_TESTS,
        "test_resolve_ref_2_a_reference_whose_version_is_not_the_objects_is_refused",
    ),
    (
        "11 evidence_ref uses the object's version",
        EV,
        "        contract_version=obj.contract_version,\n",
        "        contract_version=CONTRACT_VERSION,\n",
        IC4_TESTS,
        "test_evidence_ref_of_a_1_0_0_object_says_1_0_0",
    ),
    (
        "12 SELF_ISSUABLE_TYPES widened by one type",
        EV,
        "    {EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD, "
        "EvidenceType.DECISION_RECORD}\n",
        "    {EvidenceType.BASELINE_METHOD, EvidenceType.TRIAL_RECORD, "
        "EvidenceType.DECISION_RECORD,\n     EvidenceType.TECHNICAL_REPORT}\n",
        IC4_TESTS,
        "test_self_issuable_types_are_exactly_the_three_the_owner_allowed",
    ),
    (
        "13 Version1x pattern loosened to any major",
        BASE,
        'VERSION_1X_PATTERN = r"^1\\.',
        'VERSION_1X_PATTERN = r"^[0-9]+\\.',
        IC4_TESTS,
        "test_an_evidence_ref_refuses_anything_outside_1x",
    ),
    (
        "14 IC-1 down-level",
        IC1,
        '            if version != "1.0.0":\n',
        "            if False:\n",
        "test_ic1_v110.py",
        "test_down_level_a_1_0_0_agreement_with_a_1_1_0_reference_is_refused",
    ),
    (
        "15 IC-2 down-level, references",
        IC2,
        '            if version != "1.0.0":\n',
        "            if False:\n",
        "test_ic2_v110.py",
        "test_down_level_a_1_0_0_set_with_a_1_1_0_agreement_reference_is_refused",
    ),
    (
        "16 IC-2 down-level, role",
        IC2,
        "                if src.publisher_role.value not in V1_0_0_ISSUER_ROLES:\n",
        "                if False:\n",
        "test_ic2_v110.py",
        "test_down_level_a_1_0_0_set_with_a_new_publisher_role_is_refused",
    ),
    (
        "17 IC-11 surface down-level",
        IC11,
        '                if ref.contract_version != "1.0.0":\n',
        "                if False:\n",
        "test_ic11_v110.py",
        "test_down_level_a_1_0_0_surface_with_a_1_1_0_reference_is_refused",
    ),
    (
        "18 IC-11 instruction down-level",
        IC11,
        "an instruction labelled 1.0.0 holds only what 1.0.0 allowed.\n"
        '        if self.contract_version == "1.0.0":\n',
        "an instruction labelled 1.0.0 holds only what 1.0.0 allowed.\n        if False:\n",
        "test_ic11_v110.py",
        "test_down_level_a_1_0_0_instruction_with_a_new_role_is_refused",
    ),
    (
        "19 archive hash check",
        CLI,
        "        elif _sha256(path.read_bytes()) != pinned:\n",
        "        elif False:\n",
        "test_schema_archive.py",
        "test_a_tampered_archived_schema_fails_check",
    ),
    (
        "20 --write archive refusal",
        CLI,
        "    if refused:\n",
        "    if False:\n",
        "test_schema_archive.py",
        "test_write_refuses_to_change_an_archived_file_and_writes_nothing",
    ),
    (
        "21 R5 named decision",
        EV,
        "                if isinstance(self.issuer.reference, Unknown):\n",
        "                if False:\n",
        IC4_TESTS,
        "test_r5_a_self_issued_decision_record_names_its_decision",
    ),
    (
        "22 R6 dated decision",
        EV,
        "                if isinstance(self.effective_from, Unknown):\n",
        "                if False:\n",
        IC4_TESTS,
        "test_r6_a_self_issued_decision_record_states_when_it_takes_effect",
    ),
    (
        "23 resolve_ref type check",
        EV,
        "    if expected_type is not None and found.evidence_type is not expected_type:\n",
        "    if False:\n",
        IC4_TESTS,
        "test_a_decision_record_resolves_only_by_its_own_type_and_only_when_admitted",
    ),
    (
        "24 resolve_ref admit-needs-type",
        EV,
        "    if admit_self_issued and expected_type is None:\n",
        "    if False:\n",
        IC4_TESTS,
        "test_resolve_ref_0_admitting_a_self_issued_record_needs_the_expected_type",
    ),
]
IDS = [m[0] for m in MUTATIONS]


def _source(module: str) -> str:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None, module
    return Path(spec.origin).read_text(encoding="utf-8")


def test_the_plan_s_twenty_four_guards_are_each_listed_once() -> None:
    assert [int(guard.split()[0]) for guard in IDS] == list(range(1, 25))
    assert len({(m[4], m[5]) for m in MUTATIONS}) == 24, "one target test per guard"
    for _, _, _, _, file, test in MUTATIONS:
        assert f"def {test}(" in (TESTS / file).read_text(encoding="utf-8"), (file, test)


@pytest.mark.parametrize("guard,module,old,new,file,test", MUTATIONS, ids=IDS)
def test_the_control_passes_on_the_unmutated_source(
    guard: str, module: str, old: str, new: str, file: str, test: str
) -> None:
    """Control: the harness is sound for this module and test, so red below means the guard bit."""
    assert run_mutant(module, "", "", f"{TESTS / file}::{test}") == 0, guard


@pytest.mark.parametrize("guard,module,old,new,file,test", MUTATIONS, ids=IDS)
def test_reverting_a_guard_turns_its_test_red(
    guard: str, module: str, old: str, new: str, file: str, test: str
) -> None:
    assert _source(module).count(old) == 1, guard
    # Exit 1 is pytest's "tests ran and failed": the guard is load-bearing. A mutant that cannot
    # load, or whose target is not found exactly once, exits 3; a collection error exits 2.
    assert run_mutant(module, old, new, f"{TESTS / file}::{test}") == 1, guard


def test_the_runner_exits_3_when_a_mutant_cannot_load_or_its_target_is_missing() -> None:
    node = f"{TESTS / IC4_TESTS}::test_contract_version_is_current_and_reads_1_1_0_and_1_0_0"
    assert run_mutant(EV, 'CONTRACT_VERSION: Final = "1.2.0"\n', "raise SystemExit(0)\n", node) == 3
    assert run_mutant(EV, "no such text in the module\n", "", node) == 3
