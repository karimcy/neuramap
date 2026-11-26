"""WP-6 — unit and percent/fraction coherence at the schema door.

Two failure classes the hostile review found, both of which validated cleanly and then produced a
confident, hash-pinned, investor-facing number that was wrong by orders of magnitude:

- **F-17** — `RevenueStreamConsent.agreed_rate_gbp` was a bare unit-less float that downstream code
  read as £/kWh, £/MWh, £/kW/yr and £/MW/yr depending on the stream (x1000 error class).
- **F-52** — `DebtAssumptions.gearing_pct` accepts 0.7; if that was meant as 70% it is read as
  0.7%, sizing debt 100x too small and taking DSCR, the covenant verdict and equity IRR with it.

Both are now rejected at the door. The point is that these are *unrepresentable*, not merely
discouraged — a validator that warns is a validator that gets ignored.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gridq.schemas.enums import MarketStream
from gridq.schemas.finance import DebtAssumptions
from gridq.schemas.stream_consent import RateUnit, RevenueStreamConsent

# --- F-17: an unlabelled rate is not a rate --------------------------------------------------


def test_agreed_rate_without_a_unit_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        RevenueStreamConsent(
            stream=MarketStream.CAPACITY_MARKET, consented=True, agreed_rate_gbp=45.0
        )
    assert "agreed_rate_unit" in str(exc.value)


def test_agreed_rate_with_a_unit_is_accepted() -> None:
    consent = RevenueStreamConsent(
        stream=MarketStream.CAPACITY_MARKET,
        consented=True,
        agreed_rate_gbp=45.0,
        agreed_rate_unit=RateUnit.GBP_PER_KW_YEAR,
    )
    assert consent.agreed_rate_unit is RateUnit.GBP_PER_KW_YEAR


def test_consent_without_any_rate_still_validates() -> None:
    """Most streams need no agreed rate; the unit is required only when a rate is present."""
    consent = RevenueStreamConsent(stream=MarketStream.TARIFF_ARBITRAGE, consented=True)
    assert consent.agreed_rate_gbp is None
    assert consent.agreed_rate_unit is None


def test_the_units_that_collided_are_all_distinguishable() -> None:
    """£/kWh vs £/MW/yr is six orders of magnitude — the reason this field exists."""
    rates = {
        RateUnit.GBP_PER_KWH: 0.08,
        RateUnit.GBP_PER_MWH: 80.0,
        RateUnit.GBP_PER_KW_YEAR: 45.0,
        RateUnit.GBP_PER_MW_YEAR: 45_000.0,
        RateUnit.GBP_PER_YEAR: 12_000.0,
    }
    for unit, value in rates.items():
        consent = RevenueStreamConsent(
            stream=MarketStream.BALANCING_MECHANISM,
            consented=True,
            agreed_rate_gbp=value,
            agreed_rate_unit=unit,
        )
        assert consent.agreed_rate_unit is unit


# --- F-52: a fraction in a percent field -----------------------------------------------------


@pytest.mark.parametrize("field", ["gearing_pct", "rate_pct"])
def test_a_fraction_in_a_percent_field_is_rejected(field: str) -> None:
    """0.6 meaning 60% must not silently become 0.6%."""
    with pytest.raises(ValidationError) as exc:
        DebtAssumptions(**{field: 0.6})
    message = str(exc.value)
    assert "FRACTION" in message
    assert "60%" in message, "the error must show the user what they probably meant"


@pytest.mark.parametrize("value", [0.0, 1.0, 7.5, 60.0, 100.0])
def test_genuine_percentages_are_accepted(value: float) -> None:
    assert DebtAssumptions(gearing_pct=value).gearing_pct == value


def test_zero_stays_legal() -> None:
    """All-equity (0% gearing) and a zero rate are real states, not typos."""
    terms = DebtAssumptions(gearing_pct=0.0, rate_pct=0.0)
    assert terms.gearing_pct == 0.0
    assert terms.rate_pct == 0.0


def test_out_of_range_gearing_still_rejected() -> None:
    """The pre-existing [0, 100] bound must survive the new validator."""
    with pytest.raises(ValidationError):
        DebtAssumptions(gearing_pct=140.0)


# --- F-15: the CLI must not default to a superseded assumption release -----------------------


def test_latest_version_resolves_the_newest_release() -> None:
    """F-15. The default must be resolved from disk, never hard-coded, or it goes stale silently."""
    from gridq.assumptions import available_versions, latest_version

    versions = available_versions()
    assert versions, "no assumption releases found"
    assert latest_version() == versions[-1]
    assert latest_version() != "gb_v0_1", (
        "the newest release must not be gb_v0_1 — that was the stale default F-15 names"
    )


def test_versions_sort_numerically_not_lexically() -> None:
    """gb_v1_10 must sort after gb_v1_9; a string sort would put it before."""
    import tempfile
    from pathlib import Path

    from gridq.assumptions.loader import available_versions

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in ("gb_v1_9", "gb_v1_10", "gb_v1_2"):
            (d / f"{name}.yaml").write_text("")
        assert available_versions(base_dir=d) == ["gb_v1_2", "gb_v1_9", "gb_v1_10"]


def test_cli_run_has_no_hardcoded_assumption_default() -> None:
    import inspect

    from gridq.cli import run

    default = inspect.signature(run).parameters["assumptions_version"].default
    assert default.default is None, (
        "--assumptions must default to None (resolved to the newest release at run time), "
        "never to a pinned version string"
    )
