"""Quote lifecycle + telemetry (X.2 / X.3) — the first-class, traceable quote.

Binds a run (site + optimisation + finance) into a stamped, versioned `QuoteRecord`, manages
the append-only ``draft → indicative → underwritten → contracted → operating`` lifecycle
(prior snapshots preserved), and emits a `TelemetryEvent` from the very first quote. Every
output traces to its input hash + assumption/code/schema versions via `build_run_context`.

Public surface:

- `assemble_quote_record` — bind + stamp a finished run into a `QuoteRecord`.
- `generate_quote` / `QuoteRunResult` — the one-call orchestrator (engine → finance → quote +
  telemetry), degrading honestly when inputs are missing.
- `persist_run` / `publish_run` / `require_persisted_run` — durable run artifacts, and the
  publication path that renders a pack **from the persisted files** so no document can cite a run
  record a later reader cannot fetch.
- `record_disclosures` — what a record owes its reader: a conflict between two accounts of its own
  verdict, a claim no persisted figure evidences, every blocking data ask stated in full.
- `transition_quote` / `legal_transitions` / `InvalidTransitionError` — the lifecycle.
- `underwriting_blockers` — why a record may not be called underwritten (empty list = it may).
- `assess_confidence` / `ConfidenceBand` — the evidential-strength band, capped by the weakest
  input; wired into `generate_quote` and persisted on the record.
- `emit_quote_telemetry` — X.3 telemetry from the first quote.

No pricing or optimisation logic lives here. See docs/01_project_spec.md (X.2/X.3) and the
data contract in `gridq.schemas.quote`.
"""

from __future__ import annotations

from gridq.quotes.assembly import assemble_quote_record
from gridq.quotes.confidence import ConfidenceAssessment, ConfidenceBand, assess_confidence
from gridq.quotes.disclosures import Disclosure, record_disclosures
from gridq.quotes.lifecycle import (
    InvalidTransitionError,
    current_mode,
    history_modes,
    legal_transitions,
    transition_quote,
    underwriting_blockers,
)
from gridq.quotes.persistence import (
    PersistedRun,
    PublishedRun,
    RunStoreError,
    UnpersistedRunError,
    is_persisted,
    load_persisted_run,
    persist_run,
    publish_run,
    require_persisted_run,
)
from gridq.quotes.pipeline import QuoteRunResult, generate_quote
from gridq.quotes.telemetry import QUOTE_CREATED_EVENT_TYPE, emit_quote_telemetry

__all__ = [
    "assemble_quote_record",
    "generate_quote",
    "QuoteRunResult",
    "persist_run",
    "publish_run",
    "load_persisted_run",
    "require_persisted_run",
    "is_persisted",
    "PersistedRun",
    "PublishedRun",
    "RunStoreError",
    "UnpersistedRunError",
    "Disclosure",
    "record_disclosures",
    "transition_quote",
    "legal_transitions",
    "current_mode",
    "history_modes",
    "InvalidTransitionError",
    "underwriting_blockers",
    "assess_confidence",
    "ConfidenceAssessment",
    "ConfidenceBand",
    "emit_quote_telemetry",
    "QUOTE_CREATED_EVENT_TYPE",
]
