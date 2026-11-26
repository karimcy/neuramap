"""Where each formula term comes from (WP-OPS-11, D144).

A binding is kept apart from the formula it serves: `BindingTable` names the formula by its hash
and carries its own hash, so when NEW-IC-14 (telemetry) or IC-8 (the verifier interface) is frozen
and a "not yet measurable" term is re-bound to a real field, the formula a signed agreement cites
keeps its hash.

Every term has exactly one binding, of one of three kinds:

- `FrozenContractField`: a field of a frozen contract, by JSON pointer into its committed schema
  (``*`` steps into an array). Tests resolve the pointer in the committed schema file and in the
  live model, and check the version is the current, unarchived one. A power field states its unit
  by its name's suffix (``_kw``, ``_mw``) or, for an IC-14 telemetry value, by `UnitFromQuantity`:
  the series' point pattern, whose quantity fixes the unit (W) in IC-14's `QUANTITY_TABLE`, with
  the sign, import/export split and kW conversion the term applies.
- `DossierFieldBinding`: a field of the WP-OPS-01 candidate site dossier, by path under the field's
  evidenced value; where the value is an embedded IC-1 instance the frozen field is named too.
- `NotYetMeasurable`: no frozen contract carries it yet. The binding names the kind of source it
  will be (a telemetry field, a reconciliation-ledger field or a verifier method), the contract
  and data product expected, and the roadmap work packages that deliver them, each quoted from
  the pinned roadmap (`ROADMAP_PIN`). A tripwire test fails the day one of those contracts or
  products appears, so the binding is revisited rather than left stale.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, NotApplicable, Unknown, content_hash
from gridq.contracts.telemetry_series import (
    CURATED_RESOLUTIONS,
    QUANTITY_TABLE,
    Aspect,
    AssetKind,
    MeasurementRole,
    Quantity,
    split_import_export_w,
    w_to_kw,
)
from gridq.ops.dossier_model import DossierField

__all__ = [
    "ROADMAP_PIN",
    "Binding",
    "BindingTable",
    "DossierFieldBinding",
    "FrozenContractField",
    "NotYetMeasurable",
    "RoadmapPin",
    "RoadmapWp",
    "SchemaPointer",
    "TermBinding",
    "UnitFromQuantity",
    "ic14_kw",
    "point_pattern",
    "roadmap_wp",
    "schema_leaves",
]

_POINTER = Annotated[str, Field(pattern=r"^(/[A-Za-z0-9_*]+)+$")]
_WP_ID = Annotated[str, Field(pattern=r"^WP-[A-Z]+-[0-9]{2}$")]


class SchemaPointer(ContractModel):
    pointer: _POINTER
    json_type: Literal["integer", "number", "boolean", "string"]


class UnitFromQuantity(ContractModel):
    """An IC-14 value's unit, read from its point's quantity rather than a field-name suffix.

    The term reads the curated series of every point matching ``<asset kind>.<asset id>.<quantity>.
    <role>.<aspect>`` for the site, at one of ``resolutions``. IC-14 fixes one integer unit per
    quantity (`QUANTITY_TABLE`); ``unit`` restates it and is checked against the table. ``sign``
    says whether the load-convention value is used as measured or negated, ``split`` whether the
    term is the import or export half of `split_import_export_w`, and ``rounding`` the side
    `w_to_kw` rounds to: the conservative side for the term's place in the formula, or away from
    zero for a term the formula only tests against zero. `ic14_kw` applies all three.
    """

    kind: Literal["unit_from_quantity"]
    asset_kinds: tuple[AssetKind, ...] = Field(min_length=1)
    quantity: Quantity
    roles: tuple[MeasurementRole, ...] = Field(min_length=1)
    aspect: Aspect
    unit: Literal["W"]
    resolutions: tuple[Literal["settlement_30min", "standard_1min"], ...] = Field(min_length=1)
    sign: Literal["as_measured", "negated"]
    split: Literal["import", "export"] | NotApplicable
    rounding: Literal["floor", "ceil", "away_from_zero"]

    @model_validator(mode="after")
    def _consistent(self) -> UnitFromQuantity:
        spec = QUANTITY_TABLE[self.quantity]
        if self.unit != spec.unit.value:
            raise ValueError(f"{self.quantity.value} is in {spec.unit.value}, not {self.unit}")
        if self.aspect not in spec.aspects:
            raise ValueError(f"{self.quantity.value} has no {self.aspect.value} aspect")
        for name, seq in (("asset kinds", self.asset_kinds), ("roles", self.roles)):
            if list(seq) != sorted(set(seq)):
                raise ValueError(f"{name} are ordered, without repeats")
        if not set(self.resolutions) <= set(CURATED_RESOLUTIONS):
            raise ValueError("a resolution IC-14 does not curate")
        return self


def ic14_kw(unit: UnitFromQuantity, value_w: int) -> int:
    """The term's whole kW from one good IC-14 `Aggregate` value (W, load convention)."""
    v = -value_w if unit.sign == "negated" else value_w
    if not isinstance(unit.split, NotApplicable):
        imp, exp = split_import_export_w(v)
        v = imp if unit.split == "import" else exp
    if unit.rounding == "away_from_zero":
        return w_to_kw(v, "ceil" if v > 0 else "floor")
    return w_to_kw(v, unit.rounding)


def point_pattern(unit: UnitFromQuantity) -> str:
    """The IC-14 point keys a term reads, e.g. ``pcs.<id>.active_power.setpoint_echo.whole``."""

    def alt(xs: tuple[AssetKind, ...] | tuple[MeasurementRole, ...]) -> str:
        return xs[0].value if len(xs) == 1 else "{" + ",".join(x.value for x in xs) + "}"

    return (
        f"{alt(unit.asset_kinds)}.<id>.{unit.quantity.value}.{alt(unit.roles)}.{unit.aspect.value}"
    )


class FrozenContractField(ContractModel):
    kind: Literal["frozen_contract_field"]
    contract_id: Literal["IC-1", "IC-3", "IC-4", "IC-11", "IC-14"]
    #: The file stem in ``gridq/contracts/schemas`` (``<stem>.v<version>.schema.json``).
    schema_stem: NonEmptyStr
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    pointers: tuple[SchemaPointer, ...] = Field(min_length=1)
    #: The unit the field name states (``_mw``, ``_kw``), checked against each pointer's leaf, or
    #: for IC-14 the point pattern whose quantity states it (`UnitFromQuantity`).
    unit_suffix: Literal["_mw", "_kw"] | UnitFromQuantity | NotApplicable
    #: How the term's value is read from the field(s), in words a verifier can follow.
    derivation: NonEmptyStr


class DossierFieldBinding(ContractModel):
    kind: Literal["dossier_field"]
    field: DossierField
    #: Path under the field's evidenced ``value`` (``*`` steps into an array).
    value_path: _POINTER
    via: FrozenContractField | NotApplicable = Field(discriminator="kind")
    derivation: NonEmptyStr


class NotYetMeasurable(ContractModel):
    kind: Literal["not_yet_measurable"]
    category: Literal["telemetry_field", "ledger_field", "verifier_method"]
    #: The contract expected to carry it (``NEW-IC-14``, ``IC-8``), or unknown.
    contract_id: NonEmptyStr | Unknown
    #: The roadmap data product expected to measure it (a ``delivers`` product name).
    data_product: NonEmptyStr | Unknown
    #: The file-stem prefix the contract's schema will have, for the tripwire (``ic14_``).
    expected_schema_prefix: Annotated[str, Field(pattern=r"^ic[0-9]+_$")] | Unknown
    roadmap_wps: tuple[_WP_ID, ...] = Field(min_length=1)
    #: What the source must provide for this term, so the eventual field can be checked.
    needs: NonEmptyStr

    @model_validator(mode="after")
    def _ordered(self) -> NotYetMeasurable:
        if list(self.roadmap_wps) != sorted(set(self.roadmap_wps)):
            raise ValueError("roadmap work packages are ordered, without repeats")
        return self


Binding = Annotated[
    FrozenContractField | DossierFieldBinding | NotYetMeasurable, Field(discriminator="kind")
]


class TermBinding(ContractModel):
    term_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]*$")]
    binding: Binding


class BindingTable(ContractModel):
    """The bindings of one definition template, naming the template by its content hash."""

    definition_id: NonEmptyStr
    formula_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    bindings: tuple[TermBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_per_term(self) -> BindingTable:
        ids = [b.term_id for b in self.bindings]
        if ids != sorted(set(ids)):
            raise ValueError("one binding per term, ordered by term id")
        return self

    def binding(self, term_id: str) -> FrozenContractField | DossierFieldBinding | NotYetMeasurable:
        for b in self.bindings:
            if b.term_id == term_id:
                return b.binding
        raise KeyError(term_id)

    def binding_hash(self) -> str:
        return content_hash(self)


# --------------------------------------------------------------------------------------------
# The roadmap, pinned: every work package a binding cites, quoted from one commit of the file
# --------------------------------------------------------------------------------------------


class RoadmapWp(ContractModel):
    id: _WP_ID
    title: NonEmptyStr
    delivers: NonEmptyStr
    spec_refs: tuple[NonEmptyStr, ...]


class RoadmapPin(ContractModel):
    repo: Literal["atlas"]
    path: Literal["roadmap/roadmap.yaml"]
    atlas_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    file_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    work_packages: tuple[RoadmapWp, ...]

    @model_validator(mode="after")
    def _ordered(self) -> RoadmapPin:
        ids = [w.id for w in self.work_packages]
        if ids != sorted(set(ids)):
            raise ValueError("work packages are ordered by id, without repeats")
        return self


def _wp(id_: str, title: str, delivers: str, *spec_refs: str) -> RoadmapWp:
    return RoadmapWp(id=id_, title=title, delivers=delivers, spec_refs=spec_refs)


#: Excerpts of ``atlas/roadmap/roadmap.yaml`` at the pinned commit, word for word. The pin test
#: re-derives each from ``git show <commit>:roadmap/roadmap.yaml`` when the atlas repo is present.
ROADMAP_PIN: Final = RoadmapPin(
    repo="atlas",
    path="roadmap/roadmap.yaml",
    atlas_commit="2bd8538e2a7d8beaa9b6c07ae314aa7cf9d9c0fc",
    file_sha256="b1e272a31a9c1c9644e7e0fc7f91d0882024da3cf75a26e6f5b416a9a39de50e",
    work_packages=(
        _wp(
            "WP-EDGE-08",
            "Control safety case",
            "Releases `control_safety_case`: HAZOP/FMEA of every write path (BESS, chillers, ANM), "
            "documented fail-safe states, the host's change-control process and "
            "critical-facilities "
            "sign-off before closed loop, per site.",
            "B1-09",
        ),
        _wp(
            "WP-LEDGER-04",
            "Pre-registered baselines",
            "Releases `preregistered_baselines`: P376-compatible baselines for import-reduction "
            "services and the cooling counterfactual, both registered before the demonstrator.",
            "B6-04",
        ),
        _wp(
            "WP-LEDGER-05",
            "Delivered versus underwritten",
            "Releases `reconciliation_report`: per period MW available, MWh delivered, revenue by "
            "stream and tier, availability, curtailment events against the A1 distribution, "
            "thermal "
            "compliance minutes and resilience-floor breaches.",
            "B6-02",
        ),
        _wp(
            "WP-LEDGER-07",
            "Freeze verifier interface contract",
            "Freezes IC-8: read-only dataset manifest, method versions and the recompute-by-hash "
            "procedure, agreed with the independent verifier.",
            "IC-8",
        ),
        _wp(
            "WP-LEDGER-08",
            "Independent-verifier interface",
            "Releases `verifier_dataset`: read-only access to raw telemetry, ledger and methods "
            "with "
            "reproducible recompute by hash.",
            "B6-05",
        ),
        _wp(
            "WP-OPS-02",
            "Definition of the demonstrator MW",
            "Releases `demonstrator_mw_definition`: written agreement with Innovate UK, PSAL and "
            "hosts on what counts towards the demonstrator MW (site load under management, BESS MW "
            "dispatched or verified flexible MW). (NEW-OPS-50MW: spec §15.3, no requirement ID.)",
            "NEW-OPS-50MW",
        ),
        _wp(
            "WP-SIZE-08",
            "Dispatch envelope export",
            "Releases `dispatch_envelope_export`: the underwritten reserve policy, availability "
            "windows and SoC floors exported as IC-3 envelopes.",
            "A8-03",
        ),
        _wp(
            "WP-TEL-01",
            "Freeze telemetry-series contract",
            "Freezes NEW-IC-14: point naming, units, time bases, quality flags, raw and curated "
            "tiers and the tenant segregation key, with conformance tests and recorded fixtures. "
            "(NEW-IC-14: dispatch, control room, ledger, twin and verifier all read telemetry; "
            "spec "
            "§10A has no contract for it.)",
            "NEW-IC-14",
        ),
        _wp(
            "WP-TEL-02",
            "Time-series ingest and store",
            "Releases `telemetry_store`: ingest at one-second operational and one-minute standard "
            "resolution with a 20 Hz event archive where required; immutable raw store and curated "
            "aggregates on a bought time-series database.",
            "B4-01",
        ),
        _wp(
            "WP-TEL-03",
            "Data-quality monitors",
            "Releases `data_quality_monitors`: detection of gaps, flatlines, clock drift, unit "
            "mismatches and meter-versus-SCADA divergence.",
            "B4-02",
        ),
        _wp(
            "WP-TEL-04",
            "Metering standards and meter registry",
            "Releases `meter_standard_spec`: revenue and asset meter requirements to the relevant "
            "BSC Codes of Practice and NESO operational metering, with each meter registered in "
            "the "
            "evidence registry.",
            "B4-03",
        ),
        _wp(
            "WP-THERM-03",
            "Thermal plant model with IT-safety constraints",
            "Releases `thermal_plant_model`: plant model for the candidate sites' cooling topology "
            "with COP against load and ambient, loop and building thermal mass and setpoint bands; "
            "ASHRAE inlet class or customer SLA limits, humidity, chiller minimum run and off "
            "times "
            "and rebound; usable flex output per NEW-IC-11. Built by adapting ThermaFlex if "
            "diligence says go, otherwise from first principles. Covers the spec A4-01 demo scope; "
            "other topologies are WP-THERM-10.",
            "A4-01",
            "A4-02",
            "A4-03",
        ),
    ),
)


def roadmap_wp(wp_id: str) -> RoadmapWp:
    for w in ROADMAP_PIN.work_packages:
        if w.id == wp_id:
            return w
    raise KeyError(f"{wp_id} is not in the pinned roadmap excerpt")


# --------------------------------------------------------------------------------------------
# JSON-schema pointer resolution (shared by the tests and the renderer)
# --------------------------------------------------------------------------------------------


def _expand(schema: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    """``node`` with every ``$ref`` followed and every ``anyOf``/``oneOf``/``allOf`` opened."""
    defs = schema.get("$defs", {})
    out: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if "$ref" in n:
            stack.append(defs[n["$ref"].rsplit("/", 1)[-1]])
            continue
        alts = [a for k in ("anyOf", "oneOf", "allOf") for a in n.get(k, [])]
        if alts:
            stack.extend(alts)
        else:
            out.append(n)
    return out


def schema_leaves(schema: dict[str, Any], pointer: str) -> list[dict[str, Any]]:
    """Every schema node ``pointer`` can reach (empty when it reaches none).

    ``*`` steps into an array's items; any other step is an object property.
    """
    nodes: list[dict[str, Any]] = [schema]
    for part in pointer.strip("/").split("/"):
        nxt: list[dict[str, Any]] = []
        for n in (e for node in nodes for e in _expand(schema, node)):
            if part == "*":
                if n.get("type") == "array":
                    items = n.get("items")
                    if isinstance(items, dict):
                        nxt.append(items)
                    nxt.extend(n.get("prefixItems", []))
            elif part in n.get("properties", {}):
                nxt.append(n["properties"][part])
        nodes = nxt
    return [e for node in nodes for e in _expand(schema, node)]
