# -*- coding: utf-8 -*-
"""跨分钟结束时，完成数不能被当成"空桶"丢掉。

`sys1_end` / `sys2_end` 先累加 completed/failed 与 timing 累计，再调用
`_maybe_rotate_bucket()`。所以一个"上一分钟开始、这一分钟结束"的请求，会把
completed 记在**新桶**里，而新桶的 started 仍是 0。

`_is_bucket_empty` 只检查 started 与 vdb/graph 计数，于是这个桶被判空：
既不进 `_pending_flush`、也不会被 `flush()` 写库，completed 与 timing 直接消失。
成功率的分子就这么少掉一批真实请求。
"""

from hy_memory.metrics import MetricsCollector


def _collector_with_stale_bucket() -> MetricsCollector:
    """模拟一个在上一分钟建好、正要被翻页的当前桶。"""
    collector = MetricsCollector()
    collector._current_minute = "2000-01-01T00:00"
    return collector


def test_a_sys1_completion_that_lands_after_the_boundary_is_queued():
    collector = _collector_with_stale_bucket()

    collector.sys1_end({"sys1_waiting_ms": 100.0})

    assert len(collector._pending_flush) == 1, "the finished request must be flushed"
    minute, bucket = collector._pending_flush[0]
    assert minute == "2000-01-01T00:00"
    assert bucket["sys1_completed"] == 1
    assert bucket["sys1_timing_sums"]["sys1_waiting_ms"] == 100.0


def test_a_failed_sys2_task_that_lands_after_the_boundary_is_queued():
    collector = _collector_with_stale_bucket()

    collector.sys2_end({}, success=False)

    assert len(collector._pending_flush) == 1
    _, bucket = collector._pending_flush[0]
    assert bucket["sys2_failed"] == 1


def test_an_idle_bucket_is_still_dropped():
    collector = _collector_with_stale_bucket()

    collector._maybe_rotate_bucket()

    assert collector._pending_flush == []


def test_a_bucket_with_only_storage_ops_is_still_queued():
    collector = _collector_with_stale_bucket()

    collector.record_vdb_op(2.0)
    collector._maybe_rotate_bucket()

    assert len(collector._pending_flush) == 1
    _, bucket = collector._pending_flush[0]
    assert bucket["vdb_ops"] == 1
