"""Code-version identifiers (git commit, with a safe fallback).

Never raises: if this isn't a git checkout (or git is unavailable), `get_git_commit`
returns ``"unknown"`` and `get_code_version` falls back to the package version.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def get_git_commit() -> str:
    """Return the current git commit SHA, or ``"unknown"`` on any failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else "unknown"


def get_code_version() -> str:
    """Return a code-version string: the git commit if available, else the package version."""
    commit = get_git_commit()
    if commit != "unknown":
        return f"git:{commit[:12]}"
    from gridq import __version__

    return f"pkg:{__version__}"
