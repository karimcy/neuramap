"""IC-2 real-data fixtures: fetch recorded DNO responses and build scenario sets from them.

The responses and the sets built from them live in ``fixtures/real/``, which is **git-ignored**:
UKPN and NPg data are under the Atlas eyes-only gate (Atlas ``research/DATA_REQUESTS.md``,
"Gate"; ``atlas/curated/data_authorisations.json``: ``not_recorded``) until Huwise's written
authorisation is recorded. Only ``fixtures/real/MANIFEST.json`` (hashes, URLs, retrieval
times, licences; no rows) and ``README.md`` are committed. See that README.

Two steps, kept apart so the derivation is testable without a network:

- ``fetch()`` makes the HTTP requests (keys from the Atlas ``.env``, sent as an
  ``Authorization: Apikey`` header and never written anywhere) and saves the raw response bytes
  plus ``retrieval.json`` (URL, retrieval time, SHA-256). Run by hand only::

      python tests/contracts/_ic2_real.py fetch   # needs network + the Atlas keys
      python tests/contracts/_ic2_real.py build   # pure: responses -> IC-2 sets + MANIFEST

- ``build_ukpn`` / ``build_npg`` are pure functions of the recorded bytes. The conformance tests
  rebuild each set from its response and require the same content hash.

Derivation (both sources): an event's depth is the publisher's **average access reduction**,
held uniform over the published ``[start, end)``; an interval's value is that depth times the
fraction of the interval the event covers, summed over events. Nothing else is inferred.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

REAL = Path(__file__).resolve().parent / "fixtures" / "real"
MANIFEST = REAL / "MANIFEST.json"
RETRIEVAL = REAL / "retrieval.json"
ATLAS_ENV = Path(
    os.environ.get("ATLAS_ENV", Path(__file__).resolve().parents[3] / "atlas" / ".env")
)

UKPN = "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets"
NPG = "https://northernpowergrid.opendatasoft.com/api/explore/v2.1/catalog/datasets"
UKPN_DATASET = "ukpn-curtailment-events-site-specific"
NPG_DATASET = "curtailment-events-site-specific"

UKPN_DER = "DER4"
UKPN_WINDOW = ((6, 11), (6, 15))  # 11 June 00:00Z to 15 June 00:00Z: 4 days, 192 half-hours
NPG_CUSTOMER = "DER-0000099"
NPG_HORIZON = timedelta(days=2)  # 96 half-hours from 00:00Z on the day the event starts

GATE = (
    "Eyes-only (Neura Constraint Atlas, research/DATA_REQUESTS.md 'Gate', DR-01/DR-03; "
    "atlas/curated/data_authorisations.json status not_recorded): Huwise's written "
    "authorisation for automated retrieval is not recorded. Test fixture, internal; kept out "
    "of git; not to be shown to anyone but the user until the authorisation is recorded."
)

#: Files fetch() writes, by role.
FILES = {
    "ukpn_events": "ukpn_der4_june_events.response.json",
    "ukpn_years": "ukpn_der4_years.response.json",
    "ukpn_meta": "ukpn_dataset_meta.response.json",
    "npg_events": "npg_der0000099_events.response.json",
    "npg_meta": "npg_dataset_meta.response.json",
}
SETS = {
    "ukpn": "ic2_ukpn_der4_norwich_june.real.json",
    "npg": "ic2_npg_der0000099_norton_events.real.json",
}


# ----------------------------------------------------------------------------- fetch (network)


def _keys() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ATLAS_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


def _ukpn_window_sql() -> str:
    (m0, d0), (m1, d1) = UKPN_WINDOW
    ors = [
        f"(start_time_utc < date'{y}-{m1:02d}-{d1:02d}T00:00:00Z' AND "
        f"end_time_utc > date'{y}-{m0:02d}-{d0:02d}T00:00:00Z')"
        for y in range(2023, 2027)
    ]
    return f'der_name = "{UKPN_DER}" AND (' + " OR ".join(ors) + ")"


def requests() -> dict[str, str]:
    """Role -> request URL (no credentials)."""
    q = urllib.parse.urlencode
    return {
        "ukpn_events": f"{UKPN}/{UKPN_DATASET}/exports/json?"
        + q({"where": _ukpn_window_sql(), "order_by": "start_time_utc"}),
        "ukpn_years": f"{UKPN}/{UKPN_DATASET}/records?"
        + q(
            {
                "select": "year(start_time_utc) as year, count(*) as events",
                "where": f'der_name = "{UKPN_DER}"',
                "group_by": "year(start_time_utc)",
                "order_by": "year(start_time_utc)",
                "limit": 100,
            }
        ),
        "ukpn_meta": f"{UKPN}/{UKPN_DATASET}",
        "npg_events": f"{NPG}/{NPG_DATASET}/exports/json?"
        + q(
            {
                "where": f'customer_unique_identifier = "{NPG_CUSTOMER}"',
                "order_by": "start_time_utc",
            }
        ),
        "npg_meta": f"{NPG}/{NPG_DATASET}",
    }


def fetch() -> None:
    keys = _keys()
    REAL.mkdir(exist_ok=True)
    record: dict[str, Any] = {}
    for role, url in requests().items():
        key = keys["UKPN_API_KEY" if role.startswith("ukpn") else "NPG_API_KEY"]
        req = urllib.request.Request(url, headers={"Authorization": f"Apikey {key}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            status = r.status
        at = datetime.now(UTC).replace(microsecond=0)
        (REAL / FILES[role]).write_bytes(raw)
        record[role] = {
            "file": FILES[role],
            "url": url,
            "http_status": status,
            "retrieved_at": at.isoformat().replace("+00:00", "Z"),
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    RETRIEVAL.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- build (pure)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _z(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _series(start: datetime, n: int, step_s: int, events: list[dict[str, Any]]) -> list[float]:
    """Interval value = sum over events of depth x (overlap seconds / interval seconds)."""
    out = [0.0] * n
    for e in events:
        es, ee, depth = _t(e["start"]), _t(e["end"]), e["mean_depth_mw"]
        i0 = max(0, int((es - start).total_seconds() // step_s))
        i1 = min(n, int(-(-(ee - start).total_seconds() // step_s)))
        for i in range(i0, i1):
            t0 = start + timedelta(seconds=i * step_s)
            t1 = t0 + timedelta(seconds=step_s)
            ov = (min(ee, t1) - max(es, t0)).total_seconds()
            if ov > 0:
                out[i] += depth * ov / step_s
    return out


def _steps(values: list[float]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for i, v in enumerate(values):
        v = 0.0 if v == 0 else v
        if not steps or steps[-1]["mw"] != v:
            steps.append({"index": i, "mw": v})
    return steps


def _scenario(
    sid: str, label: str, weight: Fraction, start: datetime, n_hh: int, events: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "scenario_id": sid,
        "label": label,
        "weight": {"numerator": weight.numerator, "denominator": weight.denominator},
        "period_start": _z(start),
        "half_hourly": {
            "resolution_s": 1800,
            "n_intervals": n_hh,
            "steps": _steps(_series(start, n_hh, 1800, events)),
        },
        "minute": {
            "resolution_s": 60,
            "n_intervals": n_hh * 30,
            "steps": _steps(_series(start, n_hh * 30, 60, events)),
        },
        "events": events,
    }


def _source(
    publisher: str, dataset: str, rec: dict[str, Any], licence: str, attribution: str
) -> dict[str, Any]:
    return {
        "publisher": publisher,
        "publisher_role": "dno",
        "dataset": dataset,
        "url": rec["url"],
        "retrieved_at": rec["retrieved_at"],
        "response_sha256": rec["sha256"],
        "licence": licence,
        "attribution": attribution,
        "distribution_gate": GATE,
        "registered_evidence": {
            "kind": "unknown",
            "reason": "the recorded response is not yet registered as an IC-4 evidence object",
        },
    }


def _licence(meta_raw: bytes) -> str:
    meta = json.loads(meta_raw)
    lic = (meta.get("metas", {}).get("default", {}) or {}).get("license")
    return str(lic) if lic else "not stated in the dataset metadata"


def build_ukpn(
    events_raw: bytes, years_raw: bytes, meta_raw: bytes, rec: dict[str, Any]
) -> dict[str, Any]:
    rows = json.loads(events_raw)
    # The portal returns the value under the expression key and leaves the "year" alias null.
    years = sorted(int(r["year(start_time_utc)"]) for r in json.loads(years_raw)["results"])
    (m0, d0), (m1, d1) = UKPN_WINDOW
    scenarios = []
    w = Fraction(1, len(years))
    for y in years:
        ws, we = datetime(y, m0, d0, tzinfo=UTC), datetime(y, m1, d1, tzinfo=UTC)
        evs = []
        for r in sorted(rows, key=lambda r: r["start_time_utc"]):
            s, e = max(_t(r["start_time_utc"]), ws), min(_t(r["end_time_utc"]), we)
            if s >= e:
                continue
            evs.append(
                {
                    "start": _z(s),
                    "end": _z(e),
                    "mean_depth_mw": r["average_access_reduction_kw"] / 1000,
                    "peak_depth_mw": r["peak_access_reduction_kw"] / 1000,
                    "notice_s": {
                        "kind": "unknown",
                        "reason": "UKPN does not publish notice in this dataset",
                    },
                    "source_ref": f"{r['event_id']}/{r['der_name']}",
                }
            )
        n_hh = int((we - ws).total_seconds() // 1800)
        scenarios.append(
            _scenario(
                f"y{y}",
                (
                    f"Replay of {y}-{m0:02d}-{d0:02d} to {y}-{m1:02d}-{d1:02d} (UTC), {len(evs)} "
                    "published event(s)"
                ),
                w,
                ws,
                n_hh,
                evs,
            )
        )
    lic = _licence(meta_raw)
    return {
        "contract": "IC-2",
        "contract_version": "1.0.0",
        "set_id": "ic2-real-ukpn-der4-norwich-0611-0615",
        "site_id": f"UKPN:{UKPN_DATASET}:{UKPN_DER}",
        "direction": "export",
        "signal_basis": "generation_export_curtailment",
        "depth_definition": (
            "UKPN 'average_access_reduction_kw' per curtailment event for the DER (UKPN's own "
            "definition), in MW, held uniform over the published start-end"
        ),
        "reference_capacity_mw": {
            "kind": "unknown",
            "reason": "this UKPN dataset does not publish the DER's export capacity",
        },
        "connection_agreement": {
            "kind": "unknown",
            "reason": "the DER's connection agreement is not published",
        },
        "evidence_class": "replayed_real_data",
        "provenance": {
            "kind": "replayed_real_data",
            "sources": [
                _source(
                    "UK Power Networks",
                    UKPN_DATASET,
                    rec["ukpn_events"],
                    lic,
                    "UK Power Networks Open Data Portal (CC BY 4.0)",
                ),
                _source(
                    "UK Power Networks",
                    UKPN_DATASET,
                    rec["ukpn_years"],
                    lic,
                    "UK Power Networks Open Data Portal (CC BY 4.0)",
                ),
            ],
            "derivation": (
                "Each published event overlapping the window, clipped to it; depth = "
                "average_access_reduction_kw / 1000, uniform over [start, end); peak = "
                "peak_access_reduction_kw / 1000; interval value = depth x covered fraction of the "
                "interval, summed over events. Both UKPN drivers (Constraint and Non-constraint) "
                "are included. UKPN's total_der_access_reduction_kwh equals the average times "
                "UKPN's duration_hours, which differs from end - start "
                "by seconds per event; the series follows the published timestamps."
            ),
        },
        "weight_basis": "equal_over_observed_periods",
        "weight_rule": (
            f"One scenario per calendar year in which the dataset publishes any {UKPN_DER} event "
            f"({years[0]}-{years[-1]}, from the recorded per-year count); each replays 11-15 June "
            "(UTC) of that year; a year with no event in the window is a zero-restriction "
            "scenario; "
            f"weights equal (1/{len(years)})."
        ),
        "n_half_hours": scenarios[0]["half_hourly"]["n_intervals"],
        "scenarios": scenarios,
    }


def build_npg(events_raw: bytes, meta_raw: bytes, rec: dict[str, Any]) -> dict[str, Any]:
    rows = sorted(json.loads(events_raw), key=lambda r: (r["start_time_utc"], r["event_id"]))
    w = Fraction(1, len(rows))
    scenarios = []
    for r in rows:
        s, e = _t(r["start_time_utc"]), _t(r["end_time_utc"])
        ws = datetime(s.year, s.month, s.day, tzinfo=UTC)
        we = ws + NPG_HORIZON
        if e > we:
            raise ValueError(f"event {r['event_id']} is longer than the horizon")
        evs = [
            {
                "start": _z(s),
                "end": _z(e),
                "mean_depth_mw": float(r["average_access_reduction_mw"]),
                "peak_depth_mw": {
                    "kind": "unknown",
                    "reason": "NPg publishes the average access reduction only",
                },
                "notice_s": {
                    "kind": "unknown",
                    "reason": "NPg does not publish notice in this dataset",
                },
                "source_ref": f"{r['event_id']}/{r['customer_unique_identifier']}",
            }
        ]
        n_hh = int(NPG_HORIZON.total_seconds() // 1800)
        label = f"{r['reason_for_curtailment']} event starting {_z(s)} (48 h from 00:00Z that day)"
        scenarios.append(_scenario(f"e{s:%Y%m%d}-{r['event_id']}", label, w, ws, n_hh, evs))
    lic = _licence(meta_raw)
    return {
        "contract": "IC-2",
        "contract_version": "1.0.0",
        "set_id": "ic2-real-npg-der0000099-norton-events",
        "site_id": f"NPG:{NPG_DATASET}:{NPG_CUSTOMER}",
        "direction": "export",
        "signal_basis": "generation_export_curtailment",
        "depth_definition": (
            "NPg 'average_access_reduction_mw' per outage-related curtailment event for the "
            "customer (NPg's own definition), held uniform over the published start-end"
        ),
        "reference_capacity_mw": {
            "kind": "unknown",
            "reason": "this NPg dataset does not publish the customer's export capacity",
        },
        "connection_agreement": {
            "kind": "unknown",
            "reason": "the customer's connection agreement is not published",
        },
        "evidence_class": "replayed_real_data",
        "provenance": {
            "kind": "replayed_real_data",
            "sources": [
                _source(
                    "Northern Powergrid",
                    NPG_DATASET,
                    rec["npg_events"],
                    lic,
                    "Northern Powergrid Open Data",
                ),
            ],
            "derivation": (
                "Each published event for the customer; depth = average_access_reduction_mw, "
                "uniform over [start, end); interval value = depth x covered fraction of the "
                "interval. The published energy figure is not used (it is consistent with depth "
                "x duration to the publisher's rounding)."
            ),
        },
        "weight_basis": "equal_over_observed_periods",
        "weight_rule": (
            f"One scenario per published event for {NPG_CUSTOMER} ({len(rows)} events, all the "
            "dataset holds for it); each covers the 48 h from 00:00 UTC on the day the event "
            f"starts; weights equal (1/{len(rows)}). The set is the restriction profile "
            "CONDITIONAL on an outage-related event occurring; it carries no annual frequency."
        ),
        "n_half_hours": scenarios[0]["half_hourly"]["n_intervals"],
        "scenarios": scenarios,
    }


def build_all() -> dict[str, dict[str, Any]]:
    rec = json.loads(RETRIEVAL.read_text(encoding="utf-8"))
    raw = {role: (REAL / f).read_bytes() for role, f in FILES.items()}
    for role, r in rec.items():
        assert "sha256:" + hashlib.sha256(raw[role]).hexdigest() == r["sha256"], role
    return {
        "ukpn": build_ukpn(raw["ukpn_events"], raw["ukpn_years"], raw["ukpn_meta"], rec),
        "npg": build_npg(raw["npg_events"], raw["npg_meta"], rec),
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def build() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from gridq.contracts.curtailment import CurtailmentScenarioSet

    rec = json.loads(RETRIEVAL.read_text(encoding="utf-8"))
    manifest: dict[str, Any] = {
        "_note": (
            "Committed. Hashes and provenance of the git-ignored real-data IC-2 fixtures in this "
            "folder; no data rows and no figure derived from them. See README.md."
        ),
        "gate": GATE,
        "responses": {
            role: {
                k: r[k] for k in ("file", "url", "retrieved_at", "sha256", "bytes", "http_status")
            }
            for role, r in rec.items()
        },
        "sets": {},
    }
    for name, data in build_all().items():
        text = render(data)
        (REAL / SETS[name]).write_text(text, encoding="utf-8")
        obj = CurtailmentScenarioSet.model_validate_json(text)
        manifest["sets"][name] = {
            "file": SETS[name],
            "file_sha256": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
            "content_hash": obj.content_hash(),
            "scenarios": len(obj.scenarios),
            "weights": [f"{s.weight.numerator}/{s.weight.denominator}" for s in obj.scenarios],
            "licence": obj.provenance.sources[0].licence,  # type: ignore[union-attr]
            "attribution": obj.provenance.sources[0].attribution,  # type: ignore[union-attr]
        }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    {"fetch": fetch, "build": build}[sys.argv[1]]()
