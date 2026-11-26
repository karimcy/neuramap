"""Ingestion assembly (M0) — turn loaded inputs into a graded, ask-annotated `SiteModel`.

This is the seam between the raw loaders and the deterministic core. It does two things, and
**invents nothing**:

- `populate_missing_data` — runs the ranked data-ask detector over a site and writes the result
  into ``SiteModel.missing_data`` (the field the loaders leave empty). This is where
  `build_ranked_data_asks` finally feeds the SiteModel rather than being hand-filled in examples.
- `assemble_site_model` — optionally folds a real half-hourly summary into ``load_data`` and
  re-grades ``data_tier`` from the measured coverage, then populates the data asks.

Both honour the schema invariants by round-tripping through `SiteModel.model_validate` whenever
a field a validator polices changes (``load_data`` / ``data_tier``) — a `model_copy(update=...)`
would silently skip those validators and could mint an invalid site (e.g. T2 with no coverage).
The cheap ``missing_data``-only update uses `model_copy`, since no validator touches that field.
"""

from __future__ import annotations

from gridq.ingestion.data_tier import infer_data_tier_from_hh_summary
from gridq.ingestion.hh_data import HalfHourlySummary, to_load_data_summary
from gridq.ingestion.improvement_asks import build_ranked_data_asks, data_asks_to_missing_list
from gridq.schemas.site import SiteModel

# Fields the HH fold must not silently wipe: `to_load_data_summary` leaves these None (it carries
# only measured coverage), so a known prior value is preserved rather than dropped.
#
# The half-hourly PROVENANCE fields are here for a sharper reason (hostile review N9). A modelled
# series is a CSV on disk exactly like a metered one, so the summary built from it cannot tell the
# difference and leaves the basis None. Dropping the site's recorded basis therefore silently
# promoted every generated series to METERED — and with it the confidence ceiling from `medium` to
# `high`, which is precisely the distinction validation Gate 2 exists to protect. Every Grundon
# site is modelled, so this defeated the gate on four of the five live sites.
_PRESERVED_LOAD_FIELDS = (
    "annual_kwh",
    "max_import_kw",
    "max_export_kw",
    "half_hourly_basis",
    "half_hourly_reference_profile",
    "half_hourly_scaled_to",
)


def populate_missing_data(site: SiteModel, *, wholesale_requested: bool = False) -> SiteModel:
    """Return ``site`` with ``missing_data`` populated from the ranked data asks.

    The ranked list is deterministic (see `improvement_asks.rank_missing_data`). Uses
    `model_copy` because ``missing_data`` is a free ``list[str]`` no validator constrains.
    """
    asks = build_ranked_data_asks(site, wholesale_requested=wholesale_requested)
    return site.model_copy(update={"missing_data": data_asks_to_missing_list(asks)})


def assemble_site_model(
    site: SiteModel,
    *,
    hh_summary: HalfHourlySummary | None = None,
    wholesale_requested: bool = False,
) -> SiteModel:
    """Fold an optional half-hourly summary into ``site`` and populate the ranked data asks.

    With no ``hh_summary`` this is just `populate_missing_data`. With one, ``load_data`` is
    rebuilt from the measured coverage (preserving a prior ``annual_kwh`` / ``max_import_kw`` /
    ``max_export_kw`` the summary does not itself carry) and ``data_tier`` is re-graded via
    `infer_data_tier_from_hh_summary`. The result is re-validated, so an inconsistent fold
    (e.g. a too-sparse file that grades to T0 with no annual figure anywhere) raises a
    ``pydantic.ValidationError`` rather than producing a silently invalid site — the caller
    decides whether to surface that as a warning and proceed un-folded.
    """
    if hh_summary is None:
        return populate_missing_data(site, wholesale_requested=wholesale_requested)

    new_load = to_load_data_summary(hh_summary).model_dump(mode="json")
    old_load = site.load_data.model_dump(mode="json")
    for field in _PRESERVED_LOAD_FIELDS:
        if new_load.get(field) is None and old_load.get(field) is not None:
            new_load[field] = old_load[field]

    has_annual = site.annual_consumption_kwh is not None or new_load.get("annual_kwh") is not None
    new_tier = infer_data_tier_from_hh_summary(hh_summary, has_annual_kwh=has_annual)

    dump = site.model_dump(mode="json")
    dump["load_data"] = new_load
    dump["data_tier"] = new_tier.value
    folded = SiteModel.model_validate(dump)

    return populate_missing_data(folded, wholesale_requested=wholesale_requested)
