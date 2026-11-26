"""Shared fixtures for the optimisation (MILP) tests: a real half-hourly load CSV + a site.

Not a test module (underscore prefix) — imported by the ``test_optimisation_*`` files. Builds a
deterministic, *representative* metered half-hourly series (a weekday daytime peak with a cheap
overnight trough, the shape that makes time-of-use arbitrage + DUoS red-band avoidance real) and a
matching multi-rate `SiteModel`. Generating data for a test fixture is fine — the "never invent
data" rule is about production runs, not test inputs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.schemas import LoadDataSummary, PhysicalGateStatus, SiteModel
from gridq.schemas.commitment import AvailabilityCommitment, AvailabilityWindow
from gridq.schemas.connection import ConnectionDetails
from gridq.schemas.consents import ConsentState
from gridq.schemas.counterparty import Counterparty
from gridq.schemas.enums import DataTier, MarketStream
from gridq.schemas.tariff import TariffStack

# Monday, so the first weekday red band (16:00–19:00) lands on day 0.
_START = "2025-01-06 00:00"


def write_hh_csv(path: str | Path, *, days: int = 7) -> tuple[int, pd.DatetimeIndex]:
    """Write a deterministic HH import-kW CSV; return (row count, timestamps)."""
    n = days * 48
    ts = pd.date_range(_START, periods=n, freq="30min")
    hour = ts.hour.to_numpy()
    weekday = ts.dayofweek.to_numpy() < 5
    load = np.full(n, 60.0)  # overnight / weekend trough
    load[(hour >= 8) & (hour < 20)] = 220.0  # daytime base
    load[weekday & (hour >= 16) & (hour < 19)] = 320.0  # weekday red-band peak
    pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "import_kw": load}).to_csv(
        path, index=False
    )
    return n, ts


def multi_rate_site(
    hh_path: str | Path,
    *,
    rows: int,
    mic_kw: float = 500.0,
    max_import_kw: float = 320.0,
    site_id: str = "site_milp",
) -> SiteModel:
    """A T2 site: day/night import tariff + DUoS red rate + levies, pointing at ``hh_path``."""
    return SiteModel(
        site_id=site_id,
        postcode="LE17 4XN",
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=LoadDataSummary(
            annual_kwh=900_000.0,
            half_hourly_file_ref=str(hh_path),
            half_hourly_rows=rows,
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 13),
            max_import_kw=max_import_kw,
        ),
        annual_consumption_kwh=900_000.0,
        tariff_stack=TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="MilpTestCo",
            import_rates={"day": 0.30, "night": 0.10},
            duos_red_rate=0.10,
            levies={"ro": 0.02, "fit": 0.01},
        ),
        connection=ConnectionDetails(mic_kw=mic_kw, mec_kw=100.0, asc_kw=mic_kw),
        consents=ConsentState(),
        physical_gates=PhysicalGateStatus(),
        counterparty=Counterparty(legal_name="MILP Test Ltd", company_number="01234567"),
    )


#: The published service window of SSEN's Wallingford VAOU tender (17:00-19:00 in December,
#: January and February), the shape of the local-flex awards in the SSEN SEPD licence area. A
#: public tender fact, not a customer record.
_SSEN_WINTER_EVENING = AvailabilityWindow(
    label="SYNTHETIC winter evening window", months=[12, 1, 2], start_time="17:00", end_time="19:00"
)


def synthetic_local_flex_award(
    *, committed_power_kw: float = 200.0, tenor_years: float = 1.0
) -> AvailabilityCommitment:
    """A SYNTHETIC attested local-flex award: no real site, counterparty or contract is behind it.

    The structure of an attested award on a campaign record (one SSEN SEPD winter-evening window, a
    one-year tenor, a counterparty, a reference, an attesting party and a basis), with figures
    chosen for the tests: 200 kW declared exceeds the end-of-tenor capability of every candidate
    below about 206 kW, so the dispatch clamp binds on the usual 100 kW candidate. Replaces the
    copies of a customer record's award these tests used to read from ``examples/real`` (WP-GRC-05:
    that file is quarantined until its DPA is registered).
    """
    return AvailabilityCommitment(
        commitment_id="SYNTHETIC-local-flex-award-1",
        stream=MarketStream.LOCAL_FLEX_PICLO,
        committed_power_kw=committed_power_kw,
        windows=[_SSEN_WINTER_EVENING],
        tenor_years=tenor_years,
        counterparty="SYNTHETIC DNO (not a real counterparty)",
        reference="SYNTHETIC-AWARD-0001",
        confirmed_by="SYNTHETIC test fixture (no person attests this award)",
        basis="SYNTHETIC test input; no real award exists",
    )


def ssen_awarded_site(hh_path: str | Path, *, rows: int) -> SiteModel:
    """`multi_rate_site` in the SSEN SEPD licence area, carrying `synthetic_local_flex_award`."""
    base = multi_rate_site(hh_path, rows=rows)
    return base.model_copy(
        update={
            "connection": base.connection.model_copy(update={"dno_region": "SSEN_SEPD"}),
            "local_flex_awards": [synthetic_local_flex_award()],
        }
    )
