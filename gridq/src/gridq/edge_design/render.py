"""Render the design document from the released design, never by hand (D154).

``python -m gridq.edge_design --check`` fails when the committed document or release JSON differs
from a fresh render; ``--write`` regenerates both.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from gridq.contracts._base import NotApplicable, Unknown
from gridq.contracts.edge_cloud import ControlStage
from gridq.edge_design.model import PURDUE_ORDER, BmsConduitMode, Conduit, NoWrite, WriteScope
from gridq.edge_design.public_sources import FR_NAMES
from gridq.edge_design.release import DESIGN, DESIGN_VERSION, EdgeZoneConduitDesign, design_hash
from gridq.edge_design.review import DISCLAIMER, csf_witness
from gridq.edge_design.site import DESIGN_ASKS

__all__ = ["DOC_PATH", "render_doc"]

REPO: Final = Path(__file__).resolve().parents[3]
DOC_PATH: Final = REPO / "docs" / "design" / f"edge_zone_conduit_design.v{DESIGN_VERSION}.md"

PERSON_ONLY_ITEMS: Final = (
    "Purchase IEC 62443-3-3 (and IEC 62443-3-2) so a person can check this review against the "
    "full requirement text and the SL 2 requirement enhancements (closes GAP-62443-00).",
    "Send the host, DNO, OEM, meter operator, partner and ThermaFlex data asks below for each "
    "site; no site fact is assumed.",
    "Agree the ThermaFlex controlled interface (AWAIT-19) before any C6 conduit is built.",
)

B1_07_NOTE: Final = (
    'Spec B1-07 reads "unidirectional gateway to the facility BMS where the customer requires '
    'it. No data centre will allow BMS writes without it." A unidirectional gateway that only '
    "lets data out of the BMS cannot carry a write in, so the two sentences cannot both hold "
    "for a write path. This design does not reword the spec. It makes the diode the default "
    "(`diode_read_only`, no chiller authority, L2 at most) and reaches L3 through "
    "`diode_with_band_offer`: two opposed one-way paths, the inbound one carrying only a chiller "
    "band offer that the host's own BMS logic adopts or ignores (D156). The spec "
    "owner should confirm the reading."
)


def _cell(text: object) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _table(head: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    return [_row(head), _row("---" for _ in head), *(_row(r) for r in rows), ""]


def _codes(items: Iterable[object]) -> str:
    return ", ".join(f"`{getattr(i, 'value', i)}`" for i in items)


def _direction(c: Conduit) -> str:
    flow = {"a_to_b": f"{c.zone_a} → {c.zone_b}", "b_to_a": f"{c.zone_b} → {c.zone_a}"}
    shown = flow.get(c.data_flow, "both ways")
    return f"opened by {c.side(c.initiator)}; data {shown}"


def _authority(c: Conduit) -> str:
    a = c.authority
    if isinstance(a, NoWrite):
        return f"no write ({a.reason})"
    assert isinstance(a, WriteScope)
    target = c.side("b" if a.writer == "a" else "a")
    extra = ", tighten only" if a.tighten_only else ""
    case = ", B1-09 safety case" if a.requires_safety_case else ""
    return (
        f"{c.side(a.writer)} writes {_codes(a.point_classes)} into {target} from "
        f"`{a.min_stage.value}`{extra}{case}"
    )


def _loss(c: Conduit) -> str:
    t = c.loss_trigger
    return f"n/a: {t.reason}" if isinstance(t, NotApplicable) else f"`{t.value}`"


def _mermaid(d: EdgeZoneConduitDesign) -> list[str]:
    bands = (
        ("ent", "Levels 4 and 5: enterprise and off site", 5),
        ("dmz", "Level 3.5: site DMZ", 4),
        ("ops", "Levels 2 and 3: site operations", 3),
        ("ctl", "Levels 1 and 2: control and field devices", 2),
    )
    out = ["```mermaid", "flowchart TB"]
    for key, label, top in bands:
        out.append(f'  subgraph {key}["{label}"]')
        for z in d.zones:
            zt = PURDUE_ORDER[z.level_top]
            band = 5 if zt >= 5 else 4 if zt == 4 else 3 if zt == 3 else 2
            if band == top:
                out.append(f'    {z.zone_id}["{z.name}"]')
        out.append("  end")
    default = next(v for v in d.bms_variants if v.mode is d.default_bms_mode)
    for c in (*d.base_conduits, *default.conduits):
        arrow = {"a_to_b": "-->", "b_to_a": "<--", "both": "<-->"}[c.data_flow]
        out.append(f"  {c.zone_a} {arrow}|{c.conduit_id}| {c.zone_b}")
    out += ["```", ""]
    return out


def _stage_rows(d: EdgeZoneConduitDesign) -> list[list[object]]:
    from gridq.edge_design.query import write_bindings

    rows: list[list[object]] = []
    for mode in BmsConduitMode:
        for stage in ControlStage:
            got = write_bindings(stage, mode)
            shown = "; ".join(
                f"{b.conduit_id} → {b.target_zone}: `{b.point_class.value}`" for b in got
            )
            rows.append([f"`{mode.value}`", f"`{stage.value}`", shown or "nothing"])
    return rows


def render_doc(d: EdgeZoneConduitDesign = DESIGN) -> str:
    r = d.review
    out: list[str] = [
        f"# Edge zone and conduit design v{d.version} (WP-EDGE-02, spec B1-07)",
        "",
        "Generated by `python -m gridq.edge_design --write` from `gridq.edge_design`. Do not edit "
        "by hand: `--check` fails on any difference.",
        "",
        f"> {DISCLAIMER}",
        "",
    ]
    out += _table(
        ("Item", "Value"),
        [
            ("Release", f"`{d.release}` v{d.version}"),
            ("Design hash", f"`{design_hash(d)}`"),
            ("Release file", f"`src/gridq/edge_design/release/{d.release}.v{d.version}.json`"),
            ("Checked against", f"IC-5 edge-cloud contract {d.ic5_contract_version}"),
            ("Reference BMS mode", f"`{d.default_bms_mode.value}`"),
            ("Security level target", f"SL-T {r.status.sl_target} (Neura's design target)"),
            ("Security level achieved", f"unknown: {r.status.sl_achieved.reason}"),
            ("Certified", "no: this review is not certified"),
            ("Conformity assessment", "no: this is not a conformity assessment"),
            ("Review basis", "public SR identifiers and titles only"),
        ],
    )
    out += [
        "## What this is",
        "",
        "A generic reference design for one Neura site: zones on the Purdue model, typed "
        "conduits between them, a unidirectional gateway (data diode) option towards the "
        "facility BMS, and named invariants that every BMS mode must satisfy. It describes no "
        "real facility: a site binds to it by hash with every site fact unknown until supplied "
        "(see the data asks). The design is machine-readable; later edge packages test against "
        "`gridq.edge_design`, not this page.",
        "",
        "## Sources",
        "",
        "Only openly published material, cited by URL with the SHA-256 of the bytes retrieved. "
        "No requirement text of IEC 62443-3-3 is reproduced.",
        "",
    ]
    out += _table(
        ("Source", "Publisher", "URL", "Retrieved", "SHA-256", "Provides", "Lacks"),
        [
            (
                s.title,
                s.publisher,
                s.url,
                s.retrieved_on.isoformat(),
                f"`{s.sha256}`",
                "; ".join(s.provides),
                "; ".join(s.lacks),
            )
            for s in d.sources
        ],
    )
    out += ["## A note on spec B1-07", "", B1_07_NOTE, ""]
    out += ["## Zones", ""]
    out += _table(
        (
            "Zone",
            "Name",
            "Purdue levels",
            "Responsible",
            "On site",
            "Protocol break",
            "Assets",
            "Zone controls",
            "Purpose",
        ),
        [
            (
                f"`{z.zone_id}`",
                z.name,
                f"{z.level_top.value} to {z.level_bottom.value}",
                z.responsible.value,
                "yes" if z.on_site else "no",
                "yes" if z.protocol_break else "no",
                "; ".join(z.assets),
                _codes(z.controls),
                z.purpose,
            )
            for z in d.zones
        ],
    )
    out += ["## Diagram (reference mode)", ""]
    out += _mermaid(d)
    out += [
        "## Conduits",
        "",
        "Every conduit lists its protocols, its direction (who opens it, which way data flows, "
        "who may write what) and its security controls. A conduit missing any of them cannot be "
        "built, and one whose controls do not cover the FRs its attributes demand is refused.",
        "",
    ]
    rows = []
    modes_of: dict[str, list[str]] = defaultdict(list)
    for v in d.bms_variants:
        for c in v.conduits:
            modes_of[c.conduit_id].append(v.mode.value)
    for c in d.all_conduits():
        where = ", ".join(f"`{m}`" for m in modes_of[c.conduit_id]) or "all modes"
        rows.append(
            (
                f"`{c.conduit_id}`",
                c.name,
                f"{c.zone_a} ↔ {c.zone_b}",
                where,
                _codes(c.protocols),
                _direction(c),
                _authority(c),
                _codes(c.payloads),
                _codes(c.controls),
                _loss(c),
                c.purpose,
            )
        )
    out += _table(
        (
            "Id",
            "Name",
            "Zones",
            "Modes",
            "Protocols",
            "Direction",
            "Authority",
            "Payloads",
            "Controls",
            "IC-5 loss trigger",
            "Purpose",
        ),
        rows,
    )
    out += ["## BMS modes", ""]
    out += _table(
        ("Mode", "Conduits", "Highest IC-5 stage", "Summary", "Caveat"),
        [
            (
                f"`{v.mode.value}`" + (" (reference)" if v.mode is d.default_bms_mode else ""),
                ", ".join(c.conduit_id for c in v.conduits) or "none",
                f"`{v.max_stage.value}`",
                v.summary,
                v.caveat.reason if isinstance(v.caveat, NotApplicable) else v.caveat,
            )
            for v in d.bms_variants
        ],
    )
    out += ["## Control vocabulary", "", "Neura's own mapping, not requirement text.", ""]
    out += _table(
        ("Control", "Scope", "FRs", "SRs (title level)", "NIST CSF 1.1", "Meaning"),
        [
            (
                f"`{e.kind.value}`",
                e.spec.scope,
                ", ".join(str(f) for f in e.spec.frs),
                ", ".join(e.spec.sr_ids),
                ", ".join(e.spec.csf),
                e.spec.meaning,
            )
            for e in d.controls
        ],
    )
    out += [
        "## Invariants",
        "",
        "Checked in every BMS mode when the design is built; a violation refuses the release.",
        "",
    ]
    out += _table(("Invariant", "Statement"), [(f"`{i.name}`", i.statement) for i in d.invariants])
    out += [
        "## Consistency with IC-5 and the write allow-list",
        "",
        "What Neura's side may write, by granted IC-5 stage and BMS mode "
        "(`query.write_bindings`). WP-EDGE-03's register allow-list must stay inside this set. "
        "The DNO's ANM signal is received at every stage and never leaves the site (D119).",
        "",
    ]
    out += _table(("BMS mode", "Granted stage", "May write"), _stage_rows(d))
    out += [
        "## SL 2 review",
        "",
        f"> {DISCLAIMER}",
        "",
        "Dispositions: `title_level_mapped` (elements and a test address the SR as its title "
        "names it; only the global gap remains), `partially_mapped` (elements and a test, plus "
        "a named gap), `deferred_to_wp` (a later work package owns a named gap), "
        "`host_responsibility` (the facility's, raised as a data ask). The CSF column is NIST's "
        "own list of CSF 1.1 subcategories citing the SR; the witness says whether the design "
        "elements' subcategories meet it.",
        "",
    ]
    counts: dict[str, int] = defaultdict(int)
    for s in r.srs:
        counts[s.disposition] += 1
    out += _table(("Disposition", "SRs"), sorted(counts.items()))
    for fr, name in FR_NAMES.items():
        out += [f"### FR {fr}: {name}", "", f"> {DISCLAIMER}", ""]
        fr_rows: list[tuple[str, ...]] = []
        for s in r.srs:
            if s.fr != fr:
                continue
            w = csf_witness(s)
            fr_rows.append(
                (
                    s.sr_id,
                    s.title,
                    f"`{s.disposition}`",
                    ", ".join(f"`{e}`" for e in s.design_elements) or "none",
                    ", ".join(f"`{e}`" for e in s.evidence),
                    ", ".join(s.csf_1_1) or "not cited",
                    "n/a" if w is None else ("meets" if w else "misses"),
                    ", ".join(s.gap_ids),
                    s.note,
                )
            )
        out += _table(
            (
                "SR",
                "Public title",
                "Disposition",
                "Design elements",
                "Evidence",
                "NIST CSF 1.1",
                "Witness",
                "Gaps",
                "Neura's note",
            ),
            fr_rows,
        )
    out += ["## Gaps by owner", ""]
    by_owner: dict[str, list[str]] = defaultdict(list)
    for g in r.gaps:
        by_owner[g.owner].append(g.gap_id)
    out += _table(
        ("Gap", "Owner", "Kind", "SRs", "Statement", "Closes when"),
        [
            (g.gap_id, g.owner, g.kind, ", ".join(g.sr_ids), g.statement, g.closes_when)
            for owner in sorted(by_owner)
            for g in r.gaps
            if g.owner == owner
        ],
    )
    out += ["## Data asks", "", "No site fact is assumed. Each is asked of its holder.", ""]
    out += _table(
        ("Ask", "Holder", "Affects", "Fact needed", "Request"),
        [(f"`{a.ask_id}`", a.holder, ", ".join(a.affects), a.fact, a.request) for a in DESIGN_ASKS],
    )
    out += ["## Person-only items", ""]
    out += [f"- {p}" for p in PERSON_ONLY_ITEMS]
    out += [
        "",
        f"Full-text review: {_unknown_text(r.status.full_text_review)}.",
        "",
    ]
    return "\n".join(out)


def _unknown_text(u: Unknown) -> str:
    return f"not done ({u.reason})"
