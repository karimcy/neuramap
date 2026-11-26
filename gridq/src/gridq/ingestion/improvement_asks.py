"""Ranked improvement asks (M0).

Turns the raw missing-data findings (`missing_data.detect_missing_data`) into the **ranked
data ask** — the prioritised list of inputs the user must supply to most improve the quote.
This is the output artifact the System Brief calls "a ranked list of the data that would most
improve the quote — the back-and-forth becomes a precise ask".

The ranked ask is an *output*, not a blocker: the pipeline still runs degraded on whatever
data is present and says so. Nothing here invents or substitutes a value.
"""

from __future__ import annotations

from pydantic import Field

from gridq.ingestion.missing_data import MissingDataItem, detect_missing_data
from gridq.schemas.provenance import GridQModel
from gridq.schemas.site import SiteModel


class DataAsk(GridQModel):
    """One ranked ask: a missing input with its contiguous rank (1 = supply this first)."""

    rank: int
    ask_id: str
    title: str
    why: str
    category: str
    blocks: list[str] = Field(default_factory=list)
    detail: str | None = None


def rank_missing_data(items: list[MissingDataItem]) -> list[DataAsk]:
    """Sort missing items by impact (lower ``priority`` first) and assign contiguous ranks.

    Ordering is deterministic: by ``priority`` then ``ask_id`` (a stable tie-breaker), so the
    same site always yields the same ranking.
    """
    ordered = sorted(items, key=lambda i: (i.priority, i.ask_id))
    return [
        DataAsk(
            rank=rank,
            ask_id=item.ask_id,
            title=item.title,
            why=item.why,
            category=item.category,
            blocks=item.blocks,
            detail=item.detail,
        )
        for rank, item in enumerate(ordered, start=1)
    ]


def build_ranked_data_asks(
    site: SiteModel,
    *,
    wholesale_requested: bool = False,
) -> list[DataAsk]:
    """Detect missing data on ``site`` and return it as a ranked list of `DataAsk`s."""
    return rank_missing_data(detect_missing_data(site, wholesale_requested=wholesale_requested))


def data_asks_to_missing_list(asks: list[DataAsk]) -> list[str]:
    """Render ranked asks into the ``SiteModel.missing_data`` list[str] form.

    Produces short, ordered strings like ``"1. 12 months of half-hourly meter data
    (blocks: data_tier:T2, underwritten_quote)"`` suitable for embedding on the SiteModel.
    """
    lines: list[str] = []
    for ask in asks:
        blocks = f" (blocks: {', '.join(ask.blocks)})" if ask.blocks else ""
        lines.append(f"{ask.rank}. {ask.title}{blocks}")
    return lines


def format_data_asks(asks: list[DataAsk]) -> str:
    """Render the ranked ask as a human-readable block for a memo / installer email."""
    if not asks:
        return "No outstanding data asks — all expected inputs are present."
    out = ["Ranked data ask (supply in this order to most improve the quote):", ""]
    for ask in asks:
        out.append(f"{ask.rank}. {ask.title}")
        out.append(f"   Why: {ask.why}")
        if ask.detail:
            out.append(f"   Detail: {ask.detail}")
        if ask.blocks:
            out.append(f"   Blocks: {', '.join(ask.blocks)}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
