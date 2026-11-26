"""Run the test suite one chunk per pytest process, flushing a durable result after each chunk.

Why this exists (docs/HANDOFF_ultracode_goal.md §8): `pytest -q` buffers all output when stdout is
not a tty, so a killed run leaves an EMPTY log and tells you nothing. One process per chunk means
every completed chunk's verdict is already on disk, and a hang or a kill localises to a single chunk
instead of destroying the whole run.

Measured on this suite (2026-08-11, macOS, py3.12): the fixed per-process pytest overhead is ~1s
(tests/test_imports.py: 16 tests, 1.0s wall), so the default of one file per process costs only
~2 minutes across ~135 files and buys the best possible failure localisation. Raise --batch if you
would rather trade localisation for less overhead.

Results are written OUTSIDE the repo by default so a run never dirties the working tree.

Usage:
    python scripts/run_test_chunks.py                  # whole suite, 1 file per process
    python scripts/run_test_chunks.py --batch 14       # ~10 groups instead of ~135 processes
    python scripts/run_test_chunks.py --slice 0 40      # first 40 files only
    python scripts/run_test_chunks.py --out /some/dir   # choose the results directory

Re-running RESUMES: files already recorded in chunk_results.jsonl are skipped, so an interrupted
run continues where it stopped rather than starting over.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 900  # 15 min per chunk; no single file should approach this


def _python() -> str:
    """Prefer the repo venv, else the interpreter running this script."""
    venv = REPO / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=1, help="test files per pytest process")
    parser.add_argument("--slice", nargs=2, type=int, metavar=("START", "END"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(tempfile.gettempdir()) / "gridq_test_chunks",
        help="results directory (kept outside the repo so runs never dirty the tree)",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "chunk_results.jsonl"
    summary_path = args.out / "chunk_summary.txt"

    files = sorted(p.name for p in (REPO / "tests").glob("test_*.py"))
    if args.slice:
        files = files[args.slice[0] : args.slice[1]]

    done: set[str] = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                done.update(json.loads(line)["files"])
    files = [f for f in files if f not in done]

    chunks = [files[i : i + args.batch] for i in range(0, len(files), args.batch)]
    print(f"{len(files)} file(s), {len(chunks)} chunk(s) -> {args.out}", flush=True)

    failed_chunks = 0
    with (
        open(summary_path, "a", buffering=1) as summary,
        open(results_path, "a", buffering=1) as jsonl,
    ):
        for i, chunk in enumerate(chunks, 1):
            cmd = [
                _python(),
                "-u",
                "-m",
                "pytest",
                *[f"tests/{name}" for name in chunk],
                "-p",
                "no:cacheprovider",
                "--tb=short",
                "-rf",
                "--no-header",
                "-q",
            ]
            started = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd, cwd=REPO, capture_output=True, text=True, timeout=args.timeout
                )
                out, rc, timed_out = proc.stdout + proc.stderr, proc.returncode, False
            except subprocess.TimeoutExpired as exc:
                raw = (exc.stdout or b"") + (exc.stderr or b"")
                out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                rc, timed_out = -1, True

            elapsed = time.monotonic() - started
            lines = [ln for ln in out.splitlines() if ln.strip()]
            counts = lines[-1].strip() if lines else "(no output)"
            verdict = "TIMEOUT" if timed_out else ("PASS" if rc == 0 else "FAIL")
            if verdict != "PASS":
                failed_chunks += 1

            jsonl.write(
                json.dumps(
                    {
                        "files": chunk,
                        "verdict": verdict,
                        "rc": rc,
                        "seconds": round(elapsed, 1),
                        "counts": counts[:300],
                        "output": out[-20000:],
                    }
                )
                + "\n"
            )
            label = chunk[0] if len(chunk) == 1 else f"{chunk[0]} +{len(chunk) - 1}"
            line = f"[{i:3d}/{len(chunks)}] {verdict:7s} {elapsed:7.1f}s {label} :: {counts[:140]}"
            summary.write(line + "\n")
            print(line, flush=True)

    print(f"ALL CHUNKS COMPLETE — {failed_chunks} chunk(s) not PASS. Summary: {summary_path}")
    return 1 if failed_chunks else 0


if __name__ == "__main__":
    raise SystemExit(main())
