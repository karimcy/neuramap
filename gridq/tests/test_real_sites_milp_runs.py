"""Every installed real site actually SOLVES, on its own half-hourly bytes, under the live release.

**The gap this closes.** Gate 1 ("MILP or nothing", `docs/validation_requirements.md` §1) is the one
surviving hard gate, and until now every test that exercised it did so on a synthetic fixture. The
real sites were covered only as *data*: `test_grundon_metered_basis_wired.py` proves each site JSON
points at its own metered CSV and that the declared figures recompute from those bytes — a wiring
proof, deliberately stopping short of the engine. `test_gate3_declared_ref_resolution.py` proves the
declared refs resolve. Neither runs a solver. So a change that made the MILP infeasible, unbounded
or silently mis-priced on a *real* site — a 1.6 GWh private-wire load, a 17,518-row part-year meter,
a single-rate tariff with no spread to arbitrage — would have been caught by nothing here.

**What each assertion below guards, and why it can fail.**

1. *Discovery floor.* The site set is DISCOVERED by glob, and the count is checked against a static
   minimum. A pure ``checked == len(discovered)`` floor passes vacuously at ``0 == 0`` the moment
   the directory moves or empties, which is the exact hollow-test failure this repo has shipped
   before; the static minimum is what makes zero coverage impossible.
2. *Reconciliation, recomputed from the CSV bytes.* Row count, annual energy and peak are read off
   the file rather than trusted from the JSON or the manifest. Covers all discovered sites,
   including ``harwell.json`` and ``colnbrook.json``, which the Grundon file's hard-coded
   seven-stem map does not name.
3. *The solve itself.* ``solver_status == optimal`` on every real site: the site's own year, the
   live assumption release, a real HiGHS solve. Fails on an infeasible model, a solver regression,
   or a release whose constraints cannot be met at a real site's scale.
4. *Round-trip physics.* ``annual_charge_kwh >= annual_discharge_kwh`` whenever the battery cycled:
   the losses are bought, never conjured. A sign flip, a unit error or a lost efficiency term
   breaks this.
5. *The arbitrage ceiling, per site, from the site's own tariff.* Energy-band value is bounded by
   ``discharged kWh × (dearest band − cheapest band)`` — rigorous, because no discharged kWh can
   earn more than the full spread and the charge energy is paid for at no less than the cheapest
   band. On the two single-rate sites the spread is **zero**, so this pins arbitrage at exactly
   £0 — the standing regression guard for F-01 (a flat tariff whose price series read as ``None``,
   so the MILP charged for free and cycled daily to harvest DUoS it had not paid for).

Cost: one dispatch solve per site, ~2-3 s each on a real 17,518-row year — the whole module is
well under a minute, so it belongs in the default suite and carries no marker and no skip. It runs
against ``latest_version()`` deliberately: the release production resolves is the release that must
keep every real site solvable.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from gridq.assumptions import latest_version, load_assumptions
from gridq.dispatch.contract import DispatchProblem, DispatchResult
from gridq.ingestion import load_site_model_json
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.schemas import SiteModel, SolverStatus

REPO = Path(__file__).resolve().parents[1]
SITES = REPO / "examples" / "real" / "sites"

#: Every real site model, discovered — never a hard-coded list, because a hard-coded list is what
#: went stale when the metered rebuild split ``bishops_cleeve.json`` into two supplies and deleted
#: the original (`test_gate3_declared_ref_resolution.py` records that failure).
SITE_FILES: list[Path] = sorted(SITES.glob("*.json"))

#: The static floor on the discovered set. ``examples/real/`` has been tracked since 2026-08-11
#: (see `.gitignore:35`), so these files are present on every checkout and their absence is a
#: broken repository, not a host condition to skip on. Nine is what the metered rebuild landed:
#: five Grundon metered supplies, two invoice-anchored companions, Colnbrook, Harwell. Raise it
#: when sites are added; lowering it is a deliberate act that should be argued for in the diff.
MINIMUM_REAL_SITES = 9


def _series(path: Path) -> list[tuple[str, float]]:
    """The declared half-hourly CSV as (timestamp, kWh), read from the bytes on disk."""
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    key = "import_kwh" if rows and "import_kwh" in rows[0] else "import_kw"
    scale = 1.0 if key == "import_kwh" else 0.5  # a kW reading over a half hour is kW/2 kWh
    return [(r["timestamp"], float(r[key]) * scale) for r in rows]


def _candidate(site: SiteModel) -> tuple[float, float, float]:
    """A deterministic, site-scaled 2-hour candidate: power = 25% of the site's own peak.

    Scaled from the site rather than fixed, so a 46 kW supply and a 486 kW supply are each given a
    battery that could plausibly run there; rounded so the input is bit-stable across runs.
    """
    peak = site.load_data.max_import_kw
    assert peak, f"{site.site_id}: no max_import_kw, so no candidate can be scaled from the site"
    power_kw = round(0.25 * float(peak), 3)
    return power_kw, round(power_kw * 2.0, 3), 2.0


def _solve(site: SiteModel) -> DispatchResult:
    power_kw, energy_kwh, duration_hours = _candidate(site)
    return MilpDispatchSolver().solve(
        DispatchProblem(
            site_model=site,
            assumptions=load_assumptions(latest_version()),
            power_kw=power_kw,
            energy_kwh=energy_kwh,
            duration_hours=duration_hours,
        )
    )


def test_the_real_site_set_is_discovered_and_is_not_empty() -> None:
    """The floor under every other test here: an empty glob must fail, never pass quietly."""
    assert len(SITE_FILES) >= MINIMUM_REAL_SITES, (
        f"only {len(SITE_FILES)} site models under {SITES} (expected at least "
        f"{MINIMUM_REAL_SITES}). examples/real is TRACKED — a missing site file is a deleted "
        "site, not an uninstalled host."
    )


@pytest.mark.parametrize("site_json", SITE_FILES, ids=lambda p: p.stem)
def test_the_declared_series_exists_and_reconciles_with_its_own_bytes(site_json: Path) -> None:
    """Row count, annual energy and peak recompute from the CSV — for EVERY discovered site.

    The Grundon wiring file asserts this for the seven stems it names; the discovery here is what
    covers ``harwell`` and ``colnbrook`` too, and what keeps covering a site added tomorrow.
    """
    declared = json.loads(site_json.read_text(encoding="utf-8"))["load_data"]
    ref = declared["half_hourly_file_ref"]
    csv_path = REPO / str(ref)
    assert csv_path.is_file(), f"{site_json.name}: declared series {ref!r} is not on disk"

    rows = _series(csv_path)
    assert len(rows) == declared["half_hourly_rows"], (
        f"{site_json.name}: declares {declared['half_hourly_rows']} half-hourly rows, "
        f"the file holds {len(rows)}"
    )
    total_kwh = sum(kwh for _, kwh in rows)
    assert total_kwh == pytest.approx(declared["annual_kwh"], rel=1e-4), (
        f"{site_json.name}: declares {declared['annual_kwh']} kWh, its series sums to {total_kwh}"
    )
    peak_kw = max(kwh for _, kwh in rows) * 2.0  # kWh in a half hour -> kW
    assert peak_kw == pytest.approx(declared["max_import_kw"], rel=1e-4), (
        f"{site_json.name}: declares a {declared['max_import_kw']} kW peak, its series peaks at "
        f"{peak_kw} kW — peak drives sizing, capex, MIC headroom and the BTM discharge cap"
    )


def test_every_real_site_solves_and_prices_within_its_own_tariff_spread() -> None:
    """The engine runs on the real bytes, and its energy-band £ respects the site's own spread.

    One loop rather than one test per site so the **coverage floor** is enforceable: the count of
    sites actually solved is asserted against the count discovered, and every site's failures are
    collected so a single bad site cannot mask the other eight.
    """
    failures: list[str] = []
    checked = 0

    for site_json in SITE_FILES:
        site = load_site_model_json(site_json)
        result = _solve(site)
        checked += 1
        name = site_json.stem

        if result.solver_status != SolverStatus.OPTIMAL:
            failures.append(
                f"{name}: solver_status={result.solver_status.value} (expected optimal). "
                f"Gate 1 requires a real half-hourly MILP for this site; warnings="
                f"{result.warnings}"
            )
            continue

        charge, discharge = result.annual_charge_kwh, result.annual_discharge_kwh
        if discharge is None or charge is None:
            failures.append(
                f"{name}: energy accounting missing (charge={charge}, discharge={discharge}); "
                "finance grosses charge up from discharge, so an absent pair is a null on the "
                "pricing path, not an unknown"
            )
        elif discharge > 0.0 and charge < discharge:
            failures.append(
                f"{name}: charged {charge:.1f} kWh to discharge {discharge:.1f} kWh — round-trip "
                "losses are bought, never conjured, so charge must never be the smaller number"
            )

        rates = site.tariff_stack.import_rates
        spread = (max(rates.values()) - min(rates.values())) if rates else 0.0
        by = {v.stream.value: v.annual_revenue_gbp for v in result.value_streams}
        arbitrage = by.get("TARIFF_ARBITRAGE")
        if arbitrage is None:
            failures.append(f"{name}: no TARIFF_ARBITRAGE stream in the priced result")
        elif not math.isfinite(arbitrage):
            failures.append(f"{name}: TARIFF_ARBITRAGE is {arbitrage!r}, not a finite £")
        elif discharge is not None:
            # No discharged kWh can earn more than the full band spread, and its charge energy
            # cost no less than the cheapest band — so this bound holds for any correct dispatch,
            # and collapses to "exactly £0" on a single-rate tariff.
            ceiling = discharge * spread
            if not (0.0 <= arbitrage <= ceiling + 1e-6):
                failures.append(
                    f"{name}: TARIFF_ARBITRAGE £{arbitrage:.2f} outside [0, £{ceiling:.2f}] "
                    f"— {discharge:.1f} kWh discharged against a £{spread:.5f}/kWh band spread "
                    f"({sorted(rates.items())})"
                )

        for stream in result.value_streams:
            if not math.isfinite(stream.annual_revenue_gbp):
                failures.append(
                    f"{name}: {stream.stream.value} priced at {stream.annual_revenue_gbp!r}"
                )

    assert checked == len(SITE_FILES), (
        f"only {checked} of {len(SITE_FILES)} real sites were solved — a site skipped inside this "
        "loop is a site with no engine coverage at all"
    )
    assert checked >= MINIMUM_REAL_SITES, (
        f"{checked} sites solved, below the floor of {MINIMUM_REAL_SITES}: this module would be "
        "reporting green over an empty or truncated site set"
    )
    assert not failures, "real-site solve failures:\n  - " + "\n  - ".join(failures)
