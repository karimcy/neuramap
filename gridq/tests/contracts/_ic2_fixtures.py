"""IC-2 synthetic-shape fixtures: committed, schema-test data with the shape of the real sets.

The real UKPN / NPg sets are git-ignored (``fixtures/real/README.md``). These committed sets
copy their *shape* (sub-hour events at near-full depth, mixed zero and non-zero scenarios,
multi-hour outage events) with **synthetic values**, so the conformance tests and any consumer
can run in CI without the gated data. ``test_ic2_*`` asserts that the committed files equal a
fresh build. Regenerate with ``python tests/contracts/_ic2_fixtures.py``.

``fixtures/v1_1_0/ic2_modelled_v110`` (WP-BO-06) is the modelled set at IC-2 1.1.0, under the
IC-1 1.1.0 evidence-backed hybrid agreement (``fixtures/v1_1_0/ic1_hybrid_evidence_backed``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from _ic2_real import _scenario, _z, render

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPLAY = FIXTURES / "ic2_replay_three_years.synthetic.json"
MODELLED = FIXTURES / "ic2_modelled_hh_only.synthetic.json"
MODELLED_V110 = FIXTURES / "v1_1_0" / "ic2_modelled_v110.synthetic.json"
#: Content hash of ``fixtures/v1_1_0/ic1_hybrid_evidence_backed.synthetic.json`` (IC-1 1.1.0).
IC1_HYBRID_EVIDENCE_BACKED_HASH = (
    "sha256:122618785497c4f998cbdb76b49746be63415bf56cc09c4a6187a333c3d7c6b1"
)

UNKNOWN_NOTICE = {"kind": "unknown", "reason": "synthetic: notice not modelled in this fixture"}


def _ev(start: datetime, minutes: float, depth: float, ref: str, notice: Any) -> dict[str, Any]:
    return {
        "start": _z(start),
        "end": _z(start + timedelta(minutes=minutes)),
        "mean_depth_mw": depth,
        "peak_depth_mw": depth + 0.25,
        "notice_s": notice,
        "source_ref": ref,
    }


def build_replay() -> dict[str, Any]:
    """Three synthetic 'observed years', one day each, equal weights 1/3, minute + half-hourly."""
    plan = {
        2031: [(11, 52, 4.0, 3.5), (13, 20, 29.5, 3.75), (14, 26, 33.5, 3.8)],
        2032: [],
        2033: [(10, 43, 8.75, 3.8), (12, 13, 22.0, 3.8), (23, 15, 45.0, 2.5)],
    }
    w = Fraction(1, 3)
    scenarios = []
    for y, evs in plan.items():
        day = datetime(y, 6, 11, tzinfo=UTC)
        events = [
            _ev(day.replace(hour=h, minute=m), dur, d, f"SYNTH-EVT-{y}-{i}", UNKNOWN_NOTICE)
            for i, (h, m, dur, d) in enumerate(evs)
        ]
        scenarios.append(_scenario(f"y{y}", f"Synthetic year {y}", w, day, 48, events))
    return {
        "contract": "IC-2",
        "contract_version": "1.0.0",
        "set_id": "ic2-synthetic-replay-three-years",
        "site_id": "SYNTHETIC-SITE-NON-FIRM-DC",
        "direction": "import",
        "signal_basis": "demand_import_restriction",
        "depth_definition": "synthetic: MW below the agreed import capacity",
        "reference_capacity_mw": 4.0,
        "connection_agreement": {
            "kind": "not_applicable",
            "reason": "synthetic fixture; no agreement exists",
        },
        "evidence_class": "synthetic_fixture",
        "provenance": {
            "kind": "synthetic_fixture",
            "statement": "Schema-test data; not a site anyone holds. Values are synthetic.",
            "modelled_on": "the shape of the git-ignored UKPN DER4 replay (fixtures/real/)",
        },
        "weight_basis": "equal_over_observed_periods",
        "weight_rule": "synthetic: one scenario per synthetic year, weights equal (1/3)",
        "n_half_hours": 48,
        "scenarios": scenarios,
    }


def build_modelled() -> dict[str, Any]:
    """Three scenarios, unequal model weights (1/2, 3/10, 1/5), half-hourly only, known notice."""
    start = datetime(2031, 1, 14, tzinfo=UTC)
    plan = [
        ("p1-calm", Fraction(1, 2), []),
        ("p2-outage", Fraction(3, 10), [(7, 30, 360.0, 7.7, 900.0)]),
        ("p3-cold-snap", Fraction(1, 5), [(16, 0, 180.0, 5.0, 120.0), (30, 0, 600.0, 7.7, 0.0)]),
    ]
    scenarios = []
    for sid, w, evs in plan:
        events = []
        for i, (h, m, dur, d, notice) in enumerate(evs):
            t = start + timedelta(hours=h, minutes=m)
            e = _ev(t, dur, d, f"SYNTH-{sid}-{i}", notice)
            e["source_ref"] = {"kind": "not_applicable", "reason": "modelled event"}
            events.append(e)
        sc = _scenario(sid, f"Synthetic modelled scenario {sid}", w, start, 96, events)
        sc["minute"] = {"kind": "not_applicable", "reason": "synthetic: half-hourly model"}
        scenarios.append(sc)
    return {
        "contract": "IC-2",
        "contract_version": "1.0.0",
        "set_id": "ic2-synthetic-modelled-hh-only",
        "site_id": "SYNTHETIC-SITE-HYBRID",
        "direction": "import",
        "signal_basis": "loading_headroom",
        "depth_definition": "synthetic: MW below the agreed non-firm import",
        "reference_capacity_mw": 20.0,
        "connection_agreement": {
            "kind": "connection_agreement_ref",
            "contract": "IC-1",
            "contract_version": "1.0.0",
            # content hash of tests/contracts/fixtures/ic1_archetype_hybrid.synthetic.json
            "agreement_hash": (
                "sha256:c6ee3fb9bc76f2811be935c53cc8549b19a0b66aacffd57d567c33ac35891505"
            ),
        },
        "evidence_class": "synthetic_fixture",
        "provenance": {
            "kind": "synthetic_fixture",
            "statement": "Schema-test data; not a site anyone holds. Values are synthetic.",
            "modelled_on": "the NPg outage-event shape (fixtures/real/) and A1-03 scenario output",
        },
        "weight_basis": "model_probability",
        "weight_rule": "synthetic: stated model probabilities 1/2, 3/10, 1/5",
        "n_half_hours": 96,
        "scenarios": scenarios,
    }


def build_modelled_v110() -> dict[str, Any]:
    """The modelled set at IC-2 1.1.0, with a 1.1.0 reference to the evidence-backed agreement."""
    data = build_modelled()
    data["contract_version"] = "1.1.0"
    data["set_id"] = "ic2-synthetic-modelled-v110"
    data["connection_agreement"] = {
        "kind": "connection_agreement_ref",
        "contract": "IC-1",
        "contract_version": "1.1.0",
        "agreement_hash": IC1_HYBRID_EVIDENCE_BACKED_HASH,
    }
    return data


def main() -> None:
    REPLAY.write_text(render(build_replay()), encoding="utf-8")
    MODELLED.write_text(render(build_modelled()), encoding="utf-8")
    MODELLED_V110.write_text(render(build_modelled_v110()), encoding="utf-8")


if __name__ == "__main__":
    main()
