"""WP-OPS-11 criteria 2 and 4: the worked count on the candidate sites, and the pack's draft text.

Criterion 2 (D145). For each candidate site and each definition the worked count gives
an upper bound in kW computed only from evidenced dossier fields, or "unknown" naming each
missing field and its asks, or "not in the dossier" naming the inputs that will supply it. A
property test evaluates every admissible filling of A on synthetic series that respect the
bound's stated conditions and checks none exceeds it. With no customer data released, the real
register is unknown throughout, and its per-site output is written only where git ignores it.

Criterion 4 (D146). The pack and its draft recommend nothing and record nothing as
agreed: the draft opens and closes with its banner, renders the three options in fixed order with
identical clause structure from the typed definitions, and leaves every term of the deal blank.

Every site here is SYNTHETIC- and every document a TEST FIXTURE, except the real-register test,
which reads the real register through the DPA guard with nothing released.
"""

from __future__ import annotations

import itertools
import json
import random
import re
import subprocess
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from tests.ops._dossier_support import (
    AS_OF,
    SYN,
    SYN_B,
    entry,
    evidence,
    full_synthetic_inputs,
    ic1,
    register,
    write_ic1,
    write_registry,
    write_transcription,
)
from tests.ops._mw_support import IVS, fill
from tests.ops._mw_support import env as mw_env

from gridq.contracts._base import Unknown
from gridq.contracts.connection import ConnectionAgreement
from gridq.contracts.evidence import evidence_ref
from gridq.governance.customer_data import CUSTOMERS, load_manifest
from gridq.governance.dpa_guard import EVIDENCE_DIR_ENV
from gridq.ops.demonstrator_mw import (
    PACK_PATH,
    NotInDossier,
    UnknownInputs,
    UpperBound,
    WorkedCount,
    build_worked_count,
    main,
    pack_worked_count,
    render_pack,
    render_worked_count,
    worked_count_json,
    write_worked_count,
)
from gridq.ops.dossier import DossierWriteRefused, build_dossier
from gridq.ops.dossier_inputs import default_register
from gridq.ops.dossier_model import AskHolder, CandidateSiteDossier
from gridq.ops.mw_bindings import ROADMAP_PIN
from gridq.ops.mw_definitions import DEFINITION_A, DEFINITIONS
from gridq.ops.mw_draft import (
    BLANKS,
    DRAFT_BANNER,
    DRAFT_TITLE,
    NOT_AGREED_STATEMENT,
    render_draft_agreement,
)
from gridq.ops.mw_formula import MemberEnv, Q, Reading, SiteEnv, evaluate, resolve
from gridq.ops.mw_grant_text import GRANT_TEXT_REGISTER

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def ev_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setenv(EVIDENCE_DIR_ENV, str(root))
    return root


def _asset(tech: str, rated: float | None, operation: str | None, desc: str) -> dict[str, Any]:
    return {
        "technology": tech,
        "rated_kw": rated
        if rated is not None
        else {"kind": "unknown", "reason": "TEST FIXTURE: not stated"},
        "operation": operation
        if operation is not None
        else {"kind": "unknown", "reason": "TEST FIXTURE: not stated"},
        "description": f"TEST FIXTURE {desc}",
    }


def _inputs(
    root: Path,
    site_id: str = SYN,
    *,
    agreements: tuple[ConnectionAgreement, ...] | None = None,
    archetype: str = "hybrid",
    assets: tuple[dict[str, Any], ...] = (),
) -> None:
    """Evidenced connection terms (IC-1) and on-site generation for one synthetic site."""
    ca = evidence(site_id, "connection_agreement", "dno", tag="ca")
    tech = evidence(site_id, "technical_report", "independent_engineer", tag="tech")
    write_registry(root, ca, tech)
    for a in agreements or (ic1(site_id, source=evidence_ref(ca), archetype=archetype),):
        write_ic1(root, a)
    write_transcription(
        root,
        site_id,
        "onsite_generation",
        {
            "kind": "onsite_generation",
            "statement": "assets_listed" if assets else "no_onsite_generation",
            "assets": list(assets),
        },
        evidence_ref(tech),
    )


def _count(root: Path, *site_ids: str) -> WorkedCount:
    d = build_dossier(
        register(*(entry(s) for s in site_ids or (SYN,))), as_of=AS_OF, evidence_root=root
    )
    return build_worked_count(d)


def _cells(wc: WorkedCount, site_id: str = SYN) -> dict[str, Any]:
    s = next(s for s in wc.sites if s.site_id == site_id)
    return {c.definition_id: c.cell for c in s.cells}


HYBRID_ASSETS = (
    _asset("combined_heat_and_power", 800.0, "parallel_with_grid", "CHP"),
    _asset("diesel_genset", 500.0, "standby_only", "standby genset"),
    _asset("battery_storage", 250.5, "parallel_with_grid", "battery"),
)

# =============================================================================================
# Criterion 2: bounds from evidenced fields only
# =============================================================================================


def test_a_firm_site_with_a_standby_genset_is_bounded_by_its_import_capacity(ev_root: Path) -> None:
    full_synthetic_inputs(ev_root)
    cells = _cells(_count(ev_root))
    a = cells["site_load_under_management"]
    assert isinstance(a, UpperBound)
    assert a.kw == 20000  # max(20 MW firm + 0 non-firm, 500 kW standby), nothing in parallel
    assert [(t.term, t.kw) for t in a.terms] == [
        ("firm_import_mw", 20000),
        ("non_firm_import_mw", 0),
        ("diesel_genset rated power (standby_only)", 500),
    ]
    assert all(t.evidence for t in a.terms)
    assert [e.contract_field for e in a.not_included] == [
        "IC-3 DispatchEnvelope.rated_discharge_kw"
    ]
    b = cells["bess_mw_actively_dispatched"]
    assert isinstance(b, NotInDossier) and b.external_inputs[0].roadmap_wps == ("WP-SIZE-08",)


def test_a_hybrid_site_adds_parallel_assets_and_rounds_each_term_up(ev_root: Path) -> None:
    _inputs(ev_root, assets=HYBRID_ASSETS)
    cells = _cells(_count(ev_root))
    a, b = cells["site_load_under_management"], cells["bess_mw_actively_dispatched"]
    assert isinstance(a, UpperBound) and isinstance(b, UpperBound)
    assert a.kw == 800 + 251 + max(30000, 500)
    assert a.rounding == "each_term_rounded_up_to_whole_kw"
    assert b.kw == 251 and b.scope == "recorded_batteries_only"
    assert [t.stated for t in b.terms] == ["250.5 kW"]


def test_with_nothing_on_site_a_is_the_import_capacity(ev_root: Path) -> None:
    _inputs(ev_root)
    cells = _cells(_count(ev_root))
    a = cells["site_load_under_management"]
    assert isinstance(a, UpperBound) and a.kw == 30000
    assert isinstance(cells["bess_mw_actively_dispatched"], NotInDossier)


def test_an_unrated_battery_or_unknown_operation_is_unknown_with_a_customer_ask(
    ev_root: Path,
) -> None:
    _inputs(
        ev_root,
        assets=(
            _asset("battery_storage", None, "parallel_with_grid", "unrated battery"),
            _asset("combined_heat_and_power", 300.0, None, "CHP of unknown operation"),
        ),
    )
    wc = _count(ev_root)
    cells = _cells(wc)
    a, b = cells["site_load_under_management"], cells["bess_mw_actively_dispatched"]
    assert isinstance(a, UnknownInputs) and isinstance(b, UnknownInputs)
    assert [m.field_path for m in a.missing] == [
        f"{SYN}/onsite_generation/assets/1/operation",
        f"{SYN}/onsite_generation/assets/0/rated_kw",
    ]
    assert [m.field_path for m in b.missing] == [f"{SYN}/onsite_generation/assets/0/rated_kw"]
    # One ask per missing term, minted once, site-scoped, held by the customer.
    assert b.missing[0].ask_ids == a.missing[1].ask_ids
    minted = {x.ask_id: x for x in wc.minted_asks}
    assert len(minted) == 2
    for x in minted.values():
        assert x.holder is AskHolder.CUSTOMER and x.scope == f"site:{SYN}"


def test_two_connection_agreements_mint_an_ask_for_which_governs(ev_root: Path) -> None:
    ca = evidence(SYN, "connection_agreement", "dno", tag="ca")
    two = (
        ic1(SYN, source=evidence_ref(ca), archetype="hybrid", ref="TEST-CA-01"),
        ic1(SYN, source=evidence_ref(ca), archetype="firm", ref="TEST-CA-02"),
    )
    _inputs(ev_root, agreements=two)
    wc = _count(ev_root)
    a = _cells(wc)["site_load_under_management"]
    assert isinstance(a, UnknownInputs)
    assert a.missing[0].field_path == f"{SYN}/connection_terms/agreements"
    (ask,) = wc.minted_asks
    assert "TEST-CA-01" in ask.request and "TEST-CA-02" in ask.request


def test_an_unknown_capacity_in_the_agreement_is_asked_of_the_network_operator(
    ev_root: Path,
) -> None:
    ca = evidence(SYN, "connection_agreement", "dno", tag="ca")
    data = json.loads(ic1(SYN, source=evidence_ref(ca), archetype="hybrid").model_dump_json())
    data["non_firm_import_mw"] = {"kind": "unknown", "reason": "TEST FIXTURE: not stated"}
    _inputs(ev_root, agreements=(ConnectionAgreement.model_validate_json(json.dumps(data)),))
    wc = _count(ev_root)
    a = _cells(wc)["site_load_under_management"]
    assert isinstance(a, UnknownInputs)
    assert a.missing[0].field_path.endswith("/agreement/non_firm_import_mw")
    assert wc.minted_asks[0].holder is AskHolder.NETWORK_OPERATOR


def test_an_unknown_dossier_field_carries_the_dossiers_own_asks(ev_root: Path) -> None:
    d = build_dossier(register(entry(SYN)), as_of=AS_OF, evidence_root=ev_root)
    wc = build_worked_count(d)
    site = d.sites[0]
    a = _cells(wc)["site_load_under_management"]
    assert isinstance(a, UnknownInputs)
    by_path = {m.field_path: m for m in a.missing}
    assert set(by_path) == {f"{SYN}/connection_terms", f"{SYN}/onsite_generation"}
    for name in ("connection_terms", "onsite_generation"):
        field = getattr(site, name)
        assert by_path[f"{SYN}/{name}"].ask_ids == tuple(sorted({x.ask_id for x in field.asks}))
    assert wc.minted_asks == ()


def test_c_is_never_in_the_dossier_and_names_its_four_inputs(ev_root: Path) -> None:
    full_synthetic_inputs(ev_root)
    c = _cells(_count(ev_root))["verified_flexible_mw"]
    assert isinstance(c, NotInDossier)
    assert [e.roadmap_wps for e in c.external_inputs] == [
        ("WP-TEL-01", "WP-TEL-02"),
        ("WP-LEDGER-04",),
        ("WP-THERM-03",),
        ("WP-LEDGER-07", "WP-LEDGER-08"),
    ]
    for e in c.external_inputs:
        for wp in e.roadmap_wps:
            assert wp in {w.id for w in ROADMAP_PIN.work_packages}


def test_the_register_ceiling_needs_every_site_bounded_and_no_dossier_ask_open(
    ev_root: Path,
) -> None:
    _inputs(ev_root, SYN, assets=HYBRID_ASSETS)
    _inputs(ev_root, SYN_B)
    wc = _count(ev_root, SYN, SYN_B)
    assert [t.upper_bound for t in wc.tallies] == [2, 1, 0]
    ceiling = wc.register_ceiling_a
    if wc.dossier_asks:
        # A dossier-level ask (no Chainergy partner registered) may mean sites are missing.
        assert isinstance(ceiling, Unknown) and "dossier-level ask" in ceiling.reason
    else:
        assert not isinstance(ceiling, Unknown) and ceiling.kw == 31051 + 30000


# ---------------------------------------------------------------------------------------------
# The bound holds for every admissible filling of A
# ---------------------------------------------------------------------------------------------

_N = 48


def _series(rng: random.Random, lo: int, hi: int, *, gaps: int = 0) -> list[Fraction | None]:
    out: list[Fraction | None] = [
        Fraction(rng.randint(lo, hi), rng.choice((1, 2, 3))) for _ in range(_N)
    ]
    for i in rng.sample(range(_N), gaps):
        out[i] = None
    return out


def _hybrid_env(seed: int) -> Any:
    """Series that keep the A bound's conditions: import within the agreement (10 + 20 MW), the
    CHP and battery within their ratings, and the standby genset running only while the site
    imports nothing."""
    rng = random.Random(seed)
    imp = _series(rng, 0, 30000, gaps=2)
    for i in rng.sample(range(_N), 4):
        imp[i] = Fraction(0)
    exp = _series(rng, 0, 400)
    chp = _series(rng, 0, 800)
    bess = [Fraction(rng.randint(-2505, 2505), 10) for _ in range(_N)]
    gen = [Fraction(rng.randint(0, 500)) if v == 0 else Fraction(0) for v in imp]
    quality = ["good"] * _N
    quality[rng.randrange(_N)] = "suspect"

    def rs(vals: list[Any], q: list[str] | None = None) -> tuple[Reading | None, ...]:
        return tuple(
            None if v is None else Reading(v, (q or quality)[i] if q is not None else "good")  # type: ignore[arg-type]
            for i, v in enumerate(vals)
        )

    authority = tuple(Reading(rng.random() > 0.1) for _ in range(_N))
    return mw_env(
        SiteEnv(
            site_id=SYN,
            terms={
                "control_signoff": True,
                "control_authority": authority,
                "envelope_in_force": tuple(Reading(True) for _ in range(_N)),
                "a.firm_import_mw": Fraction(10),
                "a.non_firm_import_mw": Fraction(20),
                "a.boundary_import_kw": rs(imp, quality),
                "a.boundary_export_kw": rs(exp),
            },
            assets=(
                MemberEnv(member_id="chp", terms={"a.asset_output_kw": rs(chp)}),
                MemberEnv(member_id="genset", terms={"a.asset_output_kw": rs(gen)}),
                MemberEnv(member_id="bess", terms={"a.asset_output_kw": rs(bess)}),
            ),
        ),
        intervals=tuple(IVS[0] + (IVS[1] - IVS[0]) * i for i in range(_N)),
    )


def _a_fillings() -> list[Any]:
    out = []
    for basis, stat, coin, gap, qual, auth, win in itertools.product(
        ("agreed_capacity", "boundary_import", "gross_load"),
        ("max", "mean", "min", "percentile"),
        ("coincident", "non_coincident"),
        ("min_coverage", "unknown_if_any_gap"),
        ("exclude_interval", "site_not_counted"),
        ("contributes_zero", "exclude_interval"),
        ("demonstrator_days_all_day", "demonstrator_days_restricted"),
    ):
        choices = {
            "a.basis": basis,
            "statistic": stat,
            "coincidence": coin,
            "gap_policy": gap,
            "quality_fail": qual,
            "authority_fail": auth,
            "window": win,
            "resolution": "s1800",
        }
        numbers: dict[str, Any] = {}
        if stat == "percentile":
            numbers["statistic.p"] = Fraction(19, 20)
        if gap == "min_coverage":
            numbers["gap_policy.min_coverage"] = Fraction(1, 2)
        if win == "demonstrator_days_restricted":
            choices["window.day_types"] = "weekdays"
            numbers |= {"window.start_minute": 960, "window.end_minute": 1140}
        out.append(fill(choices, numbers))
    return out


def test_no_admissible_filling_of_a_exceeds_the_site_bound(ev_root: Path) -> None:
    _inputs(ev_root, assets=HYBRID_ASSETS)
    a = _cells(_count(ev_root))["site_load_under_management"]
    assert isinstance(a, UpperBound)
    numbers = 0
    for seed in (1, 2):
        e = _hybrid_env(seed)
        for f in _a_fillings():
            out = evaluate(resolve(DEFINITION_A, f), e).result
            if isinstance(out, Q):
                numbers += 1
                assert out.value <= a.kw, (seed, f.choices, out)
    assert numbers > 100  # most fillings give a figure, so the check has teeth


# ---------------------------------------------------------------------------------------------
# Determinism, the real register, and where a worked count may be written
# ---------------------------------------------------------------------------------------------

#: Moves only when the worked count, the definitions, the dossier or these fixtures change.
#: Re-pinned at the WP-TEL-01 integration merge (was sha256:68d182b3...4da0433d12): the IC-14
#: re-binding moved the binding hashes, so `definitions_hash` moved; every tally, cell and ask is
#: byte-identical (compared field by field against the pre-merge worked count).
GOLDEN_WORKED_COUNT = "sha256:2fb489da26f879b72504f45cff9f8b1dc8d0e95b155cac5165598e50bd90cc32"


def test_the_worked_count_is_deterministic_and_the_golden_hash_holds(ev_root: Path) -> None:
    _inputs(ev_root, SYN, assets=HYBRID_ASSETS)
    full_synthetic_inputs(ev_root, SYN_B)
    a, b = _count(ev_root, SYN, SYN_B), _count(ev_root, SYN, SYN_B)
    assert worked_count_json(a) == worked_count_json(b)
    assert render_worked_count(a) == render_worked_count(b)
    assert a.content_hash() == GOLDEN_WORKED_COUNT
    assert a.content_hash() in render_worked_count(a)
    assert "null" not in worked_count_json(a)


def _real(ev_root: Path) -> tuple[CandidateSiteDossier, WorkedCount]:
    d = build_dossier(default_register(ev_root / "none.json"), as_of=AS_OF, evidence_root=ev_root)
    return d, build_worked_count(d)


def test_with_no_customer_data_released_every_real_cell_is_unknown_or_not_in_the_dossier(
    ev_root: Path,
) -> None:
    d, wc = _real(ev_root)
    assert len(wc.sites) == 8
    assert [(t.upper_bound, t.unknown_inputs, t.not_in_dossier) for t in wc.tallies] == [
        (0, 8, 0),
        (0, 8, 0),
        (0, 0, 8),
    ]
    dossier_asks = {
        x.ask_id
        for s in d.sites
        for f in (s.connection_terms, s.onsite_generation)
        for x in getattr(f, "asks", ())
    }
    for s in wc.sites:
        assert s.customer_data == "not_released"
        for c in s.cells[:2]:
            assert isinstance(c.cell, UnknownInputs)
            for m in c.cell.missing:
                assert m.field_path.startswith(f"{s.site_id}/")
                assert set(m.ask_ids) <= dossier_asks
    assert wc.minted_asks == ()
    assert isinstance(wc.register_ceiling_a, Unknown)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("dossiers/\n", encoding="utf-8")
    return repo


def test_a_real_site_worked_count_is_written_only_where_git_ignores_it(
    ev_root: Path, tmp_path: Path
) -> None:
    _, wc = _real(ev_root)
    repo = _git_repo(tmp_path)
    json_path, md_path = write_worked_count(wc, repo / "dossiers")
    assert json_path.read_text("utf-8") == worked_count_json(wc)
    assert md_path.read_text("utf-8") == render_worked_count(wc)
    with pytest.raises(DossierWriteRefused):
        write_worked_count(wc, repo / "tracked")
    assert not (repo / "tracked").exists()
    with pytest.raises(DossierWriteRefused):
        write_worked_count(wc, tmp_path / "not_a_repo")


def test_a_synthetic_worked_count_may_be_written_anywhere(ev_root: Path, tmp_path: Path) -> None:
    _inputs(ev_root)
    json_path, _ = write_worked_count(_count(ev_root), tmp_path / "anywhere")
    assert json_path.is_file()


def test_the_cli_refuses_a_tracked_path_and_prints_no_site(
    ev_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path)
    args = [
        "--as-of",
        "2026-11-26",
        "--evidence-dir",
        str(ev_root),
        "--register",
        str(ev_root / "none.json"),
    ]
    assert main([*args, "--out", str(repo / "tracked")]) == 3
    assert main([*args, "--out", str(repo / "dossiers")]) == 0
    out = capsys.readouterr().out
    assert "sites: 8" in out
    assert "site_load_under_management: upper_bound=0 unknown_inputs=8 not_in_dossier=0" in out
    for spec in CUSTOMERS.values():
        for site_id in spec["site_ids"]:
            assert site_id not in out


def test_the_worked_count_does_not_widen_the_read_waiver() -> None:
    waiver = load_manifest().waiver_modules
    assert len(waiver) == 12
    assert not any("ops" in Path(m).parts for m in waiver)


# =============================================================================================
# Criterion 4: no recommendation, nothing agreed, a marked draft
# =============================================================================================

_PREFERENCE = re.compile(
    r"\b(recommend\w*|prefer\w*|best|worst|winner|ranked|ranking|score\w*|verdict|shortlist\w*|"
    r"eligible|suitable|viable|approve\w*|select\w*|agreed|default\w*|adopt\w*|chosen|should|"
    r"signed)\b",
    re.IGNORECASE,
)

#: Fixed statements that deny agreement, and texts quoted verbatim from elsewhere.
_EXEMPT = (
    DRAFT_BANNER,
    NOT_AGREED_STATEMENT,
    DRAFT_TITLE,
    *(q.text for q in GRANT_TEXT_REGISTER.quotes),
    *(w.delivers for w in ROADMAP_PIN.work_packages),
    *(w.title for w in ROADMAP_PIN.work_packages),
)


def _hits(text: str) -> list[str]:
    for fixed in sorted(_EXEMPT, key=len, reverse=True):
        text = text.replace(fixed, " ")
    return _PREFERENCE.findall(text)


def test_no_product_states_a_preference_or_an_agreement(ev_root: Path, tmp_path: Path) -> None:
    _inputs(ev_root, assets=HYBRID_ASSETS)
    wc = _count(ev_root)
    texts = {
        "worked_count_json": worked_count_json(wc),
        "worked_count_md": render_worked_count(wc),
        "pack": render_pack(pack_worked_count(date(2026, 11, 26), tmp_path)),
        "draft": render_draft_agreement(),
    }
    for name, text in texts.items():
        assert _hits(text) == [], name


def test_the_language_walk_has_teeth() -> None:
    assert _hits("Option B is recommended and agreed.") == ["recommended", "agreed"]
    assert _hits(f"{NOT_AGREED_STATEMENT} the agreement states") == []


def test_the_models_carry_no_recommendation_field() -> None:
    from gridq.ops.demonstrator_mw import DefinitionCell, SiteWorkedCount, Tally

    for model in (WorkedCount, SiteWorkedCount, DefinitionCell, Tally, UpperBound):
        for f in model.model_fields:
            assert not re.search(r"recommend|prefer|rank|score|agreed|chosen|default", f), (
                model,
                f,
            )


def test_the_draft_opens_and_closes_with_its_banner_and_says_it_is_not_agreed() -> None:
    text = render_draft_agreement()
    lines = [x for x in text.splitlines() if x.strip()]
    assert lines[0] == DRAFT_BANNER and lines[-1] == DRAFT_BANNER
    assert f"**{NOT_AGREED_STATEMENT}**" in text
    assert "4.1 Option: [[option.letter]]." in text
    assert text.count("Signature status: unknown (unsigned draft)") == 5
    assert "status: candidate_not_agreed" in text


def test_every_term_of_the_deal_is_a_typed_blank() -> None:
    text = render_draft_agreement()
    used = set(re.findall(r"\[\[([a-z._]+)\]\]", text))
    assert used == {b.key for b in BLANKS}
    # Award-only figures are blanks, never values.
    assert "[[project.number]]" in text
    assert not re.search(r"£\s?[0-9]", text.replace("£10,000/MW/year", ""))


def test_the_options_render_in_fixed_order_with_identical_clause_structure() -> None:
    text = render_draft_agreement()
    heads = re.findall(r"^### Option ([ABC]):", text, re.MULTILINE)
    assert heads == ["A", "B", "C"]
    structures = []
    for letter in "ABC":
        clauses = re.findall(rf"^{letter}\.([0-9]) ([A-Z][a-z]+)", text, re.MULTILINE)
        structures.append(clauses)
    assert structures[0] == structures[1] == structures[2]
    assert [n for n, _ in structures[0]] == ["1", "2", "3", "4", "5"]
    with pytest.raises(ValueError, match="fixed letter order"):
        render_draft_agreement(tuple(reversed(DEFINITIONS)))


def test_the_draft_is_rendered_from_the_definitions() -> None:
    assert render_draft_agreement() == render_draft_agreement()
    for d in DEFINITIONS:
        assert d.content_hash() in render_draft_agreement()
    changed = DEFINITION_A.model_copy(update={"quantity": "TEST FIXTURE changed quantity"})
    text = render_draft_agreement((changed, *DEFINITIONS[1:]))
    assert "TEST FIXTURE changed quantity" in text
    assert text != render_draft_agreement()


def test_no_mw_module_creates_an_evidence_object() -> None:
    src = REPO / "src" / "gridq" / "ops"
    for name in (
        "demonstrator_mw.py",
        "mw_bindings.py",
        "mw_definitions.py",
        "mw_draft.py",
        "mw_formula.py",
        "mw_grant_text.py",
    ):
        code = (src / name).read_text("utf-8")
        assert "EvidenceObject(" not in code and "evidence_ref(" not in code, name


# =============================================================================================
# The pack: tracked, generated, and holding no site value
# =============================================================================================


def test_the_tracked_pack_is_the_render(tmp_path: Path) -> None:
    expected = render_pack(pack_worked_count(date(2026, 11, 26), tmp_path))
    assert (REPO / PACK_PATH).read_text("utf-8") == expected, (
        "regenerate: python -m gridq.ops.demonstrator_mw --as-of 2026-11-26 "
        f"--write-pack {PACK_PATH}"
    )


def test_the_pack_quotes_each_text_with_its_document_and_section(tmp_path: Path) -> None:
    pack = render_pack(pack_worked_count(date(2026, 11, 26), tmp_path))
    for q in GRANT_TEXT_REGISTER.quotes:
        doc = GRANT_TEXT_REGISTER.document(q.doc_id)
        row = next(x for x in pack.splitlines() if x.startswith(f"| `{q.quote_id}` |"))
        assert doc.title.replace("|", "\\|") in row and q.location in row
        assert f'"{q.text}"' in row
    for a in GRANT_TEXT_REGISTER.asks:
        assert a.ask_id[7:19] in pack
    assert pack.count("````markdown") == 1 and DRAFT_BANNER in pack


def test_the_pack_names_no_real_site(tmp_path: Path) -> None:
    pack = render_pack(pack_worked_count(date(2026, 11, 26), tmp_path))
    for spec in CUSTOMERS.values():
        for site_id in spec["site_ids"]:
            assert site_id not in pack
