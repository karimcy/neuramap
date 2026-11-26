"""WP-OPS-11 criterion 1: each candidate definition is a formula a verifier can recompute.

D143 (typed expression trees, exact evaluation) and D147 (open parameters are
slots with every option listed, never defaults). The template checks refuse a malformed formula;
`resolve` refuses an incomplete or inadmissible filling; `evaluate` is exact, treats a gap as a
gap (never zero) and lists every missing term. Every filling and series is a TEST FIXTURE.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError
from tests.ops._mw_support import (
    BASE,
    IVS,
    SYNTHETIC_NA,
    env,
    fill,
    flags,
    series,
    site,
)

from gridq.contracts._base import NotApplicable, Unknown
from gridq.ops.mw_definitions import (
    DEFINITION_A,
    DEFINITION_B,
    DEFINITION_C,
    DEFINITIONS,
    SHARED_CONDITIONS,
    SHARED_SLOTS,
)
from gridq.ops.mw_formula import (
    Add,
    Branch,
    ChoiceSlot,
    Choose,
    Const,
    DayTypes,
    Env,
    Filling,
    FormulaError,
    GapUnknownIfAny,
    Grain,
    MinCoverage,
    MwDefinition,
    NumberSlot,
    Option,
    PercentileStat,
    Q,
    Resolution,
    SiteEnv,
    Stat,
    Sum,
    Term,
    TermRef,
    TimeAgg,
    TimeBase,
    Unit,
    Window,
    evaluate,
    render_kpi_formula,
    render_text,
    resolve,
    utc_half_hours,
    variant_hash,
)

# =============================================================================================
# A minimal hand-built template, to break one rule at a time
# =============================================================================================


def _term(term_id: str, unit: Unit, grain: Grain, time: TimeBase) -> Term:
    return Term(
        term_id=term_id,
        label=f"TEST FIXTURE {term_id}",
        unit=unit,
        grain=grain,
        time_base=time,
        measurement_point="TEST FIXTURE meter",
        evidence_class="metered",
    )


X = _term("x", Unit.KW, Grain.SITE, TimeBase.SERIES)
Y = _term("y", Unit.MW, Grain.SITE, TimeBase.STATIC)
ASSET = _term("z_asset", Unit.KW, Grain.ASSET, TimeBase.SERIES)


def _window(
    first: date = date(2028, 11, 1),
    last: date = date(2028, 11, 1),
    days: str = "all",
    start: int = 0,
    end: int = 1440,
) -> Window:
    return Window(
        kind="window",
        first_day=first,
        last_day=last,
        period_citation="TEST FIXTURE window",
        day_types=DayTypes(kind="day_types", value=days),  # type: ignore[arg-type]
        start_minute=Q.of(start),
        end_minute=Q.of(end),
    )


def _agg(
    body: Any, stat: Any = None, gaps: Any = None, window: Window | None = None, seconds: int = 1800
) -> TimeAgg:
    return TimeAgg(
        node="time_agg",
        window=window or _window(),
        resolution=Resolution(kind="resolution", seconds=seconds),  # type: ignore[arg-type]
        statistic=stat or Stat(kind="stat", name="mean"),
        gap_policy=gaps or GapUnknownIfAny(kind="gap_unknown_if_any"),
        body=body,
    )


def _x() -> TermRef:
    return TermRef(node="term", term_id="x")


def _mini(root: Any, terms: tuple[Term, ...] = (X,), slots: tuple[Any, ...] = ()) -> MwDefinition:
    return MwDefinition(
        definition_id="site_load_under_management",
        letter="A",
        title="TEST FIXTURE",
        quantity="TEST FIXTURE quantity",
        terms=terms,
        conditions=(),
        slots=slots,
        inadmissible=(),
        root=root,
    )


def _portfolio_mean(**kw: Any) -> MwDefinition:
    return _mini(Sum(node="sum", over="sites", body=_agg(_x(), **kw)))


def test_a_well_formed_minimal_template_validates() -> None:
    d = _portfolio_mean()
    assert render_text(d).startswith("site_load_under_management [kW] = sum_sites(time_agg[")


@pytest.mark.parametrize(
    ("root", "terms", "message"),
    [
        # The root must be a static portfolio kW value.
        (lambda: _agg(_x()), (X,), "the root is a static portfolio kW value"),
        # Unlike units do not add.
        (
            lambda: Sum(
                node="sum",
                over="sites",
                body=_agg(Add(node="add", args=(_x(), TermRef(node="term", term_id="y")))),
            ),
            (X, Y),
            "of unlike units",
        ),
        # A sum over sites needs a site-grain body.
        (
            lambda: Sum(
                node="sum", over="sites", body=_agg(TermRef(node="term", term_id="z_asset"))
            ),
            (ASSET,),
            "needs a site body",
        ),
        # A time aggregate needs a series.
        (
            lambda: Sum(node="sum", over="sites", body=_agg(TermRef(node="term", term_id="y"))),
            (Y,),
            "needs a series body",
        ),
        # A used term must be declared.
        (lambda: Sum(node="sum", over="sites", body=_agg(_x())), (Y,), "no term x"),
        # A declared term must be used.
        (lambda: Sum(node="sum", over="sites", body=_agg(_x())), (X, Y), "unused"),
    ],
)
def test_the_template_checks_refuse_a_malformed_formula(
    root: Any, terms: Any, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _mini(root(), terms)


def test_declared_and_referenced_slots_must_match() -> None:
    spare = NumberSlot(kind="number_slot", slot_id="spare", label="TEST FIXTURE", unit=Unit.RATIO)
    with pytest.raises(ValidationError, match="slots declared and slots referenced differ"):
        _mini(Sum(node="sum", over="sites", body=_agg(_x())), slots=(spare,))


def test_choice_options_are_in_canonical_order_and_there_are_at_least_two() -> None:
    na = NotApplicable(kind="not_applicable", reason="TEST FIXTURE")
    a = Option(option_id="a", label="a", payload=na)
    b = Option(option_id="b", label="b", payload=na)
    with pytest.raises(ValidationError, match="canonical order"):
        ChoiceSlot(kind="choice_slot", slot_id="s", label="s", options=(b, a))
    with pytest.raises(ValidationError):
        ChoiceSlot(kind="choice_slot", slot_id="s", label="s", options=(a,))


def test_a_choice_has_one_branch_per_option_in_order_and_alike_branches() -> None:
    na = NotApplicable(kind="not_applicable", reason="TEST FIXTURE")
    slot = ChoiceSlot(
        kind="choice_slot",
        slot_id="s",
        label="s",
        options=(
            Option(option_id="a", label="a", payload=na),
            Option(option_id="b", label="b", payload=na),
        ),
    )
    good = Sum(node="sum", over="sites", body=_agg(_x()))
    swapped = Choose(
        node="choose",
        slot_id="s",
        branches=(Branch(option_id="b", body=good), Branch(option_id="a", body=good)),
    )
    with pytest.raises(ValidationError, match="one branch per option"):
        _mini(swapped, slots=(slot,))
    unlike = Choose(
        node="choose",
        slot_id="s",
        branches=(
            Branch(option_id="a", body=good),
            Branch(option_id="b", body=Const(node="const", value=Q.of(1), unit=Unit.MW)),
        ),
    )
    with pytest.raises(ValidationError, match="branches differ"):
        _mini(unlike, slots=(slot,))


def test_a_rational_is_stored_in_lowest_terms() -> None:
    with pytest.raises(ValidationError):
        Q(kind="q", num=2, den=4)
    assert Q.of(Fraction(2, 4)) == Q(kind="q", num=1, den=2)


# =============================================================================================
# The three candidates: well-formed, shared slots, every key a verifier needs
# =============================================================================================


def test_the_three_candidates_are_a_b_c_in_spec_order() -> None:
    assert [d.letter for d in DEFINITIONS] == ["A", "B", "C"]
    assert [d.definition_id for d in DEFINITIONS] == [
        "site_load_under_management",
        "bess_mw_actively_dispatched",
        "verified_flexible_mw",
    ]


@pytest.mark.parametrize("d", DEFINITIONS, ids=lambda d: d.letter)
def test_every_candidate_shares_the_common_slots_and_conditions_verbatim(d: MwDefinition) -> None:
    for s in SHARED_SLOTS:
        assert d.slot(s.slot_id) == s
    for c in SHARED_CONDITIONS:
        assert d.condition(c.condition_id) == c


@pytest.mark.parametrize("d", DEFINITIONS, ids=lambda d: d.letter)
def test_every_candidate_states_quantity_point_period_and_conditions(d: MwDefinition) -> None:
    kpi = render_kpi_formula(d)
    assert list(kpi) == [
        "quantity",
        "measurement_point",
        "averaging_period",
        "qualifying_conditions",
    ]
    assert all(v.strip() for v in kpi.values())
    for t in d.terms:
        assert t.measurement_point in kpi["measurement_point"]
    for c in d.conditions:
        assert c.condition_id in kpi["qualifying_conditions"]
    assert "<window>" in kpi["averaging_period"] and "<statistic>" in kpi["averaging_period"]


@pytest.mark.parametrize("d", DEFINITIONS, ids=lambda d: d.letter)
def test_no_candidate_carries_a_default_or_a_preference_field(d: MwDefinition) -> None:
    fields = (
        set(MwDefinition.model_fields) | set(ChoiceSlot.model_fields) | set(Option.model_fields)
    )
    for banned in ("default", "preferred", "recommended", "score", "rank", "status"):
        assert not any(banned in f for f in fields)
    assert all(isinstance(s, ChoiceSlot | NumberSlot) for s in d.slots)


# =============================================================================================
# Resolution: complete fillings only; lazy on the chosen path; the variant hash
# =============================================================================================


def _a(**over: str) -> dict[str, str]:
    return {**BASE, "a.basis": "boundary_import", **over}


def test_resolve_refuses_an_unfilled_slot_on_the_chosen_path() -> None:
    choices = _a()
    del choices["statistic"]
    with pytest.raises(FormulaError, match="slot statistic is not filled"):
        resolve(DEFINITION_A, fill(choices))


def test_resolve_refuses_an_unknown_option_and_the_wrong_kind_of_fill() -> None:
    with pytest.raises(FormulaError, match="is not an option"):
        resolve(DEFINITION_A, fill(_a(statistic="median")))
    with pytest.raises(FormulaError, match="wrong kind"):
        resolve(
            DEFINITION_A,
            fill({k: v for k, v in _a().items() if k != "statistic"}, {"statistic": 1}),
        )
    with pytest.raises(FormulaError, match="wrong kind"):
        resolve(DEFINITION_A, fill({**_a(), "statistic.p": "max"}))


def test_resolve_refuses_an_inadmissible_combination() -> None:
    choices = {
        **BASE,
        "b.basis": "committed",
        "b.direction": "net",
        "b.idle": "exclude_interval",
        "b.scope": "all_site_batteries",
        "resolution": "s60",
    }
    with pytest.raises(FormulaError, match="inadmissible"):
        resolve(DEFINITION_B, fill(choices))
    assert resolve(DEFINITION_B, fill({**choices, "resolution": "s1800"})).slots == ()


def test_resolution_is_lazy_but_a_parameter_on_the_chosen_path_must_be_stated() -> None:
    # mean needs no percentile rank; percentile does.
    assert resolve(DEFINITION_A, fill(_a())).slots == ()
    with pytest.raises(FormulaError, match="parameter not agreed: statistic.p"):
        resolve(DEFINITION_A, fill(_a(statistic="percentile")))
    v = resolve(DEFINITION_A, fill(_a(statistic="percentile"), {"statistic.p": Fraction(9, 10)}))
    assert "p=9/10" in render_text(v)


def test_the_variant_hash_names_one_filling_and_leaves_the_template_alone() -> None:
    before = DEFINITION_A.content_hash()
    mean = variant_hash(DEFINITION_A, fill(_a()))
    assert mean == variant_hash(DEFINITION_A, fill(_a()))
    assert mean != variant_hash(DEFINITION_A, fill(_a(statistic="max")))
    assert mean != before
    assert DEFINITION_A.content_hash() == before
    v = resolve(DEFINITION_A, fill(_a()))
    assert "<" not in render_text(v)
    assert {t.term_id for t in v.terms} == {
        "a.boundary_import_kw",
        "control_authority",
        "control_signoff",
        "envelope_in_force",
    }
    assert [c.condition_id for c in v.conditions] == ["c_auth", "c_env", "c_q_a_boundary"]


def test_an_agreed_filling_and_only_one_cites_its_method_agreement() -> None:
    with pytest.raises(ValidationError, match="cites its method agreement"):
        Filling(record_class="agreed", agreement=SYNTHETIC_NA, choices=(), numbers=())
    with pytest.raises(ValidationError, match="filled once"):
        Filling(
            record_class="synthetic_fixture",
            agreement=SYNTHETIC_NA,
            choices=(("statistic", "mean"), ("statistic", "max")),
            numbers=(),
        )


def test_a_template_is_never_evaluated() -> None:
    with pytest.raises(FormulaError, match="resolved variant"):
        evaluate(DEFINITION_A, env(site("S1")))


# =============================================================================================
# Exact evaluation
# =============================================================================================


def _one_site(values: tuple[Any, ...]) -> Env:
    return env(SiteEnv(site_id="S1", terms={"x": series(*values)}))


@pytest.mark.parametrize(
    ("stat", "expected"),
    [
        (Stat(kind="stat", name="mean"), Fraction(5, 2)),
        (Stat(kind="stat", name="max"), Fraction(4)),
        (Stat(kind="stat", name="min"), Fraction(1)),
        (PercentileStat(kind="percentile", p=Q.of(Fraction(1, 2))), Fraction(2)),
        (PercentileStat(kind="percentile", p=Q.of(Fraction(9, 10))), Fraction(4)),
        (PercentileStat(kind="percentile", p=Q.of(0)), Fraction(1)),
    ],
)
def test_each_statistic_is_exact(stat: Any, expected: Fraction) -> None:
    out = evaluate(_portfolio_mean(stat=stat), _one_site((3, 1, 4, 2)))
    assert out.result == Q.of(expected)
    assert out.missing == ()


def test_a_gap_is_never_zero() -> None:
    gappy = _one_site((1, None, 3, 4))
    unknown = evaluate(_portfolio_mean(), gappy)
    assert isinstance(unknown.result, Unknown)
    assert [m.term_id for m in unknown.missing] == ["interval_coverage"]
    covered = evaluate(
        _portfolio_mean(gaps=MinCoverage(kind="min_coverage", ratio=Q.of(Fraction(3, 4)))), gappy
    )
    # The mean of the three readings, not of four with a zero in the gap (which would be 2).
    assert covered.result == Q.of(Fraction(8, 3))
    strict = evaluate(_portfolio_mean(gaps=MinCoverage(kind="min_coverage", ratio=Q.of(1))), gappy)
    assert isinstance(strict.result, Unknown)


def test_a_series_at_another_resolution_is_unknown_never_resampled() -> None:
    minutes = utc_half_hours(IVS[0], 4, 60)
    e = Env(
        resolution_s=60,
        intervals=minutes,
        sites=(SiteEnv(site_id="S1", terms={"x": series(1, 2, 3, 4)}),),
    )
    out = evaluate(_portfolio_mean(), e)
    assert isinstance(out.result, Unknown)
    assert "does not resample" in out.missing[0].reason


def test_the_window_is_in_local_time_and_honours_day_types() -> None:
    # BST: 2028-06-30T23:00Z is 00:00 on 1 July locally; 2028-07-01T23:00Z is 2 July.
    summer = utc_half_hours(datetime(2028, 6, 30, 22, 30, tzinfo=UTC), 4)
    e = Env(
        resolution_s=1800,
        intervals=summer,
        sites=(SiteEnv(site_id="S1", terms={"x": series(100, 1, 2, 3)}),),
    )
    july_1 = _window(date(2028, 7, 1), date(2028, 7, 1))
    out = evaluate(_portfolio_mean(window=july_1), e)
    assert out.result == Q.of(2)  # 22:30Z (23:30 on 30 June locally) is outside
    # 2028-11-04 is a Saturday.
    sat = utc_half_hours(datetime(2028, 11, 3, 23, 0, tzinfo=UTC), 4)
    e2 = Env(
        resolution_s=1800,
        intervals=sat,
        sites=(SiteEnv(site_id="S1", terms={"x": series(10, 10, 20, 30)}),),
    )
    weekend = _window(date(2028, 11, 3), date(2028, 11, 4), days="weekends")
    weekdays = _window(date(2028, 11, 3), date(2028, 11, 4), days="weekdays")
    assert evaluate(_portfolio_mean(window=weekend), e2).result == Q.of(25)
    assert evaluate(_portfolio_mean(window=weekdays), e2).result == Q.of(10)
    hours = _window(date(2028, 11, 4), date(2028, 11, 4), start=30, end=60)
    assert evaluate(_portfolio_mean(window=hours), e2).result == Q.of(30)


def test_no_interval_in_the_window_has_no_figure() -> None:
    out = evaluate(
        _portfolio_mean(window=_window(date(2028, 12, 1), date(2028, 12, 1))),
        _one_site((1, 2, 3, 4)),
    )
    assert isinstance(out.result, NotApplicable)


# ---------------------------------------------------------------------------------------------
# The real candidates on synthetic series
# ---------------------------------------------------------------------------------------------

S1_IMPORT = series(100, 200, 300, 400)
S2_IMPORT = series(400, 300, 200, 100)


def _two_sites(**s1: Any) -> Env:
    return env(
        site("S1", **{"a.boundary_import_kw": S1_IMPORT, **s1}),
        site("S2", **{"a.boundary_import_kw": S2_IMPORT}),
    )


def _a_value(choices: dict[str, str], e: Env, numbers: Any = None) -> Any:
    return evaluate(resolve(DEFINITION_A, fill(choices, numbers)), e).result


def test_coincident_and_non_coincident_differ_exactly() -> None:
    e = _two_sites()
    assert _a_value(_a(statistic="max"), e) == Q.of(500)
    assert _a_value(_a(statistic="max", coincidence="non_coincident"), e) == Q.of(800)


def test_agreed_capacity_is_firm_plus_non_firm_in_kw() -> None:
    e = env(
        site("S1", **{"a.firm_import_mw": Fraction(2), "a.non_firm_import_mw": Fraction(1, 2)}),
        site("S2", **{"a.firm_import_mw": Fraction(1), "a.non_firm_import_mw": Fraction(0)}),
    )
    assert _a_value(_a(**{"a.basis": "agreed_capacity"}), e) == Q.of(3500)


def test_gross_load_adds_asset_output_and_a_site_without_assets_sums_zero() -> None:
    from gridq.ops.mw_formula import MemberEnv

    e = env(
        SiteEnv(
            site_id="S1",
            terms={
                **site("S1").terms,
                "a.boundary_import_kw": series(100, 100, 100, 100),
                "a.boundary_export_kw": series(0, 0, 10, 0),
            },
            assets=(
                MemberEnv(member_id="chp", terms={"a.asset_output_kw": series(50, 50, 50, 50)}),
            ),
        ),
        site(
            "S2",
            **{
                "a.boundary_import_kw": series(1, 1, 1, 1),
                "a.boundary_export_kw": series(0, 0, 0, 0),
            },
        ),
    )
    out = _a_value(_a(**{"a.basis": "gross_load"}, statistic="min"), e)
    assert out == Q.of(141)  # (100 - 10 + 50) + (1 - 0 + 0) at the third interval


def test_quality_failure_excludes_or_uncounts_as_the_slot_says() -> None:
    e = _two_sites(**{"a.boundary_import_kw": series(100, (200, "suspect"), 300, 400)})
    # exclude_interval: interval 2 leaves S1 only; coincident sum skips S1 there.
    assert _a_value(_a(statistic="max"), e) == Q.of(500)
    assert _a_value(_a(statistic="min"), e) == Q.of(300)  # (S2 alone at interval 2 is 300)
    # site_not_counted: S1 is left out entirely.
    assert _a_value(_a(statistic="max", quality_fail="site_not_counted"), e) == Q.of(400)


def test_authority_failure_excludes_or_counts_zero_as_the_slot_says() -> None:
    e = env(
        site(
            "S1",
            **{
                "a.boundary_import_kw": S1_IMPORT,
                "control_authority": flags(True, True, True, False),
            },
        ),
    )
    assert _a_value(_a(statistic="mean"), e) == Q.of(200)  # (100+200+300)/3
    assert _a_value(_a(statistic="mean", authority_fail="contributes_zero"), e) == Q.of(150)


def test_conditions_use_three_valued_logic() -> None:
    gap_in_authority = {"control_authority": flags(True, None, True, True)}
    # Signed off, authority unknown at one interval: a gap there, so unknown (strict policy).
    e = env(site("S1", **{"a.boundary_import_kw": S1_IMPORT, **gap_in_authority}))
    assert isinstance(_a_value(_a(), e), Unknown)
    # Not signed off: false AND unknown is false, so the interval is excluded, never a gap.
    e2 = env(
        site(
            "S1",
            **{"a.boundary_import_kw": S1_IMPORT, **gap_in_authority, "control_signoff": False},
        ),
        site("S2", **{"a.boundary_import_kw": S2_IMPORT}),
    )
    assert _a_value(_a(), e2) == Q.of(250)


def test_an_unknown_input_lists_every_missing_term_and_is_never_bounded_away() -> None:
    e = env(
        site("S1", **{"a.boundary_import_kw": S1_IMPORT}),
        site(
            "S2",
            **{"a.boundary_import_kw": Unknown(kind="unknown", reason="TEST FIXTURE: no meter")},
        ),
        site("S3"),
    )
    out = evaluate(resolve(DEFINITION_A, fill(_a())), e)
    assert isinstance(out.result, Unknown)
    assert [(m.term_id, m.scope) for m in out.missing] == [
        ("a.boundary_import_kw", "S2"),
        ("a.boundary_import_kw", "S3"),
    ]


def test_b_and_c_evaluate_on_synthetic_series() -> None:
    from gridq.ops.mw_formula import MemberEnv

    b_choices = {
        **BASE,
        "b.basis": "measured",
        "b.direction": "discharge_only",
        "b.idle": "exclude_interval",
        "b.scope": "all_site_batteries",
        "statistic": "max",
    }
    e = env(
        SiteEnv(
            site_id="S1",
            terms={**site("S1").terms, "b.instructed_kw": series(0, 500, -500, 500)},
            assets=(
                MemberEnv(
                    member_id="bess",
                    terms={"b.pcs_ac_kw": series(0, 480, -490, 470), "b.asset_enveloped": True},
                ),
            ),
        ),
    )
    assert evaluate(resolve(DEFINITION_B, fill(b_choices)), e).result == Q.of(480)
    c_choices = {
        **BASE,
        "c.point": "boundary",
        "c.shortfall": "clip_at_zero",
        "c.basis": "all_intervals",
        "c.compliance": "none",
    }
    c_env = env(
        site(
            "S1",
            **{
                "c.baseline_kw": series(1000, 1000, 1000, 1000),
                "c.net_import_kw": series(900, 1100, 700, 1000),
                "c.baseline_preregistered": True,
                "c.verifier_recomputed": True,
            },
        )
    )
    assert evaluate(resolve(DEFINITION_C, fill(c_choices)), c_env).result == Q.of(100)
    not_verified = env(site("S1", **{**dict(c_env.sites[0].terms), "c.verifier_recomputed": False}))
    assert isinstance(
        evaluate(resolve(DEFINITION_C, fill(c_choices)), not_verified).result, NotApplicable
    )
