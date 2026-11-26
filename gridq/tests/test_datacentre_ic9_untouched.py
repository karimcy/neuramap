"""WP-SITE-07 acceptance 2: with no declared envelope, IT load is untouched in every model path.

The proof is hash and array equality across the three paths the load model has (half-hourly,
one-minute, bands), in three ways:

- **Three runs agree.** ``workload_flex`` omitted, the sentinel `NO_ENVELOPE_DECLARED` passed
  explicitly, and a synthetic IC-9 declaration: the same series hash, manifest, inputs hash,
  members, five components, expected series, bands, band intervals, aggregates, attribution,
  flags and warnings, and the same one-minute window (hash, members, IT members, bands). The plant
  model `evaluate` is called with the same arguments in each.
- **Against the code before IC-9.** The ensemble built by the WP-SITE-01 tree (commit
  ``d155e91``, read with ``git archive`` and run in a fresh interpreter) has the same series and
  window hashes as today's run with a declaration (skipped only if the commit is not in the
  clone, or the method or sampling scheme has since moved by decision).
- **Files and structure.** The four WP-SITE-01 files are byte-identical with a declaration; the
  engine, the plant model, the inputs and the baseline builder never name it (AST); and seven
  monkeypatched leaks (mutants M1 to M7) each fail the equality check, which a no-op control
  passes.

The overlay (`gridq.datacentre.turndown`) is checked too: labelled conditional, read through the
contract's own readers (`declared_at_local`, `stack_at`), on request only, and refused when nothing
is declared.

Every declaration here is a SYNTHETIC schema-test fixture (``tests/contracts/fixtures/ic9/``),
re-sited to the test site; no kW, price or SLA value is an operator's.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests._datacentre_support import (  # noqa: F401  (fixture)
    DRAWS,
    SEED,
    isolated_registries,
    make_inputs,
)

from gridq.contracts.compute_turndown import (
    ComputeTurndownDeclaration,
    NotInForce,
    declared_at_local,
    resolved_hash_of,
    stack_at,
)
from gridq.datacentre import derive, synthesiser
from gridq.datacentre.derive import derive_data_centre_half_hourly
from gridq.datacentre.synthesiser import METHOD_VERSION, SCHEME_ID, Synthesis, synthesise
from gridq.datacentre.turndown import (
    ALLOCATION,
    FIRMNESS,
    turndown_overlay,
    turndown_overlay_minutes,
)
from gridq.datacentre.workload_flex import (
    NO_ENVELOPE_DECLARED,
    NoEnvelopeDeclared,
    WorkloadFlexBinding,
)

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "gridq" / "datacentre"
FIXTURES_IC9 = REPO / "tests" / "contracts" / "fixtures" / "ic9"
CAMPUS = FIXTURES_IC9 / "ic9_declaration_gpu_campus.synthetic.json"
SUCCESSOR = FIXTURES_IC9 / "ic9_declaration_gpu_campus_successor.synthetic.json"
#: The WP-SITE-01 tip: the load model as it was before IC-9 existed.
PRE_IC9 = "d155e91"
WINDOW = (datetime(2027, 3, 8, 6, 0), datetime(2027, 3, 8, 9, 0))
#: The test site is about 1.6 MW of IT; the fixture's 2 MW retained floor would leave nothing to
#: turn down, so the re-sited copy retains 200 kW (a synthetic test value, re-sealed).
TEST_FLOOR_KW = 200
#: IC-9 requires a synthetic record to name a ``SYNTHETIC-`` site, so the test site takes that id.
SITE_ID = "SYNTHETIC-DC-GPU-CAMPUS"


def _inputs() -> Any:
    return make_inputs(site_id=SITE_ID)


def _site(make_site: Callable[..., Any]) -> Any:
    return make_site(with_max_import=False).model_copy(update={"site_id": SITE_ID})


# `derive` loads the plug-in: restore the chassis registries after each test, as the registration
# tests do, so `data_centre` never stays registered in an xdist worker.
pytestmark = pytest.mark.usefixtures("isolated_registries")


def _resited(path: Path, *, site_id: str = SITE_ID) -> ComputeTurndownDeclaration:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data["site_id"] = site_id
    if isinstance(data["it_reference"].get("retained_it_floor_kw"), int):
        data["it_reference"]["retained_it_floor_kw"] = TEST_FLOOR_KW
    data["resolved_hash"] = resolved_hash_of(data)
    return ComputeTurndownDeclaration.model_validate_json(json.dumps(data))


@pytest.fixture(scope="module")
def declaration() -> ComputeTurndownDeclaration:
    return _resited(CAMPUS)


@pytest.fixture(scope="module")
def runs(declaration: ComputeTurndownDeclaration) -> tuple[Synthesis, Synthesis, Synthesis]:
    inputs = _inputs()
    omitted = synthesise(inputs, seed=SEED, draws=DRAWS)
    explicit = synthesise(inputs, seed=SEED, draws=DRAWS, workload_flex=NO_ENVELOPE_DECLARED)
    declared = synthesise(inputs, seed=SEED, draws=DRAWS, workload_flex=declaration)
    return omitted, explicit, declared


def _same(a: np.ndarray, b: np.ndarray, what: str) -> None:
    assert a.shape == b.shape and np.array_equal(a, b), f"{what} differs"


def assert_baseline_untouched(base: Synthesis, other: Synthesis) -> None:
    """Every series, band, statistic and hash of ``other`` is ``base``'s, in every path."""
    assert other.series_hash == base.series_hash, "series hash differs"
    assert other.manifest == base.manifest, "manifest differs"
    assert other.inputs_hash == base.inputs_hash, "inputs hash differs"
    assert (other.seed, other.draws, other.timestamps) == (base.seed, base.draws, base.timestamps)
    # Half-hourly: members and the five components, IT first.
    _same(other.members_kw, base.members_kw, "members")
    assert list(other.components_kw) == list(base.components_kw)
    for name in base.components_kw:
        _same(other.components_kw[name], base.components_kw[name], f"component {name}")
    # Bands: the expected series, every band and each edge's simulation-error interval.
    _same(other.expected_kw, base.expected_kw, "expected")
    assert list(other.bands_kw) == list(base.bands_kw)
    for level in base.bands_kw:
        _same(other.bands_kw[level], base.bands_kw[level], f"band {level}")
        for k in (0, 1):
            _same(other.band_ci_kw[level][k], base.band_ci_kw[level][k], f"band CI {level}")
    assert other.medoid_draw == base.medoid_draw
    assert other.aggregates == base.aggregates, "aggregates differ"
    assert other.attribution == base.attribution, "attribution differs"
    assert other.flags == base.flags, "flags differ"
    assert other.warnings == base.warnings, "warnings differ"
    assert (other.dimensions, other.citations) == (base.dimensions, base.citations)
    # One-minute: a window, every draw.
    wa = base.one_minute(*WINDOW, jensen_gap_flag_kw=None)
    wb = other.one_minute(*WINDOW, jensen_gap_flag_kw=None)
    assert wb.window_hash == wa.window_hash, "one-minute window hash differs"
    _same(wb.members_kw, wa.members_kw, "one-minute members")
    _same(wb.it_members_kw, wa.it_members_kw, "one-minute IT members")
    _same(wb.expected_kw, wa.expected_kw, "one-minute expected")
    for level in wa.bands_kw:
        _same(wb.bands_kw[level], wa.bands_kw[level], f"one-minute band {level}")


# --- three runs agree ---------------------------------------------------------------------------


def test_omitted_sentinel_and_declared_are_the_same_ensemble(runs) -> None:  # type: ignore[no-untyped-def]
    omitted, explicit, declared = runs
    assert_baseline_untouched(omitted, explicit)
    assert_baseline_untouched(omitted, declared)
    assert omitted.workload_flex is NO_ENVELOPE_DECLARED
    assert explicit.workload_flex is NO_ENVELOPE_DECLARED
    assert repr(NO_ENVELOPE_DECLARED) == "NO_ENVELOPE_DECLARED"
    assert isinstance(NO_ENVELOPE_DECLARED, NoEnvelopeDeclared)


def test_a_declaration_is_bound_beside_the_ensemble(runs, declaration) -> None:  # type: ignore[no-untyped-def]
    _, _, declared = runs
    binding = declared.workload_flex
    assert isinstance(binding, WorkloadFlexBinding)
    assert binding.declaration is declaration
    assert binding.declaration_hash == declaration.content_hash()
    assert binding.contract_version == declaration.contract_version == "1.0.0"
    # The campus is in force over the whole ten-day March horizon; its weekday cells offer.
    assert binding.intervals_in_force == len(declared.timestamps) == 480
    assert 0 < binding.intervals_offered <= binding.intervals_in_force
    assert any("CONDITIONAL" in n for n in binding.notes)
    assert any("SYNTHETIC" in n for n in binding.notes)
    # The binding hash moves with the declaration, never the series hash.
    other = _resited(CAMPUS).model_copy()
    assert other == declaration
    successor = synthesise(
        _inputs(), seed=SEED, draws=DRAWS, workload_flex=_resited(SUCCESSOR)
    ).workload_flex
    assert isinstance(successor, WorkloadFlexBinding)
    assert successor.binding_hash != binding.binding_hash


def test_a_declaration_not_in_force_is_bound_and_says_so(runs) -> None:  # type: ignore[no-untyped-def]
    omitted, _, _ = runs
    late = synthesise(_inputs(), seed=SEED, draws=DRAWS, workload_flex=_resited(SUCCESSOR))
    assert_baseline_untouched(omitted, late)
    binding = late.workload_flex
    assert isinstance(binding, WorkloadFlexBinding)
    assert (binding.intervals_in_force, binding.intervals_offered) == (0, 0)
    assert any("not in force" in n for n in binding.notes)
    overlay = turndown_overlay(late, duration_min=60)
    assert not overlay.it_deliverable_kw.any()


def test_the_plant_model_sees_the_same_arguments(  # type: ignore[no-untyped-def]
    monkeypatch, declaration
) -> None:
    """A spy on `evaluate`: the declared run makes the same calls, with the same arrays."""
    real = synthesiser.evaluate
    calls: list[list[Any]] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen = [type(a).__name__ for a in args]
        for key in sorted(kwargs):
            value = kwargs[key]
            assert not isinstance(value, (ComputeTurndownDeclaration, WorkloadFlexBinding))
            seen.append((key, value.tobytes() if isinstance(value, np.ndarray) else repr(value)))
        calls[-1].append(seen)
        return real(*args, **kwargs)

    monkeypatch.setattr(synthesiser, "evaluate", spy)
    for flex in (NO_ENVELOPE_DECLARED, declaration):
        calls.append([])
        synthesise(_inputs(), seed=SEED, draws=DRAWS, workload_flex=flex)
    assert calls[0] and calls[1] == calls[0]


def test_the_pre_ic9_tree_builds_the_same_hashes(runs, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The WP-SITE-01 tree, before IC-9 existed, gives the series and window hashes the declared
    run gives today. Read from git, so the oracle is the code that was merged, not a recollection.
    """
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{PRE_IC9}^{{commit}}"], cwd=REPO, capture_output=True
    )
    if probe.returncode != 0:
        pytest.skip(f"commit {PRE_IC9} is not in this clone")
    tree = tmp_path / "pre_ic9"
    tree.mkdir()
    archive = subprocess.run(
        ["git", "archive", PRE_IC9, "src", "tests/_datacentre_support.py"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
    script = textwrap.dedent(
        f"""
        import json, sys
        from datetime import datetime
        sys.path[:0] = [{str(tree / "src")!r}, {str(tree)!r}]
        import gridq
        assert gridq.__file__.startswith({str(tree)!r}), gridq.__file__
        from tests._datacentre_support import make_inputs
        from gridq.datacentre import synthesise
        s = synthesise(make_inputs(site_id={SITE_ID!r}), seed={SEED}, draws={DRAWS})
        w = s.one_minute(datetime(*{WINDOW[0].timetuple()[:5]!r}),
                         datetime(*{WINDOW[1].timetuple()[:5]!r}), jensen_gap_flag_kw=None)
        print(json.dumps({{"series": s.series_hash, "window": w.window_hash,
                          "method": s.manifest["method_version"], "scheme": s.manifest["scheme"]}}))
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        cwd=tmp_path,
    )
    old = json.loads(out.stdout.strip().splitlines()[-1])
    if (old["method"], old["scheme"]) != (METHOD_VERSION, SCHEME_ID):
        pytest.skip("the method or sampling scheme has moved since WP-SITE-01, by decision")
    omitted, _, declared = runs
    window = declared.one_minute(*WINDOW, jensen_gap_flag_kw=None)
    assert old["series"] == declared.series_hash == omitted.series_hash
    assert old["window"] == window.window_hash


# --- the WS-SITE route and its files ------------------------------------------------------------


def test_the_four_site01_files_are_byte_identical_with_a_declaration(  # type: ignore[no-untyped-def]
    tmp_path: Path, make_site, declaration
) -> None:
    plain = derive_data_centre_half_hourly(
        _site(make_site),
        _inputs(),
        seed=SEED,
        draws=DRAWS,
        out_dir=tmp_path / "plain",
        release=None,
    )
    flexed = derive_data_centre_half_hourly(
        _site(make_site),
        _inputs(),
        seed=SEED,
        draws=DRAWS,
        out_dir=tmp_path / "flexed",
        release=None,
        workload_flex=declaration,
        overlay_durations_min=(30, 240),
    )
    pairs = zip(
        (plain.files.expected_csv, plain.files.bands_csv),
        (flexed.files.expected_csv, flexed.files.bands_csv),
        strict=True,
    )
    for a, b in [
        *pairs,
        (plain.files.members_npy, flexed.files.members_npy),
        (plain.files.manifest_json, flexed.files.manifest_json),
    ]:
        assert a.name == b.name and a.read_bytes() == b.read_bytes(), a.name
    four = (
        plain.files.expected_csv,
        plain.files.bands_csv,
        plain.files.members_npy,
        plain.files.manifest_json,
    )
    assert {p.name for p in (tmp_path / "plain").iterdir()} == {p.name for p in four}
    # The stamp (load data and derived records) is the same, but for the directory it names.
    swap = str(tmp_path / "flexed"), str(tmp_path / "plain")
    assert flexed.record == plain.record
    assert flexed.site.derived_assumptions == plain.site.derived_assumptions
    assert (
        flexed.site.load_data.model_dump_json().replace(*swap)
        == plain.site.load_data.model_dump_json()
    )
    assert plain.workload_flex is NO_ENVELOPE_DECLARED and plain.workload_flex_files == ()
    assert isinstance(flexed.workload_flex, WorkloadFlexBinding)

    # The declaration only adds files: the binding, and one overlay per stated duration.
    added = {p.name for p in (tmp_path / "flexed").iterdir()} - {
        p.name for p in (tmp_path / "plain").iterdir()
    }
    assert added == {p.name for p in flexed.workload_flex_files} and len(added) == 3
    bound = json.loads(flexed.workload_flex_files[0].read_text(encoding="utf-8"))
    assert bound["series_hash"] == plain.synthesis.series_hash
    assert bound["binding_hash"] == flexed.workload_flex.binding_hash
    assert bound["firmness"] == "conditional"
    assert ComputeTurndownDeclaration.model_validate_json(json.dumps(bound["declaration"])) == (
        declaration
    )
    tag = declaration.content_hash().removeprefix("sha256:")[:16]
    assert flexed.workload_flex_files[0].name == f"{SITE_ID}_dc_workload_flex_{tag}.json"
    for duration, path in zip((30, 240), flexed.workload_flex_files[1:], strict=True):
        assert path.name.startswith(f"{SITE_ID}_dc_turndown_{duration}min_")
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith(f"# IC-9 turn-down, call of {duration} min")
        assert "firmness conditional" in lines[0]
        assert lines[1].split(",")[:2] == ["timestamp", "it_deliverable_expected_kw"]
        assert len(lines) == 2 + len(plain.synthesis.timestamps)


def test_no_declaration_writes_no_ic9_file_and_overlays_need_one(  # type: ignore[no-untyped-def]
    tmp_path: Path, make_site
) -> None:
    with pytest.raises(ValueError, match="needs a declared IC-9 turn-down"):
        derive_data_centre_half_hourly(
            _site(make_site),
            _inputs(),
            seed=SEED,
            draws=DRAWS,
            out_dir=tmp_path,
            release=None,
            overlay_durations_min=(30,),
        )
    assert not tmp_path.exists() or not list(tmp_path.iterdir())
    synthesis = synthesise(_inputs(), seed=SEED, draws=DRAWS)
    with pytest.raises(ValueError, match="no IC-9 turn-down is declared"):
        derive.write_workload_flex(synthesis, tmp_path, overlay_durations_min=())


# --- structure: the declaration cannot reach the model ------------------------------------------

_IC9_NAMES = {
    "workload_flex",
    "WorkloadFlex",
    "WorkloadFlexBinding",
    "NO_ENVELOPE_DECLARED",
    "NoEnvelopeDeclared",
    "ComputeTurndownDeclaration",
    "compute_turndown",
    "bind",
    "turndown",
}


def _identifiers(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.arg):
            found.add(sub.arg)
        elif isinstance(sub, ast.keyword) and sub.arg:
            found.add(sub.arg)
        elif isinstance(sub, ast.ImportFrom):
            found.add(sub.module or "")
            found.update(a.name for a in sub.names)
        elif isinstance(sub, ast.Import):
            found.update(a.name for a in sub.names)
    return {part for name in found for part in name.split(".")}


def test_the_engine_the_plant_and_the_inputs_never_name_it() -> None:
    for name in ("model.py", "inputs.py", "distributions.py", "presets.py"):
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        assert not _identifiers(tree) & _IC9_NAMES, name
    tree = ast.parse((PACKAGE / "synthesiser.py").read_text(encoding="utf-8"))
    tops = {n.name: n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    for name in ("_Engine", "_synthesise_baseline", "_warnings", "_attribute"):
        assert not _identifiers(tops[name]) & _IC9_NAMES, name
    # The baseline takes exactly what it took before IC-9: inputs, seed and draws.
    args = tops["_synthesise_baseline"].args
    assert [a.arg for a in [*args.args, *args.kwonlyargs]] == ["inputs", "seed", "draws"]


# --- mutants: each leak of a declaration into the ensemble is caught ----------------------------

Mutate = Callable[[Synthesis], Synthesis]


def _shift_it(s: Synthesis) -> Synthesis:
    comps = dict(s.components_kw)
    comps["it"] = comps["it"] * (1.0 - 1e-3)
    return replace(s, components_kw=comps)


def _leak_engine(s: Synthesis) -> Synthesis:
    return replace(s, _engine=replace(s._engine, seed=s._engine.seed + 1))


MUTANTS: dict[str, Mutate] = {
    "M1 members scaled by a turn-down": lambda s: replace(s, members_kw=s.members_kw * 0.99),
    "M2 IT component reduced": _shift_it,
    "M3 a band moved": lambda s: replace(
        s, bands_kw={**s.bands_kw, "P50": s.bands_kw["P50"] - 1.0}
    ),
    "M4 series hash re-taken over the declaration": lambda s: replace(
        s, series_hash=s.series_hash[:-1] + ("0" if s.series_hash[-1] != "0" else "1")
    ),
    "M5 manifest names the declaration": lambda s: replace(
        s, manifest={**s.manifest, "workload_flex": "declared"}
    ),
    "M6 a flag or warning for the declaration": lambda s: replace(
        s, warnings=(*s.warnings, "IC-9 declared")
    ),
    "M7 the one-minute path reseeded": _leak_engine,
}


def _declared_with(monkeypatch, declaration, mutate: Mutate) -> Synthesis:  # type: ignore[no-untyped-def]
    real = synthesiser.replace

    def leaky(obj: Any, **changes: Any) -> Any:
        out = real(obj, **changes)
        return mutate(out) if "workload_flex" in changes else out

    monkeypatch.setattr(synthesiser, "replace", leaky)
    return synthesise(_inputs(), seed=SEED, draws=DRAWS, workload_flex=declaration)


def test_mutation_control_passes(runs, monkeypatch, declaration) -> None:  # type: ignore[no-untyped-def]
    omitted, _, _ = runs
    assert_baseline_untouched(omitted, _declared_with(monkeypatch, declaration, lambda s: s))


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_leak_is_caught(runs, monkeypatch, declaration, name: str) -> None:  # type: ignore[no-untyped-def]
    omitted, _, _ = runs
    leaked = _declared_with(monkeypatch, declaration, MUTANTS[name])
    with pytest.raises(AssertionError):
        assert_baseline_untouched(omitted, leaked)


# --- the overlay: conditional, on request, through the contract's readers -----------------------


@pytest.fixture(scope="module")
def overlay(runs):  # type: ignore[no-untyped-def]
    return turndown_overlay(runs[2], duration_min=60)


def test_the_overlay_is_conditional_and_leaves_the_ensemble(runs, overlay) -> None:  # type: ignore[no-untyped-def]
    omitted, _, declared = runs
    assert overlay.firmness == FIRMNESS == "conditional"
    assert overlay.allocation == ALLOCATION
    assert any("CONDITIONAL" in n for n in overlay.notes)
    assert overlay.binding_hash == declared.workload_flex.binding_hash
    assert overlay.it_deliverable_kw.any(), "the test floor leaves something to turn down"
    assert set(overlay.expected_kw) == {"it_deliverable", "meter_instant", "meter_settled"}
    for q in overlay.expected_kw:
        assert overlay.bands_kw[q]["P05"].shape == overlay.expected_kw[q].shape
    # On request only: computing it changed nothing, and it is reproducible.
    assert_baseline_untouched(omitted, declared)
    again = turndown_overlay(declared, duration_min=60)
    assert again.overlay_hash == overlay.overlay_hash
    assert turndown_overlay(declared, duration_min=30).overlay_hash != overlay.overlay_hash


def test_the_overlay_reads_the_contract(runs, overlay, declaration) -> None:  # type: ignore[no-untyped-def]
    """Each cell is `stack_at` at the draw's IT load floored to a whole kW, at the half-hour
    `declared_at_local` reads; never more than the IT load; the layers sum to the total."""
    _, _, declared = runs
    it = declared.components_kw["it"]
    layered = sum(overlay.layers_kw.values())
    assert np.array_equal(layered, overlay.it_deliverable_kw)
    assert (overlay.it_deliverable_kw >= 0).all()
    assert (overlay.it_deliverable_kw <= it + 1e-9).all()
    checked = 0
    for j in range(0, len(declared.timestamps), 7):
        t = declared.timestamps[j]
        answer = declared_at_local(declaration, t.date(), (t.hour * 60 + t.minute) // 30)
        for i in (0, DRAWS // 2, DRAWS - 1):
            if isinstance(answer, NotInForce):
                assert overlay.it_deliverable_kw[i, j] == 0
                continue
            load = int(np.floor(np.round(it[i, j], 6)))
            stack = stack_at(declaration, answer.instant, 60, load)
            assert overlay.it_deliverable_kw[i, j] == sum(x.kw for x in stack.layers)
            checked += 1
    assert checked > 0


def test_the_overlay_needs_a_declaration_and_a_duration(runs) -> None:  # type: ignore[no-untyped-def]
    omitted, _, declared = runs
    with pytest.raises(ValueError, match="no IC-9 turn-down is declared"):
        turndown_overlay(omitted, duration_min=60)
    with pytest.raises(ValueError, match="no IC-9 turn-down is declared"):
        turndown_overlay_minutes(omitted, *WINDOW, duration_min=60)
    for bad in (0, 1441, True, 30.0):
        with pytest.raises(ValueError, match="duration_min"):
            turndown_overlay(declared, duration_min=bad)  # type: ignore[arg-type]


def test_a_declaration_for_another_site_or_of_another_type_is_refused() -> None:
    with pytest.raises(ValueError, match="is for site 'SYNTHETIC-DC-ELSEWHERE'"):
        synthesise(
            _inputs(),
            seed=SEED,
            draws=DRAWS,
            workload_flex=_resited(CAMPUS, site_id="SYNTHETIC-DC-ELSEWHERE"),
        )
    for bad in (None, {"contract": "IC-9"}, "no_envelope_declared"):
        with pytest.raises(TypeError, match="ComputeTurndownDeclaration or NO_ENVELOPE_DECLARED"):
            synthesise(_inputs(), seed=SEED, draws=DRAWS, workload_flex=bad)  # type: ignore[arg-type]


def test_the_one_minute_overlay_reads_each_minute(runs, declaration) -> None:  # type: ignore[no-untyped-def]
    _, _, declared = runs
    minutes = turndown_overlay_minutes(declared, *WINDOW, duration_min=30)
    window = declared.one_minute(*WINDOW, jensen_gap_flag_kw=None)
    assert minutes.it_deliverable_kw.shape == window.it_members_kw.shape == (DRAWS, 180)
    assert minutes.firmness == "conditional"
    assert any("half-hourly in this version" in n for n in minutes.notes)
    for k in (0, 29, 30, 95, 179):
        t = WINDOW[0].replace(hour=WINDOW[0].hour + k // 60, minute=k % 60)
        answer = declared_at_local(declaration, t.date(), (t.hour * 60 + t.minute) // 30)
        for i in (0, DRAWS - 1):
            load = int(np.floor(np.round(window.it_members_kw[i, k], 6)))
            want = (
                0
                if isinstance(answer, NotInForce)
                else sum(x.kw for x in stack_at(declaration, answer.instant, 30, load).layers)
            )
            assert minutes.it_deliverable_kw[i, k] == want
    again = turndown_overlay_minutes(declared, *WINDOW, duration_min=30)
    assert again.overlay_hash == minutes.overlay_hash
