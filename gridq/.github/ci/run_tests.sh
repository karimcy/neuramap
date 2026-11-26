#!/usr/bin/env bash
# GridQ test tiers (WP-PLAT-01, spec E-05). The one definition that CI and a developer both run.
#
#   .github/ci/run_tests.sh fast   [pytest args]    the fast tier: every module not listed below
#   .github/ci/run_tests.sh slow   [pytest args]    SLOW_MODULES, one module per worker
#   .github/ci/run_tests.sh module <path> [args]    one HEAVY_MODULES entry, alone and serially
#   .github/ci/run_tests.sh list heavy|slow         print a list, one path per line
#
# The tiers are defined by measured cost, never by whether a test passes. A failing test stays in
# whichever tier its cost puts it in. The full suite is fast + slow + every heavy module, and the
# nightly workflow runs all of it.
#
# HEAVY_MODULES run a full demo quote (the real MILP sizing loop) at IMPORT time: about 3 minutes
# per solve on an Apple M2, twice in the golden-pin module. Under pytest-xdist every worker imports
# every module it collects, so these would be solved once per worker. Each runs alone instead.
# tests/test_determinism_golden.py holds the golden pins and runs on every PR.
#
# SLOW_MODULES each measured at least 60 s of test time (JUnit, full suite, -n 4, 2026-11-16, on
# an Apple M2 under load; figures beside each). Their tests run full sizing searches. --dist
# loadfile keeps a module on one worker, so its module-scoped fixtures are solved once.
#
# Re-measure when the suite changes: run `fast` with --durations=0 and move any module whose total
# reaches 60 s into SLOW_MODULES. The fast tier's CI step times out at the E-05 bound (10 min).
#
# Run from the repository root. PYTHON defaults to python on PATH.
set -euo pipefail

HEAVY_MODULES=(
  tests/test_determinism_golden.py
  tests/test_app_demo.py
  tests/test_app_charts_wiring.py
  tests/test_app_auth_scoping.py
)

SLOW_MODULES=(
  tests/test_e2e_examples_real.py                  # 817 s
  tests/test_optimisation_engine.py                # 509 s
  tests/test_dispatch_profile.py                   # 406 s
  tests/test_app_view_islands.py                   # 394 s
  tests/test_two_stage_sizing.py                   # 387 s
  tests/test_goal_ws3_pipeline.py                  # 357 s
  tests/test_app_demo_progress.py                  # 322 s
  tests/test_optimisation_export.py                # 318 s
  tests/test_gate_3b_wired.py                      # 286 s
  tests/test_customer_bill.py                      # 268 s
  tests/test_register_moves_no_priced_number.py    # 251 s
  tests/test_quote_run_result_carries_gate_3b.py   # 185 s
  tests/test_run_always_draws_the_dispatch_day.py  # 168 s
  tests/test_sizing_grid.py                        # 165 s
  tests/test_optimisation_dispatch_energy.py       # 163 s
  tests/test_no_spec_model_disclosures.py          # 161 s
  tests/test_priced_floor_arithmetic.py            # 158 s
  tests/test_windowed_levy_capacity_market.py      # 147 s
  tests/test_cli_run.py                            # 104 s
  tests/test_hgv_provisioning_e2e.py               # 95 s
  tests/test_before_bill_without_dispatch.py       # 62 s
  tests/test_optimisation_provisioning.py          # 60 s
)

PYTHON="${PYTHON:-python}"
tier="${1:-}"
shift || true

case "$tier" in
  fast)
    ignores=()
    for m in "${HEAVY_MODULES[@]}" "${SLOW_MODULES[@]}"; do ignores+=("--ignore=$m"); done
    exec "$PYTHON" -m pytest -p no:cacheprovider -n "${PYTEST_WORKERS:-auto}" "${ignores[@]}" "$@"
    ;;
  slow)
    exec "$PYTHON" -m pytest -p no:cacheprovider -n "${PYTEST_WORKERS:-auto}" --dist loadfile \
      "${SLOW_MODULES[@]}" "$@"
    ;;
  module)
    path="${1:?usage: run_tests.sh module <path> [pytest args]}"
    shift
    exec "$PYTHON" -m pytest -p no:cacheprovider "$path" "$@"
    ;;
  list)
    case "${1:-}" in
      heavy) printf '%s\n' "${HEAVY_MODULES[@]}" ;;
      slow) printf '%s\n' "${SLOW_MODULES[@]}" ;;
      *) echo "usage: $0 list heavy|slow" >&2; exit 2 ;;
    esac
    ;;
  *)
    echo "usage: $0 fast|slow|module <path>|list heavy|slow [pytest args]" >&2
    exit 2
    ;;
esac
