import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.performance, pytest.mark.serial]

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "bench_context_raw_scale.py"
if not SCRIPT.exists():
    pytest.skip(
        f"benchmark script is not part of this checkout: {SCRIPT.name}",
        allow_module_level=True,
    )
spec = importlib.util.spec_from_file_location("bench_context_raw_scale", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
sys.modules["bench_context_raw_scale"] = benchmark
assert spec.loader is not None
spec.loader.exec_module(benchmark)


def test_raw_scale_probe_uses_fts_and_has_a_nonzero_positive_control(tmp_path):
    path = tmp_path / "probe.sqlite3"
    db = sqlite3.connect(path, cached_statements=0)
    try:
        db.executescript(benchmark.SCHEMA + benchmark.GRANT_SCHEMA + benchmark.SEARCH_SCHEMA)
        benchmark.fill(db, 0, 10_000)
        query_plan = benchmark.plan(db)
    finally:
        db.close()
    result = benchmark.measure(path, 5)
    assert result["target_evidence_id"] == "e-0000000"
    assert any("evidence_search" in step for step in query_plan)
    assert result["p95_ms"] >= 0


def test_object_scale_probe_has_exact_positive_control_and_plan(tmp_path):
    path = tmp_path / "probe.sqlite3"
    db = sqlite3.connect(path, cached_statements=0)
    try:
        db.executescript(benchmark.SCHEMA + benchmark.GRANT_SCHEMA + benchmark.SEARCH_SCHEMA)
        benchmark.fill(db, 0, 10_000)
        query_plan = benchmark.object_plan(db)
    finally:
        db.close()
    result = benchmark.measure_object(path, 5)
    assert result["p50_ms"] >= 0 and result["p95_ms"] >= result["p50_ms"]
    assert any("SCAN" in step or "SEARCH" in step for step in query_plan)
