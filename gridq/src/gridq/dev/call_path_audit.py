"""Call-path audit — mechanical suspicion of code that landed but is never called.

**NOT part of the pricing path.** This module reads Python *source text* and reports where a
newly-added symbol, parameter or model field has no production call site. It computes no
number a lender relies on.

Run it::

    python -m gridq.dev.call_path_audit                 # diff-scoped vs HEAD (the default)
    python -m gridq.dev.call_path_audit --baseline abc123
    python -m gridq.dev.call_path_audit --no-baseline   # whole-tree advisory sweep, very noisy
    python -m gridq.dev.call_path_audit --json

Why it exists
-------------
This repo has shipped the same defect eight times: code written, tested, merged, and never
called on any production path. Every one of the eight had a passing test. What none had was a
test that would notice the *call site* disappearing, because the tests called the new function
directly. Manual inspection missed it every time. That is the argument for a mechanical check.

What this proves, and what it does not
--------------------------------------
**The audit proves that a call path exists. It never proves that a value flows.** That
distinction is not a caveat in a footnote, it is the operating manual:

* Historical instance 4 — the availability compiler — *had* a call path. ``produce_gate_context``
  reached ``commitments_from_constraints`` on every run, and the subsystem was still inert,
  because ``DispatchConstraintDescriptor.parameters`` was never populated at any construction
  site: the compiler was handed ``None`` forever and always returned ``[]``. This audit catches
  that one only by luck of shape — the population channel happened to be a keyword argument,
  which is exactly check B's subject. Had the field been populated through a dict, a config
  flag, or a list that is always empty, every check here would have reported green.
* The eighth instance — the bill-reconciliation gate, 2026-08-15 — is wired to a real production
  call site, has four passing tests, and survives a mutation check. It is inert on all six real
  sites because its precondition is never met. This audit reports it green: correctly, and
  uselessly.

So reachability is a **necessary** condition this repo was repeatedly failing, and a
**sufficient** condition for nothing. A green run here is not evidence that work landed. The
evidence that work landed is the one named in ``docs/validation_requirements.md`` §3: the
production call site (``file:line``), *plus* the test that goes red when that call site is
deleted. An implementer who reads a green audit as "the wiring is proven" has been misled by
this tool — which is why the limit is stated at the top of the module and repeated in the
footer of every report it prints.

The other half of the honest framing: this audit **reports suspicions, not verdicts**. Measured
against the tree it was calibrated on, roughly a quarter of its findings were false positives —
concentrated entirely in check B, and every one of them had a legitimate documented reason
already written in the source. A finding means "open this file and name the production call
site". It does not mean "this is a defect".

The three checks
----------------
=====  ===================================================================================
Check  Question it asks
=====  ===================================================================================
B      Does any production call site supply this defaulted parameter / pydantic model
       field? (instances 3, 4 and 6 were all exactly this shape)
A      Is this public symbol referenced anywhere in ``src/`` outside its own module?
       (instance 1)
C      Is this public symbol reachable, by name-resolved call edges, from a real entry
       point? Advisory — noisier than A, adds context rather than new detections.
=====  ===================================================================================

Diff-scoping is mandatory, not an optimisation
----------------------------------------------
A default that has existed for six months and is never overridden is a design choice; one added
in this branch and never passed is a suspicion. Whole-tree mode produces hundreds of findings
and would be switched off within a week, so the default is diff-scoped against the last
reviewed ref (``HEAD``). ``--baseline merge-base`` is available and prints a warning: on a long
branch it produces proportionally more findings.

Diff-scoping has one structural cost that must be stated, because it is the mechanism by which
this failure mode could re-hide itself: **a real finding that is merged unfixed becomes part of
every future baseline and is invisible from then on.** Two consequences, from the design:

1. Diff-scoped findings are resolved (wired to a production call site) or allowlisted in
   ``docs/dormant_code.toml`` *before* the merge. There is no "merge now, fix later" — later is
   unreachable. Use ``--strict`` to make findings a non-zero exit for that gate.
2. A periodic whole-tree advisory sweep (``--no-baseline``, warn-only, not on the commit path)
   is what surfaces anything that slipped in before adoption or through a mis-set baseline.

Implementation constraints this module holds to
------------------------------------------------
* **Pure** :func:`ast.parse` **over file text — it never imports the code it inspects.** Importing
  executes module-level code, which would make the audit's result depend on import side effects.
* **Historical trees come from** ``git archive`` **into a temporary directory** — never
  ``git worktree``, never ``git stash``. The audit never writes to the working tree, so it is
  safe to run beside a live test suite.
* **No silent caps.** Every file skipped, finding suppressed, or check not run is counted and
  named in the COVERAGE block of the report. A tool that quietly limits its own coverage reads
  as "I checked everything" when it did not — which is precisely the class of failure it exists
  to catch.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Reused, not redefined, so the two dev-facing checkers cannot drift on what "core" means.
# (Import-only: `review_gates` has no module-level side effects.)
from gridq.dev.review_gates import _NON_CORE_PACKAGES

# --- configuration ---------------------------------------------------------

#: Real production entry points. Every top-level def in these modules is a BFS root for check C.
ENTRY_MODULES: tuple[str, ...] = ("cli.py", "app/app.py")

#: Functions that are entry points in their own right, wherever they live.
ENTRY_FUNCS: frozenset[str] = frozenset({"generate_quote"})

#: Bases whose annotated-with-default class attributes are keyword parameters of a generated
#: ``__init__``. A model field *is* a keyword parameter, so check B treats them identically —
#: that is what converts historical instance 4 from "not caught" to "caught".
PYDANTIC_BASES: frozenset[str] = frozenset({"GridQModel", "BaseModel"})

#: Modules whose own signature is a public API surface: their callers live outside ``src/``, so
#: "nobody passes this" says nothing. Check B skips them.
CHECK_B_EXEMPT_PREFIXES: tuple[str, ...] = ("cli", "app.")

#: The last reviewed ref. Measured: ``merge-base`` on a 26-commit branch produced 77 findings
#: against 24 for ``HEAD`` — a volume that gets a gate switched off. See the module docstring.
DEFAULT_BASELINE = "HEAD"

#: Repo-relative path of the deliberate-dormancy allowlist (see :func:`load_allowlist`).
ALLOWLIST_PATH = "docs/dormant_code.toml"

#: An allowlist entry may not defer a decision by more than this. Dormancy is re-argued, not
#: inherited.
MAX_DORMANT_DAYS = 90

_CHECK_ORDER: dict[str, int] = {"ALLOWLIST": 0, "B": 1, "A": 2, "C": 3}

_CHECK_TITLES: dict[str, str] = {
    "B": "a defaulted parameter / model field that no production call site supplies",
    "A": "a public symbol referenced nowhere else in src/",
    "C": "a public symbol not reachable from any entry point  [advisory]",
    "ALLOWLIST": "docs/dormant_code.toml hygiene",
}

_REPO_ROOT = Path(__file__).resolve().parents[3]


class BaselineUnavailableError(RuntimeError):
    """Raised when a requested baseline ref cannot be resolved or extracted.

    Never swallowed into a silent fall-back to whole-tree mode: a diff-scoped run that
    quietly became a whole-tree run would report hundreds of findings, and a whole-tree run
    that quietly became diff-scoped would report almost none. Both misread as facts about
    the code.
    """


# --- AST facts -------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """One defaulted parameter or model field that a caller *could* supply."""

    module: str
    qualname: str
    name: str
    lineno: int
    kind: str  # "func" | "model_field"
    pos_index: int = -1
    is_method: bool = False
    #: Defined inside another *function* (a closure), so its call sites cannot be resolved by
    #: name: callers reach it through the decorated or returned object. See check B.
    in_closure: bool = False


@dataclass
class FileFacts:
    """Everything one parsed file contributes. Names only — no values, no imports resolved."""

    module: str
    path: Path
    called_names: set[str] = field(default_factory=set)
    kwargs_by_target: dict[str, set[str]] = field(default_factory=dict)
    splat_targets: set[str] = field(default_factory=set)
    max_pos_args: dict[str, int] = field(default_factory=dict)
    referenced_names: set[str] = field(default_factory=set)
    duck_names: set[str] = field(default_factory=set)
    dict_field_keys: set[str] = field(default_factory=set)
    params: list[ParamSpec] = field(default_factory=list)
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    defs: dict[str, int] = field(default_factory=dict)
    class_bases: dict[str, list[str]] = field(default_factory=dict)
    is_entry: bool = False


class _Visitor(ast.NodeVisitor):
    """Collect :class:`FileFacts` from one module's AST.

    The resolution rules below are settled *here* so no caller re-decides them:

    ==========================================  =================================================
    Situation                                   Rule
    ==========================================  =================================================
    ``x.m(...)``                                resolves by **name** to every ``src/`` definition
                                                of ``m``. One rule transparently covers the
                                                ``OptimisationEngine`` Protocol seam, dependency
                                                injection and the stream registry. It
                                                over-approximates reachability, which is the
                                                correct bias for a tool that must not cry wolf.
    ``getattr/hasattr/setattr(o, "n")``         a string-literal second argument is a reference
                                                *and* a call edge to ``n`` — no allowlist needed
    decorators, class bases, keyword bases      count as references (omitting bases was a real
                                                false-positive source)
    signature annotations and defaults          count as references (see "fix 7" below)
    ``__all__`` / ``__init__.py`` re-exports    do **not** count as references
    ``f(**d)`` / ``f(*a)``                      target marked "arity unknown"; its params are
                                                never flagged — no guessing through splats
    positional passing                          a defaulted param at index *i* is satisfied by any
                                                call passing more than *i* positional arguments
    bound method / ``__init__``                 subtract 1 for the absent ``self``
    ``model_copy(update={...})``                string keys of the dict literal populate fields
    ==========================================  =================================================

    Six bugs in the original prototype were found by *running* it, not by reasoning about it,
    and each fix is load-bearing. Do not "simplify" any of them away:

    1. Kwargs are keyed **per resolved target**, never as one global set of names passed
       anywhere. A pass-through such as ``contract_tenor_years=contract_tenor_years`` in an
       unrelated module otherwise marks the name satisfied everywhere — a **false negative**,
       the worst kind, and it is what made the prototype miss two of the historical instances.
    2. Positional passing is counted (:attr:`FileFacts.max_pos_args`).
    3. Bound methods drop ``self`` when comparing positional arity.
    4. Class bases and decorators are references.
    5. Annotation-only use is a reference (``referenced_names``, not just ``called_names``).
    6. ``model_copy(update={...})`` / ``model_validate({...})`` dict keys populate fields.

    Fix 7 is this module's own, measured after the design was written: **function-signature
    annotations and default expressions are visited too.** The prototype walked only
    ``node.body``, so a type used exclusively in signatures looked unreferenced. Measured on
    this tree it removed 8 of 51 whole-tree check-A findings — ``DispatchSolver``,
    ``ResultLike``, ``CashflowProvider``, ``BatteryModel``, ``SizingDomain``, ``Evaluation``,
    ``ProseDrafter`` and ``ExportFeasibilityOverride``, all genuine type-only uses — and changed
    nothing diff-scoped. The one real finding it demotes (``ExportFeasibilityOverride``, which
    nothing constructs) is still reported by check B on its ``__init__`` parameter, so no
    detection is lost.

    Fixes 8 and 9 are also this module's own and are described where check C uses them: a
    closure is reached with its enclosing scope, and a reached class reaches its base classes.
    """

    def __init__(self, facts: FileFacts) -> None:
        self.f = facts
        self.stack: list[str] = []
        self.func_depth = 0

    def _qual(self, name: str) -> str:
        return ".".join([*self.stack, name])

    def _record_ref(self, node: ast.AST) -> None:
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                self.f.referenced_names.add(n.id)
            elif isinstance(n, ast.Attribute):
                self.f.referenced_names.add(n.attr)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qn = self._qual(node.name)
        self.f.defs[qn] = node.lineno
        bases: list[str] = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                bases.append(b.id)
            elif isinstance(b, ast.Attribute):
                bases.append(b.attr)
        self.f.class_bases[qn] = bases
        for b in node.bases:  # fix 4: a base class is a use
            self._record_ref(b)
        for d in node.decorator_list:
            self._record_ref(d)
        for kw in node.keywords:
            self._record_ref(kw.value)
        if PYDANTIC_BASES & set(bases):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.value is not None
                    and not stmt.target.id.startswith("_")
                ):
                    self.f.params.append(
                        ParamSpec(self.f.module, qn, stmt.target.id, stmt.lineno, "model_field")
                    )
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qn = self._qual(node.name)
        self.f.defs[qn] = node.lineno
        for d in node.decorator_list:
            self._record_ref(d)
        a = node.args
        n_defaults = len(a.defaults)
        defaulted_pos = a.args[len(a.args) - n_defaults :] if n_defaults else []
        kwonly = [p for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None]
        all_pos = [*a.posonlyargs, *a.args]
        is_method = "." in qn
        in_closure = self.func_depth > 0
        for p in defaulted_pos:
            if p.arg in {"self", "cls"} or p.arg.startswith("_"):
                continue
            idx = all_pos.index(p) if p in all_pos else -1
            self.f.params.append(
                ParamSpec(self.f.module, qn, p.arg, node.lineno, "func", idx, is_method, in_closure)
            )
        for p in kwonly:
            if p.arg in {"self", "cls"} or p.arg.startswith("_"):
                continue
            self.f.params.append(
                ParamSpec(self.f.module, qn, p.arg, node.lineno, "func", -1, is_method, in_closure)
            )
        # fix 7: signature annotations and default expressions are uses of the names in them.
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
            if arg is not None and arg.annotation is not None:
                self._record_ref(arg.annotation)
        if node.returns is not None:
            self._record_ref(node.returns)
        for default in [*a.defaults, *(d for d in a.kw_defaults if d is not None)]:
            self._record_ref(default)
        self.stack.append(node.name)
        self.func_depth += 1
        for child in node.body:
            self.visit(child)
        self.func_depth -= 1
        self.stack.pop()

    visit_FunctionDef = _visit_func  # type: ignore[assignment]
    visit_AsyncFunctionDef = _visit_func  # type: ignore[assignment]

    def visit_Name(self, node: ast.Name) -> None:
        self.f.referenced_names.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.f.referenced_names.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        target: str | None = None
        if isinstance(fn, ast.Name):
            target = fn.id
        elif isinstance(fn, ast.Attribute):
            target = fn.attr
        if target:
            self.f.called_names.add(target)
            caller = ".".join(self.stack) if self.stack else "<module>"
            self.f.edges[caller].add(target)
            n_pos = len(node.args)
            if any(isinstance(x, ast.Starred) for x in node.args):
                self.f.splat_targets.add(target)  # *args: arity unknowable
            if n_pos > self.f.max_pos_args.get(target, 0):  # fix 2
                self.f.max_pos_args[target] = n_pos
            if target in {"model_copy", "model_validate", "model_construct"}:  # fix 6
                sources: list[ast.expr] = [
                    *node.args,
                    *(k.value for k in node.keywords if k.arg == "update"),
                ]
                for src in sources:
                    if isinstance(src, ast.Dict):
                        for key in src.keys:
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                self.f.dict_field_keys.add(key.value)
            if target in {"getattr", "hasattr", "setattr"} and len(node.args) >= 2:
                lit = node.args[1]
                if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                    self.f.duck_names.add(lit.value)
                    self.f.called_names.add(lit.value)
                    self.f.edges[caller].add(lit.value)
        for kw in node.keywords:
            if kw.arg is None:
                if target:
                    self.f.splat_targets.add(target)
            elif target:  # fix 1: PER TARGET, never a global set of kwarg names
                self.f.kwargs_by_target.setdefault(target, set()).add(kw.arg)
        self.generic_visit(node)


# --- parsing ---------------------------------------------------------------


@dataclass
class ParseResult:
    """Parsed facts plus an explicit account of what could **not** be parsed."""

    facts: dict[str, FileFacts]
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def parsed(self) -> int:
        return len(self.facts)


def iter_core_files(tree: Path) -> list[Path]:
    """Return the ``*.py`` files of the *core* packages under ``<tree>/src/gridq``.

    Excludes ``__pycache__`` and the non-core (tooling) packages, so the audit never audits
    itself.
    """
    src = tree / "src" / "gridq"
    if not src.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src)
        if "__pycache__" in rel.parts:
            continue
        if rel.parts and rel.parts[0] in _NON_CORE_PACKAGES:
            continue
        out.append(p)
    return out


def _module_name(path: Path, src_root: Path) -> str:
    return path.relative_to(src_root).as_posix().replace("/", ".").removesuffix(".py")


def _parse(path: Path) -> ast.Module | str:
    """Parse ``path``; return the module, or a human-readable reason it could not be read."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return f"syntax error at line {exc.lineno}"
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"


def analyse_core(tree: Path) -> ParseResult:
    """Parse every core module under ``<tree>/src/gridq``. Never imports it."""
    src_root = tree / "src" / "gridq"
    result = ParseResult(facts={})
    for p in iter_core_files(tree):
        module = _parse(p)
        if isinstance(module, str):
            result.skipped.append((p.relative_to(tree).as_posix(), module))
            continue
        f = FileFacts(module=_module_name(p, src_root), path=p)
        _Visitor(f).visit(module)
        f.is_entry = p.relative_to(src_root).as_posix() in ENTRY_MODULES
        result.facts[f.module] = f
    return result


def analyse_tests(tests_root: Path) -> tuple[FileFacts, int, list[tuple[str, str]]]:
    """Aggregate the test tree into one :class:`FileFacts`.

    Tests are *not* production. Their only role is the ``seen_in_tests`` flag, which is the
    single best discriminator in the whole audit: "there is a test, so it looks landed, and
    production never touches it" is the failure mode stated verbatim.
    """
    agg = FileFacts(module="<tests>", path=tests_root)
    parsed = 0
    skipped: list[tuple[str, str]] = []
    if not tests_root.is_dir():
        return agg, parsed, skipped
    for p in sorted(tests_root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        module = _parse(p)
        if isinstance(module, str):
            skipped.append((p.name, module))
            continue
        f = FileFacts(module="<test>", path=p)
        _Visitor(f).visit(module)
        parsed += 1
        agg.called_names |= f.called_names
        agg.referenced_names |= f.referenced_names
        agg.dict_field_keys |= f.dict_field_keys
        agg.splat_targets |= f.splat_targets
        for t, ks in f.kwargs_by_target.items():
            agg.kwargs_by_target.setdefault(t, set()).update(ks)
    return agg, parsed, skipped


# --- findings --------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One suspicion. Not a verdict — see the module docstring."""

    check: str  # "A" | "B" | "C" | "ALLOWLIST"
    module: str
    qualname: str
    lineno: int
    kind: str  # "func" | "model_field" | "symbol" | "allowlist"
    param: str | None = None
    seen_in_tests: bool = False
    note: str = ""

    @property
    def sort_key(self) -> tuple[int, str, str, str, int]:
        return (
            _CHECK_ORDER.get(self.check, 9),
            self.module,
            self.qualname,
            self.param or "",
            self.lineno,
        )

    def location(self) -> str:
        where = f"{self.module}:{self.lineno} {self.qualname}"
        return f"{where}({self.param}=)" if self.param else where

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "module": self.module,
            "qualname": self.qualname,
            "lineno": self.lineno,
            "kind": self.kind,
            "param": self.param,
            "seen_in_tests": self.seen_in_tests,
            "note": self.note,
        }


@dataclass
class Coverage:
    """What the audit examined and — the point of this class — what it did **not**.

    Every number here is printed. A suppression that is not counted is a silent cap, and a
    tool with silent caps reads as "I checked everything" when it did not.
    """

    tree: str = ""
    core_modules_parsed: int = 0
    core_files_skipped: list[tuple[str, str]] = field(default_factory=list)
    test_files_parsed: int = 0
    test_files_skipped: list[tuple[str, str]] = field(default_factory=list)
    tests_root: str = ""
    tests_root_found: bool = False
    baseline_label: str = ""
    baseline_modules_parsed: int = 0
    baseline_files_skipped: list[tuple[str, str]] = field(default_factory=list)
    checks_run: tuple[str, ...] = ()
    checks_skipped: tuple[str, ...] = ()
    arity_unknown_targets: int = 0
    suppressed_pre_existing: int = 0
    suppressed_dedup: int = 0
    suppressed_entry_func: int = 0
    suppressed_closures: int = 0
    suppressed_allowlist: int = 0
    allowlist_state: str = ""
    staleness_checked: bool = False
    display_limit: int | None = None
    display_hidden: int = 0

    def lines(self) -> list[str]:
        """The COVERAGE block, in report order."""
        out = [
            "COVERAGE — what was examined, and what was not",
            f"  tree ......................... {self.tree}",
            f"  core modules parsed .......... {self.core_modules_parsed}",
            f"  core files SKIPPED ........... {len(self.core_files_skipped)}",
        ]
        out += [f"      ! {p} — {why}" for p, why in self.core_files_skipped]
        if self.tests_root_found:
            out.append(f"  test files parsed ............ {self.test_files_parsed}")
        else:
            out.append(
                f"  test files parsed ............ 0  ! no test tree at {self.tests_root} — "
                "every 'seen in tests' flag below reads false for that reason, not because "
                "the symbol is untested"
            )
        out += [f"      ! tests/{p} — {why}" for p, why in self.test_files_skipped]
        out += [
            f"  baseline ..................... {self.baseline_label}",
            f"  baseline modules parsed ...... {self.baseline_modules_parsed}",
        ]
        if self.baseline_files_skipped:
            out.append(f"  baseline files SKIPPED ....... {len(self.baseline_files_skipped)}")
            out += [f"      ! {p} — {why}" for p, why in self.baseline_files_skipped]
        out += [
            f"  checks run ................... {', '.join(self.checks_run) or 'none'}",
            f"  checks NOT run ............... {', '.join(self.checks_skipped) or 'none'}",
            "  suppressed (each is a finding you are NOT being shown):",
            f"      pre-existing in baseline . {self.suppressed_pre_existing}",
            f"      arity unknown (*/**) ..... {self.arity_unknown_targets} call target(s); "
            "their parameters can never be flagged",
            f"      deduped into A/C ......... {self.suppressed_dedup} parameter(s) of "
            "functions already reported as unreachable",
            f"      entry-point signatures ... {self.suppressed_entry_func} parameter(s) of "
            "declared entry points, whose callers live outside src/",
            f"      closure parameters ....... {self.suppressed_closures} parameter(s) of "
            "functions nested inside functions; their callers cannot be resolved by name",
            f"      allowlisted .............. {self.suppressed_allowlist}",
            f"  allowlist .................... {self.allowlist_state}",
            "  allowlist staleness .......... "
            + (
                "checked"
                if self.staleness_checked
                else "NOT checked in diff-scoped mode — a dormant symbol that predates the "
                "baseline is out of scope, so it would look stale on every run. Run "
                "--no-baseline to check it."
            ),
        ]
        if self.display_limit is not None:
            out.append(
                f"  display limit ................ {self.display_limit} "
                f"({self.display_hidden} finding(s) hidden from this listing — use --json)"
            )
        return out


@dataclass
class AuditReport:
    """The audit's whole output: the findings, and the account of its own coverage."""

    findings: list[Finding] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    elapsed_s: float = 0.0

    def of(self, check: str) -> list[Finding]:
        return [f for f in self.findings if f.check == check]

    def to_dict(self) -> dict[str, object]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "coverage": {k: v for k, v in vars(self.coverage).items() if not k.startswith("_")},
            "elapsed_s": round(self.elapsed_s, 3),
            "disclaimer": (
                "Suspicions, not verdicts. A call path existing is not a value flowing; see "
                "gridq.dev.call_path_audit.__doc__."
            ),
        }

    def summary(self, *, max_display: int | None = None) -> str:
        lines = [
            "GRIDQ — CALL-PATH AUDIT",
            "SUSPICIONS, NOT VERDICTS — each finding is a prompt to name a production call site",
            "=" * 88,
        ]
        shown = 0
        hidden = 0
        for check in sorted({f.check for f in self.findings}, key=lambda c: _CHECK_ORDER.get(c, 9)):
            hits = self.of(check)
            lines.append("")
            lines.append(f"CHECK {check} — {_CHECK_TITLES.get(check, '')}  ({len(hits)})")
            for f in hits:
                if max_display is not None and shown >= max_display:
                    hidden += 1
                    continue
                shown += 1
                flag = "[also in tests]" if f.seen_in_tests else "[not in tests]"
                suffix = f"  — {f.note}" if f.note else ""
                lines.append(f"  {flag:15} {f.location()}  <{f.kind}>{suffix}")
        if not self.findings:
            lines.append("")
            lines.append("No suspicions in scope. That is not proof that anything is wired.")
        self.coverage.display_limit = max_display
        self.coverage.display_hidden = hidden
        lines += ["", *self.coverage.lines()]
        lines += [
            "",
            "HOW TO READ THIS",
            "  A finding means: open the file and name the production call site, or record the",
            "  dormancy in docs/dormant_code.toml. It does NOT mean a defect is proven. Measured",
            "  on the tree this audit was calibrated against, ~25% of findings (6 of 24, all in",
            "  check B) were false positives with a legitimate reason already in the source.",
            "  '[also in tests]' is the strongest signal: tested, and never exercised in",
            "  production, is this repo's failure mode stated verbatim.",
            "",
            "  THE LIMIT: this proves a CALL PATH EXISTS. It never proves a VALUE FLOWS. Two of",
            "  the eight historical instances had a call path and were inert anyway — one because",
            "  nothing populated the field, one because the precondition is never met on a real",
            "  site. A clean run here is necessary, and sufficient for nothing. The evidence that",
            "  work landed is still: the production call site, plus the test that goes red when",
            "  that call site is deleted.",
            f"  ({len(self.findings)} finding(s) in {self.elapsed_s:.2f}s)",
        ]
        return "\n".join(lines)


# --- the checks ------------------------------------------------------------


def check_b(
    facts: dict[str, FileFacts],
    tests: FileFacts,
    baseline_params: set[tuple[str, str, str]] | None,
    coverage: Coverage,
) -> list[Finding]:
    """Defaulted parameters and model fields that no production call site supplies.

    The highest-value and cheapest check: three of the six historical instances measured
    against were exactly this shape, including the one where "is the compiler called?" reports
    green.
    """
    all_splat: set[str] = set()
    max_pos: dict[str, int] = {}
    kw_by_target: dict[str, set[str]] = {}
    dict_keys: set[str] = set()
    for f in facts.values():
        dict_keys |= f.dict_field_keys
        all_splat |= f.splat_targets
        for name, n in f.max_pos_args.items():
            if n > max_pos.get(name, 0):
                max_pos[name] = n
        for t, ks in f.kwargs_by_target.items():
            kw_by_target.setdefault(t, set()).update(ks)
    coverage.arity_unknown_targets = len(all_splat)

    hits: list[Finding] = []
    for f in facts.values():
        if f.module.startswith(CHECK_B_EXEMPT_PREFIXES):
            continue
        for ps in f.params:
            if baseline_params is not None and (ps.module, ps.qualname, ps.name) in baseline_params:
                coverage.suppressed_pre_existing += 1
                continue  # pre-existing: a long-standing default is a design choice
            short = ps.qualname.split(".")[-1]
            if ps.kind == "model_field":
                # a model field is supplied at `ClassName(...)`; qualname *is* the class
                tname = short
            elif short == "__init__":
                tname = ps.qualname.split(".")[0]
            else:
                tname = short
            # fix 1 — per-target resolution. A global set of kwarg names is a FALSE NEGATIVE.
            if ps.name in kw_by_target.get(tname, set()):
                continue
            if ps.kind == "model_field" and ps.name in dict_keys:
                continue  # fix 6 — model_copy(update={...}) / model_validate({...})
            if tname in all_splat:
                continue  # arity unknowable through a splat; counted in COVERAGE
            if ps.pos_index >= 0:
                offset = 1 if (ps.is_method or short == "__init__") else 0  # fix 3 — self
                if max_pos.get(tname, 0) > ps.pos_index - offset:
                    continue  # fix 2 — supplied positionally somewhere
            if ps.in_closure:
                # fix 8 — a closure's callers reach it through the decorated or returned
                # object, never by its own name, so "nobody passes it" is unknowable here.
                # Measured: the `_guard_check_gates._guarded` wrapper produced exactly this.
                coverage.suppressed_closures += 1
                continue
            if ps.qualname in ENTRY_FUNCS:
                # A declared entry point's own kwargs are its public API; its callers are
                # outside src/, so "nobody passes it" is not evidence of anything.
                coverage.suppressed_entry_func += 1
                continue
            hits.append(
                Finding(
                    check="B",
                    module=ps.module,
                    qualname=ps.qualname,
                    lineno=ps.lineno,
                    kind=ps.kind,
                    param=ps.name,
                    seen_in_tests=ps.name in tests.kwargs_by_target.get(tname, set()),
                )
            )
    return hits


def _is_reexport_module(mod: str) -> bool:
    """``__init__`` modules re-export; a re-export is not a use."""
    return mod == "__init__" or mod.endswith(".__init__")


def _public_defs(facts: dict[str, FileFacts]) -> Iterable[tuple[str, FileFacts, str, int]]:
    for mod, f in facts.items():
        if _is_reexport_module(mod):
            continue
        for qn, lineno in f.defs.items():
            if "." in qn or qn.startswith("_"):
                continue  # nested/methods are covered via their owner; privates are internal
            yield mod, f, qn, lineno


def check_a(
    facts: dict[str, FileFacts],
    tests: FileFacts,
    baseline_defs: set[tuple[str, str]] | None,
    coverage: Coverage,
) -> list[Finding]:
    """Public symbols referenced nowhere in ``src/`` outside their own module."""
    hits: list[Finding] = []
    for mod, f, qn, lineno in _public_defs(facts):
        if baseline_defs is not None and (mod, qn) in baseline_defs:
            coverage.suppressed_pre_existing += 1
            continue
        external = any(
            qn in (of.referenced_names | of.called_names)
            for other_mod, of in facts.items()
            if other_mod != mod and not _is_reexport_module(other_mod)
        )
        if external:
            continue
        if qn in (f.called_names | f.duck_names | f.referenced_names):
            continue  # fix 5 — internal use includes annotation-only and base-class use
        hits.append(
            Finding(
                check="A",
                module=mod,
                qualname=qn,
                lineno=lineno,
                kind="symbol",
                seen_in_tests=qn in (tests.referenced_names | tests.called_names),
            )
        )
    return hits


def check_c(
    facts: dict[str, FileFacts],
    tests: FileFacts,
    baseline_defs: set[tuple[str, str]] | None,
    coverage: Coverage,
) -> list[Finding]:
    """Public symbols not reached by a BFS over name-resolved call edges from entry points.

    The roots are deliberately over-approximated (every top-level def of an entry module, and
    a module's ``<module>`` scope once the module is reached) so that the bias is "only flag
    what is *certainly* unreachable". A guard that cries wolf gets disabled.

    **Closures are reached with their enclosing scope** — fix 8, this module's own, measured
    after the design was written. A decorator that wraps a method in a nested ``_guarded``
    function makes every call *inside* that closure invisible to a by-name BFS: the closure is
    defined and returned, never called by its own name. Measured on this tree, one such wrapper
    in ``value_stack/streams.py`` made a four-function cluster in ``value_stack/gates.py`` look
    unreachable when the wrapper calls it on every gate check — four false positives from one
    decorator. Treating a nested def as reached when its owner is reached is the same
    over-approximating bias as the root set.

    **A reached class reaches its base classes** — fix 9, which is what fix 8 alone could not
    do. Reaching that same gates cluster requires ``BaseValueStream.__init_subclass__``, an
    *implicit interpreter hook*: nothing calls it by name, ever, so no by-name walk can find
    it. It becomes reachable only when a constructed subclass drags its bases along — which is
    independently just true of inheritance, since a subclass's instances answer to every method
    its bases define.
    """
    defs_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    nested: dict[tuple[str, str], list[str]] = defaultdict(list)
    bases_of: dict[tuple[str, str], list[str]] = {}
    for mod, f in facts.items():
        for qn in f.defs:
            defs_by_name[qn.split(".")[-1]].append((mod, qn))
            owner, _, _leaf = qn.rpartition(".")
            if owner:
                nested[(mod, owner)].append(qn)
        for qn, base_names in f.class_bases.items():
            bases_of[(mod, qn)] = base_names
    out_edges: dict[tuple[str, str], set[str]] = {}
    for mod, f in facts.items():
        for caller, targets in f.edges.items():
            out_edges[(mod, caller)] = set(targets)

    roots: list[tuple[str, str]] = []
    for mod, f in facts.items():
        if f.is_entry:
            roots.append((mod, "<module>"))
            roots.extend((mod, qn) for qn in f.defs)
        roots.extend((mod, qn) for qn in f.defs if qn in ENTRY_FUNCS)

    seen: set[tuple[str, str]] = set()
    queue = list(roots)
    reached_modules = {m for m, _ in roots}
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        seen.add(cur)
        mod = cur[0]
        if mod not in reached_modules:
            reached_modules.add(mod)
            if (mod, "<module>") not in seen:
                queue.append((mod, "<module>"))
        for name in out_edges.get(cur, ()):
            for tgt in defs_by_name.get(name, ()):
                if tgt not in seen:
                    queue.append(tgt)
        for child in nested.get(cur, ()):  # fix 8 — a closure runs when its owner does
            if (mod, child) not in seen:
                queue.append((mod, child))
        for base in bases_of.get(cur, ()):  # fix 9 — a subclass answers for its bases
            for tgt in defs_by_name.get(base, ()):
                if tgt not in seen:
                    queue.append(tgt)

    hits: list[Finding] = []
    for mod, _f, qn, lineno in _public_defs(facts):
        if baseline_defs is not None and (mod, qn) in baseline_defs:
            coverage.suppressed_pre_existing += 1
            continue
        if (mod, qn) in seen:
            continue
        hits.append(
            Finding(
                check="C",
                module=mod,
                qualname=qn,
                lineno=lineno,
                kind="symbol",
                seen_in_tests=qn in (tests.referenced_names | tests.called_names),
            )
        )
    return hits


def dedup(findings: Sequence[Finding], coverage: Coverage) -> list[Finding]:
    """Drop check-B parameters of functions already reported unreachable by A or C.

    If a function has no production call path at all, its individual parameters are not
    separate news. Without this the largest single subsystem finding emits five redundant
    parameter hits and buries the one that matters.
    """
    # A and C only ever report top-level defs, so the owning symbol of any B finding is the
    # first segment of its qualname (`Foo.bar` -> `Foo`; a bare function is its own owner).
    unreachable = {(f.module, f.qualname) for f in findings if f.check in {"A", "C"}}
    kept: list[Finding] = []
    for f in findings:
        if f.check == "B" and (f.module, f.qualname.split(".")[0]) in unreachable:
            coverage.suppressed_dedup += 1
            continue
        kept.append(f)
    return kept


# --- the allowlist ---------------------------------------------------------


@dataclass(frozen=True)
class AllowlistEntry:
    """One deliberately-dormant symbol, declared in ``docs/dormant_code.toml``.

    A guard that cannot say "deliberately dormant, and here is why" gets switched off within a
    month. Four properties make the file survive rather than only grow: ``ref`` is required and
    must resolve; a stale entry fails the check; ``expires`` is capped at 90 days; and the count
    is ratcheted by the caller.
    """

    symbol: str
    check: str
    reason: str
    ref: str
    added: str
    author: str
    expires: str
    param: str | None = None
    prefix: bool = False

    def matches(self, finding: Finding) -> bool:
        if self.check != finding.check:
            return False
        if self.param is not None and self.param != finding.param:
            return False
        target = f"{finding.module}.{finding.qualname}"
        symbol = self.symbol.removeprefix("gridq.")
        return target.startswith(symbol) if self.prefix else target == symbol


_ALLOWLIST_REQUIRED = ("symbol", "check", "reason", "ref", "added", "author", "expires")


def load_allowlist(
    path: Path, *, repo_root: Path, today: date | None = None
) -> tuple[list[AllowlistEntry], list[str]]:
    """Load and validate ``docs/dormant_code.toml``.

    Returns ``(entries, errors)``. Validation errors are returned, never swallowed: an entry
    with an unresolvable ``ref`` or an over-long ``expires`` is how "# TODO wire later" becomes
    permanent, so it is reported as its own finding.
    """
    today = today or date.today()
    if not path.is_file():
        return [], []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        return [], [f"{path.name} could not be read: {exc}"]

    entries: list[AllowlistEntry] = []
    errors: list[str] = []
    for i, item in enumerate(raw.get("dormant", []), start=1):
        if not isinstance(item, dict):
            errors.append(f"entry {i}: not a table")
            continue
        missing = [k for k in _ALLOWLIST_REQUIRED if not item.get(k)]
        if missing:
            errors.append(f"entry {i} ({item.get('symbol', '?')}): missing {', '.join(missing)}")
            continue
        label = str(item["symbol"])
        if str(item["check"]) not in _CHECK_TITLES:
            errors.append(f"{label}: check must be one of A, B, C")
            continue
        try:
            added = date.fromisoformat(str(item["added"]))
            expires = date.fromisoformat(str(item["expires"]))
        except ValueError as exc:
            errors.append(f"{label}: bad date — {exc}")
            continue
        if expires < today:
            errors.append(f"{label}: expired {expires.isoformat()} — dormancy must be re-argued")
            continue
        if expires - added > timedelta(days=MAX_DORMANT_DAYS):
            errors.append(
                f"{label}: expires {(expires - added).days} days after it was added "
                f"(max {MAX_DORMANT_DAYS})"
            )
            continue
        ref_error = _ref_error(str(item["ref"]), repo_root=repo_root)
        if ref_error:
            errors.append(f"{label}: {ref_error}")
            continue
        entries.append(
            AllowlistEntry(
                symbol=label,
                check=str(item["check"]),
                reason=str(item["reason"]),
                ref=str(item["ref"]),
                added=str(item["added"]),
                author=str(item["author"]),
                expires=str(item["expires"]),
                param=str(item["param"]) if item.get("param") else None,
                prefix=bool(item.get("prefix", False)),
            )
        )
    return entries, errors


def _ref_error(ref: str, *, repo_root: Path) -> str | None:
    """``ref`` must point at a file that exists, and any ``#anchor`` must appear in it."""
    file_part, _, anchor = ref.partition("#")
    target = repo_root / file_part
    if not target.is_file():
        return f"ref {ref!r} does not resolve — {file_part} not found"
    if not anchor:
        return None
    try:
        text = target.read_text(encoding="utf-8").lower()
    except (OSError, UnicodeDecodeError) as exc:
        return f"ref {ref!r}: {target.name} unreadable ({exc})"
    needles = {anchor.lower(), anchor.lower().replace("-", " ")}
    if not any(n in text for n in needles):
        return f"ref {ref!r}: anchor {anchor!r} does not appear in {file_part}"
    return None


def apply_allowlist(
    findings: Sequence[Finding],
    entries: Sequence[AllowlistEntry],
    coverage: Coverage,
    *,
    check_stale: bool,
) -> list[Finding]:
    """Suppress allowlisted findings; report entries that no longer suppress anything.

    A stale entry is a lie about the codebase — the symbol became reachable and the file now
    claims it is dormant — so it becomes a finding of its own. **Staleness is only meaningful in
    whole-tree mode**: diff-scoped, a dormant symbol that predates the baseline is out of scope
    and would look stale on every single run. That is a real interaction the design did not
    anticipate; the report says which mode it is in rather than quietly guessing.
    """
    kept: list[Finding] = []
    used: set[int] = set()
    for f in findings:
        hit = next((i for i, e in enumerate(entries) if e.matches(f)), None)
        if hit is None:
            kept.append(f)
        else:
            used.add(hit)
            coverage.suppressed_allowlist += 1
    coverage.staleness_checked = check_stale
    if check_stale:
        for i, e in enumerate(entries):
            if i not in used:
                kept.append(
                    Finding(
                        check="ALLOWLIST",
                        module=ALLOWLIST_PATH,
                        qualname=e.symbol,
                        lineno=0,
                        kind="allowlist",
                        note=(
                            "stale entry — nothing matches it any more; the symbol is reachable "
                            "or gone, so the declared dormancy is no longer true"
                        ),
                    )
                )
    return kept


# --- git baselines ---------------------------------------------------------


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise BaselineUnavailableError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.returncode}"
        )
    return proc.stdout.strip()


def resolve_baseline_ref(repo_root: Path, ref: str) -> tuple[str, str]:
    """Resolve ``ref`` to ``(sha, label)``.

    ``merge-base`` is accepted as a special value and warned about: on a long branch it
    produces proportionally more findings than the last reviewed ref — measured at 77 against
    24 on a 26-commit branch, which is the volume at which a gate gets switched off.
    """
    if ref == "merge-base":
        sha = _git(repo_root, "merge-base", "main", "HEAD")
        return sha, f"merge-base main HEAD ({sha[:7]})"
    sha = _git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return sha, f"{ref} ({sha[:7]})"


def extract_ref(repo_root: Path, sha: str, dest: Path) -> Path:
    """Extract ``<sha>:src/gridq`` into ``dest`` with ``git archive``.

    ``git archive`` (never ``git worktree``, never ``git stash``) is what makes this safe to run
    beside a live test suite: the working tree and the index are untouched. Only ``src/gridq``
    is extracted — the baseline is needed for its definitions and parameters, nothing else — so
    the extraction stays well inside the runtime budget.
    """
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "-C", str(repo_root), "archive", "--format=tar", sha, "src/gridq"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise BaselineUnavailableError(
            f"git archive {sha[:7]} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as tar:
        tar.extractall(path=dest, filter="data")
    if not (dest / "src" / "gridq").is_dir():
        raise BaselineUnavailableError(f"{sha[:7]} has no src/gridq to compare against")
    return dest


# --- orchestration ---------------------------------------------------------


def audit(
    tree: Path,
    *,
    baseline_tree: Path | None = None,
    baseline_label: str = "NONE — whole-tree mode",
    tests_root: Path | None = None,
    checks: Sequence[str] = ("B", "A", "C"),
    allowlist: Sequence[AllowlistEntry] = (),
) -> AuditReport:
    """Audit ``tree`` (a directory containing ``src/gridq``), diff-scoped to ``baseline_tree``.

    This is the pure, filesystem-only half: it does no git work, so tests can drive it with
    synthetic trees.
    """
    started = time.perf_counter()
    coverage = Coverage(tree=str(tree), baseline_label=baseline_label)
    coverage.checks_run = tuple(checks)
    coverage.checks_skipped = tuple(c for c in ("B", "A", "C") if c not in checks)

    core = analyse_core(tree)
    coverage.core_modules_parsed = core.parsed
    coverage.core_files_skipped = core.skipped

    tests_root = tests_root if tests_root is not None else tree / "tests"
    coverage.tests_root = str(tests_root)
    coverage.tests_root_found = tests_root.is_dir()
    tests, n_tests, skipped_tests = analyse_tests(tests_root)
    coverage.test_files_parsed = n_tests
    coverage.test_files_skipped = skipped_tests

    baseline_params: set[tuple[str, str, str]] | None = None
    baseline_defs: set[tuple[str, str]] | None = None
    if baseline_tree is not None:
        base = analyse_core(baseline_tree)
        coverage.baseline_modules_parsed = base.parsed
        coverage.baseline_files_skipped = base.skipped
        baseline_params = {
            (p.module, p.qualname, p.name) for f in base.facts.values() for p in f.params
        }
        baseline_defs = {(m, qn) for m, f in base.facts.items() for qn in f.defs}

    findings: list[Finding] = []
    if "B" in checks:
        findings += check_b(core.facts, tests, baseline_params, coverage)
    if "A" in checks:
        findings += check_a(core.facts, tests, baseline_defs, coverage)
    if "C" in checks:
        findings += check_c(core.facts, tests, baseline_defs, coverage)

    findings = dedup(findings, coverage)
    coverage.allowlist_state = (
        f"{len(allowlist)} entry(ies)" if allowlist else f"no entries (no {ALLOWLIST_PATH})"
    )
    findings = apply_allowlist(findings, allowlist, coverage, check_stale=baseline_tree is None)
    findings.sort(key=lambda f: f.sort_key)

    return AuditReport(
        findings=findings, coverage=coverage, elapsed_s=time.perf_counter() - started
    )


def run_audit(
    *,
    repo_root: Path | None = None,
    baseline: str | None = DEFAULT_BASELINE,
    checks: Sequence[str] = ("B", "A", "C"),
    today: date | None = None,
) -> tuple[AuditReport, list[str]]:
    """Resolve the baseline, extract it, audit, and clean up. Returns ``(report, warnings)``."""
    root = repo_root or _REPO_ROOT
    warnings: list[str] = []

    entries, allowlist_errors = load_allowlist(root / ALLOWLIST_PATH, repo_root=root, today=today)
    warnings += [f"{ALLOWLIST_PATH}: {e}" for e in allowlist_errors]

    if baseline is None:
        warnings.append(
            "WHOLE-TREE MODE: no baseline. Expect hundreds of findings, most of them "
            "long-standing design choices rather than new defects. This mode is the periodic "
            "advisory sweep, never the pre-merge gate."
        )
        report = audit(root, baseline_tree=None, checks=checks, allowlist=entries)
        return report, warnings

    sha, label = resolve_baseline_ref(root, baseline)
    if baseline == "merge-base":
        warnings.append(
            "Baseline is merge-base: findings scale with the length of the branch, so a long "
            "branch reports proportionally more. The default (the last reviewed ref) is what "
            "keeps the volume reviewable."
        )
    tmp = Path(tempfile.mkdtemp(prefix="gridq-call-path-baseline-"))
    try:
        extract_ref(root, sha, tmp)
        report = audit(
            root,
            baseline_tree=tmp,
            baseline_label=f"diff-scoped vs {label}",
            checks=checks,
            allowlist=entries,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return report, warnings


# --- CLI -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m gridq.dev.call_path_audit",
        description=(
            "Report code that landed but has no production call site. Suspicions, not "
            "verdicts: a call path existing is not a value flowing."
        ),
    )
    p.add_argument("--repo-root", type=Path, default=None, help="defaults to this repo")
    p.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE,
        help=(
            "git ref to diff-scope against, or 'merge-base'. Default: HEAD (the last reviewed "
            "ref) — measured as the volume that stays reviewable."
        ),
    )
    p.add_argument(
        "--no-baseline",
        action="store_true",
        help="whole-tree advisory sweep: very noisy, not for the commit path",
    )
    p.add_argument("--check", default="all", help="A, B, C, or all (comma-separated)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--max-display",
        type=int,
        default=None,
        help="cap the printed listing (the count hidden is always disclosed)",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help=(
            "exit non-zero when there are findings — for the pre-merge gate, where an unfixed "
            "finding would be grandfathered into every later baseline"
        ),
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m gridq.dev.call_path_audit``.

    Exit status: 0 clean or advisory, 1 the tool could not do its job (unresolvable baseline),
    2 with ``--strict`` when there are findings. Findings alone are **not** a failure by
    default — at the measured false-positive rate they are a prompt to check, not a verdict.
    """
    args = _build_parser().parse_args(argv)
    which = args.check.strip().lower()
    checks = (
        ("B", "A", "C")
        if which == "all"
        else tuple(c.strip().upper() for c in args.check.split(",") if c.strip())
    )
    unknown = [c for c in checks if c not in {"A", "B", "C"}]
    if unknown:
        print(f"unknown check(s): {', '.join(unknown)}", file=sys.stderr)
        return 1

    try:
        report, warnings = run_audit(
            repo_root=args.repo_root,
            baseline=None if args.no_baseline else args.baseline,
            checks=checks,
        )
    except BaselineUnavailableError as exc:
        print(f"call-path audit could not run: {exc}", file=sys.stderr)
        print(
            "Refusing to fall back to whole-tree mode silently — the two modes differ by two "
            "orders of magnitude and the difference would read as a fact about the code. "
            "Pass --no-baseline to ask for it explicitly.",
            file=sys.stderr,
        )
        return 1

    if args.json:
        payload = report.to_dict()
        payload["warnings"] = warnings
        print(json.dumps(payload, indent=1, sort_keys=True, default=str))
    else:
        for w in warnings:
            print(f"! {w}")
        print(report.summary(max_display=args.max_display))
    if args.strict and report.findings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
