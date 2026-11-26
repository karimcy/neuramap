"""Engine errors."""

from __future__ import annotations


class EngineError(Exception):
    """Base class for optimisation-engine errors."""


class InvalidRequestError(EngineError):
    """Raised when an engine is asked to solve something that isn't a valid request."""
