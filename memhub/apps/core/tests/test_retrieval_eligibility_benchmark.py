import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.performance, pytest.mark.serial]

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "bench_retrieval_eligibility_scale.py"
if not SCRIPT.exists():
    pytest.skip(
        f"benchmark script is not part of this checkout: {SCRIPT.name}",
        allow_module_level=True,
    )
spec = importlib.util.spec_from_file_location("bench_retrieval_eligibility_scale", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
sys.modules["bench_retrieval_eligibility_scale"] = benchmark
assert spec.loader is not None
spec.loader.exec_module(benchmark)


def test_scale_probe_proves_composite_authority_index_changes_the_plan(tmp_path):
    db = sqlite3.connect(tmp_path / "probe.sqlite3", cached_statements=0)
    try:
        benchmark.create_schema(db)
        benchmark.fill_to(db, 0, 10_000)
        before = benchmark.measure(db, benchmark.FILTERED_SQL, ("g", "allowed"), repeat=3)
        db.execute(
            "CREATE INDEX retrieval_authority_jobs "
            "ON retrieval_documents(generation_id,state,source_id,event_seq)"
        )
        after = benchmark.measure(db, benchmark.FILTERED_SQL, ("g", "allowed"), repeat=3)
    finally:
        db.close()
    assert before["rows"] == after["rows"] == 100
    assert not any("source_id" in step for step in before["plan"])
    assert any("retrieval_authority_jobs" in step for step in after["plan"])
