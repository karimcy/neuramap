"""D55 — the ALL-STREAMS-ENABLED study basis, and the gb_v1_12 release that prices it.

The owner's instruction is that every revenue stream must be one the optimiser can pick from, with
every award assumed granted. This file pins the three things that instruction must NOT be allowed to
become:

1. **A stream priced with its consent asserted.** Every priced stream carries a
   ``pending_permission`` record; a ``derived_input`` in that slot is refused at construction. This
   is the D45 line and it is the only reason a basis that prices the Balancing Mechanism into a
   floor is honest at all.
2. **A stream priced at nothing.** Enabling a stream without a price would price it at £0 on a floor
   basis — the null-on-the-pricing-path defect. Enabled-set and priced-set must match exactly.
3. **A sum.** The streams are options, not addends. Two constraints are pinned from real gb_v1_12
   numbers: the published exclusivity families, and the single annual warranty throughput budget
   that a naive sum blows through by construction.

REACHABILITY (CLAUDE.md §4) — TWO call sites, and they are not the same claim. An earlier version of
this file conflated them, so the distinction is spelled out:

1. **The canonical-input shape**, ``gridq.quotes.assembly._canonical_run_input`` (assembly.py:221),
   which dumps the basis into the hash input behind ``input_hash``. The
   ``..._canonical_input_splits_...`` tests pin it, and ``..._mutation_removing_study_basis_...`` is
   its mutation proof. These call a **private** function and pass ``study_basis=`` themselves, so
   they prove the *shape* is right — **not** that any real run reaches it. Read as reachability they
   would be exactly the built-tested-never-called defect this repo has shipped eight times.
2. **The production path**, ``gridq.quotes.pipeline.generate_quote``, which reads the basis off the
   engine through the duck-typed ``last_study_basis()`` hook and forwards it. That is what a real
   ``gridq run`` executes, and it is pinned by ``..._generate_quote_forwards_...`` and its mutation
   proof. Until 2026-08-18 this forward did not exist: the basis reached ``input_hash`` only as
   PROSE inside ``OptimisationResult.warnings``, so identity depended on the wording of a sentence
   and nothing enforced the basis's LOW confidence ceiling.

No test in this file skips, xfails, or solves an LP.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError
from tests.test_study_basis_schema import study

from gridq.assumptions import AssumptionLibrary, load_assumptions
from gridq.engine import BaselineEngine
from gridq.provenance.hashing import hash_json
from gridq.quotes import generate_quote
from gridq.quotes.assembly import _canonical_run_input, assemble_quote_record
from gridq.schemas import AllStreamsEnabledStudyBasis, QuoteRecord, SiteModel, StudyBasis
from gridq.schemas.derived import ConfidenceBand, DerivedAssumption

RELEASE = "gb_v1_12"

#: The option set, gross of the aggregator cut, as landed in gb_v1_12 for a 2 h asset. Every number
#: is read back from the library by `test_every_price_in_the_option_set_is_read_back_from_gb_v1_12`,
#: so this dict cannot drift away from the release without a test going red.
PRICES_2H: dict[str, float] = {
    "WHOLESALE_P415": 22.92,  # MID year, 2 h, net of degradation, at 80% capture
    "BALANCING_MECHANISM": 17.47,  # method B: system-price uplift x (1 - VLP skip)
    "ANCILLARY_DC_DM_DR": 20.75,  # DCL net, the best £ per kWh of warranty throughput
    "CAPACITY_MARKET": 5.96,  # forward T-4 27.10 x 0.2198 de-rating at 2 h
    "DFS": 7.558,  # NESO winter 24/25 S2 average
}

#: The published exclusivity families. DFS may not share a meter point with a BM Unit (DFS
#: Procurement Rules v5.0 para 4.7.6, unconditional) and is conditionally barred from the response
#: family (4.7.9); the CM is expressly permitted alongside DFS (4.7.8(a)) and therefore appears in
#: NO family.
FAMILIES: list[list[str]] = [
    ["DFS", "BALANCING_MECHANISM"],
    ["DFS", "ANCILLARY_DC_DM_DR"],
]

#: warranty_cycle_limits.max_cycles_per_year_by_duration_h["2"] = 365 nameplate cycles x 2 kWh/kW.
WARRANTY_2H_KWH_PER_KW_YR = 730.0


def award(stream: str) -> DerivedAssumption:
    """The assumed-award record for ``stream``: a permission, never a value."""
    return DerivedAssumption(
        kind="pending_permission",
        key=f"{AllStreamsEnabledStudyBasis.AWARD_KEY_PREFIX}{stream}",
        missing_input=(
            f"No award, registration or customer consent exists for {stream}: no registered BM "
            "Unit, Response Unit, CMU, DFS registration or per-stream consent is held."
        ),
        value=None,
        unit=None,
        basis=(
            "D55 assumes the award for this study at the owner's direction. A permission is not "
            "derivable (D45): the stream is modelled and its value shown, never granted."
        ),
        sources=[],
        derivation_method="not_derivable",
        derived_by="claude-opus-5",
        derived_on_release=RELEASE,
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask=(
            f"A signed per-stream consent for {stream} plus the award or registration the stream "
            "requires, before any figure on this basis is treated as underwritten."
        ),
    )


def cut_record(value: float = 0.20) -> DerivedAssumption:
    """The aggregator revenue-share record: derived, cited, and honest about the NOT FOUND."""
    return DerivedAssumption(
        kind="derived_input",
        key=AllStreamsEnabledStudyBasis.AGGREGATOR_CUT_KEY,
        missing_input="No GB aggregator publishes a revenue-share percentage.",
        value=value,
        unit="fraction",
        basis=(
            "local_flex_piclo.aggregator_cut_pct (a placeholder in this library) carried across to "
            "the routed grid-market streams. SEARCH RECORDED: no percentage is published by Axle "
            "Energy, Flexitricity, Limejump or enspired, and Cornwall Insight's 'Revenue Stacking "
            "for Flexibility' report for NGED contains no share figure at all."
        ),
        sources=[
            "assumptions/gb_v1_12.yaml balancing_mechanism.study_basis_aggregator_route",
            "Cornwall Insight for NGED, 'Revenue Stacking for Flexibility', December 2023, "
            "https://www.flexiblepower.co.uk/downloads/1150 — searched, NO revenue share stated",
        ],
        derivation_method="sourced_default",
        derived_by="claude-opus-5",
        derived_on_release=RELEASE,
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask=(
            "One executed or quoted VLP/aggregator term sheet stating the revenue share and any "
            "floor or minimum fee, the contract term, any exclusivity, the markets included, and "
            "who carries imbalance and non-delivery liability."
        ),
    )


def all_streams(**over: object) -> AllStreamsEnabledStudyBasis:
    """A valid D55 basis: the D46 export fields, plus the option set, awards and exclusivity."""
    export = study()  # the D46 basis, reused so the export half is provably identical
    fields: dict[str, object] = {
        "basis_id": "all_streams_enabled_study_v1",
        "authorised_by": "Olly",
        "justification": (
            "D55 all-streams feasibility study: the owner directs that every revenue stream is "
            "available for the optimiser to pick from, with every award assumed granted."
        ),
        "acknowledgement": AllStreamsEnabledStudyBasis.ACKNOWLEDGEMENT,
        "export_limit_kw": export.export_limit_kw,
        "export_tariff_gbp_per_kwh": export.export_tariff_gbp_per_kwh,
        "tariff_record": export.tariff_record,
        "export_limit_record": export.export_limit_record,
        "consent_record": export.consent_record,
        "enabled_streams": list(PRICES_2H),
        "stream_price_gbp_per_kw_yr": dict(PRICES_2H),
        "stream_award_records": [award(s) for s in PRICES_2H],
        "aggregator_cut_pct": 0.20,
        "aggregator_cut_record": cut_record(),
        "exclusive_families": [list(f) for f in FAMILIES],
        "warranty_throughput_kwh_per_kw_yr": WARRANTY_2H_KWH_PER_KW_YR,
    }
    fields.update(over)
    return AllStreamsEnabledStudyBasis.model_validate(fields)


# --------------------------------------------------------------- the basis constructs and declares


def test_a_valid_all_streams_basis_constructs_and_caps_the_band_at_low() -> None:
    basis = all_streams()
    assert basis.basis_id == AllStreamsEnabledStudyBasis.BASIS_ID
    assert basis.basis_id != StudyBasis.BASIS_ID
    assert basis.confidence_ceiling is ConfidenceBand.LOW
    # It IS a StudyBasis, which is what lets every D46 consumer accept it untouched.
    assert isinstance(basis, StudyBasis)


def test_the_basis_has_no_default_and_cannot_be_conjured_from_nothing() -> None:
    with pytest.raises(ValidationError):
        AllStreamsEnabledStudyBasis()  # type: ignore[call-arg]


def test_acknowledgement_must_be_the_verbatim_all_streams_sentence() -> None:
    # D46's sentence is NOT enough: this basis prices five more streams and says so.
    with pytest.raises(ValidationError, match="verbatim sentence"):
        all_streams(acknowledgement=StudyBasis.ACKNOWLEDGEMENT)
    with pytest.raises(ValidationError, match="verbatim sentence"):
        all_streams(acknowledgement="all streams enabled")


def test_the_basis_id_of_the_export_only_basis_is_refused_here() -> None:
    with pytest.raises(ValidationError):
        all_streams(basis_id="export_in_priced_floor_study_v1")


# ------------------------------------------------------- awards are permissions, never assertions


def test_the_derived_assumption_class_itself_refuses_a_forged_award() -> None:
    """Defence in depth, and the OUTER layer catches it first — which is the correct order.

    `gridq.schemas.derived.is_permission_key` already binds every ``stream_consents.*`` key to
    ``pending_permission``, so an assumed award cannot even be CONSTRUCTED as a derived fact. This
    test records that the outer guard fires, so the next reader does not mistake the class validator
    below for the only thing standing between the owner's instruction and a fabricated consent.
    """
    with pytest.raises(ValidationError, match="names a PERMISSION"):
        DerivedAssumption(
            kind="derived_input",
            key=f"{AllStreamsEnabledStudyBasis.AWARD_KEY_PREFIX}BALANCING_MECHANISM",
            missing_input="no BM award",
            value=1.0,
            unit="fraction",
            basis="the owner said to assume the award",
            sources=["the brief"],
            derivation_method="sourced_default",
            derived_by="claude-opus-5",
            derived_on_release=RELEASE,
            confidence_ceiling=ConfidenceBand.LOW,
            confirmation_ask="n/a",
        )


def test_a_derived_input_in_an_award_slot_is_refused() -> None:
    """THIS class's own validator: a legal derived_input smuggled into an award slot is refused.

    The record below is perfectly valid in its own right (a derived export tariff), which is exactly
    why the check has to live here too: the award slot must reject any record that is not a
    permission, not merely records that failed to construct.
    """
    legal_but_wrong_kind = DerivedAssumption(
        kind="derived_input",
        key=StudyBasis.TARIFF_KEY,
        missing_input="no agreed export tariff",
        value=0.0869675,
        unit="GBP/kWh",
        basis="mean of the Elexon MID year",
        sources=["Elexon Insights MID, 2025-08-13..2026-08-12"],
        derivation_method="sourced_default",
        derived_by="claude-opus-5",
        derived_on_release=RELEASE,
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="an agreed export tariff from the offtaker",
    )
    records = [award(s) for s in PRICES_2H if s != "BALANCING_MECHANISM"]
    with pytest.raises(ValidationError, match="pending_permission"):
        all_streams(stream_award_records=[*records, legal_but_wrong_kind])


def test_every_priced_stream_must_carry_an_award_record() -> None:
    records = [award(s) for s in PRICES_2H if s != "DFS"]
    with pytest.raises(ValidationError, match="NO\n?\\s*assumed-award record|assumed-award record"):
        all_streams(stream_award_records=records)


def test_an_award_record_for_an_unpriced_stream_is_refused() -> None:
    records = [award(s) for s in PRICES_2H] + [award("LOCAL_FLEX_PICLO")]
    with pytest.raises(ValidationError, match="does not price"):
        all_streams(stream_award_records=records)


def test_every_award_record_reads_as_outstanding_and_carries_no_value() -> None:
    basis = all_streams()
    assert len(basis.stream_award_records) == len(PRICES_2H)
    for record in basis.stream_award_records:
        assert record.kind == "pending_permission"
        assert record.value is None
        assert record.confirmation_ask.strip()
    # And the disclosure repeats it in prose the reader cannot miss.
    assert "ASSUMED, NOT HELD" in basis.note()
    assert "requires an award and customer consent" in basis.note()
    assert "MAY NEVER BE PRESENTED AS AN UNDERWRITING OUTPUT" in basis.note()


# ---------------------------------------------------------------- no stream may price at nothing


def test_an_enabled_stream_with_no_price_is_refused() -> None:
    prices = {k: v for k, v in PRICES_2H.items() if k != "CAPACITY_MARKET"}
    with pytest.raises(ValidationError, match="Enabled with no price"):
        all_streams(stream_price_gbp_per_kw_yr=prices)


def test_a_price_for_a_stream_that_is_not_enabled_is_refused() -> None:
    prices = dict(PRICES_2H, MIC_ASC_HEADROOM=3.0)
    with pytest.raises(ValidationError, match="priced but not enabled"):
        all_streams(stream_price_gbp_per_kw_yr=prices)


def test_a_duplicated_stream_is_refused_because_its_price_could_be_summed_twice() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        all_streams(enabled_streams=[*PRICES_2H, "DFS"])


# --------------------------------------------------------------------- the aggregator cut, once


def test_the_aggregator_cut_applies_to_routed_streams_and_only_to_them() -> None:
    basis = all_streams()
    assert set(basis.routed_streams()) == set(PRICES_2H)  # all five are routed
    for stream, gross in PRICES_2H.items():
        assert basis.net_price_gbp_per_kw_yr(stream) == pytest.approx(gross * 0.80)
    # A bill-avoidance stream is never routed, so no cut would apply to it.
    assert "DUOS_RED_BAND_AVOIDANCE" not in AllStreamsEnabledStudyBasis.ROUTED_STREAMS
    assert "TARIFF_ARBITRAGE" not in AllStreamsEnabledStudyBasis.ROUTED_STREAMS


def test_an_uncited_aggregator_cut_is_refused() -> None:
    naked = cut_record().model_copy(update={"sources": []})
    with pytest.raises(ValidationError, match="cites no source"):
        all_streams(aggregator_cut_record=naked)


def test_a_cut_record_that_disagrees_with_the_priced_cut_is_refused() -> None:
    with pytest.raises(ValidationError, match="must equal the value the model"):
        all_streams(aggregator_cut_pct=0.30, aggregator_cut_record=cut_record(0.20))


def test_a_cut_of_one_or_more_is_refused() -> None:
    for bad in (1.0, 1.5):
        with pytest.raises(ValidationError):
            all_streams(aggregator_cut_pct=bad, aggregator_cut_record=cut_record(bad))


# ------------------------------------------------- options, not addends: exclusivity and throughput


def test_the_published_exclusivity_families_are_detected() -> None:
    basis = all_streams()
    assert basis.exclusivity_violations(["BALANCING_MECHANISM", "ANCILLARY_DC_DM_DR"]) == []
    assert basis.exclusivity_violations(["CAPACITY_MARKET", "DFS"]) == []  # 4.7.8(a) permits it
    assert basis.exclusivity_violations(["DFS", "BALANCING_MECHANISM"]) == [
        ["BALANCING_MECHANISM", "DFS"]
    ]
    # Selecting DFS with the whole grid stack breaches both families at once.
    assert len(basis.exclusivity_violations(list(PRICES_2H))) == 2


def test_an_exclusivity_family_over_an_unpriced_stream_is_refused() -> None:
    with pytest.raises(ValidationError, match="not\n?\\s*enabled|are not enabled"):
        all_streams(exclusive_families=[["DFS", "LOCAL_FLEX_PICLO"]])


def test_a_one_member_exclusivity_family_is_refused_because_it_constrains_nothing() -> None:
    with pytest.raises(ValidationError, match="fewer"):
        all_streams(exclusive_families=[["DFS"]])


def test_the_naive_sum_of_the_option_set_is_physically_impossible() -> None:
    """The arithmetic that makes 'enable everything' ≠ 'add everything up'.

    Wholesale arbitrage at 2 h is one cycle a day = 730 kWh/kW/yr, which is EXACTLY the whole
    warranty budget. Add DCL's 58-69 kWh/kW/yr of response throughput and DFS's 56 kWh/kW/yr and the
    selection is over-committed before bill avoidance has run a single half-hour.
    """
    basis = all_streams()
    wholesale_kwh = 730.0  # gb_v1_12 wholesale_p415, 2 h, discharge_kwh_per_kw_yr
    dcl_kwh = 58.1  # gb_v1_12 ancillary_services, DCL low throughput
    dfs_kwh = 56.0  # gb_v1_12 DFS S2 central: 112 settlement periods x 0.5 h
    assert basis.throughput_headroom_kwh_per_kw_yr(wholesale_kwh) == pytest.approx(0.0)
    over = basis.throughput_headroom_kwh_per_kw_yr(wholesale_kwh + dcl_kwh + dfs_kwh)
    assert over < 0.0, "a naive stack must show NEGATIVE throughput headroom"
    assert over == pytest.approx(-114.1)


def test_the_option_set_is_labelled_as_options_and_never_as_a_total() -> None:
    basis = all_streams()
    options = basis.option_set()
    assert len(options) == len(PRICES_2H)
    assert all(o["award_assumed_not_held"] is True for o in options)
    assert all(o["aggregator_routed"] is True for o in options)
    dfs = next(o for o in options if o["stream"] == "DFS")
    assert dfs["exclusive_with"] == ["ANCILLARY_DC_DM_DR", "BALANCING_MECHANISM"]
    assert "OPTIONS, NOT A TOTAL" in basis.note()
    assert "Summing them describes a battery that does not exist" in basis.note()


def test_an_unpriced_stream_raises_rather_than_returning_zero() -> None:
    basis = all_streams()
    with pytest.raises(KeyError):
        basis.net_price_gbp_per_kw_yr("MIC_ASC_HEADROOM")


def test_the_disclosure_names_every_register_key_the_basis_consumes() -> None:
    basis = all_streams()
    keys = basis.assumption_keys()
    assert keys[:3] == [StudyBasis.TARIFF_KEY, StudyBasis.EXPORT_LIMIT_KEY, StudyBasis.CONSENT_KEY]
    assert AllStreamsEnabledStudyBasis.AGGREGATOR_CUT_KEY in keys
    for stream in PRICES_2H:
        assert f"{AllStreamsEnabledStudyBasis.AWARD_KEY_PREFIX}{stream}" in keys
    # One statement per record: D46's three, the cut, and one per assumed award.
    assert len(basis.statements()) == 4 + len(PRICES_2H)


# ------------------------------------------------------------------------------- REACHABILITY


@pytest.fixture(scope="module")
def parts() -> tuple:
    """A minimal (site, result, assumptions) triple — no solve is needed to pin a hash shape."""
    from tests.test_study_basis_hash_identity import parts as _parts  # reuse the D46 fixture

    return _parts.__wrapped__(None)  # type: ignore[attr-defined]


def test_the_canonical_input_splits_the_three_bases_into_three_identities(parts: tuple) -> None:
    """`assembly._canonical_run_input` (assembly.py:221) dumps the basis into `input_hash`.

    Three identities, all different: no basis, the D46 export basis, the D55 all-streams basis. If
    the all-streams basis did not reach the hash, the second and third would collide and a reader
    would have only prose to tell an export-only study from an everything-enabled one.

    **This is a SHAPE test, not a reachability proof** — it calls a private function and hands it
    basis itself. The production path is pinned separately, below, through `generate_quote`.
    """
    site, result, lib = parts
    none_h = hash_json(_canonical_run_input(site, result, None, lib, None))
    export_h = hash_json(_canonical_run_input(site, result, None, lib, None, study_basis=study()))
    all_h = hash_json(
        _canonical_run_input(site, result, None, lib, None, study_basis=all_streams())
    )
    assert len({none_h, export_h, all_h}) == 3, (none_h, export_h, all_h)


def test_mutation_removing_study_basis_from_the_hash_collides_the_two_bases(parts: tuple) -> None:
    """The mutation proof for the reachability claim above (CLAUDE.md §4).

    Delete the ``study_basis`` key from the canonical dict — the single line at ``assembly.py:221``
    — and the export-only and all-streams identities become the SAME hash. That collision is the
    defect the call site prevents, so this test is what fails if the call site is removed.
    """
    site, result, lib = parts
    export = _canonical_run_input(site, result, None, lib, None, study_basis=study())
    every = _canonical_run_input(site, result, None, lib, None, study_basis=all_streams())
    assert hash_json(export) != hash_json(every)
    export.pop("study_basis")
    every.pop("study_basis")
    assert hash_json(export) == hash_json(every), (
        "with the study_basis key removed the two bases MUST collide; if they still differ, "
        "something else is carrying the basis and this reachability proof is measuring the wrong "
        "call site."
    )


def test_two_different_option_sets_are_two_different_runs(parts: tuple) -> None:
    """Not just 'a basis is present' — WHICH streams were enabled is part of the identity."""
    site, result, lib = parts
    full = all_streams()
    without_dfs = all_streams(
        enabled_streams=[s for s in PRICES_2H if s != "DFS"],
        stream_price_gbp_per_kw_yr={k: v for k, v in PRICES_2H.items() if k != "DFS"},
        stream_award_records=[award(s) for s in PRICES_2H if s != "DFS"],
        exclusive_families=[],
    )
    a = hash_json(_canonical_run_input(site, result, None, lib, None, study_basis=full))
    b = hash_json(_canonical_run_input(site, result, None, lib, None, study_basis=without_dfs))
    assert a != b
    # And so is the aggregator cut: a term sheet at 30% is a different run from one at 20%.
    thirty = all_streams(aggregator_cut_pct=0.30, aggregator_cut_record=cut_record(0.30))
    c = hash_json(_canonical_run_input(site, result, None, lib, None, study_basis=thirty))
    assert c not in {a, b}


# ------------------------------------------------------------------------- the gb_v1_12 release


def test_gb_v1_12_loads_and_carries_every_gb_v1_11_value_unchanged() -> None:
    """Additive by construction. Silent value drift between releases is a defect."""
    before, after = load_assumptions("gb_v1_11"), load_assumptions(RELEASE)
    assert after.metadata.version == RELEASE
    sections = [
        "battery_capex",
        "battery_opex",
        "degradation",
        "round_trip_efficiency",
        "parasitic_load",
        "warranty_cycle_limits",
        "financing",
        "dscr_covenants",
        "p50_p90_haircuts",
        "tariff_arbitrage",
        "duos",
        "mic_asc_headroom",
        "capacity_market",
        "local_flex_piclo",
        "wholesale_p415",
        "balancing_mechanism",
        "ancillary_services",
        "business_rates",
        "insurance",
        "metering_idno",
        "dno_costs",
        "quote_validity",
        "scenario_weights",
        "availability",
        "penalties",
        "tnuos",
    ]
    # D57 item 7 (Olly, 2026-08-18): exactly ONE gb_v1_11 value moves in this release, and it is
    # enumerated here rather than allowed as slack. Its own test
    # (`test_the_two_parasitic_keys_now_carry_one_reconciled_value`) pins the new value, the
    # direction of effect and the release note; this test's job is to prove nothing ELSE drifted.
    d57_item_7_moved = {("battery_opex", "parasitic_load_fraction_of_power"): (0.01, 0.005)}
    # THE ARITHMETIC-FIX ADDITIONS (2026-08-19). `local_flex_piclo.awarded_zone_clears` gains one
    # field per awarded row - the annual availability-hour count that row already published inside
    # its own `service_window` prose - because the availability leg was re-deriving those hours from
    # the award's declared clock times and getting 180 h at Harwell against the source row's own
    # 136 h, a 1.32x overstatement that does not reconcile to SSEN's published GBP 8,200 tranche
    # budget. Enumerated rather than allowed as slack, and the check below proves that every PRICED
    # figure in the entry is byte-identical and that the ONLY additions are the named hour counts.
    d57_arithmetic_additions = {
        ("local_flex_piclo", "awarded_zone_clears"): {
            "nged_wm_port_ham_saou_da_dtugtd": {"availability_h_per_year": 1098.0},
            "ssen_sepd_wallingford_vaou_wa": {"availability_h_per_year": 136.0},
        }
    }
    for section in sections:
        prior = getattr(before, section) or {}
        current = getattr(after, section) or {}
        for name, was in prior.items():
            assert name in current, f"gb_v1_12 DROPPED {section}.{name} — that breaks callers"
            moved = d57_item_7_moved.get((section, name))
            if moved is not None:
                assert (was.value, current[name].value) == moved, (
                    f"{section}.{name} is the ONE authorised move (D57 item 7) and it did not move "
                    f"from {moved[0]} to {moved[1]}"
                )
                continue
            added = d57_arithmetic_additions.get((section, name))
            if added is not None:
                now = {
                    k: dict(v) if isinstance(v, dict) else v for k, v in current[name].value.items()
                }
                for row_key, fields in added.items():
                    row = now[row_key]
                    assert isinstance(row, dict), f"{section}.{name}.{row_key} is not a row"
                    for field, expected in fields.items():
                        assert row.pop(field, None) == expected, (
                            f"{section}.{name}.{row_key}.{field} must publish {expected}"
                        )
                # `note` is prose and it was corrected in the same change (D57 limb 5b made "no code
                # prices an award from this entry" false); every other cell must be untouched.
                strip = {k: v for k, v in now.items() if k != "note"}
                prior_strip = {k: v for k, v in was.value.items() if k != "note"}
                assert strip == prior_strip, f"VALUE DRIFT at {section}.{name}"
                continue
            assert current[name].value == was.value, f"VALUE DRIFT at {section}.{name}"


def test_gb_v1_12_leaves_both_underwriting_refusals_at_zero() -> None:
    """The zeros are documented refusals for the UNDERWRITING basis. D55 does not repeal them."""
    lib = load_assumptions(RELEASE)
    assert lib.get("wholesale_p415", "gated_upside_value_gbp_per_mw_yr").value == 0
    assert lib.get("balancing_mechanism", "gated_upside_value_gbp_per_mw_yr").value == 0


#: Keys added to gb_v1_12 AFTER the D55 batch, with the date each was reviewed. Listed rather than
#: date-globbed so a further addition has to be declared here and cannot arrive unnoticed.
_POST_D55_ADDITIONS = {
    # D60 (2026-08-19): the measured 12-zone DFS outturn, the citable copy of the table
    # `gridq.value_stack.gb_streams` transcribes. It is the one key in the release whose direction
    # of effect is DOWN: the observed period count replaces an annualisation of it, 3.16x fewer
    # paid periods in the campaign's own zone.
    ("ancillary_services", "dfs_zonal_observed_2026_27"): "2026-08-19",
}


def test_every_new_gb_v1_12_key_is_needs_review_and_low_confidence() -> None:
    """Every new key moves revenue, so not one of them may read as confirmed.

    The D55 batch is TEN keys reviewed 2026-08-18. Anything added afterwards must appear in
    `_POST_D55_ADDITIONS` with its own review date, so the count assertion keeps its teeth while a
    later addition is declared rather than absorbed.
    """
    before, after = load_assumptions("gb_v1_11"), load_assumptions(RELEASE)
    new: list[tuple[str, str]] = []
    sections = ("capacity_market", "wholesale_p415", "balancing_mechanism", "ancillary_services")
    for section in sections:
        prior, current = getattr(before, section), getattr(after, section)
        new.extend((section, name) for name in current if name not in prior)
    assert len(new) == 10 + len(_POST_D55_ADDITIONS), f"undeclared new key: {new!r}"
    for section, name in new:
        a = getattr(after, section)[name]
        assert a.status.value == "needs_review", f"{section}.{name} is {a.status.value}"
        assert a.confidence.value == "low", f"{section}.{name} is {a.confidence.value}"
        assert a.source.strip() and "http" in a.source, f"{section}.{name} carries no URL"
        assert str(a.last_reviewed) == _POST_D55_ADDITIONS.get((section, name), "2026-08-18")


def test_every_price_in_the_option_set_is_read_back_from_gb_v1_12() -> None:
    """PRICES_2H is not a second copy of the release — it is checked against it, key by key."""
    lib = load_assumptions(RELEASE)
    ws = lib.get("wholesale_p415", "study_basis_perfect_foresight_arbitrage_gbp_per_kw_yr").value
    assert ws["by_duration_h"]["2"]["at_80pct_capture"] == PRICES_2H["WHOLESALE_P415"]
    bm = lib.get("balancing_mechanism", "study_basis_marginal_value_gbp_per_kw_yr").value
    assert bm["central_gbp_per_kw_yr_2h"] == PRICES_2H["BALANCING_MECHANISM"]
    assert bm["method_b_bottom_up"]["gbp_per_kw_yr"]["2h"] == PRICES_2H["BALANCING_MECHANISM"]
    anc = lib.get("ancillary_services", "study_basis_availability_gbp_per_kw_yr_by_product").value
    assert anc["by_product"]["DCL"]["net_gbp_per_kw_yr"][0] == PRICES_2H["ANCILLARY_DC_DM_DR"]
    cm = lib.get("capacity_market", "study_basis_forward_gbp_per_kw_yr_by_duration").value
    assert cm["by_duration_h"]["2"] == PRICES_2H["CAPACITY_MARKET"]
    assert cm["clearing_price_gbp_per_kw_yr"] == 27.1  # the FORWARD auction, not the £63 headline
    dfs = lib.get("ancillary_services", "study_basis_dfs_gbp_per_kw_yr").value
    assert dfs["central_s2_avg"] == PRICES_2H["DFS"]


def test_gb_v1_12_carries_a_dispatchable_half_hourly_shape_for_every_new_stream() -> None:
    """A price with no shape is a stream the MILP cannot dispatch against. Each one has both."""
    lib = load_assumptions(RELEASE)
    mid = lib.get("wholesale_p415", "study_basis_mid_half_hourly_price_series").value
    matrix = mid["month_by_settlement_period_mean_gbp_per_mwh"]
    assert mid["half_hours"] == 17521
    assert len(matrix) == 12 and {len(v) for v in matrix.values()} == {48}
    assert min(min(v) for v in matrix.values()) > -200
    assert max(max(v) for v in matrix.values()) < 600
    bm = lib.get("balancing_mechanism", "study_basis_marginal_value_gbp_per_kw_yr").value
    assert "hour-of-day mean" in bm["hh_shape"].lower() and bm["local_csv_sha256"]
    skip = lib.get("balancing_mechanism", "study_basis_battery_skip_rate_stage5_psa").value
    assert set(skip["hh_shape_offer_skip_by_hour_2026_ytd"]) >= {"minimum", "maximum"}
    anc = lib.get("ancillary_services", "study_basis_availability_gbp_per_kw_yr_by_product").value
    assert "EFA" in anc["hh_shape"]
    dfs = lib.get("ancillary_services", "study_basis_dfs_gbp_per_kw_yr").value
    assert dfs["event_count_per_season"]["settlement_periods_procured"] == 236
    cm = lib.get("capacity_market", "study_basis_forward_gbp_per_kw_yr_by_duration").value
    assert "16:00-19:00" in cm["hh_shape"]
    assert cm["throughput_kwh_per_kw_yr"] == 0.0  # a capacity payment, not an energy one


def test_the_skip_rate_is_labelled_sourced_not_owner_supplied() -> None:
    """The owner referred to 'the skip rate I mentioned'. No such figure exists in this repo."""
    lib = load_assumptions(RELEASE)
    skip = lib.get("balancing_mechanism", "study_basis_battery_skip_rate_stage5_psa").value
    prov = skip["provenance_of_the_number"]
    assert "NOT SUPPLIED BY THE OWNER" in prov
    assert "CONFIRMATION ASK" in prov
    assert skip["central_vlp_route_2026"] == 0.412  # the VLP route, June 2026, Stage-5 PSA


def test_the_index_derived_bm_basis_warns_against_double_haircutting_the_skip_rate() -> None:
    """Applying a skip haircut to a REALISED index is a double count. The key says so."""
    lib = load_assumptions(RELEASE)
    bm = lib.get("balancing_mechanism", "study_basis_marginal_value_gbp_per_kw_yr").value
    warning = bm["method_a_index_derived"]["SKIPS_ARE_ALREADY_IN_THIS_NUMBER"]
    assert "DOUBLE COUNT" in warning
    # The two independent methods must agree within 10%, or the corroboration claim is empty.
    a = bm["method_a_index_derived"]["gbp_per_kw_yr_2025_vlp_adjusted"]
    b = bm["method_b_bottom_up"]["gbp_per_kw_yr"]["2h"]
    assert abs(a - b) / a < 0.10, (a, b)


def test_the_aggregator_route_records_the_not_found_rather_than_inventing_a_share() -> None:
    lib = load_assumptions(RELEASE)
    agg = lib.get("balancing_mechanism", "study_basis_aggregator_route").value
    assert agg["revenue_share_pct"] == 20.0
    assert "NO GB AGGREGATOR PUBLISHES A REVENUE SHARE" in agg["revenue_share_status"]
    assert agg["contract_term_pct_and_exclusivity"] == "NOT FOUND"
    proof = agg["real_world_existence_proof"]
    assert "Axle Energy" in proof["awards"] and "Fuse Energy" in proof["awards"]
    assert set(agg["applies_to"]) == set(AllStreamsEnabledStudyBasis.ROUTED_STREAMS)
    assert not set(agg["does_not_apply_to"]) & set(AllStreamsEnabledStudyBasis.ROUTED_STREAMS)


def test_the_exclusivity_matrix_is_in_the_release_and_matches_the_basis_families() -> None:
    lib = load_assumptions(RELEASE)
    rules = lib.get("ancillary_services", "study_basis_stream_exclusivity_matrix").value
    assert "FORBIDDEN" in rules["rule_3_dfs_exclusivity"]["bm_unit"]
    assert rules["rule_3_dfs_exclusivity"]["capacity_market"].startswith("PERMITTED")
    assert "730 kWh/kW/yr" in rules["rule_2_one_warranty_budget"]
    # The families the basis declares are exactly the ones the release's rules require.
    basis = all_streams()
    assert basis.exclusivity_violations(["DFS", "BALANCING_MECHANISM"]), "rule 4.7.6 must bind"
    assert not basis.exclusivity_violations(["DFS", "CAPACITY_MARKET"]), "4.7.8(a) permits CM+DFS"


def test_the_two_parasitic_keys_now_carry_one_reconciled_value() -> None:
    """D57 item 7: the 2x disagreement is closed by MOVING the fraction to the favourable end.

    **What this test used to assert, and why the inversion is authorised.** It required
    ``frac == 0.01`` and ``standby == 5.0`` with the disagreement resolved by SCOPE (auxiliary-only
    against auxiliary-plus-PCS) and "no value moved". That reading of PNNL-28379 is true, but it
    left the DISPATCH and FINANCE reading 0.01 while the VALUE STACK read 0.005 for the same
    physical quantity - a 2x disagreement on the largest opex line, measured at GBP 7,869/yr on
    Harwell's
    320.7 kW candidate. D56 takes the FAVOURABLE END of a sourced range and both ends are cited
    to the same primary source, so D57 item 7 moves the fraction to 0.005 and the keys now AGREE.

    It dies if the release value is put back to 0.01, if the two keys drift apart again, or if the
    release stops saying which value moved and in which direction.
    """
    before, after = load_assumptions("gb_v1_11"), load_assumptions(RELEASE)
    frac_b = before.get("battery_opex", "parasitic_load_fraction_of_power")
    frac_a = after.get("battery_opex", "parasitic_load_fraction_of_power")
    standby_b = before.get("parasitic_load", "standby_kw_per_mw")
    standby_a = after.get("parasitic_load", "standby_kw_per_mw")
    assert frac_b.value == 0.01, "the gb_v1_11 baseline this release moved away from"
    assert frac_a.value == 0.005, "D57 item 7 takes the auxiliary-only, favourable cited end"
    assert standby_a.value == standby_b.value == 5.0, "this key does NOT move"
    for a in (frac_a, standby_a):
        assert a.status.value == "needs_review", "was placeholder, now sourced"
        assert "PNNL-28379" in a.source
        assert "2,000 kW" in a.source
    # ONE figure for one physical quantity: 10 kW auxiliary / 2,000 kW rated = 5.0 kW/MW = 0.005.
    assert standby_a.value / 1000.0 == pytest.approx(frac_a.value, rel=1e-9)
    assert "0.01 -> 0.005" in frac_a.source
    assert "FAVOURABLE END" in frac_a.notes
    assert "DIRECTION OF EFFECT" in frac_a.notes
    assert "THIS VALUE IS UNCHANGED AT 5.0 kW/MW" in standby_a.notes


# --------------------------------------------------- REACHABILITY: THE REAL PRODUCTION PATH


class _StudyBasisEngine(BaselineEngine):
    """A `BaselineEngine` that also exposes the duck-typed ``last_study_basis()`` hook.

    The same fixture shape as `tests/test_quotes_pipeline_progress._SinkAwareEngine`: the point is
    exercise `generate_quote`'s real forwarding logic without paying for a half-hourly MILP solve.
    `MilpEngine` exposes this hook at ``optimisation/engine.py:2527``; what is under test is the
    PIPELINE's side of it, which is where the wiring was missing.
    """

    def __init__(self, basis: StudyBasis | None) -> None:
        self._basis = basis

    def last_study_basis(self) -> StudyBasis | None:
        return self._basis


def _quote_on(basis: StudyBasis | None, site: SiteModel, lib: AssumptionLibrary) -> QuoteRecord:
    """One `generate_quote` run whose engine reports ``basis`` — the production entry point."""
    return generate_quote(site, lib, engine=_StudyBasisEngine(basis)).quote


def test_generate_quote_forwards_the_engines_study_basis_into_run_identity(
    make_site: Callable[..., SiteModel],
) -> None:
    """THE reachability proof: `quotes/pipeline.py::generate_quote` → `assemble_quote_record`.

    Production call site: the ``study_basis=study_basis`` argument in `generate_quote`'s
    `assemble_quote_record` call, fed by the ``last_study_basis()`` hook read a few lines above it.

    Before this wiring landed (2026-08-18) `generate_quote` passed no basis at all, so the only
    separating an all-streams study run from an underwriting run of the same site was the engine's
    prose warning. Three real runs through the real entry point, three different ``input_hash``es.
    """
    lib = load_assumptions(RELEASE)
    site = make_site()
    none_q = _quote_on(None, site, lib)
    export_q = _quote_on(study(), site, lib)
    all_q = _quote_on(all_streams(), site, lib)
    hashes = {q.input_hash for q in (none_q, export_q, all_q)}
    assert len(hashes) == 3, f"two bases share an input_hash through generate_quote: {hashes}"
    # And the derived identity a reader quotes back at you moves with it.
    assert len({q.quote_id for q in (none_q, export_q, all_q)}) == 3


def test_mutation_dropping_the_forward_in_generate_quote_collides_the_bases(
    make_site: Callable[..., SiteModel],
) -> None:
    """The mutation proof for the production path (CLAUDE.md §4: name the call site AND the test).

    Simulates deleting ``study_basis=study_basis`` from `generate_quote`'s `assemble_quote_record`
    call by calling `assemble_quote_record` the way the pipeline did *before* the fix — everything
    else identical. The two bases then collide, which is the defect the forward prevents.
    """
    lib = load_assumptions(RELEASE)
    site = make_site()
    engine = _StudyBasisEngine(None)
    result = engine.solve(engine.build_optimisation_request(site, lib))
    unmutated = {
        assemble_quote_record(site, result, None, lib, study_basis=b).input_hash
        for b in (study(), all_streams())
    }
    assert len(unmutated) == 2
    mutated = {
        assemble_quote_record(site, result, None, lib).input_hash for _ in (study(), all_streams())
    }
    assert len(mutated) == 1, (
        "with the study_basis forward removed the two bases MUST collide; if they still differ, "
        "this mutation is measuring the wrong call site."
    )


def test_the_study_basis_caps_the_records_confidence_band_at_its_own_ceiling(
    make_site: Callable[..., SiteModel],
) -> None:
    """`StudyBasis.note()` asserts "this run's confidence band is capped at LOW" — now enforced.

    Production call site: the ``confidence_ceiling`` computed in `generate_quote` immediately after
    the ``last_study_basis()`` read, handed to `assemble_quote_record`. Nothing read
    ``study_basis.confidence_ceiling`` anywhere in ``src/`` before 2026-08-18, so the sentence was
    true only by coincidence of whatever Gate 2 happened to admit.
    """
    lib = load_assumptions(RELEASE)
    site = make_site()
    basis = all_streams()
    assert basis.confidence_ceiling is ConfidenceBand.LOW
    on_basis = _quote_on(basis, site, lib)
    baseline = _quote_on(None, site, lib)
    assert on_basis.confidence_ceiling is ConfidenceBand.LOW
    # A study basis may only LOWER the band. Where Gate 2 already capped at LOW the two agree; where
    # it admitted something stronger, the basis must win — assert the direction, never a fixture's
    # incidental value.
    assert on_basis.confidence_ceiling.rank <= baseline.confidence_ceiling.rank


# ----------------------------------------------- THE BASIS IS STAMPED ON EVERY ARTIFACT (D46/D55)


def test_every_renderer_states_the_all_streams_basis_and_never_the_export_only_one(
    make_site: Callable[..., SiteModel],
) -> None:
    """The D55 basis must be visible to all four renderers, in ITS OWN words.

    Production call sites: `outputs/viewmodel.py` (``_STUDY_BASIS_PREFIX`` detection +
    ``study_basis_label``), then `underwriting._banner`, `html_report._banner`,
    `consulting_report._head`/`_doc_title`/`_caveats` and `proposal.render_customer_proposal`.

    MEASURED DEFECT THIS PINS (2026-08-18): the detector was the literal ``"STUDY BASIS (D46)"``, so
    on a D55 run ``vm.study_basis`` was ``False`` and every one of the four renderers dropped its
    banner — the customer proposal called a study run an "underwriting run" three times and the
    underwriting pack carried no basis banner at all. D46 records that the labelling IS the entire
    mitigation for a second pricing basis, so this is the test that must fail if it regresses.
    """
    from gridq.outputs.consulting_report import render_consulting_report
    from gridq.outputs.html_report import render_html_report
    from gridq.outputs.proposal import render_customer_proposal
    from gridq.outputs.underwriting import render_underwriting_memo
    from gridq.outputs.viewmodel import build_view_model

    lib = load_assumptions(RELEASE)
    site = make_site()

    # Each basis's SHORT FORM — "STUDY BASIS (D55)" — is what an artifact must print. Matching on
    # the short form rather than the whole label is deliberate: the consulting report and customer
    # proposal pass through `formatting.no_dashes`, which rewrites the label's em dashes to commas,
    # so the full string is legitimately absent from two of the four documents.
    def short(b: StudyBasis) -> str:
        return b.LABEL.split(" — ")[0]

    for basis, other in ((all_streams(), study()), (study(), all_streams())):
        # The record comes from the real entry point, so the note it carries is the one the
        # pipeline actually writes — not one this test pasted on.
        record = _quote_on(basis, site, lib)
        vm = build_view_model(record, site)
        assert vm.study_basis, f"{basis.basis_id} is invisible to the viewmodel"
        assert vm.study_basis_label == basis.LABEL
        # The three INTERNAL artifacts must name the basis by its own label: their reader is an
        # underwriter or a lender, and which basis priced the figures governs what they may do with
        # them.
        internal = {
            "underwriting_memo": render_underwriting_memo(vm),
            "consulting_report": render_consulting_report(vm, site=site),
            "html_report": render_html_report(vm),
        }
        for name, text in internal.items():
            assert short(basis) in text, (
                f"{name} does not name the basis that priced it ({basis.basis_id}), so a "
                "study-basis figure is presented as an underwriting one"
            )
            assert "NOT AN UNDERWRITING OUTPUT" in text.upper()
            # The OTHER basis's label must be absent. Asserted on the label, not on the bare
            # decision id: a D55 run legitimately mentions "D46" because it INHERITS D46's export
            # tariff, limit and consent records, whose own provenance text names the decision that
            # created them. What must never happen is an artifact presenting itself under the wrong
            # basis — which is what a hardcoded banner did.
            assert short(other) not in text, (
                f"{name} describes a {basis.basis_id} run under {short(other)} — on the "
                "all-streams basis that understates the priced floor by five streams"
            )
        # The CUSTOMER proposal states the same fact in the customer's language: a plain-English
        # document leads with "feasibility study, not an offer" rather than a decision id. (It does
        # also carry the verbatim record statements on its D56 assumptions page, by deliberate
        # design — "a customer is entitled to both" — so this asserts the lead, not the absence of
        # every internal id.) What must never happen is the one thing that did: calling it an
        # underwriting run.
        proposal = render_customer_proposal(vm)
        assert "Feasibility study, not an offer." in proposal
        assert "the underwriting run" not in proposal
        assert "feasibility study run" in proposal
