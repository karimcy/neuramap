# Deployment — public hosting of the GridQ web app (W5-3)

**Status:** build + config + runbook **ready**; the live go-live actions are reserved for Olly (see
[§5 Go-live](#5-go-live-runbook-flyio-primary) — every account/terms/domain/secret/live-deploy step is
flagged `TODO[Olly]`). Closes the open item in [wave5_scope §1](wave5_scope.md). Decision: **D26**
([decisions.md](decisions.md)).

> **What gets deployed:** the Wave-5 FastAPI + Jinja2 app (`gridq.app`) — two explicit run modes
> (real-site primary via `MilpEngine`, demo opt-in), the navigable Inputs→Dispatch→Sizing→Finance→
> artifacts flow, the 3D sizing surface, honest provenance/gating throughout. The app is
> **self-contained** (its CSS + vendored Plotly + chart bundle are served from `/static`; **no CDN,
> no outbound network at runtime**) and **deterministic** (identical input → identical quote).

---

## 1. Artifacts in this repo

| File | Purpose |
|------|---------|
| [`Dockerfile`](../Dockerfile) | Canonical, host-agnostic image (`python:3.12-slim`, wheels-only, non-root, healthcheck, determinism env pins). Runs on Fly.io / Render / Railway / Cloud Run **and** locally. |
| [`requirements.txt`](../requirements.txt) | Fully-pinned runtime + `[app]` closure (62 pkgs) — the reproducible-build lock. Excludes dev/agent tooling. |
| [`.dockerignore`](../.dockerignore) | Keeps real/private data (`runs/`, `data/`, `.env`, `.claude/`) and dev cruft out of the image. |
| [`Procfile`](../Procfile) | For buildpack hosts (Render native / Railway Nixpacks / Heroku-style). |
| [`fly.toml`](../fly.toml) | Worked config for the **recommended** host (Fly.io): HTTPS, `/healthz` check, optional volume. |
| [`deploy/.env.example`](../deploy/.env.example) | Copy-paste template of every deploy env var. |

**Code added for deployment (additive, tested — `tests/test_app_deploy.py`):**
- `GET /healthz` → `200 {"status":"ok"}` — a cheap, unauthenticated probe for the host's load balancer.
- An **optional** HTTP Basic gate, **dormant unless `GRIDQ_BASIC_AUTH="user:pass"` is set** (so the
  default posture is unchanged). It guards everything except `/healthz` and `/static`. No credential
  ever lives in the repo — it is read from the env.

---

## 2. One-command local run

**With Docker (mirrors production):**
```bash
docker build -t gridq .
docker run --rm -p 8000:8000 gridq
# open http://127.0.0.1:8000   (health: http://127.0.0.1:8000/healthz)
```

**Without Docker (dev):**
```bash
pip install -e ".[app]"
python -m gridq.app            # serves http://127.0.0.1:8000
```

**Gate it locally** (try the public-URL password): `docker run --rm -p 8000:8000 -e GRIDQ_BASIC_AUTH=olly:s3cret gridq` — then `/` returns 401 without credentials, `/healthz` stays open.

---

## 3. The reproducible build (how & why)

- **`requirements.txt` is the exact, fully-pinned closure** the app needs, generated from the green
  test environment and **verified** by installing it into a clean venv and running the app smoke +
  demo-MILP test suite against it (all green). It is wheels-only installable
  (`pip install --only-binary=:all: -r requirements.txt`) — **no compiler/apt build layer is needed**
  (`highspy` statically bundles the HiGHS solver; numpy/scipy/pandas ship manylinux wheels).
- **Why pinned:** the solver stack (`linopy 0.5.8` / `highspy 1.15.1` / `xarray 2024.10.0` and the
  numeric deps `numpy`/`scipy`/`pandas`) is part of the **deterministic-reproduction contract**
  ([decisions.md D18 / D22](decisions.md)). Exact pins keep the MILP byte-reproducible across rebuilds.
- **The `linopy` "iceberg":** `import linopy` eagerly imports `linopy.remote`, which top-level-imports
  `requests` + `google-cloud-storage`, so those (plus `polars`/`polars-runtime-32`, `dask`, `fsspec`,
  `numexpr`, `Bottleneck`, …) are **required for import**, not optional. They are in the lock by
  necessity — re-check `import linopy` in a clean env before pruning any of them.
- **Determinism beyond the pins:** the Dockerfile pins `TZ=UTC`, `LANG/LC_ALL=C.UTF-8` and
  single-threaded numerics (`OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`,
  `NUMEXPR_NUM_THREADS=1`, `POLARS_MAX_THREADS=1`). The HiGHS solve is already `threads=1, seed=0` in
  code; these env pins keep numpy/scipy/pandas float-reductions order-stable so the quote is identical
  on any host CPU count and locale.
- **Editable, source-tree install:** the image installs gridq with `pip install --no-deps -e .` and
  keeps the repo layout under `/app`. This is **required** — `assumptions/` and `examples/demo_site`
  resolve relative to the package (`Path(__file__).parents[3]`), and the demo dir has no env override,
  so a flat site-packages install would break demo + assumption loading.

> **Packaging fix shipped with this work:** `pyproject.toml` was missing the
> `[project.optional-dependencies]` table header (the `app`/`dev` extras were bare `[project]` keys),
> which is invalid PEP 621 and made a clean `pip install` fail — masked only because the dev venv was
> bootstrapped under older tooling. The header was added (no dependency changes). Flagged as **C024**.

---

## 4. Choosing a host

The Docker image runs on any of these. **Recommended: Fly.io** — it builds our Dockerfile directly,
gives automatic HTTPS, has a first-class `/healthz` check, and offers a trivial persistent volume for
the run store if you want durable runs. Alternatives:

| Host | How | Notes |
|------|-----|-------|
| **Fly.io** *(primary)* | `fly deploy` with [`fly.toml`](../fly.toml) | Docker-native; volume = clean retention story. |
| **Render** | New **Web Service** → Docker, or a Blueprint; health check path `/healthz` | Managed HTTPS; dashboard-driven (no CLI). Persistent disk is paid; default FS ephemeral. |
| **Railway** | Deploy from repo (Dockerfile auto-detected) or the `Procfile` | Injects `$PORT` (handled). Ephemeral FS. |
| **Cloud Run** | `gcloud run deploy --source .` | Scales to zero; FS fully ephemeral → durable store needs GCS. More GCP setup. |

All four terminate TLS for you, so the optional Basic Auth (if enabled) is safe over HTTPS.

---

## 5. Go-live runbook (Fly.io primary)

The agent has **prepared** everything below; the marked steps are **yours to perform** — they create
accounts, accept third-party terms, register/point domains, set secrets, and push to a live public URL.

1. **`TODO[Olly]`** — Install `flyctl` and authenticate: `fly auth login` *(creates/uses your Fly account → accepts Fly's terms)*.
2. **`TODO[Olly]`** — Reserve the app + region: `fly launch --no-deploy --dockerfile Dockerfile` (or `fly apps create <name>`), then set `app`/`primary_region` in [`fly.toml`](../fly.toml).
3. *(Recommended)* **`TODO[Olly]`** — Gate the URL: `fly secrets set GRIDQ_BASIC_AUTH="user:strong-password"`. **Never** commit this. See [§6 privacy](#6-data-handling--privacy) for why gating is advised.
4. *(Optional)* **`TODO[Olly]`** — Durable run store: `fly volumes create gridq_data --size 1 --region <region>` and uncomment the `[[mounts]]` block in `fly.toml`. Default (no volume) = ephemeral (see [§6](#6-data-handling--privacy)).
5. **`TODO[Olly]`** — Deploy: `fly deploy`. Fly builds the Dockerfile, health-checks `/healthz`, and serves the app on an HTTPS URL.
6. *(Optional)* **`TODO[Olly]`** — Custom domain: `fly certs add <domain>` and point DNS *(registers/points a domain)*.
7. **Verify live:** `curl https://<app>.fly.dev/healthz` → `{"status":"ok"}`; load `/`; run a **Demo** quote; (gated) confirm `/` prompts for the password.

**Render / Railway:** point the service at this repo; they auto-detect the `Dockerfile` (or use the
`Procfile`); set health-check path `/healthz`; set `GRIDQ_BASIC_AUTH` as an env/secret in the
dashboard; deploy. **Cloud Run:** `gcloud run deploy --source .`, set `--port 8080` (Cloud Run injects
`$PORT`, which the image honours), and add `GRIDQ_BASIC_AUTH` as a secret. The same account/terms/domain
steps remain **`TODO[Olly]`**.

---

## 6. Data handling & privacy

Hard rules from [wave5_scope §5](wave5_scope.md) — preserved in the public build.

- **No real third-party data is shipped.** The image contains only the **labelled synthetic** demo
  dataset (`examples/demo_site/`); `runs/` and `data/` are excluded by [`.dockerignore`](../.dockerignore).
  A user's *own* real-mode submission is processed for *their* run only.
- **Where submissions live:** the run store at **`$GRIDQ_RUN_STORE`** (image default **`/data/runs`**).
  Each run writes `quote.json` + `site.json` + `telemetry.json` under its `quote_id`; an uploaded
  half-hourly CSV is written to **`/data/runs/uploads/`** (content-addressed). The site model carries
  the counterparty/consents — **sensitive**.
- **Retention — pick one (default is the first):**
  - **Ephemeral (default, privacy-aligned):** no volume mounted → the container filesystem resets on
    every redeploy/restart, so real uploads do **not** persist. Trade-off: a visitor's "revisit your
    run" links stop resolving after a redeploy, and on a multi-instance host a run saved by one instance
    isn't visible to another. Acceptable for a demo; **document to users that runs are not durable.**
  - **Durable:** mount a volume at `/data` (Fly `[[mounts]]`; Render/CGP persistent disk/bucket). Then
    runs persist — and you **must** protect them: the URL should be gated (Basic Auth, step 3), the host
    must not allow public indexing of the volume, and you own a deletion path.
- **Protection requirements (whichever retention):** the run store must not be world-readable or
  publicly indexed; access is via the (optionally gated) app only. **Deletion path:**
  `gridq.app.store.delete_run(quote_id)` removes a run (its quote/site/telemetry); for uploads, clear
  `/data/runs/uploads/`. There is **no automatic TTL/retention sweep yet** — if you enable durable
  storage for real users, add a scheduled cleanup and a per-user deletion affordance before inviting
  real submissions at scale (tracked as a follow-up; see [§7](#7-known-gaps--follow-ups)).
- **Gating the public URL (recommended):** real mode accepts third-party site data on an open URL.
  Setting `GRIDQ_BASIC_AUTH` restricts the demo to people you share credentials with and reduces
  exposure of the intake form. `/healthz` stays open so health checks work. Basic Auth only protects
  credentials over HTTPS — all the recommended hosts terminate TLS.
- **No secrets in the repo** (CLAUDE.md §2): `GRIDQ_BASIC_AUTH` and any future keys are host secrets;
  [`deploy/.env.example`](../deploy/.env.example) is a template only and is excluded from the image.

---

## 7. Verification

**Already verified in this work (without Docker, which isn't available in the build env):**
- The pinned `requirements.txt` installs into a clean venv **wheels-only** and the app smoke +
  **demo-MILP** suites pass against it (`tests/test_app_smoke.py`, `tests/test_app_demo.py`,
  `tests/test_app_deploy.py`) — proving the closure is complete and the real solver runs in it.
- `assumptions/` and `examples/demo_site` resolve under the editable, source-tree install.
- The **exact** container start command (`uvicorn gridq.app.app:app --host 0.0.0.0 --port ${PORT:-8000}`)
  boots a real server; `/healthz`→200 unauthenticated, gating returns 401 without creds / 200 with
  creds, `/static` stays open.

**`TODO[Olly]` — in-container smoke (run once Docker is available on your machine):**
```bash
docker build -t gridq .
docker run --rm -d -p 8000:8000 --name gridq gridq
curl -fsS http://127.0.0.1:8000/healthz                 # -> {"status":"ok"}
# Run a Demo quote in the browser, confirm the MILP solves and the 3D surface renders.
docker rm -f gridq
```
Optional determinism spot-check: submit the same Demo run twice and confirm an identical `quote_id`
(the app's determinism guarantee; the thread/TZ env pins make it hold on any host).

---

## 8. Known gaps / follow-ups

- **No automatic retention sweep / per-user deletion UI.** `delete_run()` exists but is not yet wired
  to a web action or a TTL job. Add before inviting real submissions at scale (see §6).
- **Image size** ≈ base + ~300 MB site-packages (scipy/pandas/numpy/the linopy cloud subtree). Fine for
  these hosts; a multi-stage build or pruning linopy's unused cloud-IO deps could slim it later (verify
  `import linopy` still works first).
- **Single instance assumed** for the run store. Multi-instance durable storage needs shared storage
  (volume per instance won't share runs); the ephemeral default sidesteps this.
