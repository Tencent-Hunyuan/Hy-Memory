#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

node scripts/assert-test-isolation.mjs

python3 scripts/verify_structure.py
python3 scripts/generate_core_test_index.py --check
python3 scripts/verify_zero_collection.py
python3 scripts/verify_zero_collection.py --self-test

# Retrieval tests require the pinned built-in model. Fetching is idempotent and
# verifies every asset hash before the full suite can classify failures.
pnpm fetch:embedding-model
uv run --directory apps/core python -c "from one_memory.embedding_assets import assets_present, verify_assets; assert assets_present(); verify_assets()"
uv run --directory apps/core python -c "from one_memory.bm25_assets import verify_bm25_assets; verify_bm25_assets()"

pnpm lint
pnpm typecheck
pnpm -r test
pnpm test:release-scripts
pnpm build
python3 scripts/verify_zero_collection.py --artifacts

uv run --directory apps/core ruff check .
uv run --directory apps/core ruff format --check .
uv run --directory apps/core mypy
uv run --directory apps/core pytest
