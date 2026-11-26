---
description: Run the quality bar (tests, lint, typecheck, format check), report failures, and propose fixes.
argument-hint: [optional: a path/area to focus on]
allowed-tools: Bash(./.venv/bin/pytest:*), Bash(./.venv/bin/ruff:*), Bash(./.venv/bin/mypy:*), Bash(pytest:*), Bash(ruff:*), Bash(mypy:*), Bash(source:*)
---

Run the project quality bar (from `CLAUDE.md §6`) and report. Optional focus: **$ARGUMENTS**

Use the project virtualenv (`.venv`) — either activate it or prefix commands with `./.venv/bin/`. Run each tool **only if it is installed**; if one is missing, note it and skip (don't fail the whole run).

1. **Tests** — `pytest` (scope to `$ARGUMENTS` if given, e.g. `pytest tests/test_x.py`).
2. **Lint** — `ruff check .`
3. **Typecheck** — `mypy`
4. **Format check** — `ruff format --check .`

Then report:
- A short **pass/fail line per check**.
- For each failure: the **relevant output** (not the whole log) and a **diagnosis** of the cause.
- A **proposed minimal fix** for each — the smallest change that makes it pass without weakening the check (never silence a real failure or loosen a rule to make it green).

Apply fixes only if they are trivial and obviously correct (e.g. a formatting fix, an import order); for anything substantive, propose it and wait. Keep the pricing path deterministic. End with whether the bar is **green** overall.
