"""Tests for `RevenueStreamConsent` and `SiteModel.stream_consents` (Revenue-Data Drive / D43).

Slice 1 is a data-only carrier: this covers the model round-trip, the canonicalised ordering
and duplicate-stream rejection on the site, unknown-field and negative-rate rejection, and the
`consented_streams` / `agreed_rate_for` convenience API. Hash-stability (None == absent; a set
list enters the identity) is proven end-to-end in `test_quotes_assembly.py`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s


def _site_with_consents(
    make_site: Callable[..., s.SiteModel], consents: list[dict[str, object]]
) -> s.SiteModel:
    """Rebuild a validated SiteModel carrying `consents` (re-runs the model validators)."""
    base = make_site().model_dump(mode="json")
    base["stream_consents"] = consents
    return s.SiteModel.model_validate(base)


def test_revenue_stream_consent_round_trips() -> None:
    c = s.RevenueStreamConsent(
        stream=s.MarketStream.CAPACITY_MARKET,
        consented=True,
        agreed_rate_gbp=30.0,
        agreed_rate_unit=s.RateUnit.GBP_PER_KW_YEAR,
        notes="CM aggregator fee",
    )
    reloaded = s.RevenueStreamConsent.model_validate(c.model_dump(mode="json"))
    assert reloaded == c


def test_defaults_are_conservative() -> None:
    c = s.RevenueStreamConsent(stream=s.MarketStream.WHOLESALE_P415)
    assert c.consented is False
    assert c.agreed_rate_gbp is None
    assert c.notes is None


def test_unknown_field_rejected() -> None:
    # GridQModel forbids extra fields — a typo is a validation error, not a silent no-op.
    with pytest.raises(ValidationError):
        s.RevenueStreamConsent(stream=s.MarketStream.WHOLESALE_P415, consentd=True)  # type: ignore[call-arg]


def test_negative_agreed_rate_rejected() -> None:
    with pytest.raises(ValidationError):
        s.RevenueStreamConsent(stream=s.MarketStream.CAPACITY_MARKET, agreed_rate_gbp=-1.0)


def test_stream_consents_are_canonically_ordered(
    make_site: Callable[..., s.SiteModel],
) -> None:
    # Provided out of order → stored sorted by stream value, so any input order hashes identically.
    site = _site_with_consents(
        make_site,
        [
            {"stream": s.MarketStream.WHOLESALE_P415, "consented": True},
            {"stream": s.MarketStream.CAPACITY_MARKET, "consented": True},
        ],
    )
    assert site.stream_consents is not None
    ordered = [c.stream for c in site.stream_consents]
    assert ordered == sorted(ordered, key=lambda st: st.value)
    assert ordered == [s.MarketStream.CAPACITY_MARKET, s.MarketStream.WHOLESALE_P415]


def test_duplicate_stream_rejected_not_deduped(
    make_site: Callable[..., s.SiteModel],
) -> None:
    with pytest.raises(ValidationError, match="at most one entry per stream"):
        _site_with_consents(
            make_site,
            [
                {"stream": s.MarketStream.WHOLESALE_P415, "consented": True},
                {"stream": s.MarketStream.WHOLESALE_P415, "consented": False},
            ],
        )


def test_consented_streams_and_agreed_rate_for(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _site_with_consents(
        make_site,
        [
            {
                "stream": s.MarketStream.CAPACITY_MARKET,
                "consented": True,
                "agreed_rate_gbp": 30.0,
                "agreed_rate_unit": "gbp_per_kw_year",
            },
            {
                "stream": s.MarketStream.WHOLESALE_P415,
                "consented": False,
                "agreed_rate_gbp": 0.05,
                "agreed_rate_unit": "gbp_per_kwh",
            },
        ],
    )
    # Only consented=True streams are in the set.
    assert site.consented_streams == frozenset({s.MarketStream.CAPACITY_MARKET})
    # agreed_rate_for returns the recorded rate regardless of the consent flag, None when absent.
    assert site.agreed_rate_for(s.MarketStream.CAPACITY_MARKET) == 30.0
    assert site.agreed_rate_for(s.MarketStream.WHOLESALE_P415) == 0.05
    assert site.agreed_rate_for(s.MarketStream.TARIFF_ARBITRAGE) is None


def test_none_default_gives_empty_convenience_views(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site()
    assert site.stream_consents is None
    assert site.consented_streams == frozenset()
    assert site.agreed_rate_for(s.MarketStream.CAPACITY_MARKET) is None
