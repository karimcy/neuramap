"""Tests for `gridq.quotes.assembly` — stamping a run into a traceable QuoteRecord.

Covers: the flat provenance fields are populated off a `RunContext`; the quote hash + id are
deterministic and timestamp-invariant; a baseline run is carried as `not_bankable` (default
DRAFT); the degraded `financial_case=None` path still produces a valid quote; and warnings are
aggregated from every layer (incl. the outstanding data asks).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.finance.financial_model import run_financial_model
from gridq.ingestion import populate_missing_data
from gridq.quotes import assemble_quote_record

ASSUMPTION_VERSION = "gb_v0_1"


@pytest.fixture
def assumptions():
    return load_assumptions(ASSUMPTION_VERSION)


def _result(site: s.SiteModel, assumptions) -> s.OptimisationResult:
    engine = BaselineEngine()
    return engine.solve(engine.build_optimisation_request(site, assumptions))


def test_stamp_fields_populated_from_run_context(
    make_site: Callable[..., s.SiteModel], assumptions
):
    site = make_site()
    result = _result(site, assumptions)
    case = run_financial_model(site, result, assumptions).case

    quote = assemble_quote_record(site, result, case, assumptions)

    assert quote.input_hash is not None and quote.input_hash.startswith("sha256:")
    assert quote.assumption_version == ASSUMPTION_VERSION
    assert quote.code_version  # git:... or pkg:...
    assert quote.quote_id.startswith("quote_")
    assert quote.site_id == site.site_id
    assert quote.data_tier == site.data_tier
    # Re-validates cleanly (schema-valid artifact).
    s.QuoteRecord.model_validate(quote.model_dump(mode="json"))


def test_hash_and_id_deterministic_and_created_at_invariant(
    make_site: Callable[..., s.SiteModel], assumptions
):
    site = make_site()
    # Two independently-built results differ only in `created_at` (excluded from the hash).
    r1, r2 = _result(site, assumptions), _result(site, assumptions)
    assert r1.created_at != r2.created_at or r1.model_dump(exclude={"created_at"}) == r2.model_dump(
        exclude={"created_at"}
    )
    q1 = assemble_quote_record(site, r1, None, assumptions)
    q2 = assemble_quote_record(site, r2, None, assumptions)
    assert q1.input_hash == q2.input_hash
    assert q1.quote_id == q2.quote_id


def test_baseline_quote_is_not_bankable_draft(make_site: Callable[..., s.SiteModel], assumptions):
    site = make_site()
    result = _result(site, assumptions)
    quote = assemble_quote_record(site, result, None, assumptions)

    assert result.bankable_status == s.BankableStatus.NOT_BANKABLE
    assert quote.quote_mode == s.QuoteMode.DRAFT
    assert quote.status_history == []
    assert any("not bankable" in w.lower() for w in quote.warnings)


def test_degraded_financial_case_none_still_valid(
    make_site: Callable[..., s.SiteModel], assumptions
):
    # A T0 site with no max_import → baseline reports insufficient_data; we assemble with no case.
    site = make_site(with_max_import=False)
    result = _result(site, assumptions)
    quote = assemble_quote_record(site, result, None, assumptions)

    assert quote.financial_case is None
    assert quote.data_tier == s.DataTier.T0_ANNUAL_KWH_TARIFF_POSTCODE
    assert quote.quote_mode == s.QuoteMode.DRAFT  # never underwritten off the baseline


def test_warnings_aggregate_data_asks(make_site: Callable[..., s.SiteModel], assumptions):
    site = populate_missing_data(make_site())  # now carries ranked asks
    assert site.missing_data  # sanity
    result = _result(site, assumptions)
    quote = assemble_quote_record(site, result, None, assumptions)
    assert any("ranked data ask" in w.lower() for w in quote.warnings)


def test_forecast_content_hash_changes_the_pin(make_site: Callable[..., s.SiteModel], assumptions):
    """Pinning a forecast changes the quote input hash (provenance includes the curve)."""
    site = make_site()
    result = _result(site, assumptions)

    class _Ref:  # minimal duck of ForecastReference: only content_hash is read
        content_hash = "sha256:" + "0" * 64

    without = assemble_quote_record(site, result, None, assumptions)
    with_ref = assemble_quote_record(site, result, None, assumptions, forecast_ref=_Ref())
    assert without.input_hash != with_ref.input_hash


def test_stream_consents_none_hash_stable_and_set_changes_it(
    make_site: Callable[..., s.SiteModel], assumptions
):
    """Revenue-Data Drive (D43), mirrors the export_arrangement D42 guarantee: a None (or absent)
    `stream_consents` yields the same quote input_hash — the field is popped from the canonical
    run input, so pre-drive sites are byte-identical — while a set list enters the identity."""
    site = make_site()
    result = _result(site, assumptions)

    q_default = assemble_quote_record(site, result, None, assumptions)
    q_explicit_none = assemble_quote_record(
        site.model_copy(update={"stream_consents": None}), result, None, assumptions
    )
    assert q_default.input_hash == q_explicit_none.input_hash  # None == absent (popped)

    site_set = s.SiteModel.model_validate(
        {
            **site.model_dump(mode="json"),
            "stream_consents": [
                {
                    "stream": s.MarketStream.CAPACITY_MARKET,
                    "consented": True,
                    "agreed_rate_gbp": 30.0,
                    "agreed_rate_unit": "gbp_per_kw_year",
                }
            ],
        }
    )
    q_set = assemble_quote_record(site_set, result, None, assumptions)
    assert q_set.input_hash != q_default.input_hash  # a real input enters the identity
