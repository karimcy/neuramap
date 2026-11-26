# syntax=docker/dockerfile:1
# GridQ public web app — reproducible deployable image (W5-3). See docs/deployment.md.
#
# Properties this image guarantees:
#   * Self-contained: the app embeds its own CSS / WebGL chart bundle — no CDN, no outbound
#     network at runtime.
#   * Deterministic: the MILP solver stack is pinned (requirements.txt; D18/D22) AND BLAS/OpenMP
#     threads + TZ/locale are pinned below, so an identical input → identical quote on any host CPU.
#   * Wheels-only: every dependency installs from a manylinux wheel (highspy statically bundles
#     HiGHS), so NO compiler / apt build layer is needed.
FROM python:3.12-slim

# --- determinism + hygiene --------------------------------------------------
# Single-threaded numerics keep float reductions order-stable (the shipped HiGHS solve is already
# threads=1/seed=0 in code; this covers numpy/scipy/pandas/numexpr/polars). UTC + C locale keep
# timestamp/DUoS-band logic stable regardless of the host's clock/locale.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    POLARS_MAX_THREADS=1 \
    GRIDQ_ASSUMPTIONS_DIR=/app/assumptions \
    GRIDQ_RUN_STORE=/data/runs

WORKDIR /app

# 1) Install the fully-pinned runtime + [app] closure as WHEELS ONLY. `--only-binary=:all:` makes a
#    missing/yanked wheel fail the build loudly instead of silently compiling from source.
COPY requirements.txt ./
RUN pip install --only-binary=:all: -r requirements.txt

# 2) Install gridq itself, EDITABLE and in its source-tree layout. Editable (not site-packages) is
#    required: assumptions/ and examples/demo_site resolve relative to the package via
#    Path(__file__).parents[3], so the package must stay a sibling of those repo-root dirs.
#    `--no-deps` => pip trusts the lock above and never re-resolves a version.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY assumptions/ ./assumptions/
COPY examples/ ./examples/
RUN pip install --no-deps -e .

# 3) Drop to a non-root user. The gitignored run store lives on a writable, mountable path
#    (/data) — ephemeral by default; mount a volume there for durable runs (see docs/deployment.md).
RUN useradd --uid 10001 --create-home app \
 && mkdir -p /data/runs \
 && chown -R app:app /data
USER app

EXPOSE 8000

# python:3.12-slim has no curl/wget — probe /healthz with stdlib urllib, honouring $PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=3).status==200 else 1)"

# Bind all interfaces and honour the platform's $PORT (Render/Railway/Cloud Run inject it; Fly +
# local default to 8000). Shell form so ${PORT} expands.
CMD uvicorn gridq.app.app:app --host 0.0.0.0 --port ${PORT:-8000}
