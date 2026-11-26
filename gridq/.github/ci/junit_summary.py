"""Sum pytest JUnit XML files into pass/fail counts (WP-PLAT-01).

Usage: python .github/ci/junit_summary.py <out.md> <out.json> <junit.xml> [<junit.xml> ...]

Writes a Markdown table (for $GITHUB_STEP_SUMMARY) and a JSON record, prints the Markdown, and
exits 1 when any test failed or errored, or when a shard produced no XML at all (a crashed shard
must read as a failure, never as zero tests).
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_FIELDS = ("tests", "failures", "errors", "skipped")


def _counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = dict.fromkeys(_FIELDS, 0)
    for suite in suites:
        for field in _FIELDS:
            totals[field] += int(suite.get(field, 0))
    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return totals


def _failed_ids(path: Path) -> list[str]:
    ids = []
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            ids.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return ids


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    out_md, out_json, xml_paths = Path(argv[1]), Path(argv[2]), [Path(p) for p in argv[3:]]
    rows, failed, missing = [], [], []
    grand = dict.fromkeys((*_FIELDS, "passed"), 0)
    for path in xml_paths:
        if not path.exists():
            missing.append(str(path))
            continue
        c = _counts(path)
        rows.append((path.stem, c))
        for k in grand:
            grand[k] += c[k]
        failed.extend(_failed_ids(path))

    lines = [
        "| Shard | Passed | Failed | Errors | Skipped | Total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, c in rows:
        lines.append(
            f"| {name} | {c['passed']} | {c['failures']} | {c['errors']} | {c['skipped']} | "
            f"{c['tests']} |"
        )
    lines.append(
        f"| **all** | **{grand['passed']}** | **{grand['failures']}** | **{grand['errors']}** | "
        f"**{grand['skipped']}** | **{grand['tests']}** |"
    )
    if missing:
        lines += ["", "**Shards with no result (crashed or timed out):** " + ", ".join(missing)]
    if failed:
        lines += ["", "<details><summary>Failed tests</summary>", ""]
        lines += [f"- `{t}`" for t in failed]
        lines += ["", "</details>"]
    md = "\n".join(lines) + "\n"
    out_md.write_text(md)
    out_json.write_text(
        json.dumps({"totals": grand, "shards": dict(rows), "failed": failed, "missing": missing})
    )
    print(md)
    return 1 if (grand["failures"] or grand["errors"] or missing) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
