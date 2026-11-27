"""The IC-4 reference pin and the IC-4 inventory (roadmap WP-BO-06; plan §7 step 6, §5).

1. No contract module other than ``evidence.py`` pins an IC-4 version. Every other contract reaches
   IC-4 through `EvidenceRef`, whose ``contract_version`` is the ``Version1x`` range, so a future
   IC-4 1.x minor touches no other contract's schema (plan §5).
2. ``src/gridq/contracts/IC4_INVENTORY.md`` names only real enum members, and every row's status
   is E, N or a gap with an owner.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from gridq.contracts import connection, curtailment, evidence, thermal_flex
from gridq.contracts.__main__ import FROZEN, SCHEMA_DIR, schema_path
from gridq.contracts._base import VERSION_1X_PATTERN

CONTRACTS_DIR = Path(evidence.__file__).resolve().parent
INVENTORY = CONTRACTS_DIR / "IC4_INVENTORY.md"
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


# ------------------------------------------------------------------ 1. the pin


def _strings(nodes: list[ast.expr | None]) -> set[str]:
    return {n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _literal_values(node: ast.expr) -> set[str]:
    """The string members of a ``Literal[...]`` subscript, or nothing."""
    if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
        return set()
    if node.value.id != "Literal":
        return set()
    inner = node.slice
    return _strings(list(inner.elts) if isinstance(inner, ast.Tuple) else [inner])


def ic4_pins(source: str) -> list[str]:
    """Where ``source`` pins an IC-4 version: a ``Literal["IC-4"]`` anywhere, or ``"IC-4"`` beside
    a version string in one call, dict, tuple, list, set or class body."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript) and "IC-4" in _literal_values(node):
            found.append(f"line {node.lineno}: Literal['IC-4']")
        siblings: set[str] = set()
        if isinstance(node, ast.Call):
            siblings = _strings([*node.args, *(k.value for k in node.keywords)])
        elif isinstance(node, ast.Dict):
            siblings = _strings([*node.keys, *node.values])
        elif isinstance(node, ast.Tuple | ast.List | ast.Set):
            siblings = _strings(list(node.elts))
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign):
                    siblings |= _literal_values(stmt.annotation) | _strings([stmt.value])
                elif isinstance(stmt, ast.Assign):
                    siblings |= _strings([stmt.value])
        if "IC-4" in siblings and any(_VERSION.match(s) for s in siblings):
            found.append(f"line {getattr(node, 'lineno', '?')}: 'IC-4' beside a version")
    return found


def test_the_pin_detector_is_not_vacuous() -> None:
    assert ic4_pins((CONTRACTS_DIR / "evidence.py").read_text(encoding="utf-8"))
    assert ic4_pins('x = {"contract": "IC-4", "contract_version": "1.0.0"}')
    assert ic4_pins('f(contract="IC-4", contract_version="1.1.0")')
    assert ic4_pins(
        'class R:\n    contract: Literal["IC-4"]\n    contract_version: Literal["1.0.0"]\n'
    )
    assert not ic4_pins('x = {"contract": "IC-1", "contract_version": "1.0.0"}')
    assert not ic4_pins('"""Depends on IC-4 v1.1.0."""')


@pytest.mark.parametrize(
    "path",
    sorted(p for p in CONTRACTS_DIR.glob("*.py") if p.name != "evidence.py"),
    ids=lambda p: p.name,
)
def test_no_contract_module_pins_an_ic4_version(path: Path) -> None:
    assert ic4_pins(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("stem,version", [(s, v) for s, _, v in FROZEN], ids=lambda v: str(v))
def test_every_live_schema_references_ic4_by_the_1x_range(stem: str, version: str) -> None:
    schema = json.loads(schema_path(stem, version).read_text(encoding="utf-8"))
    ref = schema.get("$defs", {}).get("EvidenceRef")
    if ref is None:
        return
    pin = ref["properties"]["contract_version"]
    assert pin["pattern"] == VERSION_1X_PATTERN, stem
    assert "const" not in pin and "enum" not in pin, stem


def test_ic4_is_referenced_by_every_contract_that_embeds_it() -> None:
    embedders = set()
    for stem, _, version in FROZEN:
        text = schema_path(stem, version).read_text(encoding="utf-8")
        if '"EvidenceRef"' in text:
            embedders.add(stem)
    assert embedders == {
        "ic1_connection_agreement",
        "ic2_curtailment_scenario_set",
        "ic11_thermal_flex_availability",
        # IC-3 1.0.0 (WP-SIZE-07): a committed service's basis may be an IC-4 reference.
        "ic3_dispatch_envelope",
        # IC-5 1.0.0 (WP-EDGE-01): an envelope_install frame carries the whole IC-3 envelope.
        "ic5_cloud_to_edge_frame",
        # IC-12 2.0.0 (WP-CORE-03): the chassis carries IC-4 evidence links.
        "ic12_site_model",
        "ic12_optimisation_result",
        "ic12_quote_record",
        "ic15_contract_template_set",
        "ic15_executed_contract_terms",
        # IC-9 1.0.0 (WP-SITE-07): SLA documents, firm price offers and basis evidence.
        "ic9_compute_turndown_declaration",
        # IC-14 1.0.0 (WP-TEL-01): a metered series' meter certificate.
        "ic14_series_descriptor",
    }


def test_ic15_embeds_no_ic4_enum_so_a_types_only_minor_re_versions_ic4_alone() -> None:
    # IC-15 (WP-GRC-09) names IC-4 types and roles as strings checked against the live enums, so
    # its schemas carry `EvidenceRef` and nothing else of IC-4 (README cost table).
    for stem in ("ic15_contract_template_set", "ic15_executed_contract_terms"):
        schema = json.loads(schema_path(stem, "1.0.0").read_text(encoding="utf-8"))
        assert not {"EvidenceType", "IssuerRole", "SelfIssuerRole"} & set(schema["$defs"]), stem


def test_the_ic4_version_is_in_the_range_it_publishes() -> None:
    assert re.fullmatch(VERSION_1X_PATTERN, evidence.CONTRACT_VERSION)
    for version in evidence.SUPPORTED_VERSIONS:
        assert re.fullmatch(VERSION_1X_PATTERN, version)


#: IC-4 1.2.0 (WP-GRC-09) is a types-only minor: it re-versions IC-4 alone.
_SUPPORTED = {
    evidence: ("1.0.0", "1.1.0", "1.2.0"),
    connection: ("1.0.0", "1.1.0"),
    curtailment: ("1.0.0", "1.1.0"),
    thermal_flex: ("1.0.0", "1.1.0"),
}


@pytest.mark.parametrize("module", [evidence, connection, curtailment, thermal_flex])
def test_each_re_versioned_module_writes_its_last_supported_version(module: object) -> None:
    supported = module.SUPPORTED_VERSIONS  # type: ignore[attr-defined]
    current = module.CONTRACT_VERSION  # type: ignore[attr-defined]
    assert supported == _SUPPORTED[module]  # type: ignore[index]
    assert supported[-1] == current
    assert module.GIT_TAG.endswith(f"/v{current}")  # type: ignore[attr-defined]
    assert "SUPPORTED_VERSIONS" in module.__all__  # type: ignore[attr-defined]


def test_the_schema_dir_holds_one_live_file_per_frozen_model() -> None:
    live = {schema_path(s, v).name for s, _, v in FROZEN}
    assert live <= {p.name for p in SCHEMA_DIR.glob("*.schema.json")}


# ------------------------------------------------------------------ 2. the inventory


def _table(text: str, first: str) -> list[list[str]]:
    """Rows of the markdown table whose header's first cell is ``first``."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"| {first} |"))
    rows = []
    for ln in lines[start + 2 :]:
        if not ln.startswith("|"):
            break
        rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return rows


def _inventory() -> str:
    return INVENTORY.read_text(encoding="utf-8")


def test_the_inventory_rows_are_numbered_and_have_six_columns() -> None:
    rows = _table(_inventory(), "#")
    assert [r[0] for r in rows] == [str(i) for i in range(1, len(rows) + 1)]
    assert len(rows) == 37
    assert all(len(r) == 6 for r in rows)


def test_every_backticked_type_and_role_is_an_enum_member() -> None:
    types = {t.value for t in evidence.EvidenceType}
    roles = {r.value for r in evidence.IssuerRole} | {r.value for r in evidence.SelfIssuerRole}
    seen = 0
    for row in _table(_inventory(), "#"):
        for cell in (row[3], row[4]):
            for value in re.findall(r"`([^`]+)`", cell):
                assert value in types | roles, (row[0], value)
                seen += 1
    assert seen > 50


def test_every_status_is_e_n_or_a_gap_with_an_owner() -> None:
    gaps = {r[0]: r for r in _table(_inventory(), "Gap")}
    assert all(r[2] for r in gaps.values()), "every gap names an owner"
    for row in _table(_inventory(), "#"):
        status = row[5]
        named = set(re.findall(r"\bG\d+\b", status))
        assert re.search(r"\b[EN]\b", status) or named, (row[0], status)
        assert named <= set(gaps), (row[0], named)


def test_the_owner_widening_is_recorded() -> None:
    # Plan §11.6: row 7 is Neura's decision record; G1 is closed; G7 (no organisation scope) opens.
    rows = {r[0]: r for r in _table(_inventory(), "#")}
    assert re.findall(r"`([^`]+)`", rows["7"][3]) == ["decision_record"]
    assert re.findall(r"`([^`]+)`", rows["7"][4]) == ["neura_self_issued"]
    assert rows["7"][5].startswith("N")
    gaps = {r[0]: r for r in _table(_inventory(), "Gap")}
    assert "G1" not in gaps
    assert set(gaps) == {"G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10"}
    assert "WP-BO-02" in gaps["G7"][2]


def test_wp_grc_09_closes_g2_and_opens_g8() -> None:
    # IC-4 1.2.0 types every template; IC-15 holds the required fields. G8: no issuer role for an
    # MGA or a floor provider, so the performance wrapper admits an insurer only.
    from gridq.contracts.contract_templates import load_template_set

    rows = {r[0]: r for r in _table(_inventory(), "#")}
    registered = {e.evidence_type for t in load_template_set().templates for e in t.executed_as}
    assert set(re.findall(r"`([^`]+)`", rows["9"][3])) == registered
    assert "G2" not in rows["9"][5] and "G8" in rows["9"][5]
    assert rows["9"][5].startswith("N")
    gaps = {r[0]: r for r in _table(_inventory(), "Gap")}
    assert "WP-GRC-13" in gaps["G8"][2]


def test_wp_ops_01_opens_g9_for_the_bess_funder() -> None:
    # The candidate site dossier cannot evidence a BESS funder: IC-4 has no type for a funding
    # commitment. The dossier cites this row (gridq.ops.dossier.G9).
    from gridq.ops.dossier import G9

    gaps = {r[0]: r for r in _table(_inventory(), "Gap")}
    assert "WP-BO-02" in gaps["G9"][2] and "WP-BO-02" in G9.owner
    assert G9.gap_id == "IC4-G9"


def test_neura_self_issued_appears_only_on_the_self_issuable_types() -> None:
    allowed = {t.value for t in evidence.SELF_ISSUABLE_TYPES}
    for row in _table(_inventory(), "#"):
        if "`neura_self_issued`" in row[4]:
            assert set(re.findall(r"`([^`]+)`", row[3])) <= allowed, row[0]


# The Atlas roadmap, when it is checked out beside this repository (it is on a developer machine,
# not in CI). Roadmap criterion: "an inventory lists every roadmap acceptance criterion that says
# evidence object".
ROADMAP = Path(__file__).resolve().parents[2].parent / "atlas" / "roadmap" / "roadmap.yaml"
_SAYS_EVIDENCE_OBJECT = re.compile(r"evidence[ -]objects?\b", re.IGNORECASE)
_NO_ROADMAP = "the Atlas roadmap is not checked out beside this repo"


@pytest.mark.skipif(not ROADMAP.is_file(), reason=_NO_ROADMAP)
def test_every_roadmap_criterion_that_says_evidence_object_has_an_inventory_row() -> None:
    yaml = pytest.importorskip("yaml")
    roadmap = yaml.safe_load(ROADMAP.read_text(encoding="utf-8"))
    needed = {
        wp["id"]
        for wp in roadmap["work_packages"]
        if any(_SAYS_EVIDENCE_OBJECT.search(str(line)) for line in wp.get("acceptance") or [])
    }
    packages = " ".join(r[1] for r in _table(_inventory(), "#"))
    listed = set(re.findall(r"\bWP-[A-Z]+-\d+\b", packages))
    assert len(needed) >= 24, sorted(needed)  # 24 on 2026-11-25; fewer means the wrong file
    assert needed <= listed, f"no inventory row for {sorted(needed - listed)}"
