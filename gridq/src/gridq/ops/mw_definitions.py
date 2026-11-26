"""The three candidate demonstrator-MW definitions, as slotted formulas (WP-OPS-11).

Spec §15.3 names three candidates for what counts towards the demonstrator MW: **site load under
management** (A), **BESS MW actively dispatched** (B) and **verified flexible MW** (C). Each is
written here as an `MwDefinition` template (D143) whose open parameters are explicit
slots with every admissible option listed and no default (D147), and each term has one
binding (D144).

**Parity.** The three share the same slots for the window, resolution, statistic, coincidence
across sites, gap policy, quality failure and authority failure, and the same qualifying
conditions for control authority and the dispatch envelope. They differ only where the quantity
differs. That A's agreed-capacity basis and B's rated basis can be read from documents today,
while C needs telemetry, pre-registered baselines and the verifier interface, is a statement about
**what is measurable now**, not about which definition is better. This module recommends none and
records none as agreed: agreement is WP-OPS-02.
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction
from typing import Final

from gridq.contracts._base import NotApplicable, Unknown
from gridq.contracts.telemetry_series import Aspect, AssetKind, MeasurementRole, Quantity
from gridq.ops.dossier_model import DossierField
from gridq.ops.mw_bindings import (
    BindingTable,
    DossierFieldBinding,
    FrozenContractField,
    NotYetMeasurable,
    SchemaPointer,
    TermBinding,
    UnitFromQuantity,
)
from gridq.ops.mw_formula import (
    Add,
    AllOf,
    Branch,
    ChoiceSlot,
    Choose,
    ClipLow,
    Compare,
    Condition,
    Const,
    DayTypes,
    FlagIs,
    GapUnknownIfAny,
    Grain,
    Inadmissible,
    MinCoverage,
    MwDefinition,
    Node,
    NumberSlot,
    OnFail,
    Option,
    PercentileStat,
    Q,
    QualityGood,
    Resolution,
    Scale,
    SlotRef,
    Stat,
    Sub,
    Sum,
    Term,
    TermRef,
    TimeAgg,
    TimeBase,
    Unit,
    Where,
    Window,
)

__all__ = [
    "BINDINGS",
    "DEFINITIONS",
    "DEMONSTRATOR_WINDOW_CITATION",
    "SHARED_CONDITIONS",
    "SHARED_SLOTS",
    "definition",
]

#: The demonstrator window as the application states it (quoted in the grant-text register).
DEMONSTRATOR_WINDOW_CITATION: Final = (
    "Qu9 App - NeuraFlex.pdf, 'Exit Point / Route to Scale': \"independently verified live winter "
    'validation in Nov-Dec 2028"'
)
_FIRST_DAY: Final = date(2028, 11, 1)
_LAST_DAY: Final = date(2028, 12, 31)

_NA: Final = NotApplicable(
    kind="not_applicable", reason="structural option: the branch is in the tree"
)


def _ref(slot_id: str) -> SlotRef:
    return SlotRef(kind="slot", slot_id=slot_id)


def _t(term_id: str) -> TermRef:
    return TermRef(node="term", term_id=term_id)


def _k(value: int, unit: Unit) -> Const:
    return Const(node="const", value=Q.of(Fraction(value)), unit=unit)


def _opt(option_id: str, label: str, payload: object = _NA) -> Option:
    return Option.model_validate({"option_id": option_id, "label": label, "payload": payload})


def _choice(slot_id: str, label: str, *options: Option) -> ChoiceSlot:
    return ChoiceSlot(kind="choice_slot", slot_id=slot_id, label=label, options=options)


def _number(slot_id: str, label: str, unit: Unit) -> NumberSlot:
    return NumberSlot(kind="number_slot", slot_id=slot_id, label=label, unit=unit)


def _where(conditions: tuple[str, ...], on_fail: SlotRef | OnFail, body: Node) -> Where:
    return Where(node="where", conditions=conditions, on_fail=on_fail, body=body)


def _choose(slot_id: str, *branches: tuple[str, Node]) -> Choose:
    return Choose(
        node="choose",
        slot_id=slot_id,
        branches=tuple(Branch(option_id=o, body=b) for o, b in branches),
    )


def _on_fail(value: str) -> OnFail:
    return OnFail.model_validate({"kind": "on_fail", "value": value})


def _term(
    term_id: str,
    label: str,
    unit: Unit,
    grain: Grain,
    time_base: TimeBase,
    point: str,
    evidence_class: str,
) -> Term:
    return Term.model_validate(
        {
            "term_id": term_id,
            "label": label,
            "unit": unit,
            "grain": grain,
            "time_base": time_base,
            "measurement_point": point,
            "evidence_class": evidence_class,
        }
    )


# --------------------------------------------------------------------------------------------
# Shared slots, terms and conditions (identical in A, B and C)
# --------------------------------------------------------------------------------------------

SHARED_SLOTS: Final[tuple[ChoiceSlot | NumberSlot, ...]] = (
    _choice(
        "authority_fail",
        "What an interval contributes when the site is outside its dispatch envelope or Neura "
        "does not hold control authority",
        _opt("contributes_zero", "the interval counts, at zero", _on_fail("contributes_zero")),
        _opt("exclude_interval", "the interval is left out", _on_fail("exclude_interval")),
    ),
    _choice(
        "coincidence",
        "Whether sites are summed before the time statistic (coincident) or after it",
        _opt("coincident", "statistic of the portfolio sum in each interval"),
        _opt("non_coincident", "sum over sites of each site's own statistic"),
    ),
    _choice(
        "gap_policy",
        "What a missing reading in a qualifying interval does to the result",
        _opt(
            "min_coverage",
            "a result exists when at least the share the agreement states of qualifying intervals "
            "is read",
            MinCoverage(kind="min_coverage", ratio=_ref("gap_policy.min_coverage")),
        ),
        _opt(
            "unknown_if_any_gap",
            "any gap makes the result unknown",
            GapUnknownIfAny(kind="gap_unknown_if_any"),
        ),
    ),
    _number(
        "gap_policy.min_coverage",
        "Minimum share of qualifying intervals with a reading",
        Unit.RATIO,
    ),
    _choice(
        "quality_fail",
        "What an interval whose reading is not of good quality does",
        _opt("exclude_interval", "the interval is left out", _on_fail("exclude_interval")),
        _opt("site_not_counted", "the site is not counted", _on_fail("site_not_counted")),
    ),
    _choice(
        "resolution",
        "The interval length the statistic is taken over",
        _opt(
            "s1800", "30 minutes (settlement period)", Resolution(kind="resolution", seconds=1800)
        ),
        _opt("s60", "1 minute", Resolution(kind="resolution", seconds=60)),
    ),
    _choice(
        "statistic",
        "The statistic over the window's qualifying intervals",
        _opt("max", "maximum", Stat(kind="stat", name="max")),
        _opt("mean", "mean", Stat(kind="stat", name="mean")),
        _opt("min", "minimum", Stat(kind="stat", name="min")),
        _opt(
            "percentile",
            "nearest-rank percentile",
            PercentileStat(kind="percentile", p=_ref("statistic.p")),
        ),
    ),
    _number("statistic.p", "Percentile rank (a share between 0 and 1)", Unit.RATIO),
    _choice(
        "window",
        "The days and hours the figure is taken over",
        _opt(
            "demonstrator_days_all_day",
            "every day of the demonstrator window, all day",
            Window(
                kind="window",
                first_day=_FIRST_DAY,
                last_day=_LAST_DAY,
                period_citation=DEMONSTRATOR_WINDOW_CITATION,
                day_types=DayTypes(kind="day_types", value="all"),
                start_minute=Q.of(0),
                end_minute=Q.of(1440),
            ),
        ),
        _opt(
            "demonstrator_days_restricted",
            "the demonstrator window, restricted to the day types and hours the agreement states",
            Window(
                kind="window",
                first_day=_FIRST_DAY,
                last_day=_LAST_DAY,
                period_citation=DEMONSTRATOR_WINDOW_CITATION,
                day_types=_ref("window.day_types"),
                start_minute=_ref("window.start_minute"),
                end_minute=_ref("window.end_minute"),
            ),
        ),
    ),
    _choice(
        "window.day_types",
        "Which days of the window count",
        _opt("all", "all days", DayTypes(kind="day_types", value="all")),
        _opt("weekdays", "Monday to Friday", DayTypes(kind="day_types", value="weekdays")),
        _opt("weekends", "Saturday and Sunday", DayTypes(kind="day_types", value="weekends")),
    ),
    _number("window.end_minute", "End of the daily span, local minute (exclusive)", Unit.MIN),
    _number("window.start_minute", "Start of the daily span, local minute", Unit.MIN),
)

_SHARED_TERMS: Final = (
    _term(
        "control_authority",
        "Neura holds control authority over the site's dispatchable assets in the interval",
        Unit.FLAG,
        Grain.SITE,
        TimeBase.SERIES,
        "site edge controller authority state",
        "operational_telemetry",
    ),
    _term(
        "control_signoff",
        "The host's sign-off for closed-loop control at the site is registered",
        Unit.FLAG,
        Grain.SITE,
        TimeBase.STATIC,
        "evidence registry (IC-4 control_authority_signoff)",
        "registered_document",
    ),
    _term(
        "envelope_in_force",
        "An IC-3 dispatch envelope for the site is in force at the interval",
        Unit.FLAG,
        Grain.SITE,
        TimeBase.SERIES,
        "IC-3 envelope validity (valid_from inclusive, valid_until exclusive)",
        "contracted_document",
    ),
)

SHARED_CONDITIONS: Final = (
    Condition(
        condition_id="c_auth",
        label="Neura holds control authority over the site, with the host's sign-off",
        scope="per_interval",
        predicate=AllOf(
            kind="all",
            preds=(
                FlagIs(kind="flag", term_id="control_signoff"),
                FlagIs(kind="flag", term_id="control_authority"),
            ),
        ),
    ),
    Condition(
        condition_id="c_env",
        label="The interval lies inside the site's IC-3 dispatch envelope validity",
        scope="per_interval",
        predicate=FlagIs(kind="flag", term_id="envelope_in_force"),
    ),
)

_TELEMETRY_WPS: Final = ("WP-TEL-01", "WP-TEL-02", "WP-TEL-04")


def _telemetry(needs: str, *extra_wps: str) -> NotYetMeasurable:
    return NotYetMeasurable(
        kind="not_yet_measurable",
        category="telemetry_field",
        contract_id="NEW-IC-14",
        data_product="telemetry_store",
        expected_schema_prefix="ic14_",
        roadmap_wps=tuple(sorted({*_TELEMETRY_WPS, *extra_wps})),
        needs=needs,
    )


def _ptr(pointer: str, json_type: str) -> SchemaPointer:
    return SchemaPointer.model_validate({"pointer": pointer, "json_type": json_type})


_NO_SUFFIX: Final = NotApplicable(kind="not_applicable", reason="not a power field")
_NO_SPLIT: Final = NotApplicable(kind="not_applicable", reason="the signed value is the term")

#: Every IC-14 term reads one good curated value per interval (WP-TEL-01, D148 to D153).
_IC14_READ: Final = (
    "per interval, the IC-14 CuratedSeries of each matching point at settlement_30min or "
    "standard_1min: an Aggregate of quality good gives the value in W, which is {how}, as whole "
    "kW by w_to_kw ({rounding}). A CuratedGap, or an Aggregate of any other quality, is a gap: "
    "the term fails its quality condition, never a zero."
)


def _ic14(
    term: str,
    asset_kinds: tuple[AssetKind, ...],
    roles: tuple[MeasurementRole, ...],
    *,
    sign: str,
    split: str | None,
    rounding: str,
    how: str,
) -> TermBinding:
    unit = UnitFromQuantity.model_validate(
        {
            "kind": "unit_from_quantity",
            "asset_kinds": asset_kinds,
            "quantity": Quantity.ACTIVE_POWER,
            "roles": roles,
            "aspect": Aspect.WHOLE,
            "unit": "W",
            "resolutions": ("settlement_30min", "standard_1min"),
            "sign": sign,
            "split": _NO_SPLIT if split is None else split,
            "rounding": rounding,
        }
    )
    return TermBinding(
        term_id=term,
        binding=FrozenContractField(
            kind="frozen_contract_field",
            contract_id="IC-14",
            schema_stem="ic14_curated_series",
            version="1.0.0",
            pointers=(
                _ptr("/series_id", "string"),
                _ptr("/resolution", "string"),
                _ptr("/intervals/*/kind", "string"),
                _ptr("/intervals/*/quality", "string"),
                _ptr("/intervals/*/value", "integer"),
            ),
            unit_suffix=unit,
            derivation=_IC14_READ.format(how=how, rounding=rounding.replace("_", " ")),
        ),
    )


_AK, _MR = AssetKind, MeasurementRole
_BOUNDARY: Final = (_AK.SITE_BOUNDARY,)
_REVENUE: Final = (_MR.REVENUE_METER,)

_SHARED_BINDINGS: Final = (
    TermBinding(
        term_id="control_authority",
        binding=_telemetry(
            "a per-interval authority-state point for the site (who holds control), with a quality "
            "flag; a missing sample is a gap. IC-14 1.0.0 has no quantity for it, so an IC-14 "
            "minor must add one",
            "WP-EDGE-08",
        ),
    ),
    TermBinding(
        term_id="control_signoff",
        binding=FrozenContractField(
            kind="frozen_contract_field",
            contract_id="IC-4",
            schema_stem="ic4_evidence_object",
            version="1.2.0",
            pointers=(
                _ptr("/evidence_type", "string"),
                _ptr("/issuer/role", "string"),
                _ptr("/scope/kind", "string"),
                _ptr("/scope/site_id", "string"),
            ),
            unit_suffix=_NO_SUFFIX,
            derivation=(
                "true when an IC-4 object with evidence_type control_authority_signoff, issued by "
                "the host and scoped to the site, is valid on the window's first day "
                "(the control safety case, WP-EDGE-08, produces it)"
            ),
        ),
    ),
    TermBinding(
        term_id="envelope_in_force",
        binding=FrozenContractField(
            kind="frozen_contract_field",
            contract_id="IC-3",
            schema_stem="ic3_dispatch_envelope",
            version="1.0.0",
            pointers=(
                _ptr("/site_id", "string"),
                _ptr("/valid_from", "string"),
                _ptr("/valid_until", "string"),
            ),
            unit_suffix=_NO_SUFFIX,
            derivation=(
                "true at an interval whose start t has valid_from <= t < valid_until for an IC-3 "
                "envelope of the site (the dispatch envelope export, WP-SIZE-08, issues them)"
            ),
        ),
    ),
)


def _coincidence(site_series: Node) -> Choose:
    """The portfolio statistic, coincident or not: the one place the time aggregate sits."""

    def agg(body: Node) -> TimeAgg:
        return TimeAgg(
            node="time_agg",
            window=_ref("window"),
            resolution=_ref("resolution"),
            statistic=_ref("statistic"),
            gap_policy=_ref("gap_policy"),
            body=body,
        )

    return _choose(
        "coincidence",
        ("coincident", agg(Sum(node="sum", over="sites", body=site_series))),
        ("non_coincident", Sum(node="sum", over="sites", body=agg(site_series))),
    )


def _authority(body: Node) -> Where:
    return _where(("c_auth", "c_env"), _ref("authority_fail"), body)


def _build(
    definition_id: str,
    letter: str,
    title: str,
    quantity: str,
    terms: tuple[Term, ...],
    conditions: tuple[Condition, ...],
    slots: tuple[ChoiceSlot | NumberSlot, ...],
    inadmissible: tuple[Inadmissible, ...],
    site_series: Node,
) -> MwDefinition:
    return MwDefinition.model_validate(
        {
            "definition_id": definition_id,
            "letter": letter,
            "title": title,
            "quantity": quantity,
            "terms": tuple(sorted((*_SHARED_TERMS, *terms), key=lambda t: t.term_id)),
            "conditions": tuple(
                sorted((*SHARED_CONDITIONS, *conditions), key=lambda c: c.condition_id)
            ),
            "slots": tuple(sorted((*SHARED_SLOTS, *slots), key=lambda s: s.slot_id)),
            "inadmissible": inadmissible,
            "root": _coincidence(site_series),
        }
    )


# --------------------------------------------------------------------------------------------
# A: site load under management
# --------------------------------------------------------------------------------------------

_IC1_VIA: Final = {
    name: FrozenContractField(
        kind="frozen_contract_field",
        contract_id="IC-1",
        schema_stem="ic1_connection_agreement",
        version="1.1.0",
        pointers=(_ptr(f"/{name}", "number"),),
        unit_suffix="_mw",
        derivation=f"the IC-1 field {name}, in MW as the agreement states it",
    )
    for name in ("firm_import_mw", "non_firm_import_mw")
}

_A_TERMS: Final = (
    _term(
        "a.asset_output_kw",
        "AC output of each on-site generator and battery (battery discharge positive)",
        Unit.KW,
        Grain.ASSET,
        TimeBase.SERIES,
        "each asset's AC meter (battery: PCS AC terminals)",
        "metered",
    ),
    _term(
        "a.boundary_export_kw",
        "Export at the site boundary",
        Unit.KW,
        Grain.SITE,
        TimeBase.SERIES,
        "site boundary settlement meter, export register",
        "metered",
    ),
    _term(
        "a.boundary_import_kw",
        "Import at the site boundary",
        Unit.KW,
        Grain.SITE,
        TimeBase.SERIES,
        "site boundary settlement meter, import register",
        "metered",
    ),
    _term(
        "a.firm_import_mw",
        "Firm import capacity in the connection agreement",
        Unit.MW,
        Grain.SITE,
        TimeBase.STATIC,
        "point of common coupling, as the connection agreement (IC-1) states",
        "contracted_document",
    ),
    _term(
        "a.non_firm_import_mw",
        "Non-firm import capacity in the connection agreement",
        Unit.MW,
        Grain.SITE,
        TimeBase.STATIC,
        "point of common coupling, as the connection agreement (IC-1) states",
        "contracted_document",
    ),
)

_A_CONDITIONS: Final = (
    Condition(
        condition_id="c_q_a_boundary",
        label="The boundary import reading is of good quality",
        scope="per_interval",
        predicate=QualityGood(kind="quality_good", term_ids=("a.boundary_import_kw",)),
    ),
    Condition(
        condition_id="c_q_a_gross",
        label="The boundary and every asset reading are of good quality",
        scope="per_interval",
        predicate=QualityGood(
            kind="quality_good",
            term_ids=("a.asset_output_kw", "a.boundary_export_kw", "a.boundary_import_kw"),
        ),
    ),
)

_A_SLOTS: Final = (
    _choice(
        "a.basis",
        "What 'site load' is",
        _opt(
            "agreed_capacity",
            "the import capacity the connection agreement grants (firm + non-firm)",
        ),
        _opt("boundary_import", "metered import at the site boundary"),
        _opt(
            "gross_load", "boundary import, less export, plus on-site generation and battery output"
        ),
    ),
)

_A_SERIES: Final = _authority(
    _choose(
        "a.basis",
        (
            "agreed_capacity",
            Scale(
                node="scale",
                factor=_k(1000, Unit.KW_PER_MW),
                body=Add(node="add", args=(_t("a.firm_import_mw"), _t("a.non_firm_import_mw"))),
            ),
        ),
        (
            "boundary_import",
            _where(("c_q_a_boundary",), _ref("quality_fail"), _t("a.boundary_import_kw")),
        ),
        (
            "gross_load",
            _where(
                ("c_q_a_gross",),
                _ref("quality_fail"),
                Add(
                    node="add",
                    args=(
                        Sub(
                            node="sub",
                            lhs=_t("a.boundary_import_kw"),
                            rhs=_t("a.boundary_export_kw"),
                        ),
                        Sum(node="sum", over="assets", body=_t("a.asset_output_kw")),
                    ),
                ),
            ),
        ),
    )
)

DEFINITION_A: Final = _build(
    "site_load_under_management",
    "A",
    "Site load under management",
    "The electrical load of the candidate sites whose dispatchable assets Neura controls",
    _A_TERMS,
    _A_CONDITIONS,
    _A_SLOTS,
    (),
    _A_SERIES,
)

# --------------------------------------------------------------------------------------------
# B: BESS MW actively dispatched
# --------------------------------------------------------------------------------------------

_B_TERMS: Final = (
    _term(
        "b.asset_enveloped",
        "The battery is the one the site's IC-3 dispatch envelope describes",
        Unit.FLAG,
        Grain.ASSET,
        TimeBase.STATIC,
        "meter registry entry linking the battery's meter to the site's envelope",
        "registered_document",
    ),
    _term(
        "b.committed_discharge_kw",
        "Discharge power committed to services and merchant trading in the interval",
        Unit.KW,
        Grain.SITE,
        TimeBase.SERIES,
        "battery PCS AC terminals, as the IC-3 resolved timeline commits them",
        "contracted_document",
    ),
    _term(
        "b.instructed_kw",
        "The dispatch setpoint Neura sent to the site's battery in the interval",
        Unit.KW,
        Grain.SITE,
        TimeBase.SERIES,
        "dispatch instruction log at the site edge controller",
        "operational_telemetry",
    ),
    _term(
        "b.pcs_ac_kw",
        "Measured battery power (discharge positive, charge negative)",
        Unit.KW,
        Grain.ASSET,
        TimeBase.SERIES,
        "battery PCS AC terminals",
        "metered",
    ),
    _term(
        "b.rated_discharge_kw",
        "Rated discharge power of the site's battery",
        Unit.KW,
        Grain.SITE,
        TimeBase.STATIC,
        "battery PCS AC terminals, as the IC-3 dispatch envelope rates it",
        "contracted_document",
    ),
)

_B_CONDITIONS: Final = (
    Condition(
        condition_id="c_enveloped",
        label="The battery is the enveloped demonstrator battery",
        scope="per_asset",
        predicate=FlagIs(kind="flag", term_id="b.asset_enveloped"),
    ),
    Condition(
        condition_id="c_instr",
        label="Neura instructed the battery in the interval (a non-zero setpoint)",
        scope="per_interval",
        predicate=Compare(kind="compare", lhs=_t("b.instructed_kw"), op="ne", rhs=_k(0, Unit.KW)),
    ),
    Condition(
        condition_id="c_q_b_pcs",
        label="Every battery reading is of good quality",
        scope="per_interval",
        predicate=QualityGood(kind="quality_good", term_ids=("b.pcs_ac_kw",)),
    ),
)

_B_SLOTS: Final = (
    _choice(
        "b.basis",
        "What 'BESS MW dispatched' is",
        _opt("committed", "discharge committed in the IC-3 resolved timeline"),
        _opt("measured", "measured battery power at the PCS AC terminals"),
        _opt("rated", "rated discharge power in the IC-3 envelope"),
    ),
    _choice(
        "b.direction",
        "Which direction of measured power counts",
        _opt("discharge_only", "discharge only (charge counts as zero)"),
        _opt("net", "net (charge counts as negative)"),
    ),
    _choice(
        "b.idle",
        "What an interval with no dispatch instruction does",
        _opt("contributes_zero", "the interval counts, at zero", _on_fail("contributes_zero")),
        _opt("exclude_interval", "the interval is left out", _on_fail("exclude_interval")),
    ),
    _choice(
        "b.scope",
        "Which batteries at a site count",
        _opt("all_site_batteries", "every battery at the site"),
        _opt("enveloped_batteries_only", "only the battery the site's IC-3 envelope describes"),
    ),
)

_B_MEASURED: Final = _choose(
    "b.direction",
    ("discharge_only", ClipLow(node="clip_low", floor=_k(0, Unit.KW), body=_t("b.pcs_ac_kw"))),
    ("net", _t("b.pcs_ac_kw")),
)

_B_SERIES: Final = _where(
    ("c_instr",),
    _ref("b.idle"),
    _authority(
        _choose(
            "b.basis",
            ("committed", _t("b.committed_discharge_kw")),
            (
                "measured",
                _where(
                    ("c_q_b_pcs",),
                    _ref("quality_fail"),
                    _choose(
                        "b.scope",
                        ("all_site_batteries", Sum(node="sum", over="assets", body=_B_MEASURED)),
                        (
                            "enveloped_batteries_only",
                            Sum(
                                node="sum",
                                over="assets",
                                body=_where(
                                    ("c_enveloped",), _on_fail("site_not_counted"), _B_MEASURED
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            ("rated", _t("b.rated_discharge_kw")),
        )
    ),
)

DEFINITION_B: Final = _build(
    "bess_mw_actively_dispatched",
    "B",
    "BESS MW actively dispatched",
    "Battery discharge power under Neura's dispatch instruction at the candidate sites",
    _B_TERMS,
    _B_CONDITIONS,
    _B_SLOTS,
    (
        Inadmissible(
            when=(("b.basis", "committed"), ("resolution", "s60")),
            reason=(
                "the IC-3 resolved timeline commits power in 30-minute steps; a 1-minute statistic "
                "of it would restate each half-hour value thirty times"
            ),
        ),
    ),
    _B_SERIES,
)

# --------------------------------------------------------------------------------------------
# C: verified flexible MW
# --------------------------------------------------------------------------------------------

_C_TERMS: Final = (
    _term(
        "c.asset_baseline_kw",
        "Each flexible asset's pre-registered counterfactual demand",
        Unit.KW,
        Grain.ASSET,
        TimeBase.SERIES,
        "each flexible asset's sub-meter, counterfactual registered before the demonstrator",
        "modelled",
    ),
    _term(
        "c.asset_evidence_metered",
        "The asset's IC-11 availability surface is of evidence class 'metered'",
        Unit.FLAG,
        Grain.ASSET,
        TimeBase.STATIC,
        "IC-11 thermal flex availability surface (evidence_class)",
        "registered_document",
    ),
    _term(
        "c.asset_kw",
        "Each flexible asset's measured demand",
        Unit.KW,
        Grain.ASSET,
        TimeBase.SERIES,
        "each flexible asset's sub-meter",
        "metered",
    ),
    _term(
        "c.baseline_kw",
        "The site's pre-registered baseline net import",
        Unit.KW,
        Grain.SITE,
        TimeBase.SERIES,
        "site boundary settlement meter, baseline registered before the demonstrator",
        "modelled",
    ),
    _term(
        "c.baseline_preregistered",
        "The site's baseline was registered before the first demonstrator interval",
        Unit.FLAG,
        Grain.SITE,
        TimeBase.STATIC,
        "baseline registration record",
        "registered_document",
    ),
    _term(
        "c.compliant",
        "No thermal-limit or resilience-floor breach in the interval",
        Unit.FLAG,
        Grain.SITE,
        TimeBase.SERIES,
        "reconciliation report, thermal compliance and resilience-floor breaches",
        "metered",
    ),
    _term(
        "c.event_held_min",
        "Minutes the delivery event containing the interval has been held",
        Unit.MIN,
        Grain.SITE,
        TimeBase.SERIES,
        "reconciliation report, delivery events",
        "metered",
    ),
    _term(
        "c.net_import_kw",
        "The site's measured net import (import less export)",
        Unit.KW,
        Grain.SITE,
        TimeBase.SERIES,
        "site boundary settlement meter, import less export",
        "metered",
    ),
    _term(
        "c.verifier_recomputed",
        "The independent verifier's recompute from the dataset manifest reproduces the site's "
        "figure",
        Unit.FLAG,
        Grain.SITE,
        TimeBase.STATIC,
        "independent verifier's recompute record",
        "verifier_recompute",
    ),
)

_C_CONDITIONS: Final = (
    Condition(
        condition_id="c_base",
        label="The baseline was pre-registered",
        scope="per_window",
        predicate=FlagIs(kind="flag", term_id="c.baseline_preregistered"),
    ),
    Condition(
        condition_id="c_compliant",
        label="No thermal or resilience-floor breach in the interval",
        scope="per_interval",
        predicate=FlagIs(kind="flag", term_id="c.compliant"),
    ),
    Condition(
        condition_id="c_metered",
        label="The asset's flexibility is metered evidence",
        scope="per_asset",
        predicate=FlagIs(kind="flag", term_id="c.asset_evidence_metered"),
    ),
    Condition(
        condition_id="c_q_c_asset",
        label="Every flexible asset's reading is of good quality",
        scope="per_interval",
        predicate=QualityGood(kind="quality_good", term_ids=("c.asset_kw",)),
    ),
    Condition(
        condition_id="c_q_c_boundary",
        label="The boundary net import reading is of good quality",
        scope="per_interval",
        predicate=QualityGood(kind="quality_good", term_ids=("c.net_import_kw",)),
    ),
    Condition(
        condition_id="c_sustained",
        label="The interval is inside a delivery event held at least the minutes the agreement "
        "states",
        scope="per_interval",
        predicate=Compare(
            kind="compare",
            lhs=_t("c.event_held_min"),
            op="ge",
            rhs=Const(node="const", value=_ref("c.sustain_min"), unit=Unit.MIN),
        ),
    ),
    Condition(
        condition_id="c_verif",
        label="The independent verifier reproduced the site's figure",
        scope="per_window",
        predicate=FlagIs(kind="flag", term_id="c.verifier_recomputed"),
    ),
)

_C_SLOTS: Final = (
    _choice(
        "c.basis",
        "Which intervals of delivery count",
        _opt("all_intervals", "every qualifying interval"),
        _opt(
            "sustained_events_only",
            "only intervals inside an event held the minutes the agreement states",
        ),
    ),
    _choice(
        "c.compliance",
        "Whether an interval with a thermal or resilience-floor breach counts",
        _opt("none", "breaches do not change what counts"),
        _opt("require_compliance", "an interval with a breach is left out"),
    ),
    _choice(
        "c.point",
        "Where the reduction is measured",
        _opt("asset_sum", "sum over flexible assets with metered evidence"),
        _opt("boundary", "at the site boundary"),
    ),
    _choice(
        "c.shortfall",
        "How an interval with demand above the baseline counts",
        _opt("as_negative", "as a negative reduction"),
        _opt("clip_at_zero", "as zero"),
    ),
    _number("c.sustain_min", "Minimum minutes an event is held to count", Unit.MIN),
)

_C_REDUCTION: Final = _choose(
    "c.point",
    (
        "asset_sum",
        _where(
            ("c_q_c_asset",),
            _ref("quality_fail"),
            Sum(
                node="sum",
                over="assets",
                body=_where(
                    ("c_metered",),
                    _on_fail("site_not_counted"),
                    Sub(node="sub", lhs=_t("c.asset_baseline_kw"), rhs=_t("c.asset_kw")),
                ),
            ),
        ),
    ),
    (
        "boundary",
        _where(
            ("c_q_c_boundary",),
            _ref("quality_fail"),
            Sub(node="sub", lhs=_t("c.baseline_kw"), rhs=_t("c.net_import_kw")),
        ),
    ),
)
_C_SHORTFALL: Final = _choose(
    "c.shortfall",
    ("as_negative", _C_REDUCTION),
    ("clip_at_zero", ClipLow(node="clip_low", floor=_k(0, Unit.KW), body=_C_REDUCTION)),
)
_C_BASIS: Final = _choose(
    "c.basis",
    ("all_intervals", _C_SHORTFALL),
    (
        "sustained_events_only",
        _where(("c_sustained",), _on_fail("exclude_interval"), _C_SHORTFALL),
    ),
)
_C_SERIES: Final = _where(
    ("c_base", "c_verif"),
    _on_fail("site_not_counted"),
    _authority(
        _choose(
            "c.compliance",
            ("none", _C_BASIS),
            (
                "require_compliance",
                _where(("c_compliant",), _on_fail("exclude_interval"), _C_BASIS),
            ),
        )
    ),
)

DEFINITION_C: Final = _build(
    "verified_flexible_mw",
    "C",
    "Verified flexible MW",
    "Reduction in demand against a pre-registered baseline at the candidate sites, reproduced "
    "by the independent verifier",
    _C_TERMS,
    _C_CONDITIONS,
    _C_SLOTS,
    (),
    _C_SERIES,
)

#: The candidates in fixed letter order. The order is the spec's, not a preference.
DEFINITIONS: Final = (DEFINITION_A, DEFINITION_B, DEFINITION_C)


def definition(definition_id: str) -> MwDefinition:
    for d in DEFINITIONS:
        if d.definition_id == definition_id:
            return d
    raise KeyError(definition_id)


# --------------------------------------------------------------------------------------------
# Bindings: exactly one per term
# --------------------------------------------------------------------------------------------

_RECONCILIATION: Final = {
    "c.compliant": "per interval, whether a thermal-limit or resilience-floor breach occurred",
    "c.event_held_min": "per interval, the minutes the delivery event containing it has been held",
}


_NO_LEDGER_CONTRACT: Final = Unknown(
    kind="unknown",
    reason="the roadmap names no contract for the reconciliation ledger's fields; the product "
    "is the data source",
)


def _ledger(needs: str, *wps: str, product: str = "reconciliation_report") -> NotYetMeasurable:
    return NotYetMeasurable(
        kind="not_yet_measurable",
        category="ledger_field",
        contract_id=_NO_LEDGER_CONTRACT,
        data_product=product,
        expected_schema_prefix=_NO_LEDGER_CONTRACT,
        roadmap_wps=tuple(sorted(set(wps))),
        needs=needs,
    )


def _table(d: MwDefinition, own: tuple[TermBinding, ...]) -> BindingTable:
    return BindingTable(
        definition_id=d.definition_id,
        formula_hash=d.content_hash(),
        bindings=tuple(sorted((*_SHARED_BINDINGS, *own), key=lambda b: b.term_id)),
    )


BINDINGS: Final[dict[str, BindingTable]] = {
    DEFINITION_A.definition_id: _table(
        DEFINITION_A,
        (
            _ic14(
                "a.asset_output_kw",
                (_AK.GENSET, _AK.PCS, _AK.PV),
                (_MR.OPERATIONAL_METER, _MR.REVENUE_METER),
                sign="negated",
                split=None,
                rounding="floor",
                how="negated (load convention: output is negative; a battery at its PCS AC "
                "terminals)",
            ),
            _ic14(
                "a.boundary_export_kw",
                _BOUNDARY,
                _REVENUE,
                sign="as_measured",
                split="export",
                rounding="ceil",
                how="the export half of split_import_export_w",
            ),
            _ic14(
                "a.boundary_import_kw",
                _BOUNDARY,
                _REVENUE,
                sign="as_measured",
                split="import",
                rounding="floor",
                how="the import half of split_import_export_w",
            ),
            *(
                TermBinding(
                    term_id=f"a.{name}",
                    binding=DossierFieldBinding(
                        kind="dossier_field",
                        field=DossierField.CONNECTION_TERMS,
                        value_path=f"/agreements/*/agreement/{name}",
                        via=_IC1_VIA[name],
                        derivation=(
                            f"the {name} of the site's one embedded IC-1 agreement; with more "
                            "than one agreement the term is unknown until the owner says which "
                            "governs"
                        ),
                    ),
                )
                for name in ("firm_import_mw", "non_firm_import_mw")
            ),
        ),
    ),
    DEFINITION_B.definition_id: _table(
        DEFINITION_B,
        (
            TermBinding(
                term_id="b.asset_enveloped",
                binding=NotYetMeasurable(
                    kind="not_yet_measurable",
                    category="telemetry_field",
                    contract_id="NEW-IC-14",
                    data_product="meter_standard_spec",
                    expected_schema_prefix="ic14_",
                    roadmap_wps=("WP-SIZE-08", "WP-TEL-01", "WP-TEL-04"),
                    needs="a meter-registry entry naming the battery whose meter serves the "
                    "site's IC-3 envelope (IC-14 1.0.0 names a series' meter, not the envelope "
                    "it serves)",
                ),
            ),
            TermBinding(
                term_id="b.committed_discharge_kw",
                binding=FrozenContractField(
                    kind="frozen_contract_field",
                    contract_id="IC-3",
                    schema_stem="ic3_resolved_timeline",
                    version="1.0.0",
                    pointers=(
                        _ptr("/steps/*/terms_hash", "string"),
                        _ptr("/terms/*/terms/discharge/merchant_kw", "integer"),
                        _ptr("/terms/*/terms/discharge/services/*/kw", "integer"),
                    ),
                    unit_suffix="_kw",
                    derivation=(
                        "for the step containing the interval, the sum of discharge.services[].kw "
                        "and discharge.merchant_kw of the terms the step's terms_hash names"
                    ),
                ),
            ),
            _ic14(
                "b.instructed_kw",
                (_AK.PCS,),
                (_MR.SETPOINT_ECHO,),
                sign="as_measured",
                split=None,
                rounding="away_from_zero",
                how="summed over the site's PCS, used as echoed (the formula only tests it "
                "against zero)",
            ),
            _ic14(
                "b.pcs_ac_kw",
                (_AK.PCS,),
                (_MR.DEVICE_REPORTED, _MR.OPERATIONAL_METER),
                sign="negated",
                split=None,
                rounding="floor",
                how="negated (load convention: discharge is negative)",
            ),
            TermBinding(
                term_id="b.rated_discharge_kw",
                binding=FrozenContractField(
                    kind="frozen_contract_field",
                    contract_id="IC-3",
                    schema_stem="ic3_dispatch_envelope",
                    version="1.0.0",
                    pointers=(_ptr("/rated_discharge_kw", "integer"),),
                    unit_suffix="_kw",
                    derivation="the envelope's rated_discharge_kw (integer kW)",
                ),
            ),
        ),
    ),
    DEFINITION_C.definition_id: _table(
        DEFINITION_C,
        (
            TermBinding(
                term_id="c.asset_baseline_kw",
                binding=_ledger(
                    "a counterfactual demand series per flexible asset (the cooling "
                    "counterfactual for chillers), registered before the demonstrator",
                    "WP-LEDGER-04",
                    "WP-THERM-03",
                    product="preregistered_baselines",
                ),
            ),
            TermBinding(
                term_id="c.asset_evidence_metered",
                binding=FrozenContractField(
                    kind="frozen_contract_field",
                    contract_id="IC-11",
                    schema_stem="ic11_thermal_flex_availability",
                    version="1.1.0",
                    pointers=(_ptr("/evidence_class", "string"), _ptr("/site_id", "string")),
                    unit_suffix=_NO_SUFFIX,
                    derivation="true when the asset's IC-11 surface has evidence_class 'metered'",
                ),
            ),
            _ic14(
                "c.asset_kw",
                (_AK.CHILLER, _AK.CRAH, _AK.IT_LOAD, _AK.UPS),
                (_MR.OPERATIONAL_METER,),
                sign="as_measured",
                split=None,
                rounding="ceil",
                how="used as measured (load convention: demand is positive)",
            ),
            TermBinding(
                term_id="c.baseline_kw",
                binding=_ledger(
                    "a baseline net-import series per site, registered before the demonstrator",
                    "WP-LEDGER-04",
                    product="preregistered_baselines",
                ),
            ),
            TermBinding(
                term_id="c.baseline_preregistered",
                binding=_ledger(
                    "the registration record of the site's baseline, dated before the first "
                    "demonstrator interval",
                    "WP-LEDGER-04",
                    product="preregistered_baselines",
                ),
            ),
            TermBinding(
                term_id="c.compliant",
                binding=_ledger(_RECONCILIATION["c.compliant"], "WP-LEDGER-05"),
            ),
            TermBinding(
                term_id="c.event_held_min",
                binding=_ledger(_RECONCILIATION["c.event_held_min"], "WP-LEDGER-05"),
            ),
            _ic14(
                "c.net_import_kw",
                _BOUNDARY,
                _REVENUE,
                sign="as_measured",
                split=None,
                rounding="ceil",
                how="used as measured (load convention: net import is positive)",
            ),
            TermBinding(
                term_id="c.verifier_recomputed",
                binding=NotYetMeasurable(
                    kind="not_yet_measurable",
                    category="verifier_method",
                    contract_id="IC-8",
                    data_product="verifier_dataset",
                    expected_schema_prefix="ic8_",
                    roadmap_wps=("WP-LEDGER-07", "WP-LEDGER-08"),
                    needs="the verifier's recompute-by-hash record for the site's figure, from the "
                    "dataset manifest",
                ),
            ),
        ),
    ),
}
