# GridQ frontend redesign — staged implementation plan

Target: this repo's Jinja app (`src/gridq/app`), deployed at the cloudflared tunnel. Reference for
quality and feature set: **gridq.ai (EASOS)**, benchmarked in `docs/benchmark/gridq_ai_reference/`.
Goal: match everything gridq.ai does, plus the spec's extra functionality (continuous sizing
surface beside a heat map, live input curves, transparent run experience). Spec:
`docs/frontend_redesign_spec.md`. Frontend only; the MILP optimiser (Olly) is not touched.

Design system (read from gridq.ai's live DOM): Inter / system-ui, page `#f8fafc`, white flat cards
with `#e5e7eb`/`#f3f4f6` hairline borders + 12px radius (no drop shadow), single warm-orange accent
`#ff6b35` (active-nav tint `rgba(255,107,53,0.1)`), headings `#1a1a2e`, 240px white left sidebar.
No em-dashes in UI copy. Far less on-screen text than the current pass.

## Stages (build, deploy, Playwright capture/compare/fix loop per stage, min two passes)

1. **Shell + Dashboard + Projects + Equipment (foundation).** Persistent left sidebar
   (Dashboard / Projects / Equipment + New Quote). Visual-first Dashboard front page. Projects list
   from the run store. Equipment catalogue (curated real products) with an upload-spec-sheet entry.
2. **5-stage input wizard** (`/new-quote`): Site, Consumption, Load profile, Equipment, Review.
   Progress bar on every stage; honest gating (run disabled + missing items shown until valid);
   site address type-ahead + map drop-a-pin; consumption via manual / CSV / bill scan (+ smart-meter
   "coming soon"); load-profile archetype curves; equipment pickers with Auto-select; review with
   small visuals.
3. **Live validation + run experience.** Live modelled curves as values are entered. Step-by-step
   run progress that surfaces results as each stage completes.
4. **Results polish.** Integrate the continuous sizing surface + heat map into the new shell; ensure
   every screen passes the rubric at desktop and mobile.

Status: **All stages built and deployed (2026-06-25).** Stage 1 (shell + Dashboard + Projects +
Equipment + design system); Stage 2 (5-stage `/new-quote` wizard with progress + honest gating +
address type-ahead + drop-a-pin map + manual/CSV/bill consumption + equipment pickers + review);
Stage 3 (live modelled load curve + step-by-step run experience overlay + `POST /api/run`); Stage 4
(continuous sizing surface + heat-map integrated; responsive desktop + mobile). ruff/mypy/smoke
green; verified via Playwright; changelog entry landed. Work left uncommitted for review.

Known follow-ups (honest gaps): bill-scan upload is collected but not OCR-parsed yet (the run uses
annual usage / CSV; a bill alone degrades honestly); address type-ahead + map are a canned UK set
(offline posture, no external geocoder/tiles); the run overlay animates the named pipeline stages
client-side (the backend is synchronous, so it does not stream true per-stage results, matching the
gridq.ai reference); the Inspect results page is the full engineer record under the new shell (a
leaner customer "overview" results view is a possible further polish).
