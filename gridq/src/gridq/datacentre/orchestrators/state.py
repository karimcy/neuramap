"""The orchestrator state document: raw API responses in a labelled wrapper (D159).

An adapter never connects to an orchestrator. It reads a `StateDocument`: the bodies an
orchestrator's public read API returned, kept untouched in ``payload`` (keyed by the request path
that produced each body), inside a wrapper that says

- whether the bodies were **recorded** from a real cluster or written as a **synthetic** example
  (``label``; a synthetic document can only ever yield a ``synthetic_fixture`` declaration);
- which API, at which version, documented where (``source``: the URL a reader checks the shape
  against);
- when the bodies were captured (``captured_at``; adapters never read a clock, so this is also the
  declaration's ``issued_at``).

The document is identified by its content hash, which `Declared` and `Refused` both carry.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from gridq.contracts._base import ContractModel, NonEmptyStr, UtcInstant, content_hash

__all__ = ["API_ID_PATTERN", "ApiSource", "StateDocument"]

#: An API id doubles as the adapter registry key (`gridq.extensions.registry.ID_PATTERN`).
API_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://\S+$")]


class ApiSource(ContractModel):
    """The public API the payload was read from."""

    kind: Literal["api_source"]
    api_id: Annotated[str, StringConstraints(pattern=API_ID_PATTERN)]
    api_version: NonEmptyStr
    #: The versioned API reference the payload's shape is checked against.
    doc_url: HttpsUrl
    #: The request paths whose bodies the payload holds (sorted, no repeats).
    resource_paths: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _sorted(self) -> ApiSource:
        if list(self.resource_paths) != sorted(set(self.resource_paths)):
            raise ValueError("resource_paths must be sorted with no repeats")
        return self


class StateDocument(ContractModel):
    """Orchestrator read-API bodies, labelled, sourced and timestamped. Never fetched here."""

    kind: Literal["orchestrator_state"]
    label: Literal["recorded", "synthetic"]
    source: ApiSource
    captured_at: UtcInstant
    #: What this capture is, in words (for a synthetic one: what it was written to exercise).
    capture_note: NonEmptyStr
    #: Request path -> the response body, exactly as the API returned (or would return) it.
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _payload_matches_source(self) -> StateDocument:
        if sorted(self.payload) != list(self.source.resource_paths):
            raise ValueError("payload must hold exactly the bodies of source.resource_paths")
        if self.label == "synthetic" and not self.capture_note.startswith("SYNTHETIC"):
            raise ValueError("a synthetic state document's capture_note starts SYNTHETIC")
        return self

    def content_hash(self) -> str:
        return content_hash(self)
