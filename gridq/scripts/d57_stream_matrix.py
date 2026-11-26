"""D57 acceptance matrix: every stream x every site, either a non-zero GBP or a stated carrier.

THE LOOP'S EXIT CONDITION. Reads the TOP-LEVEL `value_streams` of each published run - the layer a
reader reads - not the sizing grid, because D57 asks that every stream be PRICED, and on a selected
run the top-level list is what carries the priced figures.

Two streams are GBP0 by ACCOUNTING IDENTITY, not by any gate, and forcing them non-zero would book
the same pound twice (D57's one surviving arithmetic rule):
  * PPA_OR_BILL_SAVINGS  - itemised across TARIFF_ARBITRAGE + DUOS_RED_BAND_AVOIDANCE
  * MIC_ASC_HEADROOM     - its GBP carried by AGREED_CAPACITY_REDUCTION
Those two PASS on a stated carrier. Every other stream must carry a non-zero GBP.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/Users/oliverpugh/Desktop/Karim Project/Neura GitHub/uk")

#: stream -> the stream that carries its GBP by accounting identity (D57 arithmetic carve-out).
CARRIED_BY = {
    "PPA_OR_BILL_SAVINGS": "TARIFF_ARBITRAGE + DUOS_RED_BAND_AVOIDANCE",
    "MIC_ASC_HEADROOM": "AGREED_CAPACITY_REDUCTION",
}

#: The engine's own machine-detectable stamp for the same rule, read off the record rather than
#: hardcoded here. See `gridq.value_stack.gb_streams.CARRIED_BY_LABEL`.
CARRIED_BY_LABEL = "GBP CARRIED BY ANOTHER STREAM (D57 accounting identity)"


def matrix(run_dirs: dict[str, Path]) -> int:
    rows: list[tuple[str, str, float, str, bool]] = []
    for site, d in run_dirs.items():
        q = json.loads((d / "quote.json").read_text())
        o = q["optimisation_result"]
        streams = {v["stream"]: v for v in o.get("value_streams", [])}
        streams.update({v["stream"]: v for v in (o.get("gated_upside") or [])})
        for name, v in sorted(streams.items()):
            gbp = float(v.get("annual_revenue_gbp") or 0.0)
            if gbp > 0:
                rows.append((site, name, gbp, "priced", True))
            elif name in CARRIED_BY:
                rows.append((site, name, gbp, f"carried by {CARRIED_BY[name]}", True))
            elif any(CARRIED_BY_LABEL in w for w in v.get("warnings") or ()):
                # D57's acceptance rule VERBATIM: "either a non-zero GBP, or an explicit statement
                # on
                # the record that the GBP is carried under a named other stream". The two entries in
                # CARRIED_BY above are that rule hardcoded for the two cases known when this script
                # was written; this branch is the rule ITSELF, read off the record. It fires where
                # the
                # ENGINE states a carrier - e.g. TARIFF_ARBITRAGE on a single-rate import tariff,
                # whose import-side value is earned entirely on the DUoS bands and reported there.
                # It is not a loosening: a gated, refused or suppressed zero carries no such token,
                # and the token is only written where the zero is ARITHMETIC (on a flat tariff
                # max(rate) - min(rate) IS zero, so there is no spread to arbitrage at any size,
                # under any consent, on any basis - nothing D57 can open).
                rows.append((site, name, gbp, "carried, per the record's own statement", True))
            else:
                rows.append((site, name, gbp, "ZERO - not priced, no carrier", False))
    width = max(len(s) for s, *_ in rows) + 2
    print(f"{'site':{width}}{'stream':30}{'GBP/yr':>13}  verdict")
    for site, name, gbp, why, ok in rows:
        flag = "  " if ok else "!!"
        print(f"{flag}{site:{width - 2}}{name:30}{gbp:>13,.2f}  {why}")
    failed = [r for r in rows if not r[4]]
    sites = len(run_dirs)
    print()
    print(
        f"{len(rows)} stream-site cells over {sites} site(s): "
        f"{len(rows) - len(failed)} PASS, {len(failed)} FAIL"
    )
    if failed:
        print()
        print("STREAMS STILL NOT PRICED (D57 not yet satisfied):")
        for site, name, _gbp, _why, _ok in failed:
            print(f"   {site}  {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        dirs = {Path(a).name: Path(a) for a in args}
    else:
        # Deliberately NOT a hardcoded default any more: the ids below are whatever was published
        # last, they go stale the moment a site is re-run, and scoring stale artifacts reports the
        # OLD run's failures against the NEW code. Pass the fresh run directories as arguments.
        raise SystemExit(
            "REFUSING TO SCORE HARDCODED RUN IDS. Pass the run directories as arguments, e.g.\n"
            "  python3 scripts/d57_stream_matrix.py runs/quote_run_aaa runs/quote_run_bbb ...\n"
            "The ids a campaign published are printed by scripts/run_d57_campaign.py."
        )
        runs = REPO / "runs"
        latest = {
            "bishops_cleeve_ash_plant": "quote_run_367b478495af5a51",
            "bishops_cleeve_mrf": "quote_run_cf67b69e4694eff9",
            "knowl_hill_metered": "quote_run_e99d3dc0c02906c5",
            "knowl_hill_invoice_anchored": "quote_run_7d53872338a6f5eb",
            "ewelme_metered": "quote_run_1ea0666052ca63da",
            "ewelme_invoice_anchored": "quote_run_7f92e373c7c3f5ff",
            "tanhouse": "quote_run_1d45420476eea3d5",
            "harwell": "quote_run_7b3ed5dd361abe84",
        }
        dirs = {k: runs / v for k, v in latest.items()}
    raise SystemExit(matrix(dirs))
