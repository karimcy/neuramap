"""`StudyBasis` + the derived, SOURCED export tariff — the D46 named-basis contract.

Two halves, both of which exist to make one thing impossible: a second pricing basis that nobody
declared, priced at a £/kWh nobody can point at a document for.

- **The basis cannot be enabled implicitly.** No default, no environment variable, no truthy flag:
  four attributed fields, one of them a verbatim acknowledgement sentence, plus the two derived
  records the priced numbers stand on.
- **The tariff cannot be invented.** `derive_flat_export_tariff` refuses — naming exactly what is
  missing — rather than returning a rate when the series cannot support a sourced one. D45's no-null
  amendment applies to an export tariff exactly as to any other input, and this one multiplies into
  every £ on the floor of every report the study produces.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from gridq.ingestion.market_forecasts import (
    MarketForecast,
    MarketForecastPoint,
    hash_market_forecast,
    load_market_forecast_csv,
)
from gridq.optimisation.valuation import (
    ExportTariffUnsourced,
    derive_flat_export_tariff,
)
from gridq.schemas import ConfidenceBand, DerivedAssumption, StudyBasis

#: The repo's sourced GB wholesale series (Elexon Insights Market Index Data, fetch-verified
#: 2026-07-20 and recorded in `assumptions/gb_v1_8.yaml` under ``wholesale_p415``).
ELEXON_MID_CSV = "data/fetched/elexon/mid_2026-07-13.csv"

#: The value the sourced series actually derives to. Pinned: if the file or the derivation changes,
#: every study-basis £ in the campaign changes with it, and that must never happen silently.
EXPECTED_TARIFF_GBP_PER_KWH = 0.109612


# --------------------------------------------------------------------------- helpers


def tariff_record(value: float = EXPECTED_TARIFF_GBP_PER_KWH, **over: object) -> DerivedAssumption:
    fields: dict[str, object] = {
        "kind": "derived_input",
        "key": StudyBasis.TARIFF_KEY,
        "missing_input": "no export arrangement has been supplied for this site",
        "value": value,
        "unit": "GBP/kWh",
        "basis": "mean of a sourced GB wholesale series",
        "sources": ["elexon_insights_mid (sha256:deadbeef); vintage 2026-07-13"],
        "derivation_method": "sourced_default",
        "derived_by": "tests",
        "confidence_ceiling": ConfidenceBand.LOW,
        "confirmation_ask": "obtain a real export offer",
    }
    fields.update(over)
    return DerivedAssumption.model_validate(fields)


def limit_record(value: float = 325.0, **over: object) -> DerivedAssumption:
    fields: dict[str, object] = {
        "kind": "derived_input",
        "key": StudyBasis.EXPORT_LIMIT_KEY,
        "missing_input": "connection.mec_kw is unrecorded",
        "value": value,
        "unit": "kW",
        "basis": "taken as the evidenced import connection limit min(MIC, ASC)",
        "derivation_method": "site_analogue",
        "derived_by": "tests",
        "confidence_ceiling": ConfidenceBand.LOW,
        "confirmation_ask": "obtain the DNO-confirmed MEC",
    }
    fields.update(over)
    return DerivedAssumption.model_validate(fields)


def consent_record(**over: object) -> DerivedAssumption:
    fields: dict[str, object] = {
        "kind": "pending_permission",
        "key": StudyBasis.CONSENT_KEY,
        "missing_input": "the customer's consent to export",
        "basis": "a permission is not derivable from data at any confidence (D45)",
        "derivation_method": "not_derivable",
        "derived_by": "tests",
        "confidence_ceiling": ConfidenceBand.LOW,
        "confirmation_ask": "ask the customer to consent in writing",
    }
    fields.update(over)
    return DerivedAssumption.model_validate(fields)


def study(**over: object) -> StudyBasis:
    fields: dict[str, object] = {
        "basis_id": "export_in_priced_floor_study_v1",
        "authorised_by": "Olly",
        "justification": "D46 feasibility study on the live campaign sites.",
        "acknowledgement": StudyBasis.ACKNOWLEDGEMENT,
        "export_limit_kw": 325.0,
        "export_tariff_gbp_per_kwh": EXPECTED_TARIFF_GBP_PER_KWH,
        "tariff_record": tariff_record(),
        "export_limit_record": limit_record(),
        "consent_record": consent_record(),
    }
    fields.update(over)
    return StudyBasis.model_validate(fields)


def flat_forecast(
    price_gbp_per_mwh: float | None,
    *,
    n: int = 4,
    source: str | None = "test-mid",
    hashed: bool = True,
) -> MarketForecast:
    start = datetime(2026, 7, 13, 1, 0)
    points = [
        MarketForecastPoint(
            timestamp=start + timedelta(minutes=30 * i), price_gbp_per_mwh=price_gbp_per_mwh
        )
        for i in range(n)
    ]
    fc = MarketForecast(points=points, source=source)
    if hashed:
        fc.content_hash = hash_market_forecast(fc)
    return fc


# --------------------------------------------------------------------------- the named basis


def test_a_valid_basis_constructs_and_caps_the_band_at_low() -> None:
    basis = study()
    assert basis.basis_id == StudyBasis.BASIS_ID
    assert basis.confidence_ceiling is ConfidenceBand.LOW
    assert basis.assumption_keys() == [
        StudyBasis.TARIFF_KEY,
        StudyBasis.EXPORT_LIMIT_KEY,
        StudyBasis.CONSENT_KEY,
    ]


def test_the_basis_has_no_default_and_cannot_be_conjured_from_nothing() -> None:
    # Every one of the five declaring fields is required: there is no `StudyBasis()`.
    with pytest.raises(ValidationError):
        StudyBasis()  # type: ignore[call-arg]


def test_acknowledgement_must_be_the_verbatim_sentence() -> None:
    with pytest.raises(ValidationError, match="verbatim sentence"):
        study(acknowledgement="yes, study basis, whatever")
    # Near-misses are refused too — this is the anti-truthy-flag guard, not a keyword search.
    with pytest.raises(ValidationError, match="verbatim sentence"):
        study(acknowledgement=StudyBasis.ACKNOWLEDGEMENT.upper())


def test_unattributed_or_unexplained_basis_is_refused() -> None:
    for over in ({"authorised_by": "   "}, {"justification": " "}):
        with pytest.raises(ValidationError):
            study(**over)


@pytest.mark.parametrize("field", ["export_limit_kw", "export_tariff_gbp_per_kwh"])
def test_non_positive_limit_or_tariff_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        study(**{field: 0.0})
    with pytest.raises(ValidationError):
        study(**{field: -1.0})


def test_non_finite_tariff_is_refused() -> None:
    with pytest.raises(ValidationError, match="must be finite"):
        study(
            export_tariff_gbp_per_kwh=float("inf"),
            tariff_record=tariff_record(value=float("inf")),
        )


def test_an_uncited_tariff_record_is_refused_because_wholesale_prices_are_public() -> None:
    with pytest.raises(ValidationError, match="DERIVED AND SOURCED"):
        study(tariff_record=tariff_record(sources=[]))


def test_the_register_entry_must_agree_with_the_number_the_model_consumes() -> None:
    # A register that disagrees with the priced figure is worse than no register: it reads as
    # corroboration.
    with pytest.raises(ValidationError, match="must equal the value the model consumes"):
        study(tariff_record=tariff_record(value=0.25))
    with pytest.raises(ValidationError, match="must equal the value the model consumes"):
        study(export_limit_record=limit_record(value=999.0))


def test_records_must_name_the_field_they_stand_in_for() -> None:
    with pytest.raises(ValidationError, match="must be"):
        study(tariff_record=tariff_record(key="tariff_stack.import_rates"))
    with pytest.raises(ValidationError, match="must be"):
        study(export_limit_record=limit_record(key="connection.mic_kw"))


def test_the_consent_record_must_be_pending_permission_on_the_consent_key() -> None:
    # A `derived_input` on a consent-shaped key is already unconstructible one layer down
    # (`DerivedAssumption` binds the class to the key), which is what makes this belt-and-braces
    # rather than the only guard.
    with pytest.raises(ValidationError, match="names a PERMISSION"):
        DerivedAssumption.model_validate(
            {
                "kind": "derived_input",
                "key": StudyBasis.CONSENT_KEY,
                "missing_input": "consent",
                "value": "granted",
                "basis": "b",
                "derivation_method": "fable_advisor",
                "derived_by": "x",
                "confidence_ceiling": ConfidenceBand.LOW,
                "confirmation_ask": "a",
            }
        )
    # And a pending_permission on the WRONG key is refused by StudyBasis itself.
    with pytest.raises(ValidationError, match="consent_record.key must be"):
        study(consent_record=consent_record(key="consents.g99"))


def test_a_producer_cannot_raise_its_own_confidence_ceiling() -> None:
    for band in (ConfidenceBand.MEDIUM, ConfidenceBand.HIGH):
        with pytest.raises(ValidationError):
            study(confidence_ceiling=band)


def test_the_basis_is_frozen_because_it_enters_the_run_identity() -> None:
    basis = study()
    with pytest.raises(ValidationError):
        basis.export_tariff_gbp_per_kwh = 0.5  # type: ignore[misc]


def test_every_disclosure_channel_says_study_and_says_consent_is_outstanding() -> None:
    basis = study()
    for text in (basis.note(), basis.floor_entry_note()):
        assert "NOT AN UNDERWRITING OUTPUT" in text
        assert "consent" in text.lower()
    # The note carries the register statements verbatim, in the two D45 report languages, so the
    # reader is never told a permission was "assumed".
    note = basis.note()
    assert "requires customer consent" in note  # the pending_permission label
    assert "assumed, confirmation required" in note  # the derived_input label
    assert basis.solver_basis_label.startswith(StudyBasis.LABEL)


# --------------------------------------------------------------------------- the derived tariff


def test_the_repo_sourced_series_derives_the_pinned_tariff_and_a_complete_record() -> None:
    tariff, record = derive_flat_export_tariff(
        load_market_forecast_csv(ELEXON_MID_CSV), derived_on_release="gb_v1_8"
    )
    assert tariff == EXPECTED_TARIFF_GBP_PER_KWH
    assert record.kind == "derived_input"
    assert record.key == StudyBasis.TARIFF_KEY
    assert record.value == EXPECTED_TARIFF_GBP_PER_KWH
    assert record.unit == "GBP/kWh"
    assert record.confidence_ceiling is ConfidenceBand.LOW
    assert record.derived_on_release == "gb_v1_8"
    # D46 requires the source, the vintage, the direction of effect and the confirmation ask.
    assert record.sources and "elexon_insights_mid" in record.sources[0]
    assert "sha256:" in record.sources[0]
    assert "2026-07-13" in record.basis  # vintage
    assert "DIRECTION OF EFFECT — LEVEL: OPTIMISTIC" in record.basis
    assert "DIRECTION OF EFFECT — SHAPE: PESSIMISTIC" in record.basis
    assert "route-to-market" in record.confirmation_ask
    # And it must never read as this site's contracted price.
    assert "NOT this site's contracted export price" in record.basis


def test_the_derived_tariff_and_record_compose_into_a_valid_basis() -> None:
    tariff, record = derive_flat_export_tariff(load_market_forecast_csv(ELEXON_MID_CSV))
    basis = study(export_tariff_gbp_per_kwh=tariff, tariff_record=record)
    assert basis.export_tariff_gbp_per_kwh == tariff


def test_derivation_is_deterministic() -> None:
    a, _ = derive_flat_export_tariff(load_market_forecast_csv(ELEXON_MID_CSV))
    b, _ = derive_flat_export_tariff(load_market_forecast_csv(ELEXON_MID_CSV))
    assert a == b


def test_a_series_with_no_priced_points_refuses_rather_than_inventing_zero() -> None:
    with pytest.raises(ExportTariffUnsourced, match="NO priced points"):
        derive_flat_export_tariff(flat_forecast(None))


def test_an_unattributable_series_refuses_and_names_the_missing_field() -> None:
    with pytest.raises(ExportTariffUnsourced, match=r"source.*file_ref|file_ref"):
        derive_flat_export_tariff(flat_forecast(100.0, source=None))


def test_an_unhashed_series_refuses_because_the_number_would_not_be_traceable() -> None:
    with pytest.raises(ExportTariffUnsourced, match="content_hash"):
        derive_flat_export_tariff(flat_forecast(100.0, hashed=False))


def test_a_non_positive_mean_refuses_rather_than_pricing_export_at_zero() -> None:
    with pytest.raises(ExportTariffUnsourced, match="not a positive export price"):
        derive_flat_export_tariff(flat_forecast(0.0))


def test_unpriced_points_are_skipped_not_read_as_zero() -> None:
    start = datetime(2026, 7, 13, 1, 0)
    points = [
        MarketForecastPoint(timestamp=start, price_gbp_per_mwh=100.0),
        MarketForecastPoint(timestamp=start + timedelta(minutes=30), price_gbp_per_mwh=None),
        MarketForecastPoint(timestamp=start + timedelta(minutes=60), price_gbp_per_mwh=200.0),
    ]
    fc = MarketForecast(points=points, source="test-mid")
    fc.content_hash = hash_market_forecast(fc)
    tariff, record = derive_flat_export_tariff(fc)
    assert tariff == 0.15  # mean of the TWO priced points, not of three with a zero
    assert "2 PRICED half-hour(s)" in record.basis


def test_the_one_day_raw_series_cannot_cover_a_metered_year_and_says_so() -> None:
    """Why the study basis derives a scalar instead of aligning the series (D46 / the D43 rule).

    The repo's only wholesale file is a single day. Aligning it to a metered YEAR is refused by
    `align_export_prices` (full-coverage-or-skip, never interpolated or extrapolated), which is
    precisely why the study basis prices a derived flat tariff rather than a shaped series.
    """
    import pandas as pd

    from gridq.optimisation.export_prices import align_export_prices

    fc = load_market_forecast_csv(ELEXON_MID_CSV)
    year = pd.date_range("2025-01-01", periods=48 * 365, freq="30min")
    aligned, reason = align_export_prices(fc, year)
    assert aligned is None
    assert reason is not None and "not covered" in reason
