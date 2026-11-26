"""Generate the BESS modelling requirements checklist + constants register from the survey output.

The survey ran repo-blind (agents were forbidden from reading this codebase, so the checklist
states what the LITERATURE requires, not what we happen to have built). This script turns the
structured agent output into the two reference documents, deterministically — nothing is
paraphrased, every sourced number and citation is carried through verbatim.

Run: `python scripts/build_literature_docs.py`
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARVEST = Path(
    "/private/tmp/claude-501/-Users-oliverpugh-Desktop-Karim-Project-Neura-GitHub/"
    "8c3c87c3-f311-4e3d-9be8-83ada586d247/scratchpad/harvest/lit_all.json"
)
DOCS = REPO / "docs"

# confidence -> obligation. Stated in the document so the mapping can be challenged.
OBLIGATION = {
    "settled": "MUST",
    "majority-view": "SHOULD",
    "contested": "CONSIDER",
    "emerging": "CONSIDER",
}

SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("A", "Data requirements and admission", ("data-requirements",)),
    ("B", "MILP formulation and physics", ("milp-formulation", "physics")),
    ("C", "Degradation, lifetime and warranty", ("degradation",)),
    ("D", "Revenue streams — GB market mechanics", ("market-mechanics",)),
    ("E", "Revenue stacking, conflicts and mutual exclusivity", ("revenue-stacking",)),
    ("F", "Network charges, levies and avoided cost", ("network-charges",)),
    ("G", "Contracts, route to market and eligibility", ("contracts",)),
    ("H", "Risk quantification, P50/P90 and bankability", ("risk-and-finance",)),
]


def _slug(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:120]


def main() -> None:
    lenses = json.loads(HARVEST.read_text())

    reqs: list[dict] = []
    seen: set[str] = set()
    for lens in lenses:
        for r in lens.get("requirements", []):
            key = _slug(r.get("requirement", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            reqs.append({**r, "_lens": lens.get("lens", "?")})

    consts: list[dict] = []
    seen_c: set[str] = set()
    for lens in lenses:
        for c in lens.get("constants", []):
            key = _slug(f"{c.get('name')}|{c.get('value')}|{c.get('units')}")
            if key in seen_c:
                continue
            seen_c.add(key)
            consts.append({**c, "_lens": lens.get("lens", "?")})

    sources = sorted({s for lens in lenses for s in lens.get("sources", []) if s.strip()})
    open_qs = [q for lens in lenses for q in lens.get("open_questions", [])]

    by_section: dict[str, list[dict]] = defaultdict(list)
    for r in reqs:
        cat = (r.get("category") or "").strip().lower()
        placed = False
        for letter, _, cats in SECTIONS:
            if cat in cats:
                by_section[letter].append(r)
                placed = True
                break
        if not placed:
            by_section["D"].append(r)  # market mechanics is the natural home for the residue

    _write_checklist(by_section, reqs, sources, open_qs, len(lenses))
    _write_constants(consts, sources)


def _write_checklist(by_section, reqs, sources, open_qs, n_lenses) -> None:
    out: list[str] = []
    w = out.append
    w("# What a GB behind-the-meter BESS model must contain")
    w("")
    w("*Derived from the literature, independently of any implementation.*")
    w("")
    w("## 0. How to use this checklist, and how it was built")
    w("")
    w("This is an **audit instrument**. Each row states something a correct GB behind-the-meter")
    w("battery model must, should, or might do, with the source that establishes it and the error")
    w("that follows from omitting it. An implementation is audited against it **row by row**: an")
    w("item the pipeline does not satisfy is either implemented, or recorded as a known limitation")
    w("that caps the confidence band. It is never silently absent.")
    w("")
    w("**It was built repo-blind, deliberately.** The surveying agents were forbidden from reading")
    w("this codebase, any of its documentation, or any prior analysis. The reason is confirmation")
    w("bias: a reviewer who can see the existing implementation tends to write a checklist that")
    w("ratifies what was built and stays silent about what was omitted — precisely ")
    w("the failure this")
    w("document exists to prevent. Every requirement therefore describes what the *literature*")
    w("requires, with no knowledge of what we happen to have.")
    w("")
    w("**Obligation levels** are derived from the strength of the evidence: `settled` in the")
    w("literature → **MUST**; `majority-view` → **SHOULD**; `contested` or `emerging` →")
    w("**CONSIDER**. The mapping is stated so it can be challenged.")
    w("")
    w("### Coverage and its gap")
    w("")
    w(f"- **{len(reqs)} distinct requirements** and **{len(sources)} sources**, from")
    w(f"  {n_lenses} independent research lenses plus a critic panel and a ")
    w("gap-filling second sweep.")
    w("- **The degradation lens did not complete** (an API failure mid-run). ")
    w("Section C is therefore")
    w("  populated only by degradation requirements that surfaced incidentally in ")
    w("other lenses, and")
    w("  is **known-incomplete**. Cycle-life, calendar ageing, rainflow counting, warranty")
    w("  throughput caps and augmentation economics need a dedicated pass before ")
    w("this checklist can")
    w("  be called complete.")
    w("- Three critic angles and one gap-filling sweep completed; three further gap batches were")
    w("  lost to a usage limit, so the second sweep is partial.")
    w("")
    w("---")
    w("")
    w("## 1. The checklist")
    w("")
    for letter, title, _ in SECTIONS:
        items = by_section.get(letter, [])
        w(f"### {letter}. {title}")
        w("")
        if not items:
            w("*No requirements captured in this section — see the coverage gap above.*")
            w("")
            continue
        order = {"MUST": 0, "SHOULD": 1, "CONSIDER": 2}
        items.sort(key=lambda r: order.get(OBLIGATION.get(r.get("confidence", ""), "CONSIDER"), 9))
        for i, r in enumerate(items, 1):
            rid = f"{letter}-{i:02d}"
            ob = OBLIGATION.get(r.get("confidence", ""), "CONSIDER")
            w(f"#### {rid} · {ob} · {r.get('requirement', '').strip()}")
            w("")
            if r.get("why"):
                w(f"- **Why:** {r['why'].strip()}")
            if r.get("consequence_if_omitted"):
                w(f"- **Error if omitted:** {r['consequence_if_omitted'].strip()}")
            if r.get("implementable_as"):
                w(f"- **How to implement:** {r['implementable_as'].strip()}")
            for s in r.get("sources", []) or []:
                w(f"- *Source:* {s.strip()}")
            w("")
        w("")

    if open_qs:
        w("---")
        w("")
        w("## 2. What the literature does not settle")
        w("")
        for q in open_qs:
            w(f"- {q.strip()}")
        w("")

    w("---")
    w("")
    w("## 3. Source bibliography")
    w("")
    for s in sources:
        w(f"- {s.strip()}")
    w("")

    path = DOCS / "bess_modelling_requirements.md"
    path.write_text("\n".join(out))
    print(f"  {path.relative_to(REPO)}  ({len(reqs)} requirements, {len(sources)} sources)")


def _write_constants(consts, sources) -> None:
    out: list[str] = []
    w = out.append
    w("# BESS constants register — sourced numbers for GB behind-the-meter modelling")
    w("")
    w("Every reusable number the literature survey captured, with its units, the conditions it")
    w("applies under, its vintage and its source. Built repo-blind (see")
    w("[`bess_modelling_requirements.md`](bess_modelling_requirements.md) §0).")
    w("")
    w("**Rules for using this register.**")
    w("")
    w("- A number is only usable with its **basis**. Most errors here come from taking a value")
    w("  whose conditions do not match the case — a de-rating factor from the ")
    w("wrong auction year, a")
    w("  skip rate from the wrong methodology stage, a capture rate from a different duration.")
    w("- Where sources **conflict**, both rows are kept. Nothing is averaged.")
    w("- **Vintages matter enormously in GB.** The Targeted Charging Review, the ")
    w("frequency-response")
    w("  product set and the Open Balancing Platform all changed the answer within the last few")
    w("  years, and BM skip rates have roughly halved between 2023 and 2026. A number without a")
    w("  vintage is not usable.")
    w("")
    w("---")
    w("")
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in consts:
        groups[str(c.get("_lens", "other"))].append(c)
    for lens in sorted(groups):
        w(f"## {lens}")
        w("")
        w("| Constant | Value | Units | Basis / conditions | Source | Caveat |")
        w("|---|---|---|---|---|---|")
        for c in groups[lens]:

            def cell(v: object) -> str:
                return str(v or "—").replace("|", "\\|").replace("\n", " ").strip()

            w(
                f"| {cell(c.get('name'))} | {cell(c.get('value'))} | {cell(c.get('units'))} "
                f"| {cell(c.get('basis'))} | {cell(c.get('source'))} | {cell(c.get('caveat'))} |"
            )
        w("")

    path = DOCS / "bess_constants_register.md"
    path.write_text("\n".join(out))
    print(f"  {path.relative_to(REPO)}  ({len(consts)} constants)")


if __name__ == "__main__":
    main()
