"""Shared helpers for the perimeter data fetchers (:mod:`gridq.dev.fetch_*`).

**Developer/CLI tooling — OFF the pricing path.** Everything here is run *by hand* to pull a
provenance-pinned input from a public data source; it is **never** imported by ``gridq run``,
the solver, finance, quotes, or any pricing code (confirm with
``grep -R _fetch_common src/gridq/{optimisation,finance,quotes}``). It is a sibling of
:mod:`gridq.dev.fetch_prices` / :mod:`gridq.dev.team_sync` — coordination and tooling, not
product logic.

**Never fabricates.** A fetcher either returns the real bytes it pulled (hashed, sized, counted)
or raises :class:`FetchError` — it never writes a guessed value. The fetched numbers land in a
versioned ``gb_v1_x`` assumption release *by hand*, each transcribed against the committed
provenance sidecar this module writes; nothing here relaxes the not-bankable posture.

What this module gives the fetchers
-----------------------------------
- :func:`sha256_hex` — the sha256 of raw bytes (the provenance fingerprint of a fetch).
- :class:`ProvenanceSidecar` — the committed record of *what was fetched and when*
  (source URL, dataset id, fetched-UTC, licence, sha256, size, fetcher + args, record count).
- :func:`write_json` — a deterministic JSON writer (sorted keys, trailing newline) so an
  artifact / sidecar is byte-identical for identical content.
- :func:`http_get_bytes` — the single network seam (returns raw bytes for JSON *or* XLSX/CSV);
  tests monkeypatch this so the suite never touches the wire.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_HTTP_TIMEOUT_S = 90.0
_USER_AGENT = "gridq-fetch/1.0 (off-pricing-path dev tool; https://neura.energy)"


class FetchError(RuntimeError):
    """A network / decode / shape failure. Raised instead of ever fabricating a value."""


def sha256_hex(data: bytes) -> str:
    """Return the hex sha256 of ``data`` — the provenance fingerprint of a fetch."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ProvenanceSidecar:
    """The committed record of one fetch: enough to reproduce and audit the pull.

    Deliberately data-only (no computed values) — it pins *the bytes that were fetched*, so an
    assumption entry can cite ``sha256`` + ``fetched_utc`` + this sidecar's path and be audited
    against the exact source snapshot. ``fetched_utc`` is passed in (never read from the clock in
    a pure path) so a sidecar is byte-reproducible under test.
    """

    source_url: str
    dataset_id: str
    fetched_utc: str
    licence: str
    sha256: str
    size_bytes: int
    fetcher: str
    fetcher_args: dict[str, Any]
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        """A plain dict for deterministic JSON serialisation."""
        return asdict(self)


def build_sidecar(
    *,
    raw: bytes,
    source_url: str,
    dataset_id: str,
    fetched_utc: datetime,
    licence: str,
    fetcher: str,
    fetcher_args: dict[str, Any],
    record_count: int,
) -> ProvenanceSidecar:
    """Assemble a :class:`ProvenanceSidecar` from the raw fetched bytes + fetch context.

    ``sha256`` and ``size_bytes`` are derived from ``raw`` here so they always describe exactly
    the bytes that came off the wire; ``fetched_utc`` is normalised to a second-resolution ISO
    string (no microseconds) so the sidecar is stable.
    """
    return ProvenanceSidecar(
        source_url=source_url,
        dataset_id=dataset_id,
        fetched_utc=fetched_utc.replace(microsecond=0).isoformat(),
        licence=licence,
        sha256=sha256_hex(raw),
        size_bytes=len(raw),
        fetcher=fetcher,
        fetcher_args=dict(fetcher_args),
        record_count=record_count,
    )


def write_json(path: str | Path, obj: Any) -> Path:
    """Write ``obj`` to ``path`` as deterministic JSON (sorted keys, 2-space, trailing newline).

    Identical content ⇒ byte-identical output. Parent directories are created.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    destination.write_text(text, encoding="utf-8")
    return destination


def write_sidecar(path: str | Path, sidecar: ProvenanceSidecar) -> Path:
    """Write a :class:`ProvenanceSidecar` to ``path`` as deterministic JSON."""
    return write_json(path, sidecar.to_dict())


def http_get_bytes(url: str, *, timeout: float = _HTTP_TIMEOUT_S) -> bytes:
    """Perform one HTTP GET and return the raw response bytes (JSON, XLSX or CSV).

    This is the single network seam — tests monkeypatch it to return captured bytes, so the
    suite never touches the network. Raises :class:`FetchError` (never fabricates) on any
    connection, HTTP or timeout failure.
    """
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(
            f"request failed for {url}: {exc}. Nothing was written — this tool never fabricates."
        ) from exc


def decode_json(raw: bytes, *, url: str) -> Any:
    """Decode ``raw`` bytes as JSON, raising :class:`FetchError` (never guessing) on failure."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FetchError(f"non-JSON response from {url}: {exc}") from exc
