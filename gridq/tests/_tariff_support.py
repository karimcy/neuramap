"""The basis and provenance a synthetic test tariff states about itself (IC-12 3.0.0, WP-UW-07).

Every `TariffStack` must state its VAT/levy/network basis and where each component came from
(D103/D104). A synthetic test stack is authored by the test, so its basis is
``stated_by_record`` (the author is the record) and every component is ``assumed`` (no site's
invoice or published row stands behind it). Tests that exercise the basis or provenance build
their own; everything else uses these.
"""

from __future__ import annotations

from gridq.schemas import TariffBasis, TariffProvenance

SYNTHETIC_BASIS = TariffBasis(
    vat="excluded",
    vat_attestation="stated_by_record",
    levies="itemised",
    network_charges="itemised",
)

SYNTHETIC_PROVENANCE = TariffProvenance(commodity="assumed", network="assumed", levies="assumed")

#: A private-wire synthetic stack: no network charge applies.
SYNTHETIC_BASIS_PRIVATE_WIRE = SYNTHETIC_BASIS.model_copy(update={"network_charges": "not_charged"})
SYNTHETIC_PROVENANCE_PRIVATE_WIRE = SYNTHETIC_PROVENANCE.model_copy(
    update={"network": "not_charged"}
)
