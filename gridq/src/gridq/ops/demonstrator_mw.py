"""Demonstrator MW options: the worked count and the pack (WP-OPS-11, `demonstrator_mw_options`).

**Worked count (D145).** For each candidate site in a WP-OPS-01 dossier and each
candidate definition, one cell:

- `UpperBound`: an integer kW that every admissible filling of the definition stays at or below
  at the site, computed only from evidenced dossier fields, each cited by path and evidence, with
  the conditions under which the inequality holds and the inputs it leaves out. Rounded up to
  whole kW.
- `UnknownInputs`: the dossier fields (or the terms inside an evidenced field) that are missing,
  each with the asks that close it. Asks for terms inside an evidenced field are minted here,
  site-scoped and content-addressed like the dossier's own, and listed separately.
- `NotInDossier`: the definition needs inputs no dossier field carries (the planned demonstrator
  battery, telemetry, baselines, the verifier's recompute), each named with its contract field and
  roadmap work packages.

A cell is never zero for want of data, and no figure is a guess. Only A has a portfolio total, and
only when every site has a bound and no dossier-level ask is open. Nothing here compares a total
with any target figure or orders the sites.

**Confidentiality.** A worked count naming a real site is written only where git ignores it
(``dossiers/``), like the dossier itself. The pack in ``docs/ops`` carries definitions, sources,
quotes and the draft text, and for the worked count only per-kind tallies.

``python -m gridq.ops.demonstrator_mw --as-of YYYY-MM-DD`` builds the dossier and the worked count
into ``dossiers/``; ``--write-pack PATH`` renders the pack.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from gridq.contracts._base import (
    ContractModel,
    IsoDate,
    NonEmptyStr,
    Sha256,
    Unknown,
    canonical_json,
    content_hash,
)
from gridq.contracts.evidence import EvidenceType
from gridq.governance.dpa_guard import EVIDENCE_DIR_ENV, repo_root
from gridq.ops.dossier import DossierWriteRefused, _require_git_ignored, build_dossier
from gridq.ops.dossier_inputs import DossierInputError, default_register
from gridq.ops.dossier_model import (
    AskHolder,
    CandidateSiteDossier,
    DossierAsk,
    Evidenced,
    GenerationAsset,
    GenerationTechnology,
    Ic4Evidence,
    PublicSourceEvidence,
    SiteDossier,
    SyntheticArchetypeEvidence,
    UnknownField,
)
from gridq.ops.mw_bindings import (
    ROADMAP_PIN,
    BindingTable,
    DossierFieldBinding,
    FrozenContractField,
    NotYetMeasurable,
    UnitFromQuantity,
    point_pattern,
    roadmap_wp,
)
from gridq.ops.mw_definitions import BINDINGS, DEFINITIONS
from gridq.ops.mw_draft import NOT_AGREED_STATEMENT, render_draft_agreement
from gridq.ops.mw_formula import (
    ChoiceSlot,
    MwDefinition,
    NumberSlot,
    render_kpi_formula,
    render_text,
)
from gridq.ops.mw_grant_text import GRANT_TEXT_REGISTER, GrantTextRegister, concept_relations

__all__ = [
    "PACK_PATH",
    "WORKED_COUNT_SCHEMA",
    "DefinitionsIndex",
    "ExternalInput",
    "NotInDossier",
    "UnknownInputs",
    "UpperBound",
    "WorkedCount",
    "build_worked_count",
    "definitions_index",
    "render_pack",
    "render_worked_count",
    "worked_count_json",
    "write_worked_count",
]

WORKED_COUNT_SCHEMA: Final = "gridq.demonstrator_mw_worked_count"
WORKED_COUNT_VERSION: Final = "1.0.0"
PACK_PATH: Final = Path("docs") / "ops" / "demonstrator_mw_options.md"

_WP = Annotated[str, Field(pattern=r"^WP-[A-Z]+-[0-9]{2}$")]
DefinitionId = Literal[
    "site_load_under_management", "bess_mw_actively_dispatched", "verified_flexible_mw"
]


# --------------------------------------------------------------------------------------------
# The definitions, indexed by hash
# --------------------------------------------------------------------------------------------


class DefinitionsEntry(ContractModel):
    definition_id: DefinitionId
    template_hash: Sha256
    binding_hash: Sha256


class DefinitionsIndex(ContractModel):
    entries: tuple[DefinitionsEntry, ...] = Field(min_length=3, max_length=3)

    def content_hash(self) -> str:
        return content_hash(self)


def definitions_index(
    definitions: tuple[MwDefinition, ...] = DEFINITIONS,
    bindings: dict[str, BindingTable] | None = None,
) -> DefinitionsIndex:
    tables = BINDINGS if bindings is None else bindings
    return DefinitionsIndex(
        entries=tuple(
            DefinitionsEntry.model_validate(
                {
                    "definition_id": d.definition_id,
                    "template_hash": d.content_hash(),
                    "binding_hash": tables[d.definition_id].binding_hash(),
                }
            )
            for d in definitions
        )
    )


# --------------------------------------------------------------------------------------------
# Worked-count cells
# --------------------------------------------------------------------------------------------


class ExternalInput(ContractModel):
    """An input no dossier field carries, with where it will come from."""

    name: NonEmptyStr
    contract_field: NonEmptyStr
    roadmap_wps: tuple[_WP, ...] = Field(min_length=1)


class BoundTerm(ContractModel):
    term: NonEmptyStr
    field_path: NonEmptyStr
    #: The value as the evidenced field states it, with its unit.
    stated: NonEmptyStr
    kw: Annotated[int, Field(ge=0)]
    evidence: tuple[NonEmptyStr, ...] = Field(min_length=1)


class UpperBound(ContractModel):
    kind: Literal["upper_bound"]
    kw: Annotated[int, Field(ge=0)]
    scope: Literal["connection_and_onsite_generation", "recorded_batteries_only"]
    inequality: NonEmptyStr
    holds_while: tuple[NonEmptyStr, ...] = Field(min_length=1)
    terms: tuple[BoundTerm, ...]
    rounding: Literal["each_term_rounded_up_to_whole_kw"]
    not_included: tuple[ExternalInput, ...]


class MissingField(ContractModel):
    field_path: NonEmptyStr
    reason: NonEmptyStr
    ask_ids: tuple[Sha256, ...] = Field(min_length=1)


class UnknownInputs(ContractModel):
    kind: Literal["unknown_inputs"]
    missing: tuple[MissingField, ...] = Field(min_length=1)


class NotInDossier(ContractModel):
    kind: Literal["not_in_dossier"]
    reason: NonEmptyStr
    external_inputs: tuple[ExternalInput, ...] = Field(min_length=1)


Cell = Annotated[UpperBound | UnknownInputs | NotInDossier, Field(discriminator="kind")]


class DefinitionCell(ContractModel):
    definition_id: DefinitionId
    cell: Cell


class SiteWorkedCount(ContractModel):
    site_id: NonEmptyStr
    customer_data: Literal["released_under_dpa", "synthetic", "not_released"]
    cells: tuple[DefinitionCell, ...] = Field(min_length=3, max_length=3)


class Tally(ContractModel):
    """How many sites have each kind of cell under one definition. Not a total of kW."""

    definition_id: DefinitionId
    upper_bound: Annotated[int, Field(ge=0)]
    unknown_inputs: Annotated[int, Field(ge=0)]
    not_in_dossier: Annotated[int, Field(ge=0)]


class RegisterCeiling(ContractModel):
    """The sum of A's site bounds over the whole register: only when every site has one and no
    dossier-level ask (a missing site) is open."""

    kind: Literal["register_ceiling"]
    definition_id: Literal["site_load_under_management"]
    kw: Annotated[int, Field(ge=0)]
    statement: NonEmptyStr


class WorkedCount(ContractModel):
    product: Literal["gridq.demonstrator_mw_worked_count"]
    product_version: Literal["1.0.0"]
    work_package: Literal["WP-OPS-11"]
    statement: Literal[
        "Not agreed; no option is recommended; agreement is WP-OPS-02, the owner's to negotiate."
    ]
    as_of: IsoDate
    dossier_hash: Sha256
    dossier_schema_version: Literal["1.0.0"]
    definitions_hash: Sha256
    sites: tuple[SiteWorkedCount, ...]
    minted_asks: tuple[DossierAsk, ...]
    dossier_asks: tuple[Sha256, ...]
    tallies: tuple[Tally, ...] = Field(min_length=3, max_length=3)
    register_ceiling_a: RegisterCeiling | Unknown = Field(discriminator="kind")

    @model_validator(mode="after")
    def _ordered(self) -> WorkedCount:
        ids = [s.site_id for s in self.sites]
        if ids != sorted(set(ids)):
            raise ValueError("sites in dossier order (by site_id), without repeats")
        asks = [a.ask_id for a in self.minted_asks]
        if asks != sorted(set(asks)):
            raise ValueError("minted asks ordered by id, without repeats")
        return self

    def content_hash(self) -> str:
        return content_hash(self)

    @property
    def has_real_site(self) -> bool:
        return any(not s.site_id.startswith("SYNTHETIC-") for s in self.sites)


# --------------------------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------------------------

_PLANNED_BESS: Final = ExternalInput(
    name="planned demonstrator battery rating",
    contract_field="IC-3 DispatchEnvelope.rated_discharge_kw",
    roadmap_wps=("WP-SIZE-08",),
)
_C_EXTERNAL: Final = (
    ExternalInput(
        name="measured net import and asset demand per interval",
        contract_field="NEW-IC-14 telemetry series (not yet frozen)",
        roadmap_wps=("WP-TEL-01", "WP-TEL-02"),
    ),
    ExternalInput(
        name="pre-registered baseline per site and per flexible asset",
        contract_field="preregistered_baselines (no contract named)",
        roadmap_wps=("WP-LEDGER-04",),
    ),
    ExternalInput(
        name="thermal flexibility availability and its evidence class",
        contract_field="IC-11 ThermalFlexAvailability curve reduction_kw and evidence_class",
        roadmap_wps=("WP-THERM-03",),
    ),
    ExternalInput(
        name="the independent verifier's recompute",
        contract_field="IC-8 dataset manifest and recompute-by-hash (not yet frozen)",
        roadmap_wps=("WP-LEDGER-07", "WP-LEDGER-08"),
    ),
)
_A_HOLDS_WHILE: Final = (
    "the site keeps within the import capacity its connection agreement grants",
    "each on-site asset runs as its evidenced operation states (an asset recorded standby_only "
    "or islanded_failover runs only while the site is not importing)",
    "no asset is added to the site beyond those the dossier records",
)
_A_INEQUALITY: Final = (
    "For every admissible filling of A: A at the site <= (rating of assets run in parallel with "
    "the grid) + max(import capacity, rating of assets run only off-grid). The agreed_capacity "
    "basis equals the import capacity; boundary import is at most it; gross load is import less "
    "export plus asset output, at most the right-hand side; any statistic over the window, and "
    "the authority and quality conditions, keep the value at or below the bound."
)
_B_INEQUALITY: Final = (
    "For every admissible filling of B: B at the site <= the summed rated power of the batteries "
    "the dossier records. Rated, committed and measured discharge are each at most the rating."
)
_B_HOLDS_WHILE: Final = (
    "the site's batteries are those the dossier records, at their recorded ratings",
    "the IC-3 envelope rates and commits no more than the recorded batteries' rating",
)


def _kw(value: float, unit: Literal["mw", "kw"]) -> int:
    d = Decimal(repr(value))
    return math.ceil(d * 1000 if unit == "mw" else d)


def _evidence_ids(field: Evidenced[Any]) -> tuple[str, ...]:
    out: set[str] = set()
    for item in field.evidence:
        if isinstance(item, Ic4Evidence):
            out.add(item.ref.evidence_hash)
        elif isinstance(item, PublicSourceEvidence):
            out.add(f"public source: {item.source.title}")
        elif isinstance(item, SyntheticArchetypeEvidence):
            out.add(item.agreement_hash)
    return tuple(sorted(out))


def _unknown_field(site_id: str, name: str, f: UnknownField) -> MissingField:
    return MissingField(
        field_path=f"{site_id}/{name}",
        reason=f.reason,
        ask_ids=tuple(sorted({a.ask_id for a in f.asks})),
    )


def _site_ask(
    site_id: str,
    request: str,
    holder: AskHolder,
    closes_with: tuple[EvidenceType, ...],
    minted: dict[str, DossierAsk],
) -> str:
    ask = DossierAsk.make(
        request=request, holder=holder, scope=f"site:{site_id}", closes_with=closes_with
    )
    minted[ask.ask_id] = ask
    return ask.ask_id


_ASSET_DOCS: Final = (EvidenceType.COMMISSIONING_RECORD, EvidenceType.G99_APPROVAL)
_CONNECTION_DOCS: Final = (EvidenceType.CONNECTION_AGREEMENT, EvidenceType.CONNECTION_OFFER)


def _asset_terms(
    site: SiteDossier,
    assets: Iterable[tuple[int, GenerationAsset]],
    evidence: tuple[str, ...],
    minted: dict[str, DossierAsk],
) -> tuple[list[BoundTerm], list[MissingField], list[GenerationAsset]]:
    terms: list[BoundTerm] = []
    missing: list[MissingField] = []
    known: list[GenerationAsset] = []
    for i, asset in assets:
        path = f"{site.site_id}/onsite_generation/assets/{i}"
        if isinstance(asset.rated_kw, Unknown):
            missing.append(
                MissingField(
                    field_path=f"{path}/rated_kw",
                    reason=asset.rated_kw.reason,
                    ask_ids=(
                        _site_ask(
                            site.site_id,
                            f"State the rated power (kW) of the on-site {asset.technology.value} "
                            f"recorded as '{asset.description}'",
                            AskHolder.CUSTOMER,
                            _ASSET_DOCS,
                            minted,
                        ),
                    ),
                )
            )
            continue
        known.append(asset)
        op = asset.operation if isinstance(asset.operation, str) else "operation unknown"
        terms.append(
            BoundTerm(
                term=f"{asset.technology.value} rated power ({op})",
                field_path=f"{path}/rated_kw",
                stated=f"{asset.rated_kw!r} kW",
                kw=_kw(asset.rated_kw, "kw"),
                evidence=evidence,
            )
        )
    return terms, missing, known


def _cell_a(site: SiteDossier, minted: dict[str, DossierAsk]) -> UnknownInputs | UpperBound:
    missing: list[MissingField] = []
    terms: list[BoundTerm] = []
    cap_kw = 0
    ct = site.connection_terms
    if isinstance(ct, UnknownField):
        missing.append(_unknown_field(site.site_id, "connection_terms", ct))
    elif len(ct.value.agreements) > 1:
        refs = ", ".join(a.agreement.agreement_ref for a in ct.value.agreements)
        missing.append(
            MissingField(
                field_path=f"{site.site_id}/connection_terms/agreements",
                reason=f"{len(ct.value.agreements)} connection agreements are evidenced; which "
                "governs import during the demonstrator is not stated",
                ask_ids=(
                    _site_ask(
                        site.site_id,
                        f"State which of the site's connection agreements ({refs}) governs "
                        "import during the demonstrator window",
                        AskHolder.CUSTOMER,
                        _CONNECTION_DOCS,
                        minted,
                    ),
                ),
            )
        )
    else:
        agreement = ct.value.agreements[0].agreement
        evidence = _evidence_ids(ct)
        for name in ("firm_import_mw", "non_firm_import_mw"):
            value = getattr(agreement, name)
            path = f"{site.site_id}/connection_terms/agreements/0/agreement/{name}"
            if isinstance(value, Unknown):
                missing.append(
                    MissingField(
                        field_path=path,
                        reason=value.reason,
                        ask_ids=(
                            _site_ask(
                                site.site_id,
                                f"State {name} (MW) of connection agreement "
                                f"{agreement.agreement_ref}",
                                AskHolder.NETWORK_OPERATOR,
                                _CONNECTION_DOCS,
                                minted,
                            ),
                        ),
                    )
                )
                continue
            kw = _kw(value, "mw")
            cap_kw += kw
            terms.append(
                BoundTerm(
                    term=name, field_path=path, stated=f"{value!r} MW", kw=kw, evidence=evidence
                )
            )
    parallel_kw = offgrid_kw = 0
    og = site.onsite_generation
    if isinstance(og, UnknownField):
        missing.append(_unknown_field(site.site_id, "onsite_generation", og))
    else:
        evidence = _evidence_ids(og)
        indexed = list(enumerate(og.value.assets))
        for i, asset in indexed:
            if isinstance(asset.operation, Unknown):
                missing.append(
                    MissingField(
                        field_path=f"{site.site_id}/onsite_generation/assets/{i}/operation",
                        reason=asset.operation.reason,
                        ask_ids=(
                            _site_ask(
                                site.site_id,
                                f"State how the on-site {asset.technology.value} recorded as "
                                f"'{asset.description}' operates: standby only, in parallel "
                                "with the grid, or islanded failover",
                                AskHolder.CUSTOMER,
                                _ASSET_DOCS,
                                minted,
                            ),
                        ),
                    )
                )
        a_terms, a_missing, known = _asset_terms(site, indexed, evidence, minted)
        terms += a_terms
        missing += a_missing
        for asset in known:
            assert not isinstance(asset.rated_kw, Unknown)
            if asset.operation == "parallel_with_grid":
                parallel_kw += _kw(asset.rated_kw, "kw")
            elif isinstance(asset.operation, str):
                offgrid_kw += _kw(asset.rated_kw, "kw")
    if missing:
        return UnknownInputs(kind="unknown_inputs", missing=tuple(missing))
    return UpperBound(
        kind="upper_bound",
        kw=parallel_kw + max(cap_kw, offgrid_kw),
        scope="connection_and_onsite_generation",
        inequality=_A_INEQUALITY,
        holds_while=_A_HOLDS_WHILE,
        terms=tuple(terms),
        rounding="each_term_rounded_up_to_whole_kw",
        not_included=(_PLANNED_BESS,),
    )


def _cell_b(
    site: SiteDossier, minted: dict[str, DossierAsk]
) -> UnknownInputs | UpperBound | NotInDossier:
    og = site.onsite_generation
    if isinstance(og, UnknownField):
        return UnknownInputs(
            kind="unknown_inputs", missing=(_unknown_field(site.site_id, "onsite_generation", og),)
        )
    batteries = [
        (i, a)
        for i, a in enumerate(og.value.assets)
        if a.technology is GenerationTechnology.BATTERY_STORAGE
    ]
    if not batteries:
        return NotInDossier(
            kind="not_in_dossier",
            reason="the dossier records no battery at the site; a planned demonstrator battery's "
            "rating is not a dossier field",
            external_inputs=(_PLANNED_BESS,),
        )
    terms, missing, known = _asset_terms(site, batteries, _evidence_ids(og), minted)
    if missing:
        return UnknownInputs(kind="unknown_inputs", missing=tuple(missing))
    return UpperBound(
        kind="upper_bound",
        kw=sum(t.kw for t in terms),
        scope="recorded_batteries_only",
        inequality=_B_INEQUALITY,
        holds_while=_B_HOLDS_WHILE,
        terms=tuple(terms),
        rounding="each_term_rounded_up_to_whole_kw",
        not_included=(_PLANNED_BESS,),
    )


def _cell_c() -> NotInDossier:
    return NotInDossier(
        kind="not_in_dossier",
        reason="verified flexible MW is measured against pre-registered baselines from telemetry "
        "and reproduced by the verifier; no dossier field carries any of these",
        external_inputs=_C_EXTERNAL,
    )


def _tally(sites: Sequence[SiteWorkedCount], definition_id: str) -> Tally:
    cells = [c.cell for s in sites for c in s.cells if c.definition_id == definition_id]
    return Tally.model_validate(
        {
            "definition_id": definition_id,
            "upper_bound": sum(isinstance(c, UpperBound) for c in cells),
            "unknown_inputs": sum(isinstance(c, UnknownInputs) for c in cells),
            "not_in_dossier": sum(isinstance(c, NotInDossier) for c in cells),
        }
    )


def build_worked_count(
    dossier: CandidateSiteDossier,
    definitions: tuple[MwDefinition, ...] = DEFINITIONS,
    bindings: dict[str, BindingTable] | None = None,
) -> WorkedCount:
    """The worked count for every site of ``dossier``. Reads evidenced fields only."""
    minted: dict[str, DossierAsk] = {}
    sites: list[SiteWorkedCount] = []
    for site in dossier.sites:
        cells = (
            ("site_load_under_management", _cell_a(site, minted)),
            ("bess_mw_actively_dispatched", _cell_b(site, minted)),
            ("verified_flexible_mw", _cell_c()),
        )
        sites.append(
            SiteWorkedCount(
                site_id=site.site_id,
                customer_data=site.customer_data,
                cells=tuple(
                    DefinitionCell.model_validate({"definition_id": d, "cell": c}) for d, c in cells
                ),
            )
        )
    a_cells = [s.cells[0].cell for s in sites]
    open_dossier_asks = tuple(sorted(a.ask_id for a in dossier.dossier_asks))
    ceiling: RegisterCeiling | Unknown
    if not sites:
        ceiling = Unknown(kind="unknown", reason="the register has no site")
    elif open_dossier_asks:
        ceiling = Unknown(
            kind="unknown",
            reason=f"{len(open_dossier_asks)} dossier-level ask(s) open: the register may be "
            "missing sites",
        )
    elif not all(isinstance(c, UpperBound) for c in a_cells):
        n = sum(not isinstance(c, UpperBound) for c in a_cells)
        ceiling = Unknown(kind="unknown", reason=f"{n} site(s) have no A bound")
    else:
        ceiling = RegisterCeiling(
            kind="register_ceiling",
            definition_id="site_load_under_management",
            kw=sum(c.kw for c in a_cells if isinstance(c, UpperBound)),
            statement="the sum of every register site's A bound; it holds under each site's "
            "stated conditions and leaves out each site's not-included inputs",
        )
    return WorkedCount(
        product=WORKED_COUNT_SCHEMA,
        product_version=WORKED_COUNT_VERSION,
        work_package="WP-OPS-11",
        statement=NOT_AGREED_STATEMENT,
        as_of=dossier.as_of,
        dossier_hash=dossier.content_hash(),
        dossier_schema_version=dossier.dossier_schema_version,
        definitions_hash=definitions_index(definitions, bindings).content_hash(),
        sites=tuple(sites),
        minted_asks=tuple(minted[k] for k in sorted(minted)),
        dossier_asks=open_dossier_asks,
        tallies=tuple(_tally(sites, d.definition_id) for d in definitions),
        register_ceiling_a=ceiling,
    )


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def worked_count_json(wc: WorkedCount) -> str:
    return (
        json.dumps(json.loads(canonical_json(wc)), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


def _mw(kw: int) -> str:
    return f"{Decimal(kw) / 1000:.3f} MW"


def _cell_text(cell: UpperBound | UnknownInputs | NotInDossier) -> str:
    if isinstance(cell, UpperBound):
        parts = "; ".join(f"{t.term} {t.stated} ({t.field_path})" for t in cell.terms) or "none"
        left = ", ".join(e.name for e in cell.not_included)
        return f"at most {cell.kw} kW ({_mw(cell.kw)}); from: {parts}; not included: {left}"
    if isinstance(cell, UnknownInputs):
        return "unknown; missing: " + "; ".join(
            f"{m.field_path} (asks {', '.join(a[7:19] for a in m.ask_ids)})" for m in cell.missing
        )
    return "not in the dossier; needs: " + "; ".join(
        f"{e.name} ({e.contract_field}; {', '.join(e.roadmap_wps)})" for e in cell.external_inputs
    )


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_worked_count(wc: WorkedCount) -> str:
    """Markdown from the worked count only, with its hash. Real-site output stays git-ignored."""
    lines = [
        "# Demonstrator MW worked count (WP-OPS-11)",
        "",
        f"**{NOT_AGREED_STATEMENT}**",
        "",
        f"- As of: {wc.as_of.isoformat()}",
        f"- Worked count hash: `{wc.content_hash()}`",
        f"- Dossier hash: `{wc.dossier_hash}`",
        f"- Definitions hash: `{wc.definitions_hash}`",
        "",
        "| Site | Data | A: site load under management | B: BESS MW actively dispatched | "
        "C: verified flexible MW |",
        "|---|---|---|---|---|",
    ]
    for s in wc.sites:
        cells = [_md(_cell_text(c.cell)) for c in s.cells]
        lines.append(f"| {s.site_id} | {s.customer_data} | " + " | ".join(cells) + " |")
    lines += ["", "## Tallies (sites per kind of cell)", ""]
    lines += _tally_lines(wc)
    lines += ["", "## Asks minted for terms inside evidenced fields", ""]
    lines += [
        f"- `{a.ask_id[7:19]}` ({a.scope}, {a.holder.value}): {a.request}" for a in wc.minted_asks
    ]
    if not wc.minted_asks:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _tally_lines(wc: WorkedCount) -> list[str]:
    lines = ["| Definition | Upper bound | Unknown inputs | Not in dossier |", "|---|---|---|---|"]
    for t in wc.tallies:
        lines.append(
            f"| {t.definition_id} | {t.upper_bound} | {t.unknown_inputs} | {t.not_in_dossier} |"
        )
    ceiling = wc.register_ceiling_a
    lines += [
        "",
        "A over the whole register: "
        + (
            f"at most {ceiling.kw} kW"
            if isinstance(ceiling, RegisterCeiling)
            else f"unknown ({ceiling.reason})"
        ),
    ]
    return lines


def _binding_text(table: BindingTable, term_id: str) -> tuple[str, str]:
    b = table.binding(term_id)
    if isinstance(b, FrozenContractField):
        ptrs = ", ".join(f"`{p.pointer}`" for p in b.pointers)
        points = (
            f" of points `{point_pattern(b.unit_suffix)}` ({b.unit_suffix.unit})"
            if isinstance(b.unit_suffix, UnitFromQuantity)
            else ""
        )
        return (
            "frozen contract field",
            f"{b.contract_id} `{b.schema_stem}` v{b.version} {ptrs}{points}: {b.derivation}",
        )
    if isinstance(b, DossierFieldBinding):
        via = (
            f" (IC-1 v{b.via.version} `{b.via.pointers[0].pointer}`)"
            if isinstance(b.via, FrozenContractField)
            else ""
        )
        return "dossier field", f"`{b.field.value}` `{b.value_path}`{via}: {b.derivation}"
    assert isinstance(b, NotYetMeasurable)
    contract = b.contract_id if isinstance(b.contract_id, str) else "no contract named"
    product = b.data_product if isinstance(b.data_product, str) else "product not named"
    wps = ", ".join(f"{w} ({roadmap_wp(w).title})" for w in b.roadmap_wps)
    return f"not yet measurable ({b.category})", f"{contract}; `{product}`; {wps}: needs {b.needs}"


def _definition_section(d: MwDefinition, table: BindingTable) -> list[str]:
    kpi = render_kpi_formula(d)
    lines = [
        f"### {d.letter}. {d.title} (`{d.definition_id}`)",
        "",
        f"- Quantity: {d.quantity}",
        f"- Template hash: `{d.content_hash()}`; binding hash: `{table.binding_hash()}`",
        "",
        "```text",
        render_text(d),
        "```",
        "",
        "KPI tracker keys, derived from the tree (slots shown as `<slot>`):",
        "",
        *(f"- **{k}**: {_md(v)}" for k, v in kpi.items()),
        "",
        "| Term | Unit | Grain | Time | Measurement point | Evidence class | Source kind | Source "
        "|",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in d.terms:
        kind, src = _binding_text(table, t.term_id)
        lines.append(
            f"| `{t.term_id}` | {t.unit.value} | {t.grain.value} | {t.time_base.value} | "
            f"{_md(t.measurement_point)} | {t.evidence_class} | {kind} | {_md(src)} |"
        )
    lines += ["", "| Slot | What it fixes | Options |", "|---|---|---|"]
    for s in d.slots:
        if isinstance(s, ChoiceSlot):
            opts = "; ".join(f"`{o.option_id}`: {o.label}" for o in s.options)
        else:
            assert isinstance(s, NumberSlot)
            opts = f"a number in {s.unit.value}, stated by the agreement"
        lines.append(f"| `{s.slot_id}` | {_md(s.label)} | {_md(opts)} |")
    for bad in d.inadmissible:
        combo = ", ".join(f"`{a}` = `{b}`" for a, b in bad.when)
        lines.append(f"\nNot admissible together: {combo}: {bad.reason}")
    lines.append("")
    return lines


def render_pack(
    worked_count: WorkedCount,
    definitions: tuple[MwDefinition, ...] = DEFINITIONS,
    bindings: dict[str, BindingTable] | None = None,
    register: GrantTextRegister = GRANT_TEXT_REGISTER,
) -> str:
    """The `demonstrator_mw_options` pack. Holds no site value: the worked count enters as its
    per-kind tallies only, so the pack is safe to track."""
    tables = BINDINGS if bindings is None else bindings
    index = definitions_index(definitions, tables)
    docs = {d.doc_id: d for d in register.documents}
    lines = [
        "# Demonstrator MW options (WP-OPS-11)",
        "",
        f"**{NOT_AGREED_STATEMENT}**",
        "",
        "Generated by `python -m gridq.ops.demonstrator_mw --write-pack` from "
        "`gridq.ops.mw_definitions`, `gridq.ops.mw_grant_text` and `gridq.ops.mw_draft`; a test "
        "fails if this file differs from the render. Decisions: D143 to D147 "
        "(`docs/decisions.md`).",
        "",
        f"- Definitions hash: `{index.content_hash()}`",
        f"- Grant-text register hash: `{register.content_hash()}`",
        f"- Roadmap pin: atlas `{ROADMAP_PIN.path}` at `{ROADMAP_PIN.atlas_commit}` "
        f"(sha256 `{ROADMAP_PIN.file_sha256}`)",
        "",
        "## 1. What the grant text says about the MW figure",
        "",
        "Quoted verbatim from the documents below (never copied into this repository); a test "
        "re-reads each document, checks its sha256 and finds every fragment. Fragments of one "
        "sentence split by the PDF's columns are joined with a space.",
        "",
        "| Document | Kind | Location (outside the repo) | sha256 |",
        "|---|---|---|---|",
    ]
    for d in register.documents:
        if d.kind == "not_held":
            continue
        assert isinstance(d.location, str) and isinstance(d.sha256, str)
        lines.append(f"| {_md(d.title)} | {d.kind} | `{d.location}` | `{d.sha256[:16]}` |")
    lines += ["", "| Quote | Source | Text | MW notion: figure |", "|---|---|---|---|"]
    for q in register.quotes:
        doc = docs[q.doc_id]
        figs = "; ".join(
            f"{f.notion}: {f.figure if isinstance(f.figure, str) else 'no figure'}"
            for f in q.figures
        )
        lines.append(
            f'| `{q.quote_id}` | {_md(doc.title)}, {_md(q.location)} | "{_md(q.text)}" | {figs} |'
        )
    lines += [
        "",
        "Internal meeting notes (`Kickoff Meeting.docx`) are context only and are not grant text.",
        "",
        "### Where the held application and the earlier draft differ",
        "",
    ]
    for dv in register.divergences:
        held = ", ".join(f"`{q}`" for q in dv.as_held)
        draft = ", ".join(f"`{q}`" for q in dv.draft) or "none"
        absent = "; ".join(f"{a.statement} (pattern `{a.pattern}`)" for a in dv.absences)
        lines.append(
            f"- **{dv.topic}**: held application {held}; draft {draft}"
            + (f"; {absent}" if absent else "")
            + f"; submitted version unknown (ask `{dv.ask_id[7:19]}`)."
        )
    lines += ["", "### Not held, and award-only figures", ""]
    for d in register.documents:
        if d.kind == "not_held":
            assert isinstance(d.ask_id, str)
            lines.append(f"- {_md(d.title)}: not held ({d.note}); ask `{d.ask_id[7:19]}`.")
    for a in register.award_figures:
        lines.append(
            f"- {a.award_ref} {a.label}: unknown ({a.value.reason}); ask `{a.ask_id[7:19]}`."
        )
    lines += [
        "",
        "### Words each quote shares with each candidate's name",
        "",
        "A lexical comparison over a fixed vocabulary, for the negotiation to read; it is not a "
        "judgement of fit.",
        "",
        "| Quote | Definition | Shared | Not in the text | Other concepts in the text |",
        "|---|---|---|---|---|",
    ]
    for r in concept_relations(register):
        lines.append(
            f"| `{r.quote_id}` | {r.definition_id} | {', '.join(r.shared) or '-'} | "
            f"{', '.join(r.not_stated_in_text) or '-'} | "
            f"{', '.join(r.other_concepts_in_text) or '-'} |"
        )
    lines += [
        "",
        "## 2. The candidate definitions",
        "",
        "Each candidate is an expression tree evaluated exactly (rational arithmetic). Every open "
        "parameter is a slot listing each admissible option, with no option filled in; a filling "
        "resolves to a concrete formula whose hash names that variant. A missing reading is a "
        "gap, never zero. The three share the slots for window, resolution, statistic, "
        "coincidence, gaps, quality and authority, and the conditions on control authority and "
        "the dispatch envelope.",
        "",
        "That A (`agreed_capacity` basis) and B (`rated` basis) can be read from documents today, "
        "while C needs "
        "telemetry, pre-registered baselines and the verifier interface, reflects what is "
        "measurable now. It is not a statement of merit.",
        "",
    ]
    for definition in definitions:
        lines += _definition_section(definition, tables[definition.definition_id])
    lines += [
        "## 3. Worked count on the candidate sites",
        "",
        "Per site and definition: an upper bound in kW from evidenced dossier fields (with the "
        "conditions under which it holds and the inputs it leaves out), unknown with the missing "
        "field and its asks, or not in the dossier with the inputs that will supply it. The "
        "per-site file names real sites and is written only to git-ignored `dossiers/` by "
        "`python -m gridq.ops.demonstrator_mw --as-of <date>`.",
        "",
        f"- A: {_A_INEQUALITY} Holds while: {'; '.join(_A_HOLDS_WHILE)}.",
        f"- B: {_B_INEQUALITY} Holds while: {'; '.join(_B_HOLDS_WHILE)}.",
        "- C: not in the dossier for any site.",
        "",
        f"Tallies for the register as built on {worked_count.as_of.isoformat()} with no customer "
        f"data released (dossier `{worked_count.dossier_hash}`):",
        "",
        *_tally_lines(worked_count),
        "",
        "## 4. Asks to the owner",
        "",
    ]
    for ask in register.asks:
        lines.append(f"- `{ask.ask_id[7:19]}`: {ask.request}")
    lines += [
        "- Data processing agreements and connection documents for the candidate sites: the "
        "worked count's A and B cells stay unknown until the WP-OPS-01 dossier asks close.",
        "",
        "## 5. Draft agreement text",
        "",
        "````markdown",
        render_draft_agreement(definitions, tables, register).rstrip("\n"),
        "````",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Writing and the CLI
# --------------------------------------------------------------------------------------------


def write_worked_count(wc: WorkedCount, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir).resolve()
    stem = f"demonstrator_mw_worked_count_{wc.as_of.isoformat()}_{wc.content_hash()[7:19]}"
    json_path, md_path = out / f"{stem}.json", out / f"{stem}.md"
    if wc.has_real_site:
        for p in (json_path, md_path):
            _require_git_ignored(p)
    out.mkdir(parents=True, exist_ok=True)
    json_path.write_text(worked_count_json(wc), encoding="utf-8")
    md_path.write_text(render_worked_count(wc), encoding="utf-8")
    return json_path, md_path


def pack_worked_count(as_of: date, empty_root: Path) -> WorkedCount:
    """The worked count the tracked pack reports: the default register, nothing released.

    The evidence registry is pinned to ``empty_root`` for the build (and restored after), so the
    pack is the same on every machine whatever a local evidence directory holds.
    """
    before = os.environ.get(EVIDENCE_DIR_ENV)
    os.environ[EVIDENCE_DIR_ENV] = str(empty_root)
    try:
        dossier = build_dossier(
            default_register(empty_root / "no_register.json"),
            as_of=as_of,
            evidence_root=empty_root,
        )
    finally:
        if before is None:
            os.environ.pop(EVIDENCE_DIR_ENV, None)
        else:
            os.environ[EVIDENCE_DIR_ENV] = before
    return build_worked_count(dossier)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gridq.ops.demonstrator_mw",
        description="Build the demonstrator MW worked count (WP-OPS-11) into a git-ignored "
        "directory, or render the options pack.",
    )
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--out", type=Path, default=repo_root() / "dossiers")
    parser.add_argument("--register", type=Path, default=None)
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--write-pack", type=Path, default=None)
    parser.add_argument("--pack-empty-root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.write_pack is not None:
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                root = args.pack_empty_root or Path(tmp)
                wc = pack_worked_count(args.as_of, root)
            args.write_pack.write_text(render_pack(wc), encoding="utf-8")
            print(f"pack: {args.write_pack}")
            return 0
        dossier = build_dossier(
            default_register(args.register), as_of=args.as_of, evidence_root=args.evidence_dir
        )
        wc = build_worked_count(dossier)
        json_path, md_path = write_worked_count(wc, args.out)
    except DossierWriteRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (DossierInputError, ValueError) as exc:
        print(f"input error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"sites: {len(wc.sites)}")
    for t in wc.tallies:
        print(
            f"{t.definition_id}: upper_bound={t.upper_bound} unknown_inputs={t.unknown_inputs} "
            f"not_in_dossier={t.not_in_dossier}"
        )
    print(f"minted_asks: {len(wc.minted_asks)}")
    print(f"dossier_hash: {wc.dossier_hash}")
    print(f"content_hash: {wc.content_hash()}")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
