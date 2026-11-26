"""The release: canonical JSON and the generated design document are current, and the design's
hash is pinned (a change to the design is a new version, never a re-pin)."""

from __future__ import annotations

from gridq.edge_design.__main__ import main
from gridq.edge_design.release import (
    DESIGN,
    DESIGN_HASH,
    RELEASE_JSON,
    EdgeZoneConduitDesign,
    design_hash,
    release_text,
)
from gridq.edge_design.render import DOC_PATH, render_doc


def test_release_json_is_current() -> None:
    assert RELEASE_JSON.read_text(encoding="utf-8") == release_text()


def test_release_round_trips() -> None:
    text = RELEASE_JSON.read_text(encoding="utf-8")
    assert EdgeZoneConduitDesign.model_validate_json(text) == DESIGN


def test_design_doc_is_current() -> None:
    assert DOC_PATH.read_text(encoding="utf-8") == render_doc()


def test_design_hash_is_pinned() -> None:
    assert design_hash() == DESIGN_HASH
    assert DESIGN_HASH in render_doc()


def test_cli_check_passes() -> None:
    assert main(["--check"]) == 0
