"""WP-EDGE-02 acceptance 2: the design is reviewed against the IEC 62443-3-3 SL 2 requirements,
identified by their public identifiers and titles, with the gaps listed. The review can never
claim to be certified or a conformity assessment."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from _edge_support import ROOT
from pydantic import ValidationError

from gridq.edge_design.model import BmsConduitMode
from gridq.edge_design.public_sources import (
    CSF_1_1_BY_SR,
    FR_NAMES,
    PUBLIC_SOURCES,
    SR_CATALOGUE,
)
from gridq.edge_design.release import DESIGN
from gridq.edge_design.render import render_doc
from gridq.edge_design.review import (
    DISCLAIMER,
    GLOBAL_GAP,
    REVIEW,
    ReviewStatus,
    Sl2Review,
    SrReview,
    csf_witness,
)
from gridq.edge_design.site import DESIGN_ASKS

ROADMAP = ROOT.parent / "atlas" / "roadmap" / "roadmap.yaml"


def _review_json() -> dict[str, Any]:
    return json.loads(REVIEW.model_dump_json())


def _with_sr(sr_id: str, **update: Any) -> dict[str, Any]:
    data = _review_json()
    for s in data["srs"]:
        if s["sr_id"] == sr_id:
            s.update(update)
    return data


def test_catalogue_is_the_51_srs_of_the_toc() -> None:
    assert len(SR_CATALOGUE) == 51
    assert len({e.sr_id for e in SR_CATALOGUE}) == 51
    counts = Counter(e.fr for e in SR_CATALOGUE)
    assert [counts[f] for f in range(1, 8)] == [13, 12, 9, 3, 4, 2, 8]
    assert set(FR_NAMES) == set(range(1, 8))
    assert all(e.source_id == "isa_62443_3_3_toc" for e in SR_CATALOGUE)


def test_csf_witness_covers_47_srs() -> None:
    srs = {e.sr_id for e in SR_CATALOGUE}
    assert len(CSF_1_1_BY_SR) == 47 and set(CSF_1_1_BY_SR) <= srs
    assert srs - set(CSF_1_1_BY_SR) == {"SR 3.6", "SR 3.7", "SR 7.5", "SR 7.7"}


def test_every_sr_reviewed_once_in_catalogue_order() -> None:
    assert [(s.sr_id, s.title) for s in REVIEW.srs] == [(e.sr_id, e.title) for e in SR_CATALOGUE]
    data = _review_json()
    data["srs"] = data["srs"][1:]
    with pytest.raises(ValidationError, match="exactly the 51 SRs"):
        Sl2Review.model_validate_json(json.dumps(data))
    data = _with_sr("SR 1.1", title="a paraphrase of the requirement")
    with pytest.raises(ValidationError, match="exactly the 51 SRs"):
        Sl2Review.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "update",
    [
        {"certified": True},
        {"conformity_assessment": True},
        {"sl_achieved": 2},
        {"sl_target": 3},
        {"basis": "full_text"},
        {"full_text_review": "done"},
    ],
)
def test_review_cannot_claim_certification(update: dict[str, Any]) -> None:
    data = json.loads(REVIEW.status.model_dump_json())
    ReviewStatus.model_validate_json(json.dumps(data))  # control
    with pytest.raises(ValidationError):
        ReviewStatus.model_validate_json(json.dumps({**data, **update}))


def test_disclaimer_in_doc_top_review_and_every_fr() -> None:
    doc = render_doc()
    assert doc.count(DISCLAIMER) == 1 + 1 + len(FR_NAMES)
    assert doc.index(DISCLAIMER) < doc.index("## Zones")
    for fr, name in FR_NAMES.items():
        section = doc.split(f"### FR {fr}: {name}")[1]
        assert section.lstrip().startswith(f"> {DISCLAIMER}"), fr
    assert REVIEW.disclaimer == DISCLAIMER
    data = _review_json()
    data["disclaimer"] = "Certified review."
    with pytest.raises(ValidationError):
        Sl2Review.model_validate_json(json.dumps(data))


def test_no_claim_of_certification_or_conformity_in_the_doc() -> None:
    doc = render_doc().lower()
    for line in doc.splitlines():
        if "certified" in line and not ("oem" in line or "e-08" in line):
            assert "not certified" in line, line
        if "conformity assessment" in line:
            assert "not a conformity assessment" in line, line


@pytest.mark.parametrize(
    "sr_id,update,message",
    [
        ("SR 1.6", {"evidence": []}, "needs a test"),
        ("SR 1.6", {"design_elements": []}, "needs a design element"),
        ("SR 1.6", {"gap_ids": [GLOBAL_GAP, "GAP-62443-03"]}, "gaps and SRs disagree|only the"),
        ("SR 1.1", {"gap_ids": [GLOBAL_GAP]}, "at least one gap"),
        ("SR 1.3", {"evidence": ["WP-EDGE-03"]}, "owned by a work package"),
        ("SR 1.3", {"gap_ids": ["GAP-62443-01"]}, f"carries {GLOBAL_GAP}"),
        ("SR 7.5", {"design_elements": []}, "names the data ask"),
        ("SR 7.5", {"disposition": "title_level_mapped"}, "needs a design element"),
    ],
)
def test_claim_ceiling(sr_id: str, update: dict[str, Any], message: str) -> None:
    Sl2Review.model_validate_json(json.dumps(_review_json()))  # control
    with pytest.raises(ValidationError, match=message):
        Sl2Review.model_validate_json(json.dumps(_with_sr(sr_id, **update)))


def test_dispositions() -> None:
    counts = Counter(s.disposition for s in REVIEW.srs)
    assert counts == {
        "title_level_mapped": 13,
        "partially_mapped": 20,
        "deferred_to_wp": 17,
        "host_responsibility": 1,
    }
    for s in REVIEW.srs:
        assert s.sl2_re_applicability.kind == "unknown"


def test_csf_second_witness() -> None:
    applies = [s for s in REVIEW.srs if csf_witness(s) is not None]
    assert applies and all(csf_witness(s) for s in applies)
    mapped = [s for s in REVIEW.srs if s.disposition in ("title_level_mapped", "partially_mapped")]
    assert {s.sr_id for s in mapped} - {s.sr_id for s in applies} == {"SR 3.6", "SR 3.7", "SR 7.7"}
    off = _with_sr("SR 4.1", design_elements=["control:asset_inventory"])
    with pytest.raises(ValidationError, match="CSF subcategories miss"):
        Sl2Review.model_validate_json(json.dumps(off))


def test_test_refs_resolve() -> None:
    refs = {e for s in REVIEW.srs for e in s.evidence if e.startswith("tests/")}
    assert refs
    for ref in sorted(refs):
        path, _, name = ref.partition("::")
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert name in names, ref


def test_elements_and_asks_resolve() -> None:
    assert all(DESIGN.resolves(e) for e in DESIGN.element_refs())
    for a in DESIGN_ASKS:
        assert all(DESIGN.resolves(e) for e in a.affects), a.ask_id
    assert not DESIGN.resolves("conduit:C99") and not DESIGN.resolves("ask:invented")


@pytest.mark.skipif(not ROADMAP.exists(), reason="the atlas repository is not beside this one")
def test_wp_refs_exist_in_roadmap() -> None:
    ids = set(re.findall(r"^- id: (WP-[A-Z]+-[0-9]{2})$", ROADMAP.read_text(), re.M))
    wps = {e for s in REVIEW.srs for e in s.evidence if e.startswith("WP-")}
    owners = {g.owner for g in REVIEW.gaps} - {"person_only"}
    assert wps and owners
    assert wps | owners <= ids, sorted((wps | owners) - ids)


def test_no_requirement_text_field() -> None:
    assert set(SrReview.model_fields) == {
        "sr_id",
        "fr",
        "title",
        "disposition",
        "design_elements",
        "evidence",
        "csf_1_1",
        "gap_ids",
        "sl2_re_applicability",
        "note",
    }
    for s in REVIEW.srs:
        assert not re.search(r"\bshall\b", s.note, re.I), s.sr_id


def test_every_gap_is_referenced_and_owned() -> None:
    gaps = {g.gap_id: g for g in REVIEW.gaps}
    assert gaps[GLOBAL_GAP].sr_ids == tuple(e.sr_id for e in SR_CATALOGUE)
    assert gaps[GLOBAL_GAP].owner == "person_only"
    assert gaps[GLOBAL_GAP].kind == "text_unavailable"
    for g in REVIEW.gaps:
        assert g.sr_ids
        for sr in g.sr_ids:
            assert g.gap_id in next(s for s in REVIEW.srs if s.sr_id == sr).gap_ids
    for s in REVIEW.srs:
        if s.disposition != "title_level_mapped":
            assert set(s.gap_ids) - {GLOBAL_GAP}, s.sr_id


def test_brokered_caveat_names_its_gap() -> None:
    brokered = next(
        v for v in DESIGN.bms_variants if v.mode is BmsConduitMode.BROKERED_BIDIRECTIONAL
    )
    assert isinstance(brokered.caveat, str)
    ids = re.findall(r"GAP-62443-[0-9]{2}", brokered.caveat)
    assert ids == ["GAP-62443-20"]
    gap = next(g for g in REVIEW.gaps if g.gap_id == ids[0])
    assert "brokered_bidirectional" in gap.statement and "SR 5.2" in gap.sr_ids


def test_sources_are_public_and_hashed() -> None:
    assert set(PUBLIC_SOURCES) == {
        "isa_62443_3_3_toc",
        "isagca_quick_start_guide",
        "nist_sp_800_82r3",
        "nist_csf_1_1",
    }
    doc = render_doc()
    for s in PUBLIC_SOURCES.values():
        assert s.url.startswith("https://") and re.fullmatch(r"[0-9a-f]{64}", s.sha256)
        assert s.url in doc and s.sha256 in doc
        assert s.lacks or s.source_id == "nist_sp_800_82r3"


def test_the_doc_lists_person_only_items() -> None:
    doc = render_doc()
    person = doc.split("## Person-only items")[1]
    assert "Purchase IEC 62443-3-3 (and IEC 62443-3-2)" in person
    assert "AWAIT-19" in person


def test_review_doc_path_is_under_docs_design() -> None:
    from gridq.edge_design.render import DOC_PATH

    assert DOC_PATH.relative_to(ROOT) == Path("docs/design/edge_zone_conduit_design.v1.0.0.md")
