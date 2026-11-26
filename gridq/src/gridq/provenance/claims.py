"""The provenance claim a generated pack is allowed to make about its own figures.

A pack's footer asserts where its numbers came from. That assertion is itself a claim, and it can
be false — which is worse than a missing figure, because a reader who believes it stops checking.

## What went wrong

The six 2026-08-13 client packs carried this footer::

    Every figure on this page is copied from run record quote_run_a30b80c0a4d44c4b through the
    output view model and formatted for display. The generator performs no arithmetic: it cannot
    create, adjust or reconcile a number, and an absent figure is labelled with the reason it is
    absent.

Two independent things made it false, and both are structural rather than accidental:

1. **The cited record did not exist.** No ``runs/quote_run_a30b80c0a4d44c4b`` was ever written; the
   figures the reviewer traced (£7,072.62, £4,505.80, the ``Contracted`` firmness on
   ``WHOLESALE_P415``, the ``study_basis_export_in_floor`` gate status, 375.0 kW / 3,000.0 kWh)
   were only ever in a session temp file. "Copied from run record X" cannot be checked when X is
   not an artifact, so the sentence promised a verifiability the pack did not have.
2. **Not every figure is copied, and the generator does do arithmetic.** The renderer genuinely
   does none (``tests/test_consulting_report.py`` asserts that over its AST), but the seam beneath
   it does: ``gridq.outputs.viewmodel._split`` computes ``round(sum(...), 2)`` for the priced-floor
   and merchant-upside totals, ``gridq.ingestion.build_ranked_data_asks`` re-derives the ranked
   asks from the site record at render time, and ``gridq.outputs.sensitivities`` computes the
   Low/Central/High rows whenever the record carries none. The £11,578.42 floor a reader sees is a
   render-time sum of two record values, not a record value.

## The fix, and why it is not a softening

The temptation is to widen the sentence until it cannot be wrong ("figures are derived from run
data"). That is worse than the false claim: it withdraws the only assertion a reviewer could have
tested. So the claim here is **narrower and stronger**: it names the file each figure came from,
publishes that file's SHA-256, and then **enumerates exhaustively** the small set of values that
are recomputed rather than copied. A reader can falsify any part of it.

The claim is only constructible from a `PublicationReceipt`, and a receipt is only obtainable by
having actually written the files (`gridq.quotes.persistence.persist_run`). There is deliberately
no way to state the claim about a run that was never persisted — the sentence and the evidence for
it are produced by the same call.
"""

from __future__ import annotations

from gridq.schemas.provenance import GridQModel

#: Identifier for this wording. Bumped when the claim's *meaning* changes, so a pack's footer can
#: be traced to the exact sentence that was in force when it was rendered.
CLAIM_VERSION = "persisted-run-record-v1"

#: Every value a pack shows that is **not** a verbatim copy off a persisted file. This list is the
#: load-bearing half of the claim: the sentence is only true while it is exhaustive, so a new
#: render-time computation must be added here (and the claim re-read) rather than left implicit.
#:
#: Each entry names the value, the operation, and the persisted inputs it is computed from — enough
#: for a reader to reproduce it by hand from the same two files.
RECOMPUTED_AT_RENDER: tuple[str, ...] = (
    "the priced-floor and merchant-upside totals — the sum of the record's own per-stream "
    "annual revenues, rounded to the penny (gridq.outputs.viewmodel._split)",
    "the operating-year count — the length of the record's own P50 cashflow series less its "
    "year-0 entry (gridq.outputs.viewmodel._cashflows)",
    "the ranked data asks — re-derived from the persisted site record by "
    "gridq.ingestion.build_ranked_data_asks, not read from the run record",
    "the Low / Central / High sensitivity rows, and only when the run record carries none of its "
    "own — then computed from the record's financial case against the pinned assumption release "
    "(gridq.outputs.sensitivities)",
    "the best evaluated candidate shown when no system is recommended — selected at render time "
    "as the highest priced floor among the record's own CONFIRMED sizing-grid points (proxy points "
    "are "
    "excluded because they score a typical period scaled to a year and read high), and labelled a "
    "candidate measurement rather than a recommendation (gridq.outputs.viewmodel._best_candidate)",
)


class RunArtifact(GridQModel):
    """One persisted file of a run, pinned by content so tampering is detectable."""

    filename: str
    sha256: str
    size_bytes: int


class PublicationReceipt(GridQModel):
    """Proof that a run's evidence was on disk before a pack was rendered from it.

    Written as ``provenance.json`` beside the artifacts it pins. Holding one means the files listed
    in ``artifacts`` existed with those exact contents; it is what turns "copied from run record X"
    from an assertion into something a later reader can test.

    **Deterministic by construction.** Nothing here comes from the clock or from an absolute path:
    ``record_created_at`` is the record's own metadata and filenames are relative to the run
    directory, so re-publishing the same run rewrites the same bytes.
    """

    quote_id: str
    input_hash: str
    assumption_version: str | None = None
    code_version: str | None = None
    record_schema_version: str
    #: The record's own ``created_at``, copied — never the publication wall-clock, which would make
    #: the receipt non-reproducible and put a second, conflicting timestamp on the run.
    record_created_at: str
    artifacts: list[RunArtifact]
    claim_version: str = CLAIM_VERSION
    recomputed_at_render: list[str]

    def artifact(self, filename: str) -> RunArtifact | None:
        """The pinned artifact named ``filename``, or ``None`` when the run has no such file."""
        return next((a for a in self.artifacts if a.filename == filename), None)


def publication_claim(receipt: PublicationReceipt, *, run_dir_label: str | None = None) -> str:
    """The exact provenance sentence a pack published from ``receipt`` may state.

    ``run_dir_label`` is how the run directory should be named to the reader (default
    ``runs/<quote_id>``) — a label, never a filesystem path, so a pack does not leak the absolute
    layout of the machine that produced it.

    The sentence is true **only** of a pack rendered from these files. That is why it cannot be
    built from a `QuoteRecord`: a record in memory says nothing about what is on disk.
    """
    label = run_dir_label or f"runs/{receipt.quote_id}"
    record = receipt.artifact("quote.json")
    if record is None:
        raise ValueError(
            f"receipt for {receipt.quote_id} pins no quote.json, so no pack may claim its figures "
            "were copied from a persisted run record"
        )
    site = receipt.artifact("site.json")

    parts = [
        f"Every figure on this page is copied verbatim from the persisted run record "
        f"{receipt.quote_id}, held at {label}/quote.json ({record.sha256}).",
    ]
    if site is not None:
        parts.append(
            "Figures a caption attributes to the site record are copied verbatim from the site "
            f"model persisted beside it, {label}/site.json ({site.sha256})."
        )
    else:
        parts.append(
            "No site model was persisted with this run, so nothing on this page is attributed to "
            "one and the site-derived sections state that they are unavailable."
        )
    parts.append(
        f"Both hashes are recorded in {label}/provenance.json, which was written before this page "
        "was rendered; this page was then rendered by re-reading those files, so every copied "
        "figure can be re-read at its source by anyone holding this pack."
    )
    parts.append(
        "A copied figure is only ever formatted for display: nothing between the file and the page "
        "can create, adjust or reconcile it, and a figure the record does not carry is labelled "
        "with the reason it is absent rather than filled in."
    )
    n = len(receipt.recomputed_at_render)
    if n:
        listed = "; ".join(
            f"({i}) {item}" for i, item in enumerate(receipt.recomputed_at_render, 1)
        )
        parts.append(
            f"Exactly {n} value(s) on this page are recomputed rather than copied, and this is the "
            f"complete list: {listed}. Each is a deterministic function of the same persisted "
            f"files and the pinned assumption release "
            f"{receipt.assumption_version or 'unstamped'}; nothing else on this page is derived."
        )
    else:
        parts.append("No value on this page is recomputed; every one is a copy.")
    return " ".join(parts)


__all__ = [
    "CLAIM_VERSION",
    "RECOMPUTED_AT_RENDER",
    "PublicationReceipt",
    "RunArtifact",
    "publication_claim",
]
