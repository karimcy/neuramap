---
description: After a change, bring the changelog, project status, decisions and open questions back in sync.
argument-hint: [optional: what changed, if not obvious from the recent work]
---

A change just landed (described here, or visible in the recent work/diff): **$ARGUMENTS**

Update the documentation system so it matches reality. Make only the updates that are warranted — do not invent content, do not duplicate, keep entries concise.

1. **`docs/changelog.md`** — add a reverse-chronological entry under today's date: what changed and why (one or two lines). This is required for any meaningful change.
2. **`docs/project_status.md`** — update whichever of these moved: **current milestone**, **completed work**, **next actions**, **blockers**, **open questions**, **owner split**. Tick the milestone tracker if a milestone completed.
3. **`docs/decisions.md`** — *only if a non-obvious decision was actually made*, add a dated entry in the house format: **date · decision · reason · alternatives considered · consequences**. Skip if nothing was decided.
4. **Open questions** — record any new ones, and mark resolved ones resolved, in `docs/project_status.md` (and `docs/01_project_spec.md §10` if it's an external dependency).

Do not touch `CLAUDE.md` unless a *permanent operating rule* changed (that's what `/postmortem` is for). When done, report exactly which files you changed and the one-line summary of each edit.
