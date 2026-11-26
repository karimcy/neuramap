"""WP-UW-07 acceptance 3 — every TariffStack states its VAT and levy basis; none is guessed.

D103. A rate whose basis is unknown cannot be compared with any other rate: a VAT-inclusive
invoice rate read as net overstates every saving by the VAT in it. So `TariffStack.basis` is
required, says whether the £ figures include VAT (and at what rate, from the record), whether levy
and network lines are itemised or inside the unit rates, and how the VAT basis is known. A basis
inferred under H-01 must carry its `DerivedAssumption` on the same site. A VAT-inclusive stack is
priced net of VAT once, in the pipeline, and the site as supplied is what the record hashes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests._dpa_support import dpa_released

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.app.demo import load_demo_site
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.governance.customer_data import load_manifest
from gridq.optimisation.tariff_calendar import net_of_vat_stack, resolved_price_basis
from gridq.quotes import generate_quote
from gridq.schemas import SiteModel, TariffBasis, TariffProvenance, TariffStack
from gridq.tariffs.basis import VAT_BASIS_KEY, derived_vat_basis, h01_vat_basis_record

_ROOT = Path(__file__).resolve().parents[1]


def test_a_stack_without_a_basis_is_refused() -> None:
    with pytest.raises(ValidationError, match="basis"):
        TariffStack(provenance=SYNTHETIC_PROVENANCE, import_rates={"single": 0.2})  # type: ignore[call-arg]


def test_a_stack_without_provenance_is_refused() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        TariffStack(basis=SYNTHETIC_BASIS, import_rates={"single": 0.2})  # type: ignore[call-arg]


def test_a_basis_must_state_every_part() -> None:
    for missing in ("vat", "vat_attestation", "levies", "network_charges"):
        fields = {
            "vat": "excluded",
            "vat_attestation": "stated_by_record",
            "levies": "itemised",
            "network_charges": "itemised",
        }
        del fields[missing]
        with pytest.raises(ValidationError, match=missing):
            TariffBasis.model_validate(fields)


def test_an_included_vat_basis_needs_its_rate_and_an_excluded_one_refuses_one() -> None:
    with pytest.raises(ValidationError, match="no vat_rate_fraction"):
        TariffBasis(
            vat="included",
            vat_attestation="stated_by_record",
            levies="itemised",
            network_charges="itemised",
        )
    with pytest.raises(ValidationError, match="has no VAT in it"):
        TariffBasis(
            vat="excluded",
            vat_rate_fraction=0.2,
            vat_attestation="stated_by_record",
            levies="itemised",
            network_charges="itemised",
        )


def test_levies_inside_the_unit_rates_refuse_an_itemised_levy_line() -> None:
    basis = SYNTHETIC_BASIS.model_copy(update={"levies": "in_unit_rates"})
    with pytest.raises(ValidationError, match="never both"):
        TariffStack(
            basis=basis,
            provenance=SYNTHETIC_PROVENANCE,
            import_rates={"x": 0.2},
            levies={"ro": 0.01},
        )


def test_network_charges_inside_the_unit_rates_refuse_a_duos_line() -> None:
    basis = SYNTHETIC_BASIS.model_copy(update={"network_charges": "in_unit_rates"})
    with pytest.raises(ValidationError, match="network_charges"):
        TariffStack(
            basis=basis,
            provenance=SYNTHETIC_PROVENANCE,
            import_rates={"x": 0.2},
            duos_red_rate=0.1,
        )


def test_not_charged_network_must_agree_between_basis_and_provenance() -> None:
    basis = SYNTHETIC_BASIS.model_copy(update={"network_charges": "not_charged"})
    with pytest.raises(ValidationError, match="disagree"):
        TariffStack(basis=basis, provenance=SYNTHETIC_PROVENANCE, import_rates={"x": 0.2})


def test_a_derived_vat_basis_needs_its_record_on_the_site() -> None:
    site = load_demo_site()
    stack = site.tariff_stack.model_copy(update={"basis": derived_vat_basis()})
    dumped = site.model_dump(mode="json")
    dumped["tariff_stack"] = stack.model_dump(mode="json")
    dumped["derived_assumptions"] = None
    with pytest.raises(ValidationError, match="tariff_stack.basis.vat"):
        SiteModel.model_validate(dumped)
    record = h01_vat_basis_record(subject="the demo invoice", release="gb_v1_4")
    dumped["derived_assumptions"] = [record.model_dump(mode="json")]
    recorded = SiteModel.model_validate(dumped)
    assert recorded.tariff_stack.basis.vat_attestation == "derived"
    assert VAT_BASIS_KEY in {r.key for r in recorded.derived_assumptions or ()}


def _public_carriers() -> list[Path]:
    """The public example records that carry a tariff stack (not customer data)."""
    paths = [
        *(_ROOT / "examples").glob("*.json"),
        _ROOT / "examples" / "demo_site" / "sitemodel.json",
    ]
    return sorted(p for p in paths if "tariff_stack" in p.read_text())


def _campaign_carriers() -> list[Path]:
    """Every campaign site record (WP-GRC-05 quarantined customer data), from the committed
    manifest, never by listing or opening the quarantined directory. Each is read only once the
    DPA guard releases it (`_dpa_support.dpa_released`); until then its case skips, naming its
    blocker."""
    return sorted(
        _ROOT / e.path
        for e in load_manifest().datasets
        if e.path.startswith("examples/real/") and e.kind == "site_facts"
    )


def _carriers() -> list[Path]:
    return [*_public_carriers(), *_campaign_carriers()]


def _read_carrier(path: Path) -> dict:
    """A public record directly; a campaign record only through the guard."""
    if path in _campaign_carriers():
        path = dpa_released(path)
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", _carriers(), ids=lambda p: p.stem)
def test_every_shipped_site_states_the_basis_of_its_rates(path: Path) -> None:
    raw = _read_carrier(path)
    stack = raw.get("tariff_stack") or raw.get("site_model", {}).get("tariff_stack")
    assert stack is not None
    basis = TariffBasis.model_validate(stack["basis"])
    TariffProvenance.model_validate(stack["provenance"])
    if basis.vat_attestation == "derived":
        keys = {
            r["key"]
            for r in (
                raw.get("derived_assumptions")
                or raw.get("site_model", {}).get("derived_assumptions")
                or []
            )
        }
        assert VAT_BASIS_KEY in keys, f"{path.name}: derived VAT basis with no record"


# ------------------------------------------------------------------ priced net of VAT, once

_INCLUDED = TariffBasis(
    vat="included",
    vat_rate_fraction=0.2,
    vat_attestation="stated_by_record",
    levies="itemised",
    network_charges="itemised",
)


def test_a_vat_inclusive_stack_is_priced_net_of_vat_once() -> None:
    stack = TariffStack(
        basis=_INCLUDED,
        provenance=SYNTHETIC_PROVENANCE,
        import_rates={"day": 0.36, "night": 0.12},
        export_rates={"flat": 0.06},
        levies={"ro": 0.024},
        duos_red_rate=0.12,
        standing_charge_per_day_gbp=6.0,
        capacity_charge_gbp_per_kva_day=0.048,
    )
    assert resolved_price_basis(stack).divisor == pytest.approx(1.2)
    net = net_of_vat_stack(stack)
    assert net.import_rates == pytest.approx({"day": 0.30, "night": 0.10})
    assert net.export_rates == pytest.approx({"flat": 0.05})
    assert net.levies == pytest.approx({"ro": 0.02})
    assert net.duos_red_rate == pytest.approx(0.10)
    assert net.standing_charge_per_day_gbp == pytest.approx(5.0)
    assert net.capacity_charge_gbp_per_kva_day == pytest.approx(0.04)
    assert net.basis.vat == "excluded" and net.basis.vat_rate_fraction is None
    assert net_of_vat_stack(net) is net, "a net stack is never divided a second time"
    assert resolved_price_basis(net).note is None


class _CapturingEngine(BaselineEngine):
    """The baseline engine, remembering the site the pipeline handed it to price."""

    priced: SiteModel | None = None

    def build_optimisation_request(self, site_model, assumptions, scenario_set=None):  # type: ignore[override]
        type(self).priced = site_model
        return super().build_optimisation_request(site_model, assumptions, scenario_set)


def test_the_pipeline_prices_the_net_stack_and_records_the_supplied_one() -> None:
    supplied = load_demo_site()
    gross = supplied.tariff_stack.model_copy(
        update={
            "basis": _INCLUDED,
            "import_rates": {k: v * 1.2 for k, v in supplied.tariff_stack.import_rates.items()},
        }
    )
    site = supplied.model_copy(update={"tariff_stack": gross})
    run = generate_quote(site, load_assumptions("gb_v1_4"), engine=_CapturingEngine())
    priced = _CapturingEngine.priced
    assert priced is not None
    assert priced.tariff_stack.basis.vat == "excluded"
    assert priced.tariff_stack.import_rates == pytest.approx(supplied.tariff_stack.import_rates)
    assert run.site.tariff_stack.basis.vat == "included", "the record keeps the site as supplied"
    assert any("INCLUDE VAT at 0.2" in w for w in run.quote.warnings)


def test_a_re_run_carries_the_recorded_vat_rate_exactly() -> None:
    """`_improve.html` posts the recorded fraction, not template arithmetic; it parses back."""
    from gridq.app.app import _tariff_basis_from_form

    template = (_ROOT / "src" / "gridq" / "app" / "templates" / "_improve.html").read_text()
    assert 'name="vat_rate_fraction"' in template and 'name="vat_rate_percent"' not in template
    form = {
        "vat_basis": "included",
        "vat_rate_fraction": "0.2",
        "levy_basis": "itemised",
        "network_basis": "itemised",
        "provenance_commodity": "own_invoice",
        "provenance_network": "published_for_site",
        "provenance_levies": "assumed",
    }
    basis, provenance, records = _tariff_basis_from_form(form)
    assert basis == _INCLUDED
    assert provenance.commodity == "own_invoice" and records == []
