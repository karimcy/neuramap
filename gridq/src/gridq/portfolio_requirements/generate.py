"""One-command regeneration of every deliverable.

    python -m gridq.portfolio_requirements.generate --out deliverables/

Emits, deterministically:

* ``00_portfolio_requirements.html`` — the "what would it take to earn 20%" report
* ``<site>_info_pack.html`` and ``<site>_deck.html`` for each of the five sites
* ``portfolio_summary.html`` — the five sites rolled up, Pessimistic/Central/Optimistic
* ``assumptions_register.json`` + ``.md`` per site — the machine- and human-readable registers

Re-running with the same inputs overwrites byte-identically. Nothing here computes a number: the
generators format values from :mod:`gridq.portfolio_requirements.sites` results.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gridq.portfolio_requirements.report import build_report
from gridq.portfolio_requirements.site_report import build_deck, build_info_pack
from gridq.portfolio_requirements.sites import (
    CASES,
    EXPECTED_LEVERED,
    INVESTABILITY_RANKING,
    SITE_ORDER,
    SOLAR_LIFE_NOTE,
    cases_for,
    evaluate_site,
)

SUMMARY_CSS_IMPORT = "from gridq.portfolio_requirements.site_report import PACK_CSS"


def _pct(v: float | None, dp: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{dp}f}%"


def build_portfolio_summary() -> str:
    """The five sites rolled up — the estate view, all three cases."""
    from gridq.portfolio_requirements.site_report import PACK_CSS

    rows = ""
    totals: dict[str, dict[str, float]] = {c: {"rev": 0.0, "capex": 0.0, "eq": 0.0} for c in CASES}
    for key, label in SITE_ORDER:
        cells = ""
        for case in CASES:
            c = next((x for x in cases_for(key) if x.case == case), None)
            if c is None or not c.proposed:
                cells += '<td class="n">—</td><td class="n">—</td><td class="n">—</td>'
                continue
            r = evaluate_site(c)
            totals[case]["rev"] += c.revenue_gbp
            totals[case]["capex"] += c.capex_gbp
            totals[case]["eq"] += r.equity
            cells += (
                f'<td class="n">{c.capex_gbp / 1000:,.0f}k</td>'
                f'<td class="n">{c.revenue_gbp / 1000:,.0f}k</td>'
                f'<td class="n">{_pct(r.levered_irr)}</td>'
            )
        rows += f"<tr><td><strong>{label}</strong></td>{cells}</tr>"

    trow = ""
    for case in CASES:
        t = totals[case]
        trow += (
            f'<td class="n"><strong>{t["capex"] / 1000:,.0f}k</strong></td>'
            f'<td class="n"><strong>{t["rev"] / 1000:,.0f}k</strong></td>'
            f'<td class="n">—</td>'
        )

    rank = "".join(
        f"<tr><td class='n'>{n}</td><td><strong>"
        f"{dict(SITE_ORDER)[key]}</strong><br><span class='note'>{note}</span></td></tr>"
        for n, key, note in INVESTABILITY_RANKING
    )

    return f"""<title>Grundon + Harwell — Portfolio Summary</title>
<style>{PACK_CSS}</style>
<div class="wrap">
  <p class="eyebrow">Neura · GridQ · Indicative — pre-diligence analysis</p>
  <h1>Five sites, three cases</h1>
  <p class="lede">The Grundon estate plus Harwell, each sized from its own metered and invoiced
  data, each carried through a Pessimistic, Central and Optimistic case with every assumption
  stated and classified.</p>

  <h2>The estate</h2>
  <div class="scroll"><table>
    <thead>
      <tr><th rowspan="2">Site</th><th colspan="3" class="n">Pessimistic</th>
      <th colspan="3" class="n">Central</th><th colspan="3" class="n">Optimistic</th></tr>
      <tr><th class="n">Capex</th><th class="n">Rev/yr</th><th class="n">Lev. IRR</th>
      <th class="n">Capex</th><th class="n">Rev/yr</th><th class="n">Lev. IRR</th>
      <th class="n">Capex</th><th class="n">Rev/yr</th><th class="n">Lev. IRR</th></tr>
    </thead>
    <tbody>{rows}<tr><td><strong>Estate total</strong></td>{trow}</tr></tbody>
  </table></div>
  <p class="note">Portfolio IRR is deliberately not summed across sites — the sites have different
  financing tiers and different gating conditions, so an aggregate percentage would obscure rather
  than inform. Read each site on its own basis and the ranking below for priority.</p>

  <h2>Priority — risk-adjusted</h2>
  <div class="scroll"><table>
    <thead><tr><th class="n">#</th><th>Site and verdict</th></tr></thead>
    <tbody>{rank}</tbody>
  </table></div>

  <h2>Three gating verifications</h2>
  <p>Three sites carry a blocking unknown. None of their numbers should reach a client before the
  corresponding document is read.</p>
  <ul>
    <li><strong>Bishops Cleeve</strong> — is the uniform 0.49 p/kWh "Feed in tariff" line the FiT
    consumer levy, or the actual export price? If the latter, re-contracting 6.7 GWh/yr is worth
    roughly £400–435k/yr — about 8× the battery's Central revenue — and that action
    comes first.</li>
    <li><strong>Tanhouse &amp; Colnbrook</strong> — does the EfW private-wire contract permit
    displacement, with no take-or-pay floor? If not, the solar value is £0 and the site drops out
    entirely. This is the estate's highest raw return riding on one unread document.</li>
    <li><strong>Ewelme</strong> — was the connection MIC de-rated or terminated after the aerosol
    plant failed? The 750 kVA headroom is the whole investment case.</li>
  </ul>

  <h2>Convention note</h2>
  <p>{SOLAR_LIFE_NOTE}</p>

  <div class="foot">
    Indicative — pre-diligence analysis. No underwriting liability is carried at this stage.
    Scenario design by the Fable advisor (2026-08-05); its first expected-IRR estimates were
    retracted on reconciliation in favour of this model's DCF output. Every figure here is
    formatted from a deterministic run record.
  </div>
</div>"""


def build_register(site_key: str) -> tuple[str, dict[str, object]]:
    """Assumptions register for one site, as markdown and as JSON."""
    cs = cases_for(site_key)
    label = cs[0].site_label
    blocking = next((c.blocking for c in cs if c.blocking), None)

    seen: list[str] = []
    for c in cs:
        for a in c.key_assumptions:
            if a not in seen:
                seen.append(a)

    payload: dict[str, object] = {
        "site_key": site_key,
        "site_label": label,
        "basis": "Indicative — pre-diligence analysis",
        "base_year": 2025,
        "asset_life_years": 15,
        "convention_note": SOLAR_LIFE_NOTE,
        "blocking_verification": blocking,
        "assumptions": seen,
        "cases": [],
        "advisor_expected_levered": dict(zip(CASES, EXPECTED_LEVERED[site_key], strict=True)),
    }
    md = [
        f"# Assumptions register — {label}",
        "",
        "**Indicative — pre-diligence analysis.** Base year 2025. Asset life 15 years.",
        "",
    ]
    if blocking:
        md += ["## Blocking verification", "", blocking, ""]
    md += ["## Cases", ""]
    for c in cs:
        if not c.proposed:
            md += [f"### {c.case}", "", "No proposal in this case.", ""]
            payload_cases = payload["cases"]
            assert isinstance(payload_cases, list)
            payload_cases.append({"case": c.case, "proposed": False})
            continue
        r = evaluate_site(c)
        md += [
            f"### {c.case}  ·  classification: {c.tag.value}",
            "",
            f"- Battery: {f'{c.bess_mw:g} MW / {c.bess_mwh:g} MWh' if c.bess_mw else 'none'}",
            f"- Solar: {f'{c.solar_kwp:g} kWp' if c.solar_kwp else 'none'}",
            f"- Revenue year 1: £{c.revenue_gbp:,.0f}",
            f"- Capex: £{c.capex_gbp:,.0f}  ·  Opex: £{c.opex_gbp:,.0f}/yr",
            f"- Financing: {c.tier.name}",
            f"- Unlevered IRR **{_pct(r.unlevered_irr, 2)}**  ·  "
            f"Levered IRR **{_pct(r.levered_irr, 2)}**",
            f"- Gearing {r.gearing * 100:.0f}% (bound by {r.debt_bound_by})  ·  "
            f"DSCR avg {r.dscr_avg:.2f}×  ·  NPV@8% £{r.npv_at_8pct:,.0f}",
            f"- Dominant driver: {c.driver}",
            "",
            "Revenue lines:",
            "",
        ]
        for n, v in sorted(c.revenue_lines.items(), key=lambda kv: -kv[1]):
            md.append(f"  - {n}: £{v:,.0f}")
        md.append("")
        pc = payload["cases"]
        assert isinstance(pc, list)
        pc.append(
            {
                "case": c.case,
                "proposed": True,
                "classification": c.tag.value,
                "bess_mw": c.bess_mw,
                "bess_mwh": c.bess_mwh,
                "solar_kwp": c.solar_kwp,
                "revenue_lines_gbp": c.revenue_lines,
                "revenue_total_gbp": c.revenue_gbp,
                "capex_gbp": c.capex_gbp,
                "opex_gbp_per_year": c.opex_gbp,
                "financing_tier": c.tier.name,
                "financing_rate": c.tier.rate,
                "financing_tenor_years": c.tier.tenor_years,
                "dscr_target": c.tier.dscr_target,
                "unlevered_irr": r.unlevered_irr,
                "levered_irr": r.levered_irr,
                "gearing_achieved": r.gearing,
                "gearing_bound_by": r.debt_bound_by,
                "dscr_avg": None if r.dscr_avg == float("inf") else r.dscr_avg,
                "npv_at_8pct_gbp": r.npv_at_8pct,
                "payback_years": r.payback_years,
                "dominant_driver": c.driver,
            }
        )
    md += ["## Stated assumptions", ""]
    md += [f"{i + 1}. {a}" for i, a in enumerate(seen)]
    md += ["", "## Convention note", "", SOLAR_LIFE_NOTE, ""]
    return "\n".join(md), payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate every Grundon/Harwell deliverable.")
    ap.add_argument("--out", default="deliverables", help="output directory")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    def w(name: str, content: str) -> None:
        (out / name).write_text(content, encoding="utf-8")
        written.append(name)

    w("00_portfolio_requirements.html", build_report())
    w("01_portfolio_summary.html", build_portfolio_summary())
    for key, _label in SITE_ORDER:
        w(f"{key}_info_pack.html", build_info_pack(key))
        w(f"{key}_deck.html", build_deck(key))
        md, payload = build_register(key)
        w(f"{key}_assumptions_register.md", md)
        w(
            f"{key}_assumptions_register.json",
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )

    print(f"wrote {len(written)} files to {out}/")
    for n in written:
        print(f"  {n}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
