"""Reprice-tolerance check (L02 · X.1 / consistency contract #3).

The System Brief: a quote is valid only against the assumption version that produced it, and an
*assumption update beyond tolerance → reprice before signature*. These tests prove the mechanism:

  * the tolerance comes from the library (no private constant);
  * an identical / sub-tolerance move does **not** force a reprice;
  * a numeric move beyond tolerance **does**, and names the breaching assumption;
  * a structural change (add/remove/non-numeric) is always material;
  * the quote-level entry point loads the priced-against version by the quote's stamp.
"""

from __future__ import annotations

from datetime import date

from gridq.assumptions import (
    Assumption,
    AssumptionLibrary,
    AssumptionStatus,
    assess_quote_reprice,
    assess_reprice,
    load_assumptions,
    reprice_tolerance_pct,
)
from gridq.schemas import DataTier, QuoteMode, QuoteRecord
from gridq.schemas.enums import Confidence

VERSION = "gb_v0_1"


def _library() -> AssumptionLibrary:
    return load_assumptions(VERSION)


def _bump(lib: AssumptionLibrary, section: str, name: str, factor: float) -> AssumptionLibrary:
    """Return a deep copy of ``lib`` with one numeric assumption scaled by ``factor``."""
    moved = lib.model_copy(deep=True)
    getattr(moved, section)[name].value *= factor
    return moved


def _quote(version: str | None = VERSION) -> QuoteRecord:
    return QuoteRecord(
        quote_id="q_test",
        quote_mode=QuoteMode.INDICATIVE,
        site_id="site_test",
        assumption_version=version,
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
    )


def test_tolerance_is_sourced_from_the_library() -> None:
    # The check carries no hardcoded threshold; it reads quote_validity.reprice_tolerance_pct.
    assert reprice_tolerance_pct(_library()) == 5.0


def test_identical_library_does_not_reprice() -> None:
    lib = _library()
    check = assess_reprice(lib, lib.model_copy(deep=True))
    assert check.needs_reprice is False
    assert check.breaches == []


def test_within_tolerance_move_does_not_reprice() -> None:
    lib = _library()
    # +2% on a numeric assumption, under the 5% tolerance.
    current = _bump(lib, "battery_capex", "installed_cost", 1.02)
    check = assess_reprice(lib, current)
    assert check.needs_reprice is False


def test_beyond_tolerance_move_forces_reprice() -> None:
    lib = _library()
    # +50% on installed cost — well beyond the 5% tolerance.
    current = _bump(lib, "battery_capex", "installed_cost", 1.50)
    check = assess_reprice(lib, current)
    assert check.needs_reprice is True
    keys = {m.key for m in check.breaches}
    assert any("battery_capex.installed_cost" in k for k in keys)
    assert "reprice required" in check.summary()


def test_structural_change_is_always_material() -> None:
    lib = _library()
    current = lib.model_copy(deep=True)
    # Drop an assumption entirely — a structural change with no measurable %.
    name = next(iter(current.battery_capex))
    del current.battery_capex[name]
    check = assess_reprice(lib, current)
    assert check.needs_reprice is True
    breach = next(m for m in check.breaches if m.key.startswith(f"battery_capex.{name}"))
    assert breach.change_pct is None


def test_added_assumption_with_empty_value_is_material() -> None:
    # An add/remove must be caught even when the value flattens to no leaves (empty mapping).
    lib = _library()
    current = lib.model_copy(deep=True)
    current.battery_capex["new_structured"] = Assumption(
        value={},
        unit="GBP",
        source="placeholder",
        status=AssumptionStatus.PLACEHOLDER,
        owner_or_reviewer="Oli",
        confidence=Confidence.LOW,
        last_reviewed=date(2026, 6, 17),
        notes="x",
    )
    check = assess_reprice(lib, current)
    assert check.needs_reprice is True
    assert any(m.key == "battery_capex.new_structured" for m in check.breaches)


def test_unit_change_is_material_even_at_same_value() -> None:
    # A redenomination (same number, different unit) is material — the quote must reprice.
    lib = _library()
    current = lib.model_copy(deep=True)
    name = next(iter(current.battery_capex))
    current.battery_capex[name].unit += "_changed"
    check = assess_reprice(lib, current)
    assert check.needs_reprice is True
    assert any(m.key.endswith("[unit]") for m in check.breaches)


def test_assess_quote_reprice_loads_priced_against_version() -> None:
    # The quote stores only its version; the check reloads that library to compare.
    current = _bump(_library(), "battery_capex", "installed_cost", 1.50)
    check = assess_quote_reprice(_quote(VERSION), current)
    assert check.needs_reprice is True


def test_unchanged_current_against_quote_does_not_reprice() -> None:
    check = assess_quote_reprice(_quote(VERSION), _library())
    assert check.needs_reprice is False
