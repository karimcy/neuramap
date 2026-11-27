"""WP-SIZE-02: the shared constraint library (`gridq.constraint_library`).

The roadmap's acceptance, one section each:

1. **Sizer results unchanged after extraction.** The golden suite (`test_determinism_golden.py`,
   gb_v1_4) is the bar and is not edited. Here: the library's algebra reproduces the formulas it
   replaced, and the solver paths reach it (a spy on each library entry point the MILP, the MPC
   and the sizer's search box call), so the extraction is live rather than a copy beside the code.
2. **Library version recorded in every `OptimisationResult`.** Every constructor in ``src`` passes
   the stamp (AST), every engine stamps it (baseline, MILP spec and no-spec, underwriting), the
   conformance bar checks it, and the stamp never moves ``input_hash``.
3. **The library imports contracts only.** An AST scan of every module in the package, and a
   fresh interpreter proving the transitive closure holds no workstream module.

Plus the version discipline (D177): the source hash is pinned per released version, and
the stamp restates IC-3's `ConstraintLibraryPin`, so `check_envelope` matches a pin it wrote.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import linopy
import numpy as np
import pandas as pd
import pytest
from tests._milp_support import multi_rate_site, write_hh_csv

import gridq.constraint_library as cl
from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.constraint_library import _releases, catalogue, identity
from gridq.contracts.dispatch_envelope import ConstraintLibraryPin, DispatchEnvelope
from gridq.contracts.thermal_flex import ThermalFlexAvailability, query_at
from gridq.dispatch import DispatchProblem
from gridq.engine import BaselineEngine
from gridq.engine.conformance import check_engine_conformance
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.mpc_settings import MpcSettings
from gridq.optimisation.mpc_solver import MpcDispatchSolver
from gridq.optimisation.two_stage import connection_power_limit_kw
from gridq.quotes.assembly import assemble_quote_record
from gridq.schemas import (
    AvailabilityCommitment,
    AvailabilityWindow,
    BankableStatus,
    ConstraintLibraryRef,
    ImplementationLevel,
    MarketStream,
    OptimisationRequest,
    OptimisationResult,
    ProvisioningRequirement,
    SiteModel,
    SolverMetadata,
    SolverStatus,
)
from gridq.sizing.search_space import SizingCandidate
from gridq.sizing.sizer import _to_optimisation_result
from gridq.value_stack.interface import DispatchConstraintDescriptor

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "gridq"
PACKAGE = SRC / "constraint_library"
IC3_HYBRID = (
    REPO / "tests" / "contracts" / "fixtures" / "ic3" / "ic3_envelope_hybrid_bess.synthetic.json"
)
IC11_SURFACE = (
    REPO / "tests" / "contracts" / "fixtures" / "ic11_surface_chilled_water_modelled.synthetic.json"
)

POWER_KW = 200.0
ENERGY_KWH = 400.0


# ====================================================================== 3. the import boundary

#: Third-party packages the library may use: the array stack the solver's rows are built from.
_THIRD_PARTY = frozenset({"numpy", "pandas", "xarray"})
#: The only GridQ packages the library may import: the frozen contracts, the IC-12 chassis in
#: `gridq.schemas`, and itself (D176).
_GRIDQ_ALLOWED = ("gridq.contracts", "gridq.schemas", "gridq.constraint_library")


def _imports(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):  # walk, not body: a lazy import inside a function counts too
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                yield node.lineno, "." * node.level + (node.module or "")
            elif node.module == "gridq":
                for alias in node.names:
                    yield node.lineno, f"gridq.{alias.name}"
            else:
                yield node.lineno, node.module or ""


def _allowed(module: str) -> bool:
    top = module.split(".")[0]
    if module.startswith("gridq"):
        return any(module == a or module.startswith(a + ".") for a in _GRIDQ_ALLOWED)
    return top == "__future__" or top in sys.stdlib_module_names or top in _THIRD_PARTY


def test_the_package_is_every_module_the_scan_reads() -> None:
    names = {p.name for p in PACKAGE.glob("*.py")}
    assert names == {
        "__init__.py",
        "_releases.py",
        "catalogue.py",
        "commitments.py",
        "connection.py",
        "identity.py",
        "resilience.py",
        "soc.py",
        "thermal.py",
        "warranty.py",
    }


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_the_library_imports_contracts_only(path: Path) -> None:
    """ACCEPTANCE 3 (direct). No workstream import, no relative import, lazy imports included."""
    bad = [f"{path.name}:{line} imports {mod}" for line, mod in _imports(path) if not _allowed(mod)]
    assert not bad, bad


def test_the_boundary_scan_has_teeth(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import numpy\n"
        "from gridq.contracts import thermal_flex\n"
        "def f():\n"
        "    from gridq.optimisation._assumptions import maybe\n"
        "    from gridq import sizing\n"
        "    from . import soc\n",
        encoding="utf-8",
    )
    refused = sorted(m for _, m in _imports(probe) if not _allowed(m))
    assert refused == [".", "gridq.optimisation._assumptions", "gridq.sizing"]


def test_the_library_loads_no_workstream_module_transitively() -> None:
    """ACCEPTANCE 3 (transitive). In a fresh interpreter, importing the library loads GridQ's
    contracts and chassis and nothing else. `gridq.extensions` arrives with `gridq.schemas` (the
    IC-12 extension registries, part of the chassis since 2.0.0), never from the library itself."""
    probe = (
        "import json, sys\n"
        "import {mod}\n"
        "print(json.dumps(sorted({{'.'.join(m.split('.')[:2]) for m in sys.modules "
        "if m.startswith('gridq')}})))\n"
    )

    def loaded(mod: str) -> set[str]:
        out = subprocess.run(
            [sys.executable, "-c", probe.format(mod=mod)],
            check=True,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(REPO / "src"), "PATH": ""},
        ).stdout
        return set(json.loads(out))

    library = loaded("gridq.constraint_library")
    assert library == {
        "gridq",
        "gridq.constraint_library",
        "gridq.contracts",
        "gridq.extensions",
        "gridq.schemas",
    }
    assert library - loaded("gridq.schemas") - loaded("gridq.contracts") == {
        "gridq.constraint_library"
    }


@pytest.mark.parametrize("package", ["contracts", "schemas"])
def test_the_contracts_never_import_the_library(package: str) -> None:
    """The dependency points one way: the contracts know nothing of the library built on them."""
    hits = [
        f"{p.relative_to(SRC)}:{line}"
        for p in sorted((SRC / package).rglob("*.py"))
        for line, mod in _imports(p)
        if mod.startswith("gridq.constraint_library")
    ]
    assert not hits, hits


# ====================================================================== identity and versioning


def test_the_released_hash_is_the_source_hash() -> None:
    """D177. Edit any library module and this fails until the edit is released: bump
    ``LIBRARY_VERSION`` (patch / minor / major per `identity`) and append its hash to
    ``_releases.RELEASES``. A released entry is never edited."""
    assert cl.LIBRARY_VERSION in _releases.RELEASES
    assert _releases.RELEASES[cl.LIBRARY_VERSION] == cl.library_hash(), (
        f"library source moved: release it as a new version with hash {cl.library_hash()}"
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", cl.library_hash())


def test_the_current_version_is_the_newest_release() -> None:
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    assert all(
        re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", v) for v in _releases.RELEASES
    )
    assert max(_releases.RELEASES, key=key) == cl.LIBRARY_VERSION
    assert len(set(_releases.RELEASES.values())) == len(_releases.RELEASES)


def test_the_hash_covers_every_module_but_the_release_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    covered = [p.name for p in identity.source_files()]
    assert covered == sorted(p.name for p in PACKAGE.glob("*.py") if p.name != "_releases.py")
    copy = tmp_path / "constraint_library"
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(identity, "_PACKAGE_DIR", copy)
    unhashed = identity.library_hash.__wrapped__
    assert unhashed() == cl.library_hash()
    (copy / "_releases.py").write_text("RELEASES = {}\n", encoding="utf-8")
    assert unhashed() == cl.library_hash()  # the record of the hash is outside it
    for name in covered:
        path = copy / name
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "\n# moved\n", encoding="utf-8")
        assert unhashed() != cl.library_hash(), name
        path.write_text(original.replace("\n", "\r\n"), encoding="utf-8")
        assert unhashed() == cl.library_hash(), name  # line endings are not a change
        path.write_text(original, encoding="utf-8")


def test_the_stamp_and_the_ic3_pin_state_the_same_library() -> None:
    ref, pin = cl.library_ref(), cl.library_pin()
    assert isinstance(ref, ConstraintLibraryRef) and isinstance(pin, ConstraintLibraryPin)
    assert ref.library_id == cl.LIBRARY_ID == "gridq.constraint_library"
    assert (ref.library_version, ref.library_hash, ref.constraint_ids) == (
        pin.library_version,
        pin.library_hash,
        pin.constraint_ids,
    )
    assert ref.constraint_ids == cl.constraint_ids()
    assert ConstraintLibraryRef.model_validate(ref.model_dump(mode="json")) == ref


def _envelope_with(pin: object) -> DispatchEnvelope:
    data = json.loads(IC3_HYBRID.read_text(encoding="utf-8"))
    data["source"]["constraint_library"] = (
        pin.model_dump(mode="json") if hasattr(pin, "model_dump") else pin
    )
    return DispatchEnvelope.model_validate_json(json.dumps(data))


def test_live_dispatch_matches_an_envelope_sized_with_this_library() -> None:
    """The IC-3 loop closes: a pin written from `library_pin` is a match for this build, a pin
    from another source hash is a flagged mismatch, and an unknown pin stays unverifiable."""
    assert cl.check_envelope(_envelope_with(cl.library_pin())).status == "match"
    other = cl.library_pin().model_copy(update={"library_hash": "sha256:" + "0" * 64})
    assert cl.check_envelope(_envelope_with(other)).status == "mismatch"
    older = cl.library_pin().model_copy(update={"library_version": "0.9.0"})
    assert cl.check_envelope(_envelope_with(older)).status == "mismatch"
    unknown = {"kind": "unknown", "reason": "synthetic: not pinned"}
    assert cl.check_envelope(_envelope_with(unknown)).status == "unverifiable"


def test_the_stamp_refuses_a_malformed_library() -> None:
    good = cl.library_ref().model_dump(mode="json")
    for bad in (
        {"constraint_ids": ["soc_window", "import_limit"]},  # unsorted
        {"constraint_ids": ["import_limit", "import_limit"]},  # repeated
        {"constraint_ids": ["Import-Limit"]},  # off-pattern
        {"constraint_ids": []},
        {"library_version": "1.0"},
        {"library_hash": "sha256:xyz"},
        {"library_id": "Gridq Library"},
        {"surplus": 1},
    ):
        with pytest.raises(ValueError):
            ConstraintLibraryRef.model_validate({**good, **bad})


# ====================================================================== the catalogue


def test_the_catalogue_names_each_constraint_once_in_pin_order() -> None:
    ids = [s.constraint_id for s in catalogue.CATALOGUE]
    assert ids == sorted(set(ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", i) for i in ids)
    assert cl.constraint_ids() == tuple(ids)
    for spec in catalogue.CATALOGUE:
        assert (PACKAGE / f"{spec.module}.py").exists(), spec
        assert set(spec.applied_by) <= {catalogue.ANNUAL_MILP, catalogue.MPC_WINDOW}, spec
        assert spec.rows and spec.parameters and spec.summary, spec
        assert catalogue.spec(spec.constraint_id) is spec
    with pytest.raises(KeyError):
        catalogue.spec("no_such_constraint")


def test_every_roadmap_family_is_catalogued() -> None:
    """The WP-SIZE-02 set: import limit, resilience floor, thermal limits, SoC, warranty,
    commitments (plus the inverter's own rating)."""
    families = {s.family for s in catalogue.CATALOGUE}
    assert families == set(catalogue.Family)
    assert catalogue.spec("import_limit").family is catalogue.Family.CONNECTION
    assert catalogue.spec("thermal_turn_down_limit").applied_by == ()  # D179, stated


# ====================================================================== the algebra it replaced


@pytest.mark.parametrize(
    ("mic", "asc", "expected"),
    [
        (500.0, 400.0, 400.0),
        (300.0, None, 300.0),
        (None, 250.0, 250.0),
        (0.0, 350.0, 350.0),  # a zero is "not declared", never a 0 kW limit
        (-5.0, None, None),
        (None, None, None),
        (200, 200, 200.0),
    ],
)
def test_the_import_limit_is_min_mic_asc_over_declared_values(
    mic: float | None, asc: float | None, expected: float | None
) -> None:
    assert cl.connection.import_limit_kw(mic, asc) == expected


def test_the_charge_and_discharge_bounds_are_the_solver_formulas() -> None:
    power, limit, aux = 100.0, 400.0, 2.0
    net_load = np.array([350.0, 390.0, 100.0, 500.0])
    load = np.array([50.0, 150.0, 0.0, 80.0])
    assert np.array_equal(
        cl.connection.charge_upper_bound_kw(power, limit, net_load, aux),
        np.minimum(power, np.maximum(0.0, limit - net_load - aux)),
    )
    addition = np.array([0.0, 10.0, 0.0, 0.0])
    assert np.array_equal(
        cl.connection.charge_upper_bound_kw(power, limit, net_load, aux, addition),
        np.minimum(power, np.maximum(0.0, limit - net_load - aux - addition)),
    )
    forced = np.array([0.0, 30.0, 120.0, 0.0])
    assert np.array_equal(cl.connection.discharge_upper_bound_kw(power, load), [50, 100, 0, 80])
    assert np.array_equal(
        cl.connection.discharge_upper_bound_kw(power, load, forced),
        np.minimum(np.maximum(power - forced, 0.0), load),
    )
    assert cl.connection.export_cap_kw(power, 60.0) == 60.0
    assert cl.connection.export_cap_kw(power, -1.0) == 0.0


def test_the_soc_window_and_warranty_cap_are_the_solver_formulas() -> None:
    assert cl.soc.soe_window_kwh(0.1, 0.9, 400.0) == (0.1 * 400.0, 0.9 * 400.0)
    assert cl.soc.resolve_soc_window(None, None)[:2] == (0.0, 1.0)
    assert cl.soc.resolve_soc_window(0.1, 0.9) == (0.1, 0.9, None)
    assert (
        cl.soc.usable_window_note(
            soc_min_frac=0.0,
            soc_max_frac=1.0,
            usable_energy=400.0,
            deliverable_energy=376.0,
            energy_kwh=400.0,
            eta_dis=0.94,
        )
        is None
    )
    assert (
        cl.soc.initial_soe_refusal(
            200.0,
            soe_floor=40.0,
            soe_ceiling=360.0,
            soc_min_frac=0.1,
            soc_max_frac=0.9,
            energy_kwh=400,
        )
        is None
    )
    assert cl.soc.initial_soe_refusal(
        380.0, soe_floor=40.0, soe_ceiling=360.0, soc_min_frac=0.1, soc_max_frac=0.9, energy_kwh=400
    )
    assert cl.warranty.throughput_cap_kwh(365.0, 400.0, 52.0) == 365.0 * 400.0 / 52.0
    table = {"1": 548.0, "2": 365.0, "4": 250.0}
    assert cl.warranty.ceiling_value_for_duration(table, 3.0)[0] == 250.0
    assert cl.warranty.ceiling_value_for_duration(table, 2.0) == (365.0, None)


def test_the_optimisation_package_reads_the_library_not_a_copy() -> None:
    """One implementation: the names the optimisation package kept are the library's objects."""
    from gridq.optimisation import _assumptions, milp_solver

    assert _assumptions.ceiling_value_for_duration is cl.warranty.ceiling_value_for_duration
    assert milp_solver._commitment_mask is cl.commitments.commitment_mask
    assert milp_solver._WARRANTY_KEY == cl.warranty.WARRANTY_KEY
    assert (milp_solver._SOC_MIN_KEY, milp_solver._SOC_MAX_KEY) == (
        cl.soc.SOC_MIN_KEY,
        cl.soc.SOC_MAX_KEY,
    )
    assert milp_solver._OCTAGON_K == cl.connection.OCTAGON_K
    assert milp_solver._COMPILED_KINDS is cl.commitments.COMPILED_KINDS
    for name in (
        "_reactive_connection_screen",
        "_add_inverter_pq_rows",
        "_add_connection_kva_rows",
    ):
        assert not hasattr(milp_solver, name), name


# ====================================================================== thermal limits (IC-11)


def test_thermal_limits_are_read_from_the_ic11_surface() -> None:
    surface = ThermalFlexAvailability.model_validate_json(IC11_SURFACE.read_text(encoding="utf-8"))
    starts = [datetime(2027, 1, 15, 0, 0, tzinfo=UTC) + timedelta(hours=3 * i) for i in range(16)]
    starts += [datetime(2027, 7, 1, 12, 0, tzinfo=UTC) + timedelta(hours=3 * i) for i in range(8)]
    limits, statuses = cl.thermal.turn_down_limits_kw(surface, starts, 60)
    for i, start in enumerate(starts):
        answer = query_at(surface, start, 60)
        assert statuses[i] == answer.status
        want = float(answer.deliverable_kw) if isinstance(answer.deliverable_kw, int) else 0.0
        assert limits[i] == want, start
    assert (limits > 0).any(), "the synthetic surface should offer some holdable turn-down"
    with pytest.raises(ValueError, match="timezone-aware"):
        cl.thermal.turn_down_limits_kw(surface, [datetime(2027, 1, 15)], 60)

    model = linopy.Model()
    coords = pd.RangeIndex(len(starts), name="t")
    turn_down = model.add_variables(lower=0.0, coords=[coords], name="turn_down")
    cl.thermal.add_turn_down_rows(model, turn_down, limits, coords)
    assert "thermal_turn_down_limit" in model.constraints


# ====================================================================== 1. reachability (spies)


@pytest.fixture(scope="module")
def site_and_lib(tmp_path_factory: pytest.TempPathFactory) -> tuple[SiteModel, AssumptionLibrary]:
    path = tmp_path_factory.mktemp("size02") / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    return multi_rate_site(path, rows=rows), load_assumptions("gb_v0_1")


def _rich_problem(site: SiteModel, lib: AssumptionLibrary) -> DispatchProblem:
    """A 7-day problem that exercises every applied family: a load-service obligation (resilience
    floor) and a typed availability commitment with a held energy (commitments)."""
    window = AvailabilityWindow(
        days_of_week=[0, 1, 2, 3, 4], start_time=time(17), end_time=time(19)
    )
    requirement = ProvisioningRequirement(
        requirement_id="hgv-1", windows=[window], additional_load_kw=300.0, tenor_years=10.0
    )
    commitment = AvailabilityCommitment(
        commitment_id="cm_test",
        stream=MarketStream.CAPACITY_MARKET,
        committed_power_kw=40.0,
        held_energy_kwh=40.0,
        windows=[AvailabilityWindow(days_of_week=[5, 6], start_time=time(16), end_time=time(19))],
        tenor_years=15.0,
        reference="TEST/AGR-001",
    )
    return DispatchProblem(
        site_model=site.model_copy(update={"provisioning_requirements": [requirement]}),
        assumptions=lib,
        power_kw=POWER_KW,
        energy_kwh=ENERGY_KWH,
        duration_hours=ENERGY_KWH / POWER_KW,
        constraints=[
            DispatchConstraintDescriptor(
                constraint_id=f"{kind}_test",
                description="synthetic commitment",
                kind=kind,
                parameters=commitment,
            )
            for kind in ("availability_window", "held_state_of_energy")
        ],
    )


#: (library module, function) the MILP build calls on every solve with the rich problem.
_MILP_ENTRY_POINTS = (
    ("soc", "soe_window_kwh"),
    ("soc", "usable_window_note"),
    ("soc", "initial_soe_refusal"),
    ("soc", "add_state_of_energy"),
    ("connection", "import_limit_kw"),
    ("connection", "charge_upper_bound_kw"),
    ("connection", "discharge_upper_bound_kw"),
    ("resilience", "forced_draw_balance_rhs"),
    ("resilience", "add_load_service_prepositions"),
    ("warranty", "ceiling_value_for_duration"),
    ("warranty", "throughput_cap_kwh"),
    ("warranty", "add_throughput_cap_row"),
    ("commitments", "deliverable_envelope_kw"),
    ("commitments", "compile_commitments"),
)


class _Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, entries: tuple[tuple[str, str], ...]):
        self.calls: dict[tuple[str, str], int] = dict.fromkeys(entries, 0)
        for module_name, fn_name in entries:
            module = getattr(cl, module_name)
            monkeypatch.setattr(module, fn_name, self._wrap((module_name, fn_name), module))

    def _wrap(self, key: tuple[str, str], module: Any) -> Callable[..., Any]:
        real = getattr(module, key[1])

        def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls[key] += 1
            return real(*args, **kwargs)

        return spy

    def missed(self) -> list[tuple[str, str]]:
        return [k for k, n in self.calls.items() if n == 0]


def _rows_built(monkeypatch: pytest.MonkeyPatch) -> list[set[str]]:
    """Record the constraint and variable names of every model handed to the solver."""
    built: list[set[str]] = []
    real = linopy.Model.solve

    def solve(self: linopy.Model, *args: Any, **kwargs: Any) -> Any:
        built.append(set(self.constraints) | set(self.variables))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(linopy.Model, "solve", solve)
    return built


def test_the_annual_milp_builds_its_constraints_from_the_library(
    site_and_lib: tuple[SiteModel, AssumptionLibrary], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACCEPTANCE 1 (reachability). Delete any library call in `MilpDispatchSolver._solve_milp`
    and this fails; the rows the catalogue names are the rows the solver builds."""
    site, lib = site_and_lib
    spy = _Spy(monkeypatch, _MILP_ENTRY_POINTS)
    built = _rows_built(monkeypatch)
    result = MilpDispatchSolver().solve(_rich_problem(site, lib))
    assert result.solver_status is SolverStatus.OPTIMAL, result.warnings
    assert spy.missed() == []
    rows = set().union(*built)
    for name in (
        "soe",
        "charge",
        "discharge",
        "throughput_cap",
        "availability_reserve_aggregate",
        "held_state_of_energy_cm_test",
        "load_service_prepos_hgv_1",
    ):
        assert name in rows, (name, sorted(rows))


def test_each_mpc_window_builds_its_constraints_from_the_library(
    site_and_lib: tuple[SiteModel, AssumptionLibrary], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACCEPTANCE 1 (reachability, NEW-SIZE-LIB). The rolling-horizon MPC takes its connection
    limit and warranty cap from the library, and every window it solves is built by it, with the
    same resilience and commitment rows as the annual MILP (the catalogue's ``mpc_window``)."""
    site, lib = site_and_lib
    spy = _Spy(monkeypatch, _MILP_ENTRY_POINTS)
    built = _rows_built(monkeypatch)
    settings = MpcSettings(prediction_horizon_days=2.0, application_horizon_days=1.0)
    result = MpcDispatchSolver(settings=settings).solve(_rich_problem(site, lib))
    assert result.solver_status is SolverStatus.OPTIMAL, result.warnings
    assert spy.missed() == []
    windows = len(built)
    assert windows >= 2
    # One library build per window, plus the MPC's own reads of the limit and the cap.
    assert spy.calls[("soc", "add_state_of_energy")] == windows
    assert spy.calls[("commitments", "compile_commitments")] == windows
    assert spy.calls[("connection", "import_limit_kw")] == windows + 1
    assert spy.calls[("warranty", "throughput_cap_kwh")] == windows + 1
    every = {"soe", "charge", "discharge", "throughput_cap", "availability_reserve_aggregate"}
    assert all(every <= rows for rows in built)
    seen = set().union(*built)
    assert {"held_state_of_energy_cm_test", "load_service_prepos_hgv_1"} <= seen
    mpc = {s.constraint_id for s in catalogue.CATALOGUE if catalogue.MPC_WINDOW in s.applied_by}
    assert {"commitment_availability_window", "commitment_held_state_of_energy"} <= mpc
    assert "export_limit" not in mpc


def test_the_sizer_search_box_reads_the_library_import_limit(
    site_and_lib: tuple[SiteModel, AssumptionLibrary], monkeypatch: pytest.MonkeyPatch
) -> None:
    site, _ = site_and_lib
    spy = _Spy(monkeypatch, (("connection", "import_limit_kw"),))
    assert connection_power_limit_kw(site) == 500.0
    assert spy.calls[("connection", "import_limit_kw")] == 1


# ================================================================== 2. the stamp on every result


def _constructor_calls() -> list[tuple[str, int, set[str]]]:
    calls = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "OptimisationResult":
                kwargs = {k.arg for k in node.keywords if k.arg is not None}
                calls.append((str(path.relative_to(SRC)), node.lineno, kwargs))
    return calls


def test_every_result_constructed_in_src_is_stamped() -> None:
    """ACCEPTANCE 2 (static). A new `OptimisationResult(...)` without the stamp fails here.
    (`model_copy` and `model_validate` of an existing result carry the stamp through.)"""
    calls = _constructor_calls()
    assert {path for path, _, _ in calls} >= {
        "sizing/sizer.py",
        "optimisation/engine.py",
        "engine/baseline.py",
        "app/demo.py",
    }
    missing = [f"{p}:{line}" for p, line, kw in calls if "constraint_library" not in kw]
    assert not missing, missing


def _hand_result(**over: Any) -> OptimisationResult:
    return OptimisationResult(
        result_id="res_x",
        request_id="req_x",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.NOT_BANKABLE,
        solver_status=SolverStatus.OPTIMAL,
        solver_metadata=SolverMetadata(
            seed=0, assumption_version="gb_v0_1", code_version="c", model_version="m"
        ),
        **over,
    )


def test_the_sizer_stamps_each_candidate_result(
    site_and_lib: tuple[SiteModel, AssumptionLibrary],
) -> None:
    site, lib = site_and_lib
    dispatch = MilpDispatchSolver().solve(
        DispatchProblem(
            site_model=site,
            assumptions=lib,
            power_kw=POWER_KW,
            energy_kwh=ENERGY_KWH,
            duration_hours=ENERGY_KWH / POWER_KW,
        )
    )
    result = _to_optimisation_result(
        SizingCandidate(power_kw=POWER_KW, duration_hours=ENERGY_KWH / POWER_KW),
        dispatch,
        assumption_version="gb_v0_1",
        model_version="m",
    )
    assert result.constraint_library == cl.library_ref()
    assert result.model_dump(mode="json")["constraint_library"]["library_version"] == "1.0.1"


@pytest.fixture
def _registries_restored() -> Iterator[None]:
    """Building an `UnderwritingEngine` registers its plug-in in the process-wide extension
    registries; put them back after, as `test_underwriting_basis_v1` does, so a later test in the
    same worker still sees the registries as core ships them."""
    import gridq.extensions as ext

    for registry in ext._ALL:
        registry._ensure_home()
    snapshots = [r._snapshot() for r in ext._ALL]
    loaded, discovered = dict(ext._loaded), ext._discovered
    try:
        yield
    finally:
        for registry, snapshot in zip(ext._ALL, snapshots, strict=True):
            registry._restore(snapshot)
        ext._loaded.clear()
        ext._loaded.update(loaded)
        ext._discovered = discovered


@pytest.mark.usefixtures("_registries_restored")
def test_the_baseline_and_underwriting_engines_stamp_their_results(
    make_site: Callable[..., SiteModel],
) -> None:
    from gridq.underwriting.basis import UnderwritingBasis
    from gridq.underwriting.engine import UnderwritingEngine

    lib, site = load_assumptions("gb_v0_1"), make_site()
    baseline = BaselineEngine()
    result = baseline.solve(baseline.build_optimisation_request(site, lib))
    assert result.constraint_library == cl.library_ref()
    uw = UnderwritingEngine(
        UnderwritingBasis(
            basis_id="underwriting_basis_v1",
            authorised_by="Olly",
            as_of=datetime(2026, 11, 26).date(),
            evidence_admission="synthetic_fixtures_test_only",
        ),
        engine_factory=lambda _registry: BaselineEngine(),
    )
    assert uw.solve(uw.build_optimisation_request(site, lib)).constraint_library == (
        cl.library_ref()
    )


def test_the_milp_engine_stamps_its_no_spec_result(make_site: Callable[..., SiteModel]) -> None:
    """The no-spec branch (no observed peak) builds its own result: it is stamped too."""
    from gridq.optimisation import MilpEngine

    lib = load_assumptions("gb_v0_1")
    engine = MilpEngine()
    result = engine.solve(engine.build_optimisation_request(make_site(with_max_import=False), lib))
    assert result.selected_power_kw is None
    assert result.constraint_library == cl.library_ref()


def test_the_demo_no_spec_result_is_stamped() -> None:
    from gridq.app.demo import DemoEngine

    request = OptimisationRequest.model_construct(request_id="req_demo")
    result = DemoEngine._no_spec(
        DemoEngine.__new__(DemoEngine), request, SolverStatus.INSUFFICIENT_DATA, "no data"
    )
    assert result.constraint_library == cl.library_ref()


class _UnstampedEngine:
    """Conforms in every other respect: the baseline's result with the stamp removed."""

    def __init__(self) -> None:
        self._inner = BaselineEngine()

    def build_optimisation_request(
        self, site_model: SiteModel, assumptions: AssumptionLibrary, scenario: str | None = None
    ) -> OptimisationRequest:
        return self._inner.build_optimisation_request(site_model, assumptions, scenario)

    def solve(self, request: OptimisationRequest) -> OptimisationResult:
        return self._inner.solve(request).model_copy(update={"constraint_library": None})


def test_the_conformance_bar_refuses_an_engine_that_does_not_stamp() -> None:
    lib = load_assumptions("gb_v0_1")
    report = check_engine_conformance(_UnstampedEngine(), assumptions=lib)
    failed = {r.check for r in report.failures}
    assert failed and all(c.endswith("provenance.constraint_library_stamped") for c in failed)
    assert check_engine_conformance(BaselineEngine(), assumptions=lib).ok


def test_the_stamp_never_moves_the_run_identity(make_site: Callable[..., SiteModel]) -> None:
    """D178: the stamp names code (pinned by ``code_version`` via ``run_id``), so a
    stamped and an unstamped result of the same run share ``input_hash`` and ``quote_id``; and an
    unstamped (3.1.0) result dumps without the member, byte for byte as before."""
    lib, site = load_assumptions("gb_v0_1"), make_site()
    plain, stamped = _hand_result(), _hand_result(constraint_library=cl.library_ref())
    assert "constraint_library" not in plain.model_dump(mode="json")
    a = assemble_quote_record(site, plain, None, lib)
    b = assemble_quote_record(site, stamped, None, lib)
    assert (a.input_hash, a.quote_id) == (b.input_hash, b.quote_id)
    carried = b.model_dump(mode="json")["optimisation_result"]["constraint_library"]
    assert carried == cl.library_ref().model_dump(mode="json")
    other = cl.library_ref().model_copy(update={"library_hash": "sha256:" + "1" * 64})
    c = assemble_quote_record(site, _hand_result(constraint_library=other), None, lib)
    assert c.input_hash == a.input_hash
