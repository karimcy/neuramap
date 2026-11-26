"""Optimisation engine interface + a baseline (integration-only) implementation.

`OptimisationEngine` is the interface the platform and engine both code against. `BaselineEngine`
is a deliberately limited implementation for plumbing/integration tests — it never produces
bankable output. The real MILP will implement the same interface. See engine/README.md.
"""

from __future__ import annotations

from gridq.engine.baseline import BaselineEngine
from gridq.engine.errors import EngineError, InvalidRequestError
from gridq.engine.interface import OptimisationEngine

__all__ = [
    "OptimisationEngine",
    "BaselineEngine",
    "EngineError",
    "InvalidRequestError",
]
