"""Supply boundary on the SiteModel (L21 · M1 schemas).

The System Brief's canonical schema lists "supply boundary & supplier consent" as two distinct
required elements. Supplier consent is `ConsentState.supplier_consent_state`; these tests pin the
other half — a `SupplyBoundary` (import/supply MPAN + behind-the-meter perimeter) present on every
SiteModel, defaulting to unknown and recordable when known.
"""

from __future__ import annotations

import gridq.schemas as s


def test_site_has_a_supply_boundary_group(make_site) -> None:
    site = make_site()
    assert "supply_boundary" in s.SiteModel.model_fields
    # Default is an all-unknown bundle present on every site (never invented).
    assert site.supply_boundary.import_mpan is None
    assert site.supply_boundary.behind_the_meter is None


def test_supply_boundary_records_the_boundary(make_site) -> None:
    site = make_site()
    bounded = site.model_copy(
        update={
            "supply_boundary": s.SupplyBoundary(
                import_mpan="2000012345678",
                behind_the_meter=True,
                metering_point="HH import meter A",
            )
        }
    )
    assert bounded.supply_boundary.import_mpan == "2000012345678"
    assert bounded.supply_boundary.behind_the_meter is True
    assert bounded.supply_boundary.metering_point == "HH import meter A"


def test_supply_boundary_is_distinct_from_supplier_consent(make_site) -> None:
    # The Brief lists both — they are separate fields, not the same one.
    site = make_site()
    assert hasattr(site, "supply_boundary")
    assert hasattr(site.consents, "supplier_consent_state")
