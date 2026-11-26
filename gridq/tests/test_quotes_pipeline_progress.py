"""The presentation-only progress side channel through `generate_quote` (C035).

Guards the additive ``progress`` hook: (a) the three pipeline phases fire around solve / finance /
assembly, (b) the sink is duck-typed into an engine that exposes ``set_progress_sink``, and (c) it
is byte-neutral — ``progress=None`` (the default) produces an identical priced result and a sink
can never change a computed number.
"""

from __future__ import annotations

from collections.abc import Callable

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.engine import BaselineEngine
from gridq.engine.conformance import _result_dump
from gridq.quotes import generate_quote


class _SinkAwareEngine(BaselineEngine):
    """A BaselineEngine that also exposes the duck-typed progress setter (test fixture only)."""

    def __init__(self) -> None:
        self.received_sink: object | None = None

    def set_progress_sink(self, sink: object) -> None:
        self.received_sink = sink


def test_baseline_path_emits_exactly_the_three_pipeline_phases(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    phases: list[str] = []
    generate_quote(
        make_site(),
        lib,
        engine=BaselineEngine(),
        progress=lambda phase, done, total: phases.append(phase),
    )
    # BaselineEngine has no set_progress_sink, so only the pipeline's own three phases fire —
    # around solve, finance and assembly, in that order, exactly once each.
    assert phases == ["optimise", "finance", "finalise"]


def test_engine_with_setter_receives_the_sink(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    engine = _SinkAwareEngine()

    def sink(phase: str, done: int | None, total: int | None) -> None:
        return None

    generate_quote(make_site(), lib, engine=engine, progress=sink)
    assert engine.received_sink is sink


def test_progress_none_is_byte_neutral(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site()
    baseline = generate_quote(site, lib, engine=BaselineEngine())
    with_sink = generate_quote(site, lib, engine=BaselineEngine(), progress=lambda *args: None)
    # The side channel touches no computed number: the priced result, bankability and financial
    # case are identical whether or not a sink is attached (clock/identity fields normalised out).
    assert _result_dump(with_sink.result) == _result_dump(baseline.result)
    assert with_sink.bankable == baseline.bankable
    assert with_sink.financial_case == baseline.financial_case


def test_raising_sink_never_breaks_the_run(
    make_site: Callable[..., s.SiteModel],
) -> None:
    lib = load_assumptions("gb_v0_1")
    site = make_site()

    def boom(phase: str, done: int | None, total: int | None) -> None:
        raise RuntimeError("sink blew up")

    baseline = generate_quote(site, lib, engine=BaselineEngine())
    guarded = generate_quote(site, lib, engine=BaselineEngine(), progress=boom)
    assert _result_dump(guarded.result) == _result_dump(baseline.result)
    assert guarded.bankable == baseline.bankable
