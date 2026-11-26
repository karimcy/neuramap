"""``python -m gridq.governance.dpa_scan``: customer datasets in git without DPA evidence.

Prints paths only, never contents or figures. Exit codes:

- ``0``: no customer dataset in git lacks DPA evidence and the manifest is complete (criterion 3
  holds for the repository);
- ``1``: at least one customer dataset in git has no DPA evidence, or the manifest misses or
  mis-describes a file (each listed);
- ``2``: the scan could not run (git unavailable, unreadable manifest or evidence registry).

Options:

- ``--json``: machine-readable output;
- ``--evidence-dir DIR``: the IC-4 registry to check against (default ``$GRIDQ_EVIDENCE_DIR``);
- ``--run-store``: also list runs and uploads in the local run store that carry no DPA record
  (not in git, but stored all the same);
- ``--write-manifest``: regenerate the manifest's dataset list from the tracked files. The policy
  sections are kept. Review the diff: the ratchet test refuses a longer list.

A pre-commit hook that runs this scan ships as ``scripts/hooks/pre-commit-dpa-scan`` (not
installed; ``ln -s ../../scripts/hooks/pre-commit-dpa-scan .git/hooks/pre-commit`` installs it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gridq.governance.customer_data import MANIFEST_PATH, build_manifest, load_manifest, scan
from gridq.governance.dpa_guard import (
    DPA_SIDECAR,
    UPLOAD_SIDECAR_SUFFIX,
    EvidenceRegistryError,
    load_evidence_registry,
    repo_root,
)


def _run_store_without_record(store: Path) -> list[str]:
    """Run directories and uploads in the local run store with no DPA record beside them."""
    if not store.is_dir():
        return []
    missing: list[str] = []
    for child in sorted(store.iterdir()):
        if child.name == "uploads" and child.is_dir():
            for upload in sorted(child.iterdir()):
                if upload.is_file() and not upload.name.endswith(UPLOAD_SIDECAR_SUFFIX):
                    sidecar = upload.with_name(upload.name + UPLOAD_SIDECAR_SUFFIX)
                    if not sidecar.is_file():
                        missing.append(str(upload))
        elif child.is_dir() and (child / "quote.json").is_file():
            if not (child / DPA_SIDECAR).is_file():
                missing.append(str(child))
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gridq.governance.dpa_scan")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--run-store", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)

    if args.write_manifest:
        body = build_manifest()
        MANIFEST_PATH.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST_PATH.relative_to(repo_root())}: {body['dataset_count']} datasets")
        return 0

    try:
        registry = load_evidence_registry(args.evidence_dir)
        result = scan(registry=registry)
    except (EvidenceRegistryError, OSError, ValueError) as exc:
        print(f"dpa_scan: could not run: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - git missing or not a repository
        print(f"dpa_scan: could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    run_store: list[str] = []
    if args.run_store:
        env = os.environ.get("GRIDQ_RUN_STORE")
        run_store = _run_store_without_record(Path(env) if env else repo_root() / "runs")

    if args.json:
        body = result.to_json()
        body["run_store_without_dpa_record"] = run_store
        print(json.dumps(body, indent=2))
    else:
        manifest = load_manifest()
        verdict = "MET" if result.criterion_3_met else "NOT MET"
        print(f"WP-GRC-05 criterion 3 for files in git: {verdict}")
        print(
            f"customer datasets in git without DPA evidence: {len(result.unevidenced)} "
            f"(of {len(manifest.datasets)} listed; quarantined: the guard refuses to load them)"
        )
        for entry in result.unevidenced:
            print(f"  {entry.path}")
        if result.evidenced:
            print(f"released by DPA evidence: {len(result.evidenced)}")
            for entry in result.evidenced:
                print(f"  {entry.path}")
        if result.findings:
            print(f"manifest findings: {len(result.findings)}")
            for finding in result.findings:
                print(f"  [{finding.kind}] {finding.path}: {finding.detail}")
        if result.review_prose:
            print(
                f"prose naming customers, for review (not load-guarded): {len(result.review_prose)}"
            )
            for rel in result.review_prose:
                print(f"  {rel}")
        if args.run_store:
            print(f"local run store entries without a DPA record: {len(run_store)}")
            for item in run_store:
                print(f"  {item}")
    return 0 if result.criterion_3_met and not run_store else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
