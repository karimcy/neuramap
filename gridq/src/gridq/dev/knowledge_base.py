"""WP-GRC-14 knowledge store: one index linking assumption releases to changelog entries and ADRs.

``knowledge_base/index.json`` is generated, never hand-edited. Inputs: the assumption
registry (``assumptions/gb_v*.yaml``), ``docs/changelog.md``, ``knowledge_base/adr_index.json`` (a
snapshot of the Atlas ADR front matter) and ``knowledge_base/links.yaml`` (hand-kept release ->
changelog heading and ADR ids). Commands: ``build``, ``check``, ``sync-adrs <atlas adr dir>``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
KB = ROOT / "knowledge_base"
ASSUMPTIONS = ROOT / "assumptions"
CHANGELOG = ROOT / "docs" / "changelog.md"


def changelog_sections() -> dict[str, str]:
    """Map each ``## `` heading (text after the hashes) to its section body."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def registry_releases() -> dict[str, dict[str, Any]]:
    rel: dict[str, dict[str, Any]] = {}
    for f in sorted(ASSUMPTIONS.glob("gb_v*.yaml")):
        meta = yaml.safe_load(f.read_text(encoding="utf-8"))["metadata"]
        rel[meta["version"]] = {
            "released": str(meta["released"]),
            "file": f"assumptions/{f.name}",
        }
    return rel


def load_links() -> dict[str, Any]:
    return yaml.safe_load((KB / "links.yaml").read_text(encoding="utf-8"))["releases"] or {}


def load_adrs() -> dict[str, Any]:
    return json.loads((KB / "adr_index.json").read_text(encoding="utf-8"))


def build_index() -> dict[str, Any]:
    links, adrs = load_links(), load_adrs()
    releases = {}
    for rid, info in registry_releases().items():
        link = links.get(rid, {})
        releases[rid] = {
            **info,
            "changelog_heading": link.get("changelog"),
            "adrs": link.get("adrs", []),
        }
    return {"adrs": adrs, "releases": releases}


def problems() -> list[str]:
    """Every breach of the acceptance criterion, as readable strings."""
    out: list[str] = []
    idx, secs, adrs = build_index(), changelog_sections(), load_adrs()
    for rid, r in idx["releases"].items():
        head = r["changelog_heading"]
        if not head:
            out.append(f"{rid}: no changelog link in knowledge_base/links.yaml")
        elif head not in secs:
            out.append(f"{rid}: changelog heading not found in docs/changelog.md: {head!r}")
        elif not re.search(rf"\b{re.escape(rid)}\b", head + "\n" + secs[head]):
            out.append(f"{rid}: its changelog section does not name the release")
        for a in r["adrs"]:
            if a not in adrs:
                out.append(f"{rid}: names ADR {a!r}, which is not in knowledge_base/adr_index.json")
    for rid in load_links():
        if rid not in idx["releases"]:
            out.append(f"links.yaml names {rid}, which is not a release in assumptions/")
    return out


def _dump(idx: dict[str, Any]) -> str:
    return json.dumps(idx, indent=2, sort_keys=True) + "\n"


def sync_adrs(adr_dir: Path) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    for f in sorted(adr_dir.glob("ADR-*.md")):
        fm = yaml.safe_load(f.read_text(encoding="utf-8").split("---")[1])
        snap[fm["id"]] = {
            "title": fm.get("title"),
            "status": fm.get("status"),
            "chosen_option": fm.get("chosen_option"),
        }
    return snap


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    cmd = a[0] if a else "check"
    if cmd == "sync-adrs":
        (KB / "adr_index.json").write_text(_dump(sync_adrs(Path(a[1]))), encoding="utf-8")
    elif cmd == "build":
        (KB / "index.json").write_text(_dump(build_index()), encoding="utf-8")
    elif cmd == "check":
        bad = problems()
        stale = not (KB / "index.json").exists() or (KB / "index.json").read_text(
            encoding="utf-8"
        ) != _dump(build_index())
        for p in bad:
            print(p)
        if stale:
            print("knowledge_base/index.json is stale: run build")
        return 1 if bad or stale else 0
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
