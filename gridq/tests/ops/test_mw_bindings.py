"""WP-OPS-11 criterion 1, the mapping half (D144).

Every term of every candidate maps to exactly one source: a field of a frozen contract (by JSON
pointer, resolved here in the committed schema and in the live model), a field of the WP-OPS-01
dossier (resolved in the dossier's schema), or "not yet measurable" with the contract, data
product and roadmap work packages that will measure it (checked against a pinned, verbatim
excerpt of the Atlas roadmap). A tripwire fails the day one of those contracts or products
appears, so a stale "not yet measurable" cannot survive it.

NEW-IC-14 froze as IC-14 1.0.0 (WP-TEL-01, D148 to D153), and the tripwire fired as designed: the
seven telemetry terms IC-14 can carry are re-bound to its curated series, each with its point
pattern, sign, import/export split and kW rounding (`UnitFromQuantity`). Two stay not yet
measurable: the control-authority state (no IC-14 1.0.0 quantity) and the enveloped-battery
meter entry (the meter registry, WP-TEL-04). The tripwire now watches IC-8, the named products,
and IC-14 moving past 1.0.0.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from gridq.contracts import telemetry_series
from gridq.contracts.__main__ import ARCHIVED, FROZEN, SCHEMA_DIR, schema_path
from gridq.ops.dossier_model import CandidateSiteDossier
from gridq.ops.mw_bindings import (
    ROADMAP_PIN,
    BindingTable,
    DossierFieldBinding,
    FrozenContractField,
    NotYetMeasurable,
    UnitFromQuantity,
    ic14_kw,
    point_pattern,
    roadmap_wp,
    schema_leaves,
)
from gridq.ops.mw_definitions import BINDINGS, DEFINITIONS
from gridq.ops.mw_draft import render_draft_agreement

REPO = Path(__file__).resolve().parents[2]
ATLAS = REPO.parent / "atlas"

#: Every term of every candidate and the kind of its source. A change here is deliberate.
EXPECTED_KINDS: dict[str, dict[str, str]] = {
    "site_load_under_management": {
        "a.asset_output_kw": "frozen_contract_field",
        "a.boundary_export_kw": "frozen_contract_field",
        "a.boundary_import_kw": "frozen_contract_field",
        "a.firm_import_mw": "dossier_field",
        "a.non_firm_import_mw": "dossier_field",
        "control_authority": "not_yet_measurable",
        "control_signoff": "frozen_contract_field",
        "envelope_in_force": "frozen_contract_field",
    },
    "bess_mw_actively_dispatched": {
        "b.asset_enveloped": "not_yet_measurable",
        "b.committed_discharge_kw": "frozen_contract_field",
        "b.instructed_kw": "frozen_contract_field",
        "b.pcs_ac_kw": "frozen_contract_field",
        "b.rated_discharge_kw": "frozen_contract_field",
        "control_authority": "not_yet_measurable",
        "control_signoff": "frozen_contract_field",
        "envelope_in_force": "frozen_contract_field",
    },
    "verified_flexible_mw": {
        "c.asset_baseline_kw": "not_yet_measurable",
        "c.asset_evidence_metered": "frozen_contract_field",
        "c.asset_kw": "frozen_contract_field",
        "c.baseline_kw": "not_yet_measurable",
        "c.baseline_preregistered": "not_yet_measurable",
        "c.compliant": "not_yet_measurable",
        "c.event_held_min": "not_yet_measurable",
        "c.net_import_kw": "frozen_contract_field",
        "c.verifier_recomputed": "not_yet_measurable",
        "control_authority": "not_yet_measurable",
        "control_signoff": "frozen_contract_field",
        "envelope_in_force": "frozen_contract_field",
    },
}


def _all() -> list[tuple[str, str, Any]]:
    return [(did, b.term_id, b.binding) for did, table in BINDINGS.items() for b in table.bindings]


def _frozen_fields() -> list[tuple[str, FrozenContractField]]:
    out: list[tuple[str, FrozenContractField]] = []
    for did, term_id, b in _all():
        if isinstance(b, FrozenContractField):
            out.append((f"{did}:{term_id}", b))
        elif isinstance(b, DossierFieldBinding) and isinstance(b.via, FrozenContractField):
            out.append((f"{did}:{term_id}:via", b.via))
    return out


def _not_yet() -> list[tuple[str, NotYetMeasurable]]:
    return [(f"{d}:{t}", b) for d, t, b in _all() if isinstance(b, NotYetMeasurable)]


# =============================================================================================
# Totality: one binding per term, kept apart from the formula
# =============================================================================================


def test_every_term_of_every_candidate_has_exactly_one_binding_of_the_expected_kind() -> None:
    assert set(BINDINGS) == {d.definition_id for d in DEFINITIONS}
    for d in DEFINITIONS:
        table = BINDINGS[d.definition_id]
        assert table.formula_hash == d.content_hash()
        kinds = {b.term_id: b.binding.kind for b in table.bindings}
        assert kinds == EXPECTED_KINDS[d.definition_id]
        assert set(kinds) == {t.term_id for t in d.terms}


def test_a_binding_table_refuses_a_repeated_term() -> None:
    table = BINDINGS["site_load_under_management"]
    with pytest.raises(ValueError, match="one binding per term"):
        BindingTable(
            definition_id=table.definition_id,
            formula_hash=table.formula_hash,
            bindings=(table.bindings[0], table.bindings[0]),
        )


def test_rebinding_a_term_changes_the_binding_hash_not_the_formula_hash() -> None:
    table = BINDINGS["site_load_under_management"]
    i = next(n for n, b in enumerate(table.bindings) if isinstance(b.binding, NotYetMeasurable))
    first = table.bindings[i]
    assert isinstance(first.binding, NotYetMeasurable)
    moved = first.model_copy(
        update={"binding": first.binding.model_copy(update={"needs": "TEST FIXTURE rebinding"})}
    )
    rebound = table.model_copy(
        update={"bindings": (*table.bindings[:i], moved, *table.bindings[i + 1 :])}
    )
    assert rebound.binding_hash() != table.binding_hash()
    assert rebound.formula_hash == table.formula_hash == DEFINITIONS[0].content_hash()


# =============================================================================================
# Frozen contract fields resolve in the committed schema and in the live model
# =============================================================================================

_CURRENT = {stem: (model, version) for stem, model, version in FROZEN}


@pytest.mark.parametrize(
    ("where", "field"), _frozen_fields(), ids=lambda x: x if isinstance(x, str) else ""
)
def test_a_frozen_field_names_the_current_unarchived_version(
    where: str, field: FrozenContractField
) -> None:
    assert field.schema_stem in _CURRENT, where
    _, version = _CURRENT[field.schema_stem]
    assert field.version == version, f"{where}: {field.schema_stem} is at {version}"
    path = schema_path(field.schema_stem, field.version)
    assert path.is_file()
    assert path.name not in ARCHIVED
    assert field.schema_stem.startswith(f"ic{field.contract_id.removeprefix('IC-')}_")


@pytest.mark.parametrize(
    ("where", "field"), _frozen_fields(), ids=lambda x: x if isinstance(x, str) else ""
)
def test_a_frozen_pointer_resolves_with_its_type_and_unit(
    where: str, field: FrozenContractField
) -> None:
    committed = json.loads(schema_path(field.schema_stem, field.version).read_text("utf-8"))
    live = _CURRENT[field.schema_stem][0].model_json_schema()
    for schema in (committed, live):
        for p in field.pointers:
            leaves = schema_leaves(schema, p.pointer)
            assert leaves, f"{where}: {p.pointer} resolves to nothing"
            types = {leaf.get("type") for leaf in leaves}
            assert p.json_type in types, f"{where}: {p.pointer} is {types}"
            last = p.pointer.rsplit("/", 1)[-1]
            if p.json_type in ("integer", "number"):
                if isinstance(field.unit_suffix, UnitFromQuantity):
                    # IC-14 names a value `value`: its unit is its point's quantity's, in W.
                    unit = field.unit_suffix
                    assert field.contract_id == "IC-14" and last == "value", where
                    spec = telemetry_series.QUANTITY_TABLE[unit.quantity]
                    assert spec.unit.value == unit.unit == "W", where
                    continue
                assert isinstance(field.unit_suffix, str), f"{where}: a power field states its unit"
                assert last == field.unit_suffix[1:] or last.endswith(field.unit_suffix), where


#: The IC-14 re-binding (the TEL-01 author's table): point pattern, sign, split, rounding.
IC14_BINDINGS: dict[str, tuple[str, str, str, str]] = {
    "a.asset_output_kw": (
        "{genset,pcs,pv}.<id>.active_power.{operational_meter,revenue_meter}.whole",
        "negated",
        "-",
        "floor",
    ),
    "a.boundary_export_kw": (
        "site_boundary.<id>.active_power.revenue_meter.whole",
        "as_measured",
        "export",
        "ceil",
    ),
    "a.boundary_import_kw": (
        "site_boundary.<id>.active_power.revenue_meter.whole",
        "as_measured",
        "import",
        "floor",
    ),
    "b.instructed_kw": (
        "pcs.<id>.active_power.setpoint_echo.whole",
        "as_measured",
        "-",
        "away_from_zero",
    ),
    "b.pcs_ac_kw": (
        "pcs.<id>.active_power.{device_reported,operational_meter}.whole",
        "negated",
        "-",
        "floor",
    ),
    "c.asset_kw": (
        "{chiller,crah,it_load,ups}.<id>.active_power.operational_meter.whole",
        "as_measured",
        "-",
        "ceil",
    ),
    "c.net_import_kw": (
        "site_boundary.<id>.active_power.revenue_meter.whole",
        "as_measured",
        "-",
        "ceil",
    ),
}


def test_every_ic14_term_reads_its_stated_points_sign_split_and_rounding() -> None:
    seen: dict[str, tuple[str, str, str, str]] = {}
    for _, term_id, b in _all():
        if isinstance(b, FrozenContractField) and b.contract_id == "IC-14":
            u = b.unit_suffix
            assert isinstance(u, UnitFromQuantity)
            split = u.split if isinstance(u.split, str) else "-"
            seen[term_id] = (point_pattern(u), u.sign, split, u.rounding)
            assert u.resolutions == ("settlement_30min", "standard_1min")
            for key in (
                f"{k.value}.x1.{u.quantity.value}.{r.value}.{u.aspect.value}"
                for k in u.asset_kinds
                for r in u.roles
            ):
                telemetry_series.parse_point_key(key)  # a valid IC-14 point name
    assert seen == IC14_BINDINGS


@pytest.mark.parametrize(
    ("term_id", "value_w", "kw"),
    [
        ("a.boundary_import_kw", 1_500, 1),
        ("a.boundary_import_kw", -1_500, 0),
        ("a.boundary_export_kw", -1_500, 2),
        ("a.boundary_export_kw", 1_500, 0),
        ("a.asset_output_kw", -2_500, 2),
        ("b.pcs_ac_kw", -999, 0),
        ("b.pcs_ac_kw", 1_000, -1),
        ("b.instructed_kw", -1, -1),
        ("b.instructed_kw", 1, 1),
        ("b.instructed_kw", 0, 0),
        ("c.net_import_kw", 1_001, 2),
        ("c.asset_kw", 999, 1),
    ],
)
def test_an_ic14_value_becomes_the_terms_kw_as_bound(term_id: str, value_w: int, kw: int) -> None:
    b = next(b for _, t, b in _all() if t == term_id)
    assert isinstance(b, FrozenContractField) and isinstance(b.unit_suffix, UnitFromQuantity)
    assert ic14_kw(b.unit_suffix, value_w) == kw


def test_a_unit_from_quantity_refuses_a_unit_or_aspect_its_quantity_does_not_have() -> None:
    b = next(b for _, t, b in _all() if t == "c.asset_kw")
    assert isinstance(b, FrozenContractField) and isinstance(b.unit_suffix, UnitFromQuantity)
    base = b.unit_suffix.model_dump()
    with pytest.raises(ValueError):
        UnitFromQuantity.model_validate({**base, "unit": "Wh"})
    with pytest.raises(ValueError, match="no supply aspect"):
        UnitFromQuantity.model_validate({**base, "aspect": telemetry_series.Aspect.SUPPLY})
    with pytest.raises(ValueError, match="ordered"):
        kinds = (telemetry_series.AssetKind.UPS, telemetry_series.AssetKind.CHILLER)
        UnitFromQuantity.model_validate({**base, "asset_kinds": kinds})


def test_the_pointer_resolver_finds_nothing_for_a_wrong_pointer() -> None:
    schema = json.loads(
        (SCHEMA_DIR / "ic1_connection_agreement.v1.1.0.schema.json").read_text("utf-8")
    )
    assert schema_leaves(schema, "/firm_import_mw")
    assert schema_leaves(schema, "/firm_import_kw") == []


# =============================================================================================
# Dossier fields resolve in the dossier's schema
# =============================================================================================


def test_every_dossier_binding_resolves_under_the_evidenced_value() -> None:
    schema = CandidateSiteDossier.model_json_schema()
    found = 0
    for where, _, b in _all():
        if not isinstance(b, DossierFieldBinding):
            continue
        pointer = f"/sites/*/{b.field.value}/value{b.value_path}"
        leaves = schema_leaves(schema, pointer)
        assert leaves, f"{where}: {pointer}"
        assert isinstance(b.via, FrozenContractField)
        assert b.via.pointers[0].json_type in {leaf.get("type") for leaf in leaves}
        assert b.value_path.endswith(b.via.pointers[0].pointer)
        found += 1
    assert found == 2


# =============================================================================================
# "Not yet measurable": the product and contract that will measure it, from the pinned roadmap
# =============================================================================================


@pytest.mark.parametrize(("where", "b"), _not_yet(), ids=lambda x: x if isinstance(x, str) else "")
def test_a_not_yet_measurable_term_names_the_roadmap_product_that_will_measure_it(
    where: str, b: NotYetMeasurable
) -> None:
    wps = [roadmap_wp(w) for w in b.roadmap_wps]
    assert isinstance(b.data_product, str), where
    assert any(f"`{b.data_product}`" in w.delivers for w in wps), (
        f"{where}: no listed work package releases `{b.data_product}`"
    )
    if isinstance(b.contract_id, str):
        assert any(
            w.delivers.startswith("Freezes") and b.contract_id in w.spec_refs for w in wps
        ), f"{where}: no listed work package freezes {b.contract_id}"
        assert isinstance(b.expected_schema_prefix, str)
        assert b.expected_schema_prefix == f"ic{b.contract_id.split('-')[-1]}_"
    else:
        assert b.category == "ledger_field"


def _atlas_roadmap(rev: str) -> bytes:
    if not (ATLAS / ".git").exists():
        pytest.skip("the atlas repository is not beside this checkout")
    out = subprocess.run(
        ["git", "-C", str(ATLAS), "show", f"{rev}:{ROADMAP_PIN.path}"],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        pytest.skip(f"atlas has no {rev}:{ROADMAP_PIN.path}")
    return out.stdout


def _wps(data: bytes) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(data)
    return {w["id"]: w for w in doc["work_packages"]}


def test_the_roadmap_pin_is_verbatim_at_its_commit() -> None:
    data = _atlas_roadmap(ROADMAP_PIN.atlas_commit)
    assert hashlib.sha256(data).hexdigest() == ROADMAP_PIN.file_sha256
    wps = _wps(data)
    for w in ROADMAP_PIN.work_packages:
        src = wps[w.id]
        assert src["title"] == w.title, w.id
        assert src["delivers"] == w.delivers, w.id
        assert tuple(src.get("spec_refs", ())) == w.spec_refs, w.id


def test_the_pinned_products_and_contracts_still_stand_at_atlas_head() -> None:
    """Drift check: if the roadmap renames a product or moves a freeze, re-pin and re-bind."""
    wps = _wps(_atlas_roadmap("HEAD"))
    for w in ROADMAP_PIN.work_packages:
        assert w.id in wps, f"{w.id} left the roadmap"
        head = wps[w.id]["delivers"]
        named = w.delivers.split("`")[1] if "`" in w.delivers else None
        if named is not None:
            assert f"`{named}`" in head, f"{w.id} no longer releases `{named}`"
        else:
            assert head.startswith("Freezes"), f"{w.id} no longer freezes {w.spec_refs}"


def test_tripwire_no_awaited_contract_or_product_exists_yet() -> None:
    """Fails the day IC-8, a named product, or an IC-14 version past 1.0.0 lands: re-bind the
    terms it measures. (IC-14 1.0.0 is frozen and bound; its two waiting terms need a new IC-14
    quantity or the meter registry, so a new IC-14 version is the moment to revisit them.)"""
    prefixes = {
        b.expected_schema_prefix for _, b in _not_yet() if isinstance(b.expected_schema_prefix, str)
    }
    assert prefixes == {"ic14_", "ic8_"}
    waiting_on_ic14 = sorted(t for t, b in _not_yet() if b.expected_schema_prefix == "ic14_")
    assert waiting_on_ic14 == [
        "bess_mw_actively_dispatched:b.asset_enveloped",
        "bess_mw_actively_dispatched:control_authority",
        "site_load_under_management:control_authority",
        "verified_flexible_mw:control_authority",
    ]
    assert telemetry_series.CONTRACT_VERSION == "1.0.0"
    assert {(s, v) for s, _, v in FROZEN if s.startswith("ic14_")} == {
        (f"ic14_{n}", "1.0.0")
        for n in ("series_descriptor", "telemetry_batch", "curated_series", "dq_annotation")
    }
    for stem, _, _ in FROZEN:
        assert not stem.startswith("ic8_"), stem
    for path in SCHEMA_DIR.iterdir():
        assert not path.name.startswith("ic8_"), path.name
        if path.name.startswith("ic14_"):
            assert ".v1.0.0." in path.name, path.name
    products = {b.data_product for _, b in _not_yet() if isinstance(b.data_product, str)}
    assert products == {
        "meter_standard_spec",
        "preregistered_baselines",
        "reconciliation_report",
        "telemetry_store",
        "verifier_dataset",
    }
    for product in products:
        assert importlib.util.find_spec(f"gridq.{product}") is None, product


# =============================================================================================
# Rendering: a not-yet-measurable term never shows a value
# =============================================================================================


def test_renders_say_not_yet_measurable_and_carry_no_value_for_those_terms() -> None:
    draft = render_draft_agreement()
    for did, term_id, b in _all():
        if not isinstance(b, NotYetMeasurable):
            continue
        lines = [x for x in draft.splitlines() if x.startswith(f"  - `{term_id}` ")]
        assert lines, (did, term_id)
        expected = (
            f"source: not yet measurable ({b.category}; {b.data_product}; "
            f"{', '.join(b.roadmap_wps)})."
        )
        assert all(x.endswith(expected) for x in lines), (did, term_id)
