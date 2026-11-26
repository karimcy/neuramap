"""Access logging for confidential half-hourly data (M0, security floor).

The System Brief's non-functional floor: site half-hourly data is *confidential* and every access
is *logged*. This module records an append-only entry for each read of a site's HH data, so who
touched which file (and when) is auditable. ``load_half_hourly_csv`` records an entry by
construction, so the log cannot be bypassed by forgetting to call it.

The sink is in-memory by default (no side effects in tests/library use); when the
``GRIDQ_ACCESS_LOG`` environment variable points at a file, each entry is *also* appended there as
one JSON line (the repo's files-first convention, D4). Entries are metadata about access, never an
input to the deterministic pricing path — they carry a wall-clock timestamp and are kept entirely
out of the quote/run hashes.
"""

from __future__ import annotations

import os
from datetime import datetime

from gridq.provenance.hashing import stable_json_dumps
from gridq.schemas.provenance import GridQModel

#: Environment variable naming an optional JSONL file the access log is also appended to.
ACCESS_LOG_ENV = "GRIDQ_ACCESS_LOG"


class HhAccessEvent(GridQModel):
    """One recorded access to a site's half-hourly data."""

    file_ref: str
    action: str = "read"
    site_id: str | None = None
    actor: str | None = None
    row_count: int | None = None
    is_personal_data: bool = False
    accessed_at: datetime
    #: Why the DPA guard let this read through (WP-GRC-05): ``not_customer_data``, ``dpa``,
    #: ``transient_unstored`` or ``quarantined_legacy_unevidenced``. ``None`` on entries written
    #: by a caller that predates the guard.
    dpa_basis: str | None = None


#: In-process append-only log. Read it with `get_access_log`; reset it with `clear_access_log`.
_LOG: list[HhAccessEvent] = []


def record_hh_access(
    file_ref: str,
    *,
    action: str = "read",
    site_id: str | None = None,
    actor: str | None = None,
    row_count: int | None = None,
    is_personal_data: bool = False,
    accessed_at: datetime | None = None,
    dpa_basis: str | None = None,
) -> HhAccessEvent:
    """Record one HH-data access; append it to the in-memory log (and the JSONL file if set).

    ``actor`` defaults to the ``GRIDQ_CONTRIBUTOR`` env var (Olly) when not given, so a read
    is attributed without every caller threading the identity through.
    """
    event = HhAccessEvent(
        file_ref=file_ref,
        action=action,
        site_id=site_id,
        actor=actor if actor is not None else os.environ.get("GRIDQ_CONTRIBUTOR"),
        row_count=row_count,
        is_personal_data=is_personal_data,
        accessed_at=accessed_at if accessed_at is not None else datetime.now(),
        dpa_basis=dpa_basis,
    )
    _LOG.append(event)
    path = os.environ.get(ACCESS_LOG_ENV)
    if path:
        # Create the parent dir so a benign missing-dir does not break the read; a genuine IO
        # error is left to surface (fail-closed: don't read confidential data you can't audit).
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(stable_json_dumps(event) + "\n")
    return event


def get_access_log() -> list[HhAccessEvent]:
    """Return a copy of the in-process access log (most recent last)."""
    return list(_LOG)


def clear_access_log() -> None:
    """Clear the in-process access log (test/teardown helper)."""
    _LOG.clear()
