"""No test outside the legacy read waiver opens quarantined customer data directly (WP-GRC-05).

The waiver (``legacy_validation_waiver.modules`` in the customer-data manifest) may only shrink, so
a test module that is not on it must never read a file under a customer-data root. The DPA guard
stops the loaders that call it; this static scan stops everything else: a test that opens such a
path itself, by a path literal or an obvious join, would read customer bytes with no DPA and no
access-log entry.

**What counts as reaching a customer root.** A string literal whose path segments contain a root
from the manifest's ``customer_roots`` (``examples/real``, ``data/sites``, ``deliverables``,
``graphify-out/converted``); a join that spells one (``REPO / "examples" / "real"``,
``Path(REPO, "examples", "real")``, ``os.path.join(...)``, ``.joinpath(...)``, an f-string),
including through a name bound to a path prefix (``EXAMPLES = REPO / "examples"`` then
``EXAMPLES / "real"``); any name bound to one of those (assignment, loop or comprehension target,
``with`` target, walrus); a module-local helper or pytest fixture that returns one; and the
parameters of a test whose decorator (a ``parametrize`` over such paths) is built from one. Taint
is scoped per function, so a ``site_json`` under ``tmp_path`` in one test is not confused with a
real path of the same name in another.

**What counts as a read.** ``open`` (builtin, ``io``, ``codecs``, ``Path.open``), ``read_text``,
``read_bytes``, the pandas/numpy/openpyxl readers, and the ``shutil`` copies.

**The lawful routes are not reads here.** The guarded loaders (``load_site_model_json``,
``load_half_hourly_csv``, ...) call ``check_load`` before a byte is read, so on a quarantined file
they raise instead of reading; and the test gateway in ``tests/_dpa_support.py``
(``dpa_released``, ``dpa_released_site``, ``dpa_released_sites``) asks the guard and skips the test,
naming its blocker, until the customer's DPA is registered. The gateway counts only when imported
from ``_dpa_support``: a local function of the same name is not the gateway. Listing a directory
(``glob``, ``iterdir``, ``exists``) reads no customer bytes and is not flagged.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gridq.governance.customer_data import load_manifest

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"

_MANIFEST = load_manifest()
#: The manifest's customer-data roots, as segment tuples.
ROOTS: tuple[tuple[str, ...], ...] = tuple(
    tuple(p for p in root.split("/") if p) for root in _MANIFEST.customer_roots
)
WAIVED: frozenset[str] = frozenset(_MANIFEST.waiver_modules)

#: A method that reads its receiver's file.
READ_METHODS = frozenset({"read_text", "read_bytes", "open"})
#: A function that reads (or copies) the file named by any of its arguments.
READ_FUNCTIONS = frozenset(
    {
        "open",
        "read_csv",
        "read_excel",
        "read_json",
        "read_parquet",
        "read_table",
        "read_fwf",
        "ExcelFile",
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "load_workbook",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
    }
)
#: The test gateway: lawful only when imported from ``_dpa_support``.
GATEWAY = frozenset({"dpa_released", "dpa_released_site", "dpa_released_sites"})
_PATH_CALLS = frozenset({"Path", "PurePath", "PosixPath", "join", "joinpath"})

_Scope = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


def _segments(text: str) -> list[str | None]:
    return [p for p in text.replace("\\", "/").split("/") if p]


def _has_root(parts: list[str | None]) -> bool:
    for root in ROOTS:
        n = len(root)
        for i in range(len(parts) - n + 1):
            if tuple(parts[i : i + n]) == root:
                return True
    return False


def _docstrings(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _own_nodes(scope: _Scope) -> Iterator[ast.AST]:
    """Every node evaluated in ``scope`` itself: nested function bodies belong to their own scope.

    A nested function's decorators and argument defaults are evaluated in the enclosing scope, so
    they are yielded here; its body is not.
    """
    stack: list[ast.AST] = (
        [scope.body] if isinstance(scope, ast.Lambda) else list(scope.body)  # type: ignore[list-item]
    )
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            if not isinstance(node, ast.Lambda):
                stack.extend(node.decorator_list)
            stack.extend(d for d in node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _assigned(target: ast.AST) -> Iterator[str]:
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            yield node.id


def _bindings(scope: _Scope) -> Iterator[tuple[ast.AST, ast.AST]]:
    """(target, value) for every binding made in ``scope``."""
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                yield target, node.value
        elif isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None:
            yield node.target, node.value
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            yield node.target, node.iter
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            yield node.optional_vars, node.context_expr
        elif isinstance(node, ast.NamedExpr):
            yield node.target, node.value


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> list[str]:
    a = fn.args
    names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    names += [x.arg for x in (a.vararg, a.kwarg) if x is not None]
    return names


def _is_fixture(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
        if isinstance(target, ast.Name) and target.id == "fixture":
            return True
    return False


@dataclass
class _ScopeState:
    node: _Scope
    parent: _ScopeState | None
    local: set[str] = field(default_factory=set)
    tainted: set[str] = field(default_factory=set)
    prefixes: dict[str, list[str | None]] = field(default_factory=dict)

    def is_tainted(self, name: str) -> bool:
        if name in self.local:
            return name in self.tainted
        return self.parent.is_tainted(name) if self.parent is not None else False

    def prefix(self, name: str) -> list[str | None] | None:
        if name in self.prefixes:
            return self.prefixes[name]
        if name in self.local:
            return None
        return self.parent.prefix(name) if self.parent is not None else None


class _Scan:
    """One module's scan: the lines where a customer-root path reaches a read."""

    def __init__(self, source: str) -> None:
        self.tree = ast.parse(source)
        self.docs = _docstrings(self.tree)
        self.gateway = {
            alias.asname or alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[-1] == "_dpa_support"
            for alias in node.names
            if alias.name in GATEWAY
        }
        self.helpers: set[str] = set()  # module-local functions that return a customer path
        self.fixtures: set[str] = set()  # pytest fixtures that return one
        self.states: dict[int, _ScopeState] = {}

    # -- path arithmetic ------------------------------------------------------------------------
    def _parts(self, node: ast.AST, state: _ScopeState) -> list[str | None]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._parts(node.left, state) + self._parts(node.right, state)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return list(_segments(node.value))
        if isinstance(node, ast.JoinedStr):
            out: list[str | None] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    out += _segments(value.value)
                else:
                    out.append(None)
            return out
        if isinstance(node, ast.Name):
            prefix = state.prefix(node.id)
            return list(prefix) if prefix is not None else [None]
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in _PATH_CALLS:
                out = (
                    self._parts(fn.value, state)
                    if isinstance(fn, ast.Attribute) and name == "joinpath"
                    else []
                )
                for arg in node.args:
                    out += self._parts(arg, state)
                return out
        return [None]

    # -- taint ----------------------------------------------------------------------------------
    def _sanitised(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.gateway
        )

    def tainted(self, node: ast.AST | None, state: _ScopeState) -> bool:
        if node is None or self._sanitised(node):
            return False
        if isinstance(node, ast.Constant):
            return (
                isinstance(node.value, str)
                and id(node) not in self.docs
                and _has_root(_segments(node.value))
            )
        if isinstance(node, ast.Name):
            return state.is_tainted(node.id)
        if isinstance(node, ast.BinOp | ast.Call | ast.JoinedStr) and _has_root(
            self._parts(node, state)
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.helpers
        ):
            return True
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            return False
        return any(self.tainted(child, state) for child in ast.iter_child_nodes(node))

    # -- the fixed point ------------------------------------------------------------------------
    def _scopes(self) -> list[_Scope]:
        found: list[_Scope] = [self.tree]
        found += [
            n
            for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
        ]
        return found

    def run(self) -> list[int]:
        scopes = self._scopes()
        parents: dict[int, _Scope | None] = {id(self.tree): None}
        for scope in scopes:
            for node in _own_nodes(scope):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                    parents[id(node)] = scope

        def state_of(scope: _Scope) -> _ScopeState:
            key = id(scope)
            if key not in self.states:
                parent = parents[key]
                self.states[key] = _ScopeState(
                    node=scope, parent=state_of(parent) if parent is not None else None
                )
                st = self.states[key]
                if not isinstance(scope, ast.Module):
                    st.local.update(_params(scope))
                for target, _value in _bindings(scope):
                    st.local.update(_assigned(target))
            return self.states[key]

        for scope in scopes:
            state_of(scope)

        # Path prefixes bound to names (``EXAMPLES = REPO / "examples"``): a bounded pre-pass, so a
        # name rebound to different prefixes cannot make the fixed point below oscillate.
        for _ in range(4):
            for scope in scopes:
                st = self.states[id(scope)]
                for target, value in _bindings(scope):
                    if isinstance(target, ast.Name):
                        parts = self._parts(value, st)
                        if any(p is not None for p in parts):
                            st.prefixes[target.id] = parts

        # Taint only grows, so this terminates.
        changed = True
        while changed:
            changed = False
            for scope in scopes:
                st = self.states[id(scope)]
                for target, value in _bindings(scope):
                    if self.tainted(value, st):
                        for name in _assigned(target):
                            if name not in st.tainted:
                                st.tainted.add(name)
                                changed = True
                if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
                    enclosing = st.parent
                    assert enclosing is not None
                    params = _params(scope)
                    # A parametrize (or any decorator) built from a customer path taints the
                    # parameters it feeds; a fixture that returns one taints its users.
                    taint_params = any(self.tainted(d, enclosing) for d in scope.decorator_list)
                    for name in params:
                        if (taint_params or name in self.fixtures) and name not in st.tainted:
                            st.tainted.add(name)
                            changed = True
                    returns = [
                        n.value
                        for n in _own_nodes(scope)
                        if isinstance(n, ast.Return | ast.Yield | ast.YieldFrom)
                    ]
                    if any(self.tainted(r, st) for r in returns):
                        bucket = self.fixtures if _is_fixture(scope) else self.helpers
                        if scope.name not in bucket:
                            bucket.add(scope.name)
                            changed = True

        hits: set[int] = set()
        for scope in scopes:
            st = self.states[id(scope)]
            for node in _own_nodes(scope):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr in READ_METHODS
                    and self.tainted(fn.value, st)
                ):
                    hits.add(node.lineno)
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                args = [*node.args, *(k.value for k in node.keywords)]
                if name in READ_FUNCTIONS and any(self.tainted(a, st) for a in args):
                    hits.add(node.lineno)
        return sorted(hits)


def direct_reads(source: str) -> list[int]:
    """The lines of ``source`` that read a file under a customer-data root without the guard."""
    return _Scan(source).run()


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


# --------------------------------------------------------------------------- the guard itself


def test_no_test_outside_the_waiver_opens_a_customer_data_path() -> None:
    offenders = {
        rel: lines
        for path in _test_modules()
        if (rel := path.relative_to(REPO).as_posix()) not in WAIVED
        and (lines := direct_reads(path.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        "these test modules read quarantined customer data directly, outside the legacy read "
        f"waiver: {offenders}. Read it through tests/_dpa_support.py (dpa_released, "
        "dpa_released_site, dpa_released_sites) so the test waits for the customer's DPA, or move "
        "the test onto a SYNTHETIC fixture. Never add the module to the waiver: it may only shrink."
    )


def test_the_scan_covers_every_customer_root_and_reads_the_waiver_from_the_manifest() -> None:
    assert ("examples", "real") in ROOTS
    assert len(ROOTS) == len(_MANIFEST.customer_roots) >= 4
    assert WAIVED and all(m.startswith("tests/") and m.endswith(".py") for m in WAIVED)
    scanned = {p.relative_to(REPO).as_posix() for p in _test_modules()}
    assert len(scanned - WAIVED) > 100, "the scan must walk the whole test tree"


# ------------------------------------------------------------------ the scanner's own behaviour

_HEAD = "import json, os, shutil\nimport pandas as pd\nimport pytest\nfrom pathlib import Path\n"
_HEAD += "REPO = Path(__file__).resolve().parents[1]\n"

FLAGGED = {
    "builtin open on a literal": 'open("examples/real/sites/x.json").read()',
    "Path join then read_text": '(REPO / "examples" / "real" / "sites" / "x.json").read_text()',
    "one literal holding the root": '(REPO / "examples/real/sites/x.json").read_bytes()',
    "os.path.join": 'open(os.path.join(REPO, "examples", "real", "x.json"))',
    "Path() with segments": 'Path(REPO, "examples", "real", "x.json").open()',
    "joinpath": 'REPO.joinpath("examples", "real", "x.json").read_text()',
    "f-string": 'open(f"{REPO}/examples/real/x.json")',
    "a prefix name": 'EX = REPO / "examples"\n(EX / "real" / "x.json").read_text()',
    "a module constant used in a function": (
        'P = REPO / "examples/real/sites"\n'
        "def test_x():\n"
        '    return json.loads((P / "x.json").read_text())'
    ),
    "a local helper": (
        "def _p(n):\n"
        '    return REPO / "examples" / "real" / f"{n}.json"\n'
        "def test_x():\n"
        '    _p("x").read_text()'
    ),
    "a loop over a listing": (
        'for p in sorted((REPO / "examples" / "real").glob("*.json")):\n    p.read_text()'
    ),
    "a parametrize over a listing": (
        '@pytest.mark.parametrize("p", sorted(Path("examples/real/sites").glob("*.json")))\n'
        "def test_x(p):\n"
        "    p.read_text()"
    ),
    "a fixture": (
        "@pytest.fixture\n"
        "def real():\n"
        '    return REPO / "examples/real/x.json"\n'
        "def test_x(real):\n"
        "    real.read_bytes()"
    ),
    "pandas on another customer root": 'pd.read_csv(REPO / "data" / "sites" / "hh" / "x.csv")',
    "a copy": 'shutil.copy(REPO / "examples/real/x.json", "/tmp/y")',
    "a look-alike gateway": (
        "def dpa_released(p):\n"
        "    return p\n"
        'dpa_released(REPO / "examples" / "real" / "x.json").read_text()'
    ),
}

CLEAN = {
    "the gateway": (
        "from tests._dpa_support import dpa_released\n"
        'dpa_released(REPO / "examples" / "real" / "x.json").read_text()'
    ),
    "a guarded loader": (
        "from gridq.ingestion.loaders import load_site_model_json\n"
        'load_site_model_json(REPO / "examples/real/sites/x.json")'
    ),
    "the same name in another function": (
        "def test_a():\n"
        '    site_json = REPO / "examples" / "real" / "x.json"\n'
        "def test_b(tmp_path):\n"
        '    site_json = tmp_path / "x.json"\n'
        "    site_json.read_text()"
    ),
    "a root named in a message": 'print("see examples/real/sites")\nopen("README.md").read()',
    "a listing": 'names = [p.stem for p in (REPO / "examples" / "real").glob("*.json")]',
    "an unrelated read": '(REPO / "examples" / "demo_site" / "sitemodel.json").read_text()',
    "a word that only starts like a root": 'open("examples/realistic/x.json")',
}


@pytest.mark.parametrize("name", sorted(FLAGGED))
def test_the_scan_flags_a_direct_read(name: str) -> None:
    assert direct_reads(_HEAD + textwrap.dedent(FLAGGED[name])), name


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_the_scan_passes_a_lawful_route(name: str) -> None:
    assert direct_reads(_HEAD + textwrap.dedent(CLEAN[name])) == [], name
