"""Counterparty intake/validation + the ranked credit data-ask (M0 -> M9 seam).

The deterministic credit->rate/tenor mapping needs a credit band; ingestion's job is to *detect*
when it is missing and turn it into a precise, ranked ask — never to invent a band.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.ingestion import build_ranked_data_asks, validate_site_model


def _with_counterparty(make_site, **cp_kwargs) -> s.SiteModel:
    cp = s.Counterparty(legal_name="Acme Ltd", **cp_kwargs)
    return make_site().model_copy(update={"counterparty": cp})


# --- validation warnings ---------------------------------------------------------------------


def test_validate_flags_unassessed_credit(make_site) -> None:
    site = _with_counterparty(
        make_site, company_number="01234567", credit_status=s.CreditStatus.UNKNOWN
    )
    report = validate_site_model(site)
    assert report.ok  # a warning, not an error
    assert any(i.code == "site.credit_status_unassessed" for i in report.warnings)


def test_validate_clean_for_assessed_counterparty(make_site) -> None:
    site = _with_counterparty(
        make_site, company_number="01234567", credit_status=s.CreditStatus.ADEQUATE
    )
    report = validate_site_model(site)
    codes = {i.code for i in report.warnings}
    assert "site.credit_status_unassessed" not in codes
    assert "site.company_number_missing" not in codes
    assert "site.company_number_malformed" not in codes


def test_validate_flags_malformed_company_number(make_site) -> None:
    site = _with_counterparty(make_site, company_number="123", credit_status=s.CreditStatus.STRONG)
    report = validate_site_model(site)
    assert any(i.code == "site.company_number_malformed" for i in report.warnings)


# --- ranked data ask -------------------------------------------------------------------------


def test_ranked_ask_when_identity_present_but_credit_unassessed(make_site) -> None:
    site = _with_counterparty(
        make_site, company_number="01234567", credit_status=s.CreditStatus.UNKNOWN
    )
    asks = build_ranked_data_asks(site)
    ids = {a.ask_id for a in asks}
    assert "counterparty_credit_status" in ids
    assert "counterparty_identity" not in ids  # identity is known, so no identity ask


def test_identity_ask_when_company_number_missing_no_double_ask(make_site) -> None:
    site = _with_counterparty(make_site, company_number=None, credit_status=s.CreditStatus.UNKNOWN)
    asks = build_ranked_data_asks(site)
    ids = {a.ask_id for a in asks}
    assert "counterparty_identity" in ids
    # the credit-band ask is suppressed when there is no identity yet — one ask, not two
    assert "counterparty_credit_status" not in ids


def test_no_counterparty_ask_when_assessed(make_site) -> None:
    site = _with_counterparty(
        make_site, company_number="01234567", credit_status=s.CreditStatus.ADEQUATE
    )
    ids = {a.ask_id for a in build_ranked_data_asks(site)}
    assert "counterparty_identity" not in ids
    assert "counterparty_credit_status" not in ids
