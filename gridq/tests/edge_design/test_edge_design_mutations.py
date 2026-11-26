"""Mutation guards: reverting each rule in the source, in memory and in a fresh interpreter, turns
the test that guards it red. A control run on the unmutated source proves the harness passes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from _edge_support import HERE, run_mutant

#: (guard, module, exact source text, its in-memory replacement, test file, the test that goes red)
MUTATIONS = [
    (
        "a conduit cannot be built without protocols",
        "gridq.edge_design.model",
        "    protocols: tuple[Protocol, ...] = Field(min_length=1)\n",
        "    protocols: tuple[Protocol, ...] = ()\n",
        "test_edge_design_model.py",
        "test_conduit_without_protocols_direction_or_controls_is_refused",
    ),
    (
        "a conduit cannot be built without controls",
        "gridq.edge_design.model",
        "    controls: tuple[ControlKind, ...] = Field(min_length=1)\n    #: The IC-5",
        "    controls: tuple[ControlKind, ...] = ()\n    #: The IC-5",
        "test_edge_design_model.py",
        "test_an_empty_protocol_or_control_list_is_refused",
    ),
    (
        "crossing the site boundary demands FR 7",
        "gridq.edge_design.model",
        "        need |= {1, 3, 4, 7}\n",
        "        need |= {1, 3, 4}\n",
        "test_edge_design_model.py",
        "test_conduit_missing_an_fr_is_refused",
    ),
    (
        "conduits join only adjacent Purdue levels",
        "gridq.edge_design.invariants",
        "        if _gap(v.zones[c.zone_a], v.zones[c.zone_b]) > 1:\n",
        "        if _gap(v.zones[c.zone_a], v.zones[c.zone_b]) > 2:\n",
        "test_edge_design_invariants.py",
        "test_purdue_adjacent",
    ),
    (
        "the site boundary is crossed outbound only",
        "gridq.edge_design.invariants",
        "        if a_on != b_on and not v.zones[c.side(c.initiator)].on_site:\n",
        "        if a_on != b_on and False:\n",
        "test_edge_design_invariants.py",
        "test_boundary_outbound_only",
    ),
    (
        "nothing flows into the BMS in the diode mode",
        "gridq.edge_design.invariants",
        "        if chiller_write or into_bms:\n",
        "        if chiller_write:\n",
        "test_edge_design_invariants.py",
        "test_bms_mode_is_what_it_says",
    ),
    (
        "a mapped SR needs a test",
        "gridq.edge_design.review",
        "        if not sr.tests:\n",
        "        if False:\n",
        "test_edge_design_review.py",
        "test_claim_ceiling",
    ),
    (
        "the CSF witness compares with NIST",
        "gridq.edge_design.review",
        "    return bool(tags & set(sr.csf_1_1))\n",
        "    return True\n",
        "test_edge_design_review.py",
        "test_csf_second_witness",
    ),
    (
        "the review can never say certified",
        "gridq.edge_design.review",
        "    certified: Literal[False]\n",
        "    certified: bool\n",
        "test_edge_design_review.py",
        "test_review_cannot_claim_certification",
    ),
    (
        "a chiller band needs an L3 BMS mode",
        "gridq.edge_design.query",
        "    if closed and chiller and not stage_at_least(",
        "    if closed and chiller and False and not stage_at_least(",
        "test_edge_design_ic5.py",
        "test_a_chiller_band_is_outside_write_scope_without_an_l3_mode",
    ),
    (
        "write bindings respect the stage",
        "gridq.edge_design.query",
        "        if not stage_at_least(eff, a.min_stage):\n",
        "        if False:\n",
        "test_edge_design_ic5.py",
        "test_write_bindings_follow_stage_and_mode",
    ),
    (
        "an L3 mode needs the safety case by hash",
        "gridq.edge_design.site",
        "            if mode in _L3_MODES and not isinstance(ref, str):\n",
        "            if False:\n",
        "test_edge_design_site.py",
        "test_l3_mode_needs_safety_case_and_signoff",
    ),
]


def _nodeid(test_file: str, test: str) -> str:
    return f"{HERE / test_file}::{test}"


@pytest.mark.parametrize("module,test_file,test", sorted({(m[1], m[4], m[5]) for m in MUTATIONS}))
def test_mutation_runner_passes_on_the_unmutated_source(
    module: str, test_file: str, test: str
) -> None:
    """Control: the harness itself is sound, so a red mutant below means the guard bit."""
    assert run_mutant(module, "", "", _nodeid(test_file, test)) == 0, test


@pytest.mark.parametrize(
    "guard,module,old,new,test_file,test", MUTATIONS, ids=[m[0] for m in MUTATIONS]
)
def test_reverting_a_guard_turns_its_test_red(
    guard: str, module: str, old: str, new: str, test_file: str, test: str
) -> None:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    assert Path(spec.origin).read_text(encoding="utf-8").count(old) == 1, guard
    assert run_mutant(module, old, new, _nodeid(test_file, test)) == 1, guard
