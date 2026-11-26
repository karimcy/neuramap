# Wave 5 Acceptance Report — the Karim-ready gate (W5-5)

**Date:** 2026-06-25 · **Author:** Olly (`agent/integration`) · **Precondition:** W5-0…W5-4 (all built & green).
**Scope:** prove the GridQ platform is coherent, honest, and demo-ready, and stand up a publicly
accessible end-to-end view. Method: read the full `src/gridq/`, `tests/`, `docs/`, the System
Requirements (acceptance tests 1 & 2) and `docs/manual_benchmark.md`; run the quality bar; drive the
real running app end-to-end over HTTP; deploy it publicly; adversarially re-verify the findings.

---

## ✅ Ready to show Karim: **YES** — with two judgment calls flagged below

The engineering acceptance gate is **green**: the platform is coherent, deterministic, honest, and
demo-ready, and it is **live on a public URL** (below). The nuance Karim must hear stated plainly —
because it *is* the product's integrity — is this:

> **GridQ is bankable-*ready*, not yet bankable-*confirmed*.** Every output already carries the
> bankable *shape* — P50 **and** P90, a DSCR machinery, firm-vs-gated separation, full provenance —
> and **never** ships a point estimate. But on the only sourced assumption release today (`gb_v0_1`,
> all placeholders) every output is **honestly labelled `not_bankable`**, and DSCR is shown
> **"unavailable"** rather than guessed, because no debt gearing has been sourced. The moment
> confirmed assumptions + debt terms land, the *same* pipeline produces a genuinely bankable pack
> **with no code change.** That honesty is the feature a lender's credit desk is buying.

**Two judgment calls for Olly/Karim before wide sharing** (neither blocks a demo):

1. **Open URL + real-prospect data.** Per Olly's instruction the public URL is left **open** (no
   password) and offers a real-prospect prefill (Robert Thomas Farms). That real commercial tariff +
   counterparty identity is therefore visible to anyone with the link. The committed repo/image stay
   clean (the data is gitignored), but the **live instance exposes it by deliberate choice.** Decide
   whether to gate (`GRIDQ_BASIC_AUTH`, one env var) or anonymise the public label before sharing
   beyond Karim.
2. **Branded URL needs one action.** The working link today is a Cloudflare tunnel (random hostname).
   A clean `neura`-branded URL needs **either** you opening a port in the Hetzner Cloud Firewall
   (then `http://neurauk.49-13-2-99.sslip.io` serves directly) **or** a Cloudflare token for a named
   tunnel on `neura.energy`. See §5.

---

## 🌐 The public platform

| | |
|---|---|
| **Live URL (working now)** | **https://continuing-discussion-tire-proudly.trycloudflare.com** |
| **Health probe** | `GET /healthz → 200 {"status":"ok"}` (verified over the public internet) |
| **What's there** | Landing → mode chooser · **Demo** (synthetic, full 3D surface) · **Real site** (upload your own HH) · **Try a real site** (Robert Thomas prefill) · Inspect (provenance · sized system · 3D surface · dispatch · firm-vs-gated · finance) · System spec · Memo + Proposal · Telemetry |
| **Hosting** | This VPS (`olly-dev`, 49.13.2.99). `gridq.service` (uvicorn on `0.0.0.0:80`) + `gridq-tunnel.service` (cloudflared) — both **systemd, `Restart=always`, enabled at boot**, so they survive logout/reboot. |
| **Determinism/privacy env** | The service pins `TZ=UTC`, single-thread numerics (OMP/OPENBLAS/MKL/NUMEXPR/POLARS=1) and an out-of-repo run store `$GRIDQ_RUN_STORE=/var/lib/gridq/runs`. |

> The cloudflared hostname is stable while the tunnel process runs; if it restarts it re-rolls. For a
> permanent branded URL, see §5.

---

## The six acceptance checks

| # | Check | Verdict |
|---|-------|---------|
| 1 | **Bankable** (P50/P90 + DSCR; no point estimates; `needs_review` flagged) | ✅ **Pass** (shape correct; honestly `not_bankable` on placeholders) |
| 2 | **Fast** (telemetry: time-to-quote + human-minutes vs manual benchmark; realised-vs-underwritten) | ✅ **Pass** (capture live from quote 1; realised loop structurally present, no operating data yet) |
| 3 | **Determinism & provenance** | ✅ **Pass** (verified at runtime: identical input → identical `quote_id`) |
| 4 | **Security / privacy** | ✅ **Pass** (repo/image clean) · ⚠️ one **deliberate** open-URL exposure (judgment call #1) |
| 5 | **Honesty sweep** (no invented numbers; no LLM on pricing path; firm-vs-gated; UI labels) | ✅ **Pass** |
| 6 | **Green bar** (pytest / ruff / mypy) | ✅ **Pass** (568 tests; ruff + ruff-format + mypy clean) |

### 1 · Bankable (System Requirements, acceptance test 1)
*"P50/P90 and DSCR are mandatory; point estimates are not deliverables."*

- **P50 and P90 are mandatory, not optional.** `FinancialCase` defines both `p50_cashflows` and
  `p90_cashflows` as required fields with a model-validator that raises if either is missing or
  misaligned ([schemas/finance.py](../src/gridq/schemas/finance.py)). The output view-model and HTML
  report surface both ([outputs/viewmodel.py](../src/gridq/outputs/viewmodel.py) `CashflowsView`); the
  UI showed both P50 and P90 on the Inspect screen in the dry-run.
- **DSCR is measured on the P90 case and is `unavailable`, never guessed, when debt is absent.**
  `build_debt_terms()` returns `(None, warnings)` naming the missing keys when gearing/rate/tenor are
  absent; `DscrResult.available=False`, `min_dscr=None`, and `check_covenant().passes=None`
  ([finance/debt.py](../src/gridq/finance/debt.py)). Asserted by
  `test_finance_debt.py::test_build_debt_terms_unavailable_in_v1` and
  `test_assess_debt_unavailable_without_terms`.
- **`needs_review` / placeholder force not-bankable.** `AssumptionStatus ∈ {confirmed, placeholder,
  needs_review}`; a non-`confirmed` status emits a warning and holds `bankable=False`
  ([finance/financial_model.py](../src/gridq/finance/financial_model.py),
  [assumptions/models.py](../src/gridq/assumptions/models.py)). `gb_v0_1` is **0 confirmed**, so every
  output is `not_bankable`. The gate is real, not hard-coded: with an all-confirmed library + debt +
  a passing covenant the case flips bankable
  (`test_finance_cashflows.py::test_bankable_only_when_confirmed_result_bankable_and_covenant_passes`).
- **No point-estimate-only output ships.** Every figure is injected from the stamped `QuoteRecord`;
  unavailable metrics render *"unavailable — pending …"*, never a zero or a guess. Confirmed in the
  demo + upload runs (DSCR shown as a real `1.43×` only on the demo's **labelled illustrative** debt).

### 2 · Fast (System Requirements, acceptance test 2)
*"Telemetry measures time-to-quote and human-minutes against the manual benchmark."*

- **Telemetry ships from the first quote** (not retrofitted). `TelemetryEvent` carries `human_minutes`,
  `installer_iterations`, `data_tier`, `missing_data_count`, `contributor`, `created_at`
  ([schemas/telemetry.py](../src/gridq/schemas/telemetry.py)); `quotes/telemetry.py` emits one per run
  (the demo/upload runs each persisted a `telemetry.json`). System time-to-quote is effectively
  instant (the full-year MILP solves in ~2.5 s; see §6).
- **Manual benchmark = the control group.** `ManualBenchmarkRecord` requires a real positive
  `human_minutes` and converts into the **same** `TelemetryEvent` stream marked
  `event_type="manual_benchmark"` ([telemetry/manual_benchmark.py](../src/gridq/telemetry/manual_benchmark.py),
  [docs/manual_benchmark.md](manual_benchmark.md)) — an apples-to-apples dataset for the
  cost-compression thesis. Honest by design: a row with no measured effort is not a benchmark.
- **Realised-vs-underwritten loop:** structurally present and shipping from day one — the append-only
  `quote_mode` lifecycle (`draft→indicative→underwritten→contracted→operating`, `lapsed` terminal)
  with `status_history` ([quotes/lifecycle.py](../src/gridq/quotes/lifecycle.py)) plus the
  manual↔system `linked_quote_id` link. **Honest status:** there is **no realised/operating data yet**
  (no live sites), so the loop is built and instrumented but not yet exercised on a real outcome —
  exactly what you'd expect pre-pilot. Not a gap in the platform; a gap in the world.

### 3 · Determinism & provenance
- **Verified at runtime:** two identical Demo submissions produced the **identical** `quote_id`
  `quote_run_714ab382f4e0ac94` — locally **and** again over the public URL. (The DSCR PASS/IRR are
  byte-stable across runs.)
- **Mechanism:** canonical sorted-key JSON hashing (`provenance/hashing.py`); `RunContext` stamps
  `input_hash`, `assumption_version`, `code_version`, `schema_version`, `seed`, `output_hash`,
  `reproduction_env` (`provenance/run_context.py`); `quote_id` derives from the canonical input
  **excluding** `created_at` and the additive `sizing_grid`/`dispatch_profile`
  ([quotes/assembly.py](../src/gridq/quotes/assembly.py)). Golden tests:
  `test_provenance.py`, `test_quotes_assembly.py::test_hash_and_id_deterministic_and_created_at_invariant`.
- The Inspect screen surfaces the full provenance stamp (run id, input hash, assumption/code/schema
  versions, seed) on every run screen.

### 4 · Security / privacy
- **No secrets in the repo.** A scan of tracked files found no keys/tokens; the only matches are the
  Basic-Auth code that reads creds from the `GRIDQ_BASIC_AUTH` **env var** (`secrets.compare_digest`,
  no credential in the repo) ([app/app.py](../src/gridq/app/app.py)).
- **No real third-party data in the committed build.** `.gitignore` and `.dockerignore` exclude
  `data/`, `runs/`, `examples/real/`, `.env`, `.claude/`; only the **synthetic** `examples/demo_site`
  ships. The Robert Thomas facts + the new prefill live under the gitignored `examples/real/`.
- **Submission handling matches `docs/deployment.md`:** runs persist under `$GRIDQ_RUN_STORE`
  (here `/var/lib/gridq/runs`, out of the repo); uploads are content-addressed; `store.delete_run()`
  is the deletion path ([app/store.py](../src/gridq/app/store.py)). The optional Basic-Auth gate is
  dormant (open posture) by Olly's choice.
- ⚠️ **Deliberate divergence (judgment call #1):** the live instance is open and surfaces a real
  prospect's transcribed tariff/identity via the prefill. Repo/image stay clean; the *running site*
  exposes it by choice. Easy reversals: set `GRIDQ_BASIC_AUTH` (gate) or remove
  `examples/real/haywood_oaks_prefill.json` (hide the example).

### 5 · Honesty sweep
- **No LLM and no network on the pricing path.** A grep across `finance/`, `optimisation/`,
  `quotes/`, `value_stack/`, `scenarios/`, `sizing/`, `dispatch/`, `provenance/`, `outputs/` finds
  **zero** LLM SDKs and zero `requests/httpx/urllib/socket` calls. `linopy` imports `requests` at the
  package level (the documented "iceberg"), but no gridq pricing module references it — the MILP
  (HiGHS, `threads=1, seed=0`) is pure local numerics.
- **No invented numbers.** `review_gates` runs **GREEN** (banned-string scan of core modules clean of
  invented-number / fake-bankable smells; `src/gridq/dev/review_gates.py`). Numbers come only from the
  assumption library or are labelled unavailable.
- **Firm vs gated correct.** Priced floor = firmness ∈ `{FIRM, CONTRACTED}`; conditional/gated upside
  held out of the floor (`finance` `_PRICED_FIRMNESS`; value-stack gating). The Inspect screen renders
  a "Value vs firm" split with the priced-floor table separate from gated upside.
- **UI labels correct.** Indicative-vs-underwritten badge, the firm-vs-gated split, the NOT-BANKABLE
  banner and the illustrative-demo badge all rendered correctly in the dry-run. Arithmetic is kept out
  of the UI (AST-enforced: `test_app_templates_no_arithmetic.py`, `test_outputs_no_arithmetic_in_html.py`).

### 6 · Green bar
- **`pytest`:** 568 tests collected; all pass (incl. the full-year 17,472-HH MILP benchmark in ~2.5 s).
- **`ruff check .`:** All checks passed. **`ruff format --check .`:** 153 files already formatted.
- **`mypy`:** Success — no issues in 101 source files.
- Run on `/root/.venv` (Python 3.12). Verified on the baseline **and** after the W5-5 changes below.

---

## Demo dry-run (end-to-end through the UI, incl. the 3D surface)

All three were driven over HTTP against the live server and the persisted records were re-read.

1. **Demo run (synthetic, real engine).** `POST /run/demo` → Inspect renders the populated **3D sizing
   surface** (power × duration × levered-return, candidate markers + a distinct *Chosen optimum*
   marker; screenshot captured), the representative-day dispatch, P50/P90 cashflows, DSCR-vs-covenant
   (1.43× PASS on illustrative debt), the firm-vs-gated split, ranked data-asks and the full provenance
   stamp — sized system 124.2 kW × 4.0 h. `/spec`, `/artifacts`, `/telemetry` all 200. Badged
   *illustrative / not bankable* throughout.
2. **Upload-your-own-data (the operational path).** A 12-month HH CSV uploaded through the **real-site**
   form ran the **real MILP to `optimal`**, producing a **9-candidate sizing grid + dispatch profile +
   surface**, and came back honestly `not_bankable` (placeholder assumptions, no sourced debt). This is
   the live path a real visitor uses.
3. **Determinism double-run.** Identical `quote_id` on repeat (see §3).

> **On the "real-data run".** The real operational model (confirmed with Olly) is **front-end upload**:
> the half-hourly series arrives through the platform, not pre-loaded on the server. The real Robert
> Thomas HH CSV is intentionally **not** on the box (only the gitignored markdown facts are). The real
> path is therefore proven via the upload run above; the Robert Thomas site is offered as a real-mode
> **prefill** (real tariff/capacity/connection) that runs the full MILP + surface the moment its HH
> file is uploaded — nothing is fabricated to fill the gap.

---

## New in W5-5 (additive, tested)

To make the upload-first real-data path prominent and to honour the "include the Robert Thomas site"
ask **without fabricating a load series**:

- **`examples/real/haywood_oaks_prefill.json`** (gitignored): the real, transcribed invoice facts
  (postcode NG21 0QA, SmartFix day 20.36p / night 16.5p per kWh, £25.61/day standing, 250 kVA
  authorised capacity → MIC, DNO NGED_EM, Robert Thomas Farms Ltd). **No HH series; unknown fields
  blank** (honest data-asks).
- **`app/demo.py::load_haywood_oaks_prefill()`** — loads it if present, else `None` (degrades to a
  blank form; never a 500).
- **`GET /real/haywood-oaks`** — the real-mode intake **pre-filled** from the above, with the HH-upload
  field front-and-centre and a "REAL EXAMPLE" provenance banner; submits to the unchanged `/run/real`
  (real `MilpEngine`, no illustrative debt).
- **Landing card** ("Try a real site") shown only when the example file is installed → the committed
  public build stays clean.
- **Tests:** `tests/test_app_examples.py` (loader absent/parse/bad-json + the route always serves the
  intake form with the upload field) — hermetic, CI-safe.

This invents nothing: real metadata in, the load shape supplied by the user's own upload, unknowns
surfaced as ranked data-asks.

---

## Final fixes / recommendations before showing Karim

1. **Decide the open-URL posture (judgment call #1).** For a Karim-only demo, the open URL is fine.
   Before sharing wider, either `fly/host secrets set GRIDQ_BASIC_AUTH="user:pass"` (gate) or drop the
   Robert Thomas prefill (anonymise). One env var / one file.
2. **Branded durable URL (judgment call #2).** Pick one: **(a)** open ports 80/443 in the **Hetzner
   Cloud Firewall** → `http://neurauk.49-13-2-99.sslip.io` serves directly from this box (uvicorn is
   already listening on `0.0.0.0:80`; add Caddy for auto-HTTPS); or **(b)** give me a **Cloudflare API
   token** for `neura.energy` and I'll stand up a named tunnel at e.g. `gridq.neura.energy`. Either is
   ~10 minutes.
3. **No code blockers found.** All six checks pass; the platform is internally coherent (Waves 1–5
   harmonised; no contract drift; SSOT intact).

---

## Sign-off

**Acceptance: GREEN. Ready to show Karim: YES** — present it as a deterministic, honest, demo-ready
platform that is *bankable-ready* and will produce a confirmed-bankable pack the instant real
assumptions + debt are sourced. Public URL is live. The two judgment calls above are about *exposure*,
not correctness.

*Work left uncommitted in the shared working tree for review, per the parallel-lane convention
(HEAD flips across lanes).*
