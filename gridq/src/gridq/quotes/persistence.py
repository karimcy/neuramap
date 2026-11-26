"""Durable persistence for a run, and the one publication path that cannot outrun it.

A pack's central figure is worth exactly as much as a later reader's ability to check it. The six
2026-08-13 client packs failed that test completely: each cited a run record ``quote_run_*`` that
was never written anywhere, so the moment the producing session's temp directory was cleared the
packs' numbers became permanently unverifiable. Nobody outside that session could ever have checked
them — not the reviewer, not a lender, not us.

## The rule

**A pack is rendered from the file, not from memory.** `publish_run` writes the run's artifacts,
re-reads them off disk, and renders from what it read back. So a pack whose record is not on disk is
not "discouraged" or "flagged" — it is unconstructible on this path, because the bytes the reader
can fetch are literally the bytes the pack was built from. Round-tripping also catches a record that
serialises but does not deserialise, which an in-memory render would ship silently.

`require_persisted_run` is the same guarantee as a precondition, for callers that render directly.

## What is written

``quote.json`` · ``site.json`` · ``telemetry.json`` — the run — plus ``provenance.json``, a
`PublicationReceipt` pinning each file's SHA-256 and enumerating the values a pack recomputes rather
than copies. The receipt is what lets the pack's footer make a narrow, testable claim instead of a
broad false one; see `gridq.provenance.claims`.

Layout is deliberately the same as `gridq.app.store` (same directory, same filenames), so the UI
reloads a run published here and a run saved there identically. That duplication is known and
flagged: the app store predates this module and should eventually delegate to it.

## Not a privacy change

The store stays gitignored. Persisting is what makes a figure checkable by the people holding the
pack; it is not publication of the customer's data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gridq.provenance.claims import (
    RECOMPUTED_AT_RENDER,
    PublicationReceipt,
    RunArtifact,
    publication_claim,
)
from gridq.provenance.hashing import hash_file
from gridq.schemas import QuoteRecord, SiteModel, TelemetryEvent

if TYPE_CHECKING:
    from gridq.quotes.pipeline import QuoteRunResult

_REPO_ROOT = Path(__file__).resolve().parents[3]  # src/gridq/quotes/persistence.py -> repo root

RECORD_FILE = "quote.json"
SITE_FILE = "site.json"
TELEMETRY_FILE = "telemetry.json"
RECEIPT_FILE = "provenance.json"


class RunStoreError(RuntimeError):
    """A run's artifacts could not be written, or what was written did not read back."""


class UnpersistedRunError(RunStoreError):
    """A pack was asked for from a run whose record is not on disk.

    This is the failure the six 2026-08-13 packs should have raised. It is not a warning: a pack
    that cites a record no reader can fetch is making an unverifiable claim about money, and there
    is no degraded version of that worth shipping. The fix is always to persist the run, never to
    relax the check.
    """


def run_store_dir(root: Path | None = None) -> Path:
    """Where run artifacts live: ``root``, else ``$GRIDQ_RUN_STORE``, else ``<repo>/runs``.

    Mirrors `gridq.app.store.run_store_dir` so both writers and the UI agree on one location.
    ``root`` exists so tests write to ``tmp_path`` and never touch the real store.
    """
    if root is not None:
        return Path(root)
    env = os.environ.get("GRIDQ_RUN_STORE")
    return Path(env) if env else _REPO_ROOT / "runs"


def run_dir(quote_id: str, root: Path | None = None) -> Path:
    """The directory a run with ``quote_id`` is persisted under (no traversal, no empty ids)."""
    if not quote_id or "/" in quote_id or "\\" in quote_id or quote_id in {".", ".."}:
        raise ValueError(f"unsafe run id: {quote_id!r}")
    return run_store_dir(root) / quote_id


@dataclass(frozen=True)
class PersistedRun:
    """A run read back **off disk**, with the receipt pinning the files it was read from.

    ``quote`` here is never the in-memory record a caller passed in: it is the record as
    deserialised from ``quote.json``. That is the whole point — a pack built from this object is
    built from the artifact a reader can fetch.
    """

    quote_id: str
    directory: Path
    quote: QuoteRecord
    site: SiteModel | None
    telemetry: TelemetryEvent | None
    receipt: PublicationReceipt

    @property
    def provenance_claim(self) -> str:
        """The exact provenance sentence a pack rendered from this run may print."""
        return publication_claim(self.receipt)


def _artifact(directory: Path, filename: str) -> RunArtifact:
    path = directory / filename
    return RunArtifact(filename=filename, sha256=hash_file(path), size_bytes=path.stat().st_size)


def persist_run(
    quote: QuoteRecord,
    site: SiteModel | None = None,
    telemetry: TelemetryEvent | None = None,
    *,
    root: Path | None = None,
) -> PersistedRun:
    """Write a run's artifacts and its receipt, then read them back and return what was read.

    Idempotent: ``quote_id`` is a pure function of the inputs + assumption/code version, so
    re-persisting the same run rewrites identical bytes (the receipt carries no wall-clock and no
    absolute path, so it is byte-stable too).

    Raises `RunStoreError` if the artifacts do not read back as what was written — a record that
    cannot be reloaded is not persisted evidence, whatever is sitting in the directory.
    """
    directory = run_dir(quote.quote_id, root)
    directory.mkdir(parents=True, exist_ok=True)

    (directory / RECORD_FILE).write_text(quote.model_dump_json(indent=2), encoding="utf-8")
    if site is not None:
        (directory / SITE_FILE).write_text(site.model_dump_json(indent=2), encoding="utf-8")
    if telemetry is not None:
        (directory / TELEMETRY_FILE).write_text(
            telemetry.model_dump_json(indent=2), encoding="utf-8"
        )

    artifacts = [_artifact(directory, RECORD_FILE)]
    for optional in (SITE_FILE, TELEMETRY_FILE):
        if (directory / optional).is_file():
            artifacts.append(_artifact(directory, optional))

    receipt = PublicationReceipt(
        quote_id=quote.quote_id,
        input_hash=quote.input_hash or "",
        assumption_version=quote.assumption_version,
        code_version=quote.code_version,
        record_schema_version=quote.schema_version,
        # The record's own timestamp, not now(): a second clock reading here would make the receipt
        # irreproducible and give the run two different "when"s.
        record_created_at=quote.created_at.isoformat(),
        artifacts=artifacts,
        recomputed_at_render=list(RECOMPUTED_AT_RENDER),
    )
    (directory / RECEIPT_FILE).write_text(receipt.model_dump_json(indent=2), encoding="utf-8")

    reloaded = load_persisted_run(quote.quote_id, root=root)
    if reloaded is None:
        raise RunStoreError(
            f"wrote {directory} but could not read it back — {quote.quote_id} is not persisted"
        )
    return reloaded


def load_persisted_run(quote_id: str, *, root: Path | None = None) -> PersistedRun | None:
    """Reload a run from disk, or ``None`` when the store holds no record for ``quote_id``.

    Raises `RunStoreError` on a directory that exists but is not a coherent run (unparseable
    record, missing receipt, or a record whose ``quote_id`` is not the one the directory claims).
    Silently treating a corrupt run as absent would let a pack be regenerated from something other
    than the record its footer names.
    """
    directory = run_dir(quote_id, root)
    record_path = directory / RECORD_FILE
    if not record_path.is_file():
        return None

    try:
        quote = QuoteRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RunStoreError(f"{record_path} does not parse as a QuoteRecord: {exc}") from exc

    # The directory name is the identity a reader is given; if the record inside disagrees, the
    # pack's citation points at the wrong evidence. (Legacy runs predate the `quote_` prefix, so
    # the invariant is directory == recorded id, never a prefix rule.)
    if quote.quote_id != quote_id:
        raise RunStoreError(
            f"{directory} holds a record for {quote.quote_id!r}, not {quote_id!r} — a pack citing "
            "this directory would point a reader at the wrong run"
        )

    receipt_path = directory / RECEIPT_FILE
    if not receipt_path.is_file():
        raise RunStoreError(
            f"{directory} has no {RECEIPT_FILE}: the record is on disk but nothing pins its "
            "contents, so a pack rendered from it cannot state where its figures came from. "
            "Re-persist the run with gridq.quotes.persist_run."
        )
    try:
        receipt = PublicationReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RunStoreError(f"{receipt_path} does not parse as a receipt: {exc}") from exc

    site = None
    site_path = directory / SITE_FILE
    if site_path.is_file():
        site = SiteModel.model_validate_json(site_path.read_text(encoding="utf-8"))
    telemetry = None
    telemetry_path = directory / TELEMETRY_FILE
    if telemetry_path.is_file():
        telemetry = TelemetryEvent.model_validate_json(telemetry_path.read_text(encoding="utf-8"))

    return PersistedRun(
        quote_id=quote_id,
        directory=directory,
        quote=quote,
        site=site,
        telemetry=telemetry,
        receipt=receipt,
    )


def is_persisted(quote_id: str, *, root: Path | None = None) -> bool:
    """Whether ``quote_id``'s record is on disk and coherent."""
    try:
        return load_persisted_run(quote_id, root=root) is not None
    except RunStoreError:
        return False


def require_persisted_run(quote_id: str, *, root: Path | None = None) -> PersistedRun:
    """The persisted run for ``quote_id``, or `UnpersistedRunError`.

    The precondition for generating any pack: a document that names a run record must be able to
    hand its reader that record. Call this before rendering when not going through `publish_run`.
    """
    persisted = load_persisted_run(quote_id, root=root)
    if persisted is None:
        raise UnpersistedRunError(
            f"no run record is persisted for {quote_id} (looked in "
            f"{run_dir(quote_id, root)}). A pack may not be generated from it: its figures would "
            "cite a record no later reader could fetch, which is what made the 2026-08-13 packs "
            "unverifiable. Persist the run first with gridq.quotes.persist_run."
        )
    return persisted


@dataclass(frozen=True)
class PublishedRun:
    """A persisted run and the pack rendered from it, with the files each was written to."""

    persisted: PersistedRun
    #: ``gridq.outputs.OutputArtifacts``; typed loosely so this module stays free of the outputs
    #: import at module scope (the dependency is one-directional and lazy on purpose).
    artifacts: object
    written: tuple[Path, ...]

    @property
    def quote_id(self) -> str:
        return self.persisted.quote_id

    @property
    def provenance_claim(self) -> str:
        return self.persisted.provenance_claim


def publish_run(
    run: QuoteRunResult,
    *,
    root: Path | None = None,
    write_documents: bool = True,
) -> PublishedRun:
    """Persist ``run``, then render its pack **from the persisted files**.

    The ordering is the guarantee, not a convention: `persist_run` returns the record as read back
    off disk, and that reloaded record — not ``run.quote`` — is what the renderer is given. There is
    therefore no state in which this returns a pack whose record is absent from the store.

    ``write_documents=False`` renders without writing the documents beside the record, for callers
    that place them elsewhere.
    """
    persisted = persist_run(run.quote, run.site, run.telemetry, root=root)

    # Imported here, not at module scope: outputs depends on the quote layer, so a top-level import
    # would close a cycle, and the quote layer must stay light (no report/chart imports on the
    # pricing path).
    from gridq.outputs import generate_artifacts

    assumptions: object | None = None
    if persisted.quote.assumption_version:
        from gridq.assumptions import AssumptionError, load_assumptions

        try:
            assumptions = load_assumptions(persisted.quote.assumption_version)
        except AssumptionError:
            assumptions = None  # honest degrade — the pack keeps its pending notes

    # Hand the renderer THIS publication's own receipt and store root. Without them the footer
    # resolves the claim against the DEFAULT store, so a run persisted to a custom root would
    # disclaim evidence sitting right beside it — the pack would say "not persisted, this is a
    # working render" while its own files were one directory away. The receipt is already in hand
    # here, so passing it is the difference between a true claim and an accidentally false one.
    artifacts = generate_artifacts(
        persisted.quote,
        persisted.site,
        assumptions=assumptions,
        provenance_claim=persisted.provenance_claim,
        run_store_root=root,
    )

    written: list[Path] = []
    if write_documents:
        for filename, text in (
            ("memo.md", artifacts.memo_markdown),
            ("proposal.md", artifacts.proposal_markdown),
            ("report.html", artifacts.report_html),
            ("consulting_report.html", artifacts.consulting_report_html),
        ):
            path = persisted.directory / filename
            path.write_text(text, encoding="utf-8")
            written.append(path)

    return PublishedRun(persisted=persisted, artifacts=artifacts, written=tuple(written))


__all__ = [
    "PersistedRun",
    "PublishedRun",
    "RECEIPT_FILE",
    "RECORD_FILE",
    "RunStoreError",
    "SITE_FILE",
    "TELEMETRY_FILE",
    "UnpersistedRunError",
    "is_persisted",
    "load_persisted_run",
    "persist_run",
    "publish_run",
    "require_persisted_run",
    "run_dir",
    "run_store_dir",
]
