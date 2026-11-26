# OSS alternatives — evaluation record (red-team audit, 2026-07-07, Olly)

Per-module search for mature open-source alternatives to the homegrown code, with adopt/reject
verdicts. Research run 2026-07-07 (web + GitHub). Star counts approximate as of July 2026.

**Headline:** the platform's two most "reinvented" areas are correctly reinvented — the linopy
dispatch (PyPSA itself now builds on linopy) and UK meter/DUoS ingestion (no mature OSS exists).
Two clear adoptions: **pyxirr** (IRR robustness) and **Authlib** (OAuth should never be
homegrown). REopt.jl is retained as an external benchmark idea, not a component.

## 1. Battery dispatch optimisation (half-hourly LP/MILP)

| Candidate | Repo | Stars | Licence | Last release |
|---|---|---|---|---|
| PyPSA | github.com/PyPSA/PyPSA | ~2,000 | MIT | v1.2.4, Jun 2026 (active) |
| linopy (current LP layer) | github.com/PyPSA/linopy | ~250 | MIT | v0.8.0, Jun 2026 (active) |
| oemof.solph | github.com/oemof/oemof-solph | ~410 | MIT | v0.6.4, Apr 2026 |
| Calliope | github.com/calliope-project/calliope | ~370 | Apache-2.0 | slow release cadence |
| pyomo | github.com/Pyomo/pyomo | ~2k+ | BSD-3 | active (general AML) |
| EMHASS | github.com/davidusb-geek/emhass | ~520 | MIT | home-scale, PuLP, HA-coupled |
| SimSES | pypi.org/project/simses | small | BSD-ish | stale; simulator not optimiser |
| prosumpy | energy-modelling-toolkit/prosumpy | tiny | — | dormant (~2021) |
| RAMP | github.com/RAMP-project/RAMP | modest | EUPL | load-profile generation, not dispatch |

**Verdict: REJECT frameworks — keep the hand-rolled linopy dispatch.** PyPSA itself now builds
on linopy, so we already sit on the optimisation layer the leading framework uses, minus its
system-planning abstractions. Bespoke UK BTM logic (DUoS bands, capacity charges, warranty
throughput caps, tie-break regularisers) is easier to express and *audit* as a direct MILP than
through generic Store/Bus/Link components. PARTIAL adoption: use PyPSA's storage-constraint
formulations as a correctness reference in tests.

## 2. BESS sizing (outer search over power/duration)

| Candidate | Repo | Licence | Notes |
|---|---|---|---|
| REopt.jl (NREL) | github.com/NREL/REopt.jl | Apache-2.0 | v0.59.2 May 2026; Julia/JuMP; custom ToU tariffs supported; US-centric finance |
| PyPSA capacity expansion | github.com/PyPSA/PyPSA | MIT | joint sizing+dispatch in one LP |

**Verdict: PARTIAL — keep the homegrown two-stage search; REopt.jl only as an offline
credibility benchmark.** REopt is the one mature HOMER-like OSS doing joint sizing+dispatch with
demand charges, but it is a Julia runtime, US-centric in finance/incentives, and cannot express
the UK underwriting layer; adopting it is a rewrite. Single-shot capacity expansion (PyPSA)
hides the sizing frontier the product deliberately surfaces (the power×duration grid).

## 3. Financial modelling (IRR/NPV/DSCR/sculpting)

| Candidate | Repo | Licence | Notes |
|---|---|---|---|
| pyxirr | github.com/Anexen/pyxirr | Unlicense | v0.10.8 Nov 2025; Rust-backed; Excel-compatible root search + conservative fallback |
| numpy-financial | github.com/numpy/numpy-financial | BSD-3 | dormant (one release, 2019) |
| cashflows | github.com/jdvelasq/cashflows | MIT | dormant teaching package |
| NREL PySAM | github.com/NREL/pysam | BSD-3 | US-tax-centric (MACRS/ITC) |

**Verdict: ADOPT pyxirr for IRR/NPV root-finding; keep cashflow construction, DSCR and
sculpting in-house.** IRR root-finding is exactly where hand-rolled code fails silently
(multi-sign-change cashflows, multiple/no real roots); pyxirr documents its search strategy and
picks the conservative root. No credible OSS does project-finance DSCR waterfalls. Given the
F-012 double-count history, cashflow assembly stays in-house where it is testable.
**Adoption status (corrected 2026-07-08): NOT adopted.** On reading `finance/returns.py`, the
premise behind the recommendation (hand-rolled IRR fails silently on multi-sign-change) does not
hold: `project_irr` gates on exactly one sign change and returns `unavailable` otherwise, so the
bisection only runs on a guaranteed-bracketed single-root function — it already fails safe. pyxirr
would add a dependency without beating the existing code, so it is kept as a documented option,
not a dependency (AUDIT.md M5 records the corrected claim).

## 4. UK half-hourly meter parsing (D0010/HH CSV)

Candidates found: interview tech-test repos (kttyo/flow_files, edgarilb/oe-project — zero
maintenance), elexonpy (market data, not meter flows). **Verdict: REJECT — no mature OSS
exists; the bespoke pandas ingestion layer with golden-file tests is the state of the art.**
Spec source: MRASCO DTC catalogue (dtc.mrasco.com).

## 5. UK tariff / DUoS data

UKPN opendatasoft DUoS dashboard (official, UKPN regions only), per-DNO annual CDCM charging
statements (xlsx), OSUKED scrapers, NESO Carbon Intensity API. **Verdict: REJECT (nothing to
adopt) — maintain a hand-curated, effective-dated, provenance-linked rates pack in the
assumption library, refreshed annually.** CCL is a flat HMRC-published rate (a constant table,
not a dependency).

## 6. Google OAuth for FastAPI (server-rendered Jinja + session cookies)

| Candidate | Repo | Stars | Licence | Status |
|---|---|---|---|---|
| Authlib | github.com/authlib/authlib | ~5.4k | BSD-3 | v1.7.2 May 2026, active |
| fastapi-sso | github.com/tomasvotava/fastapi-sso | ~480 | MIT | active; history of state-validation security fixes |
| fastapi-users | github.com/fastapi-users/fastapi-users | ~6.2k | MIT | **maintenance mode** |

**Verdict: ADOPT Authlib** (Starlette client integration + itsdangerous-signed
SessionMiddleware — exactly this app's architecture; OIDC discovery/nonce/state handled by the
most-audited library). REJECT fastapi-users (maintenance mode + drags a user/DB framework).

## 7. Scroll-driven landing (vanilla JS, no build, self-hosted, <250KB, strict no-CDN)

| Candidate | Licence | Size | Notes |
|---|---|---|---|
| GSAP + ScrollTrigger | free since Webflow acquisition (2025), NOT OSI | ~40KB gz | scrubbed/pinned timelines |
| scrollama | MIT | ~5KB | IntersectionObserver step triggers, maintained |
| Vanilla IntersectionObserver + CSS sticky | platform API | 0KB | already the repo's motion.js pattern |

**Verdict: vanilla IntersectionObserver + CSS sticky (the existing `motion.js` pattern),
scrollama-style step semantics hand-rolled.** Zero new dependency, trivially CSP-compliant,
consistent with the repo's no-framework rule. GSAP only if scrub-linked physics become a
requirement (it isn't for a staged walkthrough).

## 8. Monte Carlo / P50-P90 scenario engine

numpy `random.Generator(PCG64(seed))` + `scipy.stats.qmc` (LHS/Sobol) as primitives; SALib
(MIT, active) for global sensitivity later; OpenTURNS/UQpy/chaospy = research-grade overkill.
**Verdict: REJECT frameworks — the existing deterministic-seeded scenario engine on numpy is the
right shape; consider SALib for driver-ranking when underwriting demands it.**
