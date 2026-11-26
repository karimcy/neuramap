---
proposed_by: agent/deploy
date: 2026-06-24
status: proposed   # proposed | accepted | rejected
---

## Operation
append   (one edit)

## Target
A new short subsection at the end of the deployment/operational guidance in `SKILLS.md` (e.g. under
the outputs/deployment guidance, or as a new `### Deploying the app (reproducible + honest)` block).

## Proposed text
- **Deploying the web app = a reproducible, deterministic, self-contained image — and stop at "ready to deploy."** Build from a **fully-pinned lock** (`requirements.txt`) that is the *runtime + `[app]` closure only* — generate it from the green env / a clean `pip install ".[app]"`, **never `pip freeze` the dev venv** (that ships graphify/pytest/ruff/mypy/tree-sitter). Install **wheels-only** (`pip install --only-binary=:all:`) so a missing wheel fails loudly instead of silently compiling (the solver stack — highspy bundles HiGHS, numpy/scipy/pandas ship manylinux wheels — needs **no compiler/apt layer**). Install gridq **editable, in its source-tree layout** (`pip install --no-deps -e .` with `assumptions/` + `examples/` as repo-root siblings of `src/`): both resolve via `Path(__file__).parents[3]` and the demo dir has **no env override**, so a flat site-packages install breaks demo + assumption loading. Pin determinism at the host too: `TZ=UTC`, `LANG/LC_ALL=C.UTF-8`, and single-threaded numerics (`OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS=1`, `POLARS_MAX_THREADS=1`) so the MILP stays byte-reproducible regardless of host CPU count/locale (the HiGHS solve is already `threads=1, seed=0`; this covers numpy/scipy/pandas reductions). Verify the lock by **installing it into a clean venv and running the app smoke + demo-MILP suites against it** before trusting the image. Add a cheap unauthenticated `/healthz` (host probe), keep any gate (Basic Auth) **dormant-by-default behind an env var** and **never put a credential in the repo**, and keep real/user data out of the image (`.dockerignore` `runs/`, `data/`, `.env`, `.claude/`; ship only labelled synthetic fixtures). Treat the run store as **ephemeral by default** (privacy-aligned) and document retention + a deletion path if you make it durable. **Hard boundary:** the agent prepares the build, config and runbook but must **not** create hosting accounts, accept provider terms, register/point domains, set live secrets, or push to a live URL — flag each as `TODO[Olly]` and stop at "ready to deploy."

## Evidence
W5-3 (D26) prepared the public deployment for `gridq.app`: a `python:3.12-slim` `Dockerfile`, a pinned
62-pkg `requirements.txt` (the runtime+`[app]` closure), `.dockerignore`, `Procfile`, `fly.toml`, and
`docs/deployment.md`. Real findings that motivate each clause: (1) `pip freeze` of `/root/.venv` would
have shipped the dev/agent toolchain — so the lock was computed as the runtime closure instead;
(2) `import linopy` eagerly imports `linopy.remote` → `requests`/`google-cloud-storage` at module top
level, so those are *required*, proving "verify imports, don't guess the closure"; (3) a flat
site-packages install was rejected because `assumptions/loader.py` + `app/demo.py` resolve data via
`parents[3]` and the demo dir has no env escape hatch; (4) the determinism env pins were added because
the shipped `test_run_is_deterministic` is the no-MILP T0 path (the real guard is `test_app_demo.py`),
so BLAS/OMP threading on an arbitrary host could otherwise drift the solve; (5) a latent packaging
defect surfaced — `pyproject.toml` lacked the `[project.optional-dependencies]` header (invalid PEP
621, clean installs failed; flagged C024). The lock was verified by a clean-venv install + the app
smoke + demo-MILP suites (green), and the exact container start command was verified against a real
uvicorn boot (`/healthz` open, gate returns 401/200, `/static` exempt). The live actions were left as
`TODO[Olly]` per the strict W5-3 rule.

## Notes
This is procedural "how to act when deploying" (lock hygiene, wheels-only, editable layout, determinism
pins, gate/secret/privacy posture, the human-only boundary) — a repeatable method, not a one-off event.
The event/fact record lives in `docs/changelog.md` + `docs/decisions.md` (D26) and the data-handling
specifics in `docs/deployment.md`; this captures only the reusable rules.
