"""Run the app locally: ``python -m gridq.app`` (needs the ``app`` extra).

A local developer/demo tool. ``uvicorn`` is imported lazily (in ``app.main``) so importing the app
(e.g. in tests via `TestClient`) needs no server installed. Host/port are overridable via
``GRIDQ_APP_HOST`` / ``GRIDQ_APP_PORT``.
"""

from __future__ import annotations

from gridq.app.app import main

if __name__ == "__main__":
    main()
