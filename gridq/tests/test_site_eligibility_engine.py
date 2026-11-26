"""Site eligibility engine (WP-UW-02, spec A6-02; D133 to D142).

Acceptance criteria, each tested here:

1. every rule cites a regulatory-register row;
2. the table lists every stream for every site as eligible, ineligible or undetermined, with the
   reason;
3. a domestic or small-business customer fails the Class B check;
4. a site that passes the provisional load-control rule is undetermined with that reason, never
   exempt, and the answer changes when a final rule (a labelled stand-in for WP-GRC-07's loader)
   is swapped in;
5. the segment rule is obtained only through ``load_rule`` with the run's ``as_of``, in a fresh
   process that re-reads the register on every run, and any loader exception is undetermined.

Every Atlas tree here is a SYNTHETIC TEST FIXTURE (`tests/_eligibility_support.py`) around byte
copies of the pinned Atlas loader and gate. Tests against the sibling Atlas checkout assert
invariants only, never a dated fact, and skip when the checkout is absent.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
from tests._eligibility_support import (
    AS_OF,
    DEFAULT_STATES,
    ELIGIBLE_PROVISIONAL_LOADER,
    FINAL_LOADER,
    FINAL_STATES,
    LABEL,
    PIN,
    REGULATORY,
    SIBLING_ATLAS,
    SNAPSHOTS,
    SYN_SITE,
    assets,
    atlas_tree,
    customer_facts,
    declared,
    evidenced,
    facts,
    raising_loader,
    segment_rule,
    synthetic_dossier,
    write_register,
    write_rule,
)
from tests.ops._dossier_support import entry, register

import gridq.underwriting.eligibility as eng
from gridq.governance.dpa_guard import EVIDENCE_DIR_ENV, load_evidence_registry
from gridq.ops.dossier import build_dossier
from gridq.ops.dossier_inputs import default_register
from gridq.underwriting.basis import SYNTHETIC_LABEL
from gridq.underwriting.eligibility import (
    EligibilityInputError,
    build_eligibility,
    main,
)
from gridq.underwriting.eligibility_model import (
    ControlledAssetsDeclaration,
    EligibilityFacts,
    SiteEligibilityTable,
)
from gridq.underwriting.eligibility_rules import (
    ADOPTED_LOADERS,
    CHECKS,
    RULE_TABLE_HASH,
    STREAMS,
    FactField,
    Stream,
    cited_rows,
    rule_table_payload,
)

SYNTHETIC = "synthetic_fixtures_test_only"
NON_SMALL = "non_domestic_not_small_business"
LCL = Stream.LOAD_CONTROL_EXEMPTION_CLASS


@pytest.fixture()
def ev_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setenv(EVIDENCE_DIR_ENV, str(root))
    return root


def _run(
    ev_root: Path,
    atlas: Path | None,
    *fact_items,
    site_id: str = SYN_SITE,
    as_of: date = AS_OF,
    controlled: ControlledAssetsDeclaration | None = None,
    dossier=None,
    admission: str = SYNTHETIC,
) -> SiteEligibilityTable:
    d = dossier if dossier is not None else synthetic_dossier(ev_root, site_id, as_of=as_of)
    recs = [facts(site_id, *fact_items, as_of=as_of)] if fact_items else []
    return build_eligibility(
        d,
        as_of=as_of,
        atlas_root=atlas,
        evidence_admission=admission,  # type: ignore[arg-type]
        facts=recs,
        controlled_assets=controlled if controlled is not None else assets(),
        evidence_registry=load_evidence_registry(ev_root),
    )


def _check(table: SiteEligibilityTable, check_id: str, site_id: str = SYN_SITE):
    site = table.site(site_id)
    return next(c for s in site.streams for c in s.checks if c.check_id == check_id)


def _passing_customer(ev_root: Path) -> list:
    return customer_facts(ev_root, SYN_SITE, NON_SMALL, portfolio=(NON_SMALL,))


# --------------------------------------------------------------------------------------------
# Criterion 1: every rule cites a register row
# --------------------------------------------------------------------------------------------


def test_every_check_cites_at_least_one_register_row_that_exists_in_the_pinned_register() -> None:
    assert CHECKS, "the rule table is empty"
    pinned = set(PIN["register_row_ids"])
    for rule in CHECKS:
        assert rule.rows, f"{rule.check_id} cites no register row"
        for row in rule.rows:
            assert re.fullmatch(r"REG-\d{2}", row), row
            assert row in pinned, f"{rule.check_id} cites {row}, which the register does not hold"
    assert set(cited_rows()) == {r for c in CHECKS for r in c.rows}
    assert "REG-18" not in cited_rows()  # D137


def test_every_cited_row_exists_in_the_live_atlas_register() -> None:
    reg = SIBLING_ATLAS / REGULATORY / "regulatory_register.json"
    if not reg.is_file():
        pytest.skip("no sibling Atlas checkout")
    live = {r["id"] for r in json.loads(reg.read_text(encoding="utf-8"))["rows"]}
    assert set(cited_rows()) <= live


def test_no_condition_carries_a_regulatory_figure() -> None:
    """Thresholds come only from register rows (D135): conditions name, never quote."""
    # Identifiers whose digits are not figures (REG-04, WP-GRC-07, P511, G99, §12.2): the same
    # pattern as the Atlas loader's IDENTIFIER.
    identifier = re.compile(
        r"§\s?\d+[A-Z]?(?:\.\d+)*"
        r"|\b[A-Z]{1,3}\d-\d{1,3}\b"
        r"|\b[A-Z][A-Za-z]*(?:-[A-Z][A-Za-z0-9]*)*-[A-Z]?\d+[a-z]?\b"
        r"|\b[A-Z]{1,3}\d{1,3}\b"
    )
    for rule in CHECKS:
        text = identifier.sub(" ", rule.condition)
        assert not re.search(r"\d", text), f"{rule.check_id}: {rule.condition!r}"


def test_every_check_result_carries_its_rows_and_the_gate_verdict(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"))
    by_id = {c.check_id: c for c in CHECKS}
    for s in t.site(SYN_SITE).streams:
        for c in s.checks:
            assert tuple(r.row for r in c.rows) == by_id[c.check_id].rows
    assert {r.row for r in t.row_verdicts} == set(cited_rows())
    # Each row is gated on its own: one refusal never hides another row's verdict.
    expected = {
        "signed": "passes",
        "not_signed": "RowNotSignedOff",
        "changed": "RowChanged",
        "unverified": "RowUnverified",
    }
    assert {r.row: r.verdict for r in t.row_verdicts} == {
        row: expected[state] for row, state in DEFAULT_STATES.items()
    }


def test_the_rule_table_hash_is_its_payload_and_separate_from_uw01() -> None:
    from gridq.underwriting import rules as uw01

    payload = json.dumps(
        rule_table_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert RULE_TABLE_HASH == "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert RULE_TABLE_HASH != uw01.RULE_TABLE_HASH


# --------------------------------------------------------------------------------------------
# Criterion 2: every stream, with a status and a reason
# --------------------------------------------------------------------------------------------


def test_every_stream_is_listed_for_every_site_with_a_status_and_a_reason(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"), *_passing_customer(ev_root))
    site = t.site(SYN_SITE)
    assert tuple(s.stream for s in site.streams) == STREAMS
    assert {s.stream for s in site.streams} == set(Stream)
    for s in site.streams:
        assert s.status in ("eligible", "ineligible", "undetermined")
        assert s.question.strip()
        for c in s.checks:
            assert c.reason.strip(), c.check_id
        if s.status == "undetermined":
            assert not isinstance(s.classification, str)
            assert s.classification.reason.strip()
        else:
            assert isinstance(s.classification, str)


def test_a_missing_site_fact_is_unknown_with_the_field_named_and_a_data_request(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"))
    gsp = _check(t, "MET-GSP")
    assert gsp.status == "undetermined"
    assert "facts:gsp_groups" in gsp.missing_fields
    assert "facts:gsp_groups" in gsp.reason
    asks = t.site(SYN_SITE).data_requests
    assert any("GSP group" in a.request and a.scope == f"site:{SYN_SITE}" for a in asks)
    assert [a.ask_id for a in asks] == sorted({a.ask_id for a in asks})


def test_the_gsp_group_check_reads_the_evidenced_groups(ev_root: Path, tmp_path: Path) -> None:
    atlas = atlas_tree(tmp_path / "atlas")

    def gsp(groups: list[str]):
        return evidenced(
            ev_root,
            SYN_SITE,
            FactField.GSP_GROUPS,
            {"groups": groups},
            evidence_type="mpan_registration",
            role="elexon",
        )

    one = _check(_run(ev_root, atlas, gsp(["_A"])), "MET-GSP")
    assert one.status == "eligible" and "_A" in one.reason
    two = _check(_run(ev_root, atlas, gsp(["_A", "_B"])), "MET-GSP")
    assert two.status == "ineligible" and "REG-04" in two.reason


def test_evidence_of_the_wrong_type_does_not_place_a_fact(ev_root: Path, tmp_path: Path) -> None:
    wrong = evidenced(
        ev_root,
        SYN_SITE,
        FactField.GSP_GROUPS,
        {"groups": ["_A"]},
        evidence_type="technical_report",
        role="independent_engineer",
    )
    c = _check(_run(ev_root, atlas_tree(tmp_path / "atlas"), wrong), "MET-GSP")
    assert c.status == "undetermined"
    assert "does not evidence gsp_groups" in c.read[1].note


def _msid(ev_root: Path, kw: float, pairs: int = 1):
    return evidenced(
        ev_root,
        SYN_SITE,
        FactField.MSID_EXPORT,
        {"msid_pairs": pairs, "largest_export_kw": kw},
        evidence_type="metered_data_record",
        role="meter_operator",
    )


@pytest.mark.parametrize(
    ("kw", "status", "classification"),
    [
        (2500.0, "eligible", "below_lower"),
        (5000.0, "undetermined", None),
        (9000.0, "ineligible", "above_upper"),
    ],
)
def test_p511_bands_use_the_rows_own_thresholds(
    ev_root: Path, tmp_path: Path, kw: float, status: str, classification: str | None
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"), _msid(ev_root, kw))
    c = _check(t, "P511-BAND")
    assert c.status == status, c.reason
    assert {u.name for u in c.thresholds} == {
        "lower_trading_eligibility_threshold",
        "upper_trading_eligibility_threshold",
    }
    for u in c.thresholds:
        assert LABEL in u.quote  # the fixture's figures are labelled, never regulatory
    if status == "undetermined":
        assert "thirty-day" in c.reason
    stream = t.site(SYN_SITE).stream(Stream.P511_EXPORT_BAND)
    if classification:
        assert stream.classification == classification


def test_a_row_without_machine_readable_thresholds_leaves_the_band_undetermined(
    ev_root: Path, tmp_path: Path
) -> None:
    atlas = atlas_tree(tmp_path / "atlas", thresholds=None)
    c = _check(_run(ev_root, atlas, _msid(ev_root, 2500.0)), "P511-BAND")
    assert c.status == "undetermined"
    assert "REG-01 carries no machine-readable threshold" in c.reason
    assert not c.thresholds


def test_a_threshold_whose_quote_is_not_in_the_rows_evidence_is_not_used(
    ev_root: Path, tmp_path: Path
) -> None:
    atlas = atlas_tree(tmp_path / "atlas", reg01_quotes=(f"{LABEL}: some other sentence.",))
    c = _check(_run(ev_root, atlas, _msid(ev_root, 2500.0)), "P511-BAND")
    assert c.status == "undetermined"
    assert "its quote is not in the row's evidence quotes" in c.reason


def test_net_export_alone_never_places_a_p511_band(ev_root: Path, tmp_path: Path) -> None:
    c = _check(_run(ev_root, atlas_tree(tmp_path / "atlas")), "P511-BAND")
    assert c.status == "undetermined"
    assert "facts:msid_export" in c.missing_fields
    assert any("net export cannot place a P511 band" in r.note for r in c.read)


def test_a_check_whose_row_the_gate_refuses_is_undetermined_with_the_refusal(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"))
    c = _check(t, "DFS-SERVICE")  # REG-13 is changed in the register
    assert c.status == "undetermined"
    assert "REG-13: RowChanged" in c.reason
    assert c.blocked_only_by_gate is True
    assert "sign off or re-verify REG-13" in c.unblocked_by
    refused = next(a for a in t.owner_actions if a.action_id == "REGISTER-ROWS-REFUSED")
    assert "REG-13" in refused.action


def test_the_capacity_market_class_is_undetermined_while_the_split_is_proposed(
    ev_root: Path, tmp_path: Path
) -> None:
    cm = evidenced(
        ev_root,
        SYN_SITE,
        FactField.CM_UNIT_TYPE,
        {"unit_class": "btm_storage"},
        evidence_type="cmu_prequalification",
        role="emr_settlement",
    )
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"), cm)
    assert _check(t, "CM-CLASS").status == "undetermined"
    assert "proposed" in _check(t, "CM-CLASS").reason
    assert t.site(SYN_SITE).stream(Stream.CM_CLASS).status == "undetermined"


def test_a_check_with_no_stated_exclusion_is_never_ineligible(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"), _msid(ev_root, 9000.0))
    rules = {c.check_id: c for c in CHECKS}
    for s in t.site(SYN_SITE).streams:
        for c in s.checks:
            if c.status == "ineligible":
                assert rules[c.check_id].exclusion_stated, c.check_id


# --------------------------------------------------------------------------------------------
# Criterion 3: a domestic or small-business customer fails the Class B check
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ["domestic", "small_business"])
@pytest.mark.parametrize("basis", ["evidence", "declaration"])
def test_a_domestic_or_small_business_customer_fails_the_class_b_check(
    ev_root: Path, tmp_path: Path, cls: str, basis: str
) -> None:
    fact = (
        customer_facts(ev_root, SYN_SITE, cls)[0]
        if basis == "evidence"
        else declared(FactField.CUSTOMER_CONSUMER_CLASS, {"consumer_class": cls})
    )
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"), fact)
    c = _check(t, "LCL-RULE")
    assert c.status == "ineligible", c.reason
    assert "LCL-CONSUMER-GUARD" in c.reason
    stream = t.site(SYN_SITE).stream(LCL)
    assert stream.status == "ineligible"
    assert stream.classification == f"refused:{cls}"


@pytest.mark.parametrize("cls", ["domestic", "small_business"])
def test_a_small_business_customer_elsewhere_in_the_portfolio_fails_the_site(
    ev_root: Path, tmp_path: Path, cls: str
) -> None:
    items = customer_facts(ev_root, SYN_SITE, NON_SMALL, portfolio=(NON_SMALL, cls))
    c = _check(_run(ev_root, atlas_tree(tmp_path / "atlas"), *items), "LCL-RULE")
    assert c.status == "ineligible"
    assert "LCL-PORTFOLIO" in c.reason


def test_the_live_atlas_rule_refuses_a_domestic_customer_whenever_it_loads(
    ev_root: Path,
) -> None:
    """Invariant on the sibling checkout: loaded means ineligible, refused means undetermined."""
    if not (SIBLING_ATLAS / REGULATORY / "load_control" / "rule.py").is_file():
        pytest.skip("no sibling Atlas checkout")
    fact = declared(FactField.CUSTOMER_CONSUMER_CLASS, {"consumer_class": "domestic"})
    t = _run(ev_root, SIBLING_ATLAS, fact)
    c = _check(t, "LCL-RULE")
    if t.loader.outcome == "loaded":
        assert c.status == "ineligible"
    else:
        assert c.status == "undetermined" and "load_rule" in c.reason
    assert str(SIBLING_ATLAS) not in t.model_dump_json()


def test_live_atlas_invariants_hold_whatever_the_register_says(ev_root: Path) -> None:
    """No check passes on a refused row; load control is never eligible while not final."""
    if not (SIBLING_ATLAS / REGULATORY / "load_control" / "rule.py").is_file():
        pytest.skip("no sibling Atlas checkout")
    gsp = evidenced(
        ev_root,
        SYN_SITE,
        FactField.GSP_GROUPS,
        {"groups": ["_A"]},
        evidence_type="mpan_registration",
        role="elexon",
    )
    t = _run(ev_root, SIBLING_ATLAS, gsp, _msid(ev_root, 10.0), *_passing_customer(ev_root))
    rules = {c.check_id: c for c in CHECKS}
    verdicts = {r.row: r.verdict for r in t.row_verdicts}
    for s in t.site(SYN_SITE).streams:
        for c in s.checks:
            if c.status == "eligible" and rules[c.check_id].engine_gates_rows:
                assert all(verdicts[r] == "passes" for r in rules[c.check_id].rows), c.check_id
    if t.loader.rule_status != "final":
        assert t.site(SYN_SITE).stream(LCL).status != "eligible"


# --------------------------------------------------------------------------------------------
# Criterion 4: provisional is never exempt; the final rule changes the answer
# --------------------------------------------------------------------------------------------


def test_a_site_passing_the_provisional_rule_is_undetermined_never_exempt(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, atlas_tree(tmp_path / "atlas"), *_passing_customer(ev_root))
    c = _check(t, "LCL-RULE")
    assert t.loader.rule_status == "provisional"
    assert c.status == "undetermined"
    assert "provisional" in c.reason and "never exempt" in c.reason
    assert "LCL-CONSUMER-PASS-PROVISIONAL" in c.reason
    assert "the final segment rule (WP-GRC-07)" in c.unblocked_by
    stream = t.site(SYN_SITE).stream(LCL)
    assert stream.status == "undetermined"
    assert "exempt" not in json.dumps(stream.classification.model_dump()).replace(
        "never exempt", ""
    )


def test_swapping_in_a_final_rule_changes_the_answer(ev_root: Path, tmp_path: Path) -> None:
    """The same site and facts: provisional gives undetermined; a final rule gives eligible."""
    items = _passing_customer(ev_root)
    before = _run(ev_root, atlas_tree(tmp_path / "provisional"), *items)
    final_tree = atlas_tree(
        tmp_path / "final", states=FINAL_STATES, final=True, loader=FINAL_LOADER
    )
    after = _run(ev_root, final_tree, *items)
    assert after.loader.rule_status == "final", after.loader.reason
    assert _check(before, "LCL-RULE").status == "undetermined"
    lcl = _check(after, "LCL-RULE")
    assert lcl.status == "eligible", lcl.reason
    assert (
        after.site(SYN_SITE)
        .stream(LCL)
        .classification.startswith("admitted under final rule 9.0.0")
    )
    assert after.loader.adoption == "not_adopted_synthetic_mode"
    assert before.content_hash() != after.content_hash()


def test_the_overlay_holds_when_a_provisional_evaluator_says_eligible(
    ev_root: Path, tmp_path: Path
) -> None:
    atlas = atlas_tree(tmp_path / "atlas", loader=ELIGIBLE_PROVISIONAL_LOADER)
    t = _run(ev_root, atlas, *_passing_customer(ev_root))
    c = _check(t, "LCL-RULE")
    assert '"status":"eligible"' in t.site(SYN_SITE).stream(LCL).atlas_evaluation
    assert c.status == "undetermined"
    assert "PROVISIONAL RULE, NEVER EXEMPT" in c.reason


# --------------------------------------------------------------------------------------------
# Criterion 5: load_rule with the run's as_of, fresh process, re-read, failures undetermined
# --------------------------------------------------------------------------------------------


def test_the_rule_is_loaded_with_the_runs_as_of(ev_root: Path, tmp_path: Path) -> None:
    atlas = atlas_tree(tmp_path / "atlas")
    write_rule(atlas, segment_rule(released_on="2026-12-02"))
    early = _run(ev_root, atlas, as_of=date(2026, 12, 1))
    assert early.loader.outcome == "refused"
    assert "before the rule's released_on 2026-12-02" in _check(early, "LCL-RULE").reason
    late = _run(ev_root, atlas, as_of=date(2026, 12, 3))
    assert late.loader.outcome == "loaded", late.loader.reason
    assert late.loader.reported_as_of == date(2026, 12, 3)


def test_the_register_gate_is_asked_with_the_runs_as_of(ev_root: Path, tmp_path: Path) -> None:
    """The fixture rows were verified on 2026-11-17: ninety days on, the gate calls them stale."""
    atlas = atlas_tree(tmp_path / "atlas")
    fresh = _run(ev_root, atlas, as_of=date(2026, 12, 3))
    assert _check(fresh, "P511-ROUTE").status == "eligible"
    stale = _run(ev_root, atlas, as_of=date(2027, 3, 1))
    route = _check(stale, "P511-ROUTE")
    assert route.status == "undetermined" and "REG-02: RowStale" in route.reason
    assert stale.loader.outcome == "refused"


def test_each_run_rereads_the_register_in_a_fresh_process(ev_root: Path, tmp_path: Path) -> None:
    """One parent process, two runs, the register changed between them: the verdicts follow."""
    atlas = atlas_tree(tmp_path / "atlas")
    first = _run(ev_root, atlas)
    assert _check(first, "P511-ROUTE").status == "eligible"
    assert first.loader.outcome == "loaded"

    states = {**DEFAULT_STATES, "REG-02": "not_signed", "REG-42": "signed"}
    write_register(atlas, states)
    second = _run(ev_root, atlas)
    route = _check(second, "P511-ROUTE")
    assert route.status == "undetermined" and "REG-02: RowNotSignedOff" in route.reason
    assert second.loader.outcome == "refused"
    assert "REG-42: the register gate now passes this row" in _check(second, "LCL-RULE").reason
    assert first.content_hash() != second.content_hash()
    files = {f.name: f.sha256 for f in second.loader.files}
    assert files["register_file"] != {f.name: f.sha256 for f in first.loader.files}["register_file"]


def test_no_atlas_root_makes_every_register_check_undetermined(ev_root: Path) -> None:
    t = _run(ev_root, None, *_passing_customer(ev_root))
    assert t.loader.outcome == "not_run"
    for s in t.site(SYN_SITE).streams:
        assert s.status == "undetermined"
        for c in s.checks:
            assert c.status in ("undetermined", "not_applicable")
    assert all(r.verdict == "not_read" for r in t.row_verdicts)


def test_an_atlas_root_without_the_regulatory_tree_is_undetermined(
    ev_root: Path, tmp_path: Path
) -> None:
    t = _run(ev_root, tmp_path / "empty")
    assert t.loader.outcome == "error"
    assert "no roadmap/governance/regulatory tree" in t.loader.reason
    assert all(s.status == "undetermined" for s in t.site(SYN_SITE).streams)


FAILURES = {
    "import_error": ('raise RuntimeError("fixture import failure")\n', "RuntimeError"),
    "syntax_error": ("def broken(:\n", "SyntaxError"),
    "load_rule_raises": (
        "evaluate = base.evaluate\n\ndef load_rule(path=RULE, *, as_of, register_path=None):\n"
        '    raise KeyError("fixture loader failure")\n',
        "KeyError",
    ),
    "evaluate_raises": (
        "load_rule = base.load_rule\n\ndef evaluate(rule, site):\n"
        '    raise ValueError("fixture evaluator failure")\n',
        "ValueError",
    ),
    "process_exits": (
        "import os\nevaluate = base.evaluate\n\n"
        "def load_rule(path=RULE, *, as_of, register_path=None):\n    os._exit(7)\n",
        "exited with status 7",
    ),
    "garbage_on_stdout": (
        'import os\nprint("not json")\nevaluate = base.evaluate\n\n'
        "def load_rule(path=RULE, *, as_of, register_path=None):\n"
        "    sys.stdout.flush()\n    os._exit(0)\n",
        "not JSON",
    ),
    "file_changed_during_read": (
        "evaluate = base.evaluate\n\ndef load_rule(path=RULE, *, as_of, register_path=None):\n"
        '    reg = _HERE.parent / "regulatory_register.json"\n'
        '    reg.write_text(reg.read_text() + " ")\n'
        "    return base.load_rule(path, as_of=as_of, register_path=register_path)\n",
        "changed while they were read",
    ),
}


@pytest.mark.parametrize("mode", sorted(FAILURES))
def test_any_loader_failure_is_undetermined_never_a_pass(
    ev_root: Path, tmp_path: Path, mode: str
) -> None:
    body, expect = FAILURES[mode]
    atlas = atlas_tree(tmp_path / "atlas", loader=raising_loader(body))
    t = _run(ev_root, atlas, *_passing_customer(ev_root))
    lcl = _check(t, "LCL-RULE")
    assert lcl.status == "undetermined"
    assert expect in lcl.reason or expect in t.loader.reason, (lcl.reason, t.loader.reason)
    assert t.site(SYN_SITE).stream(LCL).status == "undetermined"
    assert str(atlas) not in t.model_dump_json()
    if mode in ("process_exits", "garbage_on_stdout", "file_changed_during_read"):
        # The whole bridge result is unusable: nothing that rests on the register passes.
        for s in t.site(SYN_SITE).streams:
            assert s.status == "undetermined", s.stream
            for c in s.checks:
                assert c.status in ("undetermined", "not_applicable"), c.check_id


def test_a_bridge_timeout_is_undetermined(
    ev_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "import time\nevaluate = base.evaluate\n\n"
        "def load_rule(path=RULE, *, as_of, register_path=None):\n    time.sleep(30)\n"
    )
    atlas = atlas_tree(tmp_path / "atlas", loader=raising_loader(body))
    monkeypatch.setattr(eng, "BRIDGE_TIMEOUT_S", 3)
    t = _run(ev_root, atlas, *_passing_customer(ev_root))
    assert "did not finish within 3 s" in t.loader.reason
    assert all(s.status == "undetermined" for s in t.site(SYN_SITE).streams)


def test_the_engine_reads_the_rule_only_through_the_bridge() -> None:
    """No GridQ module imports the Atlas files; the engine starts the bridge script with -I."""
    src = Path(eng.__file__).read_text(encoding="utf-8")
    assert '[sys.executable, "-I", "-B", str(BRIDGE)]' in src
    for path in Path(eng.__file__).parent.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "eligibility_bridge.py":
            assert "import gridq" not in text and "from gridq" not in text
            continue
        assert "spec_from_file_location" not in text, path.name
    bridge = (Path(eng.__file__).parent / "eligibility_bridge.py").read_text(encoding="utf-8")
    assert bridge.count("loader.load_rule(") == 1
    assert "as_of=as_of" in bridge


# --------------------------------------------------------------------------------------------
# Adoption, identity and inputs
# --------------------------------------------------------------------------------------------


def _real_like_dossier(ev_root: Path, site_id: str = "fixture_uw02_site"):
    """A fictitious non-synthetic site with nothing evidenced (TEST FIXTURE)."""
    return build_dossier(register(entry(site_id)), as_of=AS_OF, evidence_root=ev_root)


def test_a_registered_run_relies_only_on_an_adopted_loader(ev_root: Path, tmp_path: Path) -> None:
    d = _real_like_dossier(ev_root)
    adopted = _run(
        ev_root,
        atlas_tree(tmp_path / "a"),
        dossier=d,
        site_id="fixture_uw02_site",
        admission="registered_only",
        controlled=assets(record_class="registered"),
    )
    assert adopted.loader.adoption == "adopted"
    assert any(r.verdict == "passes" for r in adopted.row_verdicts)

    drifted = atlas_tree(tmp_path / "b")
    rule_py = drifted / REGULATORY / "load_control" / "rule.py"
    rule_py.write_text(rule_py.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    t = _run(
        ev_root,
        drifted,
        dossier=d,
        site_id="fixture_uw02_site",
        admission="registered_only",
        controlled=assets(record_class="registered"),
    )
    assert t.loader.adoption == "not_adopted"
    assert all(r.verdict == "not_relied_on" for r in t.row_verdicts)
    assert all(s.status == "undetermined" for s in t.site("fixture_uw02_site").streams)
    assert "not an adopted pair" in _check(t, "P511-ROUTE", "fixture_uw02_site").reason
    assert any(a.action_id == "ADOPT-LOADER" for a in t.owner_actions)


def test_the_atlas_path_stays_out_of_the_hash(ev_root: Path, tmp_path: Path) -> None:
    a = _run(ev_root, atlas_tree(tmp_path / "one"))
    b = _run(ev_root, atlas_tree(tmp_path / "two" / "deeper"))
    assert a.content_hash() == b.content_hash()
    assert a.labels == (SYNTHETIC_LABEL,)


def test_the_inputs_must_describe_one_run(ev_root: Path, tmp_path: Path) -> None:
    d = synthetic_dossier(ev_root)
    kw = {"atlas_root": None, "evidence_admission": SYNTHETIC}
    with pytest.raises(EligibilityInputError, match="the dossier is as of"):
        build_eligibility(d, as_of=date(2026, 11, 27), **kw)  # type: ignore[arg-type]
    with pytest.raises(EligibilityInputError, match="are as of"):
        build_eligibility(
            d,
            as_of=AS_OF,
            facts=[facts(SYN_SITE, as_of=date(2026, 11, 25))],
            **kw,  # type: ignore[arg-type]
        )
    with pytest.raises(EligibilityInputError, match="does not hold"):
        build_eligibility(d, as_of=AS_OF, facts=[facts("SYNTHETIC-UW02-OTHER")], **kw)  # type: ignore[arg-type]
    with pytest.raises(EligibilityInputError, match="SYNTHETIC- site"):
        build_eligibility(d, as_of=AS_OF, atlas_root=None, evidence_admission="registered_only")
    real = _real_like_dossier(ev_root)
    with pytest.raises(EligibilityInputError, match="no synthetic input record"):
        build_eligibility(
            real,
            as_of=AS_OF,
            atlas_root=None,
            evidence_admission="registered_only",
            controlled_assets=assets(),
        )


def test_synthetic_evidence_never_places_a_fact_on_a_registered_run(
    ev_root: Path, tmp_path: Path
) -> None:
    site = "fixture_uw02_site"
    fact = evidenced(
        ev_root,
        site,
        FactField.GSP_GROUPS,
        {"groups": ["_A"]},
        evidence_type="mpan_registration",
        role="elexon",
    )
    t = build_eligibility(
        _real_like_dossier(ev_root, site),
        as_of=AS_OF,
        atlas_root=atlas_tree(tmp_path / "atlas"),
        evidence_admission="registered_only",
        facts=[
            EligibilityFacts(
                record="gridq.eligibility_facts",
                record_version="1.0.0",
                record_class="registered",
                site_id=site,
                as_of=AS_OF,
                facts=(fact,),
            )
        ],
        evidence_registry=load_evidence_registry(ev_root),
    )
    c = _check(t, "MET-GSP", site)
    assert c.status == "undetermined"
    assert "synthetic fixture" in c.read[1].note


def test_an_unknown_contracting_entity_list_is_absent_never_empty() -> None:
    from gridq.underwriting.eligibility import _Fact, lcl_site_input

    unknown = {f: _Fact("unknown", None, (), "x", ()) for f in FactField}
    out = lcl_site_input("S", unknown, None)
    assert "contracting_entity_customers" not in out and "customer" not in out
    assert out["assets"] == []


# --------------------------------------------------------------------------------------------
# Real sites, the CLI, the snapshots and stand-alone status
# --------------------------------------------------------------------------------------------


def test_real_candidate_sites_without_facts_are_undetermined_on_every_stream(
    ev_root: Path, tmp_path: Path
) -> None:
    d = build_dossier(default_register(ev_root / "none.json"), as_of=AS_OF, evidence_root=ev_root)
    t = build_eligibility(
        d,
        as_of=AS_OF,
        atlas_root=atlas_tree(tmp_path / "atlas"),
        evidence_admission="registered_only",
    )
    assert t.sites
    for site in t.sites:
        for s in site.streams:
            assert s.status == "undetermined", (s.stream, [c.reason for c in s.checks])
        assert site.data_requests


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("dossiers/\n", encoding="utf-8")
    return repo


def test_the_cli_writes_only_where_git_ignores_and_prints_no_site_id(
    ev_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path)
    args = [
        "--as-of",
        "2026-11-26",
        "--evidence-dir",
        str(ev_root),
        "--register",
        str(ev_root / "none.json"),
        "--atlas-root",
        str(atlas_tree(tmp_path / "atlas")),
    ]
    assert main([*args, "--out", str(repo / "tracked")]) == 3
    assert not (repo / "tracked").exists()
    assert main([*args, "--out", str(repo / "dossiers" / "eligibility")]) == 0
    out = capsys.readouterr().out
    assert "atlas_root: --atlas-root" in out and "streams_eligible: 0" in out
    written = json.loads(next((repo / "dossiers" / "eligibility").glob("*.json")).read_text())
    for site in written["sites"]:
        assert site["site_id"] not in out


def test_the_atlas_root_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(eng.ATLAS_ROOT_ENV, str(tmp_path / "env"))
    assert eng.resolve_atlas_root(tmp_path / "x") == (tmp_path / "x", "--atlas-root")
    assert eng.resolve_atlas_root() == (tmp_path / "env", eng.ATLAS_ROOT_ENV)
    monkeypatch.delenv(eng.ATLAS_ROOT_ENV)
    root, source = eng.resolve_atlas_root()
    assert source in ("sibling ../atlas", "not found")


def test_the_snapshots_are_the_adopted_loader() -> None:
    for name, pin in PIN["files"].items():
        digest = hashlib.sha256((SNAPSHOTS / name).read_bytes()).hexdigest()
        assert "sha256:" + digest == pin["sha256"], name
    (adopted,) = ADOPTED_LOADERS
    assert adopted.atlas_commit == PIN["atlas_commit"]
    assert adopted.loader_sha256 == PIN["files"]["rule.py.snapshot"]["sha256"]
    assert adopted.register_module_sha256 == PIN["files"]["register.py.snapshot"]["sha256"]


def test_the_sibling_atlas_checkout_matches_the_adopted_loader() -> None:
    """Drift check (D134). Fails when Atlas changes the loader or gate: adopt it first."""
    if not (SIBLING_ATLAS / REGULATORY).is_dir():
        pytest.skip("no sibling Atlas checkout")
    for name, pin in PIN["files"].items():
        shown = subprocess.run(
            ["git", "-C", str(SIBLING_ATLAS), "show", f"{PIN['atlas_commit']}:{pin['atlas_path']}"],
            capture_output=True,
            check=False,
        )
        if shown.returncode == 0:
            assert "sha256:" + hashlib.sha256(shown.stdout).hexdigest() == pin["sha256"], name
        live = (SIBLING_ATLAS / pin["atlas_path"]).read_bytes()
        assert "sha256:" + hashlib.sha256(live).hexdigest() == pin["sha256"], (
            f"Atlas {pin['atlas_path']} has changed since {PIN['atlas_commit'][:12]}: review it "
            "and add an ADOPTED_LOADERS entry (with its decision record) before relying on it"
        )


def test_the_engine_stands_alone_and_never_touches_the_study_basis() -> None:
    """R8 and D57: nothing imports the engine yet, and it imports no basis, gate or study code."""
    src = Path(eng.__file__).parents[1]
    ours = {
        "eligibility.py",
        "eligibility_bridge.py",
        "eligibility_model.py",
        "eligibility_rules.py",
    }
    for path in sorted(src.rglob("*.py")):
        if path.parent.name == "underwriting" and path.name in ours:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                names = [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            assert not any("underwriting.eligibility" in n for n in names), (path, names)
    forbidden = (
        "gridq.schemas.study",
        "gridq.extensions",
        "gridq.underwriting.gate",
        "gridq.underwriting.engine",
        "gridq.underwriting.rules",
        "gridq.quotes",
        "gridq.app",
    )
    for name in ours:
        tree = ast.parse((src / "underwriting" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden), (name, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), (name, alias.name)
