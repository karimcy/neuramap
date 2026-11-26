"""GridQ demo web shell (issue 013 / M8 UI).

A thin FastAPI + Jinja2 app that runs a `SiteModel` through the deterministic quote pipeline and
presents the three M8 artifacts in one view. It is read-only over deterministic outputs and
computes nothing — see `gridq.app.app`. Optional dependency group: ``pip install -e ".[app]"``.
"""

from __future__ import annotations

from gridq.app.app import app, create_app

__all__ = ["app", "create_app"]
