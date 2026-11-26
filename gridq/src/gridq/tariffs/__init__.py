"""Tariff arithmetic for LV, HV and EHV sites (WP-UW-07, roadmap A6-07).

A pure package: it imports `gridq.schemas` and nothing from `gridq.optimisation`, so the bill,
the published-example tests and the dispatch model all read one implementation, and the MILP
constraint library WP-SIZE-02 extracts can depend on it without a cycle.

- `gridq.tariffs.basis`: the H-01 record a derived VAT basis carries.
- `gridq.tariffs.hv_tariff_model`: CDCM and EDCM charges, from annual volumes (the DNO's own
  model algebra) or from a half-hourly series (billing determinants per month), as avoidability-
  tagged charge lines (H-02).
"""
