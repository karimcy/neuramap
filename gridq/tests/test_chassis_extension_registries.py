"""WP-CORE-03 acceptance: the chassis extension registries (IC-12 2.0.0, `gridq.extensions`).

What these tests hold, by acceptance criterion:

2. **A new archetype, study basis or constraint is registered without editing a CORE-owned
   path.** `tests/plugins/dummy_chassis_plugin` registers one of each (and a resource). It is
   loaded both ways a real workstream would: explicitly, and through a ``gridq.extensions`` entry
   point in a fresh interpreter. Each registered item is then *used* on the production route it
   extends. `test_registering_touches_no_core_owned_path` fails if a CORE-owned path names the
   plugin or any id it registers, which is what "needing to edit CORE" looks like in a diff.
3. **The study basis still enters ``input_hash``.** `test_a_different_basis_is_a_different_
   input_hash` covers no basis, both built-in bases, and a plugin's basis.

The rest pins the registry rules that make an open extension point safe for a deterministic
engine: an id is never overridden, a failing plugin is rolled back and raised, and loading a
plugin moves no existing identity and no frozen schema.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.test_all_streams_study_basis import all_streams
from tests.test_study_basis_schema import study

import gridq.extensions as ext
from gridq.assumptions import load_assumptions
from gridq.contracts import __main__ as contracts_cli
from gridq.dispatch import dispatch_constraints_for
from gridq.extensions import (
    DuplicateRegistration,
    ExtensionHost,
    ExtensionLoadError,
    RegistryError,
    ResourceKind,
    ResourceSpec,
    UnknownExtension,
)
from gridq.ingestion.hh_synthesis import (
    PROFILE_LIBRARY,
    SynthesisError,
    derive_half_hourly,
    synthesise_half_hourly,
)
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import _canonical_run_input, assemble_quote_record
from gridq.schemas import (
    STUDY_BASIS_CLASSES,
    BankableStatus,
    ImplementationLevel,
    MarketStream,
    OptimisationResult,
    SolverMetadata,
    SolverStatus,
    StudyBasis,
    study_basis_label,
)

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "tests" / "plugins"
PLUGIN = "dummy_chassis_plugin"

#: Every CORE-owned path of the GridQ repository: the entries marked "owner WS-CORE" under
#: ``layout`` in the Atlas roadmap (``atlas/roadmap/roadmap.yaml``, read 2026-11-26), with the
#: ``uk/`` prefix removed, plus ``src/gridq/extensions/`` which WP-CORE-03 adds and CORE owns.
CORE_OWNED_PATHS = (
    "src/gridq/schemas",
    "src/gridq/ingestion",
    "src/gridq/quotes",
    "src/gridq/provenance",
    "src/gridq/assumptions",
    "src/gridq/engine",
    "src/gridq/outputs",
    "src/gridq/scenarios",
    "src/gridq/dispatch",
    "src/gridq/contracts",
    "src/gridq/dev",
    "src/gridq/telemetry",
    "src/gridq/portfolio_requirements",
    "src/gridq/extensions",
    "assumptions",
    "runs",
    "docs",
    "scripts",
)


@pytest.fixture
def isolated() -> Iterator[None]:
    """Run a test with the registries as CORE ships them, and put them back afterwards."""
    for registry in ext._ALL:
        registry._ensure_home()
    registries = [r._snapshot() for r in ext._ALL]
    loaded, discovered = dict(ext._loaded), ext._discovered
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        yield
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        for registry, snapshot in zip(ext._ALL, registries, strict=True):
            registry._restore(snapshot)
        ext._loaded.clear()
        ext._loaded.update(loaded)
        ext._discovered = discovered


@pytest.fixture
def plugin(isolated: None):  # type: ignore[no-untyped-def]
    """The dummy plugin, loaded explicitly. Returns its module."""
    loaded = ext.load_plugin(PLUGIN)
    assert (loaded.owner, loaded.version) == (PLUGIN, "0.0.1")
    return sys.modules[PLUGIN]


@pytest.fixture(scope="module")
def parts() -> tuple:
    """A minimal (site, result, assumptions) triple: no solve is needed to pin a hash shape."""
    from tests.conftest import _make_site

    lib = load_assumptions("gb_v0_1")
    result = OptimisationResult(
        result_id="res_x",
        request_id="req_x",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.NOT_BANKABLE,
        solver_status=SolverStatus.OPTIMAL,
        solver_metadata=SolverMetadata(
            seed=0, assumption_version=lib.metadata.version, code_version="c", model_version="m"
        ),
    )
    return _make_site(), result, lib


# ------------------------------------------------------------------ what CORE ships


def test_the_built_ins_are_the_chassis_as_it_was(isolated: None) -> None:
    assert ext.load_archetypes.ids() == tuple(sorted(PROFILE_LIBRARY))
    assert ext.study_bases.ids() == tuple(sorted(STUDY_BASIS_CLASSES))
    for basis_id, cls in STUDY_BASIS_CLASSES.items():
        assert ext.study_bases.get(basis_id) is cls
    assert ext.resources.ids() == ("battery_storage",)
    assert ext.resources.get("battery_storage").kind is ResourceKind.STORAGE
    assert ext.constraints.ids() == ()
    for registry in ext._ALL:
        assert all(r.builtin and r.owner == "gridq" for r in registry.registrations())


# ------------------------------------------------------------------ acceptance 2


def test_the_plugin_registers_one_of_each(plugin) -> None:  # type: ignore[no-untyped-def]
    assert ext.load_archetypes.get(plugin.ARCHETYPE_ID) is plugin.FLAT_ARCHETYPE
    assert ext.study_bases.get(plugin.BASIS_ID) is plugin.DummyPluginBasis
    assert ext.constraints.get(plugin.CONSTRAINT_ID) is plugin.IMPORT_CAP
    assert ext.resources.get(plugin.RESOURCE_ID) is plugin.FLEXIBLE_LOAD
    for registry, key in (
        (ext.load_archetypes, plugin.ARCHETYPE_ID),
        (ext.study_bases, plugin.BASIS_ID),
        (ext.constraints, plugin.CONSTRAINT_ID),
        (ext.resources, plugin.RESOURCE_ID),
    ):
        registration = registry.registration(key)
        assert (registration.owner, registration.version) == (PLUGIN, "0.0.1")
        assert not registration.builtin
    # Beside the built-ins, never instead of them.
    assert set(PROFILE_LIBRARY) < set(ext.load_archetypes.ids())
    assert set(STUDY_BASIS_CLASSES) < set(ext.study_bases.ids())
    assert ext.resources.ids() == ("battery_storage", plugin.RESOURCE_ID)


def test_the_registered_archetype_synthesises_a_series(  # type: ignore[no-untyped-def]
    plugin, tmp_path: Path, make_site
) -> None:
    """The archetype is reachable by id on the production route: `derive_half_hourly`."""
    site = make_site(with_max_import=False)
    derived = derive_half_hourly(site, out_dir=tmp_path, profile=plugin.ARCHETYPE_ID, year=2025)
    assert derived.synthesis.profile.profile_id == plugin.ARCHETYPE_ID
    assert derived.site.load_data.half_hourly_reference_profile is not None
    assert plugin.ARCHETYPE_ID in derived.site.load_data.half_hourly_reference_profile
    # ...and on the lower-level entry too, which used to index the built-in dict directly.
    direct = synthesise_half_hourly(
        site_id="s", profile=plugin.ARCHETYPE_ID, year=2025, out_dir=tmp_path, annual_kwh=17520.0
    )
    assert direct.profile is plugin.FLAT_ARCHETYPE


def test_an_unregistered_archetype_is_still_refused(isolated: None, tmp_path: Path) -> None:
    with pytest.raises(SynthesisError, match="unknown reference profile 'dummy_plugin_flat'"):
        synthesise_half_hourly(
            site_id="s", profile="dummy_plugin_flat", year=2025, out_dir=tmp_path, annual_kwh=1.0
        )


def test_the_registered_basis_reaches_the_record_and_its_banner(  # type: ignore[no-untyped-def]
    plugin, parts: tuple
) -> None:
    """The basis is reachable on the production route: `assemble_quote_record`, then the label."""
    site, result, lib = parts
    basis = plugin.DummyPluginBasis(basis_id=plugin.BASIS_ID, authorised_by="a test")
    quote = assemble_quote_record(site, result, None, lib, study_basis=basis)
    assert quote.study_basis_id == plugin.BASIS_ID
    assert study_basis_label(quote.study_basis_id) == plugin.DummyPluginBasis.LABEL
    assert quote.input_hash != assemble_quote_record(site, result, None, lib).input_hash


def test_the_registered_constraint_reaches_the_dispatch_problem(  # type: ignore[no-untyped-def]
    plugin, parts: tuple
) -> None:
    """The constraint is reachable on the production seam: `dispatch_constraints_for`."""
    site, _, lib = parts
    streams = [MarketStream.TARIFF_ARBITRAGE, MarketStream.DUOS_RED_BAND_AVOIDANCE]
    before = dispatch_constraints_for(streams, site, lib)
    named = dispatch_constraints_for(
        streams, site, lib, extension_constraints=[plugin.CONSTRAINT_ID]
    )
    assert named[:-1] == before
    assert named[-1].constraint_id == plugin.CONSTRAINT_ID
    assert named[-1].parameter_source == "connection.mic_kw"
    # Registering alone changes nothing: a run that names no extension gets the old list.
    assert dispatch_constraints_for(streams, site, lib) == before


def test_an_extension_constraint_adds_and_never_replaces(  # type: ignore[no-untyped-def]
    plugin, parts: tuple
) -> None:
    site, _, lib = parts
    with pytest.raises(UnknownExtension, match="not_registered"):
        dispatch_constraints_for([], site, lib, extension_constraints=["not_registered"])
    with pytest.raises(ValueError, match="never replaces one"):
        dispatch_constraints_for(
            [], site, lib, extension_constraints=[plugin.CONSTRAINT_ID, plugin.CONSTRAINT_ID]
        )


def test_the_plugin_loads_through_an_entry_point_in_a_fresh_interpreter(tmp_path: Path) -> None:
    """The normal route: a distribution's entry point, with no call into GridQ to load it.

    A ``.dist-info`` beside nothing but its metadata stands for the installed plugin
    distribution. GridQ finds it on the first registry read that misses, and stamps the
    registration with the distribution's own name and version.
    """
    info = tmp_path / "dummy_chassis_plugin-9.9.9.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: dummy-chassis-plugin\nVersion: 9.9.9\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[gridq.extensions]\ndummy = {PLUGIN}:register\n", encoding="utf-8"
    )
    script = textwrap.dedent(
        """
        import json
        import gridq.extensions as ext
        from gridq.schemas import study_basis_label
        assert ext.loaded_plugins() == ()
        label = study_basis_label("dummy_plugin_basis_v1")
        out = {"label": label, "plugins": [(p.owner, p.version) for p in ext.loaded_plugins()]}
        for name in ("load_archetypes", "study_bases", "constraints", "resources"):
            registry = getattr(ext, name)
            out[name] = [(r.key, r.owner, r.version) for r in registry.registrations()
                         if not r.builtin]
        print(json.dumps(out))
        """
    )
    env = {
        "PYTHONPATH": ":".join([str(REPO / "src"), str(PLUGIN_DIR), str(tmp_path)]),
        "PATH": "/usr/bin:/bin",
    }
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    owner = ["dummy-chassis-plugin", "9.9.9"]
    assert out["label"] == "DUMMY PLUGIN BASIS (test fixture) — NOT AN UNDERWRITING OUTPUT"
    assert out["plugins"] == [owner]
    assert out["load_archetypes"] == [["dummy_plugin_flat", *owner]]
    assert out["study_bases"] == [["dummy_plugin_basis_v1", *owner]]
    assert out["constraints"] == [["dummy_plugin_import_cap", *owner]]
    assert out["resources"] == [["dummy_plugin_flexible_load", *owner]]


def _core_files() -> list[Path]:
    files: list[Path] = []
    for rel in CORE_OWNED_PATHS:
        root = REPO / rel
        if root.is_dir():
            files.extend(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return files


def test_registering_touches_no_core_owned_path(plugin) -> None:  # type: ignore[no-untyped-def]
    """Fails if registering the plugin's items needs an edit under any CORE-owned path.

    The plugin is loaded (the fixture), so its four items are registered and the tests above use
    each of them. This test then proves CORE was not edited to make that possible:

    1. the plugin's own files sit under no CORE-owned path;
    2. no code, schema or data file under a CORE-owned path, nor GridQ's own ``pyproject.toml``,
       names the plugin package or any id it registers. An id that had to be added to an enum,
       a ``Literal``, a lookup dict or an entry-point table in CORE would be named there;
    3. the frozen IC-12 schemas still check clean with the plugin loaded, so its ids did not
       have to enter a schema either.

    Close a registry (turn the archetype library back into a fixed dict, type ``study_basis_id``
    as a ``Literal``) and the plugin fixture fails to register; reopen it by naming the plugin in
    CORE and step 2 fails.
    """
    plugin_files = [p for p in (PLUGIN_DIR / PLUGIN).rglob("*.py")]
    assert plugin_files
    core_roots = [REPO / rel for rel in CORE_OWNED_PATHS]
    for path in plugin_files:
        assert not any(root in path.parents for root in core_roots), path

    needles = (
        PLUGIN,
        plugin.ARCHETYPE_ID,
        plugin.BASIS_ID,
        plugin.CONSTRAINT_ID,
        plugin.RESOURCE_ID,
        "DummyPluginBasis",
    )
    named: list[str] = []
    # Prose under docs/ may cite this fixture by name; prose cannot register anything.
    code_and_config = [p for p in _core_files() if (REPO / "docs") not in p.parents]
    for path in [*code_and_config, REPO / "pyproject.toml"]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        named.extend(f"{path.relative_to(REPO)}: {n}" for n in needles if n in text)
    assert named == [], (
        "a CORE-owned path names the dummy plugin or an id it registers, so registering it "
        f"needed a CORE edit: {named}"
    )

    assert contracts_cli.main(["--check"]) == 0


# ------------------------------------------------------------------ acceptance 3


def test_a_different_basis_is_a_different_input_hash(  # type: ignore[no-untyped-def]
    plugin, parts: tuple
) -> None:
    """The study basis enters ``input_hash``: no two bases, and no basis and none, share one."""
    site, result, lib = parts
    bases = {
        "none": None,
        "export_floor": study(),
        "all_streams": all_streams(),
        "plugin": plugin.DummyPluginBasis(basis_id=plugin.BASIS_ID, authorised_by="a test"),
    }
    hashes = {
        name: assemble_quote_record(site, result, None, lib, study_basis=basis).input_hash
        for name, basis in bases.items()
    }
    assert len(set(hashes.values())) == len(hashes), hashes
    # The same basis again is the same hash: the difference above is the basis and nothing else.
    again = assemble_quote_record(site, result, None, lib, study_basis=study()).input_hash
    assert again == hashes["export_floor"]
    # A field of the plugin's basis is an input like any other.
    other = plugin.DummyPluginBasis(basis_id=plugin.BASIS_ID, authorised_by="someone else")
    moved = assemble_quote_record(site, result, None, lib, study_basis=other).input_hash
    assert moved != hashes["plugin"]


def test_the_basis_key_is_in_the_canonical_input_and_removing_it_collides(parts: tuple) -> None:
    """Mutation check on the hash input itself, for the built-in bases."""
    site, result, lib = parts
    a = _canonical_run_input(site, result, None, lib, None, study())
    b = _canonical_run_input(site, result, None, lib, None, all_streams())
    assert a["study_basis"] != b["study_basis"]
    assert hash_json(a) != hash_json(b)
    a.pop("study_basis")
    b.pop("study_basis")
    assert hash_json(a) == hash_json(b)
    # A built-in basis adds no extension pin, so the D46 hash shape is exactly what it was.
    assert "study_basis_extension" not in a


def test_a_plugin_basis_also_pins_its_package_version(  # type: ignore[no-untyped-def]
    plugin, parts: tuple
) -> None:
    site, result, lib = parts
    basis = plugin.DummyPluginBasis(basis_id=plugin.BASIS_ID, authorised_by="a test")
    canonical = _canonical_run_input(site, result, None, lib, None, basis)
    assert canonical["study_basis_extension"] == {"owner": PLUGIN, "version": "0.0.1"}
    registration = ext.study_bases.registration(plugin.BASIS_ID)
    ext.study_bases._restore(
        {
            **ext.study_bases._snapshot(),
            plugin.BASIS_ID: ext.Registration(
                key=plugin.BASIS_ID, item=registration.item, owner=PLUGIN, version="0.0.2"
            ),
        }
    )
    bumped = _canonical_run_input(site, result, None, lib, None, basis)
    assert hash_json(bumped) != hash_json(canonical)


def test_an_unregistered_basis_cannot_reach_a_record(isolated: None, parts: tuple) -> None:
    """A basis no consumer can label is refused at assembly, whoever wrote it."""
    site, result, lib = parts

    class Unregistered(StudyBasis):
        BASIS_ID = "unregistered_basis_v1"

    rogue = Unregistered.model_construct(basis_id="unregistered_basis_v1")
    with pytest.raises(ValueError, match="is not registered"):
        assemble_quote_record(site, result, None, lib, study_basis=rogue)
    impostor = Unregistered.model_construct(basis_id=StudyBasis.BASIS_ID)
    with pytest.raises(ValueError, match="is registered as StudyBasis"):
        assemble_quote_record(site, result, None, lib, study_basis=impostor)


# ------------------------------------------------------------------ registry rules


def test_loading_the_plugin_moves_no_existing_identity(isolated: None, parts: tuple) -> None:
    site, result, lib = parts
    before = {
        "none": assemble_quote_record(site, result, None, lib).input_hash,
        "study": assemble_quote_record(site, result, None, lib, study_basis=study()).input_hash,
    }
    ext.load_plugin(PLUGIN)
    after = {
        "none": assemble_quote_record(site, result, None, lib).input_hash,
        "study": assemble_quote_record(site, result, None, lib, study_basis=study()).input_hash,
    }
    assert after == before


def test_an_id_is_never_overridden(plugin) -> None:  # type: ignore[no-untyped-def]
    host = ExtensionHost(owner="another_package", version="1.0.0")
    with pytest.raises(DuplicateRegistration, match="already registered by gridq"):
        host.register_load_archetype(PROFILE_LIBRARY["office"])
    with pytest.raises(DuplicateRegistration, match="already registered by gridq"):
        host.register_study_basis(StudyBasis)
    with pytest.raises(DuplicateRegistration, match=f"already registered by {PLUGIN}"):
        host.register_constraint(plugin.IMPORT_CAP)
    with pytest.raises(DuplicateRegistration, match="already registered by gridq"):
        host.register_resource(ext.BATTERY_STORAGE)
    assert ext.load_archetypes.get("office") is PROFILE_LIBRARY["office"]


def test_a_plugin_cannot_register_as_core_or_under_a_bad_id(isolated: None) -> None:
    spec = ResourceSpec(
        resource_id="x_resource", label="x", kind=ResourceKind.GENERATION, description="x"
    )
    with pytest.raises(RegistryError, match="reserved for built-ins"):
        ExtensionHost(owner="gridq", version="1").register_resource(spec)
    for bad in ("Bad-Id", "9lives", "", "has space"):
        with pytest.raises(RegistryError, match="not a valid extension id"):
            ext.resources.register(bad, spec, owner="p", version="1")
    with pytest.raises(RegistryError, match="the two must agree"):
        ext.resources.register("other_id", spec, owner="p", version="1")
    with pytest.raises(RegistryError, match="must state an owner and a version"):
        ext.resources.register("x_resource", spec, owner="p", version=" ")


def test_a_basis_weaker_than_the_built_ins_is_refused(plugin) -> None:  # type: ignore[no-untyped-def]
    from typing import ClassVar, Literal

    from pydantic import ConfigDict

    from gridq.schemas import ConfidenceBand, GridQModel

    host = ExtensionHost(owner="another_package", version="1.0.0")

    class Defaulted(GridQModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        BASIS_ID: ClassVar[str] = "defaulted_basis_v1"
        LABEL: ClassVar[str] = "DEFAULTED"
        basis_id: Literal["defaulted_basis_v1"] = "defaulted_basis_v1"
        confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

        def note(self) -> str:
            return self.LABEL

    with pytest.raises(RegistryError, match="chosen, never a default"):
        host.register_study_basis(Defaulted)  # type: ignore[arg-type]

    class Mutable(GridQModel):
        BASIS_ID: ClassVar[str] = "mutable_basis_v1"
        LABEL: ClassVar[str] = "MUTABLE"
        basis_id: str
        confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

    with pytest.raises(RegistryError) as refused:
        host.register_study_basis(Mutable)  # type: ignore[arg-type]
    for reason in ("must be frozen", "typed Literal", "must define note"):
        assert reason in str(refused.value)

    class SameBanner(GridQModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        BASIS_ID: ClassVar[str] = "same_banner_basis_v1"
        LABEL: ClassVar[str] = StudyBasis.LABEL
        basis_id: Literal["same_banner_basis_v1"]
        confidence_ceiling: ConfidenceBand = ConfidenceBand.LOW

        def note(self) -> str:
            return self.LABEL

    with pytest.raises(RegistryError, match="is the banner of"):
        host.register_study_basis(SameBanner)  # type: ignore[arg-type]
    with pytest.raises(RegistryError, match="not a pydantic model class"):
        ext.study_bases.register("not_a_model_v1", object, owner="p", version="1")  # type: ignore[arg-type]


def test_a_failing_plugin_is_rolled_back_and_raised(isolated: None) -> None:
    def half_then_fail(host: ExtensionHost) -> None:
        host.register_resource(
            ResourceSpec(
                resource_id="half_registered",
                label="x",
                kind=ResourceKind.GENERATION,
                description="x",
            )
        )
        raise RuntimeError("the second registration failed")

    before = {r.name: r.ids() for r in ext._ALL}
    with pytest.raises(ExtensionLoadError, match="rolled back"):
        ext._run(half_then_fail, owner="p", version="1", source="test hook")
    assert {r.name: r.ids() for r in ext._ALL} == before
    assert "half_registered" not in ext.resources
    # The built-ins survive a rollback.
    assert set(PROFILE_LIBRARY) <= set(ext.load_archetypes.ids())


def test_loading_twice_is_one_registration_and_a_bad_module_is_named(
    isolated: None, tmp_path: Path
) -> None:
    first = ext.load_plugin(PLUGIN)
    assert ext.load_plugin(PLUGIN) is first
    assert [p.owner for p in ext.loaded_plugins()] == [PLUGIN]
    with pytest.raises(ExtensionLoadError, match="could not be imported"):
        ext.load_plugin("no_such_gridq_plugin")
    (tmp_path / "versionless_plugin.py").write_text("def register(host): pass\n", "utf-8")
    (tmp_path / "hookless_plugin.py").write_text("__version__ = '1'\n", "utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(ExtensionLoadError, match="must state __version__"):
            ext.load_plugin("versionless_plugin")
        with pytest.raises(ExtensionLoadError, match="defines no register"):
            ext.load_plugin("hookless_plugin")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("versionless_plugin", None)
        sys.modules.pop("hookless_plugin", None)


def test_registry_reads_are_ordered_by_id_not_by_registration(isolated: None) -> None:
    for key in ("zz_resource", "aa_resource", "mm_resource"):
        ext.resources.register(
            key,
            ResourceSpec(resource_id=key, label=key, kind=ResourceKind.GENERATION, description=key),
            owner="p",
            version="1",
        )
    assert ext.resources.ids() == ("aa_resource", "battery_storage", "mm_resource", "zz_resource")
