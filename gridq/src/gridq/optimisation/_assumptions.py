"""Small assumption-library read helpers shared inside the optimisation package.

The MILP holds **no** private £/%/ratio constants — every number is read from the live
`AssumptionLibrary` (or the `SiteModel`). These helpers mirror the read conventions already used
by ``finance.cashflows`` and ``value_stack.gb_streams`` (never raising on absence, excluding
booleans/structured values from numeric reads, conservative per-duration flooring) so the engine
resolves the same numbers the same way.
"""

from __future__ import annotations

from gridq.assumptions import Assumption, AssumptionLibrary, AssumptionStatus


def maybe(assumptions: AssumptionLibrary, section: str, name: str) -> Assumption | None:
    """Fetch an assumption if present, else ``None`` (never fabricates one)."""
    try:
        return assumptions.get(section, name)
    except KeyError:
        return None


def numeric(a: Assumption | None) -> float | None:
    """The numeric value of an assumption, or ``None`` (booleans/structured values excluded)."""
    if a is None:
        return None
    v = a.value
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def is_confirmed(a: Assumption | None) -> bool:
    """True iff the assumption exists and its status is ``confirmed``."""
    return a is not None and a.status == AssumptionStatus.CONFIRMED


def placeholder_note(section: str, name: str, a: Assumption) -> str:
    """A loud label so a placeholder-derived £ can never be mistaken for a bankable number."""
    return (
        f"indicative only — NOT bankable: {section}.{name} = {a.value} {a.unit} is status "
        f"'{a.status.value}' (confidence {a.confidence.value}); source: {a.source}."
    )


def value_for_duration(table: object, duration_h: float) -> tuple[float | None, str | None]:
    """Per-duration table lookup (conservative flooring), as in ``gb_streams._derate_for_duration``.

    Picks the value for the largest tabulated duration ≤ ``duration_h`` (string keys '1'/'2'/'4'
    parsed as floats). Below the smallest tabulated point, uses that point's value and warns.
    Returns ``(value, warning|None)``.
    """
    if not isinstance(table, dict) or not table:
        return None, "per-duration table is missing or empty"
    try:
        pairs = sorted((float(k), float(v)) for k, v in table.items())
    except (TypeError, ValueError):
        return None, "per-duration table is malformed"
    chosen: float | None = None
    for d, val in pairs:
        if d <= duration_h:
            chosen = val
    if chosen is not None:
        return chosen, None
    return pairs[0][1], (
        f"duration {duration_h}h is below the smallest tabulated point ({pairs[0][0]}h); "
        "used that point's value conservatively"
    )


def ceiling_value_for_duration(table: object, duration_h: float) -> tuple[float | None, str | None]:
    """Per-duration table lookup taking the CEILING node — the conservative direction for a
    table that DECREASES with duration (D35, resolves F-002/C030).

    Warranty cycle caps fall as duration rises ('1' > '2' > '4' cycles/yr), so the floor lookup
    RELAXES the cap between nodes (a 3 h system inheriting the 2 h cap may cycle more than
    either bracketing warranty point allows). This lookup picks the smallest tabulated duration
    >= ``duration_h`` instead: the value never exceeds what any bracketing contract point
    allows, and no intermediate value is invented (north-star D28's no-interpolation stance is
    kept). On a tabulated node the node's own value is returned exactly (existing results
    unchanged). Above the largest tabulated point, uses that point's value and warns (the true
    cap for a longer duration is likely lower — needs_review until sourced warranty terms
    land). Returns ``(value, warning|None)``.
    """
    if not isinstance(table, dict) or not table:
        return None, "per-duration table is missing or empty"
    try:
        pairs = sorted((float(k), float(v)) for k, v in table.items())
    except (TypeError, ValueError):
        return None, "per-duration table is malformed"
    for d, val in pairs:
        if d >= duration_h:
            note = None
            if d != duration_h:
                note = (
                    f"duration {duration_h}h is between tabulated points; used the {d}h "
                    "node's value (ceiling lookup, the conservative direction for a "
                    "decreasing table — D35)"
                )
            return val, note
    last_d, last_val = pairs[-1]
    return last_val, (
        f"duration {duration_h}h is above the largest tabulated point ({last_d}h); used that "
        "point's value — likely optimistic for a decreasing table, needs_review until the "
        "sourced warranty terms land"
    )


def _duration_pairs(table: object) -> list[tuple[float, float]] | None:
    """``{'1h': 10.25, '2h': 17.47}`` or ``{'0.5': 1.51, '1': 2.98}`` as sorted (hours, value).

    Two key conventions are in the library for the same idea, so both are parsed here rather than
    at each call site: `capacity_market.study_basis_forward_gbp_per_kw_yr_by_duration.by_duration_h`
    keys on bare numbers and `balancing_mechanism.study_basis_marginal_value_gbp_per_kw_yr.
    method_b_bottom_up.gbp_per_kw_yr` keys on '1h'/'2h'/'4h'. Returns ``None`` when the table is
    absent, empty or malformed - never a partial parse, because a table read half-way is a table
    whose lookup silently answers off the wrong nodes.
    """
    if not isinstance(table, dict) or not table:
        return None
    pairs: list[tuple[float, float]] = []
    for key, value in table.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        text = str(key).strip().lower().removesuffix("h")
        try:
            pairs.append((float(text), float(value)))
        except (TypeError, ValueError):
            return None
    return sorted(pairs)


def duration_rescaled_rate(
    rate: float,
    *,
    table: object,
    duration_h: float,
    reference_duration_h: float,
    table_key: str,
    rate_unit: str = "GBP/kW/yr",
) -> tuple[float, str]:
    """Move a per-kW/yr rate quoted at ``reference_duration_h`` onto the candidate's own node.

    Returns ``(rescaled_rate, note)``. The note always states the basis, because a rate that has
    silently moved is worse than one that has not.

    **WHY THIS EXISTS (2026-08-19).** Two consented study-basis rates were applied to candidates of
    any duration with no duration term at all. The Balancing Mechanism rate is
    ``balancing_mechanism.study_basis_marginal_value_gbp_per_kw_yr.central_gbp_per_kw_yr_2h`` =
    GBP 17.47/kW/yr - the 2-HOUR row of a table the same key publishes as
    ``{1h: 10.25, 2h: 17.47, 4h: 28.74}`` - and it was being paid in full to a 0.5-hour asset. That
    is not an eligibility question: BM value for an energy-limited asset is the spread uplift times
    the MWh of throughput, and a 0.5 h asset moves a quarter of the MWh a 2 h asset moves per cycle.
    The library's own table says so, nearly halving from 2 h to 1 h. Measured overstatement at
    0.5 h: 1.70x.

    **FLOOR LOOKUP, NEVER INTERPOLATION** (D28/D35). The value for the largest tabulated node at or
    below ``duration_h`` is taken; below the smallest node, that node's value is taken and the note
    says so. No intermediate value is invented, so the figure is always a published one.

    **THE RATIO, not the node.** The rescale is ``rate x table[duration] / table[reference]``, which
    preserves whatever multiplier the consented rate already applied to the reference node (an
    aggregator cut, a route adjustment, a negotiated share). Reading ``table[duration]`` directly
    would silently discard it. Where the table cannot be read, or carries no reference node, the
    rate passes through UNCHANGED with a note naming the residual error and its direction - never a
    silent pass and never a fabricated factor.
    """
    pairs = _duration_pairs(table)
    if pairs is None:
        return rate, (
            f"RATE NOT DURATION-ADJUSTED: {table_key} could not be read as a per-duration "
            f"table, so the {rate_unit} {rate:,.2f} rate is applied to a {duration_h:g} h asset as "
            f"quoted at {reference_duration_h:g} h. DIRECTION OF THE RESIDUAL ERROR: OVERSTATED "
            "below the "
            "reference duration, understated above it."
        )
    reference = next((v for d, v in pairs if d == reference_duration_h), None)
    if reference is None or reference <= 0:
        return rate, (
            f"RATE NOT DURATION-ADJUSTED: {table_key} publishes no {reference_duration_h:g} h "
            f"node, so the multiplier the quoted rate applied to that node cannot be preserved "
            "and the "
            f"{rate_unit} {rate:,.2f} rate is applied to a {duration_h:g} h asset as quoted. "
            "DIRECTION OF THE RESIDUAL ERROR: OVERSTATED below the reference duration."
        )
    node_h, node = pairs[0]
    for d, v in pairs:
        if d <= duration_h:
            node_h, node = d, v
    below = duration_h < pairs[0][0]
    rescaled = rate * node / reference
    extrapolation = (
        f" The {duration_h:g} h asset is BELOW the smallest tabulated node, so the "
        f"{node_h:g} h node is used rather than an extrapolation below the sourced range "
        "(D28/D35: no interpolation, no invention) - which UNDERSTATES the haircut a shorter "
        "asset would really take."
        if below
        else ""
    )
    return rescaled, (
        f"DURATION-ADJUSTED: {rate_unit} {rate:,.2f} is quoted at the {reference_duration_h:g} h "
        f"node of {table_key} (GBP {reference:,.2f}/kW/yr) and this asset is {duration_h:g} h, so "
        "the "
        f"same multiplier is applied to that table's {node_h:g} h node (GBP {node:,.2f}/kW/yr): "
        f"{rate_unit} {rescaled:,.2f}. Floor lookup on the table's own published nodes, never an "
        f"interpolation.{extrapolation}"
    )
