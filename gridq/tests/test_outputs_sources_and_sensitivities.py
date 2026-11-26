"""Tests for the W8 closing slice: real scenario sensitivities + per-assumption sources.

The memo's dormant SensitivityRow slot renders from real `run_scenario_set` output (identity
bands honestly noted — a spread is never fabricated; per-scenario DSCR n/a until debt terms
exist), and the C020/C023 provenance moat displays where a lender looks: every stream-cited
assumption with its value/status/confidence/source from the pinned release. Both keep honest
pending notes when the library is absent.
"""

from __future__ import annotations

import gridq.schemas as s
from gridq.assumptions import load_assumptions
from gridq.outputs.generate import generate_artifacts
from gridq.outputs.sensitivities import scenario_sensitivity_rows
from gridq.outputs.viewmodel import build_view_model
from gridq.value_stack.gb_streams import local_flex_awarded_zone_clear


def _record_with_cited_streams(rich_quote) -> s.QuoteRecord:
    result = rich_quote.result.model_copy(
        update={
            "value_streams": [
                rich_quote.result.value_streams[0].model_copy(
                    update={
                        "assumptions_used": [
                            "capacity_market.clearing_price_gbp_per_kw_yr",
                            "round_trip_efficiency.ac_round_trip",
                        ]
                    }
                ),
                *rich_quote.result.value_streams[1:],
            ]
        }
    )
    return rich_quote.quote.model_copy(update={"optimisation_result": result})


def test_sensitivity_rows_come_from_real_scenario_runs(rich_quote) -> None:
    rows = scenario_sensitivity_rows(rich_quote.case, load_assumptions("gb_v0_1"))
    assert [r.label for r in rows] == ["Low", "Central", "High"]
    for r in rows:
        assert r.min_dscr is None  # per-scenario DSCR needs debt terms (B001) — n/a, not guessed
        assert r.irr_pct is not None  # the case's own cashflows support a project IRR
    # Identity bands (no real Low/High spread supplied) are named, never dressed as a spread.
    assert rows[0].note is not None and "identity multipliers" in rows[0].note
    assert rows[1].note is None  # Central IS the base case


def test_memo_renders_the_sensitivities_table_when_the_library_is_supplied(rich_quote) -> None:
    artifacts = generate_artifacts(
        rich_quote.quote, rich_quote.site, assumptions=load_assumptions("gb_v0_1")
    )
    assert "**Sensitivities**" in artifacts.memo_markdown
    assert "| Low |" in artifacts.memo_markdown
    assert "not attached to this run" not in artifacts.memo_markdown
    # Merged renderer: the record-persisted sensitivities table (heading note + table),
    # not the branch's <h4> block.
    assert "Scenario sensitivities (Low / Central / High)" in artifacts.report_html


def test_assumption_sources_render_from_the_pinned_release(rich_quote) -> None:
    quote = _record_with_cited_streams(rich_quote)
    lib = load_assumptions("gb_v1_0")  # the citation-bearing release
    vm = build_view_model(quote, assumptions=lib)
    keys = [r.key for r in vm.assumption_sources]
    assert "capacity_market.clearing_price_gbp_per_kw_yr" in keys
    row = next(r for r in vm.assumption_sources if r.key.startswith("capacity_market"))
    assert row.status == "needs_review"
    assert row.source == lib.get("capacity_market", "clearing_price_gbp_per_kw_yr").source
    artifacts = generate_artifacts(quote, rich_quote.site, assumptions=lib)
    assert "## Assumptions & sources" in artifacts.memo_markdown
    assert "capacity_market.clearing_price_gbp_per_kw_yr" in artifacts.memo_markdown
    assert row.source.split(";")[0][:40] in artifacts.memo_markdown
    assert "Assumptions &amp; sources" in artifacts.report_html


def test_pending_notes_without_the_library(rich_quote) -> None:
    quote = _record_with_cited_streams(rich_quote)
    artifacts = generate_artifacts(quote, rich_quote.site)  # no assumptions supplied
    assert "render when the release library is supplied" in artifacts.memo_markdown
    assert "not attached to this run" in artifacts.memo_markdown  # sensitivities pending too


def test_unresolvable_key_is_shown_absent_never_dropped(rich_quote) -> None:
    result = rich_quote.result.model_copy(
        update={
            "value_streams": [
                rich_quote.result.value_streams[0].model_copy(
                    update={"assumptions_used": ["nonexistent_section.some_key"]}
                )
            ]
        }
    )
    quote = rich_quote.quote.model_copy(update={"optimisation_result": result})
    vm = build_view_model(quote, assumptions=load_assumptions("gb_v0_1"))
    row = next(r for r in vm.assumption_sources if r.key == "nonexistent_section.some_key")
    assert row.status == "absent"
    assert "not present" in row.source


# --- the register resolves a LEAF inside a dict-valued assumption (2026-08-19) ----------------

_SUBKEY_RELEASE = "gb_v1_12"
_ZONE_ROW = "local_flex_piclo.awarded_zone_clears.ssen_sepd_wallingford_vaou_wa"


def _quote_citing(rich_quote, *keys: str) -> s.QuoteRecord:
    """The rich fixture's record with one stream citing exactly ``keys``."""
    result = rich_quote.result.model_copy(
        update={
            "value_streams": [
                rich_quote.result.value_streams[0].model_copy(
                    update={"assumptions_used": list(keys)}
                ),
                *rich_quote.result.value_streams[1:],
            ]
        }
    )
    return rich_quote.quote.model_copy(update={"optimisation_result": result})


def test_a_dotted_sub_key_resolves_inside_its_dict_valued_assumption(rich_quote) -> None:
    """THE DEFECT (fixed 2026-08-19). A cited LEAF must not render as absent from the release.

    `_assumption_sources` split a cited key on the FIRST dot only, so the two keys
    `optimisation/valuation.py` credits for the local-flex availability leg,
    `local_flex_piclo.awarded_zone_clears.<zone>.availability_h_per_year` (:774) and
    `...availability_gbp_per_mw_h` (:766), resolved to a NAME of
    `awarded_zone_clears.<zone>.availability_h_per_year`. `AssumptionLibrary.get` looks that up as
    `sect[name]` and raised `KeyError`, so both rows rendered "n/a", status "absent", source "(key
    not present in the supplied release)" for GBP 174.99/MW/h and 136 h/yr, which ARE in
    gb_v1_12 and which PRICE the leg.

    A pack telling an underwriter that a sourced, priced figure is absent from its own release is
    the single thing the assumptions register exists to prevent (D57 limb 4: the approvals and
    values are STATED, not hidden; D59 limb 3: per-stream, its figure and the keys it cited).

    The sub-value inherits the PARENT entry's unit, status, confidence and source, which is correct
    rather than convenient: those belong to the `awarded_zone_clears` assumption, which is the entry
    carrying the citation. Only the VALUE narrows to the leaf the stream priced from.
    """
    lib = load_assumptions(_SUBKEY_RELEASE)
    parent = lib.get("local_flex_piclo", "awarded_zone_clears")
    hours_key = f"{_ZONE_ROW}.availability_h_per_year"
    rate_key = f"{_ZONE_ROW}.availability_gbp_per_mw_h"

    vm = build_view_model(_quote_citing(rich_quote, hours_key, rate_key), assumptions=lib)
    rows = {r.key: r for r in vm.assumption_sources}

    hours = rows[hours_key]
    assert hours.status != "absent", (
        "the register reports a key the pricing path credited as missing from the release"
    )
    assert hours.value == "136.0"
    assert hours.status == parent.status.value
    assert hours.confidence == parent.confidence.value
    assert hours.source == parent.source

    rate = rows[rate_key]
    assert rate.status != "absent"
    assert rate.value == "174.99"

    # And it reaches the reader, not just the view model.
    artifacts = generate_artifacts(
        _quote_citing(rich_quote, hours_key), rich_quote.site, assumptions=lib
    )
    assert hours_key in artifacts.memo_markdown
    assert "not present in the supplied release" not in artifacts.memo_markdown


def test_the_producer_and_the_register_agree_on_the_shape_of_that_key(rich_quote) -> None:
    """REACHABILITY without a solve: the key the PRODUCER builds is the key the register resolves.

    `valuation.py` builds these keys as ``f"{clear_key}.availability_h_per_year"`` where
    ``clear_key`` comes from `gb_streams.local_flex_awarded_zone_clear`. If that function's row key
    ever gained or lost a dot, the register would silently go back to reporting "absent" while
    the leg carried on pricing. This pins the two ends together off the real producer.
    """
    lib = load_assumptions(_SUBKEY_RELEASE)
    site = rich_quote.site.model_copy(
        update={
            "connection": rich_quote.site.connection.model_copy(update={"dno_region": "SSEN_SEPD"})
        }
    )
    row, clear_key, _notes = local_flex_awarded_zone_clear(site, lib)
    assert row is not None, "the SSEN_SEPD awarded row must resolve for this site"
    assert clear_key == _ZONE_ROW
    assert row.get("availability_h_per_year") == 136.0

    produced = f"{clear_key}.availability_h_per_year"
    vm = build_view_model(_quote_citing(rich_quote, produced), assumptions=lib)
    resolved = next(r for r in vm.assumption_sources if r.key == produced)
    assert resolved.status != "absent"
    assert resolved.value == "136.0"


def test_a_sub_key_the_release_really_lacks_is_still_reported_absent(rich_quote) -> None:
    """The fix must not make every dotted key resolve to something. A real absence still shows.

    And the message names WHICH hop failed, so a reader can tell "the release has no such zone row"
    from "the zone row has no such field" - the two have different data asks.
    """
    lib = load_assumptions(_SUBKEY_RELEASE)
    bogus = f"{_ZONE_ROW}.no_such_field"
    vm = build_view_model(_quote_citing(rich_quote, bogus), assumptions=lib)
    row = next(r for r in vm.assumption_sources if r.key == bogus)
    assert row.status == "absent"
    assert "no_such_field" in row.source
    assert row.value == "n/a"
