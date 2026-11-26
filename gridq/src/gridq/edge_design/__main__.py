"""``python -m gridq.edge_design --check | --write``.

``--check`` exits 1 when the committed release JSON or design document differs from a fresh
render, or when the release's hash is not the pinned `DESIGN_HASH`. ``--write`` rewrites the two
files; it never changes the pinned hash (a design change is a new version).
"""

from __future__ import annotations

import argparse
import sys

from gridq.edge_design.release import DESIGN_HASH, RELEASE_JSON, design_hash, release_text
from gridq.edge_design.render import DOC_PATH, render_doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    files = ((RELEASE_JSON, release_text()), (DOC_PATH, render_doc()))
    if args.write:
        for path, text in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path}")
        return 0
    bad = [p for p, text in files if not p.exists() or p.read_text(encoding="utf-8") != text]
    if design_hash() != DESIGN_HASH:
        print(f"design hash {design_hash()} is not the pinned {DESIGN_HASH}", file=sys.stderr)
        bad.append(RELEASE_JSON)
    for p in bad:
        print(f"stale: {p}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
