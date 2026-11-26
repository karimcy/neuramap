# 001 · Project scaffold & tooling

**Owner:** Shared
**Module(s):** Cross-cutting (tooling) · Milestone 0
**Milestone:** Milestone 0 — Project setup & docs
**Status:** Not started

## Why this matters
Everything downstream is built sequentially on top of this scaffold, so a runnable, quality-wired empty project is the precondition for every other issue. The spec makes filling the CLAUDE.md §6 command table (test/lint/typecheck/run) a Milestone 0 exit criterion, and the testing harness is `pytest` on the Python/linopy stack — without it the determinism/traceability discipline cannot be enforced from the first quote. Critically, the DB-vs-files storage decision is decided here and not relitigated mid-build, because it blocks the run-record format used by provenance (004) and the quote lifecycle (009). This issue is shared because we build against the same toolchain and conventions.

## Requirements
- Stand up a Python package `gridq/` with a `pyproject.toml` declaring the project metadata and dev dependencies (pytest, ruff, mypy; linopy/HiGHS reserved for the real engine later, not required to run v1).
- Wire the four-command quality bar so `pytest`, lint, typecheck and run all execute from a single documented entrypoint (Makefile or task runner).
- Establish the canonical repo layout: package under `gridq/`, tests under `tests/` with a `conftest.py`, schemas under `schemas/`, versioned assumptions under `assumptions/<version>/`.
- Provide a `tests/` pytest harness that runs green on an empty project (at least one smoke test), so the harness for fixtures and conformance suites exists from day one.
- Record the DB-vs-files storage decision (Open Q1) explicitly in the docs — v1 defaults to file-based SiteModels, run records and assumption library unless a database is justified — so 004/009 record formats are unblocked.
- Fill the CLAUDE.md §6 command table (replace the `TBD` placeholders for Test/Lint/Typecheck/Run/Format with the real commands).
- Put `CHANGELOG.md` (newest-first) and a `README.md` (how to run/test) in place; add a `.gitignore`.

## Acceptance criteria
- [ ] `pyproject.toml` exists, declares the `gridq` package, and `pip install -e .[dev]` (or equivalent) succeeds.
- [ ] The documented Test/Lint/Typecheck/Run/Format commands each execute and pass on the empty project.
- [ ] A smoke `pytest` run collects and passes at least one test from `tests/`.
- [ ] The CLAUDE.md §6 command table contains real commands (no remaining `TBD`).
- [ ] The DB-vs-files storage decision is recorded in writing (with rationale), resolving Open Q1 for v1.
- [ ] `CHANGELOG.md`, `README.md` and `.gitignore` are present; README documents how to run and test.
- [ ] The repo layout exists: `gridq/`, `tests/`, `schemas/`, `assumptions/<version>/`.

## Dependencies
- None (foundational)

## Out of scope (v1)
- Installing or wiring the production solver stack (linopy/HiGHS) — reserved for the real engine (014); v1 runs against the mock.
- Any application/module code — schemas (002), assumptions (003), provenance (004) and beyond are separate issues.
- A database backend unless the Milestone 0 decision explicitly chooses one over files.
- CI/CD pipelines, release automation, review-cadence tooling and other hardening (Karim/software-team "Later").

## Suggested files to create or modify
- `pyproject.toml` — project metadata + dev deps (Python, pytest, ruff, mypy; linopy/HiGHS reserved for later).
- `README.md` — how to install, run and test the project.
- `gridq/__init__.py` — package root.
- `tests/` — pytest layout with `conftest.py` and an initial smoke test.
- `Makefile` (or task runner) — wires test/lint/typecheck/run/format behind one entrypoint.
- `.gitignore` — standard Python/venv/build ignores.
- `CHANGELOG.md` — newest-first changelog seeded with the scaffold entry.
- `CLAUDE.md` — modify: fill the §6 command table (Test/Lint/Typecheck/Run/Format) and record the DB-vs-files decision reference.
- `schemas/` — directory placeholder for the shared JSON schemas (populated by 002/005).
- `assumptions/<version>/` — directory placeholder for the versioned assumption library (populated by 003).

> Suggested only — no application code is created by this issue document.
