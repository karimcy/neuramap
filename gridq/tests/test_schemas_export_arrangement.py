"""Tests for the ExportArrangement schema (issue 018 / WS-A4).

ExportArrangement is an additive, optional sub-model of SiteModel describing a site's export /
route-to-market arrangement (route, counterparty, aggregator cut, export tariff). It is a data
contract only — no pricing logic. The frozen-contract rule: adding it is additive, defaults to
None, and is hash-stable when absent (the canonical run-input is byte-identical to pre-A4), while
a SET value enters the identity as a real input must.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

import gridq.schemas as s
from gridq.provenance.hashing import hash_json
from gridq.quotes.assembly import _canonical_site_dump

# --------------------------------------------------------------- the model


def test_all_default_instance_is_meaningful() -> None:
    arrangement = s.ExportArrangement()
    assert arrangement.route == s.ExportRoute.UNKNOWN
    assert arrangement.counterparty is None
    assert arrangement.aggregator_cut_pct is None
    assert arrangement.export_tariff_gbp_per_kwh is None
    assert arrangement.notes is None


def test_full_arrangement_is_valid() -> None:
    arrangement = s.ExportArrangement(
        route=s.ExportRoute.FLEXIBILITY_AGGREGATOR,
        counterparty="Some Aggregator Ltd",
        aggregator_cut_pct=0.2,
        export_tariff_gbp_per_kwh=0.08,
        notes="Contracted via aggregator; 20% take on gross flex revenue.",
    )
    assert arrangement.route == s.ExportRoute.FLEXIBILITY_AGGREGATOR
    assert arrangement.aggregator_cut_pct == 0.2
    assert arrangement.export_tariff_gbp_per_kwh == 0.08


@pytest.mark.parametrize("route", list(s.ExportRoute))
def test_every_route_value_is_accepted(route: s.ExportRoute) -> None:
    assert s.ExportArrangement(route=route).route == route


@pytest.mark.parametrize("bad_cut", [1.5, -0.1, 1.0001, -0.0001])
def test_aggregator_cut_out_of_range_is_rejected(bad_cut: float) -> None:
    with pytest.raises(ValidationError):
        s.ExportArrangement(aggregator_cut_pct=bad_cut)


@pytest.mark.parametrize("boundary_cut", [0.0, 1.0, 0.5])
def test_aggregator_cut_boundaries_are_accepted(boundary_cut: float) -> None:
    assert s.ExportArrangement(aggregator_cut_pct=boundary_cut).aggregator_cut_pct == boundary_cut


def test_negative_export_tariff_is_rejected() -> None:
    with pytest.raises(ValidationError):
        s.ExportArrangement(export_tariff_gbp_per_kwh=-0.01)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        s.ExportArrangement(agregator_cut_pct=0.2)  # type: ignore[call-arg]  # typo -> ValidationError


def test_round_trips_through_json() -> None:
    arrangement = s.ExportArrangement(
        route=s.ExportRoute.PPA_EXPORT, counterparty="Offtaker Ltd", aggregator_cut_pct=0.15
    )
    restored = s.ExportArrangement.model_validate(arrangement.model_dump(mode="json"))
    assert restored == arrangement


# --------------------------------------------------- SiteModel wiring (A4)


def test_sitemodel_defaults_export_arrangement_none(
    make_site: Callable[..., s.SiteModel],
) -> None:
    assert make_site().export_arrangement is None


def test_sitemodel_accepts_export_arrangement_and_round_trips(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = make_site().model_copy(
        update={
            "export_arrangement": s.ExportArrangement(
                route=s.ExportRoute.PPA_EXPORT, aggregator_cut_pct=0.2
            )
        }
    )
    restored = s.SiteModel.model_validate(site.model_dump(mode="json"))
    assert restored.export_arrangement is not None
    assert restored.export_arrangement.route == s.ExportRoute.PPA_EXPORT
    assert restored.export_arrangement.aggregator_cut_pct == 0.2
    assert restored == site


# --------------------------------------------- hash stability (frozen contract)


def test_canonical_site_dump_pops_absent_export_arrangement(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """Absent (None) export_arrangement is POPPED from the canonical dump, so a pre-existing
    site's input_hash / run_id is byte-stable across the A4 schema growth; a set value stays in
    the dump — a real input must enter the identity (the D42 pop-if-None pattern)."""
    site = make_site()
    dump = _canonical_site_dump(site)
    assert "export_arrangement" not in dump  # identical bytes to the pre-A4 dump

    site2 = site.model_copy(
        update={"export_arrangement": s.ExportArrangement(route=s.ExportRoute.PPA_EXPORT)}
    )
    dump2 = _canonical_site_dump(site2)
    assert "export_arrangement" in dump2  # a real input must enter the identity


def test_input_hash_bytes_unchanged_when_export_arrangement_none(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """The hashed canonical bytes for a None-arrangement site equal the pre-A4 shape exactly."""
    site = make_site()
    canonical = _canonical_site_dump(site)
    assert "export_arrangement" not in canonical  # popped → no A4 bytes enter the hash input

    # Reconstruct the pre-A4 canonical dump faithfully: the model dump (created_at excluded),
    # with the A4 key gone entirely and the other pop-if-None fields (provisioning_requirements,
    # and the Revenue-Data-Drive stream_consents) applied exactly as _canonical_site_dump does.
    raw = site.model_dump(mode="json", exclude={"created_at"})
    assert raw["export_arrangement"] is None  # the field IS on the model now...
    del raw["export_arrangement"]  # ...but never entered the pre-A4 dump
    if raw.get("provisioning_requirements") is None:
        raw.pop("provisioning_requirements", None)
    if raw.get("stream_consents") is None:
        raw.pop("stream_consents", None)
    # D45's derived_assumptions is the newest pop-if-None field. Its presence here is the POINT of
    # the test, not an exemption: `canonical` above has already dropped it, so adding it to this
    # reconstruction is what proves the D45 schema change left every pre-existing run identity
    # byte-identical. NOTE this list must gain every future optional SiteModel field — the test
    # fails loudly rather than silently letting a new field move the hash, which is the behaviour
    # we want, but it does mean the failure reads as a regression when it is really a reminder.
    if raw.get("derived_assumptions") is None:
        raw.pop("derived_assumptions", None)
    # D53's local_flex_awards is the newest pop-if-None field (2026-08-18). Same reasoning as
    # derived_assumptions above: `assembly._canonical_run_input` pops it only when it is falsy, so a
    # site that declares no award keeps its pre-D53 identity byte-for-byte, while a site that DOES
    # carry an award moves its own hash - required, because an award changes what is priced.
    if raw.get("local_flex_awards") is None:
        raw.pop("local_flex_awards", None)

    assert canonical == raw
    assert hash_json(canonical) == hash_json(raw)


def test_demo_site_loads_and_absent_arrangement_is_omitted() -> None:
    """The demo now DECLARES an export arrangement (issue 018 forced consent), so it loads with one
    present. The additive-field hash-stability property - an absent/None export_arrangement is
    omitted from the canonical dump, so a pre-A4 site's input_hash is unchanged - is proven here on
    a None-arrangement copy (and end-to-end in the test below)."""
    from pathlib import Path

    from gridq.ingestion import load_site_model_json

    demo_path = Path(__file__).resolve().parents[1] / "examples" / "demo_site" / "sitemodel.json"
    demo = load_site_model_json(demo_path)
    # Demo declares full consent + an export arrangement now (issue 018); it still loads cleanly.
    assert demo.export_arrangement is not None
    assert demo.export_arrangement.route == s.ExportRoute.FLEXIBILITY_AGGREGATOR
    # Absent/None arrangement is omitted from the canonical dump (hash-stable for pre-A4 sites).
    assert "export_arrangement" not in _canonical_site_dump(
        demo.model_copy(update={"export_arrangement": None})
    )


def test_full_quote_input_hash_stable_for_none_and_changes_when_set(
    make_site: Callable[..., s.SiteModel],
) -> None:
    """End-to-end: a None (or absent) export_arrangement yields the same quote input_hash; a set
    value changes it. Reuses one result so the comparison isolates the site half of the hash."""
    from gridq.assumptions import load_assumptions
    from gridq.engine import BaselineEngine
    from gridq.quotes import assemble_quote_record

    assumptions = load_assumptions("gb_v0_1")
    engine = BaselineEngine()

    site = make_site()
    result = engine.solve(engine.build_optimisation_request(site, assumptions))

    q_default = assemble_quote_record(site, result, None, assumptions)
    q_explicit_none = assemble_quote_record(
        site.model_copy(update={"export_arrangement": None}), result, None, assumptions
    )
    assert q_default.input_hash == q_explicit_none.input_hash  # None == absent

    site_set = site.model_copy(
        update={"export_arrangement": s.ExportArrangement(route=s.ExportRoute.PPA_EXPORT)}
    )
    q_set = assemble_quote_record(site_set, result, None, assumptions)
    assert q_set.input_hash != q_default.input_hash  # a real input enters the identity
