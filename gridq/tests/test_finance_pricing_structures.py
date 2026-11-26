"""Tests for the commercial pricing structures (W9 final slice, north_star customer promise).

The capped-bill structure exists as an honest shape: structure returned, NO value, with the
warning naming both hard dependencies — the M&V method (boundary Open Q4, owner unresolved)
and the sourced volume-variance keys (D30 `bill_products` homes; the risk a bill guarantee
transfers is NOT the P90 revenue haircut). Savings-share names Open Q4 the same way. The
fixed-£/kWh default is untouched.
"""

from __future__ import annotations

from gridq.assumptions import OPTIONAL_SECTION_NAMES, load_assumptions
from gridq.finance.pricing import recommend_price
from gridq.schemas import PriceStructure


def test_capped_bill_is_structure_only_with_both_dependencies_named() -> None:
    rec, warnings = recommend_price(PriceStructure.CAPPED_BILL, load_assumptions("gb_v0_1"))
    assert rec.structure == PriceStructure.CAPPED_BILL
    assert rec.value_gbp_per_kwh is None  # never a value on unpriceable risk transfer
    assert rec.hits_target_return is None
    joined = " ".join(warnings)
    assert "Open Q4" in joined and "UNRESOLVED" in joined  # the M&V dependency, honestly owned
    assert "bill_products.volume_variance_pct" in joined  # the D30 key homes, named not invented
    assert "NOT the P90 revenue haircut" in joined  # the risk-shape distinction


def test_savings_share_names_the_mv_dependency() -> None:
    rec, warnings = recommend_price(PriceStructure.SAVINGS_SHARE, load_assumptions("gb_v0_1"))
    assert rec.value_gbp_per_kwh is None
    assert any("Open Q4" in w for w in warnings)


def test_bill_products_is_a_registered_growth_section() -> None:
    assert "bill_products" in OPTIONAL_SECTION_NAMES
    for version in ("gb_v0_1", "gb_v1_0", "gb_v1_1"):
        assert load_assumptions(version).bill_products == {}  # predates the section — never zero
