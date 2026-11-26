"""The DEMO engine's progress side channel (C035) — the exact path the web app runs.

`DemoEngine` mirrors `MilpEngine`: `set_progress_sink` threads a presentation-only sink into the
two-stage sizing search and then fires exactly one post-sizing ``"dispatch"`` phase for the
full-year re-dispatch of the chosen spec. Existing coverage exercises the sink on the
`MilpEngine`/two-stage path and the FastAPI run-job endpoint (with a monkeypatched pipeline), but
the DEMO wiring — the path the web run screen actually drives — had none: stripping it kept the
suite green.

This is a real MILP solve over the committed synthetic demo load (no marker: the repo runs its
solver-dependent tests unmarked, and linopy/HiGHS are a hard dependency). It asserts the sink
narrates the sizing search and then the single dispatch re-solve, in that order.
"""

from __future__ import annotations

from gridq.app.demo import DemoEngine, load_demo_site
from gridq.assumptions import load_assumptions

#: The demo runs on the same placeholder library the golden demo test pins (`gb_v1_4`).
ASSUMPTION_VERSION = "gb_v1_4"


def test_demo_engine_progress_narrates_sizing_then_one_dispatch() -> None:
    ticks: list[str] = []
    engine = DemoEngine()
    engine.set_progress_sink(lambda phase, done, total: ticks.append(phase))

    site = load_demo_site()
    result = engine.solve(
        engine.build_optimisation_request(site, load_assumptions(ASSUMPTION_VERSION))
    )

    # A real spec was selected -> the post-sizing dispatch phase ran.
    assert result.selected_power_kw
    # (a) the sizing search narrated at least one phase (the demo site takes the two-stage path,
    # whose first solve site is "coarse").
    sizing_ticks = [p for p in ticks if p in ("coarse", "refine", "confirm", "polish", "sizing")]
    assert sizing_ticks, "the sizing search emitted no progress phase"
    assert "coarse" in ticks
    # (b) exactly one dispatch phase, emitted AFTER the sizing search completes.
    assert ticks.count("dispatch") == 1
    assert ticks.index("dispatch") > ticks.index("coarse")
