"""The value-stream registry — the generic container the core consumes streams through.

This realises the "versioned stream registry" the ``OptimisationResult`` contract refers to
(docs/03_data_contracts.md): a stable, ordered set of :class:`ValueStream` objects keyed by
their ``MarketStream`` id. The class is **market-agnostic** — it holds whatever streams it is
built with. ``default_registry()`` is the v1 composition root: it assembles the GB value
stack from ``gb_streams``. A second market would get its own builder (e.g.
``ie_value_streams()``) wrapped in a registry — **not** a ``market == "GB"`` branch in the core.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from gridq.schemas import MarketStream
from gridq.value_stack.gb_streams import gb_value_streams
from gridq.value_stack.interface import ValueStream


class StreamRegistry:
    """An ordered, id-keyed collection of value streams.

    Insertion order is preserved (it is the canonical registry order). Duplicate stream
    ids are rejected so the registry is unambiguous.
    """

    def __init__(self, streams: Iterable[ValueStream]) -> None:
        self._by_id: dict[MarketStream, ValueStream] = {}
        for stream in streams:
            if stream.stream_id in self._by_id:
                raise ValueError(f"duplicate stream id in registry: {stream.stream_id.value!r}")
            self._by_id[stream.stream_id] = stream

    def get(self, stream_id: MarketStream) -> ValueStream:
        """Return the stream for ``stream_id`` (raises ``KeyError`` if absent)."""
        try:
            return self._by_id[stream_id]
        except KeyError as exc:
            raise KeyError(f"no value stream registered for {stream_id!r}") from exc

    def all(self) -> list[ValueStream]:
        """All registered streams, in registry order."""
        return list(self._by_id.values())

    def stream_ids(self) -> list[MarketStream]:
        """All registered stream ids, in registry order."""
        return list(self._by_id)

    def requested(self, stream_ids: Iterable[MarketStream]) -> list[ValueStream]:
        """The registered streams for the requested ids, in registry order.

        Ids not in the registry are skipped (the registry is the authority on what exists).
        """
        wanted = set(stream_ids)
        return [s for sid, s in self._by_id.items() if sid in wanted]

    def __contains__(self, stream_id: object) -> bool:
        return stream_id in self._by_id

    def __iter__(self) -> Iterator[ValueStream]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


def default_registry() -> StreamRegistry:
    """The v1 default value stack — the GB stream set (composition root).

    A new market adds its own ``<market>_value_streams()`` builder and wraps it here; the
    core never branches on the market name.
    """
    return StreamRegistry(gb_value_streams())
