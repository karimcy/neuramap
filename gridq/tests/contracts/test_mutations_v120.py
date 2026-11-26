"""Mutation checks for every guard WP-GRC-09 adds: IC-4 1.2.0's R7 and archive pin, and IC-15's
twenty-five (plan docs/shared_work/wp_grc09_plan.md, step 6: 27 in all).

Each guard is reverted in memory, in a fresh interpreter, and the test named beside it must go red
(pytest exit 1). The runner is ``_contracts_support.run_mutant``, as in ``test_mutations_v110.py``:
the mutated module is served to every importer. Each mutant has its own control: the same test, the
same finder, the source unmutated, must pass.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from _contracts_support import run_mutant

TESTS = Path(__file__).resolve().parent
EV, CT, CLI = (
    "gridq.contracts.evidence",
    "gridq.contracts.contract_templates",
    "gridq.contracts.__main__",
)
IC4 = "test_ic4_v120.py"
IC15 = "test_ic15_contract_templates.py"
NO = "if False:\n"

#: (number and guard, module, exact source text, its in-memory replacement, test file, test).
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "1 R7 down-level type",
        EV,
        "            if self.evidence_type.value not in V1_1_0_EVIDENCE_TYPES:\n",
        "            " + NO,
        IC4,
        "test_r7_a_1_1_0_object_with_a_1_2_0_type_is_refused",
    ),
    (
        "2 IC-4 v1.1.0 schema archived",
        CLI,
        '    "ic4_evidence_object.v1.1.0.schema.json": (\n',
        '    "ic4_evidence_object.v1.1.0.unpinned": (\n',
        "test_schema_archive.py",
        "test_the_archive_pins_every_tagged_file",
    ),
    (
        "3 MSA safety hierarchy order",
        CT,
        "        if self.order != SAFETY_HIERARCHY:\n",
        "        " + NO,
        IC15,
        "test_a_reordered_safety_hierarchy_is_refused",
    ),
    (
        "4 MSA named clauses",
        CT,
        "        absent = [c for c in MSA_NAMED_CLAUSES if c not in "
        "{c.clause_id for c in msa.clauses}]\n",
        "        absent: list[str] = []\n",
        IC15,
        "test_an_msa_without_a_named_clause_is_refused",
    ),
    (
        "5 write stages wait on the three gates",
        CT,
        "            if s.write_path and not set(CLOSED_LOOP_GATES) <= set(s.gates):\n",
        "            " + NO,
        IC15,
        "test_a_write_stage_without_its_three_gates_is_refused",
    ),
    (
        "6 counsel review binds the reviewed text",
        CT,
        "            if self.legal_review.reviewed_text_sha256 != self.text_sha256:\n",
        "            " + NO,
        IC15,
        "test_counsel_review_binds_the_reviewed_text",
    ),
    (
        "7 an edited text is a draft again",
        CT,
        "    if text_sha256(text) != entry.text_sha256:\n        return DraftNeedsLegalReview(",
        "    if False:\n        return DraftNeedsLegalReview(",
        IC15,
        "test_an_edited_text_is_a_draft_again",
    ),
    (
        "8 each draft has a person-only legal review step",
        CT,
        "            if isinstance(t.legal_review, DraftNeedsLegalReview) and t.template_id not in "
        "reviewed:\n",
        "            " + NO,
        IC15,
        "test_a_draft_without_a_person_only_legal_review_step_is_refused",
    ),
    (
        "9 executed_as refuses a self-issuable type",
        CT,
        "        if EvidenceType(self.evidence_type) in SELF_ISSUABLE_TYPES:\n",
        "        " + NO,
        IC15,
        "test_a_template_cannot_be_executed_as_a_self_issuable_type",
    ),
    (
        "10 the wrapper admits only an available risk carrier",
        CT,
        "        if issuers != available:\n",
        "        " + NO,
        IC15,
        "test_the_wrapper_admits_only_an_available_risk_carrier",
    ),
    (
        "11 a list price is the one on record",
        CT,
        "        if (self.amount, self.unit) != (amount, unit):\n",
        "        " + NO,
        IC15,
        "test_a_list_price_that_is_not_on_record_is_refused",
    ),
    (
        "12 a list price belongs to its template",
        CT,
        "                if owner is not self.template_id:\n",
        "                " + NO,
        IC15,
        "test_a_list_price_cited_by_another_template_is_refused",
    ),
    (
        "13 a pending reference to a live contract is reported",
        CT,
        "            if version is not None:\n",
        "            " + NO,
        IC15,
        "test_a_not_yet_frozen_reference_to_a_contract_that_now_exists_is_reported",
    ),
    (
        "14 an IC-3 or IC-8 artefact named without its reference",
        CT,
        "            if term in lowered and not any(r.contract == contract for r in cited):\n",
        "            " + NO,
        IC15,
        "test_the_prose_guard_refuses_an_artefact_named_without_its_reference",
    ),
    (
        "15 a contract named in prose",
        CT,
        "        if CONTRACT_ID_IN_PROSE.search(line):\n",
        "        " + NO,
        IC15,
        "test_a_contract_id_written_in_prose_is_refused",
    ),
    (
        "16 a pending reference says unavailable until",
        CT,
        '                if f"unavailable until {wp}" not in line:\n',
        "                " + NO,
        IC15,
        "test_a_pending_reference_must_say_unavailable_until",
    ),
    (
        "17 clause anchors match the manifest",
        CT,
        "    if anchors != [c.clause_id for c in entry.clauses]:\n",
        "    " + NO,
        IC15,
        "test_clause_anchors_must_match_the_manifest",
    ),
    (
        "18 field tokens match the required fields",
        CT,
        "    if fields != expected:\n",
        "    " + NO,
        IC15,
        "test_field_tokens_must_be_in_bijection_with_required_fields",
    ),
    (
        "19 the DRAFT header",
        CT,
        '        if not lines or lines[0] != f"> **{DRAFT_HEADER}**":\n',
        "        " + NO,
        IC15,
        "test_a_draft_without_its_header_is_reported",
    ),
    (
        "20 the text is the pinned text",
        CT,
        "    if text_sha256(text) != entry.text_sha256:\n        raise TemplateTextMismatch(",
        "    if False:\n        raise TemplateTextMismatch(",
        IC15,
        "test_an_edited_text_no_longer_loads",
    ),
    (
        "21 executed terms from a draft are refused",
        CT,
        "    if isinstance(entry.legal_review, DraftNeedsLegalReview) and not admit_draft:\n",
        "    " + NO,
        IC15,
        "test_executed_terms_from_a_draft_are_refused_by_default",
    ),
    (
        "22 every required field is stated",
        CT,
        "    if missing:\n        raise TemplateRefused(",
        "    if False:\n        raise TemplateRefused(",
        IC15,
        "test_executed_terms_need_every_required_field",
    ),
    (
        "23 no field the template does not have",
        CT,
        "    if extra:\n",
        "    " + NO,
        IC15,
        "test_executed_terms_refuse_a_field_the_template_does_not_have",
    ),
    (
        "24 a value of the field's kind",
        CT,
        "    if _KIND_OF_VALUE[value.kind] is not field.kind:\n",
        "    " + NO,
        IC15,
        "test_a_value_of_the_wrong_kind_is_refused",
    ),
    (
        "25 no known instance of a contract not yet frozen",
        CT,
        "        if isinstance(ref.status, NotYetFrozen):\n            return (",
        "        if False:\n            return (",
        IC15,
        "test_a_known_instance_of_a_contract_not_yet_frozen_is_refused",
    ),
    (
        "26 the signed copy is of a registered type",
        CT,
        "    if found.evidence_type.value not in admitted:\n",
        "    " + NO,
        IC15,
        "test_a_signed_copy_of_another_type_is_refused",
    ),
    (
        "27 the signed copy is from an admitted issuer",
        CT,
        "    if found.issuer.role.value not in admitted[found.evidence_type.value]:\n",
        "    " + NO,
        IC15,
        "test_a_signed_copy_from_an_issuer_the_template_does_not_admit_is_refused",
    ),
]
IDS = [m[0] for m in MUTATIONS]


def _source(module: str) -> str:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None, module
    return Path(spec.origin).read_text(encoding="utf-8")


def test_the_twenty_seven_guards_are_each_listed_once() -> None:
    assert [int(guard.split()[0]) for guard in IDS] == list(range(1, 28))
    assert len({(m[4], m[5]) for m in MUTATIONS}) == 27, "one target test per guard"
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
