"""IC-12 (NEW-IC-12): the GridQ chassis, now at 3.2.0, release ``gridq_chassis_v3`` (WP-UW-07).

Conformance for the three chassis models, `SiteModel`, `OptimisationResult` and `QuoteRecord`:

1. the committed 3.2.0 schemas match the models, and the 1.0.0, 2.0.0, 2.1.0, 3.0.0 and 3.1.0
   schemas are archived by hash;
2. 2.0.0 is 1.0.0 plus a stated set of optional members and nothing else; 2.1.0 (WP-UW-03, D79)
   is 2.0.0 plus a stated set of optional members and two stated descriptions; 3.0.0 (WP-UW-07,
   rendered on 2.1.0) is 2.1.0 plus a stated set of members, of which exactly two (the tariff
   basis and provenance) are required; 3.1.0 (WP-UW-17, D128 to D132) is 3.0.0 plus a stated set
   of optional members and nothing else; 3.2.0 (WP-SIZE-02, D178) is 3.1.0 plus the
   optional constraint-library stamp on `OptimisationResult` and nothing else;
3. **no identity moves except by the stated basis**: a 1.0.0 instance dumps to its 1.0.0 bytes,
   pinned by hashes minted at the ``gridq_clean_baseline`` tag; at 3.0.0 every committed site
   carries its basis and provenance, and with exactly those stripped it hashes to the same pin,
   while its 3.0.0 bytes are pinned beside it; `upgrade_site_2_to_3` lifts each 2.x payload back
   to its 3.0.0 bytes only with the basis stated;
4. a set IC-1 reference or IC-4 evidence link is a real input: it is dumped, it round-trips, it
   enters ``input_hash``, and it reaches the `QuoteRecord`;
5. the free-text connection field is commentary: the set of modules that read it is pinned;
6. the contract modules stay a leaf.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from _contracts_support import IC1_FIXTURES, IC4_FIXTURES, dumps, load
from pydantic import ValidationError

from gridq.assumptions import load_assumptions
from gridq.contracts import __main__ as cli
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.evidence import EvidenceObject, evidence_ref, resolve_ref
from gridq.governance.customer_data import CUSTOMERS, load_manifest
from gridq.ingestion.loaders import load_site_model_json
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import _canonical_run_input, _canonical_site_dump, assemble_quote_record
from gridq.schemas import (
    SCHEMA_VERSION,
    BankableStatus,
    ConnectionAgreementRef,
    ConnectionDetails,
    DataTier,
    EvidenceLink,
    ImplementationLevel,
    OptimisationResult,
    QuoteMode,
    QuoteRecord,
    SiteModel,
    SolverMetadata,
    SolverStatus,
    chassis,
)
from gridq.schemas.references import connection_agreement_ref, resolve_connection_agreement

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "gridq"
STEMS = ("ic12_site_model", "ic12_optimisation_result", "ic12_quote_record")


def _schema(stem: str, version: str) -> dict[str, Any]:
    text = (cli.SCHEMA_DIR / f"{stem}.v{version}.schema.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def schema_bytes(stem: str, version: str) -> bytes:
    return (cli.SCHEMA_DIR / f"{stem}.v{version}.schema.json").read_bytes()


def _site(**connection: Any) -> SiteModel:
    import sys

    sys.path.insert(0, str(REPO))
    try:
        from tests.conftest import _make_site
    finally:
        sys.path.remove(str(REPO))
    site = _make_site()
    if connection:
        site = site.model_copy(update={"connection": site.connection.model_copy(update=connection)})
    return site


def _result(**over: Any) -> OptimisationResult:
    return OptimisationResult(
        result_id="res_x",
        request_id="req_x",
        implementation_level=ImplementationLevel.PRODUCTION,
        bankable_status=BankableStatus.NOT_BANKABLE,
        solver_status=SolverStatus.OPTIMAL,
        solver_metadata=SolverMetadata(
            seed=0, assumption_version="gb_v0_1", code_version="c", model_version="m"
        ),
        **over,
    )


def _agreement() -> ConnectionAgreement:
    return ConnectionAgreement.model_validate_json(dumps(load(IC1_FIXTURES[0].name)))


def _evidence(index: int = 0) -> EvidenceObject:
    return EvidenceObject.model_validate_json(dumps(load(IC4_FIXTURES[index].name)))


def _link(supports: str, index: int = 0) -> EvidenceLink:
    return EvidenceLink(supports=supports, evidence=evidence_ref(_evidence(index)))


# ------------------------------------------------------------------ 1. version and schemas


def test_the_chassis_is_ic12_at_3_2_0() -> None:
    assert chassis.CONTRACT_ID == "IC-12"
    assert chassis.CONTRACT_VERSION == "3.2.0"
    assert chassis.SUPPORTED_VERSIONS == ("1.0.0", "2.0.0", "2.1.0", "3.0.0", "3.1.0", "3.2.0")
    assert chassis.SUPPORTED_VERSIONS[-1] == chassis.CONTRACT_VERSION
    assert chassis.GIT_TAG == "contracts/ic12-chassis/v3.2.0"
    assert chassis.RELEASE == "gridq_chassis_v3"
    assert [m for _, m in chassis.FROZEN_MODELS] == [SiteModel, OptimisationResult, QuoteRecord]
    live = {(stem, version) for stem, _, version in cli.FROZEN}
    assert {(stem, "3.2.0") for stem in STEMS} <= live


def test_the_contract_version_is_not_the_instance_schema_version() -> None:
    """``SCHEMA_VERSION`` enters every ``run_id``; freezing the chassis must not move it."""
    assert SCHEMA_VERSION == "0.1.0"
    assert _site().schema_version == "0.1.0"


def test_check_covers_the_three_chassis_schemas(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--check"]) == 0
    out = capsys.readouterr().out
    for stem in STEMS:
        assert f"ok    {stem}.v3.2.0.schema.json" in out
        assert f"ok    {stem}.v3.1.0.schema.json (archived, sha256 pinned)" in out
        assert f"ok    {stem}.v3.0.0.schema.json (archived, sha256 pinned)" in out
        assert f"ok    {stem}.v2.1.0.schema.json (archived, sha256 pinned)" in out
        assert f"ok    {stem}.v2.0.0.schema.json (archived, sha256 pinned)" in out
        assert f"ok    {stem}.v1.0.0.schema.json (archived, sha256 pinned)" in out


def test_a_chassis_model_that_drifts_fails_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class SiteModelPlusOne(SiteModel):
        an_unversioned_field: int | None = None

    drifted = tuple(
        (stem, SiteModelPlusOne if stem == "ic12_site_model" else model, version)
        for stem, model, version in cli.FROZEN
    )
    monkeypatch.setattr(cli, "FROZEN", drifted)
    assert cli.main(["--check"]) == 1
    assert "DRIFT: SiteModelPlusOne no longer matches ic12_site_model.v3.2.0" in (
        capsys.readouterr().out
    )


# ------------------------------------------------------------------ 2. the 1.0.0 -> 2.0.0 delta

#: Everything 2.0.0 adds, per schema: new top-level properties, new definitions, and the
#: definitions whose own properties grew. Nothing else may differ.
DELTA: dict[str, dict[str, Any]] = {
    "ic12_site_model": {
        "properties": {"evidence_refs"},
        "defs": {"ConnectionAgreementRef", "EvidenceLink", "EvidenceRef"},
        "grown": {"ConnectionDetails": {"agreement"}},
    },
    "ic12_optimisation_result": {
        "properties": {"connection_agreement", "evidence_refs"},
        "defs": {"ConnectionAgreementRef", "EvidenceLink", "EvidenceRef"},
        "grown": {},
    },
    "ic12_quote_record": {
        "properties": {"connection_agreement", "evidence_refs"},
        "defs": {"ConnectionAgreementRef", "EvidenceLink", "EvidenceRef"},
        "grown": {"OptimisationResult": {"connection_agreement", "evidence_refs"}},
    },
}


@pytest.mark.parametrize("stem", STEMS)
def test_2_0_0_is_1_0_0_plus_the_stated_optional_members(stem: str) -> None:
    old, new, delta = _schema(stem, "1.0.0"), _schema(stem, "2.0.0"), DELTA[stem]
    assert set(new["properties"]) - set(old["properties"]) == delta["properties"]
    assert set(old["properties"]) <= set(new["properties"])
    for name, prop in old["properties"].items():
        assert new["properties"][name] == prop, name
    # Nothing became required, so every 1.0.0 payload is a 2.0.0 payload.
    assert new["required"] == old["required"]
    assert set(new["$defs"]) - set(old["$defs"]) == delta["defs"]
    for name, definition in old["$defs"].items():
        grown = delta["grown"].get(name, set())
        after = new["$defs"][name]
        if not grown:
            assert after == definition, name
            continue
        assert set(after["properties"]) - set(definition["properties"]) == grown, name
        assert after.get("required") == definition.get("required"), name
        for prop_name, prop in definition["properties"].items():
            assert after["properties"][prop_name] == prop, (name, prop_name)
    # Every added member admits null and defaults to it: optional, and unset on old payloads.
    added = [new["properties"][p] for p in delta["properties"]]
    added += [new["$defs"][d]["properties"][p] for d, ps in delta["grown"].items() for p in ps]
    for prop in added:
        assert prop["default"] is None
        assert {"type": "null"} in prop["anyOf"]


@pytest.mark.parametrize("stem", STEMS)
def test_a_1_0_0_reader_refuses_a_payload_that_sets_a_2_0_0_member(stem: str) -> None:
    """A 1.0.0 reader could not honour the reference, so it must not accept it silently."""
    old = _schema(stem, "1.0.0")
    assert old["additionalProperties"] is False
    for name in DELTA[stem]["properties"]:
        assert name not in old["properties"]
    for definition, members in DELTA[stem]["grown"].items():
        assert old["$defs"][definition]["additionalProperties"] is False
        assert not members & set(old["$defs"][definition]["properties"])


def test_the_references_are_the_contracts_own_types_by_the_1x_range() -> None:
    """The chassis embeds IC-1 and IC-4 references as frozen there, not copies of them."""
    from gridq.contracts._base import VERSION_1X_PATTERN

    versions = ("2.0.0", "2.1.0", "3.0.0", "3.1.0", "3.2.0")
    for stem, version in ((s, v) for s in STEMS for v in versions):
        defs = _schema(stem, version)["$defs"]
        assert defs["ConnectionAgreementRef"]["properties"]["contract"]["const"] == "IC-1"
        assert defs["EvidenceRef"]["properties"]["contract"]["const"] == "IC-4"
        for ref in ("ConnectionAgreementRef", "EvidenceRef"):
            assert defs[ref]["properties"]["contract_version"]["pattern"] == VERSION_1X_PATTERN


# ------------------------------------------------------------------ 2b. the 2.0.0 -> 2.1.0 delta

#: Everything 2.1.0 adds (WP-UW-03, D75 to D77; released as D79). Only `QuoteRecord` moved, and
#: only through the `FinancialCase` it embeds: new definitions, the definitions whose own
#: properties grew, and the definitions whose description was reworded. Nothing else may differ.
DELTA_V210: dict[str, dict[str, Any]] = {
    "ic12_site_model": {"defs": set(), "grown": {}, "described": set()},
    "ic12_optimisation_result": {"defs": set(), "grown": {}, "described": set()},
    "ic12_quote_record": {
        "defs": {
            "AmortisationProfile",
            "CalculationPeriod",
            "CashflowCase",
            "DscrCovenantTerms",
            "DscrStatus",
            "LenderTerms",
            "LenderTermsProvenance",
            "LenderTermsSource",
        },
        "grown": {
            "CovenantStatus": {"lock_up_dscr", "lock_up_passes", "not_evaluated_reason", "source"},
            "DebtAssumptions": {"principal_gbp", "sized_by"},
            "DscrSummary": {"cashflow_case", "status", "unavailable_reason"},
            "FinancialCase": {"lender_terms"},
        },
        "described": {"CovenantStatus", "DscrSummary"},
    },
}


@pytest.mark.parametrize("stem", STEMS)
def test_2_1_0_is_2_0_0_plus_the_stated_optional_members(stem: str) -> None:
    old, new, delta = _schema(stem, "2.0.0"), _schema(stem, "2.1.0"), DELTA_V210[stem]
    if not (delta["defs"] or delta["grown"] or delta["described"]):
        # Unchanged models: the 2.1.0 file is the archived 2.0.0 file, byte for byte.
        assert schema_bytes(stem, "2.1.0") == schema_bytes(stem, "2.0.0")
        return
    # The top level is untouched: no property added, removed or changed, nothing new required.
    assert {k: v for k, v in new.items() if k != "$defs"} == {
        k: v for k, v in old.items() if k != "$defs"
    }
    assert set(old["$defs"]) <= set(new["$defs"])
    assert set(new["$defs"]) - set(old["$defs"]) == delta["defs"]
    for name, definition in old["$defs"].items():
        after, grown = new["$defs"][name], delta["grown"].get(name, set())
        if name not in delta["grown"] and name not in delta["described"]:
            assert after == definition, name
            continue
        # Exactly `properties` (by addition) and, where stated, `description` differ.
        moved = {k for k in set(definition) | set(after) if definition.get(k) != after.get(k)}
        stated = ({"properties"} if grown else set()) | (
            {"description"} if name in delta["described"] else set()
        )
        assert moved == stated, (name, moved)
        assert after.get("required") == definition.get("required"), name
        assert set(after.get("properties", {})) - set(definition.get("properties", {})) == grown
        for prop_name, prop in definition.get("properties", {}).items():
            assert after["properties"][prop_name] == prop, (name, prop_name)
        for prop_name in grown:
            # Optional and unset on every 2.0.0 payload: admits null and defaults to it.
            added = after["properties"][prop_name]
            assert added["default"] is None, (name, prop_name)
            assert {"type": "null"} in added["anyOf"], (name, prop_name)


def test_a_2_0_0_reader_refuses_a_payload_that_sets_a_2_1_0_member() -> None:
    """Every grown definition was closed at 2.0.0, so a 2.0.0 reader cannot misread a 2.1.0 one."""
    old = _schema("ic12_quote_record", "2.0.0")
    for definition, members in DELTA_V210["ic12_quote_record"]["grown"].items():
        assert old["$defs"][definition]["additionalProperties"] is False, definition
        assert not members & set(old["$defs"][definition]["properties"]), definition


# ------------------------------------------------------------------ 2c. the 2.1.0 -> 3.0.0 delta

#: Everything 3.0.0 adds to 2.1.0 (WP-UW-07, D103 to D106; rendered on 2.1.0 at the
#: integration merge): new definitions, the definitions whose own properties grew, and the members
#: that became required. Nothing else may differ, so every 2.1.0 member is carried.
DELTA_V300: dict[str, dict[str, Any]] = {
    "ic12_site_model": {
        "defs": {"BandWindows", "CdcmTariff", "EdcmTariff", "TariffBasis", "TariffProvenance"},
        "grown": {
            "TariffStack": {
                "basis",
                "evidence",
                "import_rate_windows",
                "network_tariff",
                "provenance",
            }
        },
        "required": {"TariffStack": {"basis", "provenance"}},
    },
    "ic12_optimisation_result": {"defs": set(), "grown": {}, "required": {}},
    "ic12_quote_record": {
        "defs": set(),
        "grown": {"CustomerBill": {"reactive_after_gbp", "reactive_before_gbp"}},
        "required": {},
    },
}


@pytest.mark.parametrize("stem", STEMS)
def test_3_0_0_is_2_1_0_plus_the_stated_members(stem: str) -> None:
    old, new, delta = _schema(stem, "2.1.0"), _schema(stem, "3.0.0"), DELTA_V300[stem]
    if not (delta["defs"] or delta["grown"]):
        # Unchanged model: the 3.0.0 file is the archived 2.1.0 file, byte for byte.
        assert schema_bytes(stem, "3.0.0") == schema_bytes(stem, "2.1.0")
        return
    # The top level is untouched: no property added, removed or changed, nothing new required.
    assert {k: v for k, v in new.items() if k != "$defs"} == {
        k: v for k, v in old.items() if k != "$defs"
    }
    assert set(old["$defs"]) <= set(new["$defs"])
    assert set(new["$defs"]) - set(old["$defs"]) == delta["defs"]
    for name, definition in old["$defs"].items():
        after, grown = new["$defs"][name], delta["grown"].get(name, set())
        if not grown:
            assert after == definition, name
            continue
        made_required = delta["required"].get(name, set())
        moved = {k for k in set(definition) | set(after) if definition.get(k) != after.get(k)}
        assert moved == ({"properties"} | ({"required"} if made_required else set())), name
        assert set(after.get("required", [])) - set(definition.get("required", [])) == (
            made_required
        )
        assert set(after["properties"]) - set(definition["properties"]) == grown, name
        for prop_name, prop in definition["properties"].items():
            assert after["properties"][prop_name] == prop, (name, prop_name)
        for prop_name in grown - made_required:
            # Optional and unset on every 2.1.0 payload: admits null and defaults to it.
            added = after["properties"][prop_name]
            assert added["default"] is None, (name, prop_name)
            assert {"type": "null"} in added["anyOf"], (name, prop_name)
        for prop_name in made_required:
            # Required, with no default and no null: a 2.x site is refused, never guessed.
            added = after["properties"][prop_name]
            assert "default" not in added and "anyOf" not in added, (name, prop_name)


@pytest.mark.parametrize("stem", ("ic12_site_model", "ic12_quote_record"))
def test_a_2_1_0_reader_refuses_a_payload_that_sets_a_3_0_0_member(stem: str) -> None:
    """Every grown definition was closed at 2.1.0, so a 2.1.0 reader cannot misread a 3.0.0 one."""
    old = _schema(stem, "2.1.0")
    for definition, members in DELTA_V300[stem]["grown"].items():
        assert old["$defs"][definition]["additionalProperties"] is False, definition
        assert not members & set(old["$defs"][definition]["properties"]), definition


def test_one_upgrader_serves_both_2_x_site_schemas() -> None:
    """2.1.0 moved only `QuoteRecord`: its site schema is the 2.0.0 file, byte for byte, so a 2.1.0
    site payload is a 2.0.0 one and `upgrade_site_2_to_3` lifts either. A 2.1.0 `QuoteRecord` and
    `OptimisationResult` need no upgrader: each 3.0.0 schema is the 2.1.0 one plus optional
    members (`test_3_0_0_is_2_1_0_plus_the_stated_members`)."""
    assert schema_bytes("ic12_site_model", "2.1.0") == schema_bytes("ic12_site_model", "2.0.0")
    assert "2.1.0" in chassis.SUPPORTED_VERSIONS and "2.0.0" in chassis.SUPPORTED_VERSIONS


# ------------------------------------------------------------------ 2d. the 3.0.0 -> 3.1.0 delta

#: Everything 3.1.0 adds to 3.0.0 (WP-UW-17, D128 to D132; released as 2.2.0 on its branch,
#: rendered on 3.0.0 at the integration merge). Only `QuoteRecord` moved, through the
#: `FinancialCase` it embeds (the lifecycle inputs and record) and its `PriceRecommendation` (the
#: levelisation members). No description was reworded and no existing member changed.
DELTA_V310: dict[str, dict[str, Any]] = {
    "ic12_site_model": {"defs": set(), "grown": {}, "described": set()},
    "ic12_optimisation_result": {"defs": set(), "grown": {}, "described": set()},
    "ic12_quote_record": {
        "defs": {
            "CapacityAgreementIndexation",
            "CashflowLineBasis",
            "CmAuctionType",
            "ConstructionPhase",
            "ConstructionRecord",
            "ConstructionStatus",
            "DatedFlowEntry",
            "Decommissioning",
            "DrawdownPoint",
            "IndexationRecord",
            "LifecycleFlow",
            "LifecycleFlowEntry",
            "LifecycleFlowStatus",
            "LifecycleInputs",
            "LifecycleProvenance",
            "LifecycleRecord",
            "LifecycleSource",
            "LineBasisEntry",
            "PriceBasis",
            "Replacement",
            "ReplacementComponent",
            "ReturnBasis",
            "TerminalValue",
            "TerminalValueBasis",
        },
        "grown": {
            "FinancialCase": {"lifecycle", "lifecycle_inputs"},
            "PriceRecommendation": {
                "equity_closed_loop_gbp_per_kwh",
                "levelisation_basis",
                "measured_irr_with_price_pct",
            },
        },
        "described": set(),
    },
}


@pytest.mark.parametrize("stem", STEMS)
def test_3_1_0_is_3_0_0_plus_the_stated_optional_members(stem: str) -> None:
    old, new, delta = _schema(stem, "3.0.0"), _schema(stem, "3.1.0"), DELTA_V310[stem]
    if not (delta["defs"] or delta["grown"] or delta["described"]):
        assert schema_bytes(stem, "3.1.0") == schema_bytes(stem, "3.0.0")
        return
    assert {k: v for k, v in new.items() if k != "$defs"} == {
        k: v for k, v in old.items() if k != "$defs"
    }
    assert set(old["$defs"]) <= set(new["$defs"])
    assert set(new["$defs"]) - set(old["$defs"]) == delta["defs"]
    for name, definition in old["$defs"].items():
        after, grown = new["$defs"][name], delta["grown"].get(name, set())
        if not grown:
            assert after == definition, name
            continue
        moved = {k for k in set(definition) | set(after) if definition.get(k) != after.get(k)}
        assert moved == {"properties"}, (name, moved)
        assert after.get("required") == definition.get("required"), name
        assert set(after["properties"]) - set(definition["properties"]) == grown
        for prop_name, prop in definition["properties"].items():
            assert after["properties"][prop_name] == prop, (name, prop_name)
        for prop_name in grown:
            added = after["properties"][prop_name]
            assert added["default"] is None, (name, prop_name)
            assert {"type": "null"} in added["anyOf"], (name, prop_name)


def test_a_3_0_0_reader_refuses_a_payload_that_sets_a_3_1_0_member() -> None:
    """Both grown definitions were closed at 3.0.0, so a 3.0.0 reader cannot misread a 3.1.0 one."""
    old = _schema("ic12_quote_record", "3.0.0")
    for definition, members in DELTA_V310["ic12_quote_record"]["grown"].items():
        assert old["$defs"][definition]["additionalProperties"] is False, definition
        assert not members & set(old["$defs"][definition]["properties"]), definition


# ------------------------------------------------------------------ 2e. the 3.1.0 -> 3.2.0 delta

#: Everything 3.2.0 adds to 3.1.0 (WP-SIZE-02, D178): one optional member,
#: ``constraint_library``, on `OptimisationResult`, and its one new definition. ``<root>`` is the
#: result schema's own top level; `QuoteRecord` moves only through the result it embeds.
DELTA_V320: dict[str, dict[str, Any]] = {
    "ic12_site_model": {"defs": set(), "grown": {}},
    "ic12_optimisation_result": {
        "defs": {"ConstraintLibraryRef"},
        "grown": {"<root>": {"constraint_library"}},
    },
    "ic12_quote_record": {
        "defs": {"ConstraintLibraryRef"},
        "grown": {"OptimisationResult": {"constraint_library"}},
    },
}


def _node(schema: dict[str, Any], name: str) -> dict[str, Any]:
    if name == "<root>":
        return {k: v for k, v in schema.items() if k != "$defs"}
    node: dict[str, Any] = schema["$defs"][name]
    return node


@pytest.mark.parametrize("stem", STEMS)
def test_3_2_0_is_3_1_0_plus_the_stated_optional_members(stem: str) -> None:
    old, new, delta = _schema(stem, "3.1.0"), _schema(stem, "3.2.0"), DELTA_V320[stem]
    if not (delta["defs"] or delta["grown"]):
        assert schema_bytes(stem, "3.2.0") == schema_bytes(stem, "3.1.0")
        return
    old_defs, new_defs = old.get("$defs", {}), new.get("$defs", {})
    assert set(new_defs) - set(old_defs) == delta["defs"]
    assert set(old_defs) <= set(new_defs)
    for name in ["<root>", *old_defs]:
        before, after = _node(old, name), _node(new, name)
        grown = delta["grown"].get(name, set())
        if not grown:
            assert after == before, name
            continue
        moved = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        assert moved == {"properties"}, (name, moved)
        assert after.get("required") == before.get("required"), name
        assert set(after["properties"]) - set(before["properties"]) == grown
        for prop_name, prop in before["properties"].items():
            assert after["properties"][prop_name] == prop, (name, prop_name)
        for prop_name in grown:
            added = after["properties"][prop_name]
            assert added["default"] is None, (name, prop_name)
            assert {"type": "null"} in added["anyOf"], (name, prop_name)
            assert {"$ref": "#/$defs/ConstraintLibraryRef"} in added["anyOf"], (name, prop_name)


def test_the_3_2_0_stamp_is_one_closed_definition_mirroring_the_ic3_pin() -> None:
    """The stamp is the same closed definition in both files and restates IC-3's pin fields, so an
    envelope exporter copies it into ``source.constraint_library`` without translation."""
    from gridq.contracts.dispatch_envelope import ConstraintLibraryPin

    result_def = _schema("ic12_optimisation_result", "3.2.0")["$defs"]["ConstraintLibraryRef"]
    quote_def = _schema("ic12_quote_record", "3.2.0")["$defs"]["ConstraintLibraryRef"]
    assert result_def == quote_def
    assert result_def["additionalProperties"] is False
    assert set(result_def["required"]) == {
        "library_id",
        "library_version",
        "library_hash",
        "constraint_ids",
    }
    pin = ConstraintLibraryPin.model_json_schema()
    for member in ("library_version", "constraint_ids"):
        assert member in pin["properties"], member
    assert result_def["properties"]["constraint_ids"]["items"]["pattern"] == "^[a-z][a-z0-9_]*$"


def test_a_3_1_0_reader_refuses_a_payload_that_sets_the_3_2_0_stamp() -> None:
    """Both grown objects were closed at 3.1.0, so a 3.1.0 reader cannot silently drop the stamp."""
    result = _schema("ic12_optimisation_result", "3.1.0")
    assert result["additionalProperties"] is False
    assert "constraint_library" not in result["properties"]
    embedded = _schema("ic12_quote_record", "3.1.0")["$defs"]["OptimisationResult"]
    assert embedded["additionalProperties"] is False
    assert "constraint_library" not in embedded["properties"]


# ------------------------------------------------------------------ 3. no existing identity moves

#: Hashes minted on 2026-11-26 from the chassis models at commit bfeafe4, whose
#: ``src/gridq/schemas`` is byte-identical to the ``gridq_clean_baseline`` tag, before any
#: 2.0.0 field existed. A bare path is ``hash_json(_canonical_site_dump(site))``, the site half
#: of ``input_hash``; ``#full`` is the hash of the site's whole JSON dump less ``created_at``,
#: which is what every other hash site (finance ``case_id``, the schedule reference) reads.
#: These are not re-minted: a mismatch means an existing identity moved. At 3.0.0 they are what
#: a committed site hashes to with its basis and provenance (and a derived VAT basis's record)
#: stripped: the stated basis is the only thing 3.0.0 added to any of them.
#:
#: The public example sites, and five labelled SYNTHETIC sites under ``tests/fixtures/ic12_sites``
#: (WP-GRC-05). Between them they set every optional `SiteModel` member, every load-data,
#: tariff and connection option, and both the ``bare == #full`` and ``bare != #full`` cases, so
#: the identity check keeps the breadth it had on the campaign records without reading one. The
#: synthetic hashes were minted the same way, by running the bfeafe4 models over the fixtures.
BASELINE_SITE_HASHES = {
    "examples/demo_site/sitemodel.json": (
        "sha256:2ad70a7f7b60838f3b23dfca237e208e541e147dd98625d53a06be364250008f"
    ),
    "examples/demo_site/sitemodel.json#full": (
        "sha256:17db9af90b6881a89a88b0b8e4bc2ff74e94e622dc7419a0011bfd1758c6c97b"
    ),
    "examples/site_t0.json": (
        "sha256:99c990b8cd3054a8b39d608947b0ccf023861a78676ffc41fe5855cad77bbb20"
    ),
    "examples/site_t0.json#full": (
        "sha256:dbbee105568db2374861edb184d940931d864e7104a67a95c7faeca874d9cd88"
    ),
    "examples/site_t2.json": (
        "sha256:54611ef4a7ed087f5b341649fb51cc85cbd15139e821d344097cdb575114173e"
    ),
    "examples/site_t2.json#full": (
        "sha256:5c7acfe772cd44382aac2bd2a7f502b3efbe33bf7b6d464e651f6ba4c7d52fac"
    ),
    "tests/fixtures/ic12_sites/constrained_connection_blocked.synthetic.json": (
        "sha256:3d5385d2043b1e150e18cae18cce80984ccd9488e482a25ab2ad4312fa840a3b"
    ),
    "tests/fixtures/ic12_sites/constrained_connection_blocked.synthetic.json#full": (
        "sha256:7d00c4900b1615bc2c7ba3cc34594faec347b71ed5f3cad4fb91aca2cf68602b"
    ),
    "tests/fixtures/ic12_sites/invoice_anchored_modelled_shape.synthetic.json": (
        "sha256:14b700c8210745af79254f1772a4c7c6cd32caf253654f1ff313a99f3c3b4195"
    ),
    "tests/fixtures/ic12_sites/invoice_anchored_modelled_shape.synthetic.json#full": (
        "sha256:7912406db4feff39beb3a8287684500c870b5e4ac3d7ce8874fd296fcd43666b"
    ),
    "tests/fixtures/ic12_sites/metered_awarded_consented.synthetic.json": (
        "sha256:709e4ed25c8dc56cf21bbb8825b3d571e515d9c0de3cd6d1244776706f1a6f8b"
    ),
    "tests/fixtures/ic12_sites/metered_awarded_consented.synthetic.json#full": (
        "sha256:709e4ed25c8dc56cf21bbb8825b3d571e515d9c0de3cd6d1244776706f1a6f8b"
    ),
    "tests/fixtures/ic12_sites/private_wire_export.synthetic.json": (
        "sha256:4a2e448f7c6b700ae2c7e3ab7cd7d99c0f71a89acf43c8d746c247be629d8a99"
    ),
    "tests/fixtures/ic12_sites/private_wire_export.synthetic.json#full": (
        "sha256:4a2e448f7c6b700ae2c7e3ab7cd7d99c0f71a89acf43c8d746c247be629d8a99"
    ),
    "tests/fixtures/ic12_sites/sole_trader_ppa_export.synthetic.json": (
        "sha256:1216a0c214317f51134258330ef87835005dc46128e8ef1ca021f78cbb64a01b"
    ),
    "tests/fixtures/ic12_sites/sole_trader_ppa_export.synthetic.json#full": (
        "sha256:3a613773f09af90724b9644f76105e1fabd52cc8a1e04de89faf30a699e87553"
    ),
}

#: The campaign records, minted with the sites above and unchanged. They are quarantined customer
#: data (WP-GRC-05), so they are checked only through the guard: each skips, naming its blocker,
#: until the customer's DPA is registered, and then runs by itself.
QUARANTINED_SITE_HASHES = {
    "examples/real/sites/bishops_cleeve_ash_plant.json": (
        "sha256:a4244e4a4bca08c759afe94ec1b0a59b22ff8e36749a915cc20d28968cac26d9"
    ),
    "examples/real/sites/bishops_cleeve_ash_plant.json#full": (
        "sha256:a4244e4a4bca08c759afe94ec1b0a59b22ff8e36749a915cc20d28968cac26d9"
    ),
    "examples/real/sites/bishops_cleeve_mrf.json": (
        "sha256:608fb9a8dada7923a40012ba9a34a4c84310a9945864dc5c49d9468bea3bb461"
    ),
    "examples/real/sites/bishops_cleeve_mrf.json#full": (
        "sha256:608fb9a8dada7923a40012ba9a34a4c84310a9945864dc5c49d9468bea3bb461"
    ),
    "examples/real/sites/colnbrook.json": (
        "sha256:9521797e36a3de371d7e92425549b51ef99f3b807d3af921a76852be5d931f4b"
    ),
    "examples/real/sites/colnbrook.json#full": (
        "sha256:9521797e36a3de371d7e92425549b51ef99f3b807d3af921a76852be5d931f4b"
    ),
    "examples/real/sites/ewelme.json": (
        "sha256:26fc5621575a83931afefcf6cc27ac981c15554e429eeb4a0c71170346ff5744"
    ),
    "examples/real/sites/ewelme.json#full": (
        "sha256:26fc5621575a83931afefcf6cc27ac981c15554e429eeb4a0c71170346ff5744"
    ),
    "examples/real/sites/ewelme_invoice_anchored.json": (
        "sha256:be80d81eb3ffb533074d87027f96d6ef8ac30dd91a9463b2a9551654f3336980"
    ),
    "examples/real/sites/ewelme_invoice_anchored.json#full": (
        "sha256:be80d81eb3ffb533074d87027f96d6ef8ac30dd91a9463b2a9551654f3336980"
    ),
    "examples/real/sites/harwell.json": (
        "sha256:9bc2ae912b37ff8ef9a86d26eff0a4046da77fa12a3b4cabc5d1ab8600ca3679"
    ),
    "examples/real/sites/harwell.json#full": (
        "sha256:9bc2ae912b37ff8ef9a86d26eff0a4046da77fa12a3b4cabc5d1ab8600ca3679"
    ),
    "examples/real/sites/knowl_hill.json": (
        "sha256:2beca146bd6c04129507eb4ffb34c8a183a91c81061f2e0302db2f32d771f68d"
    ),
    "examples/real/sites/knowl_hill.json#full": (
        "sha256:2beca146bd6c04129507eb4ffb34c8a183a91c81061f2e0302db2f32d771f68d"
    ),
    "examples/real/sites/knowl_hill_invoice_anchored.json": (
        "sha256:d80efbdd438fd352a37eb039cefeba4375e0952ba8aad87fa00bcedf81f76040"
    ),
    "examples/real/sites/knowl_hill_invoice_anchored.json#full": (
        "sha256:d80efbdd438fd352a37eb039cefeba4375e0952ba8aad87fa00bcedf81f76040"
    ),
    "examples/real/sites/tanhouse.json": (
        "sha256:8528d3b91ab1bc87cff7ac9613a07d9f8f75abe719a9254e06d7686c0382acab"
    ),
    "examples/real/sites/tanhouse.json#full": (
        "sha256:8528d3b91ab1bc87cff7ac9613a07d9f8f75abe719a9254e06d7686c0382acab"
    ),
}

#: The 3.0.0 identities, minted 2026-11-26 (WP-UW-07) from the committed sites, each stating its
#: `TariffBasis` and `TariffProvenance`. Same key scheme as `BASELINE_SITE_HASHES`. Re-minted only
#: by a deliberate change to a committed site or to the chassis, stated in the changelog. The
#: public examples' pins are WP-UW-07's; the five synthetic sites' pins were minted at the
#: integration merge, when each fixture gained the synthetic test basis (`_tariff_support`).
V3_SITE_HASHES = {
    "examples/demo_site/sitemodel.json": (
        "sha256:eac69e0c26f8a05b0608a402c70ec4fa80a9efa45c1dc02d4349d0041aa77aee"
    ),
    "examples/demo_site/sitemodel.json#full": (
        "sha256:2fef5eb1705ceb8fd3713b362d1a4db3053a94ae24b4225607729af3e755ac37"
    ),
    "examples/site_t0.json": (
        "sha256:19797b97bd1265036fc6dbee1607a9478bc7a204284d46e7507c46781feac782"
    ),
    "examples/site_t0.json#full": (
        "sha256:3e61362fb424668412594d5a19f640373ae44ebc4bdb8e39fee4127756906911"
    ),
    "examples/site_t2.json": (
        "sha256:1d44041cf55ac96ee143369a6febd4ba077e5c97c6d8bbc0797f994792cc19b4"
    ),
    "examples/site_t2.json#full": (
        "sha256:4b853ad768fdf92e19cb97b910fed3b2e91818c8b2f838ba0548c7fc8e18f514"
    ),
    "tests/fixtures/ic12_sites/constrained_connection_blocked.synthetic.json": (
        "sha256:21b11b9cbe915747de182de9e59ea37179ac76634229f28e1cb21ef288854fa6"
    ),
    "tests/fixtures/ic12_sites/constrained_connection_blocked.synthetic.json#full": (
        "sha256:8d2ce73acac6a38088ddb9f94e6648d7013c8f69eacaaa0e16c4a12fa4e28d00"
    ),
    "tests/fixtures/ic12_sites/invoice_anchored_modelled_shape.synthetic.json": (
        "sha256:5ad535cde29e400287cc53e5f03e1ac238b8303c266324c346fcdaa3e0e95065"
    ),
    "tests/fixtures/ic12_sites/invoice_anchored_modelled_shape.synthetic.json#full": (
        "sha256:14e608c4d4a21ccf40a7639a7445a21b41dc790b9bc25bf3e3b953ce3dbd6d1f"
    ),
    "tests/fixtures/ic12_sites/metered_awarded_consented.synthetic.json": (
        "sha256:967958ef6619bacde2065a1cb319adc9306d48f1a247efa85c60b742ef414408"
    ),
    "tests/fixtures/ic12_sites/metered_awarded_consented.synthetic.json#full": (
        "sha256:967958ef6619bacde2065a1cb319adc9306d48f1a247efa85c60b742ef414408"
    ),
    "tests/fixtures/ic12_sites/private_wire_export.synthetic.json": (
        "sha256:a8fe94faccdc0277c6284bd47c9535ba200aee313e955d596db51455b4cc8ed9"
    ),
    "tests/fixtures/ic12_sites/private_wire_export.synthetic.json#full": (
        "sha256:a8fe94faccdc0277c6284bd47c9535ba200aee313e955d596db51455b4cc8ed9"
    ),
    "tests/fixtures/ic12_sites/sole_trader_ppa_export.synthetic.json": (
        "sha256:2f2f3d40669c77ae8d52e3be2aca3ad60caba9026491e454296b8ad0946e4d2f"
    ),
    "tests/fixtures/ic12_sites/sole_trader_ppa_export.synthetic.json#full": (
        "sha256:00459e500b0638aca770c3268d81f94565b480948c27a51d466d86faff77e4a9"
    ),
}

#: The campaign records' 3.0.0 identities, minted by WP-UW-07 beside their 2.0.0 pins. Checked only
#: through the DPA guard, as `QUARANTINED_SITE_HASHES` is.
V3_QUARANTINED_SITE_HASHES = {
    "examples/real/sites/bishops_cleeve_ash_plant.json": (
        "sha256:92f3a98603ed17450859c22fe3959b16c29f93541ac76c0399b119bf3d10f3ec"
    ),
    "examples/real/sites/bishops_cleeve_ash_plant.json#full": (
        "sha256:92f3a98603ed17450859c22fe3959b16c29f93541ac76c0399b119bf3d10f3ec"
    ),
    "examples/real/sites/bishops_cleeve_mrf.json": (
        "sha256:898323b1c152da35011356d374b481ba67a6f058822af8d26fede8288a8f6d37"
    ),
    "examples/real/sites/bishops_cleeve_mrf.json#full": (
        "sha256:898323b1c152da35011356d374b481ba67a6f058822af8d26fede8288a8f6d37"
    ),
    "examples/real/sites/colnbrook.json": (
        "sha256:2a0304eec1a541ef197f451972a6a97d09dfbdda2ff6e22d955405543e1f5591"
    ),
    "examples/real/sites/colnbrook.json#full": (
        "sha256:2a0304eec1a541ef197f451972a6a97d09dfbdda2ff6e22d955405543e1f5591"
    ),
    "examples/real/sites/ewelme.json": (
        "sha256:32112d6a61c4074c02c84826bba5f23649202dea43963003e16acd8d705afc12"
    ),
    "examples/real/sites/ewelme.json#full": (
        "sha256:32112d6a61c4074c02c84826bba5f23649202dea43963003e16acd8d705afc12"
    ),
    "examples/real/sites/ewelme_invoice_anchored.json": (
        "sha256:697ed2dcd3cda8743f890f0ba17fd7af8768d2c9d9a254ea40b34a38d9fdddc7"
    ),
    "examples/real/sites/ewelme_invoice_anchored.json#full": (
        "sha256:697ed2dcd3cda8743f890f0ba17fd7af8768d2c9d9a254ea40b34a38d9fdddc7"
    ),
    "examples/real/sites/harwell.json": (
        "sha256:97b1a7b5c5fe30fea7c513c8efc8f9b66f077063862154eb7a689e652bcd1f86"
    ),
    "examples/real/sites/harwell.json#full": (
        "sha256:97b1a7b5c5fe30fea7c513c8efc8f9b66f077063862154eb7a689e652bcd1f86"
    ),
    "examples/real/sites/knowl_hill.json": (
        "sha256:f111d0739b262c44e2cd65cc76e42e40045dd0c95f4bbf8f5a169e3c32a89d53"
    ),
    "examples/real/sites/knowl_hill.json#full": (
        "sha256:f111d0739b262c44e2cd65cc76e42e40045dd0c95f4bbf8f5a169e3c32a89d53"
    ),
    "examples/real/sites/knowl_hill_invoice_anchored.json": (
        "sha256:00a87b78f55a2a961d2a5a413890c32b0803876a43d3f9ace233f6840d64edd0"
    ),
    "examples/real/sites/knowl_hill_invoice_anchored.json#full": (
        "sha256:00a87b78f55a2a961d2a5a413890c32b0803876a43d3f9ace233f6840d64edd0"
    ),
    "examples/real/sites/tanhouse.json": (
        "sha256:0b5123dbd32a2b06273c43ab9f12523b7874215522de0c8638daabef7b89b3b0"
    ),
    "examples/real/sites/tanhouse.json#full": (
        "sha256:0b5123dbd32a2b06273c43ab9f12523b7874215522de0c8638daabef7b89b3b0"
    ),
}

SYNTHETIC_SITES = REPO / "tests" / "fixtures" / "ic12_sites"

#: The same minting, for the `tests/conftest.py` site with a minimal result and ``gb_v0_1``:
#: the whole canonical run input, the result's full dump and a `QuoteRecord`'s full dump.
BASELINE_RUN_INPUT_HASH = "sha256:5baf3b5eefb64ec3dcb81aba7b218b0e8e86d66ec492900dcea47cc5265d1b71"
BASELINE_RESULT_DUMP_HASH = (
    "sha256:076c0bd866c25033160732eb75682215c9b03e5527cdf2934f9d29be426fefa3"
)
BASELINE_QUOTE_DUMP_HASH = "sha256:58a9024ed86d5dcf0d7decd0cb602b9fff931371ee4523be6fe9d2abb93ea5b5"
V3_RUN_INPUT_HASH = "sha256:d5bf80e3de615962df4effd406058818b6ab96f833b652866a20c0fe984d5f68"

#: The members 3.0.0 requires on every site, and the record a derived VAT basis carries.
_V3_REQUIRED = ("basis", "provenance")
_VAT_BASIS_KEY = "tariff_stack.basis.vat"


def _strip_to_2_x(dump: dict[str, Any]) -> dict[str, Any]:
    """A 3.0.0 site dump less exactly what 3.0.0 made it state, i.e. its 2.x payload."""
    old = json.loads(json.dumps(dump))
    for member in _V3_REQUIRED:
        del old["tariff_stack"][member]
    records = old.get("derived_assumptions")
    if records is not None:
        kept = [r for r in records if r["key"] != _VAT_BASIS_KEY]
        if kept:
            old["derived_assumptions"] = kept
        else:
            del old["derived_assumptions"]
    return old


def test_the_pinned_sites_are_every_committed_example_site() -> None:
    paths = ["examples/demo_site/sitemodel.json", "examples/site_t0.json", "examples/site_t2.json"]
    paths += sorted(p.relative_to(REPO).as_posix() for p in SYNTHETIC_SITES.glob("*.json"))
    assert sorted(k for k in BASELINE_SITE_HASHES if "#" not in k) == sorted(paths)
    # The campaign records are enumerated from the committed manifest, never from the directory.
    real = [e.path for e in load_manifest().datasets if e.path.startswith("examples/real/sites/")]
    assert sorted(k for k in QUARANTINED_SITE_HASHES if "#" not in k) == sorted(real)
    assert set(V3_SITE_HASHES) == set(BASELINE_SITE_HASHES)
    assert set(V3_QUARANTINED_SITE_HASHES) == set(QUARANTINED_SITE_HASHES)


def test_every_synthetic_site_is_labelled_and_names_no_customer() -> None:
    tokens = [t for c in CUSTOMERS.values() for t in c["tokens"]]
    for path in sorted(SYNTHETIC_SITES.glob("*.json")):
        assert path.name.endswith(".synthetic.json"), path.name
        site = load_site_model_json(path, run_cross_checks=False)
        assert site.site_id.startswith("SYNTHETIC-"), path.name
        text = path.read_text(encoding="utf-8").lower()
        assert not [t for t in tokens if t in text], path.name


def test_the_pinned_sites_set_every_optional_site_member() -> None:
    """The synthetic set exists to keep breadth: each optional member is set on some pinned site."""
    sites = [
        load_site_model_json(REPO / k, run_cross_checks=False)
        for k in BASELINE_SITE_HASHES
        if "#" not in k
    ]
    added_in_2_0_0 = {"evidence_refs"}
    optional = {
        name
        for name, field in SiteModel.model_fields.items()
        if not field.is_required() and name not in added_in_2_0_0 | {"created_at"}
    }
    unset = {name for name in optional if not any(getattr(s, name) for s in sites)}
    assert not unset, f"no pinned site sets {sorted(unset)}"
    bare_is_full = [
        BASELINE_SITE_HASHES[k] == BASELINE_SITE_HASHES[k + "#full"]
        for k in BASELINE_SITE_HASHES
        if "#" not in k
    ]
    assert any(bare_is_full) and not all(bare_is_full)


def _assert_identity(
    site: SiteModel, pins: dict[str, str], v3_pins: dict[str, str], path: str
) -> None:
    """The 2.x pin holds with the basis stripped; the 3.0.0 bytes are pinned beside it; and the
    upgrader lifts the stripped payload back to exactly those bytes, given the basis."""
    canonical = _canonical_site_dump(site)
    full = site.model_dump(mode="json", exclude={"created_at"})
    assert hash_json(_strip_to_2_x(canonical)) == pins[path]
    assert hash_json(_strip_to_2_x(full)) == pins[path + "#full"]
    assert hash_json(canonical) == v3_pins[path]
    assert hash_json(full) == v3_pins[path + "#full"]
    assert "evidence_refs" not in full and "agreement" not in full["connection"]
    for unset in ("network_tariff", "import_rate_windows", "evidence"):
        assert unset not in full["tariff_stack"], unset
    records = {r.key: r for r in site.derived_assumptions or ()}
    lifted = chassis.upgrade_site_2_to_3(
        _strip_to_2_x(full),
        basis=site.tariff_stack.basis,
        provenance=site.tariff_stack.provenance,
        vat_record=records.get(_VAT_BASIS_KEY),
    )
    assert (
        hash_json(lifted.model_dump(mode="json", exclude={"created_at"}))
        == (v3_pins[path + "#full"])
    )


@pytest.mark.parametrize("path", sorted(k for k in BASELINE_SITE_HASHES if "#" not in k))
def test_every_existing_site_moved_only_by_its_stated_basis(path: str) -> None:
    site = load_site_model_json(REPO / path, run_cross_checks=False)
    _assert_identity(site, BASELINE_SITE_HASHES, V3_SITE_HASHES, path)


@pytest.mark.parametrize("path", sorted(k for k in QUARANTINED_SITE_HASHES if "#" not in k))
def test_every_campaign_site_keeps_its_identity_once_its_dpa_is_registered(path: str) -> None:
    import sys

    sys.path.insert(0, str(REPO))
    try:
        from tests._dpa_support import dpa_released_site
    finally:
        sys.path.remove(str(REPO))
    site = dpa_released_site(REPO / path, run_cross_checks=False)
    _assert_identity(site, QUARANTINED_SITE_HASHES, V3_QUARANTINED_SITE_HASHES, path)


def test_a_2_x_site_is_refused_never_read_with_a_guessed_basis() -> None:
    full = _site().model_dump(mode="json")
    old = _strip_to_2_x(full)
    with pytest.raises(ValidationError, match="basis"):
        SiteModel.model_validate(old)
    stack = _site().tariff_stack
    with pytest.raises(ValueError, match="already states"):
        chassis.upgrade_site_2_to_3(full, basis=stack.basis, provenance=stack.provenance)
    derived = stack.basis.model_copy(update={"vat_attestation": "derived"})
    with pytest.raises(ValueError, match="needs the DerivedAssumption"):
        chassis.upgrade_site_2_to_3(old, basis=derived, provenance=stack.provenance)


def test_an_existing_run_input_result_and_record_keep_their_bytes() -> None:
    site, result, lib = _site(), _result(), load_assumptions("gb_v0_1")
    canonical = _canonical_run_input(site, result, None, lib, None)
    assert hash_json(canonical) == V3_RUN_INPUT_HASH
    # The run input moved by the site's stated basis and by nothing else.
    assert hash_json({**canonical, "site": _strip_to_2_x(canonical["site"])}) == (
        BASELINE_RUN_INPUT_HASH
    )
    result_dump = result.model_dump(mode="json", exclude={"created_at"})
    assert hash_json(result_dump) == BASELINE_RESULT_DUMP_HASH
    quote = QuoteRecord(
        quote_id="q",
        quote_mode=QuoteMode.DRAFT,
        site_id="site_test",
        optimisation_result=result,
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
    )
    quote_dump = quote.model_dump(
        mode="json", exclude={"created_at": True, "optimisation_result": {"created_at"}}
    )
    assert hash_json(quote_dump) == BASELINE_QUOTE_DUMP_HASH
    for dump in (result_dump, quote_dump, quote_dump["optimisation_result"]):
        assert "connection_agreement" not in dump and "evidence_refs" not in dump
        assert "constraint_library" not in dump  # 3.2.0: unset, so absent, so the bytes hold


def test_unset_members_are_absent_in_every_dump_mode() -> None:
    site, quote = (
        _site(),
        QuoteRecord(
            quote_id="q",
            quote_mode=QuoteMode.DRAFT,
            site_id="s",
            optimisation_result=_result(),
            data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        ),
    )
    for dump in (
        site.model_dump(),
        site.model_dump(mode="json"),
        json.loads(site.model_dump_json()),
    ):
        assert "evidence_refs" not in dump and "agreement" not in dump["connection"]
    for dump in (quote.model_dump(), json.loads(quote.model_dump_json())):
        assert "connection_agreement" not in dump and "evidence_refs" not in dump
        assert "connection_agreement" not in dump["optimisation_result"]
        assert "constraint_library" not in dump["optimisation_result"]
    # The attribute is still there, and still None: only the dump omits it.
    assert site.evidence_refs is None and site.connection.agreement is None
    assert SiteModel.model_validate(site.model_dump(mode="json")).evidence_refs is None


# ------------------------------------------------------------------ 4. a set reference is an input


def test_a_connection_agreement_reference_is_typed_dumped_and_resolved() -> None:
    agreement = _agreement()
    ref = connection_agreement_ref(agreement)
    assert ref.contract == "IC-1" and ref.agreement_hash == agreement.content_hash()
    assert ref.contract_version == agreement.contract_version
    site = _site(agreement=ref)
    dump = site.model_dump(mode="json")
    assert dump["connection"]["agreement"] == {
        "kind": "connection_agreement_ref",
        "contract": "IC-1",
        "contract_version": agreement.contract_version,
        "agreement_hash": agreement.content_hash(),
    }
    assert SiteModel.model_validate_json(site.model_dump_json()).connection.agreement == ref
    assert resolve_connection_agreement(ref, [agreement]) is agreement
    with pytest.raises(LookupError, match="no connection agreement has content hash"):
        resolve_connection_agreement(ref, [])
    stale = ref.model_copy(update={"contract_version": "1.9.0"})
    with pytest.raises(ValueError, match="the reference states contract_version 1.9.0"):
        resolve_connection_agreement(stale, [agreement])


@pytest.mark.parametrize(
    "bad",
    [
        "see the offer letter",
        {"kind": "connection_agreement_ref", "contract": "IC-1", "contract_version": "1.1.0"},
        {
            "kind": "connection_agreement_ref",
            "contract": "IC-4",
            "contract_version": "1.1.0",
            "agreement_hash": "sha256:" + "0" * 64,
        },
        {
            "kind": "connection_agreement_ref",
            "contract": "IC-1",
            "contract_version": "2.0.0",
            "agreement_hash": "sha256:" + "0" * 64,
        },
        {
            "kind": "connection_agreement_ref",
            "contract": "IC-1",
            "contract_version": "1.1.0",
            "agreement_hash": "not-a-hash",
        },
    ],
)
def test_free_text_or_a_malformed_reference_is_refused_in_the_typed_field(bad: Any) -> None:
    with pytest.raises(ValidationError):
        ConnectionDetails.model_validate({"mic_kw": 300.0, "agreement": bad})


def test_a_set_agreement_enters_the_site_identity_and_input_hash() -> None:
    lib, result = load_assumptions("gb_v0_1"), _result()
    plain, referenced = _site(), _site(agreement=connection_agreement_ref(_agreement()))
    assert hash_json(_canonical_site_dump(plain)) != hash_json(_canonical_site_dump(referenced))
    a = assemble_quote_record(plain, result, None, lib)
    b = assemble_quote_record(referenced, result, None, lib)
    assert a.input_hash != b.input_hash and a.quote_id != b.quote_id
    other = ConnectionAgreementRef(
        kind="connection_agreement_ref",
        contract="IC-1",
        contract_version="1.1.0",
        agreement_hash="sha256:" + "1" * 64,
    )
    c = assemble_quote_record(_site(agreement=other), result, None, lib)
    assert c.input_hash not in {a.input_hash, b.input_hash}


def test_evidence_links_are_ordered_deduplicated_and_enter_the_hash() -> None:
    first, second = _link("connection.mic_kw", 0), _link("stream_consents.LOCAL_FLEX_PICLO", 1)
    a = _site().model_copy(update={"evidence_refs": (first, second)})
    a = SiteModel.model_validate(a.model_dump(mode="json"))
    b = SiteModel.model_validate(
        {
            **_site().model_dump(mode="json"),
            "evidence_refs": [second.model_dump(), first.model_dump()],
        }
    )
    assert a.evidence_refs == b.evidence_refs == (first, second)
    assert hash_json(_canonical_site_dump(a)) == hash_json(_canonical_site_dump(b))
    assert hash_json(_canonical_site_dump(a)) != hash_json(_canonical_site_dump(_site()))
    with pytest.raises(ValidationError, match="must not repeat"):
        SiteModel.model_validate(
            {**_site().model_dump(mode="json"), "evidence_refs": [first.model_dump()] * 2}
        )
    empty = SiteModel.model_validate({**_site().model_dump(mode="json"), "evidence_refs": []})
    assert empty.evidence_refs is None
    assert hash_json(_canonical_site_dump(empty)) == hash_json(_canonical_site_dump(_site()))
    with pytest.raises(ValidationError):
        EvidenceLink.model_validate({"supports": " ", "evidence": first.evidence.model_dump()})


def test_an_evidence_link_dereferences_through_ic4() -> None:
    """The link carries the hash; validity is judged by IC-4 where the object is in hand."""
    obj = _evidence()
    link = EvidenceLink(supports="connection.mic_kw", evidence=evidence_ref(obj))
    assert resolve_ref(link.evidence, [obj], include_synthetic=True) is obj


def test_a_result_reference_enters_the_hash_through_the_result() -> None:
    site, lib = _site(), load_assumptions("gb_v0_1")
    plain = _result()
    carrying = _result(
        connection_agreement=connection_agreement_ref(_agreement()),
        evidence_refs=[_link("LOCAL_FLEX_PICLO")],
    )
    assert carrying.evidence_refs == (_link("LOCAL_FLEX_PICLO"),)
    a = assemble_quote_record(site, plain, None, lib)
    b = assemble_quote_record(site, carrying, None, lib)
    assert a.input_hash != b.input_hash


def test_assembly_carries_the_references_onto_the_quote_record() -> None:
    """Reachability: delete either line in `assemble_quote_record` and this fails."""
    lib = load_assumptions("gb_v0_1")
    ref = connection_agreement_ref(_agreement())
    site_link, result_link = _link("connection.mic_kw", 0), _link("LOCAL_FLEX_PICLO", 1)
    site = _site(agreement=ref).model_copy(update={"evidence_refs": (site_link,)})
    result = _result(evidence_refs=[result_link, site_link.model_copy()])
    quote = assemble_quote_record(site, result, None, lib)
    assert quote.connection_agreement == ref
    assert quote.evidence_refs == (result_link, site_link)  # ordered by what each supports
    again = QuoteRecord.model_validate_json(quote.model_dump_json())
    assert again.connection_agreement == ref and again.evidence_refs == quote.evidence_refs
    bare = assemble_quote_record(_site(), _result(), None, lib)
    assert bare.connection_agreement is None and bare.evidence_refs is None


# ------------------------------------------------------------------ 5. the free text is commentary

#: Every module under ``src/gridq`` that names ``anm_or_curtailment_notes``, and why it may.
#: A new name here is a new reader of free text: connection terms come from the IC-1 agreement
#: (`ConnectionDetails.agreement`), so add a module only if it quotes or writes the note whole.
FREE_TEXT_READERS = {
    "schemas/connection.py": "declares the field",
    "schemas/references.py": "names it in the module note",
    "schemas/chassis.py": "names it in the version note",
    "contracts/connection.py": "names it in the IC-1 module note",
    "outputs/report_charts.py": "prints the note verbatim",
    "quotes/disclosures.py": "quotes the note verbatim in a record-conflict disclosure",
    "portfolio_requirements/register.py": "writes a note onto a site it builds",
}


def test_no_new_module_reads_the_free_text_connection_field() -> None:
    readers = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "anm_or_curtailment_notes" in path.read_text(encoding="utf-8")
    }
    assert readers == set(FREE_TEXT_READERS)


# ------------------------------------------------------------------ 6. the contracts stay a leaf


@pytest.mark.parametrize(
    "path",
    sorted(p for p in (SRC / "contracts").glob("*.py") if p.name != "__main__.py"),
    ids=lambda p: p.name,
)
def test_no_contract_module_imports_gridq_outside_the_package(path: Path) -> None:
    """IC-12's models live in `gridq.schemas`; only the checker (`__main__`) may read them."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            if name == "gridq" or name.startswith("gridq."):
                assert name.startswith("gridq.contracts"), f"{path.name} imports {name}"
