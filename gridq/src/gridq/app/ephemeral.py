"""Show once, store nothing: a run held in process memory only (WP-GRC-05, owner decision (a)).

The owner decided on 2026-11-23 that a visitor's run without a DPA is shown once and nothing is
stored (gridq_dpa_guard_spec.md section 6.1). The four run screens (results, system spec, memo and
proposal, telemetry) each reload the run, so "shown once" means: the derived result (quote, site,
telemetry) is held here, in this process's memory, bound to the visitor who ran it, for at most
`TTL_SECONDS`, then dropped. It is never serialised, never written under ``runs/``, and a restart
or another worker loses it (a 404, the same as an unknown run). The raw upload never comes here: it
lives in the guard's transient workspace and is deleted when the run ends.

Fable 5.2 ruling Q5 (2026-11-25): 15 minutes, a capped count, purged on expiry. Holding the result
until the job ends rather than the request ends, and the 15-minute window, are recorded as
deviations from the spec's "deleted when the request ends" for the owner to see.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Final

from gridq.app.store import StoredRun
from gridq.schemas import QuoteRecord, SiteModel, TelemetryEvent

#: How long a show-once result stays viewable.
TTL_SECONDS: Final = 15 * 60
#: How many show-once results one process holds at most (oldest dropped first).
MAX_HELD: Final = 64


@dataclass(frozen=True)
class _Held:
    run: StoredRun
    expires_at: float


_HELD: OrderedDict[str, _Held] = OrderedDict()
_LOCK = threading.Lock()


def _purge(now: float) -> None:
    for key in [k for k, v in _HELD.items() if v.expires_at <= now]:
        del _HELD[key]
    while len(_HELD) > MAX_HELD:
        _HELD.popitem(last=False)


def hold(
    quote: QuoteRecord,
    site: SiteModel | None,
    telemetry: TelemetryEvent | None,
    *,
    owner_id: str | None,
    now: float | None = None,
) -> str:
    """Hold a run in memory for `TTL_SECONDS`; return its ``quote_id``."""
    clock = time.monotonic() if now is None else now
    with _LOCK:
        _HELD[quote.quote_id] = _Held(
            run=StoredRun(quote=quote, site=site, telemetry=telemetry, owner_id=owner_id),
            expires_at=clock + TTL_SECONDS,
        )
        _HELD.move_to_end(quote.quote_id)
        _purge(clock)
    return quote.quote_id


def get(quote_id: str, *, now: float | None = None) -> StoredRun | None:
    """The held run, or ``None`` once it has expired (or was never held here)."""
    clock = time.monotonic() if now is None else now
    with _LOCK:
        _purge(clock)
        held = _HELD.get(quote_id)
        return held.run if held is not None else None


def is_held(quote_id: str) -> bool:
    return get(quote_id) is not None


def clear() -> None:
    """Drop every held run (test helper)."""
    with _LOCK:
        _HELD.clear()
