#!/usr/bin/env bash
# SessionStart hook — print the GridQ team-sync briefing (who did what · what's next).
#
# Claude Code injects this stdout into the session as context; CLAUDE.md tells the session
# to surface it to the user and reconcile the prompt against the plan before doing anything.
# Uses bare python3 + the source tree (team_sync has no third-party deps), so it works
# without the venv on PATH. Falls back to the raw TEAM_SYNC file if rendering fails.
set -u
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
PYTHONPATH="$ROOT/src" python3 -m gridq.dev.team_sync briefing 2>/dev/null && exit 0
echo "GRIDQ team-sync (fallback — could not render the briefing; read these directly):"
echo "  docs/shared_work/TEAM_SYNC.md · ACTIVE_PLAN.md · HANDOFF_LOG.md · OPEN_COMMENTS.md · BLOCKERS.md"
cat "$ROOT/docs/shared_work/TEAM_SYNC.md" 2>/dev/null || true
exit 0