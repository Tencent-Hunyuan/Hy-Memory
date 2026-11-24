# -*- coding: utf-8 -*-
"""Regression: p95 index must use nearest-rank, not a raw floor multiply.

sorted[ int(n * 0.95) ] picks index 19 for n=20 (the 20th / maximum value)
and index 95 for n=100 (the 96th value). Under nearest-rank, p95 is the
ceil(0.95*n)-th value, i.e. index ceil(0.95*n) - 1.
"""

from hy_memory.utils.metrics import MemoryMetrics, MetricRecord


def _stats_for(values):
    m = MemoryMetrics()
    m.clear()
    for i, v in enumerate(values):
        m.record(MetricRecord(method_name="m", duration_ms=float(v), timestamp=str(i)))
    return m.get_stats("m")


def test_p95_of_20_values():
    assert _stats_for(range(1, 21))["p95_ms"] == 19.0


def test_p95_of_100_values():
    assert _stats_for(range(1, 101))["p95_ms"] == 95.0
