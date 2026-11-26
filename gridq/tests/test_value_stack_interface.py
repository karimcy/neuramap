"""Tests for the value-stream interface, base classes and registry.

These assert the *shape and invariants* of the framework — Protocol conformance, the
registry returning the expected streams, the revenue-free value function, and the
GateResult consistency rules. Gate *logic* is exercised in test_value_stack_gates.py.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.schemas import Firmness, MarketStream
from gridq.value_stack import (
    DispatchConstraintDescriptor,
    FloorStream,
    GatedUpsideStream,
    GateResult,
    GateStatus,
    RequiredInput,
    StreamRegistry,
    ValueStream,
    build_gate_result,
    default_registry,
    gb_value_streams,
)

# The nine streams the GB value stack must expose, in registry order.
EXPECTED_STREAM_IDS = [
    MarketStream.PPA_OR_BILL_SAVINGS,
    MarketStream.TARIFF_ARBITRAGE,
    MarketStream.DUOS_RED_BAND_AVOIDANCE,
    MarketStream.MIC_ASC_HEADROOM,
    MarketStream.CAPACITY_MARKET,
    MarketStream.LOCAL_FLEX_PICLO,
    MarketStream.LOAD_SERVICE,
    MarketStream.WHOLESALE_P415,
    MarketStream.BALANCING_MECHANISM,
    MarketStream.ANCILLARY_DC_DM_DR,
    # E-06: DFS joined as the only direct NESO route below 1 MW, appended last so the pre-existing
    # registry order is unchanged.
    MarketStream.DFS,
    # MICASC-3: the avoided agreed-capacity charge + TCR band step. Appended on the same
    # discipline as DFS — the pre-existing registry order is untouched.
    MarketStream.AGREED_CAPACITY_REDUCTION,
]


def _t2_site() -> s.SiteModel:
    """A well-populated T2 site (floor streams open, gated/contracted conditional)."""
    return s.SiteModel(
        site_id="site_iface",
        postcode="LE17 4XN",
        data_tier=s.DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=s.LoadDataSummary(
            annual_kwh=1_800_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=420.0,
        ),
        annual_consumption_kwh=1_800_000.0,
        tariff_stack=s.TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"red": 0.42, "amber": 0.21, "green": 0.14},
            duos_red_rate=0.18,
        ),
        connection=s.ConnectionDetails(mic_kw=300.0, dno_region="WPD_East_Midlands"),
        consents=s.ConsentState(),
        physical_gates=s.PhysicalGateStatus(),
        counterparty=s.Counterparty(legal_name="Acme Ltd", company_number="01234567"),
    )


# --- registry --------------------------------------------------------------


def test_registry_returns_expected_streams() -> None:
    reg = default_registry()
    # LOAD_SERVICE joined under D42 (issue 017); DFS under E-06; AGREED_CAPACITY_REDUCTION
    # under MICASC-3.
    assert len(reg) == 12
    assert reg.stream_ids() == EXPECTED_STREAM_IDS
    # every MarketStream enum member is represented exactly once
    assert set(reg.stream_ids()) == set(MarketStream)


def test_registry_get_and_membership() -> None:
    reg = default_registry()
    stream = reg.get(MarketStream.TARIFF_ARBITRAGE)
    assert stream.stream_id == MarketStream.TARIFF_ARBITRAGE
    assert MarketStream.TARIFF_ARBITRAGE in reg


def test_registry_requested_subset_preserves_order() -> None:
    reg = default_registry()
    subset = reg.requested([MarketStream.MIC_ASC_HEADROOM, MarketStream.TARIFF_ARBITRAGE])
    # returned in registry order, not request order
    assert [s.stream_id for s in subset] == [
        MarketStream.TARIFF_ARBITRAGE,
        MarketStream.MIC_ASC_HEADROOM,
    ]


def test_registry_rejects_duplicate_ids() -> None:
    streams = gb_value_streams()
    with pytest.raises(ValueError, match="duplicate stream id"):
        StreamRegistry([streams[0], streams[0]])


def test_registry_get_unknown_raises() -> None:
    reg = StreamRegistry([gb_value_streams()[0]])
    with pytest.raises(KeyError):
        reg.get(MarketStream.CAPACITY_MARKET)


# --- Protocol conformance + declarations -----------------------------------


def test_all_streams_satisfy_protocol() -> None:
    for stream in default_registry():
        assert isinstance(stream, ValueStream)
        assert isinstance(stream.stream_id, MarketStream)
        assert isinstance(stream.display_name, str) and stream.display_name


def test_required_inputs_are_descriptors() -> None:
    for stream in default_registry():
        reqs = stream.required_inputs()
        assert reqs, f"{stream.stream_id} declared no required inputs"
        assert all(isinstance(r, RequiredInput) for r in reqs)


def test_constraints_are_descriptors() -> None:
    lib = load_assumptions("gb_v0_1")
    site = _t2_site()
    for stream in default_registry():
        cons = stream.constraints(site, lib)
        assert isinstance(cons, list)
        assert all(isinstance(c, DispatchConstraintDescriptor) for c in cons)


# --- value function: never invents revenue ---------------------------------


def test_value_function_invents_no_revenue() -> None:
    lib = load_assumptions("gb_v0_1")
    site = _t2_site()
    for stream in default_registry():
        result = stream.value_function(None, site, lib, None)
        assert isinstance(result, s.ValueStreamResult)
        # nothing is priced in v1 — the gate decides firmness, not money
        assert result.annual_revenue_gbp == 0.0
        assert result.annual_cost_gbp == 0.0
        assert result.stream == stream.stream_id
        assert result.warnings  # always carries an explanatory note


def test_value_function_firmness_matches_gate() -> None:
    lib = load_assumptions("gb_v0_1")
    site = _t2_site()
    for stream in default_registry():
        gate = stream.check_gates(site, lib)
        result = stream.value_function(None, site, lib, None)
        assert result.firmness == gate.firmness
        assert result.gate_status == gate.gate_status.value


def test_value_function_requires_site_and_assumptions() -> None:
    stream = default_registry().get(MarketStream.TARIFF_ARBITRAGE)
    with pytest.raises(ValueError, match="requires site_model and assumptions"):
        stream.value_function(None, None, None, None)  # type: ignore[arg-type]


def test_gated_value_function_never_firm() -> None:
    lib = load_assumptions("gb_v0_1")
    site = _t2_site()
    reg = default_registry()
    for sid in s.GATED_UPSIDE_STREAMS:
        result = reg.get(sid).value_function(None, site, lib, None)
        assert result.firmness != Firmness.FIRM


# --- determinism -----------------------------------------------------------


def test_gates_and_value_are_deterministic() -> None:
    lib = load_assumptions("gb_v0_1")
    site = _t2_site()
    for stream in default_registry():
        g1 = stream.check_gates(site, lib).model_dump(mode="json")
        g2 = stream.check_gates(site, lib).model_dump(mode="json")
        assert g1 == g2
        v1 = stream.value_function(None, site, lib, None).model_dump(mode="json")
        v2 = stream.value_function(None, site, lib, None).model_dump(mode="json")
        assert v1 == v2


# --- GateResult / builder invariants ---------------------------------------


def test_build_gate_result_open_is_priceable_and_firm() -> None:
    g = build_gate_result(MarketStream.TARIFF_ARBITRAGE, GateStatus.OPEN)
    assert g.can_be_priced_now is True
    assert g.firmness == Firmness.FIRM


def test_build_gate_result_conditional_not_priceable() -> None:
    g = build_gate_result(
        MarketStream.CAPACITY_MARKET,
        GateStatus.CONDITIONAL,
        open_firmness=Firmness.CONTRACTED,
        unlock_condition="deliverability",
    )
    assert g.can_be_priced_now is False
    assert g.firmness == Firmness.CONDITIONAL


def test_build_gate_result_closed_is_excluded() -> None:
    g = build_gate_result(MarketStream.DUOS_RED_BAND_AVOIDANCE, GateStatus.CLOSED)
    assert g.can_be_priced_now is False
    assert g.firmness == Firmness.EXCLUDED


def test_gate_result_rejects_inconsistent_priceability() -> None:
    with pytest.raises(ValidationError, match="can_be_priced_now"):
        GateResult(
            stream=MarketStream.TARIFF_ARBITRAGE,
            gate_status=GateStatus.CLOSED,
            can_be_priced_now=True,  # inconsistent: only OPEN is priceable
            firmness=Firmness.EXCLUDED,
        )


def test_gate_result_conditional_requires_unlock() -> None:
    with pytest.raises(ValidationError, match="unlock_condition"):
        GateResult(
            stream=MarketStream.CAPACITY_MARKET,
            gate_status=GateStatus.CONDITIONAL,
            can_be_priced_now=False,
            firmness=Firmness.CONDITIONAL,
            unlock_condition=None,  # conditional must say how it unlocks
        )


def test_gate_result_gated_cannot_be_firm() -> None:
    # Even via build_gate_result, a gated-upside stream cannot reach FIRM through OPEN
    # because the gated stream never opens; assert the GateResult validator too.
    with pytest.raises(ValidationError, match="cannot be marked firm"):
        GateResult(
            stream=MarketStream.WHOLESALE_P415,
            gate_status=GateStatus.OPEN,
            can_be_priced_now=True,
            firmness=Firmness.FIRM,
        )


# --- base-class guards -----------------------------------------------------


def test_gated_base_rejects_non_gated_stream() -> None:
    with pytest.raises(ValueError, match="not a gated-upside stream"):
        # a floor stream id cannot be built as a gated-upside stream
        class _Bad(GatedUpsideStream):
            def check_gates(self, site_model, assumptions):  # type: ignore[no-untyped-def]
                return self._gate(GateStatus.CONDITIONAL)

        _Bad(
            stream_id=MarketStream.TARIFF_ARBITRAGE,
            display_name="bad",
            required_inputs=[RequiredInput(key="x", description="x")],
        )


def test_floor_stream_open_firmness_is_firm() -> None:
    class _Floor(FloorStream):
        def check_gates(self, site_model, assumptions):  # type: ignore[no-untyped-def]
            return self._gate(GateStatus.OPEN)

    stream = _Floor(
        stream_id=MarketStream.TARIFF_ARBITRAGE,
        display_name="floor",
        required_inputs=[RequiredInput(key="x", description="x")],
    )
    lib = load_assumptions("gb_v0_1")
    assert stream.check_gates(_t2_site(), lib).firmness == Firmness.FIRM


# --- gated-upside: OPEN only with a consent basis (Revenue-Data Drive / D43) ---


def _gated_stub() -> GatedUpsideStream:
    class _Gated(GatedUpsideStream):
        def check_gates(self, site_model, assumptions, context=None):  # type: ignore[no-untyped-def]
            return self._gate(GateStatus.CONDITIONAL, unlock_condition="x")

    return _Gated(
        stream_id=MarketStream.WHOLESALE_P415,
        display_name="gated",
        required_inputs=[RequiredInput(key="x", description="x")],
    )


def test_gated_open_without_consent_still_raises() -> None:
    stream = _gated_stub()
    with pytest.raises(ValueError, match="cannot open into the priced floor without"):
        stream._gate(GateStatus.OPEN)  # no consent basis


def test_gated_open_with_consent_is_contracted_never_firm() -> None:
    stream = _gated_stub()
    gate = stream._gate(GateStatus.OPEN, consent_status="given")
    assert gate.gate_status == GateStatus.OPEN
    assert gate.firmness == Firmness.CONTRACTED  # never FIRM
    assert gate.consent_status == "given"


def test_gated_conditional_carries_consent_label() -> None:
    stream = _gated_stub()
    gate = stream._gate(GateStatus.CONDITIONAL, unlock_condition="x", consent_status="given")
    assert gate.gate_status == GateStatus.CONDITIONAL
    assert gate.firmness == Firmness.CONDITIONAL
    assert gate.consent_status == "given"


def test_consent_status_propagates_onto_value_stream_result() -> None:
    """The proxy value_function path copies consent_status from the gate onto the result."""

    class _Floor(FloorStream):
        def check_gates(self, site_model, assumptions, context=None):  # type: ignore[no-untyped-def]
            return self._gate(GateStatus.OPEN, consent_status="given")

    stream = _Floor(
        stream_id=MarketStream.TARIFF_ARBITRAGE,
        display_name="floor",
        required_inputs=[RequiredInput(key="x", description="x")],
    )
    lib = load_assumptions("gb_v0_1")
    result = stream.value_function(None, _t2_site(), lib, None)
    assert result.consent_status == "given"
