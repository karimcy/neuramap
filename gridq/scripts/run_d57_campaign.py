"""Run and publish the eight-run validation campaign on the real metered data.

EIGHT runs, not seven. Five metered Grundon sites, the two invoice-anchored variants the owner
asked to see reported alongside them, and HARWELL - which is re-run because the physics-disclosure
fix (commit 5a274ac) changes what its pack carries: `deliverables/harwell_report.html` was measured
to contain zero physics disclosures, and that fix exists precisely for the no-spec path Harwell
takes. Leaving it stale would ship an inconsistent set.

**The release is PINNED, not inherited.** `gb_v1_9` was cut mid-drive, so `latest_version()` would
silently change what this campaign priced depending on when it ran. A campaign that cannot say which
release produced it is not a validation.

**Study basis, stated rather than defaulted (D46).** Every run uses the named feasibility basis that
prices export into the priced floor, because the owner directed that export restrictions are lifted
for this study. That is NOT an underwriting output and each pack says so. Consent remains
`pending_permission` on both bases: where revenue sits is a modelling choice, but asserting a
customer has agreed an export arrangement is a factual claim about a third party.

**Identity is asserted, not assumed.** Two site JSONs deliberately share a `site_id` with their
metered twin (`grundon_ewelme`, `grundon_knowl_hill`), which is only safe if the basis actually
splits the hash. This script FAILS if any two runs collide, rather than publishing two packs that
quietly describe the same identity.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import traceback
from pathlib import Path

REPO = Path("/Users/oliverpugh/Desktop/Karim Project/Neura GitHub/uk")
sys.path.insert(0, str(REPO / "src"))

import gridq.schemas as s  # noqa: E402
from gridq.assumptions import load_assumptions  # noqa: E402
from gridq.optimisation.engine import MilpEngine  # noqa: E402
from gridq.quotes.persistence import publish_run  # noqa: E402
from gridq.quotes.pipeline import generate_quote  # noqa: E402
from gridq.schemas.derived import ConfidenceBand, DerivedAssumption  # noqa: E402
from gridq.schemas.study import AllStreamsEnabledStudyBasis, StudyBasis  # noqa: E402
from gridq.value_stack.gb_streams import dfs_zone_basis, dfs_zone_for_site  # noqa: E402

RELEASE = "gb_v1_12"  # PINNED. Never latest_version() mid-drive.

#: (label, site JSON stem). The label names the site AND its basis, so no pack can be mistaken for
#: the other basis of the same site.
RUNS = [
    ("bishops_cleeve_ash_plant", "bishops_cleeve_ash_plant"),
    ("bishops_cleeve_mrf", "bishops_cleeve_mrf"),
    ("knowl_hill_metered", "knowl_hill"),
    ("knowl_hill_invoice_anchored", "knowl_hill_invoice_anchored"),
    ("ewelme_metered", "ewelme"),
    ("ewelme_invoice_anchored", "ewelme_invoice_anchored"),
    ("tanhouse", "tanhouse"),
    ("harwell", "harwell"),
]

MID = REPO / "data" / "fetched" / "elexon" / "mid_2026-07-13.csv"
CITATION = (
    "Elexon Insights Market Index Data (MID), settlement day 2026-07-13, 48 half-hours, "
    "fetched 2026-07-20; held at data/fetched/elexon/mid_2026-07-13.csv"
)
LIMITATION = (
    "ONE SETTLEMENT DAY (2026-07-13, summer), not a forward annual curve. Indicative study "
    "input only; it is not a guaranteed behind-the-meter export price."
)


def tariff() -> tuple[float, int]:
    with MID.open() as fh:
        p = [float(r["price_gbp_per_mwh"]) for r in csv.DictReader(fh)]
    if not p:
        raise SystemExit("MID file carries no prices; refusing to invent an export tariff")
    return statistics.fmean(p) / 1000.0, len(p)


def site_of(stem: str) -> s.SiteModel:
    return s.SiteModel.model_validate(
        json.loads((REPO / "examples" / "real" / "sites" / f"{stem}.json").read_text())
    )


def limit_kw(site: s.SiteModel) -> float | None:
    c = site.connection
    for v in (getattr(c, "mic_kw", None), getattr(c, "asc_kw", None)):
        if v is not None and float(v) > 0:
            return float(v)
    peak = getattr(site.load_data, "max_import_kw", None)
    return None if peak is None else float(peak)


#: The OPTION SET this basis declares, gross of the aggregator cut, GBP/kW/yr at 2 h. Declarative:
#: no production code prices from these figures (the engine prices every stream from the dispatch,
#: the site record or the library), so this dict is the DISCLOSURE D57 limb 4 requires and not a
#: second pricing path that could disagree with the first.
PRICES_2H = {
    "WHOLESALE_P415": 22.92,  # MID year, 2 h, net of degradation, at 80% capture
    "BALANCING_MECHANISM": 17.47,  # method B: system-price uplift x (1 - VLP skip)
    "ANCILLARY_DC_DM_DR": 20.75,  # DCL net, the best GBP per kWh of warranty throughput
    "CAPACITY_MARKET": 5.96,  # forward T-4 27.10 x 0.2198 de-rating at 2 h
    # PLACEHOLDER, OVERWRITTEN PER SITE (2026-08-19). DFS is the one entry in this dict that is
    # not a single GB figure: it is ZONAL, and `basis()` replaces this with the SITE'S OWN zone's
    # measured outturn on the OBSERVED period count (zone 10 = GBP 30.34/kW/yr, zone 12 = GBP
    # 29.74/kW/yr at full delivery). The superseded figure was GBP 93.41 - zone 6, the most
    # favourable of the twelve, with its 127-day observation annualised by 365/127. The value here
    # is the zonal median so that a caller who forgets to pass a site gets a stated central case
    # rather than the best zone in the country.
    "DFS": 24.08,
}

#: The published exclusivity families, DECLARED SO THEY CAN BE SEEN TO BE WAIVED. DFS may not share
#: a
#: meter point with a BM Unit (DFS Procurement Rules v5.0 para 4.7.6, unconditional) and is
#: conditionally barred from the response family (4.7.9); the Capacity Market is expressly permitted
#: alongside DFS (4.7.8(a)) and appears in no family. Nothing in the engine enforces these - D57
#: limb 3 assumes every one of them satisfied - so they are here as the register entry, which is
#: exactly what limb 4 asks for: the optimism lives in the value, the candour lives in the label.
FAMILIES = [["DFS", "BALANCING_MECHANISM"], ["DFS", "ANCILLARY_DC_DM_DR"]]

#: warranty_cycle_limits.max_cycles_per_year_by_duration_h["2"] = 365 nameplate cycles x 2 kWh/kW.
WARRANTY_2H_KWH_PER_KW_YR = 730.0


def award(stream: str) -> DerivedAssumption:
    """The assumed-award record for ``stream``: a PERMISSION, never a value (D45)."""
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
            "D57 assumes the award, the registration, the consent and every statutory or product "
            "minimum-size threshold APPROVED for this site and this battery size, on the owner's "
            "authority (Olly, 2026-08-18). A permission is not derivable (D45): the stream is "
            "modelled and its value shown, never granted by a third party."
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
            "https://www.flexiblepower.co.uk/downloads/1150 - searched, NO revenue share stated",
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


D57_JUSTIFICATION = (
    "D57 ACADEMIC VALIDATION BASIS (Olly, owner-directed, 2026-08-18). Every revenue stream is "
    "OPEN "
    "and PRICED on every site, and every contract, consent, gate, award, registration, threshold "
    "and "
    "rule is ASSUMED APPROVED for the site AND for the battery size under evaluation - export "
    "consent, the export MPAN, G99/G100, route to market, credit assessment, CM prequalification, "
    "BM "
    "Unit registration, response-unit registration, DFS registration, DNO flexibility awards, "
    "aggregation, and every statutory or product minimum size (the 1 MW registered unit, the 0.1 "
    "MW "
    "DFS Registered Unit Capacity). Stacking exclusivity, tender availability and consent status "
    "are "
    "assumed satisfied and are declared on this record rather than enforced. Awarded-contract "
    "rates, "
    "participant-only rates and tender-specific clears are USED IN FULL (limb 5b): the award "
    "exists "
    "by assumption. THE ONE RULE PRESERVED IS ARITHMETIC INTEGRITY - no pound appears under two "
    "stream names, nor in both a cost line and a revenue line. NOT AN UNDERWRITING OUTPUT, and no "
    "figure on this basis may be presented as one."
)


def basis(
    lim: float, tar: float, n: int, *, dfs_gbp_per_kw_yr: float | None = None
) -> AllStreamsEnabledStudyBasis:
    """The D57 basis: the D46 export half unchanged, plus the declared option set and awards.

    ``dfs_gbp_per_kw_yr`` is the SITE'S OWN DFS zone's measured figure. It is declared per site
    because DFS is the one stream in the option set whose price and call count are zonal, and a
    disclosure that named one zone for every site would contradict what the engine priced.
    """
    export = export_basis(lim, tar, n)
    prices = dict(PRICES_2H)
    if dfs_gbp_per_kw_yr is not None:
        prices["DFS"] = dfs_gbp_per_kw_yr
    return AllStreamsEnabledStudyBasis(
        basis_id="all_streams_enabled_study_v1",
        authorised_by="Olly",
        justification=D57_JUSTIFICATION,
        acknowledgement=AllStreamsEnabledStudyBasis.ACKNOWLEDGEMENT,
        export_limit_kw=export.export_limit_kw,
        export_tariff_gbp_per_kwh=export.export_tariff_gbp_per_kwh,
        tariff_record=export.tariff_record,
        export_limit_record=export.export_limit_record,
        consent_record=export.consent_record,
        enabled_streams=list(prices),
        stream_price_gbp_per_kw_yr=prices,
        stream_award_records=[award(s) for s in prices],
        aggregator_cut_pct=0.20,
        aggregator_cut_record=cut_record(),
        exclusive_families=[list(f) for f in FAMILIES],
        warranty_throughput_kwh_per_kw_yr=WARRANTY_2H_KWH_PER_KW_YR,
    )


def export_basis(lim: float, tar: float, n: int) -> StudyBasis:
    return StudyBasis(
        # PINNED by the schema to a single Literal, and correctly so: a study basis whose id a
        # caller can vary is a basis nobody can compare runs across. I tried "..._v2_metered" and
        # the schema refused it. The basis is materially unchanged (export priced into the floor
        # from a sourced tariff); what changed is the LOAD DATA, which belongs in the justification
        # and in `input_hash`, not in the identifier.
        basis_id="export_in_priced_floor_study_v1",
        authorised_by="Olly",
        justification=(
            "In-house feasibility study on REAL METERED half-hourly data (decision D46). Export "
            "restrictions are lifted for this study at the owner's direction. Not an underwriting "
            "output: no site has an agreed export tariff, an export MPAN or G99 approval."
        ),
        acknowledgement=StudyBasis.ACKNOWLEDGEMENT,
        export_limit_kw=lim,
        export_tariff_gbp_per_kwh=tar,
        tariff_record=DerivedAssumption(
            kind="derived_input",
            key=StudyBasis.TARIFF_KEY,
            missing_input="No site declares an agreed export tariff; none has been negotiated.",
            value=tar,
            unit="GBP/kWh",
            basis=(
                f"Mean of {n} half-hourly Elexon MID prices, 2026-07-13. LIMITATION: {LIMITATION}"
            ),
            sources=[CITATION],
            derivation_method="sourced_default",
            derived_by="claude-opus-5",
            derived_on_release=RELEASE,
            confidence_ceiling=ConfidenceBand.LOW,
            confirmation_ask=(
                "An agreed export tariff from the offtaker, or a forward wholesale curve for the "
                "delivery years, replacing this single-day indicative mean."
            ),
        ),
        export_limit_record=DerivedAssumption(
            kind="derived_input",
            key=StudyBasis.EXPORT_LIMIT_KEY,
            missing_input="No site records an export capacity (mec_kw is null on every site).",
            value=lim,
            unit="kW",
            basis=(
                "The site's own evidenced connection bound (MIC or ASC where recorded, else the "
                "MEASURED peak from its metered series). Not an assumed larger connection."
            ),
            sources=["examples/real/sites/*.json connection block; data/sites/hh/manifest.json"],
            derivation_method="site_analogue",
            derived_by="claude-opus-5",
            derived_on_release=RELEASE,
            confidence_ceiling=ConfidenceBand.LOW,
            confirmation_ask="The site's actual MEC from its DNO connection agreement.",
        ),
        consent_record=DerivedAssumption(
            kind="pending_permission",
            key=StudyBasis.CONSENT_KEY,
            missing_input=(
                "No customer has consented to wholesale export (no G99/G100 approval, no export "
                "MPAN, no per-stream consent)."
            ),
            value=None,
            unit=None,
            basis=(
                "A permission is not derivable (D45). D46 moves where export revenue SITS on a "
                "study basis; it cannot assert that the customer has agreed."
            ),
            sources=[],
            derivation_method="not_derivable",
            derived_by="claude-opus-5",
            derived_on_release=RELEASE,
            confidence_ceiling=ConfidenceBand.LOW,
            confirmation_ask=(
                "Signed per-stream consent for WHOLESALE_P415, G99/G100 approval and an export "
                "MPAN before any figure on this basis is treated as underwritten."
            ),
        ),
    )


def main() -> int:
    # Scratchpad addition: run only the labels named on argv, so the slow MILP loop can iterate on
    # one site. No argument keeps the full eight-run campaign.
    global RUNS
    wanted = set(sys.argv[1:])
    if wanted:
        RUNS = [r for r in RUNS if r[0] in wanted]
        if not RUNS:
            raise SystemExit(f"no run label matched {sorted(wanted)}")
    lib = load_assumptions(RELEASE)
    tar, n = tariff()
    print(f"release {RELEASE} (PINNED) | export tariff GBP {tar:.6f}/kWh from {n} MID half-hours\n")
    seen: dict[str, str] = {}
    ok = 0
    for label, stem in RUNS:
        try:
            site = site_of(stem)
            lim = limit_kw(site)
            # The DECLARED DFS price is this site's own zone's measured outturn, resolved by the
            # same resolver the dispatch uses, so the register and the objective cannot disagree.
            zone, _zone_resolved, _zone_notes = dfs_zone_for_site(site)
            observation, _obs_used, _obs_notes = dfs_zone_basis(lib, zone=zone)
            engine = MilpEngine(
                study_basis=None
                if lim is None
                else basis(
                    lim,
                    tar,
                    n,
                    dfs_gbp_per_kw_yr=round(observation.gross_gbp_per_kw_yr, 2),
                )
            )
            print(f"  {label:30} DFS zone {zone}: GBP {observation.gross_gbp_per_kw_yr:.2f}/kW/yr")
            run = generate_quote(site, lib, engine=engine)
            published = publish_run(run)
            q = run.quote
            ident = f"{q.input_hash}|{q.quote_id}"
            if ident in seen:
                print(f"  {label:30} IDENTITY COLLISION with {seen[ident]} -> {ident}")
                print("  REFUSING: two runs share an identity; a basis that does not split the")
                print("  hash means two packs would silently describe the same run.")
                return 2
            seen[ident] = label
            band = getattr(q, "confidence_band", None)
            print(
                f"  {label:30} {q.quote_id}  band={band}  "
                f"warnings={len(q.warnings or [])}  bill="
                f"{'yes' if q.customer_bill is not None else 'no'}"
            )
            for p in getattr(published, "written", []) or []:
                if str(p).endswith("consulting_report.html"):
                    print(f"      pack: {p}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - report, never swallow
            print(f"  {label:30} ERROR {type(exc).__name__}: {str(exc)[:200]}")
            traceback.print_exc(limit=2)

    print(f"\n=== {ok}/{len(RUNS)} published, {len(seen)} distinct identities ===")
    if ok == 0:
        print("  NO CONCLUSION: nothing published. This is not a result.")
        return 1
    if len(seen) != ok:
        print("  DEFECT: fewer identities than runs.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
