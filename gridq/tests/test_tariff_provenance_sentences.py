"""WP-UW-07 acceptance 5 (R8): no rendered sentence calls a borrowed tariff the site's own.

The defect (2026-08-16 item R8; triage `docs/reviews/2026-11-16-findings-register-triage.md`): the
no-spec bill basis said "the site's own metered half-hourly load priced on its own tariff stack"
on every site, including Harwell, whose record says "Nothing in this stack is the site's own
price". It moved no £, but it is a false sentence to a reader. The register's own verdict was that
it could be conditioned honestly only once `TariffStack` carried a typed provenance; it now does
(D104), and `tariff_calendar.tariff_provenance_phrase` is the one producer allowed to say
"own tariff stack", and says it only when every component is the site's own.

These tests hold that from three sides: the phrase itself against every shipped site record; a
source scan proving no other string literal on any rendering path can say it; and the three
rendered carriers (the solved bill, the no-spec BEFORE-only bill, the consulting report) on a
borrowed stack and on an own one.
"""

from __future__ import annotations

import ast
import json
from html import escape, unescape
from pathlib import Path

import pytest
from tests._dpa_support import dpa_released
from tests._milp_support import multi_rate_site, write_hh_csv

from _tariff_support import SYNTHETIC_BASIS
from gridq.assumptions import load_assumptions
from gridq.dispatch import DispatchProblem
from gridq.governance.customer_data import load_manifest
from gridq.optimisation import MilpEngine
from gridq.optimisation.milp_solver import MilpDispatchSolver
from gridq.optimisation.tariff_calendar import tariff_provenance_phrase
from gridq.outputs.consulting_report import render_report_for_quote
from gridq.outputs.generate import generate_artifacts
from gridq.schemas import CustomerBill, SiteModel, TariffProvenance, TariffStack

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "gridq"

#: The claim R8 is about, and the producer allowed to make it.
OWN_CLAIM = "own tariff stack"
NOT_OWN = "not the site's own tariff"
_PRODUCER = ("optimisation/tariff_calendar.py", "tariff_provenance_phrase")

#: Harwell's record: a commodity rate by analogy, an assumed network and assumed levies.
BORROWED = TariffProvenance(commodity="by_analogy", network="assumed", levies="assumed")
#: Every component the site's own: its invoice, the DNO's row for its connection, levies invoiced.
OWN = TariffProvenance(commodity="own_invoice", network="published_for_site", levies="own_invoice")


def _public_carriers() -> list[Path]:
    """The public example records that carry a tariff stack (not customer data)."""
    paths = [
        *(_ROOT / "examples").glob("*.json"),
        _ROOT / "examples" / "demo_site" / "sitemodel.json",
    ]
    return sorted(p for p in paths if "tariff_stack" in p.read_text())


def _campaign_carriers() -> list[Path]:
    """Every campaign site record (WP-GRC-05 quarantined customer data), from the committed
    manifest, never by listing or opening the quarantined directory. Each is read only once the
    DPA guard releases it (`_dpa_support.dpa_released`); until then its case skips, naming its
    blocker."""
    return sorted(
        _ROOT / e.path
        for e in load_manifest().datasets
        if e.path.startswith("examples/real/") and e.kind == "site_facts"
    )


def _carriers() -> list[Path]:
    return [*_public_carriers(), *_campaign_carriers()]


def _read_carrier(path: Path) -> dict:
    """A public record directly; a campaign record only through the guard."""
    if path in _campaign_carriers():
        path = dpa_released(path)
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", _carriers(), ids=lambda p: p.stem)
def test_every_shipped_site_is_described_as_its_record_says(path: Path) -> None:
    raw = _read_carrier(path)
    stack = TariffStack.model_validate(
        raw.get("tariff_stack") or raw.get("site_model", {}).get("tariff_stack")
    )
    phrase = tariff_provenance_phrase(stack)
    if stack.provenance.is_own:
        assert OWN_CLAIM in phrase and NOT_OWN not in phrase
    else:
        assert OWN_CLAIM not in phrase, f"{path.name}: a borrowed stack described as its own"
        assert NOT_OWN in phrase


def test_harwell_is_never_called_its_own_tariff() -> None:
    """The register's named case: the record says nothing in the stack is the site's own."""
    # Harwell's record is quarantined customer data (WP-GRC-05): read only through the guard,
    # so this skips, naming its blocker, until the Harwell DPA is registered.
    released = dpa_released(_ROOT / "examples" / "real" / "sites" / "harwell.json")
    stack = TariffStack.model_validate(json.loads(released.read_text())["tariff_stack"])
    assert "Nothing in this stack is the site's own price" in (stack.source or "")
    phrase = tariff_provenance_phrase(stack)
    assert OWN_CLAIM not in phrase
    assert "commodity rates taken by analogy from another tariff" in phrase


def _non_docstring_strings(tree: ast.Module) -> list[tuple[ast.Constant, str | None]]:
    """Every str literal that is not a docstring, with the function it sits in."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found: list[tuple[ast.Constant, str | None]] = []

    def visit(node: ast.AST, func: str | None) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func = node.name
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.append((node, func))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, None)
    return found


def test_no_other_string_on_any_path_can_say_own_tariff_stack() -> None:
    """The structural guard: the claim has one producer, and it is conditioned on the record."""
    offenders: list[str] = []
    producer_hits = 0
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        for node, func in _non_docstring_strings(ast.parse(path.read_text())):
            if OWN_CLAIM not in node.value:
                continue
            if (rel, func) == _PRODUCER:
                producer_hits += 1
            else:
                offenders.append(f"{rel}:{node.lineno} in {func}: {node.value[:80]!r}")
    assert not offenders, "\n".join(offenders)
    assert producer_hits == 1, "the conditioned producer must still exist"


# ------------------------------------------------------------------ the rendered carriers


def _with_provenance(site: SiteModel, provenance: TariffProvenance) -> SiteModel:
    return site.model_copy(
        update={"tariff_stack": site.tariff_stack.model_copy(update={"provenance": provenance})}
    )


@pytest.fixture(scope="module")
def borrowed_site(tmp_path_factory: pytest.TempPathFactory) -> SiteModel:
    path = tmp_path_factory.mktemp("r8") / "hh.csv"
    rows, _ = write_hh_csv(path, days=7)
    site = multi_rate_site(path, rows=rows)
    assert site.tariff_stack.basis == SYNTHETIC_BASIS
    return _with_provenance(site, BORROWED)


def _solved_bill(site: SiteModel) -> CustomerBill:
    solver = MilpDispatchSolver()
    solver.solve(
        DispatchProblem(
            site_model=site,
            assumptions=load_assumptions("gb_v0_1"),
            power_kw=100.0,
            energy_kwh=200.0,
            duration_hours=2.0,
            constraints=[],
        )
    )
    bill = solver.last_customer_bill()
    assert bill is not None
    return bill


def test_the_solved_bill_says_whose_rates_it_priced(borrowed_site: SiteModel) -> None:
    bill = _solved_bill(borrowed_site)
    assert bill.basis.startswith(
        f"Tariff: the site is {tariff_provenance_phrase(borrowed_site.tariff_stack)}. "
    )
    assert OWN_CLAIM not in bill.basis and NOT_OWN in bill.basis


def test_the_before_only_bill_no_longer_asserts_its_own_tariff(borrowed_site: SiteModel) -> None:
    """The exact register sentence (engine `_before_only_customer_bill`, the no-spec path)."""
    bill, _warnings = MilpEngine()._before_only_customer_bill(
        borrowed_site, load_assumptions("gb_v0_1")
    )
    assert bill is not None and bill.annual_bill_after_gbp is None
    assert OWN_CLAIM not in bill.basis
    assert f"the site is {tariff_provenance_phrase(borrowed_site.tariff_stack)}." in bill.basis
    own, _ = MilpEngine()._before_only_customer_bill(
        _with_provenance(borrowed_site, OWN), load_assumptions("gb_v0_1")
    )
    assert own is not None and "priced on its own tariff stack" in own.basis


def _report(rich_quote, provenance: TariffProvenance) -> str:
    stack = rich_quote.site.tariff_stack.model_copy(
        update={"provenance": provenance, "duos_red_rate": 0.05}
    )
    site = rich_quote.site.model_copy(update={"tariff_stack": stack})
    html = render_report_for_quote(rich_quote.quote, site, assumptions=load_assumptions("gb_v0_1"))
    return html


def test_the_consulting_report_states_whose_tariff_from_the_record(rich_quote) -> None:
    borrowed = _report(rich_quote, BORROWED)
    assert OWN_CLAIM not in unescape(borrowed)
    assert escape(f"the site is priced on a stack that is {NOT_OWN}") in borrowed
    own = _report(rich_quote, OWN)
    assert escape("the site is priced on its own tariff stack") in own


def test_a_before_only_pack_never_calls_the_modelled_bill_the_sites_own_cost(rich_quote) -> None:
    bill = CustomerBill(
        annual_bill_before_gbp=100_000.0,
        annual_bill_after_gbp=None,
        annual_saving_gbp=None,
        energy_before_gbp=60_000.0,
        energy_after_gbp=None,
        duos_before_gbp=25_000.0,
        duos_after_gbp=None,
        levies_before_gbp=12_000.0,
        levies_after_gbp=None,
        standing_gbp=1_500.0,
        capacity_gbp=1_500.0,
        import_before_kwh=500_000.0,
        import_after_kwh=None,
        levy_rate_gbp_per_kwh=0.03,
        basis="BEFORE ONLY (test label).",
    )
    quote = rich_quote.quote.model_copy(update={"customer_bill": bill})
    arts = generate_artifacts(quote, rich_quote.site)
    for text in (
        arts.memo_markdown,
        arts.proposal_markdown,
        unescape(arts.report_html),
        unescape(arts.consulting_report_html),
    ):
        assert "site's own delivered cost" not in text
        assert OWN_CLAIM not in text
