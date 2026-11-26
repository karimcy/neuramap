"""Tests for :mod:`gridq.dev.call_path_audit`.

Three groups, and the split is deliberate:

* **The catches.** One synthetic case per check, each reproducing the *shape* of a real
  historical instance of "landed but never called". These pin what the audit is for.
* **The known false positives.** The audit was measured at roughly a 25% false-positive rate,
  concentrated entirely in check B. Those are pinned too, so the rate is a documented property
  of the tool rather than a surprise the first reviewer discovers. A test that asserts a false
  positive looks odd until you remember the alternative: an unmeasured tool whose users find
  out by being annoyed.
* **The regressions.** Seven resolution rules whose absence produced a measured wrong answer in
  the prototype. The most important is the *false negative* of fix 1 (global keyword matching
  instead of per-target resolution), which silently hid two of the historical instances. That
  one has its own test and its own name.

Every case is a synthetic tree under ``tmp_path``. Nothing here reads or writes the repo's own
source, and nothing imports the code being analysed.
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path
from textwrap import dedent

import pytest

from gridq.dev.call_path_audit import (
    AllowlistEntry,
    BaselineUnavailableError,
    Coverage,
    apply_allowlist,
    audit,
    extract_ref,
    load_allowlist,
    main,
    resolve_baseline_ref,
    run_audit,
)

# --- helpers ---------------------------------------------------------------


def _tree(root: Path, core: dict[str, str], tests: dict[str, str] | None = None) -> Path:
    """Write a synthetic repo (``<root>/src/gridq/...`` plus optional ``<root>/tests/...``)."""
    for name, src in core.items():
        p = root / "src" / "gridq" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dedent(src).lstrip(), encoding="utf-8")
    for name, src in (tests or {}).items():
        p = root / "tests" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dedent(src).lstrip(), encoding="utf-8")
    return root


def _keys(report, check: str | None = None) -> set[tuple[str, str, str | None]]:
    return {
        (f.module, f.qualname, f.param)
        for f in report.findings
        if check is None or f.check == check
    }


# --- the catches -----------------------------------------------------------


def test_check_a_flags_a_new_public_symbol_nothing_else_references(tmp_path: Path) -> None:
    """Historical instance 1: a whole gate implemented, tested, and called from nowhere.

    ``reconcile_declared_against_measured`` had zero references in ``src/`` outside its own
    module while nine tests exercised it directly. That is the failure mode verbatim, and the
    ``seen_in_tests`` flag is what makes it legible.
    """
    root = _tree(
        tmp_path,
        {
            "reconciliation.py": """
                def reconcile_declared_against_measured(declared, measured):
                    return declared == measured
            """,
            "pipeline.py": """
                def assess_confidence(site):
                    return "medium"
            """,
        },
        {
            "test_reconciliation.py": """
                from gridq.reconciliation import reconcile_declared_against_measured

                def test_it():
                    assert reconcile_declared_against_measured(1, 1)
            """,
        },
    )
    report = audit(root, checks=("A",))

    assert ("reconciliation", "reconcile_declared_against_measured", None) in _keys(report, "A")
    assert ("pipeline", "assess_confidence", None) in _keys(report, "A")
    gate = next(f for f in report.findings if f.qualname == "reconcile_declared_against_measured")
    assert gate.seen_in_tests is True  # tested, and never exercised in production
    assert gate.kind == "symbol"


def test_check_b_flags_a_model_field_no_production_site_ever_populates(tmp_path: Path) -> None:
    """Historical instance 4: the availability compiler, fed ``None`` forever.

    The acceptance phrasing for this case is "model field written but never read"; the real
    instance is its mirror image — the field is *declared and read* (``d.parameters`` is
    consumed on every run) and **never populated** at any construction site, so the consumer
    always receives ``None`` and always returns ``[]``. Check B detects the population channel,
    which is why a naive "is the compiler called?" check reports green on this one.

    It is caught only because a pydantic model field is a keyword parameter of a generated
    ``__init__`` and is treated identically to one.
    """
    root = _tree(
        tmp_path,
        {
            "interface.py": """
                from gridq.provenance import GridQModel

                class DispatchConstraintDescriptor(GridQModel):
                    name: str = ""
                    parameters: object | None = None
            """,
            "gb_streams.py": """
                from gridq.interface import DispatchConstraintDescriptor

                def descriptors():
                    return [DispatchConstraintDescriptor(name="soc")]
            """,
            "deliverability.py": """
                def commitments_from_constraints(descriptors):
                    return [d.parameters for d in descriptors if d.parameters is not None]
            """,
        },
        {
            "test_commitment.py": """
                from gridq.interface import DispatchConstraintDescriptor

                def test_parameters_are_compiled():
                    d = DispatchConstraintDescriptor(name="soc", parameters=object())
                    assert d.parameters is not None
            """,
        },
    )
    report = audit(root, checks=("B",))

    hit = next(f for f in report.findings if f.param == "parameters")
    assert hit.kind == "model_field"
    assert hit.module == "interface"
    assert hit.qualname == "DispatchConstraintDescriptor"
    assert hit.seen_in_tests is True  # tests populate it; production never does
    # `name=` IS supplied by a production construction site, so it must not be flagged.
    assert ("interface", "DispatchConstraintDescriptor", "name") not in _keys(report, "B")


def test_check_c_flags_a_symbol_no_production_path_constructs(tmp_path: Path) -> None:
    """A type that only tests ever construct, reachable from no entry point.

    Check A stays quiet because the symbol *is* referenced elsewhere in ``src/`` (as a type
    annotation), which is exactly why check C exists: being mentioned is not being reached.
    """
    root = _tree(
        tmp_path,
        {
            "cli.py": """
                from gridq.engine import run_engine

                def main():
                    return run_engine()
            """,
            "engine.py": """
                def run_engine():
                    return 1

                class ExportFeasibilityOverride:
                    limit_kw = 0.0
            """,
            "reporting.py": """
                from gridq.engine import ExportFeasibilityOverride

                def describe(override: ExportFeasibilityOverride) -> str:
                    return "override"
            """,
        },
        {
            "test_override.py": """
                from gridq.engine import ExportFeasibilityOverride

                def test_override():
                    assert ExportFeasibilityOverride().limit_kw == 0.0
            """,
        },
    )
    report = audit(root, checks=("A", "C"))

    assert ("engine", "ExportFeasibilityOverride", None) in _keys(report, "C")
    assert ("engine", "ExportFeasibilityOverride", None) not in _keys(report, "A")
    assert ("engine", "run_engine", None) not in _keys(report, "C")  # reached from cli.main
    orphan = next(f for f in report.findings if f.qualname == "ExportFeasibilityOverride")
    assert orphan.seen_in_tests is True


# --- the false negative that must never come back --------------------------


def test_kwargs_resolve_per_target_never_by_global_name(tmp_path: Path) -> None:
    """Fix 1, and the single most important test in this file.

    The prototype's first version collected one global set of "keyword names passed anywhere".
    A pass-through such as ``contract_tenor_years=contract_tenor_years`` in *some other module*
    marked the name satisfied *everywhere*, and that one shortcut hid two of the historical
    instances outright. A false negative in a tool bought to find false negatives is the worst
    available outcome, so both directions are pinned here:

    * a keyword passed to a **different** target must NOT satisfy this one, and
    * a keyword passed to the **correct** target MUST satisfy it (or the tool cries wolf).
    """
    root = _tree(
        tmp_path,
        {
            "cashflows.py": """
                def build_cashflow_model(inputs, *, contract_tenor_years=None):
                    return contract_tenor_years or 10
            """,
            "financial_model.py": """
                def build_financial_case(inputs, *, contract_tenor_years=None):
                    return contract_tenor_years or 10
            """,
            "wiring.py": """
                from gridq.cashflows import build_cashflow_model

                def run(inputs, contract_tenor_years=None):
                    # A pass-through to ONE target. Under global name matching this wrongly
                    # satisfied `contract_tenor_years` for every function in the tree.
                    return build_cashflow_model(inputs, contract_tenor_years=contract_tenor_years)
            """,
        },
    )
    flagged = _keys(audit(root, checks=("B",)), "B")

    assert ("financial_model", "build_financial_case", "contract_tenor_years") in flagged
    assert ("cashflows", "build_cashflow_model", "contract_tenor_years") not in flagged


# --- the measured false positives ------------------------------------------


def test_known_false_positives_deliberate_defaults_are_still_flagged(tmp_path: Path) -> None:
    """Pin the audit's false-positive class so the ~25% rate is documented, not discovered.

    Six of the 24 findings in the calibration run were false positives, all in check B, and
    every one had its justification already written in the source:

    1. ``wholesale_contracted_stream_result.cannibalised_floor_gbp`` — the call site says in
       so many words that it is deliberately not passed.
    2. ``generate_quote.derived_hh_dir`` — an entry point's own kwargs are its public API and
       its callers live outside ``src/``. (This one the audit now exempts by policy; the
       exemption is asserted in :func:`test_entry_point_signatures_are_exempt_and_counted`.)
    3. ``eac_response_value.toll_reference`` — a documented data-driven opt-in.
    4-6. ``payable_delivery_fraction.{grace, multiplier, payable_over_delivery}`` — sourced ENA
       taper constants whose defaults *are* the intended values.

    The audit flags shapes 1, 3 and 4-6 and it is right to: statically they are
    indistinguishable from an inert parameter. That is the cost of the check, it is bounded,
    and each is a one-line allowlist entry. This test fails if someone "fixes" the false
    positives with a heuristic — which would take the false negatives of fix 1 straight back.
    """
    root = _tree(
        tmp_path,
        {
            "valuation.py": """
                def wholesale_contracted_stream_result(dispatch, *, cannibalised_floor_gbp=0.0):
                    # `cannibalised_floor_gbp` is deliberately NOT passed by the demo path.
                    return cannibalised_floor_gbp
            """,
            "gb_streams.py": """
                def payable_delivery_fraction(
                    delivered,
                    *,
                    grace=0.05,
                    multiplier=1.0,
                    payable_over_delivery=1.1,
                ):
                    # Sourced ENA taper constants: the defaults ARE the intended values.
                    return delivered * multiplier
            """,
            "caller.py": """
                from gridq.gb_streams import payable_delivery_fraction
                from gridq.valuation import wholesale_contracted_stream_result

                def run(dispatch, delivered):
                    return (
                        wholesale_contracted_stream_result(dispatch),
                        payable_delivery_fraction(delivered),
                    )
            """,
        },
    )
    flagged = _keys(audit(root, checks=("B",)), "B")

    assert ("valuation", "wholesale_contracted_stream_result", "cannibalised_floor_gbp") in flagged
    for param in ("grace", "multiplier", "payable_over_delivery"):
        assert ("gb_streams", "payable_delivery_fraction", param) in flagged
    assert len(flagged) == 4


def test_entry_point_signatures_are_exempt_and_counted(tmp_path: Path) -> None:
    """An entry point's own kwargs are its public API; "nobody in src/ passes it" proves nothing.

    The exemption is real suppression, so it is counted in COVERAGE rather than applied quietly.
    """
    root = _tree(
        tmp_path,
        {
            "pipeline.py": """
                def generate_quote(site, *, derived_hh_dir=None, release=None):
                    return (site, derived_hh_dir, release)
            """,
        },
    )
    report = audit(root, checks=("B",))

    assert _keys(report, "B") == set()
    assert report.coverage.suppressed_entry_func == 2


# --- resolution rules whose absence produced a measured wrong answer -------


def test_positional_passing_satisfies_a_defaulted_parameter(tmp_path: Path) -> None:
    """Fix 2. Missing this produced a false positive on a parameter passed positionally."""
    root = _tree(
        tmp_path,
        {
            "calendar.py": """
                def duos_rate_series_with_absences(rates, windows=None):
                    return rates, windows

                def unpassed(rates, absences=None):
                    return rates, absences
            """,
            "solver.py": """
                from gridq.calendar import duos_rate_series_with_absences, unpassed

                def build(rates, windows):
                    return duos_rate_series_with_absences(rates, windows), unpassed(rates)
            """,
        },
    )
    flagged = _keys(audit(root, checks=("B",)), "B")

    assert ("calendar", "duos_rate_series_with_absences", "windows") not in flagged
    assert ("calendar", "unpassed", "absences") in flagged  # negative control


def test_bound_method_calls_do_not_count_self_as_an_argument(tmp_path: Path) -> None:
    """Fix 3. Missing the ``self`` offset produced a false positive on ``check_gates.context``."""
    root = _tree(
        tmp_path,
        {
            "streams.py": """
                class DemandFlexibilityServiceStream:
                    def check_gates(self, site_model, assumptions, context=None):
                        return context
            """,
            "runner.py": """
                def run(self, site_model, assumptions, context):
                    return self.check_gates(site_model, assumptions, context)
            """,
        },
    )
    flagged = _keys(audit(root, checks=("B",)), "B")

    assert (
        "streams",
        "DemandFlexibilityServiceStream.check_gates",
        "context",
    ) not in flagged


def test_model_copy_update_dict_keys_populate_model_fields(tmp_path: Path) -> None:
    """Fix 6. A field populated through a dict literal is populated."""
    root = _tree(
        tmp_path,
        {
            "provenance.py": """
                class GridQModel:
                    pass
            """,
            "site.py": """
                from gridq.provenance import GridQModel

                class SiteModel(GridQModel):
                    derived_assumptions: list = []
                    untouched_field: int = 0
            """,
            "hh_synthesis.py": """
                from gridq.site import SiteModel

                def synthesise(site: SiteModel):
                    return site.model_copy(update={"derived_assumptions": []})
            """,
        },
    )
    flagged = _keys(audit(root, checks=("B",)), "B")

    assert ("site", "SiteModel", "derived_assumptions") not in flagged
    assert ("site", "SiteModel", "untouched_field") in flagged  # negative control


def test_getattr_with_a_string_literal_is_a_reference_and_a_call_edge(tmp_path: Path) -> None:
    """Duck-typed access through ``getattr(o, "name")`` needs no allowlist entry."""
    root = _tree(
        tmp_path,
        {
            "cli.py": """
                from gridq.probe import probe

                def main(result):
                    return probe(result)
            """,
            "results.py": """
                def selected_dispatch_energy(result):
                    return 0.0
            """,
            "probe.py": """
                def probe(result):
                    return getattr(result, "selected_dispatch_energy", None)
            """,
        },
    )
    report = audit(root, checks=("A", "C"))

    assert ("results", "selected_dispatch_energy", None) not in _keys(report, "A")
    assert ("results", "selected_dispatch_energy", None) not in _keys(report, "C")


def test_class_bases_and_signature_annotations_count_as_references(tmp_path: Path) -> None:
    """Fixes 4 and 5, plus fix 7 — this module's own, measured after the design was written.

    The prototype visited only a function's *body*, so a Protocol used exclusively in
    signatures looked unreferenced. Measured on the real tree that was 8 of 51 whole-tree
    check-A findings — all genuine type-only uses. Nothing is lost by counting them: a class
    that is annotated about but never constructed is still reported by check C.
    """
    root = _tree(
        tmp_path,
        {
            "provenance.py": """
                class GridQModel:
                    pass
            """,
            "protocols.py": """
                class ResultLike:
                    pass

                class CashflowProvider:
                    pass
            """,
            "consumer.py": """
                from gridq.protocols import CashflowProvider, ResultLike
                from gridq.provenance import GridQModel

                class Quote(GridQModel):
                    total: float = 0.0

                def summarise(result: ResultLike, provider: CashflowProvider | None = None) -> str:
                    return "ok"
            """,
        },
    )
    flagged_a = _keys(audit(root, checks=("A",)), "A")

    assert ("provenance", "GridQModel", None) not in flagged_a  # fix 4: a base is a use
    assert ("protocols", "ResultLike", None) not in flagged_a  # fix 7: annotation is a use
    assert ("protocols", "CashflowProvider", None) not in flagged_a


def test_a_decorator_closure_does_not_hide_everything_it_calls(tmp_path: Path) -> None:
    """Fix 8, this module's own, and it was found by running the audit rather than reading it.

    A decorator that wraps a method in a nested ``_guarded`` function makes every call *inside*
    that closure invisible to a by-name walk: the closure is defined and returned, never called
    by its own name. Measured on the real tree, one such wrapper made a four-function cluster
    look unreachable while the wrapper calls it on every gate check — four false positives from
    one decorator, and its own ``context`` parameter a fifth in check B.

    Both halves are pinned: the closure's callees are reached, and the closure's own parameters
    are not flagged (and the suppression is counted, because it is a real gap in coverage).
    """
    root = _tree(
        tmp_path,
        {
            "cli.py": """
                from gridq.streams import build_stream

                def main():
                    return build_stream()
            """,
            "streams.py": """
                from gridq.gates import enforce_declared_inputs

                def build_stream():
                    def _guarded(self, site, assumptions, context=None):
                        return enforce_declared_inputs(site, context)

                    return _guarded
            """,
            "gates.py": """
                def enforce_declared_inputs(site, context):
                    return input_presence(site)

                def input_presence(site):
                    return True
            """,
        },
    )
    report = audit(root, checks=("B", "C"))

    assert ("gates", "enforce_declared_inputs", None) not in _keys(report, "C")
    assert ("gates", "input_presence", None) not in _keys(report, "C")
    assert ("streams", "build_stream._guarded", "context") not in _keys(report, "B")
    assert report.coverage.suppressed_closures == 1
    assert "closure parameters" in "\n".join(report.coverage.lines())


def test_a_reached_subclass_reaches_the_bases_it_inherits_from(tmp_path: Path) -> None:
    """Fix 9, and the reason fix 8 alone was not enough on the real tree.

    The wrapper that made the four-function cluster look dead is installed by
    ``__init_subclass__`` — an *implicit interpreter hook*. Nothing calls it by name, so a
    by-name walk can never reach it directly; it is reachable only because a constructed
    subclass drags its base class along. Inheritance edges are how the audit sees that, and
    they are independently correct: a subclass's instances answer to every method its bases
    define.
    """
    root = _tree(
        tmp_path,
        {
            "cli.py": """
                from gridq.gb_streams import DfsStream

                def main():
                    return DfsStream().value()
            """,
            "base.py": """
                from gridq.gates import install_guard

                class BaseValueStream:
                    def __init_subclass__(cls, **kwargs):
                        install_guard(cls)

                    def value(self):
                        return 0.0
            """,
            "gb_streams.py": """
                from gridq.base import BaseValueStream

                class DfsStream(BaseValueStream):
                    pass
            """,
            "gates.py": """
                def install_guard(cls):
                    return declared_inputs(cls)

                def declared_inputs(cls):
                    return ()
            """,
        },
    )
    unreached = _keys(audit(root, checks=("C",)), "C")

    assert ("gates", "install_guard", None) not in unreached
    assert ("gates", "declared_inputs", None) not in unreached
    assert ("base", "BaseValueStream", None) not in unreached


def test_a_symbol_used_only_inside_its_own_module_is_used(tmp_path: Path) -> None:
    """The internal half of fix 5, which the cross-module test above does not reach.

    A Protocol declared and consumed entirely within one module — annotated about, never called
    — is used. Check A's "no reference outside its own module" question has to be paired with
    "and no use inside it either", counting annotations, or every internal type alias becomes a
    finding. This case is a separate code path from the cross-module one and needs its own test:
    a mutation that dropped it survived the entire suite until this existed.
    """
    root = _tree(
        tmp_path,
        {
            "cashflows.py": """
                class ResultLike:
                    pass

                class NeverMentioned:
                    pass

                def summarise(result: ResultLike) -> float:
                    return 0.0
            """,
        },
    )
    flagged = _keys(audit(root, checks=("A",)), "A")

    assert ("cashflows", "ResultLike", None) not in flagged
    assert ("cashflows", "NeverMentioned", None) in flagged  # negative control


def test_splat_call_sites_make_arity_unknown_and_are_disclosed(tmp_path: Path) -> None:
    """``f(**payload)`` could supply anything, so nothing about ``f`` is asserted — and it is
    counted, because an unexamined target is exactly the kind of gap that must not be silent."""
    root = _tree(
        tmp_path,
        {
            "sink.py": """
                def sink(payload, *, option=None):
                    return payload, option
            """,
            "caller.py": """
                from gridq.sink import sink

                def run(payload, extras):
                    return sink(payload, **extras)
            """,
        },
    )
    report = audit(root, checks=("B",))

    assert ("sink", "sink", "option") not in _keys(report, "B")
    assert report.coverage.arity_unknown_targets >= 1
    assert "arity unknown" in "\n".join(report.coverage.lines())


# --- diff-scoping and dedup ------------------------------------------------


def test_baseline_scoping_suppresses_pre_existing_findings(tmp_path: Path) -> None:
    """A default that has existed for months is a design choice; a new one is a suspicion.

    Whole-tree mode produced hundreds of findings on the real repo and would be switched off
    within a week; the diff-scoped run is the shippable one.
    """
    baseline = _tree(
        tmp_path / "base",
        {
            "streams.py": """
                def old_stream(inputs, *, longstanding=None):
                    return inputs, longstanding
            """,
        },
    )
    current = _tree(
        tmp_path / "cur",
        {
            "streams.py": """
                def old_stream(inputs, *, longstanding=None):
                    return inputs, longstanding

                def new_stream(inputs, *, brand_new=None):
                    return inputs, brand_new
            """,
        },
    )
    report = audit(current, baseline_tree=baseline, checks=("B",))

    assert _keys(report, "B") == {("streams", "new_stream", "brand_new")}
    assert report.coverage.suppressed_pre_existing >= 1


def test_dedup_folds_parameters_into_the_unreachable_function_that_owns_them(
    tmp_path: Path,
) -> None:
    """If a function has no call path at all, its individual parameters are not separate news.

    Without this the largest unwired subsystem in the real repo emitted five redundant
    parameter hits and buried the finding that mattered.
    """
    root = _tree(
        tmp_path,
        {
            "orphan.py": """
                def render_publishable_pack(record, *, run_store_root=None, confidence=None):
                    return record, run_store_root, confidence
            """,
        },
    )
    report = audit(root, checks=("A", "B"))

    assert _keys(report, "B") == set()
    assert ("orphan", "render_publishable_pack", None) in _keys(report, "A")
    assert report.coverage.suppressed_dedup == 2


# --- no silent caps --------------------------------------------------------


def test_an_unparseable_file_is_named_not_silently_skipped(tmp_path: Path) -> None:
    """A tool that quietly drops a file it cannot read claims coverage it does not have —
    which is the same class of failure it exists to catch."""
    root = _tree(
        tmp_path,
        {
            "good.py": "def fine():\n    return 1\n",
            "broken.py": "def broken(:\n    pass\n",
        },
    )
    report = audit(root, checks=("A",))

    skipped = dict(report.coverage.core_files_skipped)
    assert "src/gridq/broken.py" in skipped
    assert "syntax error" in skipped["src/gridq/broken.py"]
    assert "src/gridq/broken.py" in report.summary()


def test_a_missing_test_tree_is_disclosed_because_it_changes_every_flag(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"mod.py": "def orphan():\n    return 1\n"})
    report = audit(root, checks=("A",))

    assert report.coverage.tests_root_found is False
    assert "no test tree" in "\n".join(report.coverage.lines())


def test_display_limit_discloses_how_many_findings_it_hid(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "mod.py": """
                def a():
                    return 1

                def b():
                    return 2

                def c():
                    return 3
            """
        },
    )
    report = audit(root, checks=("A",))
    text = report.summary(max_display=1)

    assert len(report.findings) == 3
    assert "2 finding(s) hidden from this listing" in text


def test_the_report_states_that_it_reports_suspicions_and_states_its_limit(
    tmp_path: Path,
) -> None:
    """Wording is load-bearing at a 25% false-positive rate, and so is the known limit.

    The audit proves a call path exists; it never proves a value flows. Two of the eight
    historical instances had a call path and were inert anyway. A reader who takes a clean run
    as proof of wiring has been misled by the tool.
    """
    root = _tree(tmp_path, {"mod.py": "def orphan():\n    return 1\n"})
    text = audit(root, checks=("A",)).summary()

    assert "SUSPICIONS, NOT VERDICTS" in text
    assert "CALL PATH EXISTS" in text and "VALUE FLOWS" in text
    assert "~25%" in text
    assert "sufficient for nothing" in text


def test_a_module_that_explodes_on_import_is_analysed_anyway(tmp_path: Path) -> None:
    """The audit reads source text and never imports it, so import side effects cannot change
    its answer — and a module that cannot be imported at all is still analysed."""
    root = _tree(
        tmp_path,
        {
            "explosive.py": """
                raise RuntimeError("module-level side effect")

                def unreachable_symbol():
                    return 1
            """,
        },
    )
    report = audit(root, checks=("A",))

    assert ("explosive", "unreachable_symbol", None) in _keys(report, "A")
    assert report.coverage.core_files_skipped == []


# --- git baselines ---------------------------------------------------------


def _init_repo(root: Path) -> None:
    _tree(
        root,
        {
            "streams.py": """
                def committed_stream(inputs, *, longstanding=None):
                    return inputs, longstanding
            """,
        },
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-qm",
            "base",
        ],
        cwd=root,
        check=True,
    )


def test_extract_ref_uses_git_archive_and_leaves_the_working_tree_untouched(
    tmp_path: Path,
) -> None:
    """``git archive`` into a temp dir — never ``git worktree``, never ``git stash``.

    The audit has to be safe to run beside a live test suite, so "the working tree is
    unchanged" is a property worth asserting rather than assuming.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "src" / "gridq" / "streams.py").write_text(
        "def committed_stream(inputs, *, longstanding=None, added_later=None):\n"
        "    return inputs, longstanding, added_later\n",
        encoding="utf-8",
    )
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout

    sha, label = resolve_baseline_ref(repo, "HEAD")
    dest = extract_ref(repo, sha, tmp_path / "extract")

    assert (dest / "src" / "gridq" / "streams.py").is_file()
    assert "added_later" not in (dest / "src" / "gridq" / "streams.py").read_text()
    assert label.startswith("HEAD (")
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert before == after


def test_run_audit_diff_scopes_against_head_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "src" / "gridq" / "streams.py").write_text(
        "def committed_stream(inputs, *, longstanding=None):\n"
        "    return inputs, longstanding\n\n\n"
        "def uncommitted_stream(inputs, *, brand_new=None):\n"
        "    return inputs, brand_new\n",
        encoding="utf-8",
    )

    report, warnings = run_audit(repo_root=repo, checks=("B",))

    assert _keys(report, "B") == {("streams", "uncommitted_stream", "brand_new")}
    assert warnings == []
    assert "diff-scoped vs HEAD" in report.coverage.baseline_label


def test_an_unresolvable_baseline_is_loud_never_a_silent_whole_tree_fallback(
    tmp_path: Path,
) -> None:
    """The two modes differ by two orders of magnitude in finding count. A silent switch
    between them would read as a fact about the code."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    with pytest.raises(BaselineUnavailableError):
        resolve_baseline_ref(repo, "no-such-ref-at-all")
    with pytest.raises(BaselineUnavailableError):
        run_audit(repo_root=repo, baseline="no-such-ref-at-all")

    assert main(["--repo-root", str(repo), "--baseline", "no-such-ref-at-all"]) == 1


def test_cli_reports_findings_but_only_strict_makes_them_an_exit_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """At a measured 25% false-positive rate, findings are a prompt to check, not a verdict —
    so the default exit status is 0. ``--strict`` is for the pre-merge gate, where an unfixed
    finding would be grandfathered into every later baseline and become invisible."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "src" / "gridq" / "streams.py").write_text(
        "def committed_stream(inputs, *, longstanding=None):\n"
        "    return inputs, longstanding\n\n\n"
        "def uncommitted_stream(inputs, *, brand_new=None):\n"
        "    return inputs, brand_new\n",
        encoding="utf-8",
    )

    assert main(["--repo-root", str(repo), "--check", "B"]) == 0
    out = capsys.readouterr().out
    assert "uncommitted_stream" in out
    assert "SUSPICIONS, NOT VERDICTS" in out

    assert main(["--repo-root", str(repo), "--check", "B", "--strict"]) == 2
    capsys.readouterr()

    assert main(["--repo-root", str(repo), "--check", "B", "--json"]) == 0
    assert '"disclaimer"' in capsys.readouterr().out


def test_whole_tree_mode_warns_that_it_is_advisory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    _report, warnings = run_audit(repo_root=repo, baseline=None, checks=("A",))

    assert any("WHOLE-TREE MODE" in w for w in warnings)


# --- the allowlist ---------------------------------------------------------


def _allowlist_toml(**overrides: str) -> str:
    fields = {
        "symbol": "gridq.sizing.underwrite.underwrite_spec",
        "check": "A",
        "reason": "Independent re-price, invoked by underwriting review rather than by a run.",
        "ref": "docs/decisions.md#d-45",
        "added": date.today().isoformat(),
        "author": "Olly",
        "expires": (date.today() + timedelta(days=60)).isoformat(),
    }
    fields.update(overrides)
    body = "\n".join(f'{k} = "{v}"' for k, v in fields.items())
    return f"[[dormant]]\n{body}\n"


def test_a_valid_allowlist_entry_suppresses_and_is_counted(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "decisions.md").write_text(
        "## D-45 derive and declare\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "dormant_code.toml").write_text(_allowlist_toml(), encoding="utf-8")

    entries, errors = load_allowlist(tmp_path / "docs" / "dormant_code.toml", repo_root=tmp_path)

    assert errors == []
    assert len(entries) == 1
    root = _tree(tmp_path / "t", {"underwrite.py": "def underwrite_spec():\n    return 1\n"})
    report = audit(root, checks=("A",), allowlist=entries)
    assert _keys(report, "A") == {("underwrite", "underwrite_spec", None)}  # module path differs

    entries = [
        AllowlistEntry(
            symbol="underwrite.underwrite_spec",
            check="A",
            reason=entries[0].reason,
            ref=entries[0].ref,
            added=entries[0].added,
            author="Olly",
            expires=entries[0].expires,
        )
    ]
    report = audit(root, checks=("A",), allowlist=entries)
    assert _keys(report, "A") == set()
    assert report.coverage.suppressed_allowlist == 1


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"ref": "docs/nope.md"}, "does not resolve"),
        ({"ref": "docs/decisions.md#not-in-the-file"}, "does not appear"),
        (
            {"expires": (date.today() + timedelta(days=200)).isoformat()},
            f"max {90}",
        ),
        ({"expires": (date.today() - timedelta(days=1)).isoformat()}, "expired"),
        ({"reason": ""}, "missing reason"),
    ],
)
def test_allowlist_hygiene_failures_are_reported_not_ignored(
    tmp_path: Path, overrides: dict[str, str], fragment: str
) -> None:
    """``ref`` must resolve and dormancy expires within 90 days. Without both, "# TODO wire
    later" becomes permanent and the file only ever grows."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "decisions.md").write_text(
        "## D-45 derive and declare\n", encoding="utf-8"
    )
    path = tmp_path / "docs" / "dormant_code.toml"
    path.write_text(_allowlist_toml(**overrides), encoding="utf-8")

    entries, errors = load_allowlist(path, repo_root=tmp_path)

    assert entries == []
    assert any(fragment in e for e in errors), errors


def test_stale_allowlist_entries_are_reported_only_in_whole_tree_mode() -> None:
    """A stale entry is a lie about the codebase — but diff-scoped, a dormant symbol that
    predates the baseline is out of scope and would look stale on every single run.

    The design did not anticipate that interaction. Rather than guess, the audit checks
    staleness only where the answer is meaningful and says which mode it is in.
    """
    entry = AllowlistEntry(
        symbol="gone.symbol",
        check="A",
        reason="r",
        ref="docs/decisions.md",
        added="2026-08-01",
        author="Olly",
        expires="2026-10-01",
    )

    whole_tree = Coverage()
    kept = apply_allowlist([], [entry], whole_tree, check_stale=True)
    assert [f.check for f in kept] == ["ALLOWLIST"]
    assert "stale entry" in kept[0].note
    assert whole_tree.staleness_checked is True

    diff_scoped = Coverage()
    assert apply_allowlist([], [entry], diff_scoped, check_stale=False) == []
    assert "NOT checked in diff-scoped mode" in "\n".join(diff_scoped.lines())


def test_a_missing_allowlist_file_is_an_empty_allowlist_and_is_stated(tmp_path: Path) -> None:
    entries, errors = load_allowlist(tmp_path / "docs" / "dormant_code.toml", repo_root=tmp_path)

    assert (entries, errors) == ([], [])
    root = _tree(tmp_path / "t", {"mod.py": "def orphan():\n    return 1\n"})
    report = audit(root, checks=("A",))
    assert "no entries" in report.coverage.allowlist_state


# --- coverage of the checks themselves -------------------------------------


def test_the_audit_never_examines_its_own_tooling_package(tmp_path: Path) -> None:
    """``dev/`` is excluded, reusing ``review_gates``' definition of "core" so the two dev
    checkers cannot drift on what they scan."""
    root = _tree(
        tmp_path,
        {
            "dev/tooling.py": "def never_flagged():\n    return 1\n",
            "core.py": "def flagged():\n    return 1\n",
        },
    )
    flagged = _keys(audit(root, checks=("A",)), "A")

    assert ("core", "flagged", None) in flagged
    assert not any(m.startswith("dev") for m, _, _ in flagged)


def test_a_check_that_is_not_run_is_named_in_the_coverage_block(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"mod.py": "def orphan():\n    return 1\n"})
    report = audit(root, checks=("A",))

    assert report.coverage.checks_run == ("A",)
    assert set(report.coverage.checks_skipped) == {"B", "C"}
    assert "checks NOT run ............... B, C" in "\n".join(report.coverage.lines())
