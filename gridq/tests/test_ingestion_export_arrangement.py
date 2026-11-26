"""Soft coherence validation for ExportArrangement (issue 018 / WS-A4).

If a site declares a ppa_export or flexibility_aggregator route but has no export path evidenced
(no export capacity AND no export MPAN), ingestion emits a WARNING — a ranked data-ask, never a
hard error, so the demo/intake path still runs. The aggregator_cut_pct range is enforced by the
schema Field, not by these checks.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import gridq.schemas as s
from gridq.ingestion import validate_site_model

_WARN_CODE = "site.export_route_without_export_path"


def _with_arrangement(
    make_site: Callable[..., s.SiteModel],
    arrangement: s.ExportArrangement | None,
    **overrides: object,
) -> s.SiteModel:
    update: dict[str, object] = {"export_arrangement": arrangement, **overrides}
    return make_site().model_copy(update=update)


@pytest.mark.parametrize("route", [s.ExportRoute.PPA_EXPORT, s.ExportRoute.FLEXIBILITY_AGGREGATOR])
def test_declared_route_without_export_path_warns(
    make_site: Callable[..., s.SiteModel], route: s.ExportRoute
) -> None:
    # make_site's default connection has mec_kw=None and consents.export_mpan=None → no path.
    site = _with_arrangement(make_site, s.ExportArrangement(route=route))
    report = validate_site_model(site)
    assert report.ok  # soft: a warning, never an error
    assert any(i.code == _WARN_CODE for i in report.warnings)


def test_no_warning_when_export_capacity_present(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _with_arrangement(
        make_site,
        s.ExportArrangement(route=s.ExportRoute.PPA_EXPORT),
        connection=s.ConnectionDetails(mic_kw=300.0, mec_kw=100.0),
    )
    report = validate_site_model(site)
    assert _WARN_CODE not in {i.code for i in report.issues}


def test_no_warning_when_export_mpan_present(
    make_site: Callable[..., s.SiteModel],
) -> None:
    site = _with_arrangement(
        make_site,
        s.ExportArrangement(route=s.ExportRoute.FLEXIBILITY_AGGREGATOR),
        consents=s.ConsentState(export_mpan="2000012345678"),
    )
    report = validate_site_model(site)
    assert _WARN_CODE not in {i.code for i in report.issues}


@pytest.mark.parametrize("route", [s.ExportRoute.NONE, s.ExportRoute.UNKNOWN])
def test_no_warning_for_none_or_unknown_route(
    make_site: Callable[..., s.SiteModel], route: s.ExportRoute
) -> None:
    site = _with_arrangement(make_site, s.ExportArrangement(route=route))
    report = validate_site_model(site)
    assert _WARN_CODE not in {i.code for i in report.issues}


def test_no_warning_when_no_arrangement(make_site: Callable[..., s.SiteModel]) -> None:
    site = _with_arrangement(make_site, None)
    assert site.export_arrangement is None
    report = validate_site_model(site)
    assert _WARN_CODE not in {i.code for i in report.issues}


def test_warning_is_soft_report_stays_ok(make_site: Callable[..., s.SiteModel]) -> None:
    site = _with_arrangement(make_site, s.ExportArrangement(route=s.ExportRoute.PPA_EXPORT))
    report = validate_site_model(site)
    assert report.ok  # the intake/demo path must still run
    assert report.errors == []
