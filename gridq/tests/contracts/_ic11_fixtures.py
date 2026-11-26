"""IC-11 synthetic fixtures: committed schema-test data for the thermal-flex contract.

Two availability surfaces and one instruction with three acknowledgements:

- ``ic11_surface_chilled_water_modelled``: a chilled-water site (water-cooled chillers with
  towers, CRAH) with **no trial data**, so the evidence class is ``modelled``. Its summer
  afternoon and peak cells are **zero flex** (assessed: 0 kW).
- ``ic11_surface_metered_trial``: an air-cooled site with a **metered trial** (IC-4 refs to the
  meter records and the baseline method). The trial ran January to March, so only January to
  March cells carry the metered class. Their day cells are **unavailable** (the host allows
  control only overnight). Every April to December cell is **unknown**: ``evidence_class`` is the
  floor for every flex and zero-flex cell, and those months were not observed. Winter is split
  into ``winter_jan_mar`` and ``winter_oct_dec`` for that reason.
- ``ic11_instruction_winter_night`` against the metered surface, and its acknowledgements
  ``ic11_ack_accepted``, ``ic11_ack_partial`` and ``ic11_ack_rejected``.
- ``v1_1_0/ic11_surface_trialled_self_issued`` (IC-11 1.1.0, WP-BO-06): the chilled-water site
  after a July to September trial. Its ``trial_evidence`` and ``baseline_method`` are 1.1.0
  references to Neura's own self-issued trial record and baseline method
  (``v1_1_0/ic4_self_issued_*``). Only the trialled months carry flex or zero-flex cells.

All values are synthetic. Site ids start ``SYNTHETIC-``; evidence hashes are SHA-256 of a
synthetic placeholder text, not of any real document; the ASHRAE numbers are as commonly cited
and were not verified against any edition of the standard, so the source string names no
edition (pre-tag review F4: the 60 % RH figure may belong to an earlier edition than the one
first cited). ``test_ic11_*`` asserts that the committed files equal a fresh build. Regenerate
with ``python tests/contracts/_ic11_fixtures.py``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SURFACE_MODELLED = FIXTURES / "ic11_surface_chilled_water_modelled.synthetic.json"
SURFACE_METERED = FIXTURES / "ic11_surface_metered_trial.synthetic.json"
INSTRUCTION = FIXTURES / "ic11_instruction_winter_night.synthetic.json"
ACK_ACCEPTED = FIXTURES / "ic11_ack_accepted.synthetic.json"
ACK_PARTIAL = FIXTURES / "ic11_ack_partial.synthetic.json"
ACK_REJECTED = FIXTURES / "ic11_ack_rejected.synthetic.json"
SURFACE_TRIALLED_V110 = FIXTURES / "v1_1_0" / "ic11_surface_trialled_self_issued.synthetic.json"

#: Content hashes of the two self-issued IC-4 1.1.0 fixtures the trialled surface rests on.
TRIAL_RECORD_HASH = "sha256:7b4ac2d71f2e1d686f4776efdc44769cabe2fae419f52085a59d4f42c1b4a260"
BASELINE_METHOD_HASH = "sha256:7db1389a7de85da60c3d797eb0c1ecd4ed549b853ef879a8f24460ff1e6bf6ea"

ASHRAE_SOURCE = (
    "synthetic fixture: envelope as commonly cited for ASHRAE TC9.9 Thermal Guidelines for Data "
    "Processing Environments; edition not stated; figures unverified against the standard"
)


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _placeholder_hash(label: str) -> str:
    return "sha256:" + hashlib.sha256(f"SYNTHETIC PLACEHOLDER: {label}".encode()).hexdigest()


def _ref(label: str) -> dict[str, Any]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": "1.0.0",
        "evidence_hash": _placeholder_hash(label),
    }


def _rebound(kw: int, minutes: int) -> dict[str, int]:
    """A synthetic rebound: ~80% of the shed energy comes back at ~60% of the shed kW."""
    energy = kw * minutes * 8 // 600
    peak = max(1, kw * 6 // 10)
    recovery = -(-energy * 60 // peak) + 10
    return {"energy_kwh": energy, "peak_kw": peak, "recovery_min": recovery}


def _flex(*points: tuple[int, int]) -> dict[str, Any]:
    return {
        "kind": "flex",
        "curve": [
            {"duration_min": d, "reduction_kw": kw, "rebound": _rebound(kw, d)} for d, kw in points
        ],
    }


def _cells(seasons: list[dict[str, Any]], bands: list[dict[str, Any]], grid: Any) -> list[Any]:
    return [
        {
            "season_id": s["season_id"],
            "band_id": b["band_id"],
            "availability": grid[s["season_id"]][b["band_id"]],
        }
        for s in seasons
        for b in bands
    ]


def _band(band_id: str, start: int, end: int) -> dict[str, Any]:
    return {"band_id": band_id, "start_minute": start, "end_minute": end}


def build_surface_modelled() -> dict[str, Any]:
    seasons = [
        {"season_id": "winter", "months": [1, 2, 3, 11, 12]},
        {"season_id": "shoulder", "months": [4, 5, 9, 10]},
        {"season_id": "summer", "months": [6, 7, 8]},
    ]
    bands = [
        _band("night", 0, 360),
        _band("morning", 360, 720),
        _band("afternoon", 720, 960),
        _band("peak", 960, 1140),
        _band("evening", 1140, 1440),
    ]
    zero = {
        "kind": "zero_flex",
        "reason": "synthetic: chillers at full load on the summer afternoon; no headroom below "
        "the binding inlet limit",
    }
    grid = {
        # Long holds overnight, a few minutes at the evening peak (the A4-03 Cardiff shape).
        "winter": {
            "night": _flex((30, 900), (120, 600), (408, 250)),
            "morning": _flex((30, 700), (90, 400)),
            "afternoon": _flex((30, 500), (60, 250)),
            "peak": _flex((12, 300)),
            "evening": _flex((60, 600), (240, 300)),
        },
        "shoulder": {
            "night": _flex((30, 700), (120, 450), (300, 150)),
            "morning": _flex((30, 500), (60, 300)),
            "afternoon": _flex((30, 300)),
            "peak": _flex((10, 150)),
            "evening": _flex((60, 400), (180, 200)),
        },
        "summer": {
            "night": _flex((30, 400), (60, 250)),
            "morning": _flex((30, 200)),
            "afternoon": zero,
            "peak": zero,
            "evening": _flex((30, 150)),
        },
    }
    return {
        "contract": "IC-11",
        "contract_version": "1.0.0",
        "surface_id": "ic11-synthetic-chilled-water-modelled",
        "site_id": "SYNTHETIC-DC-CHILLED-WATER",
        "record_class": "synthetic_fixture",
        "topology": ["crah_crac", "water_cooled_chillers_with_towers"],
        "cooling_electrical_capacity_kw": 1800,
        "issued_at": "2026-11-20T09:00:00Z",
        "valid_from": "2027-01-01T00:00:00Z",
        "valid_until": "2028-01-01T00:00:00Z",
        "clock": "Europe/London",
        "evidence_class": "modelled",
        "evidence": {
            "kind": "modelled",
            "model": "thermal_plant_model",
            "model_version": "0.0.0-synthetic",
            "input_hash": _placeholder_hash("thermal_plant_model inputs, chilled-water site"),
            "calibration_evidence": [],
        },
        "limits": {
            "ashrae": {
                "ashrae_class": "A1",
                "envelope": "recommended",
                "temperature": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": 270},
                "humidity": {
                    "kind": "humidity_limit",
                    "min_dew_point_tenths_c": -90,
                    "max_dew_point_tenths_c": 150,
                    "max_relative_humidity_percent": 60,
                },
                "max_rate_of_change_tenths_c_per_hour": 200,
                "source": ASHRAE_SOURCE,
            },
            "customer_sla": {
                "kind": "unknown",
                "reason": "synthetic: the host's SLA has not been received",
            },
            "binding": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": 250},
            "binding_source": "operator_margin",
            "chiller_min_run_min": 15,
            "chiller_min_off_min": 10,
        },
        "response": {"kind": "response_terms", "min_notice_min": 5, "ramp_min": 5},
        "seasons": seasons,
        "bands": bands,
        "cells": _cells(seasons, bands, grid),
    }


def build_surface_metered() -> dict[str, Any]:
    # The trial observed January to March only. evidence_class is the floor for every flex and
    # zero-flex cell, so October to December, though winter, are a season of unknown cells.
    seasons = [
        {"season_id": "winter_jan_mar", "months": [1, 2, 3]},
        {"season_id": "summer", "months": [4, 5, 6, 7, 8, 9]},
        {"season_id": "winter_oct_dec", "months": [10, 11, 12]},
    ]
    bands = [
        _band("night", 0, 420),
        _band("day", 420, 960),
        _band("peak", 960, 1140),
        _band("evening", 1140, 1440),
    ]
    off_window = {
        "kind": "unavailable",
        "reason_code": "outside_agreed_window",
        "detail": "synthetic: the host allows closed-loop control 19:00-07:00 only at this stage "
        "of staged authority (TF-06)",
    }
    not_observed = {
        "kind": "unknown",
        "reason": "synthetic: the metered trial ran January to March; no summer events observed",
    }
    not_observed_late_winter = {
        "kind": "unknown",
        "reason": "synthetic: the metered trial ran January to March; no October to December "
        "events observed, so these cells are not served as metered",
    }
    grid = {
        "winter_jan_mar": {
            "night": _flex((30, 800), (120, 550), (300, 200)),
            "day": off_window,
            "peak": off_window,
            "evening": _flex((30, 600), (90, 350)),
        },
        "summer": {b["band_id"]: not_observed for b in bands},
        "winter_oct_dec": {b["band_id"]: not_observed_late_winter for b in bands},
    }
    return {
        "contract": "IC-11",
        "contract_version": "1.0.0",
        "surface_id": "ic11-synthetic-metered-trial",
        "site_id": "SYNTHETIC-DC-AIR-COOLED-METERED",
        "record_class": "synthetic_fixture",
        "topology": ["air_cooled_chillers", "crah_crac"],
        "cooling_electrical_capacity_kw": 1200,
        "issued_at": "2026-04-02T12:00:00Z",
        "valid_from": "2026-04-02T12:00:00Z",
        "valid_until": "2027-04-01T00:00:00Z",
        "clock": "Europe/London",
        "evidence_class": "metered",
        "evidence": {
            "kind": "metered",
            "meter_evidence": sorted(
                (
                    _ref("meter commissioning certificate, chiller feeders"),
                    _ref("metered event data"),
                ),
                key=lambda r: r["evidence_hash"],
            ),
            "baseline_method": _ref("cooling baseline method, registered before the trial"),
            "observed_from": "2026-01-05T00:00:00Z",
            "observed_until": "2026-03-30T00:00:00Z",
            "n_events": 24,
        },
        "limits": {
            "ashrae": {
                "ashrae_class": "H1",
                "envelope": "recommended",
                "temperature": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": 220},
                "humidity": {"kind": "unknown", "reason": "synthetic: not transcribed"},
                "max_rate_of_change_tenths_c_per_hour": {
                    "kind": "unknown",
                    "reason": "synthetic: not transcribed",
                },
                "source": ASHRAE_SOURCE,
            },
            "customer_sla": {
                "kind": "customer_sla",
                "temperature": {"point": "it_inlet", "min_tenths_c": 180, "max_tenths_c": 230},
                "humidity": {
                    "kind": "humidity_limit",
                    "min_dew_point_tenths_c": -90,
                    "max_dew_point_tenths_c": 150,
                    "max_relative_humidity_percent": 60,
                },
                "evidence": _ref("customer SLA, cooling schedule"),
            },
            "binding": {"point": "it_inlet", "min_tenths_c": 185, "max_tenths_c": 215},
            "binding_source": "operator_margin",
            "chiller_min_run_min": 10,
            "chiller_min_off_min": {
                "kind": "unknown",
                "reason": "synthetic: not in the O&M manual",
            },
        },
        "response": {"kind": "response_terms", "min_notice_min": 15, "ramp_min": 3},
        "seasons": seasons,
        "bands": bands,
        "cells": _cells(seasons, bands, grid),
    }


def _ref_v110(evidence_hash: str) -> dict[str, Any]:
    return {
        "kind": "evidence_ref",
        "contract": "IC-4",
        "contract_version": "1.1.0",
        "evidence_hash": evidence_hash,
    }


def build_surface_trialled_self_issued() -> dict[str, Any]:
    """IC-11 1.1.0: the chilled-water site's limits, trialled July to September 2026."""
    seasons = [
        {"season_id": "jan_jun", "months": [1, 2, 3, 4, 5, 6]},
        {"season_id": "jul_sep", "months": [7, 8, 9]},
        {"season_id": "oct_dec", "months": [10, 11, 12]},
    ]
    bands = [_band("night", 0, 360), _band("day", 360, 1140), _band("evening", 1140, 1440)]
    not_trialled = {
        "kind": "unknown",
        "reason": "synthetic: the trial ran July to September; these months were not trialled",
    }
    grid = {
        "jan_jun": {b["band_id"]: not_trialled for b in bands},
        "jul_sep": {
            "night": _flex((30, 400), (60, 250)),
            "day": {
                "kind": "zero_flex",
                "reason": "synthetic: chillers at full load through the trial's summer days",
            },
            "evening": _flex((30, 150)),
        },
        "oct_dec": {b["band_id"]: not_trialled for b in bands},
    }
    data = build_surface_modelled()
    data.update(
        {
            "contract_version": "1.1.0",
            "surface_id": "ic11-synthetic-chilled-water-trialled-self-issued",
            "issued_at": "2026-10-26T09:00:00Z",
            "valid_from": "2026-11-01T00:00:00Z",
            "valid_until": "2027-11-01T00:00:00Z",
            "evidence_class": "trialled",
            "evidence": {
                "kind": "trialled",
                "trial_evidence": [_ref_v110(TRIAL_RECORD_HASH)],
                "baseline_method": _ref_v110(BASELINE_METHOD_HASH),
                "observed_from": "2026-07-01T00:00:00Z",
                "observed_until": "2026-09-30T00:00:00Z",
                "n_events": 12,
            },
            "seasons": seasons,
            "bands": bands,
            "cells": _cells(seasons, bands, grid),
        }
    )
    return data


def _contracts() -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from gridq.contracts import thermal_flex

    return thermal_flex


def build_instruction() -> dict[str, Any]:
    tf = _contracts()
    surface = tf.ThermalFlexAvailability.model_validate_json(json.dumps(build_surface_metered()))
    return {
        "contract": "IC-11",
        "contract_version": "1.0.0",
        "instruction_id": "ic11-synthetic-instr-0001",
        "site_id": "SYNTHETIC-DC-AIR-COOLED-METERED",
        "availability": tf.availability_ref(surface).model_dump(mode="json"),
        "issued_at": "2027-01-14T23:30:00Z",
        "issuer_role": "neura_dispatch",
        "issuer_id": "svc-synthetic-b2-dispatch",
        "on_behalf_of": "dno",
        "stream": "LOCAL_FLEX_PICLO",
        "reduction_kw": 500,
        "start": "2027-01-15T00:15:00Z",
        "duration_min": 120,
        "ack_deadline": "2027-01-14T23:40:00Z",
    }


def _ack(ack_id: str, at: str, response: dict[str, Any]) -> dict[str, Any]:
    tf = _contracts()
    instruction = tf.FlexInstruction.model_validate_json(json.dumps(build_instruction()))
    return {
        "contract": "IC-11",
        "contract_version": "1.0.0",
        "ack_id": ack_id,
        "instruction_id": instruction.instruction_id,
        "instruction_hash": instruction.content_hash(),
        "acknowledged_at": at,
        "responder_id": "thermaflex-synthetic-site-controller",
        "response": response,
    }


def build_ack_accepted() -> dict[str, Any]:
    return _ack("ic11-synthetic-ack-accepted", "2027-01-14T23:31:12Z", {"kind": "accepted"})


def build_ack_partial() -> dict[str, Any]:
    return _ack(
        "ic11-synthetic-ack-partial",
        "2027-01-14T23:31:40Z",
        {
            "kind": "partially_accepted",
            "accepted_kw": 350,
            "reason_code": "it_safety_limit",
            "detail": "synthetic: hall 2 inlet at 21.0 C; holding 500 kW for 120 min would pass "
            "the 21.5 C binding limit",
        },
    )


def build_ack_rejected() -> dict[str, Any]:
    return _ack(
        "ic11-synthetic-ack-rejected",
        "2027-01-14T23:44:05Z",
        {
            "kind": "rejected",
            "reason_code": "rebound_recovery_in_progress",
            "detail": "synthetic: the previous event's rebound is still recovering",
        },
    )


BUILDERS = {
    SURFACE_MODELLED: build_surface_modelled,
    SURFACE_METERED: build_surface_metered,
    INSTRUCTION: build_instruction,
    ACK_ACCEPTED: build_ack_accepted,
    ACK_PARTIAL: build_ack_partial,
    ACK_REJECTED: build_ack_rejected,
}
#: The IC-11 1.1.0 fixtures (WP-BO-06), kept apart so `BUILDERS` and its test are unchanged.
BUILDERS_V110 = {
    SURFACE_TRIALLED_V110: build_surface_trialled_self_issued,
}


def main() -> None:
    for path, build in {**BUILDERS, **BUILDERS_V110}.items():
        path.write_text(render(build()), encoding="utf-8")


if __name__ == "__main__":
    main()
