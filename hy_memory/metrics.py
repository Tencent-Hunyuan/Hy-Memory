"""
HY Memory - System Metrics Collector

进程级单例，收集系统负载指标并按分钟粒度落盘到 SQLite。
支持按时间段查询聚合数据。

Usage:
    from hy_memory.metrics import MetricsCollector

    metrics = MetricsCollector.get()
    metrics.sys1_start()
    # ... 处理 ...
    metrics.sys1_end({"sys1_waiting_ms": 100, ...})

    # 查询最近 5 分钟
    snapshot = await metrics.get_snapshot(minutes=5)
"""

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 滑动窗口大小（秒）
_WINDOW_SECONDS = 60

# flush 间隔（秒）
_FLUSH_INTERVAL = 60

# cleanup 间隔（秒）
_CLEANUP_INTERVAL = 3600

# 数据保留天数
_RETENTION_DAYS = 7


class MetricsCollector:
    """
    进程级 metrics 收集器。

    线程安全（所有写入通过 _mu 保护）。
    按分钟粒度将增量数据持久化到 SQLite，支持时间段聚合查询。
    """

    _instance: Optional["MetricsCollector"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "MetricsCollector":
        """获取单例实例"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（仅测试用）"""
        cls._instance = None

    def __init__(self):
        self._start_time = time.time()
        self._mu = threading.Lock()
        self._cache = None
        self._background_started = False
        self._flush_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

        # ── 实时计数（不落盘） ──
        self._active_sys1 = 0
        self._active_sys2 = 0
        self._queued_sys2 = 0

        # ── 当前分钟桶（待 flush） ──
        self._current_minute = self._get_minute_key(datetime.now())
        self._bucket = self._empty_bucket()
        self._pending_flush: List[tuple] = []  # [(minute_key, bucket_data), ...]

        # ── 滑动窗口（最近 60s 完成数） ──
        self._sys1_completions: deque = deque()
        self._sys2_completions: deque = deque()

    # ================================================================
    # 初始化
    # ================================================================

    def bind_cache(self, cache) -> None:
        """绑定 cache 实例用于落盘"""
        self._cache = cache

    async def start_background_tasks(self) -> None:
        """启动后台 flush + cleanup 协程（调用一次即可）"""
        if self._background_started:
            return
        self._background_started = True
        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="hy-memory-metrics-flush",
        )
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="hy-memory-metrics-cleanup",
        )
        logger.info("[metrics] background tasks started (flush=60s, cleanup=3600s)")

    async def stop_background_tasks(self) -> None:
        """Cancel and join background tasks before their owning loop closes."""
        tasks = [
            task for task in (self._flush_task, self._cleanup_task)
            if task is not None
        ]
        self._flush_task = None
        self._cleanup_task = None
        self._background_started = False
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[metrics] background tasks stopped")

    # ================================================================
    # 采集 API
    # ================================================================

    def sys1_start(self) -> None:
        """S1 请求开始"""
        with self._mu:
            self._active_sys1 += 1
            self._bucket["sys1_started"] += 1

    def sys1_end(self, timing: Dict[str, float], success: bool = True) -> None:
        """S1 请求结束"""
        now = time.time()
        with self._mu:
            self._active_sys1 -= 1
            if success:
                self._bucket["sys1_completed"] += 1
            else:
                self._bucket["sys1_failed"] += 1

            # 累加 timing
            for key in ("sys1_waiting_ms", "sys1_l1_process_ms", "sys1_workflow_ms", "sys1_ops_avg_ms"):
                val = timing.get(key, 0)
                self._bucket["sys1_timing_sums"][key] = (
                    self._bucket["sys1_timing_sums"].get(key, 0) + val
                )

            # 滑动窗口
            self._sys1_completions.append(now)

            # 可能需要翻转 bucket
            self._maybe_rotate_bucket()

    def sys2_enqueue(self) -> None:
        """S2 任务入队"""
        with self._mu:
            self._queued_sys2 += 1

    def sys2_start(self) -> None:
        """S2 任务开始处理（从队列取出）"""
        with self._mu:
            self._active_sys2 += 1
            self._queued_sys2 = max(0, self._queued_sys2 - 1)
            self._bucket["sys2_started"] += 1

    def sys2_end(self, timing: Dict[str, float], success: bool = True) -> None:
        """S2 任务结束"""
        now = time.time()
        with self._mu:
            self._active_sys2 -= 1
            if success:
                self._bucket["sys2_completed"] += 1
            else:
                self._bucket["sys2_failed"] += 1

            # 累加 timing
            for key in ("sys2_waiting_ms", "sys2_preprocess_ms", "sys2_agent_generate_ms",
                        "sys2_agent_tools_avg_ms", "sys2_sweeper_ms"):
                val = timing.get(key, 0)
                self._bucket["sys2_timing_sums"][key] = (
                    self._bucket["sys2_timing_sums"].get(key, 0) + val
                )

            # 滑动窗口
            self._sys2_completions.append(now)

            self._maybe_rotate_bucket()

    def record_vdb_op(self, elapsed_ms: float) -> None:
        """记录一次 VDB 操作"""
        with self._mu:
            self._bucket["vdb_ops"] += 1
            self._bucket["vdb_ops_sum_ms"] += elapsed_ms

    def record_graph_op(self, elapsed_ms: float) -> None:
        """记录一次 Graph 操作"""
        with self._mu:
            self._bucket["graph_ops"] += 1
            self._bucket["graph_ops_sum_ms"] += elapsed_ms

    # ================================================================
    # 查询 API
    # ================================================================

    async def get_snapshot(self, minutes: int = 5) -> Dict[str, Any]:
        """
        获取最近 N 分钟的聚合指标。

        从 SQLite 读取历史分钟数据 + 合并当前分钟内存数据。
        """
        now = datetime.now()
        start_ts = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")
        end_ts = now.strftime("%Y-%m-%dT%H:%M")

        # 从 SQLite 读取历史
        history_buckets: List[Dict] = []
        if self._cache and hasattr(self._cache, "load_metrics_range"):
            try:
                history_buckets = await self._cache.load_metrics_range(start_ts, end_ts)
            except Exception as e:
                logger.warning(f"[metrics] load_metrics_range failed: {e}")

        # 合并当前内存 bucket + pending_flush
        with self._mu:
            current_bucket = dict(self._bucket)
            current_bucket["sys1_timing_sums"] = dict(self._bucket["sys1_timing_sums"])
            current_bucket["sys2_timing_sums"] = dict(self._bucket["sys2_timing_sums"])

            # pending_flush 中的旧桶也需要包含
            pending_buckets = [data for _, data in self._pending_flush]

            # 实时数据
            active_sys1 = self._active_sys1
            active_sys2 = self._active_sys2
            queued_sys2 = self._queued_sys2

            # 滑动窗口：清理过期
            cutoff = time.time() - _WINDOW_SECONDS
            while self._sys1_completions and self._sys1_completions[0] < cutoff:
                self._sys1_completions.popleft()
            while self._sys2_completions and self._sys2_completions[0] < cutoff:
                self._sys2_completions.popleft()
            sys1_last_60s = len(self._sys1_completions)
            sys2_last_60s = len(self._sys2_completions)

        # 聚合所有 buckets
        all_buckets = history_buckets + pending_buckets + [current_bucket]
        agg = self._aggregate_buckets(all_buckets)

        # 构建返回值
        sys1_count = agg["sys1_completed"] + agg["sys1_failed"]
        sys2_count = agg["sys2_completed"] + agg["sys2_failed"]

        # 平均 latency
        avg_latency = {}
        if sys1_count > 0:
            s1_sums = agg["sys1_timing_sums"]
            avg_latency["sys1_waiting"] = round(s1_sums.get("sys1_waiting_ms", 0) / sys1_count, 1)
            avg_latency["sys1_l1_process"] = round(s1_sums.get("sys1_l1_process_ms", 0) / sys1_count, 1)
            avg_latency["sys1_workflow"] = round(s1_sums.get("sys1_workflow_ms", 0) / sys1_count, 1)
            avg_latency["sys1_ops_avg"] = round(s1_sums.get("sys1_ops_avg_ms", 0) / sys1_count, 1)
            avg_latency["sys1_total"] = round(
                avg_latency["sys1_waiting"] + avg_latency["sys1_l1_process"] + avg_latency["sys1_workflow"], 1
            )

        if sys2_count > 0:
            s2_sums = agg["sys2_timing_sums"]
            avg_latency["sys2_waiting"] = round(s2_sums.get("sys2_waiting_ms", 0) / sys2_count, 1)
            avg_latency["sys2_preprocess"] = round(s2_sums.get("sys2_preprocess_ms", 0) / sys2_count, 1)
            avg_latency["sys2_agent_generate"] = round(s2_sums.get("sys2_agent_generate_ms", 0) / sys2_count, 1)
            avg_latency["sys2_agent_tools_avg"] = round(s2_sums.get("sys2_agent_tools_avg_ms", 0) / sys2_count, 1)
            avg_latency["sys2_sweeper"] = round(s2_sums.get("sys2_sweeper_ms", 0) / sys2_count, 1)
            avg_latency["sys2_total"] = round(
                avg_latency["sys2_waiting"] + avg_latency["sys2_preprocess"] +
                avg_latency["sys2_agent_generate"] + avg_latency["sys2_sweeper"], 1
            )

        # Storage ops
        storage_ops = {}
        if agg["vdb_ops"] > 0:
            storage_ops["vdb_ops_total"] = agg["vdb_ops"]
            storage_ops["vdb_ops_avg_ms"] = round(agg["vdb_ops_sum_ms"] / agg["vdb_ops"], 1)
        if agg["graph_ops"] > 0:
            storage_ops["graph_ops_total"] = agg["graph_ops"]
            storage_ops["graph_ops_avg_ms"] = round(agg["graph_ops_sum_ms"] / agg["graph_ops"], 1)

        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "window_minutes": minutes,
            "requests": {
                "total": agg["sys1_started"],
                "active_sys1": active_sys1,
                "active_sys2": active_sys2,
                "queued_sys2": queued_sys2,
                "completed": agg["sys1_completed"],
                "failed": agg["sys1_failed"],
            },
            "avg_latency_ms": avg_latency,
            "throughput": {
                "sys1_completed_last_60s": sys1_last_60s,
                "sys2_completed_last_60s": sys2_last_60s,
            },
            "storage_ops": storage_ops,
            "sys2_requests": {
                "total": agg["sys2_started"],
                "completed": agg["sys2_completed"],
                "failed": agg["sys2_failed"],
            },
            "since": start_ts,
        }

    # ================================================================
    # 落盘
    # ================================================================

    async def flush(self) -> None:
        """将当前分钟桶 + pending 队列写入 SQLite"""
        if self._cache is None:
            return

        with self._mu:
            # 收集所有待写入的 buckets
            to_flush: List[tuple] = list(self._pending_flush)
            self._pending_flush.clear()

            # 当前桶也 flush（如果非空）
            if not self._is_bucket_empty(self._bucket):
                to_flush.append((self._current_minute, self._bucket))
                self._bucket = self._empty_bucket()

            self._current_minute = self._get_minute_key(datetime.now())

        for minute_key, data in to_flush:
            try:
                await self._cache.store_metrics_minute(minute_key, data)
                logger.debug(f"[metrics] flushed minute={minute_key}")
            except Exception as e:
                logger.warning(f"[metrics] flush failed for {minute_key}: {e}")

    # ================================================================
    # 内部方法
    # ================================================================

    @staticmethod
    def _get_minute_key(dt: datetime) -> str:
        """返回分钟精度的 key，如 '2026-05-23T13:07'"""
        return dt.strftime("%Y-%m-%dT%H:%M")

    @staticmethod
    def _empty_bucket() -> Dict[str, Any]:
        return {
            "sys1_started": 0,
            "sys1_completed": 0,
            "sys1_failed": 0,
            "sys2_started": 0,
            "sys2_completed": 0,
            "sys2_failed": 0,
            "sys1_timing_sums": {},
            "sys2_timing_sums": {},
            "vdb_ops": 0,
            "vdb_ops_sum_ms": 0.0,
            "graph_ops": 0,
            "graph_ops_sum_ms": 0.0,
        }

    @staticmethod
    def _is_bucket_empty(bucket: Dict) -> bool:
        # 跨分钟结束的请求只会在新桶里累加 completed/failed 与 timing（started 记在
        # 上一分钟的桶里），所以这些字段任何一项非零都算"有活动"，不能丢。
        counters = (
            "sys1_started", "sys1_completed", "sys1_failed",
            "sys2_started", "sys2_completed", "sys2_failed",
            "vdb_ops", "graph_ops",
        )
        if any(bucket[name] for name in counters):
            return False
        return not bucket["sys1_timing_sums"] and not bucket["sys2_timing_sums"]

    def _maybe_rotate_bucket(self) -> None:
        """如果当前分钟已过，翻转 bucket（在 _mu 锁内调用）"""
        now_minute = self._get_minute_key(datetime.now())
        if now_minute != self._current_minute:
            # 旧 bucket 移入待 flush 队列
            if not self._is_bucket_empty(self._bucket):
                self._pending_flush.append((self._current_minute, self._bucket))
            self._current_minute = now_minute
            self._bucket = self._empty_bucket()

    @staticmethod
    def _aggregate_buckets(buckets: List[Dict]) -> Dict[str, Any]:
        """聚合多个 bucket 的数据"""
        agg = MetricsCollector._empty_bucket()
        for b in buckets:
            agg["sys1_started"] += b.get("sys1_started", 0)
            agg["sys1_completed"] += b.get("sys1_completed", 0)
            agg["sys1_failed"] += b.get("sys1_failed", 0)
            agg["sys2_started"] += b.get("sys2_started", 0)
            agg["sys2_completed"] += b.get("sys2_completed", 0)
            agg["sys2_failed"] += b.get("sys2_failed", 0)
            agg["vdb_ops"] += b.get("vdb_ops", 0)
            agg["vdb_ops_sum_ms"] += b.get("vdb_ops_sum_ms", 0)
            agg["graph_ops"] += b.get("graph_ops", 0)
            agg["graph_ops_sum_ms"] += b.get("graph_ops_sum_ms", 0)
            # timing sums
            for key, val in b.get("sys1_timing_sums", {}).items():
                agg["sys1_timing_sums"][key] = agg["sys1_timing_sums"].get(key, 0) + val
            for key, val in b.get("sys2_timing_sums", {}).items():
                agg["sys2_timing_sums"][key] = agg["sys2_timing_sums"].get(key, 0) + val
        return agg

    # ================================================================
    # 后台协程
    # ================================================================

    async def _flush_loop(self) -> None:
        """每分钟 flush 当前 bucket 到 SQLite"""
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            try:
                await self.flush()
            except Exception as e:
                logger.warning(f"[metrics] flush_loop error: {e}")

    async def _cleanup_loop(self) -> None:
        """每小时清理 7 天前的 metrics 数据"""
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            if self._cache is None:
                continue
            try:
                cutoff = (datetime.now() - timedelta(days=_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M")
                await self._cache.cleanup_old_metrics(cutoff)
                logger.debug(f"[metrics] cleanup done, removed before {cutoff}")
            except Exception as e:
                logger.warning(f"[metrics] cleanup error: {e}")
