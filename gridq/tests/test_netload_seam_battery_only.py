"""Battery-only v1 with the net-load interface held open for solar v2 (L10 · M1 schemas).

System Brief: "Battery-only in v1; solar deferred to v2 with the net-load interface held open."
These tests pin both halves: solar is **not** a sizing dimension (the sizing candidate is
power × duration only, and no schema carries a solar/PV sizing input), and the **net-load seam**
is present — HH ingestion accepts a net-load series (the `net_load_kw` alias) and the dispatch
profile the engine consumes is keyed on `site_load_kw` (net site load), so v2 solar feeds in as
net load without changing the core.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import gridq.schemas as s
from gridq.ingestion import load_half_hourly_csv
from gridq.sizing.search_space import SizingCandidate

START = datetime(2025, 1, 1, 0, 0)


# --- solar is not a sizing dimension ---------------------------------------


def test_sizing_candidate_is_battery_only() -> None:
    # The only sizing dimensions are battery power and duration (energy is derived) — no solar.
    assert set(SizingCandidate.__dataclass_fields__) == {"power_kw", "duration_hours"}


def test_no_schema_carries_a_solar_sizing_input() -> None:
    # No SiteModel / OptimisationRequest field sizes or inputs solar in v1 (it is display-only).
    for model in (s.SiteModel, s.LoadDataSummary, s.OptimisationRequest):
        names = " ".join(model.model_fields).lower()
        assert "solar" not in names
        assert "pv" not in names.split()


# --- the net-load interface is held open -----------------------------------


def test_ingestion_accepts_a_net_load_series(tmp_path: Path) -> None:
    # A site can supply net load via the `net_load_kw` alias; it canonicalises to `site_load_kw`,
    # so a v2 solar adjustment (gross load − generation) flows through the same input.
    path = tmp_path / "hh.csv"
    rows = ["timestamp,import_kw,net_load_kw"]
    for i in range(6):
        ts = (START + timedelta(minutes=30 * i)).isoformat()
        rows.append(f"{ts},{100 + i},{90 + i}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    df = load_half_hourly_csv(path)
    assert "site_load_kw" in df.columns  # net_load_kw alias resolved to the canonical column
    assert df["site_load_kw"].tolist() == [90.0, 91.0, 92.0, 93.0, 94.0, 95.0]


def test_dispatch_profile_is_keyed_on_net_site_load() -> None:
    # The dispatch series the engine emits carries `site_load_kw` — the net-load the dispatch sees.
    assert "site_load_kw" in s.DispatchProfilePoint.model_fields
