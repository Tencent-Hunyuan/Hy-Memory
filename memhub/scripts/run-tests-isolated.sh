#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_HOME="${ONE_MEMORY_TEST_REAL_HOME:-$(python3 "$ROOT/scripts/resolve-real-home.py")}"
# Git Bash resolves /tmp inside the user profile, which the isolation guard
# below refuses; use the repo's gitignored build/ there instead.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) TEST_TMP_BASE="$ROOT/build" ;;
  *) TEST_TMP_BASE="${TMPDIR:-/tmp}" ;;
esac
mkdir -p "$TEST_TMP_BASE"
RUN_ROOT="$(mktemp -d "$TEST_TMP_BASE/one-memory-tests.XXXXXX")"
TEST_CACHE_ROOT="${ONE_MEMORY_TEST_CACHE_ROOT:-$REAL_HOME/.cache}"
CORE_PORT=$((25000 + ($$ % 10000)))
BROKER_PORT=$((CORE_PORT + 10000))
LEASE_FILE="${ONE_MEMORY_TEST_LEASE_FILE:-}"
LEASE_LINE="One Memory isolated tests pid=$$ | $(date '+%Y-%m-%d %H:%M:%S %Z') | ports ${CORE_PORT}/${BROKER_PORT} | ${RUN_ROOT}"

mkdir -p "$RUN_ROOT/home" "$RUN_ROOT/data" "$RUN_ROOT/config" "$RUN_ROOT/tmp"
mkdir -p "$TEST_CACHE_ROOT/corepack" "$TEST_CACHE_ROOT/one-memory-bin"

PINNED_PNPM="$REAL_HOME/.cache/corepack/v1/pnpm/11.24.0/bin/pnpm.cjs"
if [[ -f "$PINNED_PNPM" ]]; then
  printf '#!/bin/sh\nexec node %q "$@"\n' "$PINNED_PNPM" \
    > "$TEST_CACHE_ROOT/one-memory-bin/pnpm"
  chmod 0755 "$TEST_CACHE_ROOT/one-memory-bin/pnpm"
  export PATH="$TEST_CACHE_ROOT/one-memory-bin:$PATH"
fi

export ONE_MEMORY_TEST_ISOLATED=1
export ONE_MEMORY_TEST_REAL_HOME="$REAL_HOME"
export HOME="$RUN_ROOT/home"
# os.homedir() reads USERPROFILE, not HOME, on Windows.
export USERPROFILE="$RUN_ROOT/home"
export ONE_MEMORY_DATA_DIR="$RUN_ROOT/data"
export ONE_MEMORY_SOURCE_HOME="$RUN_ROOT/home"
export ONE_MEMORY_HOST_HOME="$RUN_ROOT/home"
export XDG_CONFIG_HOME="$RUN_ROOT/config"
export TMPDIR="$RUN_ROOT/tmp"
# os.tmpdir() reads TEMP/TMP before TMPDIR on Windows.
export TEMP="$RUN_ROOT/tmp"
export TMP="$RUN_ROOT/tmp"
export COREPACK_HOME="$TEST_CACHE_ROOT/corepack"
export ONE_MEMORY_PORT="$CORE_PORT"
export ONE_MEMORY_CORE_PORT="$CORE_PORT"
export ONE_MEMORY_RUNTIME_PORT="$CORE_PORT"
export ONE_MEMORY_BROKER_PORT="$BROKER_PORT"

cleanup() {
  if [[ -n "$LEASE_FILE" && -f "$LEASE_FILE" ]]; then
    lease_tmp="${LEASE_FILE}.pid-$$"
    awk -v line="$LEASE_LINE" '$0 != line' "$LEASE_FILE" > "$lease_tmp"
    mv "$lease_tmp" "$LEASE_FILE"
  fi
  case "$RUN_ROOT" in
    */one-memory-tests.*) /bin/rm -rf -- "$RUN_ROOT" ;;
    *) printf 'refusing to clean unexpected test root: %s\n' "$RUN_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

if [[ -n "$LEASE_FILE" && -d "$(dirname "$LEASE_FILE")" ]]; then
  touch "$LEASE_FILE"
  printf '%s\n' "$LEASE_LINE" >> "$LEASE_FILE"
fi

node "$ROOT/scripts/assert-test-isolation.mjs"

mode="${1:-workspace}"
if [[ $# -gt 0 ]]; then shift; fi
cd "$ROOT"

run_core_tests() {
  local suite="$1"
  local marker="$2"
  shift 2
  local stamp report_dir status
  stamp="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
  report_dir="${ONE_MEMORY_TEST_REPORT_DIR:-$ROOT/build/test-reports/core/${suite}-${stamp}}"
  mkdir -p "$report_dir"
  python3 "$ROOT/scripts/generate_core_test_index.py" --check
  if [[ "$suite" != "core-audit" ]]; then
    printf 'Core test report: %s\n' "$report_dir"
  fi
  local -a command=(
    uv run --directory apps/core pytest
    --durations=100
    --durations-min=0.05
    --junitxml="$report_dir/junit.xml"
    --core-report-dir="$report_dir"
    --core-suite="$suite"
  )
  if [[ -n "$marker" ]]; then
    command+=(-m "$marker")
  fi
  command+=("$@")
  if [[ "$suite" == "core-audit" ]]; then
    if "${command[@]}" >"$report_dir/pytest-collection.txt"; then
      status=0
    else
      status=$?
    fi
    python3 "$ROOT/scripts/audit_core_test_design.py" "$report_dir" "$ROOT/apps/core/tests"
    python3 "$ROOT/scripts/summarize_core_test_report.py" "$report_dir"
    return "$status"
  fi
  if "${command[@]}"; then
    status=0
  else
    status=$?
  fi
  python3 "$ROOT/scripts/audit_core_test_design.py" "$report_dir" "$ROOT/apps/core/tests"
  return "$status"
}

case "$mode" in
  workspace) pnpm -r test "$@" ;;
  core) run_core_tests core "" "$@" ;;
  core-fast) run_core_tests core-fast "unit or contract" "$@" ;;
  core-integration) run_core_tests core-integration "integration" "$@" ;;
  core-pr) run_core_tests core-pr "not performance and not live" "$@" ;;
  core-performance) run_core_tests core-performance "performance" "$@" ;;
  core-live) run_core_tests core-live "live" "$@" ;;
  core-all) run_core_tests core-all "" "$@" ;;
  core-audit) run_core_tests core-audit "" --collect-only --quiet --core-classification-enforce "$@" ;;
  check) bash scripts/check.sh "$@" ;;
  node) node "$@" ;;
  pnpm) pnpm "$@" ;;
  self-test)
    printf 'test isolation ready: HOME=%s data=%s ports=%s/%s COREPACK_HOME=%s\n' \
      "$HOME" "$ONE_MEMORY_DATA_DIR" "$ONE_MEMORY_PORT" "$ONE_MEMORY_BROKER_PORT" "${COREPACK_HOME:-}"
  ;;
  *)
    printf '%s\n' \
      "usage: $0 {workspace|core|core-fast|core-integration|core-pr|core-performance|core-live|core-all|core-audit|check|node|pnpm|self-test} [args...]" >&2
    exit 64
    ;;
esac
