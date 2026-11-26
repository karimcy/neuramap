"""The WP-SITE-01 data rule, enforced: no numeric default anywhere, and every preset quoted.

- **No input has a numeric default.** Every field of every input and distribution model is
  required; the only defaults in the package are the string tags of discriminated unions.
  ``synthesise`` and the WS-SITE route take ``seed`` and ``draws`` with no default either; their
  optional IC-9 ``workload_flex`` defaults to the sentinel ``NO_ENVELOPE_DECLARED`` (WP-SITE-07).
- **Each preset's numbers are the numbers it quotes**, from a source recorded with its URL, the
  date it was retrieved and the SHA-256 of the file held. No ASHRAE document is a source.
- **Ambient is an input.** The package fetches nothing: no network module is imported, and a
  horizon the supplied series does not cover is refused rather than filled.
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests._datacentre_support import make_inputs

import gridq.datacentre as dc
from gridq.datacentre import derive, distributions, inputs, sources, synthesiser
from gridq.datacentre.presets import (
    NODE_LEVEL_UPPER_BOUND,
    S1_TABLE_3_1,
    S1_TABLE_4_3_UPS,
    sd_fraction_shehabi_2024,
    ups_efficiency_shehabi_2024,
)
from gridq.datacentre.sources import SOURCES, Citation
from gridq.datacentre.workload_flex import NO_ENVELOPE_DECLARED

PACKAGE = Path(dc.__file__).parent


def _models() -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    for module in (inputs, distributions, sources):
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                found.append(obj)
    return found


def test_no_input_field_has_a_numeric_default() -> None:
    models = _models()
    assert len(models) >= 20
    offenders: list[str] = []
    for model in models:
        for name, info in model.model_fields.items():
            if info.is_required():
                continue
            tag = name == "kind" and isinstance(info.default, str)
            if not tag or info.default_factory is not None:
                offenders.append(f"{model.__name__}.{name} = {info.default!r}")
    assert offenders == [], f"fields with a default: {offenders}"


def test_seed_and_draws_have_no_default() -> None:
    for fn in (
        synthesiser.synthesise,
        derive.derive_data_centre_half_hourly,
        dc.DataCentreArchetype.synthesise,
    ):
        params = inspect.signature(fn).parameters
        for name in ("seed", "draws"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is inspect.Parameter.empty, (fn, name)
        # WP-SITE-07: the optional IC-9 input defaults to the stated sentinel, never None (A2-04).
        flex = params["workload_flex"]
        assert flex.kind is inspect.Parameter.KEYWORD_ONLY
        assert flex.default is NO_ENVELOPE_DECLARED, fn
    window = inspect.signature(synthesiser.Synthesis.one_minute).parameters
    assert window["jensen_gap_flag_kw"].default is inspect.Parameter.empty


def test_the_ups_preset_is_the_range_it_quotes() -> None:
    for space_type, (_, low, high, printed) in S1_TABLE_4_3_UPS.items():
        preset = ups_efficiency_shehabi_2024(space_type)  # type: ignore[arg-type]
        stated = preset.efficiency
        assert stated.bounds() == (low, high)
        citation = stated.citation
        assert citation is not None and citation.source_id == "S1"
        assert citation.quote == printed
        assert f"{round(low * 100)}–{round(high * 100)}%" in printed
        assert "Table 4.3" in citation.locator
        assert citation.caveat and "uniform distribution on it is this package's choice" in (
            citation.caveat
        )


def test_the_sd_preset_is_the_ratio_of_the_figures_it_quotes() -> None:
    for workload, (pavg, sd) in S1_TABLE_3_1.items():
        stated = sd_fraction_shehabi_2024(workload)
        assert stated.bounds() == (sd / pavg, sd / pavg)
        citation = stated.citation
        assert citation is not None and citation.source_id == "S1"
        assert f"Pavg {pavg}" in citation.quote and f"SD {sd}" in citation.quote
        assert "Power measurements are per-node" in citation.quote
        assert citation.caveat and citation.caveat.startswith(NODE_LEVEL_UPPER_BOUND)
    with pytest.raises(KeyError, match="no workload row"):
        sd_fraction_shehabi_2024("a workload the table does not have")


def test_sources_are_recorded_and_no_ashrae_document_is_one() -> None:
    for source in SOURCES.values():
        assert source.url.startswith("https://")
        assert source.retrieved_on == "2026-11-26"
        assert "ASHRAE" not in source.reference
    # S1 supplies the only numbers; its file is identified by hash.
    assert re.fullmatch(r"[0-9a-f]{64}", SOURCES["S1"].sha256)
    citation = Citation(source_id="S1", locator="x", quote="y", caveat=None)
    assert citation.render().startswith(SOURCES["S1"].reference)


def test_the_package_fetches_nothing() -> None:
    network = {"urllib", "requests", "httpx", "http", "socket", "aiohttp", "cdsapi"}
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in network, f"{path.name} imports {name}"
    with pytest.raises(ValueError, match="none is fetched or filled"):
        make_inputs(horizon_end=date(2027, 3, 20))


def test_numbers_in_the_package_come_only_from_presets() -> None:
    """Outside `presets.py`, no module carries a float literal that could be a parameter value.

    Allowed: arithmetic (0, 1, 2, kW to MW), the hours in a half-hour (0.5, also the centre of a
    certain input's unused uniform), numerical tolerances, and the method constants of decision
    D83 (the band levels and the simulation-error confidence), which define what the
    output reports rather than what the plant is. Anything else would be a parameter in code.
    """
    allowed = {0.0, 0.5, 1.0, 2.0, 1000.0, 1e-9, 1e-12}
    method_constants = {"LEVELS", "LEVEL_NAMES", "CONFIDENCE"}
    found: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "presets.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip: set[int] = set()
        for stmt in tree.body:
            targets = (
                stmt.targets
                if isinstance(stmt, ast.Assign)
                else [stmt.target]
                if isinstance(stmt, ast.AnnAssign)
                else []
            )
            if any(isinstance(t, ast.Name) and t.id in method_constants for t in targets):
                skip.update(id(n) for n in ast.walk(stmt))
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value not in allowed:
                    found.append(f"{path.name}:{node.lineno}: {node.value}")
    assert found == [], found
