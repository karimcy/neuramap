"""Render the HTML report view (M8) — one self-contained document from the view model.

This is the visual layer: a single, self-contained HTML page (inline CSS, no external assets) that
shows the underwriting pack and the customer-proposal essentials together, with a colour-coded
bankability banner.

**This module does no arithmetic.** It only reads already-extracted view-model values and turns
them into strings via `formatting`, assembling markup with f-strings and ``str.join`` (never
numeric ``+``/``-``/``*``/``/`` and never an aggregation like ``sum``/``round``). That invariant is
asserted by tests/test_outputs_no_arithmetic_in_html.py — every figure is computed upstream (in the
deterministic finance core) and merely formatted here. All free text is HTML-escaped.
"""

from __future__ import annotations

from html import escape

from gridq.outputs import formatting as fmt
from gridq.outputs import report_charts as charts
from gridq.outputs.prose import ProseDrafter, default_prose_drafter
from gridq.outputs.viewmodel import (
    BEST_CANDIDATE_CAVEAT,
    BEST_CANDIDATE_HEADING,
    BEST_CANDIDATE_HELD_OUT,
    BEST_CANDIDATE_IN_FLOOR,
    NO_CANDIDATE_COST_REASON,
    NO_FINANCIAL_CASE_REASON,
    AssumptionPageRow,
    BestCandidateStreamRow,
    BestCandidateView,
    ContractedMerchantSplit,
    CounterpartyView,
    DscrView,
    OutputViewModel,
    SensitivityRow,
    StreamAssumptionRow,
    StreamNote,
    StreamRow,
)
from gridq.schemas import DispatchProfile, SizingGrid

#: Self-contained report CSS, aligned to the GridQ design system (docs/design/ui-system.md):
#: Inter type, slate surfaces, brand-orange eyebrow headings, firm-green trust signal. Inlined (no
#: external assets) so the artifact renders identically offline / under a strict CSP / when printed.
_STYLE = """
:root{--ink:#1a1a2e;--body:#374151;--muted:#6b7280;--line:#e5e7eb;--line-soft:#f3f4f6;
--bg:#f8fafc;--card:#fff;--ok:#1f7a4d;--ok-bg:#ecfdf5;--warn:#b45309;--warn-bg:#fffbeb;--accent:#ff6b35;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--body);
font:15px/1.55 "Inter",system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
-webkit-font-smoothing:antialiased;}
.wrap{max-width:920px;margin:0 auto;padding:32px 20px 64px;}
header.rep{margin-bottom:8px;border-top:3px solid var(--accent);padding-top:18px}
header.rep .brand{display:flex;align-items:center;gap:8px;margin-bottom:14px}
header.rep .brandmark{width:26px;height:26px;border-radius:7px;background:var(--accent);color:#fff;
display:grid;place-items:center;font-weight:800;font-size:14px}
header.rep .brandname{font-weight:700;color:var(--ink);font-size:15px;letter-spacing:-.01em}
header.rep .brandname em{font-style:normal;font-weight:500;color:var(--muted);
font-size:12px;margin-left:4px}
header.rep h1{font-size:26px;margin:0 0 2px;letter-spacing:-.02em;color:var(--ink)}
header.rep .sub{color:var(--muted);font-size:13px}
.banner{border-radius:12px;padding:14px 16px;margin:18px 0;font-weight:600;
border:1px solid transparent}
.banner.ok{background:var(--ok-bg);color:var(--ok);border-color:#bbf7d0}
.banner.warn{background:var(--warn-bg);color:var(--warn);border-color:#fde68a}
.banner .status{display:block;font-weight:500;font-size:12px;margin-top:4px;opacity:.85}
section.card{background:var(--card);border:1px solid var(--line-soft);border-radius:12px;
padding:18px 20px;margin:16px 0;}
section.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);
margin:0 0 12px;border-bottom:1px solid var(--line);padding-bottom:8px;font-weight:700}
section.card h3{font-size:14px;margin:16px 0 6px;color:var(--ink)}
p.lead{color:var(--body);margin:.2em 0 1em}
p.note{color:var(--muted);font-size:13px;margin:.6em 0}
table{width:100%;border-collapse:collapse;margin:6px 0;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line-soft);vertical-align:top;
overflow-wrap:anywhere}
th{color:var(--muted);font-weight:600}
code{font-family:ui-monospace,Menlo,Consolas,"Liberation Mono",monospace;font-size:.92em;
overflow-wrap:anywhere;color:var(--ink)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.kv td:first-child{color:var(--muted);width:42%}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;
background:#f1f5f9;color:var(--muted)}
.unavail{color:var(--muted);font-style:italic}
.total{font-weight:600}
ol.streamnotes{margin:.4em 0 0;padding-left:0;list-style:none;font-size:13px;color:var(--body)}
ol.streamnotes li{border-top:1px solid var(--line-soft);padding:9px 0;line-height:1.5}
ol.streamnotes .notenum{display:inline-block;font-size:11px;font-weight:700;color:var(--accent);
text-transform:uppercase;letter-spacing:.06em;margin-right:6px}
ul.warn{margin:0;padding-left:18px}
ul.warn li{margin:3px 0;color:var(--body)}
.scroll{overflow-x:auto}
footer.rep{color:var(--muted);font-size:12px;margin-top:24px;text-align:center}
section.card.freshpage{break-before:page;page-break-before:always}
table.assum th{white-space:nowrap}
.dirflag{font-weight:700;text-transform:uppercase;font-size:11px;letter-spacing:.06em}
@media print{body{background:#fff}.wrap{padding-top:8px}
section.card{break-inside:avoid}section.card.freshpage{break-inside:auto}}
"""


def render_html_report(
    vm: OutputViewModel,
    *,
    drafter: ProseDrafter = default_prose_drafter,
    sizing_grid: SizingGrid | None = None,
    dispatch_profile: DispatchProfile | None = None,
) -> str:
    """Render the full self-contained HTML report from the view model.

    ``sizing_grid`` and ``dispatch_profile`` are the two persisted engine projections the view model
    does not expose. They are what the graphs are drawn from (D59); without them each figure states
    its own absence rather than being omitted, because "the search is not on this record" and "this
    page does not show it" are different facts.

    **The section order is the owner's** (D59, Olly, 2026-08-18): the IRR and the revenue breakdown
    lead, then the graphs, then the methodology, then the assumptions register, and the evidence
    cards that used to sit in the middle move down. The banner stays at the top because it is a
    safety label, not evidence.
    """
    has_floor = vm.split.floor_total_gbp > 0
    body = "\n".join(
        [
            _header(vm),
            _banner(vm, drafter),
            # 1. The IRR and what returns it, then revenue per stream and each stream's own basis.
            _returns_card(vm),
            _cashflows(vm),
            _dscr(vm.dscr, vm.sensitivities),
            _split(vm.split, drafter, vm.best_candidate),
            _stream_assumptions_card(vm),
            # 2. The graphs.
            _graphs_card(vm, sizing_grid, dispatch_profile),
            _system(vm),
            # Directly under the specification card, so "no viable configuration" and "here is what
            # the best size measured" cannot be read apart. Empty on a run that selected a system.
            _best_candidate(vm),
            # 3. The methodology.
            _methodology_card(vm),
            # 4. The assumptions register.
            _assumptions_page(vm),
            # 5. The evidence cards, moved down and not out.
            _summary(vm, drafter),
            _proposal_panel(vm, drafter, has_floor),
            _price_debt(vm),
            _counterparty(vm.counterparty),
            _consents(vm),
            _provenance(vm),
            _sources_card(vm),
            _warnings(vm),
            _footer(vm),
        ]
    )
    # Scrubbed of em and en dashes at the document seam, like the other three artifacts.
    #
    # **This reverses a prior decision, deliberately.** This page used to be exempt so that the
    # run's
    # warnings appeared byte-for-byte. The owner has now instructed three times that no report may
    # carry an em dash, and that instruction outranks a typographic exemption. `no_dashes` rewrites
    # nothing but the two dash characters, the RECORD is untouched, and every warning still
    # appears in
    # full and in the engine's own words; the verbatim-warning test compares the scrubbed form.
    return fmt.no_dashes(
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>GridQ report: {escape(vm.provenance.quote_id)}</title>\n"
        f"<style>{_STYLE}{charts.CHART_CSS}</style>\n</head>\n<body>\n"
        f'<div class="wrap">\n{body}\n</div>\n</body>\n</html>\n'
    )


# --- value helpers (None → labelled unavailable; never invents) ------------


def _opt(value: object, formatted: str) -> str:
    """Wrap ``formatted`` (already a string), or label the cell n/a when the value is None."""
    if value is None:
        return '<span class="unavail">n/a</span>'
    return escape(formatted)


def _unavail(reason: str) -> str:
    return f'<span class="unavail">{escape(fmt.unavailable(reason))}</span>'


# --- sections --------------------------------------------------------------


def _header(vm: OutputViewModel) -> str:
    return (
        '<header class="rep">\n'
        '<div class="brand"><span class="brandmark">G</span>'
        '<span class="brandname">GridQ <em>by Neura</em></span></div>\n'
        "<h1>Site report</h1>\n"
        f'<div class="sub">Quote {escape(vm.provenance.quote_id)} · '
        f"{escape(fmt.titleise(vm.quote_mode))} · {escape(vm.data_tier)}</div>\n"
        "</header>"
    )


#: See `underwriting._STUDY_BASIS_BANNER_BODY`: same fact, same prominence, this page's markup — and
#: basis-agnostic for the same reason. The basis's own label is prepended from the record at render
#: time, so a D55 all-streams run is never described in D46's export-only words.
_STUDY_BASIS_BANNER_BODY = (
    "This run was priced on a named feasibility-study basis, which prices revenue the "
    "underwriting basis treats as gated or conditional INTO the priced floor and therefore into "
    "P50, P90, the DSCR and both IRRs. The underwriting basis forbids exactly that, so no figure "
    "on this page may be presented as an underwriting result."
)


def _banner(vm: OutputViewModel, drafter: ProseDrafter) -> str:
    cls = "ok" if vm.bankable else "warn"
    caption = escape(drafter.banner_line(bankable=vm.bankable))
    status = escape(fmt.titleise(vm.bankable_status))
    banner = (
        f'<div class="banner {cls}">{caption}'
        f'<span class="status">Status on record: {status}</span></div>'
    )
    if vm.study_basis:
        # The label is scrubbed of em dashes at the point of use, as in `underwriting`: every
        # `StudyBasis.LABEL` joins its phrases with em dashes and this page is deliberately not
        # scrubbed whole (its warnings card reproduces record text verbatim).
        label = escape(fmt.no_dashes(vm.study_basis_label or ""))
        # Conditional, because on a record that names its basis by id alone there IS no verbatim
        # warning to point a reader at. Promising one that is not on the page is the same class of
        # false claim as dropping the banner altogether.
        if vm.study_basis_note is not None:
            pointer = (
                "The record's own statement of the basis, naming every stream it prices and "
                "every award it assumes, is reproduced verbatim in the warnings."
            )
        else:
            basis_id = vm.study_basis_id or "not recorded"
            pointer = (
                f"The record names the basis by its recorded basis id {basis_id} and carries no "
                "prose statement of it, so none is reproduced below."
            )
        return (
            f'<div class="banner warn">{label}: {escape(_STUDY_BASIS_BANNER_BODY)}'
            f'<span class="status">{escape(pointer)}</span></div>'
            f"{banner}"
        )
    return banner


def _summary(vm: OutputViewModel, drafter: ProseDrafter) -> str:
    text = escape(
        drafter.memo_summary(
            bankable=vm.bankable,
            quote_mode=vm.quote_mode,
            data_tier=vm.data_tier,
            has_warnings=bool(vm.warnings),
        )
    )
    return f'<section class="card"><h2>Summary</h2><p class="lead">{text}</p></section>'


def _system(vm: OutputViewModel) -> str:
    s = vm.spec
    if s.selection_status == "no_viable_configuration":
        reason = escape(
            s.selection_verdict_reason
            or "the run record states no reason for the verdict beyond the status itself."
        )
        # THAT no system was selected is a recorded outcome; WHY is a claim, and this card used to
        # make it ("none is economically viable on the priced value floor") on records carrying no
        # financial case and no computed return for any candidate. The consulting report was fixed
        # for the same sentence; this one had kept it. The outcome is stated, the reason attributed.
        return _card(
            "System specification",
            '<p class="lead"><strong>No system was selected.</strong> The sizing search ran over '
            "the candidate space and selected none. That is the recorded outcome; the reason "
            "below is the record's own, reproduced rather than restated, and this card asserts "
            "nothing beyond it.</p>"
            f'<p class="muted">{reason}</p>',
        )
    rows = [
        (
            "Power",
            _opt(s.selected_power_kw, fmt.kw(s.selected_power_kw))
            if s.selected_power_kw is not None
            else _unavail("not yet sized"),
        ),
        (
            "Energy",
            _opt(s.selected_energy_kwh, fmt.kwh(s.selected_energy_kwh))
            if s.selected_energy_kwh is not None
            else _unavail("not yet sized"),
        ),
        (
            "Duration",
            _opt(s.duration_hours, fmt.hours(s.duration_hours))
            if s.duration_hours is not None
            else _unavail("not yet sized"),
        ),
        (
            "Engine",
            escape(fmt.titleise(s.implementation_level))
            if s.implementation_level
            else _unavail("no result"),
        ),
        (
            "Solver",
            escape(fmt.titleise(s.solver_status)) if s.solver_status else _unavail("no result"),
        ),
    ]
    return _card("System specification", _kv(rows))


def _counted_in(row: BestCandidateStreamRow) -> str:
    """Which side of the candidate's floor a stream sits on, in the shared words."""
    return BEST_CANDIDATE_IN_FLOOR if row.in_priced_floor else BEST_CANDIDATE_HELD_OUT


def _best_candidate(vm: OutputViewModel) -> str:
    """What the best size the search evaluated MEASURED, on a run that specified no system.

    See `viewmodel.BestCandidateView`: an all-zero revenue column on a no-spec pack reads as "this
    site earns nothing", and on the 2026-08-15 campaign the same record's sizing grid held measured
    candidate revenue from a dispatch that discharged. The card states those figures and labels
    them, in the heading and in the body, as candidate measurements that recommend nothing.

    Power and duration are printed and the energy is not: this module may do no arithmetic (its AST
    is guarded), so it cannot raise the precision until the three multiply, and a triple a reader's
    calculator rejects is how the evaluated-points table shipped nine wrong rows. Two of the three
    state the same fact and cannot disagree. The full triple is in the consulting report, which has
    `report_charts.spec_triple` to hand.
    """
    bc = vm.best_candidate
    if bc is None:
        return ""

    def _cost(value: float | None) -> str:
        """A recorded cost line, or the REASON it is absent. Never a bare n/a and never a zero.

        `_opt` would print "n/a", which is the same cell an absent figure and a zero would share on
        a page whose whole subject is telling those apart. The reason travels with the absence, as
        every other absent figure in this package does.
        """
        if value is None:
            return _unavail(NO_CANDIDATE_COST_REASON)
        return escape(fmt.gbp(value))

    rows = [
        ("Candidate power", escape(fmt.kw(bc.power_kw))),
        ("Candidate duration", escape(fmt.hours(bc.duration_hours))),
        ("Priced-floor revenue measured", escape(fmt.gbp(bc.annual_revenue_gbp))),
        ("Recorded operating cost", _cost(bc.annual_operating_cost_gbp)),
        ("Recorded dispatch cost", _cost(bc.annual_dispatch_cost_gbp)),
        ("Revenue less recorded cost", _cost(bc.net_annual_gbp)),
        ("Confirmed candidates measured", escape(str(bc.confirmed_candidate_count))),
    ]
    stream_rows = "".join(
        f"<tr><td>{escape(fmt.stream_label(r.stream))}</td>"
        f'<td class="num">{escape(fmt.gbp(r.annual_revenue_gbp))}</td>'
        f"<td>{escape(_counted_in(r))}</td>"
        f"<td>{escape(fmt.titleise(r.firmness))}</td>"
        f"<td>{escape(r.gate_status or 'n/a')}</td></tr>"
        for r in (*bc.floor_streams, *bc.held_out_streams)
    )
    table = (
        '<div class="scroll"><table><tr><th>Stream</th><th class="num">Annual revenue</th>'
        f"<th>Counted in</th><th>Firmness</th><th>Gate</th></tr>{stream_rows}</table></div>"
        if stream_rows
        else '<p class="note">This candidate reported no value streams on the grid, which is '
        "silence rather than a set of zeros.</p>"
    )
    return _card(
        BEST_CANDIDATE_HEADING,
        f'<p class="lead">{escape(bc.headline(fmt.gbp))}</p>'
        f'<p class="note">{escape(BEST_CANDIDATE_CAVEAT)}</p>'
        f"{_kv(rows)}{table}",
    )


def _proposal_panel(vm: OutputViewModel, drafter: ProseDrafter, has_floor: bool) -> str:
    intro = escape(
        drafter.proposal_intro(
            bankable=vm.bankable,
            quote_mode=vm.quote_mode,
            has_priced_floor=has_floor,
            # Same two record facts the Markdown proposal passes: this panel renders the same
            # opening, so it inherited the same two false claims.
            system_selected=vm.spec.selection_status != "no_viable_configuration",
            study_basis=vm.study_basis,
        )
    )
    p = vm.price
    cf = vm.cashflows
    offer = _kv(
        [
            ("Structure", escape(fmt.structure_label(p.structure))),
            (
                "Price",
                _opt(p.value_gbp_per_kwh, fmt.gbp_per_kwh(p.value_gbp_per_kwh))
                if p.value_gbp_per_kwh is not None
                else _unavail("price-to-target not yet computed"),
            ),
            (
                "Term (modelled)",
                _opt(
                    cf.operating_years,
                    fmt.years(cf.operating_years) if cf.operating_years is not None else "",
                )
                if cf.operating_years is not None
                else _unavail("term not yet set"),
            ),
            (
                "Valid until",
                _opt(
                    vm.provenance.expiry_date,
                    fmt.date_str(vm.provenance.expiry_date) if vm.provenance.expiry_date else "",
                ),
            ),
        ]
    )
    viability = ""
    if p.exceeds_counterfactual:
        counterfactual = (
            fmt.gbp_per_kwh(p.counterfactual_import_gbp_per_kwh)
            if p.counterfactual_import_gbp_per_kwh is not None
            else "their current rate"
        )
        viability = (
            '<p class="muted"><strong>Not commercially viable as a fixed PPA:</strong> the '
            "price required to hit the target return is above the customer's current blended "
            f"import cost ({escape(counterfactual)}). Shown for transparency, not as an "
            "offer.</p>"
        )
    bill = _bill_block(vm)
    inner = (
        f'<p class="lead">{intro}</p>'
        f"<h3>Your offer</h3>{offer}{viability}"
        f"<h3>Your bill: before &amp; after</h3>{bill}"
    )
    return _card("Customer proposal", inner)


def _bill_block(vm: OutputViewModel) -> str:
    """Three states: a priced comparison, the BEFORE side alone, or no bill at all.

    The BEFORE-only state used to render the "not yet computed" seam with an EMPTY reason (the
    view carries no pending reason when a bill exists), so the block read as an unexplained
    absence over a record holding a real figure.
    """
    b = vm.bill_savings
    if b.available and b.after_side_priced and b.annual_savings_gbp is not None:
        rows = [
            (
                "Bill today",
                _opt(
                    b.bill_before_gbp,
                    fmt.gbp(b.bill_before_gbp) if b.bill_before_gbp is not None else "",
                ),
            ),
            (
                "Bill with battery",
                _opt(
                    b.bill_after_gbp,
                    fmt.gbp(b.bill_after_gbp) if b.bill_after_gbp is not None else "",
                ),
            ),
            ("Annual saving", escape(fmt.gbp(b.annual_savings_gbp))),
        ]
        # The bill's honesty label always travels with the headline (what is NOT modelled).
        basis = f'<p class="note">{escape(b.basis)}</p>' if b.basis else ""
        return f"{_kv(rows)}{basis}"
    if b.available and b.bill_before_gbp is not None:
        before_rows = [("Bill today", escape(fmt.gbp(b.bill_before_gbp)))]
        basis = f'<p class="note">{escape(b.basis)}</p>' if b.basis else ""
        return f'{_kv(before_rows)}<p class="note">{escape(b.pending_reason)}</p>{basis}'
    return (
        f"{_unavail('not yet computed')}"
        f'<p class="note">{escape(b.pending_reason)}</p>'
        f'<p class="note"><strong>What we need:</strong> {escape(b.data_ask)}</p>'
    )


def _cashflows(vm: OutputViewModel) -> str:
    cf = vm.cashflows
    if not cf.available:
        if not vm.returns.case_available:
            # No `financial_case` at all: nothing was ever evaluated, which is a different fact from
            # a case that was run and produced no series. Both used to print the same cell, so an
            # absence read as an economic result.
            return _card("P50 / P90 cashflows", _unavail(NO_FINANCIAL_CASE_REASON))
        return _card(
            "P50 / P90 cashflows",
            _unavail("no financial case on this quote (no positive sizing to price)"),
        )
    head = (
        '<tr><th>Year</th><th class="num">P50 free cashflow</th>'
        '<th class="num">P90 free cashflow</th></tr>'
    )
    rows = [head]
    for year, (p50, p90) in enumerate(zip(cf.p50, cf.p90, strict=False)):
        label = "0 (capex)" if year == 0 else str(year)
        rows.append(
            f"<tr><td>{escape(label)}</td>"
            f'<td class="num">{escape(fmt.gbp(p50))}</td>'
            f'<td class="num">{escape(fmt.gbp(p90))}</td></tr>'
        )
    table = f'<div class="scroll"><table>{"".join(rows)}</table></div>'
    note = (
        '<p class="note">P90 is the bankable basis (the P50→P90 haircut is the headline downside). '
        "Year 0 is installed capex; later years are free cashflow before debt.</p>"
    )
    return _card("P50 / P90 cashflows", f"{table}{note}")


def _dscr(d: DscrView, sensitivities: tuple[SensitivityRow, ...]) -> str:
    if not d.available:
        body = _unavail(d.unavailable_reason or "DSCR not computed")
        if d.covenant is not None:
            body = (
                f"{body}"
                f'<p class="note">Covenant on record: {escape(fmt.ratio(d.covenant))} '
                "(placeholder assumption: not bankable).</p>"
            )
        body = f"{body}{_sensitivities(sensitivities)}"
        return _card("DSCR &amp; covenant", body)
    rows = [
        ("Minimum DSCR", _opt(d.min_dscr, fmt.ratio(d.min_dscr) if d.min_dscr is not None else "")),
        ("Basis", escape(d.basis)),
        (
            "Covenant (min P90 DSCR)",
            _opt(d.covenant, fmt.ratio(d.covenant) if d.covenant is not None else ""),
        ),
        ("Verdict", escape(fmt.pass_fail(d.covenant_passes))),
    ]
    body = _kv(rows)
    if d.by_year:
        traj = escape(" · ".join(fmt.ratio(x) for x in d.by_year))
        body = f'{body}<p class="note"><strong>DSCR by year:</strong> {traj}</p>'
    body = f"{body}{_sensitivities(sensitivities)}"
    return _card("DSCR &amp; covenant", body)


def _sensitivities(sensitivities: tuple[SensitivityRow, ...]) -> str:
    """The scenario DSCR/return table (Low/Central/High) with time-to-energisation; the standing
    pending note when none are attached (not shown rather than guessed).

    Merge note (north-star): this record-persisted renderer supersedes the branch's
    generation-time `_sensitivity_block` — every displayed figure traces to
    `QuoteRecord.scenario_sensitivities`, never to numbers authored at render time.
    """
    if not sensitivities:
        return (
            '<p class="note">Scenario DSCR/return sensitivities (Low / Central / High) '
            "activate when the scenario engine (M6) is run over the case. Not shown "
            "rather than guessed.</p>"
        )
    head = (
        "<tr><th>Scenario</th><th>Time to energisation</th>"
        '<th class="num">Min DSCR</th><th class="num">IRR</th></tr>'
    )
    rows = [head]
    for r in sensitivities:
        cod = "on time" if r.cod_delay_months == 0 else f"+{r.cod_delay_months} mo (COD delay)"
        min_dscr = fmt.ratio(r.min_dscr) if r.min_dscr is not None else fmt.unavailable("n/a")
        irr = fmt.pct(r.irr_pct) if r.irr_pct is not None else fmt.unavailable("n/a")
        rows.append(
            f"<tr><td>{escape(r.label)}</td><td>{escape(cod)}</td>"
            f'<td class="num">{escape(min_dscr)}</td>'
            f'<td class="num">{escape(irr)}</td></tr>'
        )
    table = f'<div class="scroll"><table>{"".join(rows)}</table></div>'
    heading = '<p class="note"><strong>Scenario sensitivities (Low / Central / High)</strong></p>'
    return f"{heading}{table}"


def _sources_card(vm: OutputViewModel) -> str:
    """The per-assumption source table (C020/C023) — provenance where a lender looks."""
    rows = vm.assumption_sources
    if not rows:
        return _card(
            "Assumptions &amp; sources",
            '<p class="note">Per-assumption sources render when the release library is supplied '
            f"at generation (this run is pinned to assumption version "
            f"{escape(str(vm.provenance.assumption_version))}, shown in Provenance). Not shown "
            "rather than guessed.</p>",
        )
    body = "".join(
        f"<tr><td><code>{escape(r.key)}</code></td>"
        f"<td>{escape(f'{r.value} {r.unit}'.strip())}</td>"
        f"<td>{escape(r.status)}</td><td>{escape(r.confidence)}</td>"
        f"<td>{escape(r.source)}</td></tr>"
        for r in rows
    )
    return _card(
        "Assumptions &amp; sources",
        '<div class="scroll"><table>'
        "<tr><th>Assumption</th><th>Value</th><th>Status</th><th>Confidence</th><th>Source</th>"
        f"</tr>{body}</table></div>"
        '<p class="note">Every stream-cited assumption, from release '
        f"{escape(str(vm.provenance.assumption_version))}. Nothing below "
        "<strong>confirmed</strong> is bankable.</p>",
    )


def _split(
    split: ContractedMerchantSplit,
    drafter: ProseDrafter,
    best_candidate: BestCandidateView | None = None,
) -> str:
    explainer = (
        f'<p class="note">'
        f"{escape(drafter.firmness_explainer(has_consented_streams=split.has_consented))}</p>"
    )
    # Above the tables, at the point of misreading: on a no-spec run every row below reads 0.00 and
    # the grid beside it holds measured candidate figures. Authored once on the view model so this
    # card, the memo and the consulting report say the same thing.
    if best_candidate is not None:
        explainer = (
            f'{explainer}<p class="note">{escape(best_candidate.register_lead(fmt.gbp))}</p>'
        )
    floor_title = (
        f"<h3>Contracted floor (priced): total {escape(fmt.gbp(split.floor_total_gbp))}</h3>"
    )
    floor = (
        _stream_table(split.floor)
        if split.floor
        else '<p class="note">No firm/contracted streams in this run.</p>'
    )
    upside_title = (
        f"<h3>Merchant upside (not priced): total {escape(fmt.gbp(split.upside_total_gbp))}</h3>"
    )
    upside = (
        _stream_table(split.upside)
        if split.upside
        else '<p class="note">No conditional/gated streams in this run.</p>'
    )
    excluded = ""
    if split.excluded:
        names = escape(", ".join(fmt.stream_label(r.stream) for r in split.excluded))
        excluded = f'<p class="note">Excluded (not priced): {names}.</p>'
    return _card(
        "Contracted vs merchant",
        f"{explainer}{floor_title}{floor}{upside_title}{upside}{excluded}"
        f"{_stream_notes(split.stream_notes)}",
    )


def _stream_table(rows: tuple[StreamRow, ...]) -> str:
    head = (
        '<tr><th>Stream</th><th class="num">Annual revenue</th><th>Firmness</th>'
        "<th>Gate</th><th>Unlock condition</th><th>Basis of figure</th></tr>"
    )
    out = [head]
    for r in rows:
        out.append(
            f"<tr><td>{escape(fmt.stream_label(r.stream))}</td>"
            f'<td class="num">{escape(fmt.gbp(r.annual_revenue_gbp))}</td>'
            f"<td>{escape(fmt.titleise(r.firmness))}</td>"
            f"<td>{escape(r.gate_status or 'n/a')}</td>"
            f"<td>{escape(_unlock_cell(r))}</td>"
            f"<td>{escape(r.figure_basis_cell)}</td></tr>"
        )
    return f'<div class="scroll"><table>{"".join(out)}</table></div>'


#: Why the notes are here at all, in the reader's own terms. A revenue of GBP 0.00 is exactly where
#: a reader most needs to know *which* zero it is, and until these notes rendered, the pack printed
#: the figure and kept the record's reason to itself.
_STREAM_NOTES_LEAD = (
    "Why these figures read as they do. Each note below is the run record's own statement against "
    "the streams named in it, reproduced in full and stated once however many streams carry it. "
    "The Basis of figure column cites them by number. A revenue of zero is not one fact: the "
    "optimiser may have priced the stream and declined it, a gate may have held it out of the "
    "priced floor, or no dispatch may have been solved to measure it at all."
)


def _stream_notes(notes: tuple[StreamNote, ...]) -> str:
    """The de-duplicated stream notes, each naming the streams it applies to.

    Rendered from the record's own ``ValueStreamResult.warnings`` and nothing else. The text is
    passed through `formatting.no_dashes` because this page is not scrubbed at document level
    (see `render_html_report`) and record text is the likeliest source of an em dash in the pack;
    the record itself keeps its original strings, exactly as that helper documents.

    Excluded streams have no row on this page, only a name list, so a note that named no streams
    would leave a null-input stream's reason unattributable. Every note names its own.
    """
    if not notes:
        return ""
    items = []
    for note in notes:
        streams = escape(fmt.no_dashes(", ".join(fmt.stream_label(s) for s in note.streams)))
        items.append(
            f'<li><span class="notenum">Note {escape(str(note.number))}</span> '
            f"<strong>{streams}.</strong> {escape(fmt.no_dashes(note.text))}</li>"
        )
    return (
        f'<h3>Notes on the value streams</h3><p class="note">{escape(_STREAM_NOTES_LEAD)}</p>'
        f'<ol class="streamnotes">{"".join(items)}</ol>'
    )


def _unlock_cell(r: StreamRow) -> str:
    """The unlock-condition cell — says "Consent given" when the customer authorised the stream
    (conditional/unlock wording reserved for unconsented rows). Consent is authorisation, not a
    revenue guarantee, so the firmness bar and any residual unlock condition still travel with
    it. String assembly only (no arithmetic — the HTML generator authors no number)."""
    if r.consent_status == "given":
        if r.unlock_condition:
            return f"Consent given · {r.unlock_condition}"
        return "Consent given"
    return r.unlock_condition or "n/a"


def _price_debt(vm: OutputViewModel) -> str:
    p = vm.price
    r = vm.returns
    d = vm.debt
    price_rows = [
        ("Structure", escape(fmt.structure_label(p.structure))),
        (
            "Price to target",
            _opt(p.value_gbp_per_kwh, fmt.gbp_per_kwh(p.value_gbp_per_kwh))
            if p.value_gbp_per_kwh is not None
            else _unavail("needs dispatch volume (M3) + confirmed target return"),
        ),
        (
            "Project IRR (P50)",
            _opt(r.irr_pct, fmt.pct(r.irr_pct))
            if r.irr_pct is not None
            else _unavail(r.irr_unavailable_reason),
        ),
        (
            "Simple payback",
            _opt(r.payback_years, fmt.years(r.payback_years))
            if r.payback_years is not None
            else _unavail(r.payback_unavailable_reason),
        ),
    ]
    if d.available:
        debt_rows = [
            (
                "Gearing",
                _opt(d.gearing_pct, fmt.pct(d.gearing_pct) if d.gearing_pct is not None else ""),
            ),
            (
                "Interest rate",
                _opt(d.rate_pct, fmt.pct(d.rate_pct) if d.rate_pct is not None else ""),
            ),
            (
                "Tenor",
                _opt(d.tenor_years, fmt.years(d.tenor_years) if d.tenor_years is not None else ""),
            ),
            ("Sculpting basis", escape(d.sculpting_basis or "n/a")),
        ]
        debt_block = f"<h3>Debt assumptions</h3>{_kv(debt_rows)}"
    else:
        # The record's own reason (WP-UW-03, D75); the legacy text only for pre-D75 records.
        reason = d.unavailable_reason or (
            "no per-site debt structure on record (M9 credit mapping, B001)"
        )
        debt_block = f"<h3>Debt assumptions</h3>{_unavail(reason)}"
    return _card("Price to target, returns &amp; debt", f"{_kv(price_rows)}{debt_block}")


def _counterparty(cp: CounterpartyView) -> str:
    if not cp.available:
        return _card(
            "Counterparty credit",
            _unavail("site context not supplied to this render, so nothing is assumed"),
        )
    rows = [
        ("Legal name", escape(cp.legal_name or "n/a")),
        ("Company number", escape(cp.company_number or "n/a")),
        ("Sector", escape(cp.sector or "n/a")),
        ("Credit standing", escape(fmt.titleise(cp.credit_status)) if cp.credit_status else "n/a"),
        (
            "Expected tenure",
            _opt(
                cp.expected_tenure_years,
                fmt.years(cp.expected_tenure_years) if cp.expected_tenure_years is not None else "",
            ),
        ),
    ]
    note = f'<p class="note">{escape(cp.rate_tenor_note)}</p>'
    return _card("Counterparty credit", f"{_kv(rows)}{note}")


def _consents(vm: OutputViewModel) -> str:
    cg = vm.consents_gates
    if not cg.available:
        return _card(
            "Consents &amp; physical gates",
            _unavail("site context not supplied to this render, so nothing is assumed"),
        )
    consents = _kv([(label, escape(fmt.titleise(status))) for label, status in cg.consents])
    gates = _kv([(label, escape(status)) for label, status in cg.gates])
    body = f"<h3>Consents</h3>{consents}<h3>Physical gates</h3>{gates}"
    consented = [r for r in (*vm.split.floor, *vm.split.upside) if r.consent_status == "given"]
    if consented:
        labels = escape(", ".join(fmt.stream_label(r.stream) for r in consented))
        body = (
            f'{body}<p class="note">Stream consents given (customer-authorised, priced on a '
            f"contracted basis): {labels}.</p>"
        )
    if cg.gate_notes:
        body = f'{body}<p class="note">{escape(cg.gate_notes)}</p>'
    return _card("Consents &amp; physical gates", body)


def _provenance(vm: OutputViewModel) -> str:
    p = vm.provenance
    rows = [
        ("Quote id", f"<code>{escape(p.quote_id)}</code>"),
        ("Lifecycle mode", escape(fmt.titleise(p.quote_mode))),
        ("Site id", f"<code>{escape(p.site_id)}</code>"),
        ("Data tier", escape(p.data_tier)),
        (
            "Input hash",
            f"<code>{escape(p.input_hash)}</code>" if p.input_hash else _unavail("unstamped"),
        ),
        (
            "Assumption version",
            f"<code>{escape(p.assumption_version)}</code>"
            if p.assumption_version
            else _unavail("unstamped"),
        ),
        (
            "Code version",
            f"<code>{escape(p.code_version)}</code>" if p.code_version else _unavail("unstamped"),
        ),
        ("Schema version", f"<code>{escape(p.schema_version)}</code>"),
        ("Quote expiry", _opt(p.expiry_date, fmt.date_str(p.expiry_date) if p.expiry_date else "")),
        ("Record stamped", escape(fmt.date_str(p.created_at))),
    ]
    note = (
        '<p class="note">Every figure above ties to this input hash + assumption/code version; an '
        "identical record regenerates an identical report.</p>"
    )
    return _card("Provenance", f"{_kv(rows)}{note}")


def _warnings(vm: OutputViewModel) -> str:
    if not vm.warnings:
        return _card("Warnings", '<p class="note">No warnings recorded on this quote.</p>')
    items = "".join(f"<li>{escape(w)}</li>" for w in vm.warnings)
    return _card("Warnings", f'<ul class="warn">{items}</ul>')


def _scrub(text: str) -> str:
    """Record text on its way into this page: dashes rewritten, then escaped.

    This document is not passed through `formatting.no_dashes` whole (its warnings card is
    byte-for-byte the record's), so the rewrite happens at the point of use. Same arrangement as
    the stream notes below.
    """
    return escape(fmt.no_dashes(text))


def _assumption_rows(rows: tuple[AssumptionPageRow, ...]) -> str:
    """One assumption class as a table. Every cell is record text, scrubbed and escaped."""
    body = []
    for row in rows:
        unit = f" {row.unit}" if row.unit else ""
        asserted = (
            f"<strong>Asserted by:</strong> {_scrub(row.asserted_by)}<br>"
            if row.asserted_by
            else ""
        )
        statement = (
            f"<br><em>Full statement, verbatim from the record: {_scrub(row.verbatim)}</em>"
            if row.verbatim
            else ""
        )
        body.append(
            "<tr>"
            f"<td><code>{_scrub(row.key)}</code></td>"
            f"<td>{_scrub(row.value)}{_scrub(unit)}<br><em>{_scrub(row.label)}</em></td>"
            f"<td><em>Range:</em> {_scrub(row.published_range)}<br>"
            f"<em>Source:</em> {_scrub(row.source)}{statement}</td>"
            f"<td>{asserted}{_scrub(row.status)}</td>"
            f'<td><span class="dirflag">{_scrub(row.direction)}</span><br>'
            f"{_scrub(row.direction_basis)}</td>"
            f"<td>{_scrub(row.confirmation_ask)}</td>"
            "</tr>"
        )
    head = (
        "<tr><th>Key</th><th>Value used and class</th><th>Source or published range</th>"
        "<th>Status</th><th>Direction of effect</th><th>Confirmation ask</th></tr>"
    )
    return f'<div class="scroll"><table class="assum">{head}{"".join(body)}</table></div>'


def _returns_card(vm: OutputViewModel) -> str:
    """The IRR first, then payback and the DSCR headline, then the price rows.

    Section one of the owner's order (D59): "the project IRR first". The page used to open with a
    summary paragraph and put the returns four cards down, behind the specification and the proposal
    panel.
    """
    r = vm.returns
    d = vm.dscr
    p = vm.price
    rows = [
        (
            "Project IRR (P50, unlevered)",
            _opt(r.irr_pct, fmt.pct(r.irr_pct or 0.0))
            if r.irr_pct is not None
            else _unavail(r.irr_unavailable_reason),
        ),
        (
            "Simple payback",
            _opt(r.payback_years, fmt.years(r.payback_years or 0.0))
            if r.payback_years is not None
            else _unavail(r.payback_unavailable_reason),
        ),
        (
            f"Minimum DSCR ({escape(d.basis)} basis)",
            _opt(d.min_dscr, fmt.ratio(d.min_dscr or 0.0))
            if d.min_dscr is not None
            else _unavail(d.unavailable_reason or "no DSCR on the record"),
        ),
        ("Covenant test", escape(fmt.pass_fail(d.covenant_passes))),
        ("Price structure", escape(fmt.structure_label(p.structure))),
        (
            "Price to target",
            _opt(p.value_gbp_per_kwh, fmt.gbp_per_kwh(p.value_gbp_per_kwh or 0.0))
            if p.value_gbp_per_kwh is not None
            else _unavail("no price is on the record"),
        ),
    ]
    return _card("Project IRR and returns", _kv(rows))


def _stream_assumption_block(row: StreamAssumptionRow) -> str:
    """One stream: its figure, the library entries it cited, and its own statements.

    Three parts, never flattened: a cited library value, an approval assumed under D57 and an
    engine-written note about the rate used are three different kinds of evidence about one pound.
    """
    figures = _kv(
        [
            ("Annual revenue on the record", escape(fmt.gbp(row.annual_revenue_gbp))),
            ("Counted in", escape(row.counted_in)),
            ("Firmness", escape(fmt.titleise(row.firmness))),
            ("Gate on the record", escape(row.gate_status)),
        ]
    )
    if row.library:
        head = (
            "<tr><th>Assumption key</th><th>Value</th><th>Unit</th><th>Status</th>"
            "<th>Source</th></tr>"
        )
        body = "".join(
            "<tr>"
            f"<td><code>{_scrub(a.key)}</code></td><td>{_scrub(a.value)}</td>"
            f"<td>{_scrub(a.unit)}</td><td>{_scrub(a.status)}</td><td>{_scrub(a.source)}</td>"
            "</tr>"
            for a in row.library
        )
        library = f'<div class="scroll"><table class="assum">{head}{body}</table></div>'
    else:
        library = (
            f'<p class="note"><strong>No assumption key cited.</strong> {_scrub(row.keys_note)}</p>'
        )
    if row.approvals:
        items = "".join(f"<li>{_scrub(text)}</li>" for text in row.approvals)
        approvals = (
            '<p class="note"><strong>Assumed approved for this stream under D57.</strong> Not '
            "granted: the record says assumed approved, which is true, never granted, which would "
            f"be a claim about a third party.</p><ul>{items}</ul>"
        )
    else:
        approvals = ""
    other = [text for text in row.statements if text not in row.approvals]
    if other:
        items = "".join(f"<li>{_scrub(text)}</li>" for text in other)
        statements = (
            '<p class="note"><strong>Everything else this stream alone recorded about its own '
            f"figure, verbatim.</strong></p><ul>{items}</ul>"
        )
    elif row.approvals or row.shared_notes:
        statements = ""
    else:
        statements = (
            '<p class="note"><strong>No statement recorded.</strong> '
            f"{_scrub(row.statements_note)}</p>"
        )
    # CITED, never repeated: the numbered note under the revenue register holds a shared statement
    # once, in full, naming every stream it covers.
    if row.shared_notes:
        numbers = ", ".join(str(number) for number in row.shared_notes)
        shared = f'<p class="note">{_scrub(fmt.STREAM_SHARED_NOTES_LEAD)}: {escape(numbers)}.</p>'
    else:
        shared = ""
    heading = (
        f"{fmt.stream_label(row.stream)}: {fmt.gbp(row.annual_revenue_gbp)} a year, "
        f"{row.counted_in.lower()}"
    )
    return f"<h3>{escape(heading)}</h3>{figures}{library}{approvals}{statements}{shared}"


def _stream_assumptions_card(vm: OutputViewModel) -> str:
    """The per-stream assumption register: the owner's explicit ask, one block per stream."""
    if not vm.stream_assumptions:
        return _card(
            escape(fmt.STREAM_ASSUMPTIONS_TITLE),
            '<p class="note"><strong>None recorded:</strong> the record carries no value stream, '
            "so "
            "there is no per-stream basis to show. That is an absent result, not a set of streams "
            "that earned nothing.</p>",
        )
    lead = (
        '<p class="lead">For each revenue stream, the assumptions behind ITS OWN figure: the '
        "assumption-library entries that stream's dispatch cited, with the value and source of "
        "each, and then the statements the engine itself wrote onto the stream, reproduced "
        "verbatim "
        "rather than summarised.</p>"
    )
    blocks = "".join(_stream_assumption_block(row) for row in vm.stream_assumptions)
    return _card(escape(fmt.STREAM_ASSUMPTIONS_TITLE), f"{lead}{blocks}")


def _graphs_card(
    vm: OutputViewModel,
    sizing_grid: SizingGrid | None,
    dispatch_profile: DispatchProfile | None,
) -> str:
    """The four graphs the owner named, in the order the owner named them.

    Per-stream revenue, the solved dispatch day, the full sizing surface, and the ranking that
    selected one point out of it. Every figure is drawn by `report_charts` over recorded values;
    this card places them and states what each one is.
    """
    solver_note = (
        None
        if vm.spec.solver_status not in ("mock", "insufficient_data")
        else "The record's own solver status says this dispatch was not solved on a production "
        "engine. Read every figure drawn here as documentation of a run, not as evidence about "
        "the site."
    )
    parts = [
        '<p class="lead">The four exhibits the owner asked for: revenue by stream, how the battery '
        "was dispatched on a real solved day, the whole searched sizing space, and the ranking "
        "that "
        "picked one point out of it.</p>",
        "<h3>Revenue by stream</h3>",
        charts.revenue_stack(
            floor=vm.split.floor, upside=vm.split.upside, excluded=vm.split.excluded
        ),
        "<h3>How the battery was dispatched on a real solved day</h3>",
        charts.firming_diagram(profile=dispatch_profile, site=None, solver_note=solver_note),
        "<h3>The full sizing surface, every evaluated candidate</h3>",
        charts.sizing_map(
            grid=sizing_grid,
            constraints_binding=vm.spec.constraints_binding,
            solver_note=solver_note,
        ),
        charts.sizing_surface(grid=sizing_grid),
        "<h3>How the selection was made</h3>",
        charts.milp_selection(grid=sizing_grid, solver_note=solver_note),
    ]
    return _card(
        "The graphs: revenue, dispatch, the sizing surface and the selection", "".join(parts)
    )


def _methodology_card(vm: OutputViewModel) -> str:
    """How the dispatch, the sizing search and the finance model work, and what THIS run did.

    Fixed mechanism copy from `MethodologyView`, so all four artifacts describe one machine in one
    set of words, then this run's own recorded identifiers, copied.
    """
    m = vm.methodology
    parts = [f'<p class="lead">{_scrub(m.lead)}</p>']
    for heading, bullets in (
        ("How the battery is dispatched", m.dispatch),
        ("How the size is chosen", m.sizing),
        ("How the financial case is built", m.finance),
    ):
        items = "".join(f"<li>{_scrub(line)}</li>" for line in bullets)
        parts.append(f"<h3>{escape(heading)}</h3><ul>{items}</ul>")
    parts.append("<h3>What this run actually did, from the record</h3>")
    if m.recorded:
        parts.append(_kv([(label, _scrub(value)) for label, value in m.recorded]))
    else:
        parts.append(f'<p class="note"><strong>None recorded:</strong> {_scrub(m.empty_note)}</p>')
    parts.append(f'<p class="note">{_scrub(m.note)}</p>')
    return _card(escape(fmt.METHODOLOGY_TITLE), "".join(parts))


def _assumptions_page(vm: OutputViewModel) -> str:
    """The dedicated assumptions page (D56 rule 4), on a page of its own.

    One consolidated page enumerating every assumption the run rests on, class by class, never
    merged: an owner attestation, a derived fact and a permission nobody has granted carry very
    different weight and must not share a table. An empty class prints an explicit "none recorded"
    line, so a reader can tell it apart from a class nobody looked for.
    """
    page = vm.assumptions_page
    parts = [
        f'<p class="lead"><strong>Which basis produced this page.</strong> '
        f"{_scrub(page.basis_statement)}</p>",
        f'<p class="note"><strong>What this page could see.</strong> {_scrub(page.site_note)}</p>',
    ]
    for block in page.blocks:
        parts.append(f"<h3>{_scrub(block.title)}</h3>")
        parts.append(f'<p class="note">{_scrub(block.lead)}</p>')
        parts.append(
            _assumption_rows(block.rows)
            if block.rows
            else f'<p class="note"><strong>None recorded:</strong> {_scrub(block.empty_note)}</p>'
        )
        parts.append(f'<p class="note">Source: {_scrub(block.source)}</p>')
    parts.append(f"<h3>{_scrub(fmt.ASSUMPTIONS_LIBRARY_TITLE)}</h3>")
    if page.library_rows:
        parts.append(f'<p class="note">{_scrub(page.library_note)}</p>')
        head = (
            "<tr><th>Key</th><th>Value</th><th>Unit</th><th>Status</th><th>Confidence</th>"
            "<th>Source</th></tr>"
        )
        body = "".join(
            "<tr>"
            f"<td><code>{_scrub(a.key)}</code></td><td>{_scrub(a.value)}</td>"
            f"<td>{_scrub(a.unit)}</td><td>{_scrub(a.status)}</td>"
            f"<td>{_scrub(a.confidence)}</td><td>{_scrub(a.source)}</td>"
            "</tr>"
            for a in page.library_rows
        )
        parts.append(f'<div class="scroll"><table class="assum">{head}{body}</table></div>')
    else:
        parts.append(
            f'<p class="note"><strong>None recorded:</strong> {_scrub(page.library_note)}</p>'
        )
    parts.append(f'<p class="note">{_scrub(page.footnote)}</p>')
    inner = "".join(parts)
    title = escape(fmt.ASSUMPTIONS_PAGE_TITLE)
    return f'<section class="card freshpage"><h2>{title}</h2>{inner}</section>'


def _footer(vm: OutputViewModel) -> str:
    return (
        '<footer class="rep">Generated by GridQ from the stamped run record · '
        f"assumption release {escape(vm.provenance.assumption_version or 'unstamped')} · "
        "numbers injected from the record, prose drafted (LLM-free in v1).</footer>"
    )


# --- markup helpers (string assembly only; no arithmetic) ------------------


def _card(title: str, inner_html: str) -> str:
    return f'<section class="card"><h2>{title}</h2>{inner_html}</section>'


def _kv(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td>{escape(label)}</td><td>{value}</td></tr>" for label, value in rows)
    return f'<table class="kv">{body}</table>'
