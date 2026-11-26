"""Manual quote benchmark — the human-time baseline for the cost-compression thesis.

One :class:`ManualBenchmarkRecord` per hand-built quote: how long it took, how many
rounds of installer/customer back-and-forth, what data was missing, and the outcome.
These rows are the *control group* — the cost of a quote **before** the system compresses
it. They must be captured at the start, honestly; retrofitted, the data is worthless
(System Brief, Acceptance test 2; ``docs/manual_benchmark.md``).

This is a perimeter / telemetry record — it never feeds a price. A benchmark converts to
a :class:`gridq.schemas.TelemetryEvent` (``event_type="manual_benchmark"``) so it sits in
the same dataset as system quotes.

**No fabricated data.** A benchmark records *measured* human effort on a real hand-built
quote. ``human_minutes`` must be a real, positive measurement (over-counting is fine;
guessing is not). Example/illustrative rows must be clearly labelled and never treated as
a real input — see ``examples/manual_benchmark_entry_example.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from enum import StrEnum

from pydantic import Field

from gridq.schemas.enums import DataTier, QuoteOutcome
from gridq.schemas.provenance import GridQModel
from gridq.schemas.telemetry import TelemetryEvent

# The event_type marker that distinguishes a manual baseline from a system quote in the
# shared telemetry dataset.
MANUAL_BENCHMARK_EVENT_TYPE = "manual_benchmark"

#: Backward-compatible alias — the commercial-outcome enum is now the canonical
#: ``gridq.schemas.QuoteOutcome``, so manual benchmarks and system quotes share one vocabulary.
ManualQuoteOutcome = QuoteOutcome


class ManualQuoteType(StrEnum):
    """The kind of artifact the hand-built quote produced."""

    INDICATIVE = "indicative"
    UNDERWRITTEN = "underwritten"
    PROPOSAL = "proposal"
    OTHER = "other"


class ManualBenchmarkRecord(GridQModel):
    """One hand-built quote, measured. Inherits ``GridQModel`` (``extra='forbid'`` +
    ``schema_version``) so an unknown/typo field is a validation error.

    ``human_minutes`` is required and must be positive — a benchmark with no recorded
    effort is not a benchmark. ``date`` and ``data_tier`` are required so every row is
    placeable in time and by data quality. The remaining fields default sensibly.
    """

    benchmark_id: str
    site_name: str
    contributor: str
    date: date
    data_tier: DataTier
    human_minutes: float = Field(gt=0, description="total wall-clock effort, all people")
    number_of_back_and_forth_iterations: int = Field(default=0, ge=0)
    missing_data_categories: list[str] = Field(default_factory=list)
    source: str = Field(default="manual_quote", description="provenance of the benchmark")
    quote_type: ManualQuoteType = ManualQuoteType.INDICATIVE
    quote_outcome: ManualQuoteOutcome = ManualQuoteOutcome.PENDING
    notes: str | None = None
    linked_quote_id: str | None = None


# --- construction ----------------------------------------------------------


def create_manual_benchmark_record(
    *,
    benchmark_id: str,
    site_name: str,
    contributor: str,
    date: date | str,
    data_tier: DataTier | str,
    human_minutes: float,
    number_of_back_and_forth_iterations: int = 0,
    missing_data_categories: Sequence[str] | None = None,
    source: str = "manual_quote",
    quote_type: ManualQuoteType | str = ManualQuoteType.INDICATIVE,
    quote_outcome: ManualQuoteOutcome | str = ManualQuoteOutcome.PENDING,
    notes: str | None = None,
    linked_quote_id: str | None = None,
) -> ManualBenchmarkRecord:
    """Build and validate a :class:`ManualBenchmarkRecord`.

    Raises ``pydantic.ValidationError`` for an invalid record (e.g. missing/zero/negative
    ``human_minutes``, an unknown ``data_tier``, or an unknown enum value). Strings are
    coerced to the right types by Pydantic (ISO date, enum members).
    """
    return ManualBenchmarkRecord(
        benchmark_id=benchmark_id,
        site_name=site_name,
        contributor=contributor,
        date=date,  # type: ignore[arg-type]  # pydantic coerces str -> date
        data_tier=data_tier,  # type: ignore[arg-type]
        human_minutes=human_minutes,
        number_of_back_and_forth_iterations=number_of_back_and_forth_iterations,
        missing_data_categories=list(missing_data_categories or []),
        source=source,
        quote_type=quote_type,  # type: ignore[arg-type]
        quote_outcome=quote_outcome,  # type: ignore[arg-type]
        notes=notes,
        linked_quote_id=linked_quote_id,
    )


# --- markdown rendering ----------------------------------------------------

_COLUMNS: tuple[str, ...] = (
    "benchmark_id",
    "date",
    "site",
    "source",
    "contributor",
    "data tier",
    "human minutes",
    "iterations",
    "missing data",
    "quote type",
    "outcome",
    "linked quote",
    "notes",
)


def _cell(value: object) -> str:
    """Render a cell, escaping pipes so a stray ``|`` can't break the table."""
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        text = "; ".join(str(v) for v in value) if value else "—"
    elif isinstance(value, StrEnum):
        text = value.value
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _row(record: ManualBenchmarkRecord) -> str:
    cells = (
        record.benchmark_id,
        record.date.isoformat(),
        record.site_name,
        record.source,
        record.contributor,
        record.data_tier.value,
        f"{record.human_minutes:g}",
        record.number_of_back_and_forth_iterations,
        record.missing_data_categories,
        record.quote_type.value,
        record.quote_outcome.value,
        record.linked_quote_id,
        record.notes,
    )
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def render_manual_benchmark_markdown(
    records: ManualBenchmarkRecord | Iterable[ManualBenchmarkRecord],
    *,
    include_header: bool = True,
) -> str:
    """Render one record or many as a Markdown table.

    With ``include_header`` (default) the column header + separator are emitted, so the
    result is a complete, pasteable table. With ``include_header=False`` only the data
    row(s) are returned (to append under an existing table).
    """
    if isinstance(records, ManualBenchmarkRecord):
        records = [records]
    body = [_row(r) for r in records]
    if not include_header:
        return "\n".join(body)
    header = "| " + " | ".join(_COLUMNS) + " |"
    separator = "|" + "|".join("---" for _ in _COLUMNS) + "|"
    return "\n".join([header, separator, *body])


# --- telemetry conversion --------------------------------------------------


def convert_benchmark_to_telemetry_event(
    record: ManualBenchmarkRecord,
    *,
    event_id: str | None = None,
) -> TelemetryEvent:
    """Convert a manual benchmark into a :class:`gridq.schemas.TelemetryEvent`.

    The event is marked ``event_type="manual_benchmark"`` so it is distinguishable from a
    system-generated quote while living in the same dataset. ``quote_id`` is the linked
    system quote if one exists, else a stable ``manual:<benchmark_id>`` id. ``created_at``
    is derived deterministically from the benchmark ``date`` (it is metadata only and
    never enters a computed number).
    """
    summary_bits = [
        f"source={record.source}",
        f"quote_type={record.quote_type.value}",
        f"outcome={record.quote_outcome.value}",
    ]
    if record.notes:
        summary_bits.append(record.notes)
    notes = "manual benchmark · " + " · ".join(summary_bits)

    return TelemetryEvent(
        event_id=event_id or f"{record.benchmark_id}:manual_benchmark",
        quote_id=record.linked_quote_id or f"manual:{record.benchmark_id}",
        event_type=MANUAL_BENCHMARK_EVENT_TYPE,
        data_tier=record.data_tier,
        human_minutes=record.human_minutes,
        contributor=record.contributor,
        installer_iterations=record.number_of_back_and_forth_iterations,
        missing_data_count=len(record.missing_data_categories),
        outcome=record.quote_outcome,
        notes=notes,
        created_at=datetime(record.date.year, record.date.month, record.date.day),
    )
