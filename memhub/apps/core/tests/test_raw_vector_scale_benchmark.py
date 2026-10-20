import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.performance, pytest.mark.serial]

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "bench_raw_vector_scale.py"
if not SCRIPT.exists():
    pytest.skip(
        f"benchmark script is not part of this checkout: {SCRIPT.name}",
        allow_module_level=True,
    )
spec = importlib.util.spec_from_file_location("bench_raw_vector_scale", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
sys.modules["bench_raw_vector_scale"] = benchmark
assert spec.loader is not None
spec.loader.exec_module(benchmark)


def test_vector_scale_probe_uses_real_512_dimension_filtered_search():
    result = benchmark.run((100,), 5)
    assert result["dimensions"] == 512
    layer = result["results"][0]
    assert layer["chunks"] == 100 and layer["allowlist"] == 64
    assert layer["correct"] and layer["returned_counts"] == [64] * 5
    assert layer["p95_ms"] >= 0 and layer["directory_bytes"] > 0
