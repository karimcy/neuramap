"""Smoke test: the package and every scaffolded subpackage import, and metadata is present.

This is the Milestone-0 import test — it proves the scaffold installs and imports
cleanly before any real logic exists.
"""

import importlib

import pytest

import gridq

SUBPACKAGES = [
    "gridq.schemas",
    "gridq.ingestion",
    "gridq.assumptions",
    "gridq.provenance",
    "gridq.engine",
    "gridq.dispatch",
    "gridq.extensions",
    "gridq.sizing",
    "gridq.value_stack",
    "gridq.scenarios",
    "gridq.finance",
    "gridq.quotes",
    "gridq.telemetry",
    "gridq.outputs",
    "gridq.dev",
    "gridq.cli",
]
# `gridq.app` is intentionally excluded: it is an optional extra (FastAPI/Jinja2) covered by
# tests/test_app_smoke.py, so the core scaffold smoke-test stays importable without the extra.


def test_version_present() -> None:
    assert isinstance(gridq.__version__, str)
    assert gridq.__version__


@pytest.mark.parametrize("module", SUBPACKAGES)
def test_subpackage_imports(module: str) -> None:
    assert importlib.import_module(module) is not None
