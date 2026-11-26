#!/usr/bin/env bash
# Stop hook — nudge the active contributor to log a HANDOFF_LOG entry.
#
# Non-blocking (exit 0): it reminds, it never forces. It only speaks when BOTH:
#   - src/ or docs/ has uncommitted changes (work happened), and
#   - HANDOFF_LOG.md has no entry dated today (you haven't logged yet).
# So once you log a handoff today, it goes quiet for the rest of the day.
set -u
cat >/dev/null 2>&1 || true  # drain the hook's stdin JSON; we don't need it

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" 2>/dev/null || exit 0

CHANGED="$(git status --porcelain -- src docs 2>/dev/null)"
TODAY="$(date +%Y-%m-%d)"
LOG="docs/shared_work/HANDOFF_LOG.md"

if [ -n "$CHANGED" ] && ! grep -q "^### ${TODAY}" "$LOG" 2>/dev/null; then
  WHO="${GRIDQ_CONTRIBUTOR:-(set GRIDQ_CONTRIBUTOR=Olly)}"
  MSG="team-sync reminder — ${WHO}: src/ or docs/ changed but no HANDOFF_LOG entry today. Log it before you finish:  python3 -m gridq.dev.team_sync add-handoff --summary \"...\"  (see docs/shared_work/README.md)"
  # JSON systemMessage surfaces it to the user where supported; stderr is the transcript fallback.
  printf '{"systemMessage": %s}\n' "$(printf '%s' "$MSG" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"team-sync: log a HANDOFF_LOG entry before finishing."')"
  printf '%s\n' "$MSG" >&2
fi
exit 0