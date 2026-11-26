# Session-sync — how Olly stays coherent across sessions

**Olly** is the sole contributor to this repo. This folder is the shared brain that keeps
work coherent across separate Claude Code sessions. It answers, at any moment: **what's been
done, when — and what needs doing next.** Everything here is plain Markdown plus a
small helper (`gridq.dev.team_sync`); there is **no pricing/optimisation logic**.

## The files

| File | What it holds |
|------|---------------|
| [`TEAM_SYNC.md`](TEAM_SYNC.md) | The situational-awareness page: what changed recently, current tasks, next actions. Read first. |
| [`HANDOFF_LOG.md`](HANDOFF_LOG.md) | **Append-only** record of every work session — `### <date time> — <who>` + summary / files / tests / remaining / warnings. This is the "who did what, when". |
| [`ACTIVE_PLAN.md`](ACTIVE_PLAN.md) | The live plan (current milestone/task, files, acceptance, risks, last-touched-by, notes for the next person). |
| [`OPEN_COMMENTS.md`](OPEN_COMMENTS.md) | Async questions/decisions between Olly (`C###`, status `open`/`resolved`/`deferred`). |
| [`BLOCKERS.md`](BLOCKERS.md) | Things stopping or threatening progress (`B###`). |

## Who am I? (attribution)

Attribution is driven by one environment variable, **`GRIDQ_CONTRIBUTOR`**, set **per machine**
(never committed) in `.claude/settings.local.json`:

```json
{ "env": { "GRIDQ_CONTRIBUTOR": "Olly" } }
```

Olly sets `"Olly"` on his machine. That's the *only* per-person setup step. `oli`/`olly`/`op` →
`Olly` and `ed`/`edward` → `Olly` are accepted (case-insensitive). Check it any time:

```bash
python -m gridq.dev.team_sync whoami
```

## What happens automatically

Two hooks (wired in `.claude/settings.json`, shared by both of you) enforce the workflow — see
`.claude/hooks/`:

1. **`SessionStart` → the briefing.** Every session, *whatever the prompt*, prints the team-sync
   briefing (you are X · what's been done by whom · what's next · open items/blockers). Claude is
   instructed (here and in `CLAUDE.md`) to **show it to you and reconcile your request against the
   plan before doing anything**. Print it yourself any time:
   ```bash
   python -m gridq.dev.team_sync briefing     # the session briefing
   python -m gridq.dev.team_sync              # the fuller dashboard
   ```
2. **`Stop` → the log reminder.** When a session ends having changed `src/` or `docs/` but with no
   `HANDOFF_LOG` entry dated today, you get a one-line nudge to log it. It is **non-blocking** and
   **self-silences** once you've logged today.

## Logging your work (do this before you finish)

Append a handoff entry, attributed to you automatically:

```bash
python -m gridq.dev.team_sync add-handoff \
  --summary "what you did" \
  --files "paths changed" \
  --tests "what you ran + result" \
  --remaining "what's left" \
  --warnings "anything the next person must know"
```

`--summary` is required; the rest are optional. The contributor defaults to `GRIDQ_CONTRIBUTOR`
(override with `--contributor Olly`). From Python:

```python
from gridq.dev.team_sync import add_handoff_entry, current_contributor
add_handoff_entry(current_contributor() or "Olly", "what you did", files_changed=[...], tests_run="...")
```

Record a question or open decision, or update the plan:

```python
from gridq.dev.team_sync import add_open_comment, resolve_open_comment, update_active_plan
add_open_comment(author=current_contributor(), target="src/...", comment="…")   # → C0NN
update_active_plan(task="…", notes="…", last_touched=current_contributor())
```

## The rhythm

1. **Start:** read the briefing Claude surfaces; reconcile your task against the plan; flag clashes.
2. **During:** raise cross-person questions in `OPEN_COMMENTS.md`; never silently change a shared
   contract (`SiteModel`/`OptimisationResult`/stream registry) — flag and version it.
3. **End:** `add-handoff` (who/what/next), and update `ACTIVE_PLAN.md`/`TEAM_SYNC.md` if the plan moved.

> Procedural memory (`SKILLS.md`) is single-writer (the skills-maintainer only) — propose changes
> via `skills/proposed_updates/`, don't edit it directly. See [`docs/parallel_development_plan.md`](../parallel_development_plan.md).
