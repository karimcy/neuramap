"""WP-GRC-14: each assumption release links to its changelog entry, and to its ADR if one exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from gridq.dev import knowledge_base as kb


def test_every_release_links_to_changelog_and_existing_adrs() -> None:
    assert kb.problems() == []


def test_every_registry_release_is_indexed() -> None:
    assert set(kb.build_index()["releases"]) == set(kb.registry_releases())
    assert len(kb.registry_releases()) >= 14


def test_index_is_current() -> None:
    assert (kb.KB / "index.json").read_text(encoding="utf-8") == kb._dump(kb.build_index())


def test_adr_snapshot_matches_atlas_when_present() -> None:
    atlas = kb.ROOT.parent / "atlas" / "roadmap" / "adr"
    if not atlas.is_dir():
        pytest.skip("Atlas ADR directory not beside this repo")
    assert kb.load_adrs() == kb.sync_adrs(atlas)


def _with_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> list[str]:
    (tmp_path / "links.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(kb, "KB", tmp_path)
    (tmp_path / "adr_index.json").write_text(
        (Path(kb.ROOT) / "knowledge_base" / "adr_index.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return kb.problems()


def test_fails_when_release_has_no_changelog_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert any(
        "gb_v1_6: no changelog link" in p
        for p in _with_links(monkeypatch, tmp_path, "releases: {}\n")
    )


def test_fails_when_release_names_missing_adr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    links = (Path(kb.ROOT) / "knowledge_base" / "links.yaml").read_text(encoding="utf-8")
    bad = links.replace("adrs: [ADR-D3]", "adrs: [ADR-D999]")
    assert any("ADR-D999" in p for p in _with_links(monkeypatch, tmp_path, bad))


def test_fails_when_changelog_section_does_not_name_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    links = (Path(kb.ROOT) / "knowledge_base" / "links.yaml").read_text(encoding="utf-8")
    bad = links.replace('gb_v1_6: {changelog: "2026-07-20"}', 'gb_v1_6: {changelog: "2026-07-17"}')
    assert any(
        "gb_v1_6" in p and "does not name" in p for p in _with_links(monkeypatch, tmp_path, bad)
    )
