"""The recorded assumption behind a derived VAT basis (WP-UW-07, D103).

`gridq.schemas.TariffBasis` requires every tariff stack to say whether its rates include VAT. When
no invoice line or record field says so, the basis is *derived*, never invented: H-01
(``docs/bess_modelling_requirements.md``) prices a VAT-registered business net of VAT, so the rates
are read as excluding it, and the reading is recorded as a `DerivedAssumption` on
``tariff_stack.basis.vat`` with the direction of its error. `SiteModel` refuses a derived basis
that has no such record.
"""

from __future__ import annotations

from gridq.schemas import ConfidenceBand, DerivedAssumption, TariffBasis, TariffProvenance

__all__ = [
    "H01_SOURCE",
    "UNSTATED_PROVENANCE",
    "VAT_BASIS_KEY",
    "derived_vat_basis",
    "h01_vat_basis_record",
]

VAT_BASIS_KEY = "tariff_stack.basis.vat"
H01_SOURCE = (
    "docs/bess_modelling_requirements.md H-01 (Exclude VAT entirely from the savings calculation "
    "for a VAT-registered business), citing HMRC Notice 701/19, "
    "https://www.gov.uk/guidance/vat-on-fuel-and-power-notice-70119"
)


def derived_vat_basis(
    *, levies: str = "itemised", network_charges: str = "itemised"
) -> TariffBasis:
    """A basis whose VAT treatment is derived under H-01 (rates read as excluding VAT)."""
    return TariffBasis.model_validate(
        {
            "vat": "excluded",
            "vat_attestation": "derived",
            "levies": levies,
            "network_charges": network_charges,
        }
    )


def h01_vat_basis_record(*, subject: str, release: str | None = None) -> DerivedAssumption:
    """The `DerivedAssumption` a derived VAT basis carries, for the stack ``subject`` describes.

    ``subject`` names where the rates came from (an invoice, an estate workbook, a form), so the
    confirmation ask can name it. The record states the direction of the error: if the rates were
    in fact VAT-inclusive, every £ the stack prices, and so every saving, is overstated by the VAT
    that was included.
    """
    return DerivedAssumption(
        kind="derived_input",
        key=VAT_BASIS_KEY,
        missing_input="whether the tariff rates include VAT",
        value="excluded",
        unit=None,
        basis=(
            f"No line of {subject} states whether its rates include VAT. H-01 prices a "
            "VAT-registered business net of VAT (input VAT on its electricity is recoverable), so "
            "the rates are read as excluding VAT. Direction of error: if they were in fact "
            "VAT-inclusive, every priced £ in the stack, and so every saving, is overstated by "
            "the VAT that was included."
        ),
        sources=[H01_SOURCE],
        derivation_method="sourced_default",
        derived_by="gridq.tariffs.basis",
        derived_on_release=release,
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask=(
            f"Confirm whether the unit and standing charges in {subject} are shown before or "
            "after VAT, and that the company is VAT-registered."
        ),
    )


#: The provenance a stack takes when nobody said where its rates came from: every component
#: assumed, so no sentence can call it the site's own.
UNSTATED_PROVENANCE = TariffProvenance(commodity="assumed", network="assumed", levies="assumed")
