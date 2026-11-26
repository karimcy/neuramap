# ACTIVE_PLAN

The current build plan in plain English. The fields between the markers are the live plan — edit them by hand or via `gridq.dev.team_sync.update_active_plan(...)`. Keep prose human-readable.

<!-- ACTIVE_PLAN:BEGIN -->
- **Current milestone:** **The north-star drive is COMPLETE through Wave 9** ([docs/north_star.md](../north_star.md), adopted D27). Waves 6 (adaptive sizing, issue 016 all 9 stages, D28/D29), 7 (the firming substrate end-to-end: C026 commitment schema → solver window/held-SoE constraints → end-of-tenor deliverability → evidence-bearing gates → the customer bill, C017 resolved), 8 (underwriting risk shapes: D30, perfect-foresight haircut consumed, per-firmness P90, penalty line, contracted-basis DSCR, gb_v1_1, C020/C023 resolved) and 9 (calendar-as-content, honest peak measurement, capped-bill structure; PV + headroom objective = C028/C029 awaiting Olly) — 19 loop iterations, 2026-07-01→02, **568→674 tests green**, all work uncommitted on `agent/sizing-search` per the lane convention.
- **Current task:** NONE RUNNING — the drive's loop stopped per its rule: everything remaining is externally gated or human-only.
- **What unblocks the next wave (owners):** **Olly** — review/co-decide C026 (commitment schema), C027 (ValueStreamResult growth), C028 (explicit PV on SiteModel), C029 (peak-protection objective + TariffStack capacity fields), plus D28/D29 sign-off. **Karim/Zara** — RTM partner (B002), NESO de-rating + clearing series, Piclo tenders, the £/kWh capex component (**C025** — gates the wider-search engine default per D29). **Sim** — gearing/rate/tenor (B001; activates DSCR with zero code change). **Olly** — `GRIDQ_BASIC_AUTH` on the live URL (**still open, still urgent**: real-prospect prefill publicly reachable), and commit/merge reconciliation across the uncommitted lanes.
- **Acceptance criteria (met):** every slice landed with the full bar green (pytest/ruff/format/mypy/review_gates); every invariant intact (determinism, not_bankable on placeholders, no invented data, frozen contracts changed only by filed joint events); goldens preserved except where priced output changed honestly (the W8 foresight correction — demo gearing re-tuned 55→25%, documented).
- **Risks:** the uncommitted volume across lanes keeps growing — commit/merge reconciliation is now the biggest operational risk; C025 means the wider sizing search must NOT become the engine default until the capex shape carries an energy component.
- **Last touched by:** Karim (the north-star drive, /loop iterations 1–19) — 2026-07-02
- **Notes for the next person:** Read [docs/north_star.md](../north_star.md) + the drive summary page (`/static/changes-2026-07.html` on the live VPS — additive file, safe to delete) + the 2026-07-01/02 changelog for the slice-by-slice record; D27–D30 are the decisions, C025–C029 the open threads. `GRIDQ_CONTRIBUTOR` is still unset on Karim's machine.
<!-- ACTIVE_PLAN:END -->

## Working notes (free-form, human-edited)
Anything that doesn't fit the structured block above — sketches, decisions in flight, links. Durable decisions still go to `docs/decisions.md`.

- 2026-07-17 (Olly): **PROPOSED next drive** — [issue 018 — meeting-readiness drive](../issues/018-meeting-readiness-drive.md) (export-aware dispatch, Elexon historical HH price inputs, local-flex net of aggregator cut, Robert Thomas end-to-end, new README). Awaiting kickoff; slices A2/A3 and the ValueStreamResult shape are Olly-gated (G1/G2 in the issue). Update the structured block above when execution starts.
