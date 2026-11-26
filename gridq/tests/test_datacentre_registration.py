"""WP-SITE-01 acceptance 1: ``data_centre`` is selectable in SiteModel without editing a CORE path.

The archetype is registered by ``gridq.datacentre.plugin`` through the IC-12 chassis
(`gridq.extensions`) and selected on the WS-SITE route, ``derive_data_centre_half_hourly``, which
stamps the same fields CORE's ``derive_half_hourly`` stamps. These tests prove that:

- the plug-in's files sit under no CORE-owned path, and no CORE code or config file (nor GridQ's
  ``pyproject.toml``) names the package, the id or the class: nothing in CORE had to change;
- the frozen IC-12 schemas still check clean with it loaded;
- unloaded, the id is unknown; importing the package registers nothing; loading twice registers
  once;
- CORE's own scaling route refuses the archetype (it has no shape to scale), whatever the site
  states;
- the selected site revalidates as a `SiteModel`, Gate 2 admits it MODELLED with the reference
  profile, the derived record is appended, and the run's ``input_hash`` moves.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from tests._datacentre_support import (  # noqa: F401  (fixture)
    DRAWS,
    SEED,
    isolated_registries,
    make_inputs,
)
from tests.test_chassis_extension_registries import CORE_OWNED_PATHS, REPO, _core_files

import gridq.extensions as ext
from gridq.assumptions import load_assumptions
from gridq.contracts import __main__ as contracts_cli
from gridq.datacentre import ARCHETYPE_ID, PLUGIN_MODULE, DataCentreArchetype, ensure_registered
from gridq.datacentre.derive import derive_data_centre_half_hourly
from gridq.datacentre.plugin import __version__ as PLUGIN_VERSION
from gridq.datacentre.presets import ups_efficiency_shehabi_2024
from gridq.extensions import ExtensionLoadError, UnknownExtension
from gridq.ingestion.hh_mandate import HalfHourlyBasis, admit_half_hourly
from gridq.ingestion.hh_synthesis import (
    ProfileSource,
    ReferenceProfile,
    SynthesisError,
    derive_half_hourly,
    synthesise_half_hourly,
)
from gridq.quotes.assembly import assemble_quote_record
from gridq.schemas import (
    BankableStatus,
    ConfidenceBand,
    ImplementationLevel,
    OptimisationResult,
    SiteModel,
    SolverMetadata,
    SolverStatus,
)

PACKAGE_DIR = REPO / "src" / "gridq" / "datacentre"
NEEDLES = ("gridq.datacentre", ARCHETYPE_ID, "DataCentreArchetype")

pytestmark = pytest.mark.usefixtures("isolated_registries")


def _result() -> tuple[OptimisationResult, object]:
    lib = load_assumptions("gb_v0_1")
    result = OptimisationResult(
        result_id="res_dc",
        request_id="req_dc",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.NOT_BANKABLE,
        solver_status=SolverStatus.OPTIMAL,
        solver_metadata=SolverMetadata(
            seed=0, assumption_version=lib.metadata.version, code_version="c", model_version="m"
        ),
    )
    return result, lib


def test_registering_touches_no_core_owned_path() -> None:
    """Load the plug-in and prove CORE was not edited to make that possible."""
    loaded = ensure_registered()
    assert ARCHETYPE_ID in ext.load_archetypes.ids()
    assert isinstance(ext.load_archetypes.get(ARCHETYPE_ID), DataCentreArchetype)
    assert (loaded.owner, loaded.version) == (PLUGIN_MODULE, PLUGIN_VERSION)

    # 1. The plug-in's own files sit under no CORE-owned path.
    plugin_files = list(PACKAGE_DIR.rglob("*.py"))
    assert len(plugin_files) >= 8
    core_roots = [REPO / rel for rel in CORE_OWNED_PATHS]
    for path in plugin_files:
        assert not any(root in path.parents for root in core_roots), path

    # 2. No CORE code, schema or data file, nor GridQ's pyproject.toml, names the plug-in.
    named: list[str] = []
    code_and_config = [p for p in _core_files() if (REPO / "docs") not in p.parents]
    assert code_and_config
    for path in [*code_and_config, REPO / "pyproject.toml"]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        named.extend(f"{path.relative_to(REPO)}: {n}" for n in NEEDLES if n in text)
    assert named == [], f"a CORE-owned path names the data-centre plug-in: {named}"

    # 3. The frozen IC-12 schemas still check clean with it loaded.
    assert contracts_cli.main(["--check"]) == 0


def test_unloaded_the_id_is_unknown_and_importing_registers_nothing() -> None:
    importlib.import_module("gridq.datacentre")
    importlib.import_module("gridq.datacentre.derive")
    assert ARCHETYPE_ID not in ext.load_archetypes.ids()
    with pytest.raises(UnknownExtension):
        ext.load_archetypes.get(ARCHETYPE_ID)


def test_loading_twice_registers_once() -> None:
    first = ext.load_plugin(PLUGIN_MODULE)
    second = ensure_registered()
    assert first == second
    assert ext.load_archetypes.ids().count(ARCHETYPE_ID) == 1
    registration = ext.load_archetypes.registration(ARCHETYPE_ID)
    assert (registration.owner, registration.version) == (PLUGIN_MODULE, PLUGIN_VERSION)
    assert not registration.builtin
    assert [p.owner for p in ext.loaded_plugins()].count(PLUGIN_MODULE) == 1


def test_core_scaling_route_refuses_the_archetype(tmp_path: Path, make_site) -> None:  # type: ignore[no-untyped-def]
    """A data centre has no reference shape to scale, so CORE's route refuses it either way."""
    ensure_registered()
    no_total = make_site(with_max_import=False).model_copy(update={"annual_consumption_kwh": None})
    no_total = no_total.model_copy(
        update={"load_data": no_total.load_data.model_copy(update={"annual_kwh": None})}
    )
    with pytest.raises(SynthesisError, match="no annual or monthly consumption"):
        derive_half_hourly(no_total, out_dir=tmp_path, profile=ARCHETYPE_ID)
    with_total = make_site(with_max_import=False)
    with pytest.raises(SynthesisError, match="no reference day shape"):
        derive_half_hourly(with_total, out_dir=tmp_path, profile=ARCHETYPE_ID, year=2027)
    with pytest.raises(SynthesisError, match="no reference day shape"):
        synthesise_half_hourly(
            site_id="s", profile=ARCHETYPE_ID, year=2027, out_dir=tmp_path, annual_kwh=1.0
        )
    assert not list(tmp_path.glob("*.csv")), "a refused route must write no series"


def test_data_centre_is_selectable_in_site_model(tmp_path: Path, make_site) -> None:  # type: ignore[no-untyped-def]
    site = make_site(with_max_import=False)
    before_annual = (site.load_data.annual_kwh, site.annual_consumption_kwh)
    derived = derive_data_centre_half_hourly(
        site, make_inputs(), seed=SEED, draws=DRAWS, out_dir=tmp_path, release="gb_v0_1"
    )

    # The stamped site is a valid SiteModel, re-validated from its own serialisation.
    again = SiteModel.model_validate(derived.site.model_dump(mode="json"))
    load = again.load_data
    assert load.half_hourly_basis == HalfHourlyBasis.MODELLED.value
    assert load.half_hourly_reference_profile == (
        f"{ARCHETYPE_ID} ({ProfileSource.ARCHETYPE_PENDING_SOURCE.value})"
    )
    assert load.half_hourly_rows == len(derived.synthesis.timestamps)
    assert "no measured total" in (load.half_hourly_scaled_to or "")
    assert derived.synthesis.inputs_hash in (load.half_hourly_scaled_to or "")
    # Modelled energy never enters the measured fields.
    assert (load.annual_kwh, again.annual_consumption_kwh) == before_annual

    # Gate 2 reads the basis from the record: MODELLED, with the reference profile.
    admission = admit_half_hourly(again)
    assert admission.basis is HalfHourlyBasis.MODELLED
    assert admission.reference_profile == load.half_hourly_reference_profile
    assert admission.file_ref == str(derived.files.expected_csv)

    # The derived record is appended, capped at MEDIUM, and honest about having no preset.
    assert again.derived_assumptions is not None
    assert again.derived_assumptions[-1] == derived.record
    assert len(again.derived_assumptions) == len(site.derived_assumptions or []) + 1
    record = derived.record
    assert record.key == "load_data.half_hourly_reference_profile"
    assert record.value == ARCHETYPE_ID
    assert record.confidence_ceiling is ConfidenceBand.MEDIUM
    assert record.derivation_method == "archetype_pending_source"
    assert record.sources == []
    assert "UPS OEM part-load efficiency curve" in record.confirmation_ask

    # The run's identity moves: a derived series is not the same run as no series.
    result, lib = _result()
    assert (
        assemble_quote_record(again, result, None, lib).input_hash
        != assemble_quote_record(site, result, None, lib).input_hash
    )


def test_a_preset_makes_the_record_a_sourced_default(tmp_path: Path, make_site) -> None:  # type: ignore[no-untyped-def]
    site = make_site(with_max_import=False)
    inputs = make_inputs(
        ups=make_inputs().ups.model_copy(
            update={"efficiency": ups_efficiency_shehabi_2024("hyperscale_ai_air")}
        )
    )
    derived = derive_data_centre_half_hourly(
        site, inputs, seed=SEED, draws=DRAWS, out_dir=tmp_path, release=None
    )
    record = derived.record
    assert record.derivation_method == "sourced_default"
    assert len(record.sources) == 1
    assert "Table 4.3" in record.sources[0] and "Hyperscale 90–99%" in record.sources[0]
    assert any("no electrical effect" in w for w in derived.synthesis.warnings)


def test_a_foreign_data_centre_registration_is_refused(tmp_path: Path, make_site) -> None:  # type: ignore[no-untyped-def]
    """If something else holds the id, the WS-SITE route will not synthesise from it.

    The chassis refuses first (an id is never overridden), so the plug-in's load fails and is
    rolled back; nothing is written.
    """
    impostor = ReferenceProfile(
        profile_id=ARCHETYPE_ID,
        label="not the component model",
        source=ProfileSource.ARCHETYPE_PENDING_SOURCE,
        source_ref="a test impostor",
        weekday=(1.0,) * 48,
        saturday=(1.0,) * 48,
        sunday=(1.0,) * 48,
    )
    ext._run(
        lambda host: host.register_load_archetype(impostor),
        owner="impostor",
        version="0",
        source="a test",
    )
    with pytest.raises(ExtensionLoadError, match="'data_centre' is already registered by impostor"):
        derive_data_centre_half_hourly(
            make_site(with_max_import=False),
            make_inputs(),
            seed=SEED,
            draws=DRAWS,
            out_dir=tmp_path,
            release=None,
        )
    assert not list(tmp_path.iterdir())


def test_inputs_for_another_site_are_refused(tmp_path: Path, make_site) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="inputs are for site"):
        derive_data_centre_half_hourly(
            make_site(with_max_import=False),
            make_inputs(site_id="elsewhere"),
            seed=SEED,
            draws=DRAWS,
            out_dir=tmp_path,
            release=None,
        )
