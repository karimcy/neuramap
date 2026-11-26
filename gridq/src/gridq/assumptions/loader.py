"""Loader for versioned assumption libraries.

The single door to underwriting assumptions: modules call `load_assumptions(version)`
and read from the returned `AssumptionLibrary` — they never hold private constants.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from gridq.assumptions.models import AssumptionLibrary

# Repository-level assumptions directory (assumptions/ at the repo root).
ASSUMPTIONS_DIR = Path(__file__).resolve().parents[3] / "assumptions"

_SUFFIXES = (".yaml", ".yml", ".json")


class AssumptionError(Exception):
    """Raised when an assumption version cannot be found or loaded."""


def _resolve_dir(base_dir: str | Path | None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    env = os.environ.get("GRIDQ_ASSUMPTIONS_DIR")
    return Path(env) if env else ASSUMPTIONS_DIR


def available_versions(*, base_dir: str | Path | None = None) -> list[str]:
    """Every assumption release present on disk, oldest first.

    Sorted by the numeric parts of the ``<market>_v<major>_<minor>`` name, so ``gb_v1_10`` sorts
    after ``gb_v1_9`` (a plain string sort would not).
    """
    directory = _resolve_dir(base_dir)
    if not directory.is_dir():
        return []
    names = {p.stem for p in directory.iterdir() if p.suffix in _SUFFIXES and p.is_file()}

    def _key(name: str) -> tuple[str, list[int]]:
        market, _, version = name.partition("_v")
        parts = [int(p) for p in version.split("_") if p.isdigit()]
        return (market, parts)

    return sorted(names, key=_key)


def latest_version(*, base_dir: str | Path | None = None) -> str:
    """The newest assumption release on disk.

    Exists so callers never hard-code a version that silently goes stale (hostile review F-15: the
    CLI defaulted every run to ``gb_v0_1`` long after ``gb_v1_6`` shipped, presenting a Capacity
    Market revenue line the library's own audit trail says was overstated ~2.3-4x).
    """
    versions = available_versions(base_dir=base_dir)
    if not versions:
        raise AssumptionError(
            f"no assumption releases found in {_resolve_dir(base_dir)} — cannot resolve a default."
        )
    return versions[-1]


def load_assumptions(version: str, *, base_dir: str | Path | None = None) -> AssumptionLibrary:
    """Load and validate the assumption library for ``version`` (e.g. ``"gb_v0_1"``).

    Looks for ``<dir>/<version>.{yaml,yml,json}``. Raises `AssumptionError` if no file
    exists for the version, or if the file is malformed.
    """
    directory = _resolve_dir(base_dir)
    path = next(
        (directory / f"{version}{s}" for s in _SUFFIXES if (directory / f"{version}{s}").is_file()),
        None,
    )
    if path is None:
        looked = ", ".join(f"{version}{s}" for s in _SUFFIXES)
        raise AssumptionError(
            f"no assumption file for version {version!r} in {directory} (looked for: {looked})"
        )

    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise AssumptionError(f"failed to parse {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise AssumptionError(f"{path} must contain a mapping at the top level")

    return AssumptionLibrary.model_validate(data)
