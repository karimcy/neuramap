"""Select the ``data_centre`` archetype for a site: synthesise, write, stamp, record (WS-SITE).

Decision D84 (Fable 5.2). This is the data-centre counterpart of CORE's
``derive_half_hourly`` and stamps exactly the fields CORE's route stamps, so Gate 2
(``admit_half_hourly``) reads the series as MODELLED with its reference profile, and the confidence
ceiling is MEDIUM. It differs from CORE's route in one way that matters: there is no measured
total, so nothing is "scaled to" one. The scaled-to field says so, and names the input hash.

What it never does:

- write the modelled energy into ``load_data.annual_kwh`` or ``annual_consumption_kwh``: those are
  measured fields, and a modelled number there would be read as a measurement;
- add a `SiteModel` field: bands, one-minute series and members go in sidecar files named by hash;
- register anything on import: it loads the plug-in through the chassis when called.

Files written to ``out_dir`` (``<site>_dc_<kind>_<series hash, 16 hex>``):

- ``…_expected_….csv``: ``timestamp,import_kwh``, the expected (mean) series, CORE's format;
- ``…_bands_….csv``: expected kW, P05…P95 kW and each edge's simulation-error interval;
- ``…_members_….npy``: every draw's half-hourly facility kW, ``(draws, intervals)``;
- ``…_manifest_….json``: hashes, the method manifest, aggregates, attribution, flags, warnings,
  citations and the data requests.

Those four are the same bytes whether or not an IC-9 turn-down is declared (WP-SITE-07,
D126). When one is declared (``workload_flex=``), it is bound beside the series and two
kinds of file are added, never changing the four:

- ``<site>_dc_workload_flex_<declaration hash, 16 hex>.json``: the binding (declaration hash,
  contract version, binding hash, half-hours in force and offered, notes) and the declaration;
- ``<site>_dc_turndown_<duration>min_<overlay hash, 16 hex>.csv``: for each duration the caller
  states in ``overlay_durations_min`` (none by default), the expected and P05 to P95 turn-down
  (IT, meter instant, meter settled), labelled conditional (`gridq.datacentre.turndown`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gridq.datacentre.archetype import ARCHETYPE_ID, DataCentreArchetype
from gridq.datacentre.inputs import DcLoadInputs
from gridq.datacentre.plugin import PLUGIN_MODULE
from gridq.datacentre.presets import DATA_REQUESTS
from gridq.datacentre.synthesiser import LEVEL_NAMES, LEVELS, Synthesis
from gridq.datacentre.turndown import TurndownOverlay, turndown_overlay
from gridq.datacentre.workload_flex import (
    NO_ENVELOPE_DECLARED,
    NoEnvelopeDeclared,
    WorkloadFlex,
    WorkloadFlexBinding,
)
from gridq.extensions import load_archetypes, load_plugin
from gridq.ingestion.hh_mandate import HalfHourlyBasis
from gridq.ingestion.hh_synthesis import SynthesisError
from gridq.provenance.hashing import hash_file
from gridq.schemas import ConfidenceBand, DerivedAssumption, SiteModel

__all__ = [
    "DerivedDataCentre",
    "derive_data_centre_half_hourly",
    "write_outputs",
    "write_workload_flex",
]

_DERIVED_BY = "gridq.datacentre.derive"


@dataclass(frozen=True)
class DcFiles:
    expected_csv: Path
    bands_csv: Path
    members_npy: Path
    manifest_json: Path


@dataclass(frozen=True)
class DerivedDataCentre:
    """The stamped site, the ensemble behind it, the files written, and the derived record."""

    site: SiteModel
    synthesis: Synthesis
    files: DcFiles
    record: DerivedAssumption
    #: The bound IC-9 declaration (WP-SITE-07), or the sentinel; and the files it added.
    workload_flex: WorkloadFlexBinding | NoEnvelopeDeclared = NO_ENVELOPE_DECLARED
    workload_flex_files: tuple[Path, ...] = ()


def write_outputs(synthesis: Synthesis, out_dir: Path) -> DcFiles:
    """Write the four files for ``synthesis``. Deterministic: same synthesis, same bytes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{synthesis.inputs.site_id}_dc"
    tag = synthesis.series_hash.removeprefix("sha256:")[:16]
    expected = out_dir / f"{stem}_expected_{tag}.csv"
    bands = out_dir / f"{stem}_bands_{tag}.csv"
    members = out_dir / f"{stem}_members_{tag}.npy"
    manifest = out_dir / f"{stem}_manifest_{tag}.json"

    lines = ["timestamp,import_kwh"]
    lines.extend(
        f"{ts:%Y-%m-%d %H:%M:%S},{kwh:.6f}"
        for ts, kwh in zip(synthesis.timestamps, synthesis.expected_kwh, strict=True)
    )
    expected.write_text("\n".join(lines) + "\n", encoding="utf-8")

    names = [LEVEL_NAMES[p] for p in LEVELS]
    header = ["timestamp", "expected_kw"]
    for name in names:
        header += [f"{name}_kw", f"{name}_ci_low_kw", f"{name}_ci_high_kw"]
    rows = [",".join(header)]
    for i, ts in enumerate(synthesis.timestamps):
        cells = [f"{ts:%Y-%m-%d %H:%M:%S}", f"{synthesis.expected_kw[i]:.6f}"]
        for name in names:
            lo, hi = synthesis.band_ci_kw[name]
            cells += [f"{synthesis.bands_kw[name][i]:.6f}", f"{lo[i]:.6f}", f"{hi[i]:.6f}"]
        rows.append(",".join(cells))
    bands.write_text("\n".join(rows) + "\n", encoding="utf-8")

    np.save(members, np.ascontiguousarray(synthesis.members_kw, dtype="<f8"), allow_pickle=False)

    body = {
        "series_hash": synthesis.series_hash,
        "inputs_hash": synthesis.inputs_hash,
        "files": {
            "expected_csv": {"name": expected.name, "sha256": hash_file(expected)},
            "bands_csv": {"name": bands.name, "sha256": hash_file(bands)},
            "members_npy": {"name": members.name, "sha256": hash_file(members)},
        },
        "method": synthesis.manifest,
        "expected_label": "expected (per-interval mean across draws; not P50)",
        "medoid_draw": synthesis.medoid_draw,
        "aggregates": [a.as_dict() for a in synthesis.aggregates],
        "attribution": [a.as_dict() for a in synthesis.attribution],
        "flags": synthesis.flags,
        "warnings": list(synthesis.warnings),
        "citations": [c.render() for c in synthesis.citations],
        "data_requests": DATA_REQUESTS,
    }
    manifest.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return DcFiles(expected, bands, members, manifest)


def _overlay_csv(overlay: TurndownOverlay, path: Path) -> None:
    names = [LEVEL_NAMES[p] for p in LEVELS]
    quantities = list(overlay.expected_kw)
    header = ["timestamp"]
    for q in quantities:
        header += [f"{q}_expected_kw", *(f"{q}_{name}_kw" for name in names)]
    rows = [
        f"# IC-9 turn-down, call of {overlay.duration_min} min from each half-hour; firmness "
        f"{overlay.firmness}, allocation {overlay.allocation}; binding {overlay.binding_hash}",
        ",".join(header),
    ]
    for i, ts in enumerate(overlay.timestamps):
        cells = [f"{ts:%Y-%m-%d %H:%M:%S}"]
        for q in quantities:
            cells.append(f"{overlay.expected_kw[q][i]:.6f}")
            cells += [f"{overlay.bands_kw[q][name][i]:.6f}" for name in names]
        rows.append(",".join(cells))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_workload_flex(
    synthesis: Synthesis, out_dir: Path, *, overlay_durations_min: tuple[int, ...]
) -> tuple[Path, ...]:
    """Write the binding of a declared IC-9 turn-down and an overlay per stated duration.
    Raises when nothing is declared. Never touches the four series files."""
    binding = synthesis.workload_flex
    if not isinstance(binding, WorkloadFlexBinding):
        raise ValueError("no IC-9 turn-down is declared for this ensemble: nothing to write")
    stem = f"{synthesis.inputs.site_id}_dc"
    tag = binding.declaration_hash.removeprefix("sha256:")[:16]
    bound = out_dir / f"{stem}_workload_flex_{tag}.json"
    body = {
        "series_hash": synthesis.series_hash,
        "declaration_hash": binding.declaration_hash,
        "contract": binding.declaration.contract,
        "contract_version": binding.contract_version,
        "binding_hash": binding.binding_hash,
        "intervals_in_force": binding.intervals_in_force,
        "intervals_offered": binding.intervals_offered,
        "firmness": "conditional",
        "notes": list(binding.notes),
        "declaration": binding.declaration.model_dump(mode="json"),
    }
    bound.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written = [bound]
    for duration in overlay_durations_min:
        overlay = turndown_overlay(synthesis, duration_min=duration)
        otag = overlay.overlay_hash.removeprefix("sha256:")[:16]
        path = out_dir / f"{stem}_turndown_{duration}min_{otag}.csv"
        _overlay_csv(overlay, path)
        written.append(path)
    return tuple(written)


def derive_data_centre_half_hourly(
    site: SiteModel,
    inputs: DcLoadInputs,
    *,
    seed: int,
    draws: int,
    out_dir: Path,
    release: str | None,
    workload_flex: WorkloadFlex = NO_ENVELOPE_DECLARED,
    overlay_durations_min: tuple[int, ...] = (),
) -> DerivedDataCentre:
    """Synthesise the site's pre-operational load and stamp it onto a copy of the site.

    The archetype is resolved through the chassis registry (`load_archetypes`), after loading this
    package's plug-in, so the route is the registry's and not a private shortcut.
    ``workload_flex`` is the operator's optional IC-9 declaration: bound beside the series, it
    changes no series, file or stamp, and adds its own files (module docstring).
    ``overlay_durations_min`` names the call durations to write overlays for; it needs a
    declaration.
    """
    if overlay_durations_min and workload_flex is NO_ENVELOPE_DECLARED:
        raise ValueError("overlay_durations_min needs a declared IC-9 turn-down (workload_flex=)")
    if inputs.site_id != site.site_id:
        raise ValueError(f"inputs are for site {inputs.site_id!r}, not {site.site_id!r}")
    load_plugin(PLUGIN_MODULE)
    archetype = load_archetypes.get(ARCHETYPE_ID)
    if not isinstance(archetype, DataCentreArchetype):
        raise SynthesisError(
            f"the registry's {ARCHETYPE_ID!r} is not gridq.datacentre's archetype "
            f"({type(archetype).__name__}); refusing to synthesise from it"
        )
    synthesis = archetype.synthesise(inputs, seed=seed, draws=draws, workload_flex=workload_flex)
    files = write_outputs(synthesis, out_dir)
    flex_files = (
        ()
        if synthesis.workload_flex is NO_ENVELOPE_DECLARED
        else write_workload_flex(synthesis, out_dir, overlay_durations_min=overlay_durations_min)
    )

    scaled_to = (
        f"site-declared data-centre inputs ({synthesis.inputs_hash}; series "
        f"{synthesis.series_hash}); no measured total: the energy is modelled, not scaled"
    )
    citations = [c.render() for c in synthesis.citations]
    record = DerivedAssumption(
        kind="derived_input",
        key="load_data.half_hourly_reference_profile",
        missing_input=(
            "the site's half-hourly load (the data centre is not yet operating, so no meter "
            "or invoice exists)"
        ),
        value=ARCHETYPE_ID,
        unit=None,
        basis=(
            "Pre-operational load MODELLED by gridq.datacentre "
            f"({synthesis.manifest['method_version']}) from the site's declared IT ramp, "
            "utilisation, UPS, distribution, auxiliary and "
            f"cooling inputs over {inputs.horizon_start} to {inputs.horizon_end}, ambient from "
            f"{inputs.ambient.source}; {synthesis.draws} seeded Latin-hypercube draws (seed "
            f"{synthesis.seed}) give the bands. The series written is the per-interval mean "
            f"('expected'), not a measurement and not a P50. "
            + (
                f"{len(citations)} cited preset(s) used; every other value is the site's own."
                if citations
                else "No preset was used: every value is the site's own declaration."
            )
        ),
        sources=citations,
        derivation_method="sourced_default" if citations else "archetype_pending_source",
        derived_by=_DERIVED_BY,
        derived_on_release=release,
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask="Confirm or supply: " + " ".join(DATA_REQUESTS.values()),
    )
    load = site.load_data.model_copy(
        update={
            "half_hourly_file_ref": str(files.expected_csv),
            "half_hourly_rows": len(synthesis.timestamps),
            "half_hourly_basis": HalfHourlyBasis.MODELLED.value,
            "half_hourly_reference_profile": f"{archetype.profile_id} ({archetype.source.value})",
            "half_hourly_scaled_to": scaled_to,
            "validation_warnings": [*site.load_data.validation_warnings, *synthesis.warnings],
        }
    )
    derived = [*(site.derived_assumptions or []), record]
    return DerivedDataCentre(
        site=site.model_copy(update={"load_data": load, "derived_assumptions": derived}),
        synthesis=synthesis,
        files=files,
        record=record,
        workload_flex=synthesis.workload_flex,
        workload_flex_files=flex_files,
    )
