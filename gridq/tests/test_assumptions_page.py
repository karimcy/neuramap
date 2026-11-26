"""The dedicated assumptions page reaches the BYTES of all four packs (D56 rule 4).

Owner instruction, verbatim (2026-08-18): *"ensure all these assumption go on a fresh page in the
report"* / *"recording all assumptions to be put into the final report on a new page"*. Recorded as
D56 in `docs/decisions.md` and as rule 4 of `CLAUDE.md` section 2.0.

**Why this file asserts on rendered bytes and builds its own records.** This repo has shipped the
same built-tested-never-called defect eleven times, every one with a passing test. Two rules follow
and both are load-bearing here:

* Every assertion is against the output of `gridq.outputs.generate.generate_artifacts`, rendered end
  to end, and each of the four artifacts is asserted **separately** — the classic version of this
  defect is a page wired into three packs out of four.
* The `QuoteRecord` and `SiteModel` are constructed *in this file*. `runs/` is gitignored, so a test
  that loads a real run directory passes vacuously on a fresh checkout, which is no test at all.

The one thing read off disk is `examples/real/sites/harwell.json`, which IS checked in, and it is
read for a single purpose: to prove the published range this page transcribes still matches the
record it was transcribed from.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from tests._dpa_support import dpa_released, dpa_released_site

from _tariff_support import SYNTHETIC_BASIS, SYNTHETIC_PROVENANCE
from gridq.assumptions import load_assumptions
from gridq.outputs import formatting as fmt
from gridq.outputs.generate import generate_artifacts
from gridq.outputs.viewmodel import build_view_model
from gridq.schemas import (
    AvailabilityCommitment,
    AvailabilityWindow,
    BankableStatus,
    ConfidenceBand,
    ConnectionDetails,
    ConsentState,
    Counterparty,
    CreditStatus,
    DataTier,
    DerivedAssumption,
    Firmness,
    ImplementationLevel,
    LoadDataSummary,
    MarketStream,
    OptimisationResult,
    PhysicalGateStatus,
    QuoteMode,
    QuoteRecord,
    SiteModel,
    SolverStatus,
    TariffStack,
    ValueStreamResult,
)

REAL_SITES = Path(__file__).resolve().parents[1] / "examples" / "real" / "sites"
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

#: The pipeline's own disclosure format (`gridq.quotes.pipeline`, two call sites). Reproduced here
#: rather than imported so the page's parser is tested against the *format*, and a change to either
#: side shows up as a failure instead of silently agreeing with itself.
WARNING_PREFIX = "ASSUMPTION REQUIRING CONFIRMATION"

#: The exact strings the owner's worked example requires on the page (D56 limb 1).
CM_VALUE = "0.30581"
CM_RANGE = "5.313 to 31.382 p/kWh"
CM_COMPARATOR = "17.057 p/kWh"
CM_KEY = "tariff_stack.windowed_levies.CM_peak"

#: The owner's attestation, as the site record carries it (D54).
ATTESTER = "Olly (site owner), 2026-08-17"
ATTESTATION_REFERENCE = "OWNER-ATTESTATION-D54-2026-08-17"


# --- records built here, never loaded from a run directory -----------------------------------


def _optimistic_record() -> DerivedAssumption:
    """The CM peak rate: a cited range with the favourable end taken, and it says so itself."""
    return DerivedAssumption(
        kind="derived_input",
        key=CM_KEY,
        missing_input="the Capacity Market supplier obligation this site pays through its supply "
        "contract: the p/kWh rate and the window it is charged on",
        value=0.30581,
        unit="gbp_per_kwh",
        basis=(
            "RATE 30.581 p/kWh. npower's monthly table runs January 2021 to July 2026 and spans "
            "5.313 to 31.382 p/kWh. Two 2026/27 months are published, 30.581 (April-May) and "
            "31.382 (June-July). DIRECTION OF EFFECT: UP. This record RAISES modelled avoided "
            "cost and therefore RAISES the modelled margin, so it is the number in this stack "
            "that needs the most scrutiny. Historic reconciliations have moved an initial rate by "
            "up to about 10% in both directions (April 2025 initial 17.057 against reconciliation "
            "17.738 p/kWh)."
        ),
        sources=[
            "npower Business Solutions, 'Third Party and Industry Charges', Capacity Mechanism "
            "section, 'Capacity Mechanism Peak Rate - Initial Billing Rate', delivery months "
            "April and May 2026 = 30.581 p/kWh (retrieved 2026-08-17)"
        ],
        derivation_method="sourced_default",
        derived_by="docs/bess_constants_register.md, transcribed from the npower table",
        derived_on_release="gb_v1_11",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask=(
            "One recent electricity invoice for this MPAN showing its Capacity Mechanism line, and "
            "whether the contract PASSES THROUGH that cost with reconciliation or FIXES it into "
            "the unit rate."
        ),
    )


def _plain_derived_record() -> DerivedAssumption:
    """A derived input that declares no direction: the page must not invent one for it."""
    return DerivedAssumption(
        kind="derived_input",
        key="tariff_stack.duos_red_rate",
        missing_input="this site's own licence-area network charge basis",
        value=0.05471,
        unit="gbp_per_kwh",
        basis="Priced on the site's own licence area, SSEN Southern (SEPD), evidenced from the "
        "MPAN core distributor id. TCR band is not establishable and band_4 is a CHOICE.",
        sources=["SSEN, 'Use of System Charging Statement 2026-27' (retrieved 2026-08-17)"],
        derivation_method="sourced_default",
        derived_by="gridq.ingestion.tariff_resolution",
        derived_on_release="gb_v1_11",
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask="Send one full electricity invoice for this site showing the DUoS lines "
        "and the TCR band.",
    )


def _permission_record() -> DerivedAssumption:
    """A permission. Carries no value, and may never be readable as consent granted."""
    return DerivedAssumption(
        kind="pending_permission",
        key="local_flex_awards.reference",
        missing_input="the DNO/DSO contract or trade reference for the local-flexibility award "
        "the owner attests this site holds",
        basis="OWNER ATTESTATION, ON GOOD AUTHORITY (D54). NOT verified against any DNO register, "
        "Piclo feed or CMZ list. THIS SITE SITS IN NO TENDERED ZONE.",
        sources=[],
        derivation_method="not_derivable",
        derived_by="gridq.value_stack.gb_streams",
        derived_on_release="gb_v1_11",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="Supply the awarded contract or trade reference, the awarded volume and "
        "the service windows as contracted.",
    )


def _not_applicable_record() -> DerivedAssumption:
    """An evidenced absence. Must never be reported as 'assumed, confirmation required'."""
    return DerivedAssumption(
        kind="not_applicable",
        key="tariff_stack.duos_green_rate",
        missing_input="a published green-band DUoS unit rate for this connection",
        basis="PRIVATE WIRE. This site takes supply behind a landlord's meter and faces no "
        "distribution use-of-system charge at all, so there is no rate to find.",
        sources=["Landlord supply agreement, clause 4 (site file, 2026-08-14)"],
        derivation_method="not_derivable",
        derived_by="gridq.ingestion.tariff_resolution",
        derived_on_release="gb_v1_11",
        confidence_ceiling=ConfidenceBand.MEDIUM,
        confirmation_ask="Confirm the site takes supply behind a landlord meter and receives no "
        "DUoS-bearing invoice of its own.",
    )


def _gate_record() -> DerivedAssumption:
    """A gate-emitted record: it reaches the quote as a warning and NEVER touches the site record.

    This is the row the page would silently lose if it read the site model alone, and the reason the
    page parses the run's own disclosures as well.
    """
    return DerivedAssumption(
        kind="derived_input",
        key="tariff_stack.delivered_cost_reconciliation",
        missing_input="an invoice for this site: its modelled delivered cost has never been "
        "compared to a real bill",
        basis="NO INVOICE EVIDENCE EXISTS for this site, so its modelled delivered cost has never "
        "been compared to anything. The direction of any error here is unknown.",
        sources=[],
        derivation_method="not_derivable",
        derived_by="gridq.ingestion.bill_reconciliation",
        derived_on_release="gb_v1_11",
        confidence_ceiling=ConfidenceBand.LOW,
        confirmation_ask="Supply any invoice for this site showing delivered cost for a known "
        "period.",
    )


def _attestation() -> AvailabilityCommitment:
    """An award the OWNER asserts. Nobody checked a register, and the page must say so."""
    return AvailabilityCommitment(
        commitment_id="site_test_local_flex_award_2026",
        stream=MarketStream.LOCAL_FLEX_PICLO,
        committed_power_kw=213.81,
        windows=[
            AvailabilityWindow(
                label="Wallingford VAOU (WA) service window (published)",
                months=[12, 1, 2],
                start_time="17:00:00",
                end_time="19:00:00",
            )
        ],
        tenor_years=1.0,
        counterparty="Scottish and Southern Electricity Networks (SEPD). NO ZONE OF ITS OWN; "
        "terms borrowed from DONOR ZONE 'Wallingford' (SEPD).",
        reference=ATTESTATION_REFERENCE,
        confirmed_by=ATTESTER,
        basis="OWNER ATTESTATION, ON GOOD AUTHORITY (D54). NOT verified against any DNO register, "
        "Piclo feed or CMZ list.",
    )


def _site(*, populated: bool = True) -> SiteModel:
    """A site record with every assumption class on it, or a bare one with none."""
    return SiteModel(
        site_id="site_test",
        site_name="Assumptions Page Test Site",
        postcode="OX11 0QX",
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        load_data=LoadDataSummary(
            annual_kwh=1_200_000.0,
            half_hourly_rows=17520,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            max_import_kw=213.81,
        ),
        annual_consumption_kwh=1_200_000.0,
        tariff_stack=TariffStack(
            basis=SYNTHETIC_BASIS,
            provenance=SYNTHETIC_PROVENANCE,
            supplier="X",
            import_rates={"single": 0.24},
            levies={"ccl": 0.01},
        ),
        connection=ConnectionDetails(mic_kw=213.81),
        consents=ConsentState(),
        physical_gates=PhysicalGateStatus(footprint_available=True, access_ok=True),
        counterparty=Counterparty(
            legal_name="Assumptions Page Test Ltd",
            company_number="09999999",
            sector="manufacturing",
            credit_status=CreditStatus.ADEQUATE,
            expected_tenure_years=10.0,
        ),
        derived_assumptions=(
            [
                _optimistic_record(),
                _plain_derived_record(),
                _permission_record(),
                _not_applicable_record(),
            ]
            if populated
            else None
        ),
        local_flex_awards=[_attestation()] if populated else None,
    )


def _disclosure(record: DerivedAssumption) -> str:
    """The pipeline's own warning for a record, built from the schema's own statement."""
    return f"{WARNING_PREFIX} [{record.report_label}] {EM_DASH} {record.statement()}"


def _quote(*, warnings: list[str] | None = None) -> QuoteRecord:
    """A no-spec draft record: no `optimisation_result`, no `financial_case`.

    The shape the live gb_v1_11 campaign runs are actually in (the sizing search selected nothing),
    and therefore the shape the page has to be correct on. A page that only works once a system is
    specified is a page that does not work.
    """
    return QuoteRecord(
        quote_id="quote_test_assumptions",
        quote_mode=QuoteMode.DRAFT,
        site_id="site_test",
        data_tier=DataTier.T2_VALIDATED_12_MONTH_HALF_HOURLY,
        assumption_version="gb_v1_11",
        warnings=[
            "no viable configuration: 30 candidate system(s) were solved and NONE WAS SELECTED.",
            *(warnings if warnings is not None else [_disclosure(_gate_record())]),
        ],
        status_history=[],
    )


def _packs(quote: QuoteRecord, site: SiteModel | None) -> dict[str, str]:
    """All four artifacts, rendered end to end from the record. The only source of assertions."""
    arts = generate_artifacts(quote, site, assumptions=load_assumptions("gb_v1_11"))
    return {
        "memo.md": arts.memo_markdown,
        "proposal.md": arts.proposal_markdown,
        "report.html": arts.report_html,
        "consulting_report.html": arts.consulting_report_html,
    }


@pytest.fixture
def packs() -> dict[str, str]:
    return _packs(_quote(), _site())


# --- the reachability floor: one assertion per pack, so a missing wire names itself -----------


def test_the_page_reaches_the_memo() -> None:
    assert fmt.ASSUMPTIONS_PAGE_TITLE in _packs(_quote(), _site())["memo.md"]


def test_the_page_reaches_the_customer_proposal() -> None:
    assert fmt.ASSUMPTIONS_PAGE_TITLE in _packs(_quote(), _site())["proposal.md"]


def test_the_page_reaches_the_html_report() -> None:
    assert fmt.ASSUMPTIONS_PAGE_TITLE in _packs(_quote(), _site())["report.html"]


def test_the_page_reaches_the_consulting_report() -> None:
    assert fmt.ASSUMPTIONS_PAGE_TITLE in _packs(_quote(), _site())["consulting_report.html"]


def test_the_page_starts_a_fresh_page_in_every_pack(packs) -> None:
    """The owner asked for a FRESH PAGE, so the break is part of the deliverable."""
    for name in ("memo.md", "proposal.md"):
        assert "break-before: page" in packs[name], name
    assert "section.card.freshpage{break-before:page" in packs["report.html"]
    assert 'class="card freshpage"' in packs["report.html"]
    assert "section.sec.freshpage{break-before:page" in packs["consulting_report.html"]
    assert 'class="sec freshpage"' in packs["consulting_report.html"]


# --- the classes are all present, and none is merged into another -----------------------------


def test_every_class_is_enumerated_in_every_pack(packs) -> None:
    """Five classes, five headings, in all four artifacts. A merged class is a defect."""
    for name, text in packs.items():
        assert "Optimistic selections (D56)" in text, name
        assert "Owner attestations and asserted commitments (D54, D55)" in text, name
        assert "Derived inputs (D45)" in text, name
        assert "Pending permissions" in text, name
        assert "Evidenced non-applicable components" in text, name
        assert fmt.ASSUMPTIONS_LIBRARY_TITLE in text, name


def test_each_class_keeps_its_own_report_language(packs) -> None:
    """The three `DerivedAssumption` classes never share a sentence, and neither do their rows."""
    for name, text in packs.items():
        assert "assumed, confirmation required" in text, name
        assert "requires customer consent" in text, name
        assert "does not apply to this site, evidenced" in text, name
        assert "asserted, not verified" in text, name


def test_a_permission_is_never_written_as_granted(packs) -> None:
    """A `pending_permission` carries no value and reads as an ask, never as a consent."""
    for name, text in packs.items():
        assert "no value used: a permission is not a value" in text, name
        assert "NONE OF THESE IS GRANTED" in text, name
        assert _permission_record().confirmation_ask in text, name


def test_the_owner_attestation_is_attributed_and_is_never_a_register_match(packs) -> None:
    """D54: the asserter travels with the assertion, and the page never claims a register check."""
    for name, text in packs.items():
        assert ATTESTER in text, name
        assert "Asserted by" in text, name
        assert "NOT verified against any DNO register, Piclo feed or CMZ list" in text, name
        assert ATTESTATION_REFERENCE in text, name


def test_the_optimistic_pick_shows_the_value_the_range_and_the_comparator(packs) -> None:
    """D56 limb 1's worked example: the choice is only visible if all three are on the page."""
    for name, text in packs.items():
        assert CM_VALUE in text, name
        assert CM_RANGE in text, name
        assert CM_COMPARATOR in text, name


def test_the_gate_emitted_record_is_on_the_page_although_the_site_never_carries_it(packs) -> None:
    """The row that exists only in the run's warnings. Reading the site model alone loses it."""
    for name, text in packs.items():
        assert "tariff_stack.delivered_cost_reconciliation" in text, name
        assert _gate_record().confirmation_ask in text, name


def test_every_row_carries_a_direction_of_effect_and_an_exact_ask(packs) -> None:
    """The two columns that make the page worth printing, on every row, in every pack."""
    page = build_view_model(
        _quote(), _site(), assumptions=load_assumptions("gb_v1_11")
    ).assumptions_page
    rows = [row for block in page.blocks for row in block.rows]
    assert len(rows) == 6, [row.key for row in rows]
    for row in rows:
        assert row.direction in {"up", "down", "neutral", "not established"}, row
        assert row.direction_basis
        assert row.confirmation_ask
        for name, text in packs.items():
            assert row.key in text, (name, row.key)
            assert row.confirmation_ask in text, (name, row.key)


def test_a_record_that_declares_no_direction_is_not_assigned_one_by_guesswork() -> None:
    """The gate record's own basis says the direction is unknown, so the page must not call it."""
    page = build_view_model(
        _quote(), _site(), assumptions=load_assumptions("gb_v1_11")
    ).assumptions_page
    by_key = {row.key: row for block in page.blocks for row in block.rows}
    assert by_key["tariff_stack.delivered_cost_reconciliation"].direction == "not established"
    assert by_key[CM_KEY].direction == "up"
    assert "stated on the record" in by_key[CM_KEY].direction_basis


def test_only_a_record_stated_direction_puts_a_row_in_the_optimistic_block() -> None:
    """`duos_red_rate` raises revenue but its basis claims no range, so it is not an optimism."""
    page = build_view_model(
        _quote(), _site(), assumptions=load_assumptions("gb_v1_11")
    ).assumptions_page
    blocks = {block.title: [row.key for row in block.rows] for block in page.blocks}
    assert blocks["Optimistic selections (D56)"] == [CM_KEY]
    assert (
        "tariff_stack.duos_red_rate"
        in blocks["Derived inputs (D45): facts about the site the model derived"]
    )


# --- the empty cases, which are where a disclosure page usually goes quiet --------------------


def test_an_empty_class_renders_none_recorded_and_never_a_missing_section() -> None:
    """ "No pending permission on this run" and "nobody looked" are different facts."""
    packs = _packs(_quote(warnings=[]), _site(populated=False))
    for name, text in packs.items():
        assert fmt.ASSUMPTIONS_PAGE_TITLE in text, name
        assert "Optimistic selections (D56)" in text, name
        assert "Pending permissions" in text, name
        assert "Evidenced non-applicable components" in text, name
        assert "None recorded" in text, name
        assert "the site record carries no asserted commitment" in text, name
        assert "this run rests on no pending permission" in text, name


def test_no_site_record_says_so_rather_than_showing_an_empty_register() -> None:
    """Rendered without a site: it states what it could not see, and keeps the rows it has."""
    packs = _packs(_quote(), None)
    for name, text in packs.items():
        assert "NO SITE RECORD WAS SUPPLIED" in text, name
        # The gate-emitted row survives, because it lives in the warnings, not on the site.
        assert "tariff_stack.delivered_cost_reconciliation" in text, name
        # And the site-only classes are honestly empty rather than absent.
        assert "None recorded" in text, name


def test_a_record_with_no_assumptions_at_all_states_the_underwriting_basis() -> None:
    packs = _packs(_quote(warnings=[]), _site(populated=False))
    for name, text in packs.items():
        assert "UNDERWRITING BASIS." in text, name


# --- which basis produced the pack (D46/D55/D56) ----------------------------------------------


def test_a_study_basis_run_reproduces_the_records_own_words(packs) -> None:
    note = (
        "STUDY BASIS (D55): NOT AN UNDERWRITING OUTPUT. Every stream is priced INTO the priced "
        "floor on the all_streams_enabled_study_v1 basis."
    )
    study = _packs(_quote(warnings=[note, _disclosure(_gate_record())]), _site())
    for name, text in study.items():
        assert "STUDY BASIS: NOT AN UNDERWRITING OUTPUT." in text, name
        assert "all_streams_enabled_study_v1" in text, name
    # And without that label the page says so, rather than asserting a basis the record lacks.
    for name, text in packs.items():
        assert "No named study basis is stamped on this record" in text, name
        assert "It must not be presented as an underwriting output" in text, name


def test_the_unqualified_run_is_still_flagged_as_not_an_underwriting_output(packs) -> None:
    """A run resting on an attestation and an optimistic pick is feasibility work, labelled."""
    for name, text in packs.items():
        assert "NOT AN UNDERWRITING OUTPUT." in text, name
        assert "1 asserted commitment(s) and 1 optimistic selection(s)" in text, name


# --- house style and the contracts this page depends on ---------------------------------------


def test_the_page_carries_no_em_or_en_dash_in_any_pack(packs) -> None:
    """Zero em/en dashes in anything that reaches a pack, asserted on the page's own slice.

    Sliced rather than whole-document: the memo and the HTML report deliberately reproduce the
    record's warnings byte-for-byte elsewhere (`underwriting.py`, `html_report.py`), and that
    contract is not this page's to break.
    """
    for name, text in packs.items():
        page = text[text.index(fmt.ASSUMPTIONS_PAGE_TITLE) :]
        assert EM_DASH not in page, name
        assert EN_DASH not in page, name


def test_report_labels_match_the_schema() -> None:
    """The page groups by `report_label`, so a schema wording change must fail here, not silently.

    A contract with `gridq.schemas.derived`, which is the authority on the sentence each class gets.
    """
    from gridq.outputs.viewmodel import _KIND_BY_LABEL

    for kind, key in (
        ("derived_input", "tariff_stack.duos_red_rate"),
        ("pending_permission", "stream_consents.CAPACITY_MARKET"),
        ("not_applicable", "tariff_stack.duos_green_rate"),
    ):
        record = DerivedAssumption(
            kind=kind,  # type: ignore[arg-type]
            key=key,
            missing_input="x",
            value=0.05 if kind == "derived_input" else None,
            unit="gbp_per_kwh" if kind == "derived_input" else None,
            basis="y",
            derivation_method="sourced_default" if kind == "derived_input" else "not_derivable",
            derived_by="test",
            confidence_ceiling=ConfidenceBand.LOW,
            confirmation_ask="z",
        )
        assert _KIND_BY_LABEL[record.report_label] == kind


def test_the_statement_format_this_page_parses_is_the_schemas_own() -> None:
    """The warning parser is tested against a statement the SCHEMA built, not a hand-typed one."""
    from gridq.outputs.viewmodel import _parse_assumption_warning

    record = _gate_record()
    block, row = _parse_assumption_warning(_disclosure(record))
    assert block == "derived_input"
    assert row.key == record.key
    assert row.confirmation_ask == record.confirmation_ask
    assert not row.verbatim, "a parseable disclosure must not fall back to the verbatim cell"
    assert "LOW" in row.status


def test_an_unparseable_disclosure_is_still_rendered_in_full_and_never_dropped() -> None:
    """A row on this page may be ugly. It may never be missing."""
    from gridq.outputs.viewmodel import _parse_assumption_warning

    broken = f"{WARNING_PREFIX} [assumed, confirmation required] {EM_DASH} connection.mic_kw: the "
    block, row = _parse_assumption_warning(broken)
    assert block == "derived_input"
    assert row.verbatim == broken
    packs = _packs(_quote(warnings=[broken]), _site(populated=False))
    for name, text in packs.items():
        assert "connection.mic_kw" in text, name
        assert "could not be split into columns" in text, name


def test_the_published_range_is_transcribed_from_the_real_site_record() -> None:
    """The drift guard on the one authored string on the page.

    `_PUBLISHED_RANGES` transcribes a range out of the record's own basis text so it can be printed
    beside the value. `examples/real/sites/*.json` is checked in, so this is a real comparison and
    not a fixture agreeing with itself: if the record's range changes, this fails.
    """
    from gridq.outputs.viewmodel import _PUBLISHED_RANGES

    # Quarantined until the customer's DPA is registered (WP-GRC-05): skips, naming why, until then.
    record_path = dpa_released(REAL_SITES / "harwell.json")
    record = next(
        r
        for r in json.loads(record_path.read_text(encoding="utf-8"))["derived_assumptions"]
        if r["key"] == CM_KEY
    )
    published, comparator = _PUBLISHED_RANGES[CM_KEY]
    assert CM_RANGE in published
    assert "5.313 to 31.382 p/kWh" in record["basis"]
    assert "17.057" in comparator
    assert "17.057" in record["basis"]
    assert str(record["value"]) == CM_VALUE


def test_the_page_renders_from_a_real_campaign_site_record() -> None:
    """The live records, with the run built here: every class, on a site nobody wrote for a test."""
    # Through the guarded loader: skips, naming why, until the customer's DPA is registered.
    site = dpa_released_site(REAL_SITES / "harwell.json", run_cross_checks=False)
    quote = _quote().model_copy(update={"site_id": site.site_id})
    packs = _packs(quote, site)
    for name, text in packs.items():
        assert fmt.ASSUMPTIONS_PAGE_TITLE in text, name
        assert CM_VALUE in text, name
        assert CM_RANGE in text, name
        assert "requires customer consent" in text, name
        assert "OWNER-ATTESTATION-D54-2026-08-17" in text, name


# --- D57: the assumed approvals the ENGINE wrote onto the streams reach the page ---------------


def _d57_result() -> OptimisationResult:
    """An `OptimisationResult` whose stream warnings carry D57 assumed-approval statements.

    Shaped like the real thing: one waiver repeated across two streams (the stacking waiver is), and
    one statement on a stream in ``gated_upside`` rather than ``value_streams``, because both lists
    are places the engine writes these and a page that reads only the first is a page that drops
    half of them.
    """
    waiver = (
        "DFS STACKING EXCLUSIVITY IS ASSUMED WAIVED (D57 limb 3), NOT CHECKED. Procurement Rules "
        "4.7.6 bars a DFS Unit Meter Point from forming part of a BM Unit."
    )
    return OptimisationResult(
        request_id="req_d57",
        result_id="res_d57",
        selected_power_kw=100.0,
        selected_energy_kwh=200.0,
        duration_hours=2.0,
        solver_status=SolverStatus.OPTIMAL,
        bankable_status=BankableStatus.NOT_BANKABLE,
        implementation_level=ImplementationLevel.PRODUCTION,
        value_streams=[
            ValueStreamResult(
                stream=MarketStream.DFS,
                annual_revenue_gbp=1_000.0,
                firmness=Firmness.CONTRACTED,
                gate_status="open",
                warnings=[
                    "DFS IS PRICED ON D57 ASSUMPTIONS, NOT ON A HELD REGISTRATION.",
                    waiver,
                    "an ordinary stream note carrying no assumed-approval token at all,"
                    " which must not become a row",
                ],
            ),
            ValueStreamResult(
                stream=MarketStream.BALANCING_MECHANISM,
                annual_revenue_gbp=500.0,
                firmness=Firmness.CONTRACTED,
                gate_status="open",
                warnings=[waiver],
            ),
        ],
        gated_upside=[
            ValueStreamResult(
                stream=MarketStream.WHOLESALE_P415,
                firmness=Firmness.CONDITIONAL,
                gate_status="conditional",
                warnings=["CAPACITY MARKET GATE OPENED ON A D57 ASSUMPTION, NOT ON A CHECK."],
            )
        ],
    )


def test_the_engines_d57_approvals_reach_the_assumptions_page_in_every_pack() -> None:
    """D57 limb 4: every assumed approval is STATED on the dedicated assumptions page.

    **The gap this closes.** The page read the site record's `DerivedAssumption` list and
    ``quote.warnings`` only. Every D57 approval the engine emits is written onto the
    `ValueStreamResult` it applies to - the stacking waiver, the DFS registration assumption, the
    Capacity Market commitment assumption, the agreed-capacity attestation - so NONE of them reached
    the page, and limb 4 is the mechanism that makes limbs 1 to 3 honest: the optimism lives in the
    value and the candour lives in the label, and a reader has to see both in one place.

    It dies if `viewmodel._d57_rows` stops being called, if it stops reading ``gated_upside``, or if
    the block is dropped from ``_BLOCK_ORDER``.
    """
    quote = _quote().model_copy(update={"optimisation_result": _d57_result()})
    for name, pack in _packs(quote, _site()).items():
        assert "Assumed-approved contracts, consents, awards and thresholds (D57)" in pack, name
        # The engine's own words, from BOTH lists.
        assert "DFS IS PRICED ON D57 ASSUMPTIONS" in pack, name
        assert "CAPACITY MARKET GATE OPENED ON A D57 ASSUMPTION" in pack, name
        assert "STACKING EXCLUSIVITY IS ASSUMED WAIVED" in pack, name
        # Assumed, never granted (D45).
        assert "never granted" in pack, name


def test_a_repeated_waiver_renders_once_naming_every_stream_that_carries_it() -> None:
    """Deterministic de-duplication: one sentence, one row, every stream named on it."""
    quote = _quote().model_copy(update={"optimisation_result": _d57_result()})
    view = build_view_model(quote, _site(), assumptions=load_assumptions("gb_v1_11"))
    block = next(b for b in view.assumptions_page.blocks if "(D57)" in b.title)
    waiver_rows = [r for r in block.rows if "STACKING EXCLUSIVITY" in r.verbatim]
    assert len(waiver_rows) == 1
    assert waiver_rows[0].key == "BALANCING_MECHANISM, DFS"
    assert waiver_rows[0].direction == "up"
    # A stream warning with no D57 token in it is not a row.
    assert not [r for r in block.rows if "an ordinary stream note" in r.verbatim]


def test_a_run_with_no_d57_assumption_says_so_rather_than_rendering_an_empty_block() -> None:
    """An empty class is an explicit row, never a missing section."""
    for name, pack in _packs(_quote(), _site()).items():
        assert "no stream on this run rests on a D57 assumed approval" in pack, name
