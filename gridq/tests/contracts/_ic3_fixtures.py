"""IC-3 synthetic fixtures: committed schema-test data for the dispatch-envelope contract.

Four envelopes and one compiled timeline, in ``fixtures/ic3/`` (their own folder, so the 1.0.0
fixture globs and golden table one level up are undisturbed):

- ``ic3_envelope_hybrid_bess``: the hybrid-connection site of the IC-1 and IC-2 1.1.0 fixtures
  (``SYNTHETIC-SITE-HYBRID``, 10 MW firm plus 20 MW non-firm), bound to both by hash. A 5 MW /
  10 MWh battery with a capacity-market service on winter weekday days and a local-flex service
  on winter weekday peaks; overnight the import limit rests on the non-firm tranche. One dated
  override withholds half the asset for a maintenance day. No cooling plant.
- ``ic3_envelope_hybrid_bess_successor``: a re-assessment of the same site that supersedes the
  first from 1 July 2027 (a larger capacity-market slice). With the first, it exercises
  `active_at`.
- ``ic3_envelope_dc_thermal``: the chilled-water data centre of the IC-11 modelled surface
  (``SYNTHETIC-DC-CHILLED-WATER``), bound to it by hash, with its binding inlet band, an operating
  band per cell and cooling turn-down commitments for a demand-flexibility service only where the
  surface declares enough firm kW (winter and shoulder evenings, never the zero-flex summer
  afternoons). It pins a synthetic constraint-library version.
- ``ic3_envelope_clock_change_week``: the firm site of the IC-1 firm archetype
  (``SYNTHETIC-SITE-FIRM``) over the week the GB clock goes forward (28 March 2027), so its
  night band is six UTC hours that Sunday and seven on the other days.
- ``ic3_timeline_clock_change_week``: that envelope compiled (`resolve`), for a consumer that
  reads the timeline and trusts it by hash with no clock logic of its own.

All values are synthetic. Site ids start ``SYNTHETIC-`` and every envelope is ``record_class:
synthetic_fixture``; input and evidence hashes are SHA-256 of a synthetic placeholder text, not of
any real run or document; no counterparty reference exists; no underwritten value is stated (each
is ``unknown`` or ``not_applicable``). The basis banners are the registered study bases' own
labels, copied verbatim (a test checks them against `gridq.extensions.study_bases`).
``test_ic3_*`` asserts the committed files equal a fresh build. Regenerate with
``python tests/contracts/_ic3_fixtures.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURES_IC3 = Path(__file__).resolve().parent / "fixtures" / "ic3"
HYBRID = FIXTURES_IC3 / "ic3_envelope_hybrid_bess.synthetic.json"
HYBRID_SUCCESSOR = FIXTURES_IC3 / "ic3_envelope_hybrid_bess_successor.synthetic.json"
DC_THERMAL = FIXTURES_IC3 / "ic3_envelope_dc_thermal.synthetic.json"
CLOCK_WEEK = FIXTURES_IC3 / "ic3_envelope_clock_change_week.synthetic.json"
CLOCK_WEEK_TIMELINE = FIXTURES_IC3 / "ic3_timeline_clock_change_week.synthetic.json"

#: Content hashes of the fixtures the envelopes bind to (each pinned by its own golden table).
IC1_HYBRID_V110_HASH = "sha256:122618785497c4f998cbdb76b49746be63415bf56cc09c4a6187a333c3d7c6b1"
IC1_FIRM_HASH = "sha256:93f1919f8179e71eec8628e85b60a2095a395e0221fc4b188bf42f31847306cc"
IC2_HYBRID_V110_HASH = "sha256:69acdae915c6b4c0c6e6610bddea1a04b6f83b9300c6216b0935eaead2f51ddf"
IC11_CHILLED_WATER_HASH = "sha256:bbac57be8fd2e8b05fa131ec21659d20e1c67b0e33c04bbf618f0d38a2d07025"

#: The registered banners (`gridq.schemas.study`), verbatim.
ALL_STREAMS_BASIS = "all_streams_enabled_study_v1"
ALL_STREAMS_LABEL = (
    "STUDY BASIS (D55) — EVERY REVENUE STREAM PRICED IN THE PRICED FLOOR, EVERY AWARD ASSUMED "
    "— NOT AN UNDERWRITING OUTPUT"
)
EXPORT_FLOOR_BASIS = "export_in_priced_floor_study_v1"
EXPORT_FLOOR_LABEL = (
    "STUDY BASIS (D46) — EXPORT PRICED IN THE PRICED FLOOR — NOT AN UNDERWRITING OUTPUT"
)
DISCLOSURE = (
    "synthetic fixture: schema-test data, not a priced run; the banner is the named study "
    "basis's own"
)

CM = "svc-capacity-market"
FLEX = "svc-local-flex-winter-peak"
DFS = "svc-dfs-cooling"


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _placeholder_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC PLACEHOLDER: {label}".encode()).hexdigest()


def _contracts() -> Any:
    from gridq.contracts import dispatch_envelope

    return dispatch_envelope


def _na(reason: str) -> dict[str, str]:
    return {"kind": "not_applicable", "reason": f"synthetic fixture: {reason}"}


def _unknown(reason: str) -> dict[str, str]:
    return {"kind": "unknown", "reason": f"synthetic fixture: {reason}"}


def _source(
    site: str,
    basis_id: str,
    label: str,
    *,
    agreement: dict[str, Any],
    scenario_set: dict[str, Any],
    library: dict[str, Any],
) -> dict[str, Any]:
    input_hash = _placeholder_hash(f"assessment run inputs, {site}")
    pins = {
        "assumption_version": "synthetic-fixture",
        "code_version": "0.0.0-synthetic",
        "schema_version": "0.1.0",
    }
    run_id = _contracts().derive_run_id(
        input_hash, pins["assumption_version"], pins["code_version"], pins["schema_version"]
    )
    return {
        "input_hash": input_hash,
        "run": {"run_id": run_id, **pins},
        "producer": {"engine": "gridq", "engine_version": "0.0.0-synthetic"},
        "basis": {"kind": "study", "basis_id": basis_id, "label": label, "disclosure": DISCLOSURE},
        "constraint_library": library,
        "assessment": {
            "kind": "quote_binding",
            "contract": "IC-12",
            "contract_version": "2.0.0",
            "quote_id": f"quote_{run_id}",
            "quote_input_hash": input_hash,
        },
        "connection_agreement": agreement,
        "scenario_set": scenario_set,
    }


def _agreement_ref(version: str, agreement_hash: str) -> dict[str, str]:
    return {
        "kind": "connection_agreement_ref",
        "contract": "IC-1",
        "contract_version": version,
        "agreement_hash": agreement_hash,
    }


def _part(
    resilience: int,
    merchant: int,
    services: tuple[tuple[str, int], ...] = (),
    withheld: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "resilience_kw": resilience,
        "services": [{"service_id": s, "kw": kw} for s, kw in services],
        "merchant_kw": merchant,
        "withheld": withheld or _na("nothing is withheld"),
    }


def _stack(
    resilience: int,
    merchant: int,
    up: tuple[tuple[str, int], ...] = (),
    withheld: int = 0,
) -> dict[str, Any]:
    return {
        "resilience_floor_kwh": resilience,
        "held_up": [{"service_id": s, "kwh": kwh} for s, kwh in up],
        "merchant_kwh": merchant,
        "held_down": [],
        "withheld_kwh": withheld,
    }


def _terms(
    import_kw: int,
    export_kw: int,
    basis: str,
    discharge: dict[str, Any],
    charge: dict[str, Any],
    energy: dict[str, Any],
    thermal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "import_limit_kw": import_kw,
        "export_limit_kw": export_kw,
        "import_basis": basis,
        "discharge": discharge,
        "charge": charge,
        "energy": energy,
        "thermal": thermal or _na("no controllable cooling plant"),
    }


def _band(band_id: str, start: int, end: int) -> dict[str, Any]:
    return {"band_id": band_id, "start_minute": start, "end_minute": end}


def _cells(
    seasons: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    bands: list[dict[str, Any]],
    grid: dict[str, dict[str, list[str]]],
) -> list[dict[str, str]]:
    return [
        {
            "season_id": s["season_id"],
            "day_group_id": g["day_group_id"],
            "band_id": b["band_id"],
            "terms_id": grid[s["season_id"]][g["day_group_id"]][i],
        }
        for s in seasons
        for g in groups
        for i, b in enumerate(bands)
    ]


def _service(
    service_id: str,
    stream: str,
    sustain: int | dict[str, str],
    role: str,
    basis: dict[str, Any],
    underwritten: dict[str, Any],
) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "stream": stream,
        "sustain_min": sustain,
        "counterparty_role": role,
        "reference": _unknown("no counterparty reference exists"),
        "basis": basis,
        "underwritten": underwritten,
    }


def _assumed(ask: str) -> dict[str, str]:
    return {"kind": "assumed_approved", "confirmation_ask": f"synthetic fixture: {ask}"}


def _seal(data: dict[str, Any]) -> dict[str, Any]:
    """Fill ``resolved_hash`` from the draft's own compilation (`resolved_hash_of`)."""
    data["resolved_hash"] = _contracts().resolved_hash_of(data)
    return data


def _envelope(**fields: Any) -> dict[str, Any]:
    return _seal(
        {
            "contract": "IC-3",
            "contract_version": "1.0.0",
            **fields,
            "clock": "Europe/London",
            "resolution_min": 30,
        }
    )


# ------------------------------------------------------------------ the hybrid site


_HYBRID_SEASONS = [
    {"season_id": "winter", "months": [1, 2, 3, 10, 11, 12]},
    {"season_id": "summer", "months": [4, 5, 6, 7, 8, 9]},
]
_WEEK = [
    {"day_group_id": "weekday", "iso_weekdays": [1, 2, 3, 4, 5]},
    {"day_group_id": "weekend", "iso_weekdays": [6, 7]},
]
_HYBRID_BANDS = [
    _band("night", 0, 420),
    _band("day", 420, 960),
    _band("peak", 960, 1140),
    _band("evening", 1140, 1440),
]
_HYBRID_GRID = {
    "winter": {
        "weekday": ["night-non-firm", "cm-day", "cm-flex-peak", "cm-day"],
        "weekend": ["night-non-firm", "base", "base", "base"],
    },
    "summer": {
        "weekday": ["night-non-firm", "base", "base", "base"],
        "weekend": ["night-non-firm", "base", "base", "base"],
    },
}


def _hybrid_terms(cm_kw: int) -> list[dict[str, Any]]:
    """The five named terms of the hybrid site; the capacity-market slice is ``cm_kw``."""
    charge = _part(0, 5000)
    maintenance_withheld = {
        "kind": "withheld",
        "kw": 2500,
        "reason_code": "maintenance",
        "reason": "synthetic fixture: one of two inverter blocks out for planned maintenance",
    }
    return [
        {
            "terms_id": "base",
            "terms": _terms(10000, 5000, "firm", _part(500, 4500), charge, _stack(1000, 9000)),
        },
        {
            "terms_id": "cm-day",
            "terms": _terms(
                10000,
                5000,
                "firm",
                _part(500, 4500 - cm_kw, ((CM, cm_kw),)),
                charge,
                _stack(1000, 9000 - cm_kw, ((CM, cm_kw),)),
            ),
        },
        {
            # Held energy: the CM slice for 60 min, the local-flex slice for 120 min.
            "terms_id": "cm-flex-peak",
            "terms": _terms(
                10000,
                5000,
                "firm",
                _part(500, 2500 - cm_kw, ((CM, cm_kw), (FLEX, 2000))),
                charge,
                _stack(1000, 5000 - cm_kw, ((CM, cm_kw), (FLEX, 4000))),
            ),
        },
        {
            "terms_id": "maintenance",
            "terms": _terms(
                10000,
                5000,
                "firm",
                _part(500, 2000, withheld=maintenance_withheld),
                _part(0, 2500, withheld=maintenance_withheld),
                _stack(1000, 4000, withheld=5000),
            ),
        },
        {
            # Overnight the import limit rests on the non-firm tranche (10 MW firm + 20 MW
            # non-firm in the IC-1 agreement); the fail-safe limit stays the firm 10 MW.
            "terms_id": "night-non-firm",
            "terms": _terms(25000, 5000, "non_firm", _part(500, 4500), charge, _stack(1000, 9000)),
        },
    ]


#: A planned maintenance day, carried by both hybrid envelopes (the successor keeps the plan).
_MAINTENANCE = {
    "override_id": "ovr-maintenance-2027-07-06",
    "start": "2027-07-06T07:00:00Z",
    "end": "2027-07-06T15:00:00Z",
    "terms_id": "maintenance",
    "reason": "synthetic fixture: planned maintenance of one inverter block",
}


def _hybrid_services() -> list[dict[str, Any]]:
    return [
        _service(
            CM,
            "CAPACITY_MARKET",
            60,
            "emr_settlement",
            {
                "kind": "evidenced",
                "evidence": {
                    "kind": "evidence_ref",
                    "contract": "IC-4",
                    "contract_version": "1.1.0",
                    "evidence_hash": _placeholder_hash("capacity agreement, hybrid site"),
                },
            },
            _unknown("no underwritten value is stated"),
        ),
        _service(
            FLEX,
            "LOCAL_FLEX_PICLO",
            120,
            "dno",
            _assumed("confirm a DNO flexibility award covering winter weekday peaks"),
            _na("the local-flex service is not underwritten in this fixture"),
        ),
    ]


def build_hybrid() -> dict[str, Any]:
    return _envelope(
        envelope_id="ic3-synthetic-hybrid-bess-2027",
        site_id="SYNTHETIC-SITE-HYBRID",
        record_class="synthetic_fixture",
        source=_source(
            "hybrid site",
            ALL_STREAMS_BASIS,
            ALL_STREAMS_LABEL,
            agreement=_agreement_ref("1.1.0", IC1_HYBRID_V110_HASH),
            scenario_set={
                "kind": "scenario_set_ref",
                "contract": "IC-2",
                "contract_version": "1.1.0",
                "scenario_set_hash": IC2_HYBRID_V110_HASH,
            },
            library=_unknown("the constraint library is not yet published (WP-SIZE-02)"),
        ),
        supersedes=_na("the first envelope for this site"),
        valid_from="2027-01-01T00:00:00Z",
        valid_until="2028-01-01T00:00:00Z",
        rated_discharge_kw=5000,
        rated_charge_kw=5000,
        usable_energy_kwh=10000,
        fail_safe={
            "firm_import_limit_kw": 10000,
            "resilience_floor_kwh": 1000,
            "markets": "declared_unavailable",
        },
        services=_hybrid_services(),
        thermal=_na("no controllable cooling plant at this site"),
        seasons=_HYBRID_SEASONS,
        day_groups=_WEEK,
        bands=_HYBRID_BANDS,
        cells=_cells(_HYBRID_SEASONS, _WEEK, _HYBRID_BANDS, _HYBRID_GRID),
        terms=_hybrid_terms(1000),
        overrides=[_MAINTENANCE],
    )


def build_hybrid_successor() -> dict[str, Any]:
    first = _contracts().DispatchEnvelope.model_validate_json(json.dumps(build_hybrid()))
    return _envelope(
        envelope_id="ic3-synthetic-hybrid-bess-2027-h2",
        site_id="SYNTHETIC-SITE-HYBRID",
        record_class="synthetic_fixture",
        source=_source(
            "hybrid site, re-assessment",
            ALL_STREAMS_BASIS,
            ALL_STREAMS_LABEL,
            agreement=_agreement_ref("1.1.0", IC1_HYBRID_V110_HASH),
            scenario_set={
                "kind": "scenario_set_ref",
                "contract": "IC-2",
                "contract_version": "1.1.0",
                "scenario_set_hash": IC2_HYBRID_V110_HASH,
            },
            library=_unknown("the constraint library is not yet published (WP-SIZE-02)"),
        ),
        supersedes=first.content_hash(),
        valid_from="2027-07-01T00:00:00Z",
        valid_until="2028-07-01T00:00:00Z",
        rated_discharge_kw=5000,
        rated_charge_kw=5000,
        usable_energy_kwh=10000,
        fail_safe={
            "firm_import_limit_kw": 10000,
            "resilience_floor_kwh": 1000,
            "markets": "declared_unavailable",
        },
        services=_hybrid_services(),
        thermal=_na("no controllable cooling plant at this site"),
        seasons=_HYBRID_SEASONS,
        day_groups=_WEEK,
        bands=_HYBRID_BANDS,
        cells=_cells(_HYBRID_SEASONS, _WEEK, _HYBRID_BANDS, _HYBRID_GRID),
        terms=_hybrid_terms(1500),
        overrides=[_MAINTENANCE],
    )


# ------------------------------------------------------------------ the data centre


def _thermal(band_max: int, commitments: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
    return {
        "kind": "thermal_terms",
        "band": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": band_max},
        "commitments": [
            {"service_id": DFS, "reduction_kw": kw, "duration_min": minutes}
            for kw, minutes in commitments
        ],
    }


def build_dc_thermal() -> dict[str, Any]:
    seasons = [
        {"season_id": "winter", "months": [1, 2, 3, 11, 12]},
        {"season_id": "shoulder", "months": [4, 5, 9, 10]},
        {"season_id": "summer", "months": [6, 7, 8]},
    ]
    groups = [{"day_group_id": "all-week", "iso_weekdays": [1, 2, 3, 4, 5, 6, 7]}]
    bands = [
        _band("off-peak", 0, 960),
        _band("peak", 960, 1140),
        _band("flex-window", 1140, 1260),
        _band("late", 1260, 1440),
    ]
    grid = {
        "winter": {"all-week": ["base", "winter-peak", "winter-dfs", "base"]},
        "shoulder": {"all-week": ["base", "base", "shoulder-dfs", "base"]},
        "summer": {"all-week": ["base", "base", "base", "base"]},
    }
    charge = _part(0, 1000)
    # Turn-down windows run 19:00-21:00 local, inside the IC-11 surface's evening band, where it
    # declares 600 kW (winter) and 400 kW (shoulder) for 60 minutes; the commitments are below.
    # The operating band during a turn-down is tighter than the 18.0-25.0 °C binding band.
    terms = [
        {
            "terms_id": "base",
            "terms": _terms(
                3000, 0, "firm", _part(200, 800), charge, _stack(400, 1600), _thermal(250)
            ),
        },
        {
            "terms_id": "shoulder-dfs",
            "terms": _terms(
                3000,
                0,
                "firm",
                _part(200, 800),
                charge,
                _stack(400, 1600),
                _thermal(240, ((300, 60),)),
            ),
        },
        {
            "terms_id": "winter-dfs",
            "terms": _terms(
                3000,
                0,
                "firm",
                _part(200, 800),
                charge,
                _stack(400, 1600),
                _thermal(240, ((400, 60),)),
            ),
        },
        {
            "terms_id": "winter-peak",
            "terms": _terms(
                3000,
                0,
                "firm",
                _part(200, 300, ((CM, 500),)),
                charge,
                _stack(400, 1100, ((CM, 500),)),
                _thermal(250),
            ),
        },
    ]
    return _envelope(
        envelope_id="ic3-synthetic-dc-chilled-water-2027",
        site_id="SYNTHETIC-DC-CHILLED-WATER",
        record_class="synthetic_fixture",
        source=_source(
            "chilled-water data centre",
            EXPORT_FLOOR_BASIS,
            EXPORT_FLOOR_LABEL,
            agreement=_unknown("no IC-1 agreement fixture exists for this site"),
            scenario_set=_unknown("no IC-2 scenario set exists for this site"),
            library={
                "kind": "constraint_library_pin",
                "library_version": "0.0.0-synthetic",
                "library_hash": _unknown("no library content exists to hash (WP-SIZE-02)"),
                "constraint_ids": [
                    "synthetic_import_limit",
                    "synthetic_soe_floor",
                    "synthetic_thermal_band",
                ],
            },
        ),
        supersedes=_na("the first envelope for this site"),
        valid_from="2027-01-01T00:00:00Z",
        valid_until="2028-01-01T00:00:00Z",
        rated_discharge_kw=1000,
        rated_charge_kw=1000,
        usable_energy_kwh=2000,
        fail_safe={
            "firm_import_limit_kw": 3000,
            "resilience_floor_kwh": 400,
            "markets": "declared_unavailable",
        },
        services=[
            _service(
                CM,
                "CAPACITY_MARKET",
                60,
                "emr_settlement",
                _assumed("confirm a capacity agreement for the battery"),
                _unknown("no underwritten value is stated"),
            ),
            _service(
                DFS,
                "DFS",
                _na("delivered by cooling turn-down only: no battery slice"),
                "neso",
                _assumed("confirm demand-flexibility registration for the cooling plant"),
                _unknown("no underwritten value is stated"),
            ),
        ],
        thermal={
            "kind": "thermal_envelope",
            "surface": {
                "kind": "thermal_flex_availability_ref",
                "contract": "IC-11",
                "contract_version": "1.0.0",
                "availability_hash": IC11_CHILLED_WATER_HASH,
            },
            "binding": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": 250},
        },
        seasons=seasons,
        day_groups=groups,
        bands=bands,
        cells=_cells(seasons, groups, bands, grid),
        terms=terms,
        overrides=[],
    )


# ------------------------------------------------------------------ the clock-change week


def build_clock_week() -> dict[str, Any]:
    seasons = [{"season_id": "all-year", "months": list(range(1, 13))}]
    groups = [{"day_group_id": "all-week", "iso_weekdays": [1, 2, 3, 4, 5, 6, 7]}]
    bands = [_band("night", 0, 420), _band("day", 420, 1440)]
    charge = _part(0, 2000)
    return _envelope(
        envelope_id="ic3-synthetic-firm-clock-change-week",
        site_id="SYNTHETIC-SITE-FIRM",
        record_class="synthetic_fixture",
        source=_source(
            "firm site",
            ALL_STREAMS_BASIS,
            ALL_STREAMS_LABEL,
            agreement=_agreement_ref("1.0.0", IC1_FIRM_HASH),
            scenario_set=_na("a firm connection has no curtailment scenarios"),
            library=_unknown("the constraint library is not yet published (WP-SIZE-02)"),
        ),
        supersedes=_na("the first envelope for this site"),
        valid_from="2027-03-25T00:00:00Z",
        valid_until="2027-04-01T00:00:00Z",
        rated_discharge_kw=2000,
        rated_charge_kw=2000,
        usable_energy_kwh=4000,
        fail_safe={
            "firm_import_limit_kw": 20000,
            "resilience_floor_kwh": 800,
            "markets": "declared_unavailable",
        },
        services=[
            _service(
                CM,
                "CAPACITY_MARKET",
                60,
                "emr_settlement",
                _unknown("no capacity agreement exists"),
                _unknown("no underwritten value is stated"),
            )
        ],
        thermal=_na("no controllable cooling plant at this site"),
        seasons=seasons,
        day_groups=groups,
        bands=bands,
        cells=_cells(seasons, groups, bands, {"all-year": {"all-week": ["night", "day-cm"]}}),
        terms=[
            {
                "terms_id": "day-cm",
                "terms": _terms(
                    20000,
                    2000,
                    "firm",
                    _part(200, 800, ((CM, 1000),)),
                    charge,
                    _stack(800, 2200, ((CM, 1000),)),
                ),
            },
            {
                "terms_id": "night",
                "terms": _terms(20000, 2000, "firm", _part(200, 1800), charge, _stack(800, 3200)),
            },
        ],
        overrides=[],
    )


def build_clock_week_timeline() -> dict[str, Any]:
    c = _contracts()
    envelope = c.DispatchEnvelope.model_validate_json(json.dumps(build_clock_week()))
    data: dict[str, Any] = c.resolve(envelope).model_dump(mode="json")
    return data


BUILDERS = {
    HYBRID: build_hybrid,
    HYBRID_SUCCESSOR: build_hybrid_successor,
    DC_THERMAL: build_dc_thermal,
    CLOCK_WEEK: build_clock_week,
    CLOCK_WEEK_TIMELINE: build_clock_week_timeline,
}


def main() -> None:
    FIXTURES_IC3.mkdir(exist_ok=True)
    for path, build in BUILDERS.items():
        path.write_text(render(build()), encoding="utf-8")


if __name__ == "__main__":
    main()
