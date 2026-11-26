"""A draft of the demonstrator-MW agreement text, for the owner to negotiate (WP-OPS-11).

Rendered, never authored by hand (D146): the three options are written from the typed
definitions in fixed letter order with identical clause structure, so the draft cannot describe a
formula other than the one in `gridq.ops.mw_definitions`, and changing a definition changes the
text. Every party, date, reference and figure is a typed blank (``[[key]]``). The text opens and
closes with `DRAFT_BANNER`, says `NOT_AGREED_STATEMENT`, and has unsigned signature blocks. It
names no option and creates no evidence object: on execution, WP-OPS-02 registers the executed text
as IC-4 ``method_agreement`` objects (the registration map below says how).
"""

from __future__ import annotations

from typing import Final

from gridq.contracts._base import ContractModel, NonEmptyStr
from gridq.ops.mw_bindings import (
    BindingTable,
    DossierFieldBinding,
    FrozenContractField,
    UnitFromQuantity,
    point_pattern,
)
from gridq.ops.mw_definitions import BINDINGS, DEFINITIONS
from gridq.ops.mw_formula import (
    ChoiceSlot,
    MwDefinition,
    NumberSlot,
    render_kpi_formula,
    render_text,
)
from gridq.ops.mw_grant_text import GRANT_TEXT_REGISTER, GrantTextRegister

__all__ = [
    "BLANKS",
    "DRAFT_BANNER",
    "DRAFT_TITLE",
    "NOT_AGREED_STATEMENT",
    "Blank",
    "render_draft_agreement",
]

DRAFT_BANNER: Final = "DRAFT FOR NEGOTIATION. NOT AGREED. NOT FOR SIGNATURE IN THIS FORM."
DRAFT_TITLE: Final = "Demonstrator MW: draft agreement text (WP-OPS-11, for WP-OPS-02)"
NOT_AGREED_STATEMENT: Final = (
    "Not agreed; no option is recommended; agreement is WP-OPS-02, the owner's to negotiate."
)

#: The quotes the draft's recital cites (held application, then competition page).
_RECITAL_QUOTES: Final = (
    "app_demonstrator_pilot",
    "app_live_validation_target",
    "app_window",
    "comp_2gw_target",
)


class Blank(ContractModel):
    """A place in the draft only the negotiation can fill."""

    key: NonEmptyStr
    label: NonEmptyStr


BLANKS: Final = (
    Blank(key="party.funder", label="Funder (Innovate UK, as the grant offer names it)"),
    Blank(key="party.research_partner", label="Research partner and independent verifier (PSAL)"),
    Blank(key="party.hosts", label="Each host site's legal entity"),
    Blank(key="party.neura", label="Neura contracting entity"),
    Blank(key="project.number", label="Project number (award register AW-01; unknown)"),
    Blank(key="mw.figure", label="The demonstrator MW figure this agreement measures"),
    Blank(key="window.dates", label="First and last day of the demonstrator window"),
    Blank(key="option.letter", label="The option the parties settle on (A, B or C)"),
    Blank(key="option.slots", label="The option's slot fillings, one per slot"),
    Blank(key="citation", label="Where the executed text states the definition (clause)"),
    Blank(key="date.execution", label="Date of execution"),
)


def _b(key: str) -> str:
    if key not in {b.key for b in BLANKS}:
        raise KeyError(key)
    return f"[[{key}]]"


def _slot_lines(d: MwDefinition) -> list[str]:
    lines: list[str] = []
    for s in d.slots:
        if isinstance(s, ChoiceSlot):
            opts = "; ".join(f"`{o.option_id}`: {o.label}" for o in s.options)
            lines.append(f"  - `{s.slot_id}` ({s.label}): one of {opts}.")
        elif isinstance(s, NumberSlot):
            lines.append(f"  - `{s.slot_id}` ({s.label}, {s.unit.value}): a number to state.")
    for bad in d.inadmissible:
        combo = ", ".join(f"`{sid}` = `{oid}`" for sid, oid in bad.when)
        lines.append(f"  - Not admissible together: {combo} ({bad.reason}).")
    return lines


def _source_text(table: BindingTable, term_id: str) -> str:
    b = table.binding(term_id)
    if isinstance(b, FrozenContractField):
        if isinstance(b.unit_suffix, UnitFromQuantity):
            return (
                f"{b.contract_id} {b.schema_stem} v{b.version}, points "
                f"`{point_pattern(b.unit_suffix)}`"
            )
        return f"{b.contract_id} {b.schema_stem} v{b.version}"
    if isinstance(b, DossierFieldBinding):
        return f"candidate site dossier field `{b.field.value}`"
    product = b.data_product if isinstance(b.data_product, str) else "to be named"
    return f"not yet measurable ({b.category}; {product}; {', '.join(b.roadmap_wps)})"


def _option(d: MwDefinition, table: BindingTable) -> list[str]:
    kpi = render_kpi_formula(d)
    lines = [
        f"### Option {d.letter}: {d.title}",
        "",
        f"{d.letter}.1 Quantity. {d.quantity}, in kW, reported in MW to three decimal places.",
        f"{d.letter}.2 Formula. The figure is the value of the formula below, with each slot "
        "filled as clause 4 records:",
        "",
        "```text",
        render_text(d),
        "```",
        "",
        f"{d.letter}.3 Terms and their sources:",
    ]
    for t in d.terms:
        lines.append(
            f"  - `{t.term_id}` ({t.unit.value}, {t.grain.value}, {t.time_base.value}): "
            f"{t.label}; measured at {t.measurement_point}; evidence class {t.evidence_class}; "
            f"source: {_source_text(table, t.term_id)}."
        )
    lines += [
        f"{d.letter}.4 Slots the parties fill (each has the options listed and no other):",
        *_slot_lines(d),
        f"{d.letter}.5 Formula candidate record (the KPI tracker's keys):",
        "",
        "```yaml",
        "formula_candidate:",
        "  status: candidate_not_agreed",
        f"  definition_id: {d.definition_id}",
        f"  template_hash: {d.content_hash()}",
        f"  binding_hash: {table.binding_hash()}",
        *(f"  {k}: {v!r}" for k, v in kpi.items()),
        f"  citation: {_b('citation')}",
        "```",
        "",
    ]
    return lines


def render_draft_agreement(
    definitions: tuple[MwDefinition, ...] = DEFINITIONS,
    bindings: dict[str, BindingTable] | None = None,
    register: GrantTextRegister = GRANT_TEXT_REGISTER,
) -> str:
    """The draft text. Deterministic: the same definitions give the same bytes."""
    tables = BINDINGS if bindings is None else bindings
    letters = [d.letter for d in definitions]
    if letters != sorted(letters):
        raise ValueError("options are rendered in fixed letter order")
    recital = []
    for qid in _RECITAL_QUOTES:
        q = register.quote(qid)
        doc = register.document(q.doc_id)
        recital.append(f'  - "{q.text}" ({doc.title}, {q.location}).')
    lines = [
        DRAFT_BANNER,
        "",
        f"# {DRAFT_TITLE}",
        "",
        f"**{NOT_AGREED_STATEMENT}**",
        "",
        "## 1. Parties",
        "",
        f"1.1 {_b('party.funder')} (the funder).",
        f"1.2 {_b('party.research_partner')} (the research partner, acting as independent "
        "verifier).",
        f"1.3 {_b('party.hosts')} (each a host).",
        f"1.4 {_b('party.neura')} (Neura).",
        "",
        "## 2. Recital",
        "",
        f"2.1 Project {_b('project.number')} states a demonstrator MW figure. The texts the "
        f"parties "
        "hold say:",
        *recital,
        f"2.2 This agreement fixes how {_b('mw.figure')} is measured over {_b('window.dates')}, so "
        "that every party can recompute it from the same data.",
        "",
        "## 3. Options under negotiation",
        "",
        "3.1 The three options below are set out in the order spec §15.3 names them. Each is a "
        "formula a verifier can recompute: quantity, measurement point, averaging period and "
        "qualifying conditions. The order carries no weight.",
        "",
    ]
    for d in definitions:
        lines += _option(d, tables[d.definition_id])
    lines += [
        "## 4. Settled terms",
        "",
        f"4.1 Option: {_b('option.letter')}.",
        f"4.2 Slot fillings, one per slot of that option: {_b('option.slots')}.",
        "4.3 A slot not filled here leaves the figure unknown; no party may supply a value later "
        "without a written amendment.",
        "",
        "## 5. Measurement, gaps and verification",
        "",
        "5.1 A missing reading is a gap, never zero. The gap policy filled at clause 4 decides "
        "whether a figure exists.",
        "5.2 Terms marked 'not yet measurable' are measured by the roadmap products named against "
        "them; the parties will confirm each source field before the window opens.",
        "5.3 The independent verifier recomputes the figure from the dataset manifest and states "
        "whether it reproduces.",
        "",
        "## 6. Registration (WP-OPS-02)",
        "",
        "6.1 On execution, the executed text is hashed once. Each of the funder, the research "
        "partner and every host issues an IC-4 evidence object of type `method_agreement` naming "
        "that one document hash. The KPI tracker reads the formula only when all of them exist.",
        "6.2 This draft is not that document, and its hash is not that hash.",
        "",
        "## 7. Signature blocks",
        "",
    ]
    for role, key in (
        ("Funder", "party.funder"),
        ("Research partner (independent verifier)", "party.research_partner"),
        ("Host", "party.hosts"),
        ("Neura", "party.neura"),
        ("Witness: independent verifier", "party.research_partner"),
    ):
        lines += [
            f"- {role}: {_b(key)}",
            "  - Name and position: ____________________",
            f"  - Date: {_b('date.execution')}",
            "  - Signature status: unknown (unsigned draft)",
        ]
    lines += ["", DRAFT_BANNER, ""]
    return "\n".join(lines)
